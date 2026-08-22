#!/usr/bin/env python3
"""Primary-side heartbeat: an outbound, signed POST to the edge.

Inverted on purpose. The primary needs only outbound access — which the WSL
host already has — and the watchdog never has to ask a dead process whether
it is dead.

The heartbeat carries no policy content. It says "this instance was alive at
this moment", nothing more, so a compromised or confused edge cannot learn
anything useful from it or replay it into a verdict.
"""
import argparse
import datetime
import hashlib
import hmac
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CONFIG_DIR = Path(os.environ.get(
    "GOVERNOR_CONFIG_DIR", os.path.expanduser("~/.config/review-governor")))
SECRET_PATH = CONFIG_DIR / "heartbeat-secret"
INTERVAL_SECONDS = 15


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def instance_id():
    return os.environ.get("GOVERNOR_INSTANCE_ID") or f"primary@{socket.gethostname()}"


def sign(secret: bytes, raw: bytes) -> str:
    return "sha256=" + hmac.new(secret, raw, hashlib.sha256).hexdigest()


def beat_once(endpoint: str, secret: bytes) -> dict:
    body = json.dumps({"primary_instance_id": instance_id(), "at": utcnow(),
                       "kind": "liveness"}).encode()
    req = urllib.request.Request(
        endpoint.rstrip("/") + "/primary/heartbeat", method="POST", data=body,
        headers={"Content-Type": "application/json",
                 "User-Agent": "governor-primary-heartbeat",
                 "X-Governor-Signature": sign(secret, body)})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {"at": utcnow(), "status": resp.status,
                    "body": json.loads(resp.read().decode() or "{}")}
    except urllib.error.HTTPError as e:
        return {"at": utcnow(), "status": e.code,
                "error": e.read().decode()[:200]}
    except Exception as exc:
        # A failed heartbeat is not an error to swallow: from the edge's
        # point of view it is indistinguishable from death, which is the
        # correct interpretation.
        return {"at": utcnow(), "status": None,
                "error": f"{type(exc).__name__}: {exc}"}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--endpoint", required=True,
                    help="https://governor-edge.<domain>")
    ap.add_argument("--interval", type=int, default=INTERVAL_SECONDS)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    if not SECRET_PATH.exists():
        print(f"missing heartbeat secret at {SECRET_PATH}", file=sys.stderr)
        return 2
    secret = SECRET_PATH.read_bytes().strip()
    if args.once:
        result = beat_once(args.endpoint, secret)
        rendered = json.dumps(result, indent=2)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(rendered + "\n")
        print(rendered)
        return 0
    while True:
        print(json.dumps(beat_once(args.endpoint, secret)), flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
