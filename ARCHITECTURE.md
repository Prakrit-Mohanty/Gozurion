# sonar_to_jira agent — architecture

## Where this fits in the bigger pipeline

```
GitHub Action → sonar-scanner CLI → gozu-export-report → JSON report in MinIO
                                                                    ↓
                                              THIS AGENT: sonar_to_jira
                                                                    ↓
                                                          Jira tickets created
```

This project is the second half of the pipeline — everything upstream (running the
scan, fetching findings from SonarQube's API, uploading them as JSON) lives in the
`SonarToJira` repo's `gozu-export-report` script. This agent's whole job starts
*after* that JSON report already exists somewhere in S3/MinIO.

## The Agent — `src/agent/agent.py`

```python
@agent()
async def sonar_to_jira(payload: Dict[str, Any]) -> dict:
    bucket = payload["bucket"]
    key = payload["key"]

    findings = await toolExecutor.execute("fetch_report_from_s3", bucket, key)
    result = await toolExecutor.execute("create_jira_tickets", findings)

    return {"bucket": bucket, "key": key, "finding_count": len(findings), **result}
```

This is the Temporal *workflow* — the orchestration layer. It's deliberately thin: it
takes `{"bucket": ..., "key": ...}` (matching the triggers defined in
`src/agent/metadata.json`), calls one tool to get data, feeds that output into a
second tool, and returns a combined summary. It contains **no actual I/O itself** — no
S3 calls, no HTTP calls to Jira — because workflow code has to stay deterministic and
replayable; all the real work is delegated to tools.

## Tool 1: `fetch_report_from_s3` — `src/tools/tools.py:24-32`

A Temporal *activity*. Given a bucket/key, it builds a `boto3` S3 client pointed at
whatever `S3_ENDPOINT_URL` is set in `.env` (MinIO locally, real AWS S3 in prod — same
code either way), downloads the object, and `json.loads`s it into a plain list of
dicts. It returns dicts rather than `Finding` objects because Temporal activity return
values must be JSON-serializable — reconstructing typed objects happens on the other
side.

Note the `asyncio.to_thread(_fetch)` wrapper: `boto3` is synchronous/blocking, so the
actual network call runs in a worker thread rather than blocking the async event loop
the tool runs on.

## Tool 2: `create_jira_tickets` — `src/tools/tools.py:35-55`

The second activity. For each raw dict, it does `Finding.model_validate(raw)` to turn
it back into a real typed `Finding` (imported from the vendored `core/models.py`),
then:

1. `ticket_client.find_existing(finding.key)` — searches Jira by label
   (`source-key-{finding.key}`) to see if a ticket already exists for this exact
   finding.
2. If found → record it as **skipped** (dedupe).
3. If not found → `ticket_client.create_ticket(finding)`, which builds the full Jira
   issue (summary, ADF-formatted description, priority mapped from severity, labels,
   best-effort sprint assignment and a remote link back to the SonarQube finding), and
   record the new ticket key as **created**.

`ticket_client` comes from `get_ticket_client()` in `ticket/factory.py` — it reads
`TICKET_BACKEND`/`JIRA_URL`/`JIRA_EMAIL`/`JIRA_API_TOKEN`/`JIRA_PROJECT_KEY` straight
from `.env` and constructs a `JiraClient` (`ticket/jira_client.py`). All the actual
Jira REST API logic (raw `requests` calls, no SDK) lives there — it's vendored,
byte-for-byte, from the `SonarToJira` repo, since depending on that repo as a live
library conflicted with `aetherion-sdk`'s pinned dependencies.

## How the two are wired together

`toolExecutor.execute("fetch_report_from_s3", bucket, key)` is string-based dispatch —
the SDK looks up whichever function was registered under that name via `@tool()` and
runs it as a Temporal activity, with its own independent retry/timeout policy. Nothing
here calls the tool functions as plain Python functions; going through `toolExecutor`
is what gives each step Temporal's durability (if `create_jira_tickets` crashes
partway or the worker restarts, Temporal can retry just that activity rather than
re-running the whole workflow from scratch, and `fetch_report_from_s3`'s
already-fetched result isn't redone).

## Triggering it

`src/agent/metadata.json` declares `bucket` and `key` as required string triggers —
that's what makes `aetherion test` generate a form with those two fields, and what
`aetherion agent sonar_to_jira '{"bucket": "...", "key": "..."}'` expects as its JSON
payload (exactly what the GitHub Action's last step calls after uploading the
report).

## How it actually runs (execution mechanics)

Everything above describes the *code*. What actually executes it is Temporal, and
it's worth tracing through a real run step by step, because the agent/tool split only
makes sense once you see what's on each side of it.

**The pieces involved:**

- **Temporal server** — the `temporal` container in the local docker stack (port
  `7233`). It's a durable coordinator, not a code runner: it stores an event history
  for every workflow execution and hands out work to whichever worker asks for it. It
  never imports or executes a single line of this project's Python.
- **`aetherion run`** — starts two long-lived worker processes for this project, each
  polling one Temporal *task queue*: a workflow worker on
  `AETHERION_AGENT_TASK_QUEUE` and an activity worker on
  `AETHERION_TOOL_TASK_QUEUE` (both just per-project-unique queue names, set in
  `.env`). This has to already be running before anything below can happen — it's
  what actually holds the `sonar_to_jira` workflow code and the two tool functions in
  memory.
- **`aetherion agent sonar_to_jira '{"bucket": ..., "key": ...}'`** — a short-lived
  CLI invocation. It doesn't run any of this project's code itself; it's a Temporal
  *client* that asks the server to start a new workflow execution, then (by default,
  `--wait`) blocks polling the server for that execution's result.

**A single run, traced through:**

1. The CLI call above lands on Temporal server as "start `sonar_to_jira` with this
   payload, on the agent task queue." The server records this in a new execution's
   event history and marks it available for a worker to pick up.
2. The workflow worker (from `aetherion run`) polls that queue, gets the task, and
   starts executing `sonar_to_jira`'s Python code for real — but inside Temporal's
   deterministic workflow sandbox, which is what makes replay (below) possible.
3. Execution reaches `await toolExecutor.execute("fetch_report_from_s3", bucket,
   key)`. This does **not** call the Python function directly. It asks Temporal
   server to schedule an *activity task* on the tool task queue, and the workflow
   suspends right there — Temporal persists "workflow is waiting on this activity" to
   the event history and the workflow worker is freed up.
4. The activity worker (the other half of `aetherion run`) polls the tool task queue,
   picks up that task, and runs the actual `fetch_report_from_s3` function — this is
   the only point where real I/O happens: the boto3 call against MinIO. It reports
   the result (or a failure) back to Temporal server.
5. Temporal appends that result to the workflow's event history and redelivers it to
   the suspended workflow, which resumes exactly where it left off with `findings`
   now populated. Steps 3-5 repeat for `create_jira_tickets`.
6. The workflow function returns its final dict. Temporal marks the execution
   complete and hands that return value to whichever client is still waiting on
   it — the blocked `aetherion agent` CLI call, which prints it and exits.

**Why route everything through activities instead of just calling the functions:**
this event-history mechanism is what gives the workflow *durability*. If the worker
process dies mid-run (crash, restart, deploy), a new worker can pick the workflow back
up and replay its event history to reconstruct exactly where it was — including which
activities already completed, so `fetch_report_from_s3` isn't re-run just because
`create_jira_tickets` hadn't finished yet. Each activity also gets its own retry
policy independent of the workflow (e.g. the `start_to_close_timeout` override shown
in `my_first_agent`'s example) — a transient failure in one tool call gets retried on
its own, not by re-running the whole agent from scratch.
