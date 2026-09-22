# S3 report contract

`fetch_report_from_s3` downloads a JSON array of `Finding` objects from
S3/MinIO (see `report-schema.json`, generated from `core/models.py` via
`scripts/generate_report_schema.py` - regenerate after changing `Finding`).

One report = one scan of one branch at one commit. `create_jira_tickets`
dedupes findings against Jira via a label search on `finding_identity()`
(`ticket/base.py` - a hash of `component`+`line`+`rule_key`+`finding_type`,
deliberately branch/key-independent) - no other ordering or completeness
requirement on the report itself.

`commit_sha`, `repo_full_name`, and `default_branch` are stamped by
`scanner/git_context.py` but not currently read by the agent - kept in the
schema for exporter/report-consumer compatibility and possible future use
(e.g. regression detection, auto-closing resolved findings), not because
anything today depends on them being set.
