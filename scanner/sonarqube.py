# Copyright (c) 2026 Calfus Inc.
# Author: Wasiullah Rafeeq S
#
# Depends on: SonarQube (SonarSource) - direct API client

"""
Self-hosted SonarQube findings fetch. Only /api/issues/search is used -
/api/hotspots/search is deprecated (hotspots now surface as regular
issues, tagged "former-hotspot"), so classification checks the legacy
`type`, the MQR `impacts` array, and that tag together.
"""

import logging

import requests

from core.errors import ScannerAuthError
from core.models import Finding, Severity
from scanner.html_text import strip_html

logger = logging.getLogger(__name__)

SONAR_SEVERITY_MAP = {
    "BLOCKER": Severity.CRITICAL,
    "CRITICAL": Severity.CRITICAL,
    "MAJOR": Severity.HIGH,
    "MINOR": Severity.MEDIUM,
    "INFO": Severity.LOW,
}
DEFAULT_SEVERITY = Severity.MEDIUM

_HOW_TO_FIX_SECTION_KEY = "how_to_fix"
_PAGE_SIZE = 500
_SECURITY_IMPACT_QUALITY = "SECURITY"
FORMER_HOTSPOT_TAG = "former-hotspot"


def _is_security_relevant(raw: dict) -> bool:
    if raw.get("type") == "VULNERABILITY":
        return True
    if any(impact.get("softwareQuality") == _SECURITY_IMPACT_QUALITY for impact in raw.get("impacts", [])):
        return True
    return FORMER_HOTSPOT_TAG in raw.get("tags", [])


class SonarQubeClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._rule_cache: dict[str, tuple[str, str | None]] = {}

    def _auth(self) -> tuple[str, str]:
        return (self.token, "")

    def _fetch_rule_details(self, rule_key: str) -> tuple[str, str | None]:
        if rule_key in self._rule_cache:
            return self._rule_cache[rule_key]

        name = rule_key
        how_to_fix = None
        try:
            response = requests.get(
                f"{self.base_url}/api/rules/show",
                params={"key": rule_key},
                auth=self._auth(),
            )
            if response.status_code == 200:
                rule = response.json().get("rule", {})
                name = rule.get("name", rule_key)
                for section in rule.get("descriptionSections", []):
                    if section.get("key") == _HOW_TO_FIX_SECTION_KEY:
                        how_to_fix = strip_html(section.get("content", ""), preserve_block_breaks=True) or None
                        break
        except requests.RequestException:
            pass

        self._rule_cache[rule_key] = (name, how_to_fix)
        return name, how_to_fix

    def _map_severity(self, raw_severity: str | None) -> Severity:
        if raw_severity in SONAR_SEVERITY_MAP:
            return SONAR_SEVERITY_MAP[raw_severity]
        logger.warning(f"Unrecognized or missing severity '{raw_severity}', defaulting to '{DEFAULT_SEVERITY.value}'")
        return DEFAULT_SEVERITY

    def _build_title(self, rule_name: str, component: str, line: int | None) -> str:
        relative_path = component.split(":", 1)[-1]
        location = f"{relative_path}:{line}" if line is not None else relative_path
        return f"{rule_name} ({location})"

    def _fetch_all_pages(self, params: dict) -> list[dict]:
        items: list[dict] = []
        page = 1
        while True:
            response = requests.get(
                f"{self.base_url}/api/issues/search",
                params={**params, "p": page, "ps": _PAGE_SIZE},
                auth=self._auth(),
            )
            if response.status_code in (401, 403):
                raise ScannerAuthError(
                    f"SonarQube issues/search failed with status {response.status_code} (invalid/expired token?): {response.text}"
                )
            if response.status_code != 200:
                raise RuntimeError(f"SonarQube issues/search failed with status {response.status_code}: {response.text}")

            data = response.json()
            page_items = data.get("issues", [])
            items.extend(page_items)

            total = data.get("paging", {}).get("total", len(items))
            if not page_items or len(items) >= total:
                return items
            page += 1

    def fetch_findings(self, project_key: str, branch: str | None = None) -> list[Finding]:
        """Every currently OPEN/CONFIRMED security-relevant finding - a full snapshot, not a delta (see docs/REPORT_CONTRACT.md)."""
        params = {"componentKeys": project_key, "issueStatuses": "OPEN,CONFIRMED"}
        if branch:
            params["branch"] = branch

        findings = []
        for raw in self._fetch_all_pages(params):
            if not _is_security_relevant(raw):
                continue

            key = raw["key"]
            rule = raw.get("rule", "")
            rule_name, how_to_fix = self._fetch_rule_details(rule) if rule else (rule, None)
            component = raw.get("component", "")
            line = raw.get("line")
            finding_type = "hotspot" if FORMER_HOTSPOT_TAG in raw.get("tags", []) else "vulnerability"
            findings.append(
                Finding(
                    key=key,
                    title=self._build_title(rule_name, component, line),
                    severity=self._map_severity(raw.get("severity")),
                    component=component,
                    line=line,
                    message=raw.get("message", ""),
                    finding_type=finding_type,
                    deep_link=f"{self.base_url}/project/issues?id={project_key}&issues={key}",
                    source_tool="sonarqube",
                    how_to_fix=how_to_fix,
                    rule_key=rule or None,
                )
            )
        return findings
