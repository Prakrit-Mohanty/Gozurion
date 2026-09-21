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
