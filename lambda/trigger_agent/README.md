# trigger_agent Lambda

Starts the `sonar_to_jira` agent when a report lands in the Aetherion reports bucket. It only
queues the workflow on Temporal; an Aetherion worker picks it up and does the work. See the
docstring in `handler.py` for why it reads `meta.json` and how duplicate runs are avoided.

## Build

```bash
./build.sh            # -> trigger_agent.zip (x86_64)
ARCH=arm64 ./build.sh # for an arm64 function
```

## Deploy

**Runtime:** `python3.12`. **Handler:** `handler.handler`. **Timeout:** 30s.

**Environment variables:**

| Name                         | Required | Default                    |
| ---------------------------- | -------- | -------------------------- |
| `AETHERION_CLIENT_ID`        | yes      |                            |
| `AETHERION_CLIENT_SECRET`    | yes      |                            |
| `AETHERION_TASK_QUEUE`       | yes      | the published agent's workflow task queue |
| `AETHERION_KC_URL`           | no       | `https://kc.dev.aetherion.io` |
| `AETHERION_REALM`            | no       | `calfus`                   |
| `AETHERION_TEMPORAL_ADDRESS` | no       | `wfe.dev.aetherion.io:443` |
| `AETHERION_NAMESPACE`        | no       | `calfus`                   |

**Execution role:** `AWSLambdaBasicExecutionRole` plus `s3:GetObject` on
`arn:aws:s3:::<bucket>/*`.

**Network:** the function makes outbound calls to Keycloak and to Temporal on port 443. If it
runs inside a VPC, the VPC needs a NAT gateway.

**S3 trigger:** event `s3:ObjectCreated:*`, suffix `latest/meta.json`. The suffix filter matters.
The exporter writes several objects per run, but only this one marks a complete report. The
suffix also skips the agent's own `_state/` writes and the per-commit history copies.

```bash
aws lambda add-permission --function-name gozurion-trigger-agent \
  --statement-id s3-invoke --action lambda:InvokeFunction \
  --principal s3.amazonaws.com --source-arn arn:aws:s3:::<bucket>

aws s3api put-bucket-notification-configuration --bucket <bucket> \
  --notification-configuration '{
    "LambdaFunctionConfigurations": [{
      "LambdaFunctionArn": "<function-arn>",
      "Events": ["s3:ObjectCreated:*"],
      "Filter": {"Key": {"FilterRules": [{"Name": "suffix", "Value": "latest/meta.json"}]}}
    }]
  }'
```

`put-bucket-notification-configuration` **replaces** the bucket's whole notification
config. If the bucket already has notifications, merge this entry into the output of
`get-bucket-notification-configuration` first.
