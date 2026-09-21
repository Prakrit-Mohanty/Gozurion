# Copyright (c) 2026 Calfus Inc.
# Author: Wasiullah Rafeeq S
#
# Depends on: SonarQube (SonarSource) - findings fetch
# Depends on: S3/MinIO - report upload

"""
Standalone report exporter: fetches findings from SonarQube, stamps
commit/branch/repo metadata, uploads the JSON report to S3/MinIO (see
docs/REPORT_CONTRACT.md). Runs before/outside the Temporal agent - the
bucket/key it prints is what `aetherion agent sonar_to_jira` gets triggered
with.

    SONAR_URL=... SONAR_TOKEN=... SONAR_PROJECT_KEY=... \
    S3_ENDPOINT_URL=http://localhost:9010 S3_ACCESS_KEY_ID=minioadmin \
    S3_SECRET_ACCESS_KEY=minioadmin S3_BUCKET=sonar-reports \
    python3 -m scanner.export
"""

import json
import logging
import os
import sys
from dataclasses import dataclass

from core.s3 import get_s3_client
from scanner import git_context
from scanner.sonarqube import SonarQubeClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class GitMetadata:
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


def build_report(meta: GitMetadata) -> list[dict]:
    sonar_url = os.environ["SONAR_URL"]
    sonar_token = os.environ["SONAR_TOKEN"]
    project_key = os.environ["SONAR_PROJECT_KEY"]

    client = SonarQubeClient(sonar_url, sonar_token)
    findings = client.fetch_findings(project_key, meta.branch)

    report = []
    for finding in findings:
        finding.branch = meta.branch
        finding.commit_sha = meta.commit_sha
        finding.repo_full_name = meta.repo_full_name
        finding.default_branch = meta.default_branch
        report.append(finding.model_dump(mode="json"))
    return report


def upload_report(report: list[dict], meta: GitMetadata) -> tuple[str, str]:
    bucket = os.environ.get("S3_BUCKET", "sonar-reports")
    key = f"{meta.repo_full_name or 'unknown-repo'}/{meta.branch or 'unknown-branch'}/{meta.commit_sha or 'unknown-commit'}.json"

    get_s3_client().put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(report).encode("utf-8"),
        ContentType="application/json",
    )
    return bucket, key


def main() -> None:
    meta = gather_git_metadata()
    report = build_report(meta)
    bucket, key = upload_report(report, meta)
    logger.info(f"Uploaded {len(report)} finding(s) to s3://{bucket}/{key}")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"bucket={bucket}\n")
            f.write(f"key={key}\n")

    print(json.dumps({"bucket": bucket, "key": key, "finding_count": len(report)}))


if __name__ == "__main__":
    try:
        main()
    except KeyError as e:
        logger.error(f"Missing required environment variable: {e}")
        sys.exit(1)
