"""Tool definitions for the project."""

from __future__ import annotations

import asyncio
import json

from aetherion_sdk import tool
from core.models import Finding
from core.s3 import get_s3_client
from ticket import claims
from ticket.base import finding_identity
from ticket.factory import get_ticket_client
from ticket.github_compare import is_ancestor


@tool()
async def fetch_report_from_s3(bucket: str, key: str) -> list[dict]:
    """Download a Sonar findings report (JSON list of Finding dicts) from S3/MinIO."""

    def _fetch() -> list[dict]:
        response = get_s3_client().get_object(Bucket=bucket, Key=key)
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


@tool()
async def reconcile_resolved_findings(findings: list[dict]) -> list[str]:
    """
    Auto-close tickets for findings no longer in a default-branch report.
    A default-branch report is a full snapshot of currently-open findings
    (see docs/REPORT_CONTRACT.md) - an open claim whose identity is
    missing from it has been fixed. Only trusted for the default branch,
    same reasoning as _should_reopen(): any other branch isn't
    authoritative about what's actually fixed.
    """

    def _reconcile() -> list[str]:
        if not findings:
            return []

        parsed = [Finding.model_validate(raw) for raw in findings]
        sample = parsed[0]
        if sample.default_branch is None or sample.branch != sample.default_branch:
            return []

        ticket_client = get_ticket_client()
        destination = ticket_client.destination_id()
        current_identities = {finding_identity(f) for f in parsed}
        commit_sha = sample.commit_sha

        transition_to_done = getattr(ticket_client, "transition_to_done", None)
        add_comment = getattr(ticket_client, "add_comment", None)
        closed: list[str] = []

        with claims.get_connection() as conn:
            for row in claims.list_open(conn, destination):
                if row["finding_identity"] in current_identities:
                    continue

                ticket_key = row["ticket_key"]
                try:
                    if transition_to_done is not None and not transition_to_done(ticket_key):
                        continue
                    if add_comment is not None:
                        add_comment(ticket_key, "Closed automatically - no longer reported on the default branch")
                    claims.mark_closed(conn, destination, row["finding_identity"], commit_sha)
                    closed.append(ticket_key)
                except Exception:
                    continue  # best-effort - one bad ticket shouldn't sink the batch

        return closed

    return await asyncio.to_thread(_reconcile)
