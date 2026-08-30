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
CREATE TABLE IF NOT EXISTS baselines (
    baseline_id     TEXT PRIMARY KEY,
    baseline_digest TEXT NOT NULL,
    repo            TEXT NOT NULL,
    pr_number       INTEGER NOT NULL,
    provider        TEXT NOT NULL,
    read_ok         INTEGER NOT NULL,
    payload         TEXT NOT NULL,
    captured_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS provider_revisions (
    revision_id  TEXT PRIMARY KEY,
    snapshot_id  TEXT NOT NULL,
    repo         TEXT NOT NULL,
    pr_number    INTEGER NOT NULL,
    provider     TEXT NOT NULL,
    kind         TEXT NOT NULL,
    revision     TEXT NOT NULL,
    observed_at  TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS revisions_immutable_update
BEFORE UPDATE ON provider_revisions
BEGIN SELECT RAISE(ABORT, 'a recorded revision cannot be rewritten'); END;
CREATE TRIGGER IF NOT EXISTS revisions_immutable_delete
BEFORE DELETE ON provider_revisions
BEGIN SELECT RAISE(ABORT, 'revisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS baselines_immutable_update
BEFORE UPDATE ON baselines
BEGIN SELECT RAISE(ABORT, 'a captured baseline cannot be rewritten'); END;
CREATE TRIGGER IF NOT EXISTS baselines_immutable_delete
BEFORE DELETE ON baselines
BEGIN SELECT RAISE(ABORT, 'a captured baseline cannot be deleted'); END;
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
            scope = (existing["repo"], existing["pr_number"],
                     existing["head_sha"], existing["provider"],
                     existing["generation"], existing["request_id"])
            if scope != (repo, int(pr_number), head_sha, provider,
                         int(generation), request_id):
                raise SnapshotError(
                    "a snapshot with this digest exists under a different "
                    "scope; identical payloads from different rounds must "
                    "not share provenance")
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

    def capture_baseline(self, *, repo, pr_number, provider, read_ok,
                         payload, captured_at):
        """A baseline is a *read*, and a failed read is not an empty one.

        `read_ok` is stored rather than inferred, because an observed empty
        provider surface is perfectly valid on a new PR while an
        unobserved one is not, and the two are otherwise identical.

        The id is per-capture, not content-addressed. Deriving it from the
        payload digest made two readings of an unchanged surface the same
        row, so a request could cite a capture that happened hours before
        it — and the whole point of a baseline is the causal order of the
        reading, not the bytes it found. Identical content in two readings
        is the normal case and must still be two events.
        """
        digest = digest_of(payload)
        seq = (self.conn.execute(
            "SELECT COUNT(*) FROM baselines WHERE repo=? AND pr_number=? "
            "AND provider=?", (repo, int(pr_number), provider)).fetchone()[0])
        bid = "base-" + hashlib.sha256(
            f"{repo}\x00{pr_number}\x00{provider}\x00{captured_at}\x00{seq}"
            f"\x00{digest}".encode()).hexdigest()[:24]
        if self.baseline(bid):
            raise SnapshotError(
                f"baseline {bid} already recorded; a capture is an event and "
                "is written once")
        self.conn.execute(
            "INSERT INTO baselines (baseline_id, baseline_digest, repo,"
            " pr_number, provider, read_ok, payload, captured_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (bid, digest, repo, int(pr_number), provider, 1 if read_ok else 0,
             json.dumps(payload, sort_keys=True), captured_at))
        self.conn.commit()
        return self.baseline(bid)

    def record_revision(self, *, snapshot_id, repo, pr_number, provider, kind,
                        revision):
        """One observed revision of one provider carrier.

        Kept beside the snapshot rather than inside it: a snapshot is what
        the surface showed once and must never change, while the revision
        history is how many times we have looked since.
        """
        seq = self.conn.execute(
            "SELECT COUNT(*) FROM provider_revisions WHERE snapshot_id=?",
            (snapshot_id,)).fetchone()[0]
        rid = "rev-" + hashlib.sha256(
            f"{snapshot_id}\x00{kind}\x00{seq}\x00{revision.get('observed_at')}"
            .encode()).hexdigest()[:24]
        self.conn.execute(
            "INSERT INTO provider_revisions (revision_id, snapshot_id, repo,"
            " pr_number, provider, kind, revision, observed_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (rid, snapshot_id, repo, int(pr_number), provider, kind,
             json.dumps(revision, sort_keys=True), revision.get("observed_at")))
        self.conn.commit()
        return {"revision_id": rid, "snapshot_id": snapshot_id, "kind": kind,
                "revision": revision}

    def revisions_for(self, snapshot_id):
        return [{**dict(r), "revision": json.loads(r["revision"])}
                for r in self.conn.execute(
                    "SELECT * FROM provider_revisions WHERE snapshot_id=? "
                    "ORDER BY rowid", (snapshot_id,))]

    def baseline(self, baseline_id):
        row = self.conn.execute(
            "SELECT * FROM baselines WHERE baseline_id=?",
            (baseline_id,)).fetchone()
        if not row:
            return None
        out = dict(row)
        out["payload"] = json.loads(out["payload"])
        out["read_ok"] = bool(out["read_ok"])
        return out

    def snapshot(self, snapshot_id):
        row = self.conn.execute(
            "SELECT * FROM evidence_snapshots WHERE snapshot_id=?",
            (snapshot_id,)).fetchone()
        if not row:
            return None
        out = dict(row)
        out["payload"] = json.loads(out["payload"])
        return out

    def replay_scoped(self, snapshot_id, predicate_fn, *, repo, pr_number,
                      head_sha, provider, generation, request_id,
                      expected_digest):
        """Replay bound to the round that claims it.

        A digest that reproduces proves the payload is intact. It does not
        prove the payload is *this* round's evidence, which is what the
        bundle is asserting when it cites a snapshot id.
        """
        snap = self.snapshot(snapshot_id)
        if not snap:
            raise SnapshotError(f"no snapshot {snapshot_id}")
        envelope = {
            "repo": snap["repo"] == repo,
            "pr_number": snap["pr_number"] == int(pr_number),
            "head_sha": snap["head_sha"] == head_sha,
            "provider": snap["provider"] == provider,
            "generation": snap["generation"] == int(generation),
            "request_id": snap["request_id"] == request_id,
            "digest": snap["snapshot_digest"] == expected_digest,
        }
        if not all(envelope.values()):
            raise SnapshotError(
                f"snapshot envelope does not match the round citing it: "
                f"{[k for k, v in envelope.items() if not v]}")
        out = self.replay(snapshot_id, predicate_fn)
        out["envelope"] = envelope
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
