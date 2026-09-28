"""Tool definitions for the project."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

from aetherion_sdk import tool
from temporalio import activity

from core.models import Finding
from scanner.export import latest_prefix
from storage.factory import get_storage_client
from ticket.base import finding_identity
from ticket.factory import get_ticket_client
from ticket.screenshot import build_screenshot


@tool()
async def fetch_report_from_s3(repo_full_name: str, branch: str = "main") -> list[dict]:
    """Download the latest combined findings report (JSON list of Finding dicts) for a repo/branch -
    bucket comes from S3_BUCKET. Reads the fixed "latest" pointer scanner/export.py overwrites every
    run, not a commit-specific key - deployed org-wide, the agent is only ever told which repo/branch
    to check, never a fresh key per run."""

    def _fetch() -> list[dict]:
        bucket = os.environ["S3_BUCKET"]
        key = f"{latest_prefix(repo_full_name, branch)}/combined.json"
        return json.loads(get_storage_client().download(bucket, key))

    return await asyncio.to_thread(_fetch)


# scanner/export.py writes this alongside every combined.json, latest or history -
# see upload_reports(). Keeping the suffix here instead of importing it from
# scanner/export.py: that module only exposes the *prefix* builder
# (latest_prefix), since the exporter itself never needs the "/latest/" segment
# by itself the way discovery below does.
_META_SUFFIX = "/latest/meta.json"


@tool()
async def discover_new_reports(
    repo_full_name: str | None = None, branch: str | None = None
) -> list[dict]:
    """Returns every repo/branch whose latest commit_sha hasn't been recorded as processed yet
    (see record_processed_commit).

    With no arguments, scans the whole bucket for every repo/branch that has ever exported a
    report - what makes the agent org-wide and zero-config: nothing has to tell it which repos
    exist, it finds them by listing S3 itself. With repo_full_name (+ branch, default "main"),
    checks only that one repo/branch's meta.json instead - the path a CI-triggered run takes
    right after its own upload, so it never picks up (and races over) other repos' reports."""

    def _discover() -> list[dict]:
        bucket = os.environ["S3_BUCKET"]
        storage = get_storage_client()

        if repo_full_name is not None:
            key = f"{latest_prefix(repo_full_name, branch or 'main')}/meta.json"
            if not storage.exists(bucket, key):
                raise FileNotFoundError(f"No report at s3://{bucket}/{key}")
            new = _new_report(storage, bucket, key, repo_full_name, branch or "main")
            return [new] if new else []

        new_reports: list[dict] = []
        for key in storage.list_keys(bucket):
            if key.startswith("_state/") or not key.endswith(_META_SUFFIX):
                continue

            # key shape: {repo_full_name}/{branch}/latest/meta.json - repo_full_name
            # itself contains one "/" (owner/repo), so peel off the fixed suffix
            # first and split what's left from the right, not the left.
            found_repo, found_branch = key[: -len(_META_SUFFIX)].rsplit("/", 1)
            if new := _new_report(storage, bucket, key, found_repo, found_branch):
                new_reports.append(new)

        return new_reports

    return await asyncio.to_thread(_discover)


def _new_report(
    storage, bucket: str, meta_key: str, repo_full_name: str, branch: str
) -> dict | None:
    """The discover_new_reports entry for one repo/branch, or None if its latest commit_sha is
    missing or already recorded as processed."""
    commit_sha = json.loads(storage.download(bucket, meta_key)).get("commit_sha")
    if commit_sha is None:
        return None

    state_key = _state_key(repo_full_name, branch)
    last_commit_sha = None
    if storage.exists(bucket, state_key):
        last_commit_sha = json.loads(storage.download(bucket, state_key)).get("commit_sha")

    if commit_sha == last_commit_sha:
        return None
    return {"repo_full_name": repo_full_name, "branch": branch, "commit_sha": commit_sha}


def _state_key(repo_full_name: str, branch: str) -> str:
    return f"_state/{repo_full_name}/{branch}.json"


@tool()
async def record_processed_commit(repo_full_name: str, branch: str, commit_sha: str) -> None:
    """Marks commit_sha as the latest one this agent has already created tickets for, for this
    repo/branch, so the next run's discover_new_reports skips it. Called only after
    create_jira_tickets succeeds - a failed run leaves the previous state in place, so the same
    commit gets retried next time rather than silently skipped."""

    def _record() -> None:
        bucket = os.environ["S3_BUCKET"]
        body = json.dumps({"commit_sha": commit_sha}).encode("utf-8")
        get_storage_client().upload(bucket, _state_key(repo_full_name, branch), body)

    await asyncio.to_thread(_record)


def _attach_screenshot(ticket_client, ticket_key: str, finding: Finding) -> None:
    """Best-effort, like sprint assignment/remote links in JiraClient.create_ticket - must never fail ticket creation."""
    attach = getattr(ticket_client, "attach_screenshot", None)
    if attach is None:
        return
    try:
        image_bytes = build_screenshot(finding, github_token=os.environ.get("GITHUB_TOKEN"))
        image_path = Path(tempfile.gettempdir()) / f"{finding_identity(finding)}.png"
        image_path.write_bytes(image_bytes)
        attach(ticket_key, image_path)
    except Exception as e:
        activity.logger.warning(f"Screenshot attachment failed for {ticket_key}: {e}")


def _find_already_ticketed(ticket_client, findings: list[Finding]) -> dict[str, str]:
    """finding.key -> existing ticket key, for whichever of `findings` already have one.
    Uses the batched find_existing_many if the client has it (one Jira search per ~50
    findings instead of one per finding); falls back to find_existing per-finding
    otherwise, since that's the only part of the TicketClient contract every backend
    is required to implement."""
    find_many = getattr(ticket_client, "find_existing_many", None)
    if find_many is not None:
        return find_many(findings)
    return {
        finding.key: existing
        for finding in findings
        if (existing := ticket_client.find_existing(finding)) is not None
    }


@tool()
async def create_jira_tickets(findings: list[dict]) -> dict:
    """Create Jira tickets for findings that don't already have one (Jira label search, no DB) -
    each new ticket gets a screenshot attached (code snippet, or an info card for line-less findings)."""

    def _create() -> dict:
        ticket_client = get_ticket_client()
        parsed = [Finding.model_validate(raw) for raw in findings]
        already_ticketed = _find_already_ticketed(ticket_client, parsed)

        created: list[dict] = []
        skipped: list[str] = []

        for finding in parsed:
            if finding.key in already_ticketed:
                skipped.append(finding.key)
                continue
            ticket_key = ticket_client.create_ticket(finding)
            created.append({"finding_key": finding.key, "ticket_key": ticket_key})
            _attach_screenshot(ticket_client, ticket_key, finding)

        return {"created": created, "skipped": skipped}

    return await asyncio.to_thread(_create)
