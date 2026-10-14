"""S3 -> Temporal trigger for the sonar_to_jira agent.

Subscribed to s3:ObjectCreated:* on the reports bucket, filtered to keys
ending in "latest/meta.json". scanner/export.py's upload_reports() writes
that object last, so by the time this fires the repo/branch's
latest/combined.json is already in place.

All this does is put a sonar_to_jira workflow on the agent's task queue -
the Aetherion worker picks it up and does the actual work. Same workflow
type, input and ID scheme the old CI-side trigger (scripts/trigger-agent.sh)
used:

- Input is {"repo_full_name", "branch"}, so discover_new_reports checks only
  that one repo/branch.
- The workflow ID is fixed per repo/branch with USE_EXISTING, so two reports
  for the same branch close together attach to one in-flight run instead of
  racing it on the Jira dedup search. S3 delivers events at least once, and
  this also absorbs a redelivered event.

repo_full_name/branch are read out of meta.json itself rather than parsed
from the key - a branch name can contain "/" (feature/foo), which makes the
{owner}/{repo}/{branch}/latest/meta.json key ambiguous to split.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.parse
import urllib.request

import boto3
from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_s3 = boto3.client("s3")

KC_URL = os.environ.get("AETHERION_KC_URL", "https://kc.dev.aetherion.io")
REALM = os.environ.get("AETHERION_REALM", "calfus")
TEMPORAL_ADDRESS = os.environ.get("AETHERION_TEMPORAL_ADDRESS", "wfe.dev.aetherion.io:443")
TEMPORAL_NAMESPACE = os.environ.get("AETHERION_NAMESPACE", "calfus")


def _fetch_token() -> str:
    """Service-account token from Aetherion's Keycloak (client-credentials), used as the Temporal API key."""
    body = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": os.environ["AETHERION_CLIENT_ID"],
            "client_secret": os.environ["AETHERION_CLIENT_SECRET"],
        }
    ).encode()
    req = urllib.request.Request(
        f"{KC_URL}/realms/{REALM}/protocol/openid-connect/token", data=body, method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        token = json.load(resp).get("access_token")
    if not token:
        raise RuntimeError(f"No access_token from {KC_URL} for client {os.environ['AETHERION_CLIENT_ID']}")
    return token


def _reports_from_event(event: dict) -> set[tuple[str, str]]:
    reports = set()
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        # S3 event keys are URL-encoded (spaces as "+").
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        meta = json.loads(_s3.get_object(Bucket=bucket, Key=key)["Body"].read())

        repo_full_name, branch = meta.get("repo_full_name"), meta.get("branch")
        if not repo_full_name or not branch:
            # The exporter couldn't resolve git context - the agent has no
            # repo/branch to look this report up by, so there's nothing to start.
            logger.warning(f"Skipping s3://{bucket}/{key}: meta.json has no repo_full_name/branch")
            continue
        reports.add((repo_full_name, branch))
    return reports


async def _start_workflows(reports: set[tuple[str, str]]) -> None:
    client = await Client.connect(
        TEMPORAL_ADDRESS, namespace=TEMPORAL_NAMESPACE, api_key=_fetch_token(), tls=True
    )
    for repo_full_name, branch in reports:
        handle = await client.start_workflow(
            "sonar_to_jira",
            {"repo_full_name": repo_full_name, "branch": branch},
            id=f"sonar_to_jira-{repo_full_name}-{branch}",
            task_queue=os.environ["AETHERION_TASK_QUEUE"],
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        )
        logger.info(f"Queued {handle.id} (run {handle.result_run_id}) for {repo_full_name}@{branch}")


def handler(event: dict, context) -> dict:
    reports = _reports_from_event(event)
    if reports:
        asyncio.run(_start_workflows(reports))
    return {"queued": sorted(f"{r}@{b}" for r, b in reports)}
