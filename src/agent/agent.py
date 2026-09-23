"""Agent definitions for the project."""

from __future__ import annotations

from typing import Any, Dict

from aetherion_sdk import agent, toolExecutor


@agent()
async def sonar_to_jira(payload: Dict[str, Any]) -> dict:
    """Agent that:
    - Pulls the latest combined findings report (JSON) for a repo/branch from S3
      (bucket from env; key resolved from repo_full_name+branch - see fetch_report_from_s3)
    - Creates Jira tickets for any findings that don't already have one
    """

    repo_full_name = payload["repo_full_name"]
    branch = payload.get("branch", "main")
    jira_url = payload["jira_url"]

    findings = await toolExecutor.execute("fetch_report_from_s3", repo_full_name, branch)
    result = await toolExecutor.execute("create_jira_tickets", findings, jira_url)

    return {"repo_full_name": repo_full_name, "branch": branch, "finding_count": len(findings), **result}
