"""Alerting: get a sentence to a human, outside both hosts.

Three decisions are load-bearing here.

**The payload is an allowlist.** Fields not on the list raise rather than
being stripped, because a redaction pass is a list of the leaks somebody
thought of. Webhook bodies, OAuth material, provider evidence and PEMs are
not filtered out; there is nowhere to put them.

**Every alert is a state transition, not a log line.** A condition that is
still true does not re-page every poll, and a condition that clears sends a
recovery. A red light nobody watches turn green becomes a red light that
gets muted, and then the next real one arrives to an audience of nobody.

**Delivery failure is itself visible.** If the channel is down the alert is
kept open and retried, and the failure is recorded locally, because a
notifier that silently drops is worse than none: it converts an outage into
the appearance of calm.
"""
import datetime
import json
import os
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG_DIR = Path(os.environ.get(
    "GOVERNOR_ALERT_CONFIG",
    os.environ.get("GOVERNOR_EDGE_CONFIG",
                   os.path.expanduser("~/.config/review-governor"))))

CRITICAL = "CRITICAL"
WARNING = "WARNING"
RECOVERY = "RECOVERY"
SEVERITIES = (CRITICAL, WARNING, RECOVERY)

#: The complete set of fields that may ever leave the host.
ALLOWED_FIELDS = ("severity", "cause", "repo", "pr_number", "check_run_id",
                  "incident_id", "detected_at", "state")
REQUIRED_FIELDS = ("severity", "cause", "detected_at")

#: Preregistered causes. A typo must not silently become a new alert class
#: that nobody has a runbook entry for.
CAUSES = (
    "heartbeat_age_critical",
    "heartbeat_age_warning",
    "watchdog_incident",
    "watchdog_revocation_outcome_unknown",
    "watchdog_revocation_failed",
    "installation_token_mint_failed",
    "auth_lost",
    "refresh_outcome_unknown",
    "reconciliation_stale",
    "webhook_receiver_unavailable",
    "delivery_stuck",
)

#: While a condition stays true, re-notify at most this often.
RENOTIFY_SECONDS = 900

SCHEMA = """
CREATE TABLE IF NOT EXISTS open_alerts (
    cause          TEXT PRIMARY KEY,
    severity       TEXT NOT NULL,
    opened_at      TEXT NOT NULL,
    last_sent_at   TEXT,
    last_payload   TEXT NOT NULL,
    delivered      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS alert_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    at           TEXT NOT NULL,
    severity     TEXT NOT NULL,
    cause        TEXT NOT NULL,
    payload      TEXT NOT NULL,
    transport    TEXT NOT NULL,
    delivered    INTEGER NOT NULL,
    error        TEXT
);
"""


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value):
    return datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=datetime.timezone.utc)


class PayloadRefused(Exception):
    """Raised instead of quietly sending something that should never leave."""


def build_payload(severity, cause, detected_at=None, **fields):
    """Construct the only shape allowed on the wire.

    Rejects unknown fields rather than dropping them: a caller that thinks
    it is attaching context deserves to find out at the call site, not to
    have it silently vanish and be reintroduced by the next refactor.
    """
    if severity not in SEVERITIES:
        raise PayloadRefused(f"unknown severity: {severity!r}")
    if cause not in CAUSES:
        raise PayloadRefused(f"unregistered cause: {cause!r}")
    extra = set(fields) - set(ALLOWED_FIELDS)
    if extra:
        raise PayloadRefused(
            f"fields not on the allowlist: {sorted(extra)}. "
            "Alert payloads carry identifiers, never content.")
    payload = {"severity": severity, "cause": cause,
               "detected_at": detected_at or utcnow()}
    for key in ALLOWED_FIELDS:
        if key in fields and fields[key] is not None:
            payload[key] = fields[key]
    missing = [f for f in REQUIRED_FIELDS if f not in payload]
    if missing:
        raise PayloadRefused(f"missing required fields: {missing}")
    return payload


def render(payload, origin):
    """One screen, readable on a phone at an unkind hour."""
    head = {"CRITICAL": "\U0001F534", "WARNING": "\U0001F7E0",
            "RECOVERY": "\U0001F7E2"}.get(payload["severity"], "")
    lines = [f"{head} {payload['severity']} · {payload['cause']}",
             f"origin: {origin}"]
    for key in ("repo", "pr_number", "check_run_id", "incident_id", "state"):
        if key in payload:
            lines.append(f"{key}: {payload[key]}")
    lines.append(f"detected_at: {payload['detected_at']}")
    return "\n".join(lines)


# --- transports --------------------------------------------------------------

class NullTransport:
    """Used by tests and by --dry-run. Records, never sends."""
    name = "null"

    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return True, None


class TelegramTransport:
    name = "telegram"

    def __init__(self, token, chat_id, timeout=10):
        self.token = token
        self.chat_id = chat_id
        self.timeout = timeout

    def send(self, text):
        data = urllib.parse.urlencode({
            "chat_id": self.chat_id, "text": text,
            "disable_web_page_preview": "true"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{self.token}/sendMessage",
            data=data, headers={"User-Agent": "governor-alerting"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode() or "{}")
                return bool(body.get("ok")), None
        except urllib.error.HTTPError as e:
            # Telegram puts the reason in the body; the token is in the URL,
            # never in the message, so this is safe to keep.
            return False, f"HTTP {e.code}: {e.read().decode()[:200]}"
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"


def transport_from_config(config_dir=None, dry_run=False):
    if dry_run:
        return NullTransport()
    config_dir = Path(config_dir or CONFIG_DIR)
    path = config_dir / "alerting.json"
    if not path.exists():
        return None
    config = json.loads(path.read_text())
    if config.get("channel") == "telegram":
        return TelegramTransport(config["bot_token"], config["chat_id"])
    raise PayloadRefused(f"unsupported channel: {config.get('channel')!r}")


# --- notifier ----------------------------------------------------------------

class Notifier:
    """Transition-driven. `raise_` and `clear` are idempotent by design."""

    def __init__(self, db_path, transport, origin, renotify=RENOTIFY_SECONDS):
        self.path = str(db_path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self.transport = transport
        self.origin = origin
        self.renotify = renotify

    def close(self):
        self.conn.close()

    # -- internals --
    def _open(self, cause):
        return self.conn.execute(
            "SELECT * FROM open_alerts WHERE cause=?", (cause,)).fetchone()

    def _deliver(self, payload):
        text = render(payload, self.origin)
        ok, error = self.transport.send(text)
        self.conn.execute(
            "INSERT INTO alert_log (at, severity, cause, payload, transport,"
            " delivered, error) VALUES (?,?,?,?,?,?,?)",
            (utcnow(), payload["severity"], payload["cause"],
             json.dumps(payload, sort_keys=True), self.transport.name,
             1 if ok else 0, error))
        self.conn.commit()
        return ok, error

    # -- api --
    def raise_(self, severity, cause, **fields):
        """Open a condition, or keep it open. Sends on the transition, and
        again only after `renotify` seconds, or if a previous send failed."""
        payload = build_payload(severity, cause, **fields)
        row = self._open(cause)
        now = utcnow()
        should_send = True
        if row:
            if row["delivered"] and row["last_sent_at"]:
                age = (parse_ts(now) - parse_ts(row["last_sent_at"])).total_seconds()
                should_send = age >= self.renotify
            # an undelivered alert is retried every time until it lands
        if not should_send:
            return {"sent": False, "reason": "already open and recently sent",
                    "payload": payload}
        ok, error = self._deliver(payload)
        self.conn.execute(
            "INSERT INTO open_alerts (cause, severity, opened_at, last_sent_at,"
            " last_payload, delivered) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(cause) DO UPDATE SET severity=excluded.severity,"
            " last_sent_at=excluded.last_sent_at,"
            " last_payload=excluded.last_payload, delivered=excluded.delivered",
            (cause, severity, row["opened_at"] if row else now, now,
             json.dumps(payload, sort_keys=True), 1 if ok else 0))
        self.conn.commit()
        return {"sent": True, "delivered": ok, "error": error,
                "payload": payload}

    def clear(self, cause, **fields):
        """Close a condition and tell the human it closed.

        A no-op when nothing was open — recovery for an incident nobody was
        told about is noise, and noise is how alerting dies.
        """
        row = self._open(cause)
        if not row:
            return {"sent": False, "reason": "nothing was open"}
        payload = build_payload(RECOVERY, cause, **fields)
        payload["state"] = fields.get("state", "RECOVERED")
        ok, error = self._deliver(payload)
        if ok:
            self.conn.execute("DELETE FROM open_alerts WHERE cause=?", (cause,))
            self.conn.commit()
        return {"sent": True, "delivered": ok, "error": error,
                "was_open_since": row["opened_at"], "payload": payload}

    def open_causes(self):
        return [row["cause"] for row in self.conn.execute(
            "SELECT cause FROM open_alerts ORDER BY cause")]

    def log(self, limit=50):
        return [dict(row) for row in self.conn.execute(
            "SELECT * FROM alert_log ORDER BY id DESC LIMIT ?", (limit,))]
