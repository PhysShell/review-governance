#!/usr/bin/env python3
"""Phase Recovery instrument: human re-authorization after revocation.

Device Flow again — a *new authorization generation*, not a continuation of
the revoked chain. The new pair is committed through the same CAS store, so
recovery cannot silently overwrite a generation another worker landed.

    --start    request one device code, print user_code, exit
    --resume   poll the pending code and commit the new generation

The user_code and verification_uri are meant to be shown. The device code,
access token and refresh token go to 0600 storage and are never printed.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import creds
import gh_probe

DEVICE_CODE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"
GRANT = "urn:ietf:params:oauth:grant-type:device_code"
PENDING_PATH = creds.CONFIG_DIR / "reauth-pending.json"


def post_form(url: str, fields: dict) -> dict:
    req = urllib.request.Request(
        url, method="POST", data=urllib.parse.urlencode(fields).encode(),
        headers={"Accept": "application/json",
                 "User-Agent": "review-governor-a1c-reauth"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return json.loads(raw)
        except ValueError:
            return {"error": f"http_{e.code}", "error_description": raw[:300]}


def client_id() -> str:
    return json.loads((creds.CONFIG_DIR / "app-public.json").read_text())["client_id"]


def start() -> int:
    start_payload = post_form(DEVICE_CODE_URL, {"client_id": client_id()})
    if "device_code" not in start_payload:
        print(f"could not start device flow: {start_payload}", file=sys.stderr)
        return 4
    PENDING_PATH.write_text(json.dumps({
        "device_code": start_payload["device_code"],
        "interval": start_payload.get("interval", 5),
        "expires_at": time.time() + int(start_payload.get("expires_in", 900)),
    }, indent=2) + "\n")
    os.chmod(PENDING_PATH, 0o600)
    print(json.dumps({
        "action_required": "re-authorize in a browser as PhysShell",
        "verification_uri": start_payload.get("verification_uri"),
        "user_code": start_payload.get("user_code"),
        "expires_in_sec": start_payload.get("expires_in"),
        "issued_at": gh_probe.utcnow(),
    }, indent=2))
    return 0


def resume(label: str, wait_min: int) -> int:
    pending = json.loads(PENDING_PATH.read_text())
    interval = max(int(pending.get("interval", 5)), 5)
    deadline = min(pending["expires_at"], time.time() + wait_min * 60)
    while time.time() < deadline:
        time.sleep(interval)
        payload = post_form(TOKEN_URL, {
            "client_id": client_id(), "device_code": pending["device_code"],
            "grant_type": GRANT})
        error = payload.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += int(payload.get("interval", 5)) or 5
            continue
        if error in ("expired_token", "access_denied"):
            print(f"device flow ended: {error}", file=sys.stderr)
            return 5
        if error:
            print(f"token endpoint error: {payload}", file=sys.stderr)
            return 4
        if "access_token" in payload:
            from_generation = creds.current_generation()
            committed = creds.commit_new_generation(
                payload, from_generation, label=label,
                obtained_via="github_app_device_flow_reauthorization",
                obtained_at=gh_probe.utcnow())
            PENDING_PATH.unlink(missing_ok=True)
            print(json.dumps({"recovered_from_generation": from_generation,
                              "committed": committed}, indent=2))
            return 0
    print("device code expired before authorization", file=sys.stderr)
    return 7


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", action="store_true")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--label", default="G2")
    ap.add_argument("--wait-min", type=int, default=15)
    args = ap.parse_args()
    if args.start:
        return start()
    if args.resume:
        return resume(args.label, args.wait_min)
    ap.error("choose --start or --resume")


if __name__ == "__main__":
    sys.exit(main())
