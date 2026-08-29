"""Durable acceptances and provider requests. Append-only, scoped.

A6a returned an acceptance as a Python dict and serialised part of it on
request. That is not a transition — nothing survived the process, so
"ACCEPT-CANDIDATE recorded durably" was a sentence in a docstring rather
than a fact on disk.

Two relations, both append-only:

    acceptances       one commit, one authorization observation
    provider_requests intent recorded BEFORE any network call

The ordering in the second is the whole safety property. A request whose
POST response was lost may or may not have reached the provider, and the
only way to tell afterwards is to have written down that we were about to
try. A row written after a successful post is a log; a row written before
is a record.

An acceptance is never updated. A head move produces a new acceptance or
none, and `state` moves only through append: there is no UPDATE anywhere
in this module and the triggers enforce it.
"""
import datetime
import hashlib
import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS acceptances (
    acceptance_id       TEXT PRIMARY KEY,
    repo                TEXT NOT NULL,
    pr_number           INTEGER NOT NULL,
    epoch_id            TEXT NOT NULL,
    head_sha            TEXT NOT NULL,
    accepted_at         TEXT NOT NULL,
    auth_observation_id INTEGER,
    auth_generation     INTEGER,
    state               TEXT NOT NULL,
    UNIQUE (repo, pr_number, head_sha, accepted_at)
);
CREATE TABLE IF NOT EXISTS provider_requests (
    request_id          TEXT PRIMARY KEY,
    acceptance_id       TEXT NOT NULL,
    repo                TEXT NOT NULL,
    pr_number           INTEGER NOT NULL,
    provider            TEXT NOT NULL,
    generation          INTEGER NOT NULL,
    requested_for_head  TEXT NOT NULL,
    auth_observation_id INTEGER,
    intent_recorded_at  TEXT NOT NULL,
    request_carrier_id  INTEGER,
    request_outcome     TEXT NOT NULL,
    outcome_recorded_at TEXT,
    UNIQUE (acceptance_id, provider, generation)
);
CREATE TRIGGER IF NOT EXISTS acceptances_no_delete
BEFORE DELETE ON acceptances
BEGIN SELECT RAISE(ABORT, 'acceptances are append-only'); END;
CREATE TRIGGER IF NOT EXISTS acceptances_no_scope_update
BEFORE UPDATE OF repo, pr_number, head_sha, epoch_id ON acceptances
BEGIN SELECT RAISE(ABORT, 'an acceptance is about one commit and cannot be repointed'); END;
CREATE TRIGGER IF NOT EXISTS requests_no_delete
BEFORE DELETE ON provider_requests
BEGIN SELECT RAISE(ABORT, 'provider requests are append-only'); END;
CREATE TRIGGER IF NOT EXISTS requests_no_intent_update
BEFORE UPDATE OF requested_for_head, provider, generation, acceptance_id
ON provider_requests
BEGIN SELECT RAISE(ABORT, 'recorded intent cannot be rewritten'); END;
"""

ACCEPTED = "ACCEPTED"
INVALIDATED = "INVALIDATED"

INTENT_RECORDED = "INTENT_RECORDED"
SENT = "SENT"
OUTCOME_UNKNOWN = "REQUEST_OUTCOME_UNKNOWN"


class RoundError(Exception):
    """Raised where a record would be written without provable scope."""


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ident(prefix, payload):
    return prefix + hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24]


class RoundStore:
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

    # --- acceptances ------------------------------------------------------
    def record_acceptance(self, *, repo, pr_number, epoch_id, head_sha,
                          permission, accepted_at=None):
        if len(head_sha or "") != 40:
            raise RoundError("an acceptance must name the full head")
        if not getattr(permission, "permits_action", False):
            raise RoundError(
                f"acceptance refused: authorization permission is "
                f"{getattr(permission, 'state', 'MISSING')}")
        accepted_at = accepted_at or utcnow()
        aid = _ident("acc-", {"repo": repo, "pr": int(pr_number),
                              "head": head_sha, "at": accepted_at})
        self.conn.execute(
            "INSERT INTO acceptances (acceptance_id, repo, pr_number,"
            " epoch_id, head_sha, accepted_at, auth_observation_id,"
            " auth_generation, state) VALUES (?,?,?,?,?,?,?,?,?)",
            (aid, repo, int(pr_number), epoch_id, head_sha, accepted_at,
             permission.observation_id, permission.auth_generation, ACCEPTED))
        self.conn.commit()
        return self.acceptance(aid)

    def acceptance(self, acceptance_id):
        row = self.conn.execute(
            "SELECT * FROM acceptances WHERE acceptance_id=?",
            (acceptance_id,)).fetchone()
        return dict(row) if row else None

    def acceptances_for(self, repo, pr_number):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM acceptances WHERE repo=? AND pr_number=? "
            "ORDER BY rowid", (repo, int(pr_number)))]

    def current_acceptance(self, repo, pr_number, head_sha):
        """Only an acceptance about *this* commit counts.

        Deliberately filtered by head rather than by recency: the most
        recent acceptance for a PR may be about a commit that no longer
        exists, and returning it would let evidence gathered for one head
        authorise work on another.
        """
        rows = [a for a in self.acceptances_for(repo, pr_number)
                if a["head_sha"] == head_sha and a["state"] == ACCEPTED]
        return rows[-1] if rows else None

    def invalidate_for_head_move(self, repo, pr_number, current_head, at=None):
        """Mark acceptances about vanished commits, without repointing any.

        `state` is the one column that may move, and only to INVALIDATED.
        The schema trigger refuses any attempt to change what an acceptance
        is about.
        """
        at = at or utcnow()
        stale = [a for a in self.acceptances_for(repo, pr_number)
                 if a["state"] == ACCEPTED and a["head_sha"] != current_head]
        for a in stale:
            self.conn.execute(
                "UPDATE acceptances SET state=? WHERE acceptance_id=?",
                (INVALIDATED, a["acceptance_id"]))
        self.conn.commit()
        return [{"acceptance_id": a["acceptance_id"],
                 "was_for_head": a["head_sha"], "current_head": current_head,
                 "state": INVALIDATED, "at": at} for a in stale]

    # --- provider requests -------------------------------------------------
    def record_intent(self, *, acceptance_id, repo, pr_number, provider,
                      generation, requested_for_head, permission,
                      intent_recorded_at=None):
        """Written BEFORE the network call, never after."""
        if not self.acceptance(acceptance_id):
            raise RoundError("no such acceptance")
        acc = self.acceptance(acceptance_id)
        if acc["state"] != ACCEPTED:
            raise RoundError(f"acceptance is {acc['state']}")
        if acc["head_sha"] != requested_for_head:
            raise RoundError(
                "a request may only be made for the head its acceptance is "
                "about")
        at = intent_recorded_at or utcnow()
        rid = _ident("req-", {"acc": acceptance_id, "provider": provider,
                              "gen": int(generation)})
        self.conn.execute(
            "INSERT INTO provider_requests (request_id, acceptance_id, repo,"
            " pr_number, provider, generation, requested_for_head,"
            " auth_observation_id, intent_recorded_at, request_carrier_id,"
            " request_outcome) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (rid, acceptance_id, repo, int(pr_number), provider,
             int(generation), requested_for_head,
             getattr(permission, "observation_id", None), at, None,
             INTENT_RECORDED))
        self.conn.commit()
        return self.request(rid)

    def settle_request(self, request_id, *, outcome, carrier_id=None, at=None):
        if outcome not in (SENT, OUTCOME_UNKNOWN):
            raise RoundError(f"unknown request outcome {outcome!r}")
        self.conn.execute(
            "UPDATE provider_requests SET request_outcome=?,"
            " request_carrier_id=?, outcome_recorded_at=? WHERE request_id=?",
            (outcome, carrier_id, at or utcnow(), request_id))
        self.conn.commit()
        return self.request(request_id)

    def request(self, request_id):
        row = self.conn.execute(
            "SELECT * FROM provider_requests WHERE request_id=?",
            (request_id,)).fetchone()
        return dict(row) if row else None

    def requests_for(self, acceptance_id):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM provider_requests WHERE acceptance_id=? "
            "ORDER BY rowid", (acceptance_id,))]
