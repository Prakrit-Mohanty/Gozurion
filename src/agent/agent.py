"""Agent definitions for the project."""

from __future__ import annotations

from typing import Any, Dict

from aetherion_sdk import agent, toolExecutor


@agent()
async def sonar_to_jira(payload: Dict[str, Any] | None = None) -> dict:
    """Org-wide agent, two ways to run it:
    - Empty payload (schedule / manual): lists every repo/branch in S3 with a
      report whose commit_sha hasn't been processed yet (discover_new_reports)
    - {"repo_full_name": ..., "branch": ...} (CI, right after its own upload -
      scripts/trigger-agent.sh): checks only that one repo/branch
    Either way, for each new report: pulls its latest combined findings, creates
    Jira tickets for any that don't already have one, then records that
    commit_sha as processed so the next run skips it.
    """

    payload = payload or {}
    new_reports = await toolExecutor.execute(
        "discover_new_reports", payload.get("repo_full_name"), payload.get("branch")
    )

    processed = []
    for report in new_reports:
        repo_full_name = report["repo_full_name"]
        branch = report["branch"]
        commit_sha = report["commit_sha"]

        findings = await toolExecutor.execute("fetch_report_from_s3", repo_full_name, branch)
        result = await toolExecutor.execute("create_jira_tickets", findings)
        await toolExecutor.execute("record_processed_commit", repo_full_name, branch, commit_sha)

        processed.append(
            {
                "repo_full_name": repo_full_name,
                "branch": branch,
                "commit_sha": commit_sha,
                "finding_count": len(findings),
                **result,
            }
        )

    return {"repos_processed": len(processed), "results": processed}
