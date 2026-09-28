#!/usr/bin/env bash
# Starts a sonar_to_jira run for just this repo/branch, right after
# gozurion-export-report uploaded its report - called from action.yml and
# .github/workflows/export-sonar-report.yml.
#
# Goes through the temporal CLI instead of `aetherion agent` because the
# aetherion SDK only ships a macOS arm64 wheel (see pyproject.toml), which
# can't install on a standard Linux CI runner. Same thing underneath: a
# service-account token from Aetherion's Keycloak, used as the Temporal API
# key, starting the sonar_to_jira workflow on the agent's task queue.
#
# The workflow ID is fixed per repo/branch, with --id-conflict-policy
# UseExisting: two pushes to the same branch close together attach to the
# one in-flight run instead of starting a second one that would race it on
# the Jira dedup search and create duplicate tickets. Different repos get
# different IDs, so they always run in parallel.
#
# Skips (exit 0) if AETHERION_CLIENT_ID is unset, so repos that only want
# the S3 upload don't need Aetherion credentials at all.

set -euo pipefail

if [[ -z "${AETHERION_CLIENT_ID:-}" ]]; then
  echo "AETHERION_CLIENT_ID not set - skipping agent trigger (report is still in S3)."
  exit 0
fi

: "${AETHERION_CLIENT_SECRET:?AETHERION_CLIENT_SECRET is required when AETHERION_CLIENT_ID is set}"
: "${AETHERION_TASK_QUEUE:?AETHERION_TASK_QUEUE is required - the task queue of the published agent}"
KC_URL="${AETHERION_KC_URL:-https://kc.dev.aetherion.io}"
REALM="${AETHERION_REALM:-calfus}"
TEMPORAL_ADDRESS="${AETHERION_TEMPORAL_ADDRESS:-wfe.dev.aetherion.io:443}"
TEMPORAL_NAMESPACE="${AETHERION_NAMESPACE:-calfus}"

# Same resolution as scanner/git_context.py, so this names exactly the
# {repo}/{branch}/latest/ prefix the export step just wrote.
REPO_FULL_NAME="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY not set}"
BRANCH="${GITHUB_HEAD_REF:-${GITHUB_REF_NAME:?GITHUB_REF_NAME not set}}"

if ! command -v temporal >/dev/null; then
  curl -sSf https://temporal.download/cli.sh | sh -s -- --dir "$HOME/.temporalio" >/dev/null
  export PATH="$HOME/.temporalio/bin:$PATH"
fi

TOKEN="$(
  curl -sSf -X POST "$KC_URL/realms/$REALM/protocol/openid-connect/token" \
    --data-urlencode grant_type=client_credentials \
    --data-urlencode "client_id=$AETHERION_CLIENT_ID" \
    --data-urlencode "client_secret=$AETHERION_CLIENT_SECRET" \
    | jq -r .access_token
)"
if [[ -z "$TOKEN" || "$TOKEN" == "null" ]]; then
  echo "Couldn't get a token from $KC_URL for client $AETHERION_CLIENT_ID" >&2
  exit 1
fi
echo "::add-mask::$TOKEN"

# Env var, not --api-key, so the token never appears in the process list or
# in an error message echoing back the command line.
export TEMPORAL_API_KEY="$TOKEN" TEMPORAL_ADDRESS TEMPORAL_NAMESPACE TEMPORAL_TLS=true

temporal workflow start \
  --type sonar_to_jira \
  --task-queue "$AETHERION_TASK_QUEUE" \
  --workflow-id "sonar_to_jira-$REPO_FULL_NAME-$BRANCH" \
  --id-conflict-policy UseExisting \
  --input "$(jq -cn --arg r "$REPO_FULL_NAME" --arg b "$BRANCH" '{repo_full_name: $r, branch: $b}')"
