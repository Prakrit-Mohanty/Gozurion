# sonar_to_jira agent — architecture

## Where this fits in the bigger pipeline

```
Any org repo's CI (calls the reusable workflow below)
  semgrep/trivy/sonarqube  →  scanner/export.py (this repo)
                          |
                          v
        S3 - one shared bucket, {repo_full_name}/{branch}/{commit_sha}/combined.json
                          (history) and {repo_full_name}/{branch}/latest/combined.json
                          (always overwritten - what the agent reads)
                          |
                          v
     Temporal agent (this repo): fetch_report_from_s3 -> create_jira_tickets
                          |
                          v
                        Jira
```

The exporter (`scanner/`) and the Temporal agent (`src/agent/`, `src/tools/`) both
live in this repo. `.github/workflows/export-sonar-report.yml` is a reusable workflow
any repo in the org calls. **It stops at uploading the report to S3.** It does not
trigger the agent — actually invoking the agent per new report is the Aetherion
platform's job once this is published there, not this repo's CI.

## The exporter — `scanner/`

Runs in CI, outside Temporal entirely, on a plain `ubuntu-latest` runner:

1. `scanner/sonarqube.py`'s `SonarQubeClient.fetch_findings()` queries a self-hosted
   SonarQube's `/api/issues/search` and normalizes each result into a `Finding`.
2. `scanner/git_context.py` stamps `commit_sha`/`branch`/`repo_full_name` (from
   GitHub Actions' own env vars, falling back to `git`) and `default_branch` (via
   GitHub's API — optionally authenticated with `GITHUB_TOKEN` for private repos).
3. `scanner/export.py` stamps that metadata onto every finding and uploads the report
   via `storage/factory.py`'s `get_storage_client()`.

**Deliberately kept dependency-light**: this whole chain only imports `boto3`/
`requests`/`pydantic` — no `aetherion-sdk`, no `temporalio`. `pyproject.toml` splits
those into an `[agent]` extra for exactly this reason: `aetherion-sdk`'s pinned wheel
is macOS-arm64-only, so a hard dependency on it would break `pip install .` on the
workflow's Linux runner.

See `docs/REPORT_CONTRACT.md`/`docs/report-schema.json` for the exact report shape.
`commit_sha`/`repo_full_name`/`default_branch` are stamped but not currently read by
the agent — kept for compatibility/future use, not a functional requirement today.

## Report storage — `storage/`

`fetch_report_from_s3` (despite the name) and `scanner/export.py` never touch `boto3`
directly — both go through `storage.base.ReportStorage` (`upload`/`download`), built
by `storage/factory.py`. `storage/s3_storage.py` is the only implementation today, and it always talks to real
AWS S3.

## The Agent — `src/agent/agent.py`

```python
@agent()
async def sonar_to_jira(payload: Dict[str, Any]) -> dict:
    repo_full_name = payload["repo_full_name"]
    branch = payload.get("branch", "main")
    jira_url = payload["jira_url"]
    findings = await toolExecutor.execute("fetch_report_from_s3", repo_full_name, branch)
    result = await toolExecutor.execute("create_jira_tickets", findings, jira_url)
    return {"repo_full_name": repo_full_name, "branch": branch, "finding_count": len(findings), **result}
```

A thin Temporal *workflow* — no I/O itself, two tool calls, merges the result.
`bucket` is **not** a trigger input — it comes from the `S3_BUCKET` env var, since
it's fixed per deployment rather than something that varies per run. `repo_full_name`
(which repo's report to fetch), `branch` (defaults to `main`), and `jira_url` (which
Jira instance to create tickets in) are the trigger inputs (`src/agent/metadata.json`).
Deployed org-wide across many repos, nothing hands the agent a fresh S3 key per
run — `fetch_report_from_s3` resolves `repo_full_name`+`branch` to the fixed
`latest/combined.json` pointer `scanner/export.py` overwrites every export (see
`docs/REPORT_CONTRACT.md`). The rest of the Jira destination
(`JIRA_EMAIL`/`JIRA_API_TOKEN`/`JIRA_PROJECT_KEY`) still comes from `.env`, via
`ticket/factory.py`'s `build_ticket_client()` (the pure, env-free constructor —
`create_jira_tickets` merges the input `jira_url` with those env values into one
credentials dict itself).

## Dedup — a live Jira label search, no database

`create_jira_tickets` is stateless: no Postgres, no ledger, nothing but Jira itself.
`ticket_client.find_existing(finding)` (`ticket/jira_client.py`) searches Jira for a
ticket already labeled `issue-{finding_identity(finding)}`; if none exists,
`create_ticket(finding)` creates one with that same label stamped on it.

`finding_identity()` (`ticket/base.py`) hashes `component + line + rule_key +
finding_type` — deliberately excludes `finding.key`/`finding.branch`, so the same bug
reported from two different branches (SonarQube scopes its own `key` per branch)
resolves to the same identity and the same Jira label, avoiding duplicate tickets
without needing any external state.

There's deliberately no auto-close/reconciliation step and no per-repo DB-driven
Jira routing (an earlier iteration explored both via a Postgres ledger and
per-repo `ticket_destinations`/`repo_configs` tables migrated into the shared
platform database — reverted: this project doesn't own or manage new tables in that
shared schema). `TICKET_BACKEND`/`JIRA_EMAIL`/`JIRA_API_TOKEN`/`JIRA_PROJECT_KEY`
come from `.env`; `jira_url` comes from the trigger input instead, so the same
deployment can point different runs at different Jira instances without touching
`.env` per call.

## How the tools are wired together

`toolExecutor.execute("fetch_report_from_s3", repo_full_name, branch)` is string-based dispatch — the
SDK looks up whichever function was registered under that name via `@tool()` and
runs it as a Temporal activity, with its own independent retry/timeout policy. Going
through `toolExecutor` (not calling the functions directly) is what gives each step
Temporal's durability.

## Triggering it

`src/agent/metadata.json` declares `repo_full_name` (required) and `jira_url`
(required) plus `branch` (optional, defaults to `main`) as string triggers — what
`aetherion test` turns into a form, and what `aetherion agent sonar_to_jira
'{"repo_full_name": "...", "branch": "...", "jira_url": "..."}'` expects as payload.
**Nothing in this repo calls that yet** — the exporter's CI job stops at uploading
to S3 (see above). Whatever eventually triggers this per repo (the Aetherion
platform, a schedule, a webhook) only ever needs to know the repo's identity, not
any report-specific key.

## How it actually runs (execution mechanics)

Everything above describes the *code*. What actually executes it is Temporal, worth
tracing through step by step since the agent/tool split only makes sense once you see
what's on each side of it.

**The pieces involved:**

- **Temporal server** — a durable coordinator, not a code runner: stores an event
  history per workflow execution, hands out work to whichever worker asks. Never
  imports or executes a line of this project's Python.
- **`aetherion run`** — starts two long-lived worker processes: a workflow worker on
  `AETHERION_AGENT_TASK_QUEUE`, an activity worker on `AETHERION_TOOL_TASK_QUEUE`
  (both set in `.env`). Has to already be running before anything below can happen —
  it's what holds the `sonar_to_jira` workflow code and the two tool functions in
  memory.
- **`aetherion agent sonar_to_jira '{"repo_full_name": ..., "jira_url": ...}'`** — a short-lived CLI invocation, a
  Temporal *client* asking the server to start a new workflow execution, then (by
  default, `--wait`) blocking on the result.

**A single run, traced through:**

1. The CLI call lands on Temporal server as "start `sonar_to_jira` with this payload,
   on the agent task queue." Recorded in a new execution's event history, marked
   available for a worker to pick up.
2. The workflow worker polls that queue, gets the task, starts executing
   `sonar_to_jira`'s Python code inside Temporal's deterministic workflow sandbox —
   what makes replay (below) possible.
3. Execution reaches `await toolExecutor.execute("fetch_report_from_s3", repo_full_name, branch)`. This
   does **not** call the Python function directly — it asks Temporal server to
   schedule an *activity task* on the tool task queue, and the workflow suspends
   right there.
4. The activity worker polls the tool task queue, picks up the task, runs the actual
   `fetch_report_from_s3` function — the only point where real I/O happens (the S3
   call). Reports the result back to Temporal server.
5. Temporal appends that result to the event history and redelivers it to the
   suspended workflow, which resumes with `findings` populated. Steps 3-5 repeat for
   `create_jira_tickets`.
6. The workflow returns its final dict. Temporal marks the execution complete and
   hands the return value to the blocked `aetherion agent` CLI call.

**Why route everything through activities instead of calling the functions
directly:** this event-history mechanism is what gives the workflow *durability*. If
the worker process dies mid-run, a new worker can replay the event history to
reconstruct exactly where it was — including which activities already completed, so
`fetch_report_from_s3` isn't re-run just because `create_jira_tickets` hadn't
finished. Each activity also gets its own independent retry policy.
