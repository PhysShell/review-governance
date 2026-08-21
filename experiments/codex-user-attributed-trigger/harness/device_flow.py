#!/usr/bin/env python3
"""GitHub App Device Flow — obtain a user access token for the Governor App.

    client_id -> POST /login/device/code -> user_code + verification_uri
              -> PhysShell authorizes in a browser
              -> poll POST /login/oauth/access_token
              -> user access token (ghu_…), stored 0600

The user_code / verification_uri are meant to be shown to the user and are
displayed. The access token, refresh token and device_code are secret: they
go straight to ~/.config/review-governor/user-token.json (0600) and are
never printed, logged, or committed. Expiration is left at GitHub's
default; refresh tokens are stored but deliberately unused in A1b.

Exit codes: 0 authorized · 4 device flow disabled on the App · 5 denied ·
6 total deadline reached without authorization.
"""
import argparse
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEVICE_CODE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"
GRANT = "urn:ietf:params:oauth:grant-type:device_code"
CONFIG_DIR = Path(os.environ.get(
    "GOVERNOR_CONFIG_DIR", os.path.expanduser("~/.config/review-governor")))


def utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def post_form(url: str, fields: dict) -> dict:
    req = urllib.request.Request(
        url, method="POST",
        data=urllib.parse.urlencode(fields).encode(),
        headers={"Accept": "application/json",
                 "User-Agent": "review-governor-a1b-device-flow"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return json.loads(raw)
        except ValueError:
            return {"error": f"http_{e.code}", "error_description": raw[:300]}


def store_token(payload: dict, client_id: str) -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    record = dict(payload)
    record["obtained_via"] = "github_app_device_flow"
    record["obtained_at"] = utcnow()
    record["client_id_prefix"] = client_id[:3]
    path = CONFIG_DIR / "user-token.json"
    path.write_text(json.dumps(record, indent=2) + "\n")
    os.chmod(path, 0o600)
    token = payload.get("access_token", "")
    prefix = next((p for p in ("ghu_", "ghs_", "gho_", "ghp_")
                   if token.startswith(p)), "unknown")
    return {
        "stored": str(path),
        "token_prefix": prefix,
        "token_type": payload.get("token_type"),
        "expires_in": payload.get("expires_in"),
        "refresh_token_issued": bool(payload.get("refresh_token")),
        "refresh_token_expires_in": payload.get("refresh_token_expires_in"),
        "note": "token value withheld by design",
    }


PENDING_PATH = CONFIG_DIR / "device-flow-pending.json"


def request_device_code(client_id: str, repository_id=None) -> dict:
    fields = {"client_id": client_id}
    if repository_id:
        # Attempted per protocol; the device-code endpoint documents no
        # repository_id parameter, so whether it narrows anything is verified
        # afterwards against /user/installations/*/repositories rather than
        # assumed from a silent acceptance here.
        fields["repository_id"] = str(repository_id)
    return post_form(DEVICE_CODE_URL, fields)


def wait_until_enabled(client_id: str, deadline_ts: float, repository_id=None) -> dict:
    """Poll until Device Flow stops returning device_flow_disabled."""
    announced = False
    while time.time() < deadline_ts:
        start = request_device_code(client_id, repository_id)
        if start.get("error") != "device_flow_disabled":
            return start
        if not announced:
            print("waiting: Device Flow still disabled on the App", flush=True)
            announced = True
        time.sleep(30)
    return {"error": "wait_deadline_reached"}


def store_pending(start: dict, client_id: str) -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PENDING_PATH.write_text(json.dumps({
        "device_code": start["device_code"],
        "client_id": client_id,
        "interval": start.get("interval", 5),
        "expires_at": time.time() + int(start.get("expires_in", 900)),
        "issued_at": utcnow(),
    }, indent=2) + "\n")
    os.chmod(PENDING_PATH, 0o600)
    return {
        "action_required": "authorize in a browser as PhysShell",
        "verification_uri": start.get("verification_uri"),
        "user_code": start.get("user_code"),
        "expires_in_sec": start.get("expires_in"),
        "issued_at": utcnow(),
    }


def poll_pending(deadline_ts: float) -> int:
    pending = json.loads(PENDING_PATH.read_text())
    interval = max(int(pending.get("interval", 5)), 5)
    while time.time() < min(pending["expires_at"], deadline_ts):
        time.sleep(interval)
        payload = post_form(TOKEN_URL, {
            "client_id": pending["client_id"],
            "device_code": pending["device_code"],
            "grant_type": GRANT,
        })
        err = payload.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += int(payload.get("interval", 5)) or 5
            continue
        if err == "expired_token":
            print("device code expired before authorization", file=sys.stderr)
            return 7
        if err == "access_denied":
            print("ACCESS_DENIED — authorization was declined; that is a result, "
                  "not an error to work around.", file=sys.stderr)
            return 5
        if err:
            print(f"token endpoint error: {payload}", file=sys.stderr)
            return 4
        if "access_token" in payload:
            PENDING_PATH.unlink(missing_ok=True)
            print(json.dumps(store_token(payload, pending["client_id"]), indent=2))
            return 0
    print("device code expired before authorization", file=sys.stderr)
    return 7


def run(client_id: str, deadline_ts: float, repository_id=None) -> int:
    while time.time() < deadline_ts:
        start = request_device_code(client_id, repository_id)
        if start.get("error") == "device_flow_disabled":
            print("DEVICE_FLOW_DISABLED — enable it on the App first "
                  "(Settings → Developer settings → GitHub Apps → "
                  "physshell-review-governor → Optional features / "
                  "'Enable Device Flow'). No other App setting may change.",
                  file=sys.stderr)
            return 4
        if "device_code" not in start:
            print(f"unexpected device/code response: {start}", file=sys.stderr)
            return 4
        interval = max(int(start.get("interval", 5)), 5)
        code_expiry = time.time() + int(start.get("expires_in", 900))
        print(json.dumps({
            "action_required": "authorize in a browser as PhysShell",
            "verification_uri": start.get("verification_uri"),
            "user_code": start.get("user_code"),
            "expires_in_sec": start.get("expires_in"),
            "issued_at": utcnow(),
        }, indent=2), flush=True)

        while time.time() < min(code_expiry, deadline_ts):
            time.sleep(interval)
            payload = post_form(TOKEN_URL, {
                "client_id": client_id,
                "device_code": start["device_code"],
                "grant_type": GRANT,
            })
            err = payload.get("error")
            if err == "authorization_pending":
                continue
            if err == "slow_down":
                interval = max(interval + int(payload.get("interval", 5)), interval + 5)
                continue
            if err == "expired_token":
                print("device code expired; requesting a new one", flush=True)
                break
            if err == "access_denied":
                print("ACCESS_DENIED — authorization was declined; that is a "
                      "result, not an error to work around.", file=sys.stderr)
                return 5
            if err:
                print(f"token endpoint error: {payload}", file=sys.stderr)
                return 4
            if "access_token" in payload:
                print(json.dumps(store_token(payload, client_id), indent=2))
                return 0
    print("deadline reached without authorization", file=sys.stderr)
    return 6


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--wait-min", type=int, default=180,
                    help="total deadline; device codes are re-requested as they expire")
    ap.add_argument("--repository-id", type=int, default=None,
                    help="attempt to scope authorization to one repository")
    ap.add_argument("--start-only", action="store_true",
                    help="wait until Device Flow is enabled, emit one user_code, exit")
    ap.add_argument("--resume", action="store_true",
                    help="poll the pending device code until authorization")
    args = ap.parse_args()
    deadline = time.time() + args.wait_min * 60
    if args.resume:
        return poll_pending(deadline)
    public = json.loads((CONFIG_DIR / "app-public.json").read_text())
    client_id = public["client_id"]
    if args.start_only:
        start = wait_until_enabled(client_id, deadline, args.repository_id)
        if "device_code" not in start:
            print(f"could not start device flow: {start}", file=sys.stderr)
            return 4
        print(json.dumps(store_pending(start, client_id), indent=2))
        return 0
    return run(client_id, deadline, args.repository_id)


if __name__ == "__main__":
    sys.exit(main())
