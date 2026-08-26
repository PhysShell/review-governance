#!/usr/bin/env python3
"""The edge service: webhook receiver + heartbeat sink, for the dedicated VPS.

Two endpoints, both small on purpose:

    POST /github/webhook   HMAC over the raw body -> durable INSERT -> 2xx
    POST /primary/heartbeat  HMAC over the raw body -> record last-seen

Order is the contract in both cases: nothing is acknowledged before it is
durable. A webhook here is a *signal* that something changed, never a second
authoritative record — the primary re-reads GitHub and derives its own
observation.

This process publishes nothing to GitHub. Revocation lives in
`edge_watchdog.py`, which is the only component on this host allowed to
write, and only ever downward.
"""
import argparse
import datetime
import hashlib
import hmac
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import edge_store

CONFIG_DIR = Path(os.environ.get(
    "GOVERNOR_EDGE_CONFIG", os.path.expanduser("~/.config/review-governor-edge")))
WEBHOOK_SECRET_PATH = CONFIG_DIR / "webhook-secret"
HEARTBEAT_SECRET_PATH = CONFIG_DIR / "heartbeat-secret"
MAX_BODY = 5 * 1024 * 1024
RELEVANT_EVENTS = ("pull_request", "check_run", "check_suite",
                   "github_app_authorization", "push")


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def verify(secret: bytes, raw: bytes, provided: str, prefix="sha256=") -> bool:
    if not provided or not provided.startswith(prefix):
        return False
    expected = prefix + hmac.new(secret, raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)


class EdgeService:
    """Transport-independent core so the tests exercise what actually runs."""

    def __init__(self, store, webhook_secret, heartbeat_secret):
        self.store = store
        self.webhook_secret = webhook_secret
        self.heartbeat_secret = heartbeat_secret
        self.rejected = []

    # --- webhook ---------------------------------------------------------
    def handle_webhook(self, headers, raw):
        lower = {k.lower(): v for k, v in headers.items()}
        signature = lower.get("x-hub-signature-256")
        guid = lower.get("x-github-delivery")
        event = lower.get("x-github-event")

        if not verify(self.webhook_secret, raw, signature):
            self.rejected.append({"at": utcnow(), "reason": "bad_signature",
                                  "guid_not_consumed": guid})
            return 401, {"error": "signature verification failed"}
        if not guid or not event:
            return 400, {"error": "missing delivery id or event"}
        try:
            payload = json.loads(raw.decode())
        except ValueError:
            return 400, {"error": "malformed payload"}

        # durable BEFORE the ACK — a delivery GitHub believes it made must
        # never exist only in this process's memory
        fresh = self.store.record_delivery(
            guid=guid, event=event, action=payload.get("action"),
            repository=(payload.get("repository") or {}).get("full_name"),
            received_at=utcnow(),
            body_hash=hashlib.sha256(raw).hexdigest())
        return 202, {"accepted": True, "duplicate": not fresh,
                     "relevant": event in RELEVANT_EVENTS,
                     "note": "signal only; the primary re-reads GitHub"}

    # --- signals ---------------------------------------------------------
    def handle_signals(self, headers, after):
        """Authenticated, metadata-only feed. The signature covers the cursor
        so a request cannot be replayed against a different position."""
        lower = {k.lower(): v for k, v in headers.items()}
        if not verify(self.heartbeat_secret, f"signals:{after}".encode(),
                      lower.get("x-governor-signature")):
            self.rejected.append({"at": utcnow(), "reason": "bad_signal_sig"})
            return 401, {"error": "signature verification failed"}
        signals = self.store.signals_after(after)
        return 200, {"after": int(after), "count": len(signals),
                     "signals": signals,
                     "note": "metadata only; re-read GitHub for the truth"}

    # --- ack -------------------------------------------------------------
    def handle_ack(self, headers, raw):
        """The primary saying how far it has got. Advisory, never authority.

        This does not tell the primary anything and cannot be used to skip
        work: the primary's own cursor is unaffected, and reconciliation
        ignores both. Its only job is to let `delivery_stuck` describe a
        condition that can actually end.
        """
        lower = {k.lower(): v for k, v in headers.items()}
        if not verify(self.heartbeat_secret, raw,
                      lower.get("x-governor-signature")):
            self.rejected.append({"at": utcnow(), "reason": "bad_ack_sig"})
            return 401, {"error": "signature verification failed"}
        try:
            body = json.loads(raw.decode())
            through = int(body["through"])
        except (ValueError, KeyError, TypeError):
            return 400, {"error": "ack needs an integer `through`"}
        moved = self.store.mark_processed_through(through)
        return 202, {"accepted": True, "through": through, "marked": moved,
                     "note": "advisory only; the primary's cursor is the "
                             "authority and is not stored here"}

    # --- heartbeat -------------------------------------------------------
    def handle_heartbeat(self, headers, raw):
        lower = {k.lower(): v for k, v in headers.items()}
        if not verify(self.heartbeat_secret, raw,
                      lower.get("x-governor-signature")):
            self.rejected.append({"at": utcnow(), "reason": "bad_heartbeat_sig"})
            return 401, {"error": "heartbeat signature verification failed"}
        try:
            beat = json.loads(raw.decode())
        except ValueError:
            return 400, {"error": "malformed heartbeat"}
        instance = beat.get("primary_instance_id")
        if not instance:
            return 400, {"error": "heartbeat without an instance id"}
        now = datetime.datetime.now(datetime.timezone.utc)
        self.store.record_heartbeat(instance_id=instance, at=utcnow(),
                                    epoch=now.timestamp(), payload=beat)
        return 202, {"accepted": True, "recorded_at": utcnow()}


def serve(service, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass                       # never log headers or bodies

        def _send(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self.send_error(413)
                return
            raw = self.rfile.read(length)
            headers = dict(self.headers.items())
            if self.path == "/github/webhook":
                status, body = service.handle_webhook(headers, raw)
            elif self.path == "/primary/heartbeat":
                status, body = service.handle_heartbeat(headers, raw)
            elif self.path == "/signals/ack":
                status, body = service.handle_ack(headers, raw)
            else:
                status, body = 404, {"error": "not found"}
            self._send(status, body)

        def do_GET(self):
            if self.path.startswith("/signals"):
                query = urllib.parse.urlparse(self.path).query
                after = urllib.parse.parse_qs(query).get("after", ["0"])[0]
                try:
                    cursor = int(after)
                except ValueError:
                    self._send(400, {"error": "after must be an integer"})
                    return
                status, body = service.handle_signals(dict(self.headers.items()),
                                                      cursor)
                self._send(status, body)
            elif self.path == "/healthz":
                beat = service.store.latest_heartbeat()
                self._send(200, {"ok": True,
                                 "last_primary_heartbeat":
                                     beat["last_seen_at"] if beat else None})
            else:
                self._send(404, {"error": "not found"})

    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8931)
    ap.add_argument("--db", default=str(CONFIG_DIR / "edge.sqlite3"))
    args = ap.parse_args()
    for path in (WEBHOOK_SECRET_PATH, HEARTBEAT_SECRET_PATH):
        if not path.exists():
            print(f"missing secret: {path}", file=sys.stderr)
            return 2
    service = EdgeService(edge_store.EdgeStore(args.db),
                          WEBHOOK_SECRET_PATH.read_bytes().strip(),
                          HEARTBEAT_SECRET_PATH.read_bytes().strip())
    print(f"edge listening on 127.0.0.1:{args.port} "
          "(terminate TLS in front of this)", flush=True)
    serve(service, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
