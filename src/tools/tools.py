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
from ticket.factory import build_ticket_client
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


@tool()
async def create_jira_tickets(findings: list[dict], jira_url: str) -> dict:
    """Create Jira tickets for findings that don't already have one (Jira label search, no DB) -
    each new ticket gets a screenshot attached (code snippet, or an info card for line-less findings)."""

    def _create() -> dict:
        credentials = {
            "jira_url": jira_url,
            "jira_email": os.environ["JIRA_EMAIL"],
            "jira_api_token": os.environ["JIRA_API_TOKEN"],
            "jira_project_key": os.environ["JIRA_PROJECT_KEY"],
        }
        ticket_client = build_ticket_client(os.environ.get("TICKET_BACKEND", "jira"), credentials)
        created: list[dict] = []
        skipped: list[str] = []

        for raw in findings:
            finding = Finding.model_validate(raw)
            existing = ticket_client.find_existing(finding)
            if existing:
                skipped.append(finding.key)
                continue
            ticket_key = ticket_client.create_ticket(finding)
            created.append({"finding_key": finding.key, "ticket_key": ticket_key})
            _attach_screenshot(ticket_client, ticket_key, finding)

        return {"created": created, "skipped": skipped}

    return await asyncio.to_thread(_create)
