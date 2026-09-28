# S3 report contract

`scanner/export.py` uploads two copies of each report - an immutable,
commit-scoped one (history) and a fixed "latest" pointer (always
overwritten), per repo/branch:

    {bucket}/{repo_full_name}/{branch}/{commit_sha}/{scanner}.json
    {bucket}/{repo_full_name}/{branch}/{commit_sha}/combined.json
    {bucket}/{repo_full_name}/{branch}/{commit_sha}/meta.json
    {bucket}/{repo_full_name}/{branch}/latest/{scanner}.json
    {bucket}/{repo_full_name}/{branch}/latest/combined.json
    {bucket}/{repo_full_name}/{branch}/latest/meta.json

`meta.json` is the `GitMetadata` (`commit_sha`/`branch`/`repo_full_name`/`default_branch`)
stamped on every finding in that same run, written even when the run had zero
findings - the agent's multi-repo discovery (`discover_new_reports` in
`src/tools/tools.py`) needs a commit_sha to check against regardless of whether
`combined.json` has anything in it to read one off of.

The agent also owns one more prefix in the same bucket, not written by the
exporter:

    {bucket}/_state/{repo_full_name}/{branch}.json

The last `commit_sha` this agent has already created tickets for, for that
repo/branch (`record_processed_commit`). `discover_new_reports` compares each
repo/branch's `latest/meta.json` commit_sha against this to decide what's new.
This is purely a "don't bother re-scanning a repo with nothing new" skip - it's
not what prevents duplicate tickets (that's still the stateless Jira label
search below), so a missing/stale/lost state file just costs some redundant
Jira searches, never a duplicate ticket.

`fetch_report_from_s3(repo_full_name, branch="main")` downloads the
`latest/combined.json` JSON array of `Finding` objects (see
`report-schema.json`, generated from `core/models.py` via
`scripts/generate_report_schema.py` - regenerate after changing `Finding`).
Deployed org-wide across many repos each exporting on its own schedule,
the agent is only ever told which repo/branch to check - never a
commit-specific key - so it always gets that repo's most recent combined
snapshot regardless of which run produced it. `scanner/export.py::latest_prefix()`
is the single source of truth for that fixed path; keep the exporter and
`src/tools/tools.py` in sync with it, not with a copy of the format string.

One report = one scan of one branch at one commit. `create_jira_tickets`
dedupes findings against Jira via a label search on `finding_identity()`
(`ticket/base.py` - a hash of `repo_full_name`+`component`+`line`+`rule_key`+
`finding_type`, deliberately branch/key-independent, but repo-specific -
`JIRA_PROJECT_KEY` is one fixed value for every repo this agent processes,
not routed per repo, so the hash has to disambiguate repos itself) - no
other ordering or completeness requirement on the report itself.

`commit_sha` and `repo_full_name` are stamped by `scanner/git_context.py` and read
by the agent (discovery and dedup, respectively - see above). `default_branch` is
stamped but not currently read by anything - kept in the schema for possible future
use (e.g. only ticketing findings on the default branch), not because anything
today depends on it being set.

Only ever point the agent (or anything else consuming this contract) at
a `latest/combined.json` or `{commit_sha}/combined.json` produced by this
exporter. A shared bucket can end up with other pipelines' ad-hoc scanner
dumps sitting alongside these (e.g. a raw `semgrep --json` CLI output file
someone uploaded directly) - those aren't normalized `Finding` objects and
will fail `Finding.model_validate()` if fed in here.
