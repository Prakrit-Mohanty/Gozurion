# Copyright (c) 2026 Calfus Inc.
# Author: Wasiullah Rafeeq S

"""
Idempotent seeder for ticket_destinations/ticket_destination_credentials/
repo_configs (ticket/schema.sql) from a declarative YAML file - the actual
way those tables get populated, so no one hand-writes SQL against prod.
Safe to re-run any time the file changes (upserts, never duplicates).

    python3 scripts/seed_ticket_routing.py config/ticket-routing.yaml

File shape - credential values support ${ENV_VAR} interpolation so real
tokens come from the environment/CI secrets, never committed in the file
itself:

    destinations:
      - name: team-a-jira
        ticket_backend: jira
        credentials:
          jira_url: https://team-a.atlassian.net
          jira_email: bot@team-a.com
          jira_api_token: ${TEAM_A_JIRA_TOKEN}
          jira_project_key: TEAMA
        repos:
          - org/repo-a
          - org/repo-a-shared
"""

import os
import re
import sys

import yaml

from core.db import get_connection

_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _interpolate(value: str) -> str:
    def _sub(match: re.Match) -> str:
        name = match.group(1)
        if name not in os.environ:
            raise KeyError(f"${{{name}}} referenced in config but not set in the environment")
        return os.environ[name]

    return _ENV_VAR_RE.sub(_sub, value)


def seed(config: dict) -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            for destination in config.get("destinations", []):
                name = destination["name"]
                backend = destination.get("ticket_backend", "jira")

                cur.execute(
                    "INSERT INTO ticket_destinations (name, ticket_backend) VALUES (%s, %s) "
                    "ON CONFLICT (name) DO UPDATE SET ticket_backend = EXCLUDED.ticket_backend "
                    "RETURNING id",
                    (name, backend),
                )
                destination_id = cur.fetchone()["id"]

                for key, raw_value in destination.get("credentials", {}).items():
                    value = _interpolate(str(raw_value))
                    cur.execute(
                        "INSERT INTO ticket_destination_credentials (destination_id, key, value) "
                        "VALUES (%s, %s, %s) "
                        "ON CONFLICT (destination_id, key) DO UPDATE SET value = EXCLUDED.value",
                        (destination_id, key, value),
                    )

                for repo_full_name in destination.get("repos", []):
                    cur.execute(
                        "INSERT INTO repo_configs (repo_full_name, destination_id) VALUES (%s, %s) "
                        "ON CONFLICT (repo_full_name) DO UPDATE SET destination_id = EXCLUDED.destination_id",
                        (repo_full_name, destination_id),
                    )

                print(f"seeded '{name}' (id={destination_id}): "
                      f"{len(destination.get('credentials', {}))} credential(s), "
                      f"{len(destination.get('repos', []))} repo(s)")
        conn.commit()


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python3 scripts/seed_ticket_routing.py <config.yaml>", file=sys.stderr)
        sys.exit(1)

    with open(sys.argv[1]) as f:
        config = yaml.safe_load(f)

    seed(config)


if __name__ == "__main__":
    main()
