"""Append-only Governor decision history for A3b.

A Check Run is mutable, so it can never be the audit log. This store can:
rows are inserted, never updated, never deleted, and each one names the
evidence bundle its verdict was derived from and the decision it follows.
The Check Run is only the current projection of this chain.
"""
import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    decision_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    epoch_id               TEXT NOT NULL,
    head_sha               TEXT NOT NULL,
    verdict                TEXT NOT NULL,
    bundle_hash            TEXT,
    bundle_schema          TEXT,
    decision_rule_revision TEXT NOT NULL,
    auth_generation        INTEGER NOT NULL,
    cause                  TEXT,
    invalidates_decision_id INTEGER,
    invalidates_bundle_hash TEXT,
    decided_at             TEXT NOT NULL,
    previous_decision_id   INTEGER
);
CREATE TABLE IF NOT EXISTS projections (
    epoch_id      TEXT PRIMARY KEY,
    head_sha      TEXT NOT NULL,
    check_run_id  INTEGER,
    conclusion    TEXT,
    decision_id   INTEGER,
    updated_at    TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS decisions_are_append_only_update
BEFORE UPDATE ON decisions
BEGIN
    SELECT RAISE(ABORT, 'decision history is append-only');
END;
CREATE TRIGGER IF NOT EXISTS decisions_are_append_only_delete
BEFORE DELETE ON decisions
BEGIN
    SELECT RAISE(ABORT, 'decision history is append-only');
END;
"""


class History:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def record(self, *, epoch_id, head_sha, verdict, decided_at,
               decision_rule_revision, auth_generation, bundle_hash=None,
               bundle_schema=None, cause=None, invalidates_decision_id=None,
               invalidates_bundle_hash=None) -> int:
        previous = self.latest()
        cur = self.conn.execute(
            "INSERT INTO decisions (epoch_id, head_sha, verdict, bundle_hash,"
            " bundle_schema, decision_rule_revision, auth_generation, cause,"
            " invalidates_decision_id, invalidates_bundle_hash, decided_at,"
            " previous_decision_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (epoch_id, head_sha, verdict, bundle_hash, bundle_schema,
             decision_rule_revision, auth_generation, cause,
             invalidates_decision_id, invalidates_bundle_hash, decided_at,
             previous["decision_id"] if previous else None))
        self.conn.commit()
        return cur.lastrowid

    def latest(self):
        return self.conn.execute(
            "SELECT * FROM decisions ORDER BY decision_id DESC LIMIT 1").fetchone()

    def latest_success(self):
        return self.conn.execute(
            "SELECT * FROM decisions WHERE verdict='SUCCESS' "
            "ORDER BY decision_id DESC LIMIT 1").fetchone()

    def chain(self):
        return self.conn.execute(
            "SELECT * FROM decisions ORDER BY decision_id").fetchall()

    def project(self, epoch_id, head_sha, check_run_id, conclusion,
                decision_id, at):
        self.conn.execute(
            "INSERT INTO projections (epoch_id, head_sha, check_run_id,"
            " conclusion, decision_id, updated_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(epoch_id) DO UPDATE SET check_run_id=excluded.check_run_id,"
            " conclusion=excluded.conclusion, decision_id=excluded.decision_id,"
            " updated_at=excluded.updated_at",
            (epoch_id, head_sha, check_run_id, conclusion, decision_id, at))
        self.conn.commit()

    def projection(self, epoch_id):
        return self.conn.execute(
            "SELECT * FROM projections WHERE epoch_id=?", (epoch_id,)).fetchone()

    def projections(self):
        return self.conn.execute(
            "SELECT * FROM projections ORDER BY updated_at").fetchall()

    def replay(self) -> dict:
        """Rebuild the current projection from the chain alone — never from
        anything read back out of GitHub."""
        state = {}
        for row in self.chain():
            state[row["epoch_id"]] = {
                "epoch_id": row["epoch_id"], "head_sha": row["head_sha"],
                "verdict": row["verdict"], "bundle_hash": row["bundle_hash"],
                "decision_id": row["decision_id"], "decided_at": row["decided_at"],
            }
        return state

    def as_json(self) -> list:
        return [dict(row) for row in self.chain()]


def expected_conclusion(verdict: str) -> str:
    """The only mapping from Governor verdict to a GitHub conclusion."""
    return {
        "SUCCESS": "success",
        "EVIDENCE_INVALIDATED": "failure",
        "NOT_ESTABLISHED": "failure",
        "AUTHORIZATION_UNAVAILABLE": "failure",
        "STALE": "cancelled",
    }[verdict]
