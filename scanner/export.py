# Copyright (c) 2026 Calfus Inc.
# Author: Wasiullah Rafeeq S
#
# Depends on: SonarQube (SonarSource) - findings fetch
# Depends on: S3 - report upload

"""
Standalone report exporter: fetches findings from one or more scanners,
stamps commit/branch/repo metadata, uploads one JSON report per scanner
to S3, plus a "combined" report merging all of them (see
docs/REPORT_CONTRACT.md) - all under one fixed "latest" prefix per
repo/branch, overwritten every run, with meta.json written last:

    {bucket}/{repo_full_name}/{branch}/latest/{scanner}.json
    {bucket}/{repo_full_name}/{branch}/latest/combined.json
    {bucket}/{repo_full_name}/{branch}/latest/meta.json

Runs before/outside the Temporal agent. Deployed org-wide (many repos,
each exporting on its own schedule), the agent can't be handed a fresh
commit-scoped key every time - it's triggered with just repo_full_name
+ branch and reads the "latest" pointer above (see
src/agent/agent.py/fetch_report_from_s3), always the most recent
combined snapshot for that repo/branch regardless of which commit
produced it.

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

from core.errors import MissingBranchError
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
        raise MissingBranchError(
            "Couldn't determine the branch of this run. Set GITHUB_HEAD_REF or GITHUB_REF_NAME, or run on a branch."
        )
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


def latest_prefix(repo_full_name: str | None, branch: str | None) -> str:
    """The fixed, non-commit-specific prefix the agent reads from - same
    shape scanner/export.py writes to and src/tools/tools.py reads from."""
    if not repo_full_name or not branch:
        raise MissingBranchError("A report key needs the repo and the branch.")
    return f"{repo_full_name}/{branch}/latest"


def upload_reports(reports: dict[str, list[dict]], meta: GitMetadata) -> tuple[str, dict[str, str]]:
    bucket = os.environ.get("S3_BUCKET", "reports")
    latest = latest_prefix(meta.repo_full_name, meta.branch)
    history_prefix = f"{meta.repo_full_name}/{meta.branch}/{meta.commit_sha or 'unknown-commit'}"
    storage = get_storage_client()

    keys = {}
    for name, report in reports.items():
        key = f"{latest}/{name}.json"
        storage.upload(bucket, key, json.dumps(report).encode("utf-8"))
        keys[name] = key

    # Written last, and even when a report has zero findings. Its upload is
    # what fires the trigger Lambda (lambda/trigger_agent/), so the reports
    # above must already be in place, and the agent's discovery
    # (src/tools/tools.py's discover_new_reports) reads commit_sha from it
    # without depending on combined.json containing anything.
    meta_body = json.dumps(meta.model_dump(mode="json")).encode("utf-8")
    storage.upload(bucket, f"{latest}/meta.json", meta_body)

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
