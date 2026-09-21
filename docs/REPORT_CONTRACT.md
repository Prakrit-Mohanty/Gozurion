# S3 report contract

`fetch_report_from_s3` downloads a JSON array of `Finding` objects from
S3/MinIO (see `report-schema.json`, generated from `core/models.py` via
`scripts/generate_report_schema.py` - regenerate after changing `Finding`).

One report = one scan of one branch at one commit. All findings in a
single report are expected to share the same `branch`/`commit_sha`/
`repo_full_name`/`default_branch`.

## Fields nullable in the schema but required for dedup to actually work

`commit_sha`, `repo_full_name`, and `default_branch` are `null`-able so
older/partial reports still validate, but without real values:

- **Regression detection is dead.** `create_jira_tickets`'s
  `_should_reopen()` needs `commit_sha` + `repo_full_name` to call
  GitHub's compare API - without them it always returns `False`, so a
  genuinely fixed-then-reintroduced bug on the default branch stays
  permanently closed instead of getting a new ticket.
- **Reconciliation never runs.** The reconcile step only trusts a report
  as a full snapshot when `branch == default_branch` - without
  `default_branch` set, resolved findings' tickets never auto-close.

`branch` should also always be set - `_should_reopen()` and
reconciliation both compare it against `default_branch` to decide
whether this report's findings are authoritative.

## What the exporter must guarantee

- The report is a **full snapshot** of currently-open findings for that
  branch, not a delta - reconciliation infers "fixed" from an identity's
  absence, so a partial/filtered report will falsely auto-close tickets.
- `default_branch` reflects the repo's actual trunk (e.g. via the
  GitHub API's `repository.default_branch`, not a hardcoded `"main"`).
