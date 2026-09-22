"""Agent definitions for the project."""

from __future__ import annotations

from typing import Any, Dict

from aetherion_sdk import agent, toolExecutor


@agent()
async def sonar_to_jira(payload: Dict[str, Any]) -> dict:
    """Agent that:
    - Pulls a Sonar findings report (JSON) from S3/MinIO (bucket from env, key from input)
    - Creates Jira tickets for any findings that don't already have one
    """

    key = payload["key"]
    jira_url = payload["jira_url"]

    findings = await toolExecutor.execute("fetch_report_from_s3", key)
    result = await toolExecutor.execute("create_jira_tickets", findings, jira_url)

    return {"key": key, "finding_count": len(findings), **result}
