# Copyright (c) 2026 Calfus Inc.
# Author: Wasiullah Rafeeq S
#
# Depends on: SonarQube (SonarSource) - findings fetch
# Depends on: S3/MinIO - report upload

"""
Standalone report exporter: fetches findings from one or more scanners,
stamps commit/branch/repo metadata, uploads one JSON report per scanner
to S3/MinIO, plus a "combined" report merging all of them (see
docs/REPORT_CONTRACT.md) - keyed as:

    {bucket}/{repo_full_name}/{branch}/{commit_sha}/{scanner}.json
    {bucket}/{repo_full_name}/{branch}/{commit_sha}/combined.json

Runs before/outside the Temporal agent - the bucket/key it prints
(the "combined" one) is what `aetherion agent sonar_to_jira` gets
triggered with; reconciliation needs that full snapshot, not a single
scanner's slice.

    ENABLED_SCANNERS=semgrep,trivy \
    S3_ACCESS_KEY_ID=... S3_SECRET_ACCESS_KEY=... S3_BUCKET=reports \
    python3 -m scanner.export

`ENABLED_SCANNERS` (default "semgrep,trivy") picks which of the clients
below run - "sonarqube" is still wired in but disabled by default since
it needs a running SonarQube server; both semgrep and trivy are local CLI
tools with no server/token to stand up.
"""

import json
import logging
import os
import sys

from pydantic import BaseModel

from core.models import Finding
from scanner import git_context
from scanner.semgrep_scanner import SemgrepClient
from scanner.sonarqube import SonarQubeClient
from scanner.trivy_scanner import TrivyClient
from storage.factory import get_storage_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class GitMetadata(BaseModel):
    branch: str | None
    commit_sha: str | None
    repo_full_name: str | None
    default_branch: str | None


def gather_git_metadata() -> GitMetadata:
    branch = git_context.branch()
    repo = git_context.repo_full_name()
    meta = GitMetadata(
        branch=branch,
        commit_sha=git_context.commit_sha(),
        repo_full_name=repo,
        default_branch=git_context.default_branch(repo),
    )
    if meta.branch is None:
        logger.warning("Couldn't determine branch - regression/reconciliation logic will be disabled for this report")
    if meta.default_branch is None:
        logger.warning("Couldn't determine default_branch (no GITHUB_TOKEN / repo lookup failed) - same effect")
    return meta


def _run_sonarqube(meta: GitMetadata) -> list[Finding]:
    sonar_url = os.environ["SONAR_URL"]
    sonar_token = os.environ["SONAR_TOKEN"]
    project_key = os.environ["SONAR_PROJECT_KEY"]
    return SonarQubeClient(sonar_url, sonar_token).fetch_findings(project_key, meta.branch)


def _run_semgrep(meta: GitMetadata) -> list[Finding]:
    config = os.environ.get("SEMGREP_CONFIG", "auto")
    target = os.environ.get("SCAN_TARGET", ".")
    return SemgrepClient(config).fetch_findings(target)


def _run_trivy(meta: GitMetadata) -> list[Finding]:
    severities = os.environ.get("TRIVY_SEVERITY", "CRITICAL,HIGH,MEDIUM")
    target = os.environ.get("SCAN_TARGET", ".")
    return TrivyClient(severities).fetch_findings(target)


_SCANNERS = {
    "sonarqube": _run_sonarqube,
    "semgrep": _run_semgrep,
    "trivy": _run_trivy,
}


def _enabled_scanners() -> list[str]:
    raw = os.environ.get("ENABLED_SCANNERS", "semgrep,trivy")
    return [name.strip() for name in raw.split(",") if name.strip()]


def _stamp(finding: Finding, meta: GitMetadata) -> dict:
    finding.branch = meta.branch
    finding.commit_sha = meta.commit_sha
    finding.repo_full_name = meta.repo_full_name
    finding.default_branch = meta.default_branch
    return finding.model_dump(mode="json")


def build_reports(meta: GitMetadata) -> dict[str, list[dict]]:
    """One report per enabled scanner, keyed by scanner name, plus a
    "combined" report - the full snapshot the agent's reconciliation step
    needs (see docs/REPORT_CONTRACT.md); splitting per scanner without it
    would make reconciliation wrongly auto-close tickets for findings from
    scanners that particular fetch didn't include."""
    by_scanner: dict[str, list[dict]] = {}
    combined: list[dict] = []
    for name in _enabled_scanners():
        runner = _SCANNERS.get(name)
        if runner is None:
            logger.warning(f"Unknown scanner '{name}' in ENABLED_SCANNERS, skipping")
            continue
        stamped = [_stamp(finding, meta) for finding in runner(meta)]
        by_scanner[name] = stamped
        combined.extend(stamped)

    return {**by_scanner, "combined": combined}


def upload_reports(reports: dict[str, list[dict]], meta: GitMetadata) -> tuple[str, dict[str, str]]:
    bucket = os.environ.get("S3_BUCKET", "reports")
    prefix = f"{meta.repo_full_name or 'unknown-repo'}/{meta.branch or 'unknown-branch'}/{meta.commit_sha or 'unknown-commit'}"
    storage = get_storage_client()

    keys = {}
    for name, report in reports.items():
        key = f"{prefix}/{name}.json"
        storage.upload(bucket, key, json.dumps(report).encode("utf-8"))
        keys[name] = key
    return bucket, keys


def main() -> None:
    meta = gather_git_metadata()
    reports = build_reports(meta)
    bucket, keys = upload_reports(reports, meta)

    for name, key in keys.items():
        logger.info(f"Uploaded {len(reports[name])} finding(s) to s3://{bucket}/{key}")

    combined_key = keys["combined"]
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"bucket={bucket}\n")
            f.write(f"key={combined_key}\n")

    print(
        json.dumps(
            {
                "bucket": bucket,
                "key": combined_key,
                "keys": keys,
                "finding_count": len(reports["combined"]),
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except KeyError as e:
        logger.error(f"Missing required environment variable: {e}")
        sys.exit(1)
