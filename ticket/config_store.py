# Copyright (c) 2026 Calfus Inc.
# Author: Wasiullah Rafeeq S
#
# Depends on: PostgreSQL - data storage

"""
Per-repo ticket routing: repo_configs maps a repo onto a
ticket_destinations row (credentials + backend), so different repos can
land in different Jira projects (or the same one) without redeploying.

Credentials are stored in plain text, deliberately - not an oversight.
gozu (the reference repo) encrypts these because it ships as a
general-purpose tool its users run and re-key themselves; this agent is
being published on the Aetherion platform, which will own hosting and
secrets - reinventing encryption/rotation here has no one to hand a key
to. Revisit if that assumption changes.

A repo with no row in repo_configs falls back to ticket/factory.py's
get_ticket_client() (the single flat-env-var destination) - so an
unconfigured repo keeps working exactly as before this existed.
"""

from typing import Any

import psycopg

from core.db import get_connection
from ticket.base import TicketClient
from ticket.factory import build_ticket_client, get_ticket_client


def _lookup_destination(conn: psycopg.Connection[dict[str, Any]], repo_full_name: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT d.id, d.ticket_backend FROM repo_configs c "
            "JOIN ticket_destinations d ON d.id = c.destination_id "
            "WHERE c.repo_full_name = %s",
            (repo_full_name,),
        )
        return cur.fetchone()


def _load_credentials(conn: psycopg.Connection[dict[str, Any]], destination_id: int) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT key, value FROM ticket_destination_credentials WHERE destination_id = %s",
            (destination_id,),
        )
        return {row["key"]: row["value"] for row in cur.fetchall()}


def get_ticket_client_for_repo(repo_full_name: str | None) -> TicketClient:
    """DB-routed ticket client for `repo_full_name`, falling back to the flat env-var destination if unset/unconfigured."""
    if repo_full_name is None:
        return get_ticket_client()

    with get_connection() as conn:
        destination = _lookup_destination(conn, repo_full_name)
        if destination is None:
            return get_ticket_client()
        credentials = _load_credentials(conn, destination["id"])

    return build_ticket_client(destination["ticket_backend"], credentials)
