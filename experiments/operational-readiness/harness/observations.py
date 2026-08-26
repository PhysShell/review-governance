"""Primary-side observation store for the webhook fast path.

Holds two things: how far the primary has consumed the edge's signal feed,
and what it derived from GitHub for each signal it acted on.

The cursor lives here rather than on the edge on purpose. The edge must
never be able to tell the primary "you have seen everything"; only the
primary knows what it has actually processed, and reconciliation ignores
this table entirely.

An observation stores the whole open-PR snapshot the primary re-read, not a
single PR. A metadata-only signal does not say *which* PR moved, so picking
one would be a guess wearing the costume of a fact.
"""
import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS signal_cursor (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    seq         INTEGER NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS observations (
    seq              INTEGER PRIMARY KEY,
    delivery_guid    TEXT NOT NULL,
    event            TEXT NOT NULL,
    action           TEXT,
    repository       TEXT,
    received_at      TEXT NOT NULL,
    observed_at      TEXT NOT NULL,
    latency_seconds  REAL,
    pr_count         INTEGER NOT NULL,
    pr_snapshot      TEXT NOT NULL,
    source           TEXT NOT NULL DEFAULT 'github-read-after-signal'
);
"""


class ObservationStore:
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

    def cursor(self) -> int:
        row = self.conn.execute("SELECT seq FROM signal_cursor WHERE id=1").fetchone()
        return row["seq"] if row else 0

    def advance(self, seq, at):
        """Advanced only after the observation is durable, never before."""
        self.conn.execute(
            "INSERT INTO signal_cursor (id, seq, updated_at) VALUES (1,?,?) "
            "ON CONFLICT(id) DO UPDATE SET seq=excluded.seq,"
            " updated_at=excluded.updated_at", (int(seq), at))
        self.conn.commit()

    def record(self, *, seq, delivery_guid, event, action, repository,
               received_at, observed_at, latency_seconds, pr_snapshot):
        """`pr_snapshot` is the list of open PRs the primary re-read from
        GitHub, as [{"number": n, "head": sha}, ...]."""
        self.conn.execute(
            "INSERT OR REPLACE INTO observations (seq, delivery_guid, event,"
            " action, repository, received_at, observed_at, latency_seconds,"
            " pr_count, pr_snapshot) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (int(seq), delivery_guid, event, action, repository, received_at,
             observed_at, latency_seconds, len(pr_snapshot),
             json.dumps(pr_snapshot, sort_keys=True)))
        self.conn.commit()

    def observations(self):
        rows = []
        for row in self.conn.execute("SELECT * FROM observations ORDER BY seq"):
            item = dict(row)
            item["pr_snapshot"] = json.loads(item["pr_snapshot"])
            rows.append(item)
        return rows
