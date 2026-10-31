# The Gozu ticket contract

Three tools share one Jira board. Gozurion and the Gozu CLI write the tickets. veil reads them and opens the fix PRs. This page says how a ticket and a PR look, so the three tools stay in step.

The same page is in three repos: `Gozurion/docs/TICKET_CONTRACT.md` is the original, and `sonar-to-jira/docs/TICKET_CONTRACT.md` and `veil/docs/ticket-contract.md` are copies. The file `tests/contract/golden_tickets.json` is the same in all three repos. Each repo has a test against it. Run `gozu/scripts/check-ticket-contract.sh` to check that the copies are equal.

## The ticket

A ticket needs a repository and a branch. A tool that cannot find them stops before it writes anything. No word such as "unknown" stands for a missing value.

### Title

```
repo | file.py | 42 | Rule title
```

- The repo is the name without the owner. The file is the last part of the path. The Details list has the full repository and path.
- The title is at most 120 characters.
- A finding with no line has no line part (for example a package finding in `requirements.txt`).
- If the title is too long, the rule title is cut and ends with `...`.

### Labels

| Label | Meaning |
|---|---|
| `issue-<id>` | The identity of the finding: the first 20 characters of the SHA-1 of `repo`, `file`, `line`, `rule` and `type` joined by `\|`. The same finding gives the same label from every tool and on every branch. |
| `source-<tool>` | The scanner: `semgrep`, `trivy` or `sonarqube`. |
| `repo-<owner>-<name>` | The repository, in lower case. Filter a board by it. |
| `branch-<branch>` | The branch that the scan ran on. |

veil adds `veil-failed-fix` to a ticket that it cannot fix. A person or a Jira rule adds `veil-eligible`, if the setting `eligible_label` asks for it.

The old label `source-key-<scanner key>` is not written any more. The Gozu CLI still searches for it, to find a ticket that it made before this contract.

### Description

A short paragraph about the problem, then a Details list in this order. Leave out a line that has no value.

```
Repository: owner/repo
Component: path/to/file.py
Line: 42
Type: vulnerability
Rule: python.lang.security.eval
Severity: MEDIUM
Source: semgrep
Branch: feature/login
Commit: 0123456789
```

A package finding also has `Package`, `Installed version` and `Fixed version` after `Component`. The section "Recommended action" follows when the scanner gives one.

### Other fields

The issue type is Bug. The priority comes from the severity: CRITICAL is Highest, HIGH is High, MEDIUM is Medium, and LOW and INFO are Low.

## The commit and the PR (veil)

- **Commit subject:** a Conventional Commit, `fix(security): replace eval with ast.literal_eval`. It has at most 72 characters and no full stop at the end. The footer is `Refs: VT-10`.
- **PR title, one ticket:** the commit subject and the ticket, `fix(security): replace eval with ast.literal_eval (VT-10)`.
- **PR title, many tickets:** `fix(security): resolve 3 findings (VT-10, VT-11, VT-12)`. More than three tickets end with `and N more`.
- **PR body:** `## Summary` (the repo, the branch it targets, and the words "draft"), then one section for each ticket, `## VT-10 · path/to/file.py:42`. Each section has three parts: **Why this is a problem**, **What changed** and **What this solves**. The model writes them in its own words, and they never copy the ticket.
- **PR state:** always a draft. A person reviews it and merges it.
- **Language:** ASD-STE100. A sentence has at most 25 words.
