# S3 report contract

`scanner/export.py` uploads two copies of each report - an immutable,
commit-scoped one (history) and a fixed "latest" pointer (always
overwritten), per repo/branch:

    {bucket}/{repo_full_name}/{branch}/{commit_sha}/{scanner}.json
    {bucket}/{repo_full_name}/{branch}/{commit_sha}/combined.json
    {bucket}/{repo_full_name}/{branch}/latest/{scanner}.json
    {bucket}/{repo_full_name}/{branch}/latest/combined.json

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

`commit_sha`, `repo_full_name`, and `default_branch` are stamped by
`scanner/git_context.py` but not currently read by the agent - kept in the
schema for exporter/report-consumer compatibility and possible future use
(e.g. regression detection, auto-closing resolved findings), not because
anything today depends on them being set.

Only ever point the agent (or anything else consuming this contract) at
a `latest/combined.json` or `{commit_sha}/combined.json` produced by this
exporter. A shared bucket can end up with other pipelines' ad-hoc scanner
dumps sitting alongside these (e.g. a raw `semgrep --json` CLI output file
someone uploaded directly) - those aren't normalized `Finding` objects and
will fail `Finding.model_validate()` if fed in here.
