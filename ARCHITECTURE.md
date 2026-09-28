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
     Temporal agent (this repo): discover_new_reports -> fetch_report_from_s3 -> create_jira_tickets
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
`commit_sha` and `repo_full_name` are read by the agent (discovery and dedup,
respectively - see below); `default_branch` is stamped but not currently read by
anything, kept for possible future use (e.g. only ticketing findings on the default
branch).

## Report storage — `storage/`

`fetch_report_from_s3` (despite the name) and `scanner/export.py` never touch `boto3`
directly — both go through `storage.base.ReportStorage` (`upload`/`download`), built
by `storage/factory.py`. `storage/s3_storage.py` is the only implementation today, and it always talks to real
AWS S3.

## The Agent — `src/agent/agent.py`

```python
@agent()
async def sonar_to_jira(payload: Dict[str, Any] | None = None) -> dict:
    new_reports = await toolExecutor.execute("discover_new_reports")

    processed = []
    for report in new_reports:
        repo_full_name, branch, commit_sha = report["repo_full_name"], report["branch"], report["commit_sha"]
        findings = await toolExecutor.execute("fetch_report_from_s3", repo_full_name, branch)
        result = await toolExecutor.execute("create_jira_tickets", findings)
        await toolExecutor.execute("record_processed_commit", repo_full_name, branch, commit_sha)
        processed.append({"repo_full_name": repo_full_name, "branch": branch, "commit_sha": commit_sha, "finding_count": len(findings), **result})

    return {"repos_processed": len(processed), "results": processed}
```

Zero-config, no trigger inputs at all (`src/agent/metadata.json` declares an empty
`triggers` list) — the agent finds its own work by listing S3 rather than being told
which repo/branch to check. `discover_new_reports` (`src/tools/tools.py`) scans the
whole bucket for every repo/branch that has ever exported a report (via each one's
`latest/meta.json`, see `docs/REPORT_CONTRACT.md`) and compares its `commit_sha`
against the last one this agent recorded as processed for that repo/branch
(`_state/{repo_full_name}/{branch}.json`, also in the same S3 bucket — no database).
Only repos/branches with a genuinely new commit_sha come back; the agent loops over
just those, and calls `record_processed_commit` after each one's tickets are created
so a crash mid-loop leaves that repo's state untouched and it gets retried next run
rather than silently skipped. This state is purely a "don't bother re-scanning a repo
with nothing new" skip, not what prevents duplicate tickets — the Jira label search
below still owns that, so a lost/stale state file costs redundant searches, never a
duplicate ticket.

`bucket` is **not** a trigger input — it comes from the `S3_BUCKET` env var, since
it's fixed per deployment rather than something that varies per run. The Jira
destination (`JIRA_URL`/`JIRA_EMAIL`/`JIRA_API_TOKEN`/`JIRA_PROJECT_KEY`) is also one
fixed value for the whole org, read from `.env` via `ticket/factory.py`'s
`build_ticket_client()` — not per-run input, so every repo this agent discovers
files into the same Jira instance/project.

`JIRA_URL` is the plain site URL (`https://x.atlassian.net`) - `JiraClient.__init__`
(`ticket/jira_client.py`) resolves it to `https://api.atlassian.com/ex/jira/{cloudId}`
via the public, unauthenticated `{jira_url}/_edge/tenant_info` and routes every REST
call through that gateway instead. Required for Atlassian's newer API tokens with
scopes to work at all - Basic Auth against the plain site URL only works with a
classic (unscoped) token; scoped tokens 401 ("Client must be authenticated") no
matter what scopes are granted. The gateway route works the same for both token
types, so this isn't conditional on which kind is configured.

## Dedup — a live Jira label search, no database

`create_jira_tickets` is stateless: no Postgres, no ledger, nothing but Jira itself.
`ticket_client.find_existing(finding)` (`ticket/jira_client.py`) searches Jira for a
ticket already labeled `issue-{finding_identity(finding)}`; if none exists,
`create_ticket(finding)` creates one with that same label stamped on it.

For a whole report at once, `create_jira_tickets` (`src/tools/tools.py`) prefers
`find_existing_many(findings)` over calling `find_existing` per finding - one
`labels in (...)` JQL search per ~50 findings instead of one `labels = "..."`
search per finding. Bonus capability like `attach_screenshot`, detected via
`getattr` with a per-finding `find_existing` fallback for any `TicketClient` that
doesn't implement it. `JiraClient` also keeps one `requests.Session` (shared with
`SprintAssigner`) for every call it makes - dedup search, ticket create, sprint
add, remote link, screenshot upload - so a run reuses one pooled connection to
Jira instead of a fresh TCP/TLS handshake per request.

`finding_identity()` (`ticket/base.py`) hashes `repo_full_name + component + line +
rule_key + finding_type` — deliberately excludes `finding.key`/`finding.branch`, so
the same bug reported from two different branches (SonarQube scopes its own `key`
per branch) resolves to the same identity and the same Jira label, avoiding
duplicate tickets without needing any external state. `repo_full_name` is included
on purpose, unlike branch/key: deployed org-wide, `JIRA_PROJECT_KEY` is one fixed
value for every repo this agent processes (no per-repo routing), so without it two
unrelated repos hitting the same rule at the same relative path/line - plausible
with shared CI templates/boilerplate - would collide onto the same label and the
second repo's finding would silently never get ticketed.

There's deliberately no auto-close/reconciliation step and no per-repo DB-driven
Jira routing (an earlier iteration explored both via a Postgres ledger and
per-repo `ticket_destinations`/`repo_configs` tables migrated into the shared
platform database — reverted: this project doesn't own or manage new tables in that
shared schema). The `_state/` commit-tracking above is the one piece of state this
project does own, and it's a plain object in the same S3 bucket, not a new table -
consistent with that same decision. `TICKET_BACKEND`/`JIRA_URL`/`JIRA_EMAIL`/
`JIRA_API_TOKEN`/`JIRA_PROJECT_KEY` all come from `.env` - one fixed Jira
destination for every repo this agent discovers.

## Screenshots — `ticket/screenshot.py`

Every newly created ticket gets a PNG attached via `JiraClient.attach_screenshot()`
(a bonus capability - `create_jira_tickets` detects it with `getattr`, same pattern
as the rest of the "not part of the `TicketClient` contract" methods). Two cases:

- **Line-based findings** (semgrep, sonarqube, trivy secrets) — `finding.line` plus
  `repo_full_name`/`commit_sha` (stamped by the exporter) are enough to fetch the
  actual source from `raw.githubusercontent.com` at that commit and render a
  syntax-highlighted snippet (Pygments + Pillow) centered on that line.
- **Line-less findings** (trivy dependency vulnerabilities - `component` is a
  package name, not a source location) — no source to show, so a plain info card
  instead (title/component/severity/rule/message/how_to_fix as text).

Best-effort like sprint assignment/remote links already are in
`JiraClient.create_ticket` - a failed fetch or render never fails ticket creation
itself, just skips the attachment for that one ticket.

## How the tools are wired together

`toolExecutor.execute("fetch_report_from_s3", repo_full_name, branch)` is string-based dispatch — the
SDK looks up whichever function was registered under that name via `@tool()` and
runs it as a Temporal activity, with its own independent retry/timeout policy. Going
through `toolExecutor` (not calling the functions directly) is what gives each step
Temporal's durability.

## Triggering it

`src/agent/metadata.json` declares no triggers at all — `aetherion test`/`aetherion
agent sonar_to_jira` need no payload (an empty `{}` or omitted entirely). Whatever
triggers a run (a schedule is the expected case, since it discovers its own work
each time) doesn't need to know which repos exist, let alone pass one in — that's
the whole point of `discover_new_reports` over the old repo/branch/jira_url trigger
inputs. **Nothing in this repo calls that yet** — the exporter's CI job stops at
uploading to S3 (see above); actually scheduling `sonar_to_jira` is the Aetherion
platform's job once this is published there.

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
- **`aetherion agent sonar_to_jira`** — a short-lived CLI invocation (no payload
  needed), a Temporal *client* asking the server to start a new workflow execution,
  then (by default, `--wait`) blocking on the result.

**A single run, traced through:**

1. The CLI call lands on Temporal server as "start `sonar_to_jira`, on the agent
   task queue." Recorded in a new execution's event history, marked available for a
   worker to pick up.
2. The workflow worker polls that queue, gets the task, starts executing
   `sonar_to_jira`'s Python code inside Temporal's deterministic workflow sandbox —
   what makes replay (below) possible.
3. Execution reaches `await toolExecutor.execute("discover_new_reports")`. This does
   **not** call the Python function directly — it asks Temporal server to schedule
   an *activity task* on the tool task queue, and the workflow suspends right there.
4. The activity worker polls the tool task queue, picks up the task, runs the actual
   `discover_new_reports` function — the only point where real I/O happens (the S3
   listing). Reports the result back to Temporal server.
5. Temporal appends that result to the event history and redelivers it to the
   suspended workflow, which resumes with `new_reports` populated. Steps 3-5 repeat
   for `fetch_report_from_s3`, `create_jira_tickets`, and `record_processed_commit`,
   once per discovered repo/branch.
6. The workflow returns its final dict. Temporal marks the execution complete and
   hands the return value to the blocked `aetherion agent` CLI call.

**Why route everything through activities instead of calling the functions
directly:** this event-history mechanism is what gives the workflow *durability*. If
the worker process dies mid-run, a new worker can replay the event history to
reconstruct exactly where it was — including which activities already completed, so
`fetch_report_from_s3` isn't re-run just because `create_jira_tickets` hadn't
finished. Each activity also gets its own independent retry policy.
