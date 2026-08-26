"""Edge-host storage.

Deliberately small, and deliberately incapable of holding a policy verdict.
Three tables: what GitHub delivered, when the primary was last alive, and
what the watchdog did about it. Losing this database cannot manufacture a
success, because no authoritative success is ever born here.
"""
import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    delivery_guid   TEXT PRIMARY KEY,
    event           TEXT NOT NULL,
    action          TEXT,
    repository      TEXT,
    received_at     TEXT NOT NULL,
    body_hash       TEXT NOT NULL,
    processing_state TEXT NOT NULL DEFAULT 'RECEIVED'
);
CREATE TABLE IF NOT EXISTS primary_heartbeat (
    primary_instance_id TEXT PRIMARY KEY,
    last_seen_at        TEXT NOT NULL,
    last_seen_epoch     REAL NOT NULL,
    payload             TEXT
);
CREATE TABLE IF NOT EXISTS watchdog_incidents (
    incident_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at         TEXT NOT NULL,
    stale_age_seconds   REAL NOT NULL,
    primary_instance_id TEXT,
    affected_check_runs TEXT NOT NULL,
    results             TEXT NOT NULL,
    closed_at           TEXT
);
"""

RECEIVED = "RECEIVED"
PROCESSED = "PROCESSED"
DROPPED = "DROPPED"          # used by the A5a-c1 missed-delivery injection


class EdgeStore:
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

    # --- webhook deliveries ----------------------------------------------
    def record_delivery(self, *, guid, event, action, repository, received_at,
                        body_hash):
        """Durable *before* the ACK. Returns True if this GUID is new."""
        try:
            self.conn.execute(
                "INSERT INTO webhook_deliveries (delivery_guid, event, action,"
                " repository, received_at, body_hash, processing_state)"
                " VALUES (?,?,?,?,?,?,?)",
                (guid, event, action, repository, received_at, body_hash,
                 RECEIVED))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False           # GitHub reuses the GUID on redelivery

    def delivery(self, guid):
        return self.conn.execute(
            "SELECT * FROM webhook_deliveries WHERE delivery_guid=?",
            (guid,)).fetchone()

    def set_processing_state(self, guid, state):
        self.conn.execute(
            "UPDATE webhook_deliveries SET processing_state=? "
            "WHERE delivery_guid=?", (state, guid))
        self.conn.commit()

    def signals_after(self, cursor=0, limit=100):
        """Metadata-only feed for the primary's fast path.

        Deliberately returns no payload: the signal says *something changed*,
        and the primary re-reads GitHub to derive the observation itself. The
        implicit rowid is the cursor, so a primary that falls behind resumes
        exactly where it stopped.
        """
        rows = self.conn.execute(
            "SELECT rowid AS seq, delivery_guid, event, action, repository,"
            " received_at, body_hash FROM webhook_deliveries WHERE rowid > ?"
            " ORDER BY rowid LIMIT ?", (int(cursor), int(limit))).fetchall()
        return [dict(row) for row in rows]

    def deliveries(self, state=None):
        if state:
            return self.conn.execute(
                "SELECT * FROM webhook_deliveries WHERE processing_state=? "
                "ORDER BY received_at", (state,)).fetchall()
        return self.conn.execute(
            "SELECT * FROM webhook_deliveries ORDER BY received_at").fetchall()

    # --- heartbeat --------------------------------------------------------
    def record_heartbeat(self, *, instance_id, at, epoch, payload=None):
        self.conn.execute(
            "INSERT INTO primary_heartbeat (primary_instance_id, last_seen_at,"
            " last_seen_epoch, payload) VALUES (?,?,?,?) "
            "ON CONFLICT(primary_instance_id) DO UPDATE SET"
            " last_seen_at=excluded.last_seen_at,"
            " last_seen_epoch=excluded.last_seen_epoch,"
            " payload=excluded.payload",
            (instance_id, at, epoch, json.dumps(payload or {})))
        self.conn.commit()

    def latest_heartbeat(self):
        return self.conn.execute(
            "SELECT * FROM primary_heartbeat ORDER BY last_seen_epoch DESC "
            "LIMIT 1").fetchone()

    # --- incidents --------------------------------------------------------
    def open_incident(self, *, detected_at, stale_age, primary_instance_id,
                      affected, results):
        cur = self.conn.execute(
            "INSERT INTO watchdog_incidents (detected_at, stale_age_seconds,"
            " primary_instance_id, affected_check_runs, results)"
            " VALUES (?,?,?,?,?)",
            (detected_at, stale_age, primary_instance_id,
             json.dumps(affected), json.dumps(results)))
        self.conn.commit()
        return cur.lastrowid

    def incidents(self):
        return self.conn.execute(
            "SELECT * FROM watchdog_incidents ORDER BY incident_id").fetchall()
