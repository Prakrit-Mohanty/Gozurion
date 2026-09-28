# Gozurion Scan & Export

A GitHub Action that runs [semgrep](https://semgrep.dev/) and/or [Trivy](https://trivy.dev/) against
your repo and uploads a normalized findings report to S3 (see
[docs/REPORT_CONTRACT.md](docs/REPORT_CONTRACT.md) for the report shape). Downstream, a separate
Jira-ticketing agent reads that report and files/reconciles tickets per finding — this action only
covers the scan-and-export half.

## Usage

```yaml
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: Prakrit-Mohanty/Gozurion@v1
        with:
          enabled_scanners: semgrep,trivy   # optional, default shown
          s3_access_key_id: ${{ secrets.S3_ACCESS_KEY_ID }}
          s3_secret_access_key: ${{ secrets.S3_SECRET_ACCESS_KEY }}
          s3_bucket: ${{ secrets.S3_BUCKET }}
          # optional - start the Jira agent for this repo/branch after the upload
          aetherion_client_id: ${{ secrets.AETHERION_CLIENT_ID }}
          aetherion_client_secret: ${{ secrets.AETHERION_CLIENT_SECRET }}
          aetherion_task_queue: ${{ secrets.AETHERION_TASK_QUEUE }}
```

### Inputs

| Name                    | Required | Default          | Description                                              |
| ----------------------- | -------- | ---------------- | ---------------------------------------------------------|
| `enabled_scanners`      | no       | `semgrep,trivy`   | Comma-separated scanners to run                           |
| `scan_target`           | no       | `.`               | Path to scan, relative to the checked-out repo            |
| `s3_access_key_id`      | yes      |                   | AWS access key ID for the S3 bucket                        |
| `s3_secret_access_key`  | yes      |                   | AWS secret access key for the S3 bucket                   |
| `s3_bucket`             | yes      |                   | S3 bucket name reports are uploaded to                    |
| `github_token`          | no       | `${{ github.token }}` | Token used to look up the repo's default branch        |
| `aetherion_client_id`   | no       |                   | Aetherion service-account client ID - if set, starts the Jira agent for this repo/branch after the upload |
| `aetherion_client_secret` | with client ID |           | Aetherion service-account client secret                   |
| `aetherion_task_queue`  | with client ID |             | Task queue of the published `sonar_to_jira` agent         |

### Outputs

| Name     | Description                              |
| -------- | ------------------------------------------|
| `bucket` | S3 bucket the combined report was uploaded to |
| `key`    | S3 key of the combined report              |

A single S3 bucket is normally shared across every repo using this action — set the `S3_*` (and
`AETHERION_*`) secrets at the GitHub org level so each repo inherits them for free instead of copy-pasting per repo.

## Internal / agent development

The rest of this repo also contains the Temporal-based Jira ticketing agent (`src/`, `ticket/`,
`core/`) built with Calfus's internal `aetherion` SDK. That part isn't used by the Action above and
isn't published to Marketplace; it's documented here for contributors working on the agent itself.

### Creating a New Project

Use the `aetherion` CLI to scaffold a new project.

```bash

aetherion init sonar_to_jira_agent
cd sonar_to_jira_agent

uv sync

source .venv/bin/activate
```

## Configuration

```bash
aetherion config init
```

## Write Tools and Agents

Example `agent/metadata.json`:

```json
{
  "name": "sonar_to_jira_agent",
  "version": "1.0.0",
  "description": "Agent workflows for My Package.",
  "config": {
    "triggers": [
      {
        "name": "files",
        "type": "file",
        "required": true,
        "description": "Files to be uploaded for processing.",
        "friendly_name": "Upload Files"
      }
    ]
  }
}
```

Supported trigger fields:

- text / str  
- dropdown  
- file / form  
- boolean  
- number  
- textarea  
- default  


### Tool Execution Options

```python
from datetime import timedelta
from aetherion_sdk import toolExecutor

result = await toolExecutor.execute(
    "analyze_text",
    "Some text",
    start_to_close_timeout=timedelta(seconds=5),
)
```

## Running Workers

Start both workers and cache the task-queue maps:

```bash
aetherion run
```

## Local Test Form

Open a browser form generated from `agent/metadata.json`, run the agent, and see
the result in the page (Stop button shuts it down):

```bash
aetherion test
```

## Triggering Headlessly

```bash
aetherion agent sonar_to_jira_agent '{"input": "World"}'
```

## Publish

```bash
aetherion publish
```

