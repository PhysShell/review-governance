"""PR-scoped durable production epochs.

The defect this replaces was not a missing column. `last_known_head` took
`repo` and `pr`, used neither, and could not: the table had nowhere to put
them. It returned `None`, and `None` was read as "no drift" — a comparison
that never ran, reported as a comparison that found nothing.

So scope is identity here, not a parameter:

    repo · pr_number · full head_sha · generation

and the lookup answers in three states rather than two:

    RESOLVED    a scoped record exists for this (repo, PR)
    NO_EPOCH    nothing was ever decided here
    UNRESOLVED  records exist but scope cannot be established

`UNRESOLVED` fails closed. It is the state that did not exist before, and
its absence is what let an inert comparison look reassuring.

Append-only, like every decision store in this programme: the question
"when did this head become current" must survive whatever happened after.
"""
import hashlib
import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS production_epochs (
    epoch_id    TEXT PRIMARY KEY,
    repo        TEXT NOT NULL,
    pr_number   INTEGER NOT NULL,
    head_sha    TEXT NOT NULL,
    generation  INTEGER NOT NULL,
    opened_at   TEXT NOT NULL,
    source      TEXT NOT NULL,
    UNIQUE (repo, pr_number, head_sha, generation)
);
CREATE TABLE IF NOT EXISTS production_decisions (
    decision_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    epoch_id      TEXT NOT NULL,
    verdict       TEXT NOT NULL,
    bundle_hash   TEXT,
    bundle_schema TEXT,
    cause         TEXT,
    decided_at    TEXT NOT NULL,
    previous_decision_id INTEGER
);
CREATE TABLE IF NOT EXISTS production_projections (
    epoch_id      TEXT PRIMARY KEY,
    check_run_id  INTEGER,
    intended      TEXT,
    observed      TEXT,
    state         TEXT NOT NULL,
    decision_id   INTEGER,
    attempted_at  TEXT,
    settled_at    TEXT,
    updated_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS migration_records (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    legacy_epoch  TEXT NOT NULL,
    legacy_head   TEXT NOT NULL,
    mapped_to     TEXT,
    justification TEXT NOT NULL,
    source_artifact TEXT NOT NULL,
    at            TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS epochs_append_only_update
BEFORE UPDATE ON production_epochs
BEGIN SELECT RAISE(ABORT, 'production epochs are append-only'); END;
CREATE TRIGGER IF NOT EXISTS epochs_append_only_delete
BEFORE DELETE ON production_epochs
BEGIN SELECT RAISE(ABORT, 'production epochs are append-only'); END;
CREATE TRIGGER IF NOT EXISTS decisions_append_only_update
BEFORE UPDATE ON production_decisions
BEGIN SELECT RAISE(ABORT, 'production decisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS decisions_append_only_delete
BEFORE DELETE ON production_decisions
BEGIN SELECT RAISE(ABORT, 'production decisions are append-only'); END;
"""

RESOLVED = "RESOLVED"
NO_EPOCH = "NO_EPOCH"
UNRESOLVED = "UNRESOLVED"


class ScopeError(Exception):
    """Raised where an epoch would be created without provable scope."""


def epoch_id(repo: str, pr_number: int, head_sha: str, generation: int) -> str:
    """Derived from the identity tuple, so an id cannot disagree with it.

    Deliberately a hash rather than a readable join: `bootstrap-8aeafa9c`
    looked like an identifier and was actually a head prefix, which is how
    a scope came to be inferred from a substring.
    """
    if len(head_sha) != 40:
        raise ScopeError(f"head must be a full SHA, got {head_sha!r}")
    if not repo or pr_number is None:
        raise ScopeError("repo and pr_number are part of identity")
    payload = json.dumps({"repo": repo, "pr_number": int(pr_number),
                          "head_sha": head_sha, "generation": int(generation)},
                         sort_keys=True)
    return "pe-" + hashlib.sha256(payload.encode()).hexdigest()[:24]


class EpochStore:
    def __init__(self, path):
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
    def open_epoch(self, *, repo, pr_number, head_sha, opened_at,
                   source="steady-state", generation=None):
        """Open, or return the existing epoch for this exact identity."""
        if generation is None:
            row = self.conn.execute(
                "SELECT MAX(generation) g FROM production_epochs "
                "WHERE repo=? AND pr_number=? AND head_sha=?",
                (repo, int(pr_number), head_sha)).fetchone()
            generation = (row["g"] or 0) + 1 if row["g"] is not None else 1
        eid = epoch_id(repo, pr_number, head_sha, generation)
        existing = self.epoch(eid)
        if existing:
            return existing
        self.conn.execute(
            "INSERT INTO production_epochs (epoch_id, repo, pr_number,"
            " head_sha, generation, opened_at, source) VALUES (?,?,?,?,?,?,?)",
            (eid, repo, int(pr_number), head_sha, int(generation), opened_at,
             source))
        self.conn.commit()
        return self.epoch(eid)

    def epoch(self, eid):
        row = self.conn.execute(
            "SELECT * FROM production_epochs WHERE epoch_id=?", (eid,)).fetchone()
        return dict(row) if row else None

    def epochs_for(self, repo, pr_number):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM production_epochs WHERE repo=? AND pr_number=? "
            "ORDER BY generation, rowid", (repo, int(pr_number)))]

    # --- the three-state lookup ------------------------------------------
    def last_known_head(self, repo, pr_number):
        """Answer only what can be proven about this (repo, PR).

        The tri-state is the whole point. `NO_EPOCH` says nothing was
        decided; `UNRESOLVED` says something was, but its scope cannot be
        established — and the caller must fail closed rather than treat it
        as absence of drift.
        """
        scoped = self.epochs_for(repo, pr_number)
        if scoped:
            latest = scoped[-1]
            return {"state": RESOLVED, "head_sha": latest["head_sha"],
                    "epoch_id": latest["epoch_id"],
                    "generation": latest["generation"],
                    "repo": latest["repo"], "pr_number": latest["pr_number"]}
        unmapped = self.conn.execute(
            "SELECT COUNT(*) n FROM migration_records WHERE mapped_to IS NULL"
        ).fetchone()["n"]
        any_epoch = self.conn.execute(
            "SELECT COUNT(*) n FROM production_epochs").fetchone()["n"]
        if unmapped:
            return {"state": UNRESOLVED,
                    "cause": f"{unmapped} legacy decision(s) could not be "
                             "scoped; this PR may be among them",
                    "repo": repo, "pr_number": pr_number}
        if any_epoch:
            return {"state": NO_EPOCH, "repo": repo, "pr_number": pr_number}
        return {"state": NO_EPOCH, "repo": repo, "pr_number": pr_number}

    # --- decisions and projections ---------------------------------------
    def record_decision(self, *, epoch_id, verdict, decided_at, cause=None,
                        bundle_hash=None, bundle_schema=None):
        if not self.epoch(epoch_id):
            raise ScopeError(f"no such epoch: {epoch_id}")
        previous = self.conn.execute(
            "SELECT decision_id FROM production_decisions "
            "ORDER BY decision_id DESC LIMIT 1").fetchone()
        cur = self.conn.execute(
            "INSERT INTO production_decisions (epoch_id, verdict, bundle_hash,"
            " bundle_schema, cause, decided_at, previous_decision_id)"
            " VALUES (?,?,?,?,?,?,?)",
            (epoch_id, verdict, bundle_hash, bundle_schema, cause, decided_at,
             previous["decision_id"] if previous else None))
        self.conn.commit()
        return cur.lastrowid

    def decisions_for(self, epoch_id):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM production_decisions WHERE epoch_id=? "
            "ORDER BY decision_id", (epoch_id,))]

    def project(self, *, epoch_id, check_run_id, intended, state, decision_id,
                at, observed=None):
        self.conn.execute(
            "INSERT INTO production_projections (epoch_id, check_run_id,"
            " intended, observed, state, decision_id, attempted_at,"
            " settled_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(epoch_id) DO UPDATE SET check_run_id=excluded.check_run_id,"
            " intended=excluded.intended, observed=excluded.observed,"
            " state=excluded.state, settled_at=excluded.settled_at,"
            " updated_at=excluded.updated_at",
            (epoch_id, check_run_id, intended, observed, state, decision_id,
             at, at if state != "PENDING" else None, at))
        self.conn.commit()

    def projection(self, epoch_id):
        row = self.conn.execute(
            "SELECT * FROM production_projections WHERE epoch_id=?",
            (epoch_id,)).fetchone()
        return dict(row) if row else None

    # --- migration --------------------------------------------------------
    def record_migration(self, *, legacy_epoch, legacy_head, mapped_to,
                         justification, source_artifact, at):
        self.conn.execute(
            "INSERT INTO migration_records (legacy_epoch, legacy_head,"
            " mapped_to, justification, source_artifact, at)"
            " VALUES (?,?,?,?,?,?)",
            (legacy_epoch, legacy_head, mapped_to, justification,
             source_artifact, at))
        self.conn.commit()

    def migrations(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM migration_records ORDER BY id")]
