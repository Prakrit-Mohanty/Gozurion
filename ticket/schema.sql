-- ticket_claims: idempotency/dedupe ledger, see ticket/claims.py.
-- Applied once against a fresh Postgres instance/volume.

CREATE TABLE IF NOT EXISTS ticket_claims (
    destination          TEXT NOT NULL,
    finding_identity      TEXT NOT NULL,
    ticket_key            TEXT,
    status                 TEXT NOT NULL DEFAULT 'open',
    closed_at_commit_sha   TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (destination, finding_identity)
);

-- Per-repo ticket routing (ticket/config_store.py). A named destination's
-- credentials live once here; repo_configs maps repos onto one - many
-- repos can point at the same destination_id (shared Jira project) or
-- each get their own row (dedicated project). No encryption: explicit
-- call, not an oversight - see ticket/config_store.py's module docstring.

CREATE TABLE IF NOT EXISTS ticket_destinations (
    id             SERIAL PRIMARY KEY,
    name           TEXT NOT NULL UNIQUE,
    ticket_backend TEXT NOT NULL DEFAULT 'jira',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ticket_destination_credentials (
    id             SERIAL PRIMARY KEY,
    destination_id INTEGER NOT NULL REFERENCES ticket_destinations(id) ON DELETE CASCADE,
    key            TEXT NOT NULL,
    value          TEXT NOT NULL,
    UNIQUE (destination_id, key)
);

CREATE TABLE IF NOT EXISTS repo_configs (
    repo_full_name TEXT PRIMARY KEY,
    destination_id INTEGER NOT NULL REFERENCES ticket_destinations(id),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
