# Copyright (c) 2026 Calfus Inc.
# Author: Wasiullah Rafeeq S
#
# Depends on: PostgreSQL - data storage

"""
Postgres-backed idempotency ledger for ticket creation - single source of
truth across branches/repos, keyed on finding_identity() (see
ticket/base.py), not the scanner's own per-branch finding key. Two
branches reporting the same issue resolve to the same row here.

Schema: ticket/schema.sql.
"""

import os
from typing import Any

import psycopg
from psycopg.rows import dict_row

_STALE_CLAIM_SECONDS = 300  # abandoned in-progress claim self-heal window


def get_connection() -> psycopg.Connection[dict[str, Any]]:
    return psycopg.Connection[dict[str, Any]].connect(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ["POSTGRES_PORT"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ["POSTGRES_DB"],
        row_factory=dict_row,
    )


def get_claim(conn: psycopg.Connection[dict[str, Any]], destination: str, finding_identity: str) -> dict[str, Any] | None:
    """Full row (ticket_key, status, closed_at_commit_sha) for this identity, if any."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ticket_key, status, closed_at_commit_sha FROM ticket_claims "
            "WHERE destination = %s AND finding_identity = %s",
            (destination, finding_identity),
        )
        return cur.fetchone()


def claim(conn: psycopg.Connection[dict[str, Any]], destination: str, finding_identity: str) -> bool:
    """Atomically claim the right to create a ticket. True = proceed, False = someone else holds it."""
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM ticket_claims WHERE destination = %s AND finding_identity = %s "
            "AND ticket_key IS NULL AND created_at < now() - make_interval(secs => %s)",
            (destination, finding_identity, _STALE_CLAIM_SECONDS),
        )
        cur.execute(
            "INSERT INTO ticket_claims (destination, finding_identity) VALUES (%s, %s) "
            "ON CONFLICT (destination, finding_identity) DO NOTHING RETURNING 1",
            (destination, finding_identity),
        )
        won = cur.fetchone() is not None
    conn.commit()  # committed immediately so a concurrent claim() sees this right away
    return won


def record_ticket(conn: psycopg.Connection[dict[str, Any]], destination: str, finding_identity: str, ticket_key: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE ticket_claims SET ticket_key = %s WHERE destination = %s AND finding_identity = %s",
            (ticket_key, destination, finding_identity),
        )
    conn.commit()


def release(conn: psycopg.Connection[dict[str, Any]], destination: str, finding_identity: str) -> None:
    """Give up an in-progress claim (e.g. after create_ticket() raised) so a retry can reclaim it."""
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM ticket_claims WHERE destination = %s AND finding_identity = %s AND ticket_key IS NULL",
            (destination, finding_identity),
        )
    conn.commit()


def list_open(conn: psycopg.Connection[dict[str, Any]], destination: str) -> list[dict[str, Any]]:
    """Open claims with a real ticket_key - for reconciliation to check against the scanner."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT finding_identity, ticket_key FROM ticket_claims "
            "WHERE destination = %s AND status = 'open' AND ticket_key IS NOT NULL",
            (destination,),
        )
        return cur.fetchall()


def mark_closed(conn: psycopg.Connection[dict[str, Any]], destination: str, finding_identity: str, commit_sha: str | None) -> None:
    """Record that this claim's ticket was resolved, and at which commit - used later to tell a genuine regression from a stale branch still carrying the old bug."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE ticket_claims SET status = 'closed', closed_at_commit_sha = %s "
            "WHERE destination = %s AND finding_identity = %s",
            (commit_sha, destination, finding_identity),
        )
    conn.commit()


def reopen(conn: psycopg.Connection[dict[str, Any]], destination: str, finding_identity: str) -> None:
    """Clear a closed claim entirely so it can be claim()'d fresh - used once ancestry confirms a real regression."""
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM ticket_claims WHERE destination = %s AND finding_identity = %s",
            (destination, finding_identity),
        )
    conn.commit()


def clear_stale(conn: psycopg.Connection[dict[str, Any]], destination: str, finding_identity: str) -> None:
    """Remove a claim whose ticket_key was verified to no longer exist in the backend."""
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM ticket_claims WHERE destination = %s AND finding_identity = %s",
            (destination, finding_identity),
        )
    conn.commit()
