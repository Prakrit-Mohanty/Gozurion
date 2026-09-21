# Copyright (c) 2026 Calfus Inc.
# Author: Wasiullah Rafeeq S
#
# Depends on: PostgreSQL - data storage

"""Shared Postgres connection - used by ticket/claims.py and ticket/config_store.py."""

import os
from typing import Any

import psycopg
from psycopg.rows import dict_row


def get_connection() -> psycopg.Connection[dict[str, Any]]:
    return psycopg.Connection[dict[str, Any]].connect(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ["POSTGRES_PORT"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        dbname=os.environ["POSTGRES_DB"],
        row_factory=dict_row,
    )
