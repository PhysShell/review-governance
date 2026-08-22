#!/usr/bin/env python3
"""Capture-only first-party webhook receiver for A2a.

Order of operations is the contract:

    read raw body
      -> verify HMAC over the raw bytes
      -> ONLY THEN consume / record the X-GitHub-Delivery id
      -> reduce into epoch and authorization state
      -> append a sanitized envelope to the capture log

A rejected delivery leaves no trace in the idempotency store, so a forged
request cannot burn a delivery id that a genuine redelivery would need.

The receiver implements no Governor engine: it creates no Check Runs, posts
nothing to GitHub, and triggers no providers.
"""
import argparse
import datetime
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import control_plane
import verify

CONFIG_DIR = Path(os.environ.get(
    "GOVERNOR_CONFIG_DIR", os.path.expanduser("~/.config/review-governor")))
SECRET_PATH = CONFIG_DIR / "webhook-secret"
MAX_BODY = 5 * 1024 * 1024


def utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sanitize(event: str, payload: dict) -> dict:
    """Keep identity, action and SHA fields; drop everything else."""
    pull = payload.get("pull_request") or {}
    head = pull.get("head") or {}
    base = pull.get("base") or {}
    sender = payload.get("sender") or {}
    installation = payload.get("installation") or {}
    return {
        "event": event,
        "action": payload.get("action"),
        "repository": (payload.get("repository") or {}).get("full_name"),
        "pull_request": ({"number": pull.get("number"),
                          "draft": pull.get("draft"),
                          "state": pull.get("state"),
                          "head_sha": head.get("sha"),
                          "head_ref": head.get("ref"),
                          "base_sha": base.get("sha")} if pull else None),
        "before": payload.get("before"),
        "after": payload.get("after"),
        "sender": {"login": sender.get("login"), "id": sender.get("id"),
                   "type": sender.get("type")} if sender else None,
        "installation_id": installation.get("id"),
    }


class Receiver:
    """Transport-independent core, so the tests exercise exactly what runs."""

    def __init__(self, secret: bytes, capture_path: Path):
        self.secret = secret
        self.capture_path = capture_path
        self.state = control_plane.ControlPlane()
        self.rejected = []

    def handle(self, headers: dict, raw_body: bytes) -> tuple:
        """Returns (http_status, response_dict)."""
        lower = {k.lower(): v for k, v in headers.items()}
        signature = lower.get("x-hub-signature-256")
        delivery_id = lower.get("x-github-delivery")
        event = lower.get("x-github-event")

        # 1. verify BEFORE the delivery id is consumed or recorded
        if not verify.verify(self.secret, raw_body, signature):
            self.rejected.append({"at": utcnow(), "reason": "bad_signature",
                                  "delivery_id_seen_but_not_consumed":
                                      delivery_id, "event": event})
            return 401, {"error": "signature verification failed"}

        if not delivery_id or not event:
            self.rejected.append({"at": utcnow(), "reason": "missing_headers",
                                  "event": event})
            return 400, {"error": "missing delivery id or event"}

        try:
            payload = json.loads(raw_body.decode())
        except ValueError:
            self.rejected.append({"at": utcnow(), "reason": "malformed_json",
                                  "delivery_id": delivery_id, "event": event})
            return 400, {"error": "malformed payload"}

        # 2. only now: consume the delivery id and reduce
        outcome = self.state.apply(delivery_id, event, payload)
        envelope = {
            "received_at": utcnow(),
            "delivery_id": delivery_id,
            "signature_present": bool(signature),
            "signature_verified": True,
            "payload": sanitize(event, payload),
            "effect": outcome["effect"],
            "duplicate": outcome["effect"] == "DUPLICATE_IGNORED",
        }
        self._append(envelope)
        return 202, {"accepted": True, "effect": outcome["effect"]}

    def _append(self, envelope: dict) -> None:
        self.capture_path.parent.mkdir(parents=True, exist_ok=True)
        with self.capture_path.open("a") as handle:
            handle.write(json.dumps(envelope) + "\n")


def serve(receiver: Receiver, port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # never log headers or bodies

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self.send_error(413)
                return
            raw = self.rfile.read(length)
            status, body = receiver.handle(dict(self.headers.items()), raw)
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            print(f"{utcnow()} {status} {body.get('effect', body.get('error'))}",
                  flush=True)

        def do_GET(self):  # liveness only
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

    HTTPServer(("127.0.0.1", port), Handler).serve_forever()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8919)
    ap.add_argument("--captures", default=".captures/a2a/deliveries.jsonl")
    args = ap.parse_args()
    if not SECRET_PATH.exists():
        print(f"missing webhook secret at {SECRET_PATH}", file=sys.stderr)
        return 2
    secret = SECRET_PATH.read_bytes().strip()
    receiver = Receiver(secret, Path(args.captures))
    print(f"receiver listening on 127.0.0.1:{args.port}", flush=True)
    serve(receiver, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
