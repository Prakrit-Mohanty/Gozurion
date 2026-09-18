"""Tool definitions for the project."""

from __future__ import annotations

import asyncio
import json
import os

import boto3
from aetherion_sdk import tool
from core.models import Finding
from ticket.factory import get_ticket_client


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


@tool()
async def create_jira_tickets(findings: list[dict]) -> dict:
    """Create Jira tickets for findings that don't already have one (label-search dedupe)."""

    def _create() -> dict:
        ticket_client = get_ticket_client()
        created: list[dict] = []
        skipped: list[str] = []

        for raw in findings:
            finding = Finding.model_validate(raw)
            existing = ticket_client.find_existing(finding.key)
            if existing:
                skipped.append(finding.key)
                continue
            ticket_key = ticket_client.create_ticket(finding)
            created.append({"finding_key": finding.key, "ticket_key": ticket_key})

        return {"created": created, "skipped": skipped}

    return await asyncio.to_thread(_create)
