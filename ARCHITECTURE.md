# sonar_to_jira agent — architecture

## Where this fits in the bigger pipeline

```
Any org repo's CI (calls the reusable workflow below)
  sonar-scanner  →  scanner/export.py (this repo)
                          |
                          v
        S3/MinIO - one shared bucket, key = {repo_full_name}/{branch}/{commit_sha}.json
                          |
                          v
     Temporal agent (this repo): fetch_report_from_s3
                             -> create_jira_tickets
                             -> reconcile_resolved_findings
                          |
                          v
              Jira (routed per repo - see below)
```

The exporter (`scanner/`) and the Temporal agent (`src/agent/`, `src/tools/`) both
live in this repo now — the exporter isn't an external dependency anymore, it's
`.github/workflows/export-sonar-report.yml`, a reusable workflow any repo in the org
calls. **It stops at uploading the report to S3.** It does not trigger the agent —
once this agent is published on the Aetherion platform, triggering is the platform's
job, not this repo's CI.

S3/MinIO is a **single shared bucket**, not one per repo — separation between repos
and scans is purely a key-naming convention (`{repo_full_name}/{branch}/
{commit_sha}.json`, see `scanner/export.py`'s `upload_report()`), not physical
isolation. Same reasoning as Postgres below: one shared resource, scoped by a column
(here, a key prefix) instead of one instance per repo.

## The exporter — `scanner/`

Runs in CI, outside Temporal entirely, on a plain `ubuntu-latest` runner:

1. `scanner/sonarqube.py`'s `SonarQubeClient.fetch_findings()` queries a self-hosted
   SonarQube's `/api/issues/search` and normalizes each result into a `Finding`.
2. `scanner/git_context.py` stamps `commit_sha`/`branch`/`repo_full_name` (from
   GitHub Actions' own env vars, falling back to `git`) and `default_branch` (the one
   that needs a real network call — GitHub's API, since a local checkout can't know
   which branch is trunk).
3. `scanner/export.py` stamps that metadata onto every finding in the report and
   uploads it via `storage/factory.py`'s `get_storage_client()`.

**Deliberately kept dependency-light**: this whole chain only imports `boto3`/
`requests`/`pydantic` (verified by walking its AST) — no `aetherion-sdk`, no
`temporalio`, no `psycopg`. `pyproject.toml` splits those into an `[agent]` extra for
exactly this reason: `aetherion-sdk`'s pinned wheel is macOS-arm64-only, so a hard
dependency on it would break `pip install .` on the workflow's Linux runner.

See `docs/REPORT_CONTRACT.md`/`docs/report-schema.json` for the exact report shape
and which fields are functionally required for what (a report predating these fields
still validates, it just silently disables regression-reopening/reconciliation).

## Report storage — `storage/`

`fetch_report_from_s3` (despite the name) and `scanner/export.py` never touch `boto3`
directly — both go through `storage.base.ReportStorage` (`upload`/`download`), built
by `storage/factory.py`. `storage/s3_storage.py` is the only implementation today,
and it's already backend-agnostic in practice: MinIO locally and real AWS S3 are the
exact same code path, differing only in `S3_ENDPOINT_URL`. `STORAGE_BACKEND=s3` is an
env flag for now; `build_storage_client(backend, credentials)` is a pure constructor
(no env reads) so a future DB-driven config would call it directly, same shape as
`ticket/factory.py`'s `build_ticket_client()`.

## The Agent — `src/agent/agent.py`

```python
@agent()
async def sonar_to_jira(payload: Dict[str, Any]) -> dict:
    findings = await toolExecutor.execute("fetch_report_from_s3", bucket, key)
    result = await toolExecutor.execute("create_jira_tickets", findings)
    closed = await toolExecutor.execute("reconcile_resolved_findings", findings)
    return {"bucket": bucket, "key": key, "finding_count": len(findings), "closed": closed, **result}
```

Still the thin Temporal *workflow* it always was — no I/O itself, three tool calls,
merges their results. Now three tools instead of two.

## Dedup — the Postgres ledger, not a Jira label search

This is the part that changed most since the first version. The original design
deduped by searching Jira for a label matching the finding's own key
(`source-key-{finding.key}`) — but SonarQube scopes that key per branch, so the same
bug on two branches got two tickets. Everything below fixes that.

**`finding_identity()`** (`ticket/base.py`) is a hash of `component + line + rule_key
+ finding_type` — deliberately excludes `finding.key`/`finding.branch`, so the same
bug resolves to the same identity no matter which branch or scan reported it.

**`ticket_claims`** (`ticket/schema.sql`, `ticket/claims.py`) is the ledger:
`(destination, finding_identity) → ticket_key, status, closed_at_commit_sha`.
`destination` scopes it per Jira project. One row per identity, ever — Postgres is
the source of truth, not a live Jira search (Jira label search only ever existed as a
fallback in the reference repo; this repo doesn't keep that fallback since it started
pre-production).

**`create_jira_tickets`**, per finding:
- No row / a row with no `ticket_key` yet (in-progress or a crashed attempt) →
  `claims.claim()` atomically grabs it (self-heals a stale claim after 5 minutes) →
  `create_ticket()` → `claims.record_ticket()`.
- Row `status='open'` → already ticketed, skip.
- Row `status='closed'` → see below.

**Closed doesn't mean forever, but it defaults to it.** A closed finding stays
suppressed unless `_should_reopen()` confirms a genuine regression:
1. The finding must be on the repo's `default_branch` — any other branch is assumed
   to just be behind, not a regression, and is skipped with zero API calls.
2. If it is the default branch: the commit that closed it (`closed_at_commit_sha`)
   must be a git **ancestor** of the commit reporting it again, checked via GitHub's
   compare API (`ticket/github_compare.py`). This is what tells "someone reverted the
   fix on `main`" apart from "a stale branch never had the fix" without relying on
   branch-protection settings being enforced everywhere (they aren't, org-wide).

Only when both hold does `claims.reopen()` clear the row and it falls through to
claim+create like a brand-new finding.

**`reconcile_resolved_findings`** is what actually closes something. It only runs
against a default-branch report, treats that report as a **full snapshot** of
everything currently open (not a delta — see `docs/REPORT_CONTRACT.md`), and any
`open` claim whose identity is missing from it gets `transition_to_done()` + a
comment + `claims.mark_closed(..., commit_sha)` — that commit is exactly what step 2
above later checks ancestry against. Non-default-branch reports are a no-op; they
aren't authoritative about what's actually fixed.

## Per-repo ticket routing — `ticket/config_store.py`

Since this runs org-wide across many repos, a single flat `JIRA_*` env-var
destination doesn't scale to "different teams want different Jira projects."
`ticket_destinations`/`ticket_destination_credentials`/`repo_configs`
(`ticket/schema.sql`) let a repo be routed to a specific destination's credentials;
`repo_configs` is the routing table itself — many repos can point at the same
`destination_id` (shared project) or each get their own, purely by how rows are
inserted, no separate flag needed. A repo with no row falls back to
`ticket/factory.py`'s `get_ticket_client()` (the original flat env-var destination),
so nothing that worked before this existed breaks.

Credentials are stored **plain text, deliberately** — the reference repo (`gozu`)
encrypts these because it ships as a tool its own users run and re-key; this agent is
being hosted on the Aetherion platform, which will own secrets once it's published
there, so there's no one to hand an encryption key to yet.

`create_jira_tickets`/`reconcile_resolved_findings` derive `repo_full_name` from the
report itself (every finding in one report shares it, per the report contract) and
call `get_ticket_client_for_repo(repo_full_name)` instead of a single global client.

## How the tools are wired together

`toolExecutor.execute("fetch_report_from_s3", bucket, key)` is string-based dispatch
— the SDK looks up whichever function was registered under that name via `@tool()`
and runs it as a Temporal activity, with its own independent retry/timeout policy.
Going through `toolExecutor` (not calling the functions directly) is what gives each
step Temporal's durability.

## Triggering it

`src/agent/metadata.json` declares `bucket`/`key` as required string triggers —
what `aetherion test` turns into a form, and what `aetherion agent sonar_to_jira
'{"bucket": "...", "key": "..."}'` expects as payload. **Nothing in this repo calls
that yet** — the exporter's CI job stops at uploading to S3 (see above); actually
invoking the agent per new report is the Aetherion platform's responsibility once
this is published there.

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
  it's what holds the `sonar_to_jira` workflow code and the three tool functions in
  memory.
- **`aetherion agent sonar_to_jira '{"bucket": ..., "key": ...}'`** — a short-lived
  CLI invocation, a Temporal *client* asking the server to start a new workflow
  execution, then (by default, `--wait`) blocking on the result.

**A single run, traced through:**

1. The CLI call lands on Temporal server as "start `sonar_to_jira` with this payload,
   on the agent task queue." Recorded in a new execution's event history, marked
   available for a worker to pick up.
2. The workflow worker polls that queue, gets the task, starts executing
   `sonar_to_jira`'s Python code inside Temporal's deterministic workflow sandbox —
   what makes replay (below) possible.
3. Execution reaches `await toolExecutor.execute("fetch_report_from_s3", ...)`. This
   does **not** call the Python function directly — it asks Temporal server to
   schedule an *activity task* on the tool task queue, and the workflow suspends
   right there.
4. The activity worker polls the tool task queue, picks up the task, runs the actual
   `fetch_report_from_s3` function — the only point where real I/O happens (the S3
   call). Reports the result back to Temporal server.
5. Temporal appends that result to the event history and redelivers it to the
   suspended workflow, which resumes with `findings` populated. Steps 3-5 repeat for
   `create_jira_tickets`, then `reconcile_resolved_findings`.
6. The workflow returns its final dict. Temporal marks the execution complete and
   hands the return value to the blocked `aetherion agent` CLI call.

**Why route everything through activities instead of calling the functions
directly:** this event-history mechanism is what gives the workflow *durability*. If
the worker process dies mid-run, a new worker can replay the event history to
reconstruct exactly where it was — including which activities already completed, so
`fetch_report_from_s3` isn't re-run just because `reconcile_resolved_findings` hadn't
finished. Each activity also gets its own independent retry policy.
