"""Tool definitions for the project."""

from __future__ import annotations

import asyncio
import json
import os

import boto3
from aetherion_sdk import tool
from core.models import Finding
from ticket import claims
from ticket.base import finding_identity
from ticket.factory import get_ticket_client
from ticket.github_compare import is_ancestor


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("S3_ENDPOINT_URL") or None,
        aws_access_key_id=os.environ.get("S3_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("S3_SECRET_ACCESS_KEY"),
    )


@tool()
async def fetch_report_from_s3(bucket: str, key: str) -> list[dict]:
    """Download a Sonar findings report (JSON list of Finding dicts) from S3/MinIO."""

    def _fetch() -> list[dict]:
        response = _s3_client().get_object(Bucket=bucket, Key=key)
        return json.loads(response["Body"].read())

    return await asyncio.to_thread(_fetch)


def _should_reopen(finding: Finding, existing: dict) -> bool:
    """
    A closed finding_identity is normally permanently suppressed - except a
    real regression on the default branch, which must get a new ticket.
    Every other branch is expected to be behind until it merges, so it
    stays suppressed regardless of what its code currently looks like.
    """
    if finding.default_branch is None or finding.branch != finding.default_branch:
        return False
    closed_sha = existing["closed_at_commit_sha"]
    if not (closed_sha and finding.commit_sha and finding.repo_full_name):
        return False
    return is_ancestor(finding.repo_full_name, closed_sha, finding.commit_sha)


@tool()
async def create_jira_tickets(findings: list[dict]) -> dict:
    """Create Jira tickets for findings with no open (or unresolved) claim, via the Postgres ledger."""

    def _create() -> dict:
        ticket_client = get_ticket_client()
        destination = ticket_client.destination_id()
        created: list[dict] = []
        skipped: list[str] = []

        with claims.get_connection() as conn:
            for raw in findings:
                finding = Finding.model_validate(raw)
                identity = finding_identity(finding)

                existing = claims.get_claim(conn, destination, identity)
                if existing and existing["ticket_key"]:
                    if existing["status"] == "open" or not _should_reopen(finding, existing):
                        skipped.append(finding.key)
                        continue
                    claims.reopen(conn, destination, identity)

                if not claims.claim(conn, destination, identity):
                    skipped.append(finding.key)
                    continue

                try:
                    ticket_key = ticket_client.create_ticket(finding)
                    claims.record_ticket(conn, destination, identity, ticket_key)
                    created.append({"finding_key": finding.key, "ticket_key": ticket_key})
                except Exception:
                    claims.release(conn, destination, identity)
                    raise

        return {"created": created, "skipped": skipped}

    return await asyncio.to_thread(_create)
