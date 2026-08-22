"""Durable Governor state for A2b (SQLite).

This is the source of truth. The Check Run published to GitHub is a
projection of what is written here — never the reverse, with one narrow
exception: recovering a lost `check_run_id` mapping, and only from a run
whose App identity, external id, head SHA and name all match.

Uniqueness of a logical Governor check for
`(repo_id, pr_number, head_sha, name)` is enforced here, because the
Checks API is perfectly happy to hold many identically named runs on one
commit.
"""
import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS review_epochs (
    epoch_id       TEXT PRIMARY KEY,
    repo_id        INTEGER NOT NULL,
    repo           TEXT NOT NULL,
    pr_number      INTEGER NOT NULL,
    head_sha       TEXT NOT NULL,
    generation     INTEGER NOT NULL,
    state          TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    superseded_at  TEXT,
    UNIQUE (repo_id, pr_number, head_sha)
);
CREATE TABLE IF NOT EXISTS governor_decisions (
    decision_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    epoch_id               TEXT NOT NULL REFERENCES review_epochs(epoch_id),
    verdict                TEXT NOT NULL,
    conclusion             TEXT NOT NULL,
    auth_state             TEXT NOT NULL,
    provider_state         TEXT NOT NULL,
    decision_rule_revision TEXT NOT NULL,
    evidence_refs          TEXT NOT NULL,
    created_at             TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS check_runs (
    epoch_id      TEXT PRIMARY KEY REFERENCES review_epochs(epoch_id),
    check_run_id  INTEGER,
    name          TEXT NOT NULL,
    repo_id       INTEGER NOT NULL,
    pr_number     INTEGER NOT NULL,
    head_sha      TEXT NOT NULL,
    app_id        INTEGER,
    external_id   TEXT NOT NULL,
    status        TEXT NOT NULL,
    conclusion    TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    UNIQUE (repo_id, pr_number, head_sha, name)
);
CREATE TABLE IF NOT EXISTS reconciliation_runs (
    run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT NOT NULL,
    repo         TEXT NOT NULL,
    pr_number    INTEGER NOT NULL,
    github_head  TEXT,
    stored_head  TEXT,
    actions      TEXT NOT NULL,
    finished_at  TEXT
);
"""

CURRENT = "CURRENT"
STALE = "STALE"


class Store:
    def __init__(self, path: str):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # --- epochs ----------------------------------------------------------
    def open_epoch(self, epoch_id, repo_id, repo, pr_number, head_sha,
                   created_at):
        existing = self.epoch_for_head(repo_id, pr_number, head_sha)
        if existing:
            return dict(existing)
        generation = 1 + (self.conn.execute(
            "SELECT COALESCE(MAX(generation), 0) FROM review_epochs "
            "WHERE repo_id=? AND pr_number=?", (repo_id, pr_number)
        ).fetchone()[0])
        self.conn.execute(
            "INSERT INTO review_epochs (epoch_id, repo_id, repo, pr_number,"
            " head_sha, generation, state, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (epoch_id, repo_id, repo, pr_number, head_sha, generation,
             CURRENT, created_at))
        self.conn.commit()
        return dict(self.epoch(epoch_id))

    def epoch(self, epoch_id):
        return self.conn.execute("SELECT * FROM review_epochs WHERE epoch_id=?",
                                 (epoch_id,)).fetchone()

    def epoch_for_head(self, repo_id, pr_number, head_sha):
        return self.conn.execute(
            "SELECT * FROM review_epochs WHERE repo_id=? AND pr_number=? "
            "AND head_sha=?", (repo_id, pr_number, head_sha)).fetchone()

    def current_epoch(self, repo_id, pr_number):
        return self.conn.execute(
            "SELECT * FROM review_epochs WHERE repo_id=? AND pr_number=? "
            "AND state=? ORDER BY generation DESC LIMIT 1",
            (repo_id, pr_number, CURRENT)).fetchone()

    def epochs_for(self, repo_id, pr_number):
        return self.conn.execute(
            "SELECT * FROM review_epochs WHERE repo_id=? AND pr_number=? "
            "ORDER BY generation", (repo_id, pr_number)).fetchall()

    def mark_stale(self, epoch_id, at):
        self.conn.execute(
            "UPDATE review_epochs SET state=?, superseded_at=? "
            "WHERE epoch_id=? AND state=?", (STALE, at, epoch_id, CURRENT))
        self.conn.commit()

    # --- decisions -------------------------------------------------------
    def record_decision(self, epoch_id, verdict, conclusion, auth_state,
                        provider_state, rule_revision, evidence_refs, at):
        self.conn.execute(
            "INSERT INTO governor_decisions (epoch_id, verdict, conclusion,"
            " auth_state, provider_state, decision_rule_revision,"
            " evidence_refs, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (epoch_id, verdict, conclusion, auth_state,
             json.dumps(provider_state, sort_keys=True), rule_revision,
             json.dumps(evidence_refs, sort_keys=True), at))
        self.conn.commit()

    def decisions_for(self, epoch_id):
        return self.conn.execute(
            "SELECT * FROM governor_decisions WHERE epoch_id=? "
            "ORDER BY decision_id", (epoch_id,)).fetchall()

    # --- check runs ------------------------------------------------------
    def record_check(self, epoch_id, check_run_id, name, repo_id, pr_number,
                     head_sha, app_id, external_id, status, conclusion, at):
        self.conn.execute(
            "INSERT INTO check_runs (epoch_id, check_run_id, name, repo_id,"
            " pr_number, head_sha, app_id, external_id, status, conclusion,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(epoch_id) DO UPDATE SET check_run_id=excluded.check_run_id,"
            " app_id=excluded.app_id, status=excluded.status,"
            " conclusion=excluded.conclusion, updated_at=excluded.updated_at",
            (epoch_id, check_run_id, name, repo_id, pr_number, head_sha,
             app_id, external_id, status, conclusion, at, at))
        self.conn.commit()

    def check_for_epoch(self, epoch_id):
        return self.conn.execute("SELECT * FROM check_runs WHERE epoch_id=?",
                                 (epoch_id,)).fetchone()

    def check_for_head(self, repo_id, pr_number, head_sha, name):
        return self.conn.execute(
            "SELECT * FROM check_runs WHERE repo_id=? AND pr_number=? "
            "AND head_sha=? AND name=?",
            (repo_id, pr_number, head_sha, name)).fetchone()

    def forget_check_run_id(self, epoch_id, at):
        """Simulates a lost mapping (used by the missed-check recovery test)."""
        self.conn.execute(
            "UPDATE check_runs SET check_run_id=NULL, updated_at=? "
            "WHERE epoch_id=?", (at, epoch_id))
        self.conn.commit()

    def all_checks(self):
        return self.conn.execute(
            "SELECT * FROM check_runs ORDER BY created_at").fetchall()

    # --- reconciliation --------------------------------------------------
    def start_reconciliation(self, repo, pr_number, at):
        cur = self.conn.execute(
            "INSERT INTO reconciliation_runs (started_at, repo, pr_number,"
            " actions) VALUES (?,?,?,?)", (at, repo, pr_number, "[]"))
        self.conn.commit()
        return cur.lastrowid

    def finish_reconciliation(self, run_id, github_head, stored_head, actions, at):
        self.conn.execute(
            "UPDATE reconciliation_runs SET github_head=?, stored_head=?,"
            " actions=?, finished_at=? WHERE run_id=?",
            (github_head, stored_head, json.dumps(actions), at, run_id))
        self.conn.commit()

    def reconciliations(self):
        return self.conn.execute(
            "SELECT * FROM reconciliation_runs ORDER BY run_id").fetchall()
