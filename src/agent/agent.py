"""Agent definitions for the project."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict

from aetherion_sdk import agent, toolExecutor
from temporalio.common import RetryPolicy

from ticket import ste


async def _rewrite_in_ste(findings: list[dict]) -> list[dict]:
    """Description and Recommended action in ASD-STE100, written by the hosted llm_chat tool in
    batches. Best effort: a batch whose call fails keeps the scanner's own text, and its tickets
    are still created."""
    rewritten = []
    for batch in ste.batches(findings):
        try:
            reply = await toolExecutor.execute(
                "llm_chat",
                {"prompt": ste.build_prompt(batch), "context": ""},
                start_to_close_timeout=timedelta(minutes=3),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )
            text = reply.get("response") if isinstance(reply, dict) else None
        except Exception:
            text = None
        rewritten += ste.apply_reply(batch, text)
    return rewritten


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
        new_findings = await toolExecutor.execute("select_new_findings", findings)
        new_findings = await _rewrite_in_ste(new_findings)
        result = await toolExecutor.execute("create_jira_tickets", new_findings)
        await toolExecutor.execute("record_processed_commit", repo_full_name, branch, commit_sha)

        processed.append(
            {
                "repo_full_name": repo_full_name,
                "branch": branch,
                "commit_sha": commit_sha,
                "finding_count": len(findings),
                "already_ticketed": len(findings) - len(new_findings),
                **result,
            }
        )

    return {"repos_processed": len(processed), "results": processed}
