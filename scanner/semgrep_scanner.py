# Copyright (c) 2026 Calfus Inc.
#
# Depends on: Semgrep CLI - static analysis findings fetch

"""
Local Semgrep findings fetch. Shells out to the `semgrep` CLI (no server,
no API token) and parses its `--json` output - exit code 1 just means
"findings were found", not a failure; only >=2 is a real error.
"""

import json
import logging
import subprocess

from core.models import Finding, Severity

logger = logging.getLogger(__name__)

SEMGREP_SEVERITY_MAP = {
    "ERROR": Severity.HIGH,
    "WARNING": Severity.MEDIUM,
    "INFO": Severity.LOW,
}
DEFAULT_SEVERITY = Severity.MEDIUM
_TIMEOUT_SECONDS = 900


class SemgrepClient:
    def __init__(self, config: str = "auto"):
        self.config = config

    def _map_severity(self, raw_severity: str | None) -> Severity:
        if raw_severity in SEMGREP_SEVERITY_MAP:
            return SEMGREP_SEVERITY_MAP[raw_severity]
        logger.warning(f"severity: got '{raw_severity}', defaulting to '{DEFAULT_SEVERITY.value}'")
        return DEFAULT_SEVERITY

    def _build_title(self, check_id: str, path: str, line: int | None) -> str:
        short_id = check_id.rsplit(".", 1)[-1]
        location = f"{path}:{line}" if line is not None else path
        return f"{short_id} ({location})"

    def fetch_findings(self, target: str = ".") -> list[Finding]:
        try:
            proc = subprocess.run(
                ["semgrep", "--config", self.config, "--json", "--quiet", target],
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECONDS,
            )
        except FileNotFoundError as e:
            raise RuntimeError("semgrep CLI not found - install it (brew install semgrep)") from e

        if proc.returncode not in (0, 1):
            raise RuntimeError(f"semgrep failed (exit {proc.returncode}): {proc.stderr}")

        data = json.loads(proc.stdout or "{}")
        findings = []
        for result in data.get("results", []):
            check_id = result.get("check_id", "")
            extra = result.get("extra", {})
            metadata = extra.get("metadata", {})
            path = result.get("path", "")
            line = result.get("start", {}).get("line")

            findings.append(
                Finding(
                    key=f"semgrep:{check_id}:{path}:{line}",
                    title=self._build_title(check_id, path, line),
                    severity=self._map_severity(extra.get("severity")),
                    component=path,
                    line=line,
                    message=extra.get("message", ""),
                    finding_type="vulnerability",
                    deep_link=metadata.get("source", ""),
                    source_tool="semgrep",
                    how_to_fix=extra.get("fix") or metadata.get("fix"),
                    rule_key=check_id or None,
                )
            )
        return findings
