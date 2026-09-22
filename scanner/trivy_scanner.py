# Copyright (c) 2026 Calfus Inc.
#
# Depends on: Trivy CLI - dependency/secret/misconfig findings fetch

"""
Local Trivy findings fetch (`trivy fs`) - vulnerable dependencies, leaked
secrets, and misconfigurations, all from one filesystem scan. No server,
no API token.
"""

import json
import logging
import subprocess

from core.models import Finding, Severity

logger = logging.getLogger(__name__)

TRIVY_SEVERITY_MAP = {
    "CRITICAL": Severity.CRITICAL,
    "HIGH": Severity.HIGH,
    "MEDIUM": Severity.MEDIUM,
    "LOW": Severity.LOW,
    "UNKNOWN": Severity.INFO,
}
DEFAULT_SEVERITY = Severity.MEDIUM
_TIMEOUT_SECONDS = 900


class TrivyClient:
    def __init__(self, severities: str = "CRITICAL,HIGH,MEDIUM"):
        self.severities = severities

    def _map_severity(self, raw_severity: str | None) -> Severity:
        if raw_severity in TRIVY_SEVERITY_MAP:
            return TRIVY_SEVERITY_MAP[raw_severity]
        logger.warning(f"severity: got '{raw_severity}', defaulting to '{DEFAULT_SEVERITY.value}'")
        return DEFAULT_SEVERITY

    def _from_vuln(self, vuln: dict, target: str) -> Finding:
        vuln_id = vuln.get("VulnerabilityID", "")
        pkg = vuln.get("PkgName", "")
        fixed_version = vuln.get("FixedVersion")
        return Finding(
            key=f"trivy:{vuln_id}:{pkg}:{target}",
            title=f"{vuln_id} in {pkg} ({target})",
            severity=self._map_severity(vuln.get("Severity")),
            component=target,
            line=None,
            message=vuln.get("Title") or vuln.get("Description", ""),
            finding_type="vulnerability",
            deep_link=vuln.get("PrimaryURL", ""),
            source_tool="trivy",
            how_to_fix=f"Upgrade {pkg} to {fixed_version}" if fixed_version else None,
            rule_key=vuln_id or None,
        )

    def _from_secret(self, secret: dict, target: str) -> Finding:
        rule_id = secret.get("RuleID", "")
        line = secret.get("StartLine")
        title = secret.get("Title", rule_id)
        location = f"{target}:{line}" if line is not None else target
        return Finding(
            key=f"trivy:{rule_id}:{target}:{line}",
            title=f"{title} ({location})",
            severity=self._map_severity(secret.get("Severity")),
            component=target,
            line=line,
            message=secret.get("Match", ""),
            finding_type="vulnerability",
            deep_link="",
            source_tool="trivy",
            how_to_fix="Rotate and remove the exposed secret from source control.",
            rule_key=rule_id or None,
        )

    def _from_misconfig(self, misconfig: dict, target: str) -> Finding:
        check_id = misconfig.get("ID", "")
        line = misconfig.get("CauseMetadata", {}).get("StartLine")
        return Finding(
            key=f"trivy:{check_id}:{target}",
            title=f"{misconfig.get('Title', check_id)} ({target})",
            severity=self._map_severity(misconfig.get("Severity")),
            component=target,
            line=line,
            message=misconfig.get("Description", ""),
            finding_type="vulnerability",
            deep_link=misconfig.get("PrimaryURL", ""),
            source_tool="trivy",
            how_to_fix=misconfig.get("Resolution"),
            rule_key=check_id or None,
        )

    def fetch_findings(self, target: str = ".") -> list[Finding]:
        try:
            proc = subprocess.run(
                [
                    "trivy",
                    "fs",
                    "--format",
                    "json",
                    "--scanners",
                    "vuln,secret,misconfig",
                    "--severity",
                    self.severities,
                    "--quiet",
                    target,
                ],
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECONDS,
            )
        except FileNotFoundError as e:
            raise RuntimeError("trivy CLI not found - install it (brew install trivy)") from e

        if proc.returncode != 0:
            raise RuntimeError(f"trivy failed (exit {proc.returncode}): {proc.stderr}")

        data = json.loads(proc.stdout or "{}")
        findings = []
        for result in data.get("Results") or []:
            result_target = result.get("Target", target)
            for vuln in result.get("Vulnerabilities") or []:
                findings.append(self._from_vuln(vuln, result_target))
            for secret in result.get("Secrets") or []:
                findings.append(self._from_secret(secret, result_target))
            for misconfig in result.get("Misconfigurations") or []:
                findings.append(self._from_misconfig(misconfig, result_target))
        return findings
