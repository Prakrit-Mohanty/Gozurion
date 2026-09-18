"""Agent definitions for the project."""

from __future__ import annotations

from typing import Any, Dict

from aetherion_sdk import agent, toolExecutor


@agent()
async def sonar_to_jira(payload: Dict[str, Any]) -> dict:
    """Agent that:
    - Pulls a Sonar findings report (JSON) from S3/MinIO
    - Creates Jira tickets for any findings that don't already have one
    """

    bucket = payload["bucket"]
    key = payload["key"]

    findings = await toolExecutor.execute("fetch_report_from_s3", bucket, key)
    result = await toolExecutor.execute("create_jira_tickets", findings)

    return {
        "bucket": bucket,
        "key": key,
        "finding_count": len(findings),
        **result,
    }
