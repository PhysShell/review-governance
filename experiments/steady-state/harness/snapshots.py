"""Durable evidence snapshots. The food inside the frozen refrigerator.

The bundle carried a `snapshot_digest` and the snapshot itself lived in a
local variable. A reader holding one SHA-256 can re-derive nothing, so the
claim that the verdict is reproducible was a commitment to a preimage that
no longer existed anywhere.

Snapshots are stored immutably here and referenced by the bundle. Replay
means: load the stored snapshot, recompute its digest, run the frozen
predicate revision against it, and compare with what the bundle claims.
"""
import hashlib
import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS evidence_snapshots (
    snapshot_id      TEXT PRIMARY KEY,
    snapshot_digest  TEXT NOT NULL,
    repo             TEXT NOT NULL,
    pr_number        INTEGER NOT NULL,
    head_sha         TEXT NOT NULL,
    provider         TEXT NOT NULL,
    generation       INTEGER NOT NULL,
    request_id       TEXT NOT NULL,
    payload          TEXT NOT NULL,
    frozen_at        TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS snapshots_immutable_update
BEFORE UPDATE ON evidence_snapshots
BEGIN SELECT RAISE(ABORT, 'a frozen snapshot cannot be rewritten'); END;
CREATE TRIGGER IF NOT EXISTS snapshots_immutable_delete
BEFORE DELETE ON evidence_snapshots
BEGIN SELECT RAISE(ABORT, 'a frozen snapshot cannot be deleted'); END;
"""


class SnapshotError(Exception):
    """Raised where a snapshot would be stored or replayed unsoundly."""


def digest_of(payload):
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()).hexdigest()


class SnapshotStore:
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

    def freeze(self, *, repo, pr_number, head_sha, provider, generation,
               request_id, payload, frozen_at):
        digest = digest_of(payload)
        sid = "snap-" + digest[:24]
        existing = self.snapshot(sid)
        if existing:
            return existing
        self.conn.execute(
            "INSERT INTO evidence_snapshots (snapshot_id, snapshot_digest,"
            " repo, pr_number, head_sha, provider, generation, request_id,"
            " payload, frozen_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sid, digest, repo, int(pr_number), head_sha, provider,
             int(generation), request_id, json.dumps(payload, sort_keys=True),
             frozen_at))
        self.conn.commit()
        return self.snapshot(sid)

    def snapshot(self, snapshot_id):
        row = self.conn.execute(
            "SELECT * FROM evidence_snapshots WHERE snapshot_id=?",
            (snapshot_id,)).fetchone()
        if not row:
            return None
        out = dict(row)
        out["payload"] = json.loads(out["payload"])
        return out

    def replay(self, snapshot_id, predicate_fn):
        """Independent re-derivation: recompute the digest from stored bytes,
        then re-run the frozen predicate. A digest that no longer matches its
        payload means the store was tampered with, not that the verdict
        changed."""
        snap = self.snapshot(snapshot_id)
        if not snap:
            raise SnapshotError(f"no snapshot {snapshot_id}")
        recomputed = digest_of(snap["payload"])
        if recomputed != snap["snapshot_digest"]:
            raise SnapshotError(
                f"stored payload does not hash to its recorded digest: "
                f"{recomputed} != {snap['snapshot_digest']}")
        return {"snapshot_id": snapshot_id,
                "digest_reproduced": True,
                "predicate": predicate_fn(snap["provider"], snap["payload"])}
