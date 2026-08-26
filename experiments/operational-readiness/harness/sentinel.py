#!/usr/bin/env python3
"""Primary-side sentinel: the process that pages about the other processes.

It deliberately owns none of the work it watches. Reconciliation writes a
health file and this reads it, because a loop that alarms about its own
staleness stops alarming at exactly the moment it matters.

What it will not do, and why it matters more than what it will:

**It never attempts a token refresh.** GitHub's Device Flow refresh tokens
are single-use with rotation, so "check whether refresh still works" is not
a read — it is a write that can strand the credential. `AUTH_LOST` and
`REFRESH_OUTCOME_UNKNOWN` are therefore reported *by the refresh path*
through `auth-state.json`; the sentinel forwards them and never infers them.

**Absence of a bad state is not a good state.** If `auth-state.json` does
not exist, the sentinel says `NOT_REPORTED` rather than `HEALTHY`. An
unreported subsystem that renders as green is how a dashboard starts lying.
"""
import argparse
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import alerting
import governor

CONFIG_DIR = Path(os.environ.get(
    "GOVERNOR_CONFIG_DIR", os.path.expanduser("~/.config/review-governor")))
RECONCILIATION_MAX_AGE = 60
FORWARDED_AUTH_STATES = {"AUTH_LOST": "auth_lost",
                         "REFRESH_OUTCOME_UNKNOWN": "refresh_outcome_unknown"}


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def age_of(timestamp):
    if not timestamp:
        return None
    try:
        return (alerting.parse_ts(utcnow()) -
                alerting.parse_ts(timestamp)).total_seconds()
    except ValueError:
        return None


# --- individual checks -------------------------------------------------------

def check_reconciliation(args, notifier):
    path = Path(args.health_file)
    if not path.exists():
        state = {"state": "NOT_REPORTED", "age_seconds": None}
    else:
        health = json.loads(path.read_text() or "{}")
        age = age_of(health.get("last_complete_sweep_at"))
        state = {"state": "HEALTHY" if age is not None and age <= args.reconciliation_max_age
                          else "STALE",
                 "age_seconds": None if age is None else round(age, 1),
                 "last_complete_sweep_at": health.get("last_complete_sweep_at"),
                 "pr_count": health.get("pr_count")}
    if notifier:
        if state["state"] == "HEALTHY":
            notifier.clear("reconciliation_stale", repo=args.repo,
                           detected_at=utcnow())
        else:
            notifier.raise_(alerting.CRITICAL, "reconciliation_stale",
                            repo=args.repo, detected_at=utcnow(),
                            state=f"{state['state']} age={state['age_seconds']}")
    return state


def check_installation_token(args, notifier):
    try:
        governor.installation_token()
        state = {"state": "PASS"}
        if notifier:
            notifier.clear("installation_token_mint_failed", repo=args.repo,
                           detected_at=utcnow())
    except Exception as exc:
        state = {"state": "FAIL", "error": type(exc).__name__}
        if notifier:
            notifier.raise_(alerting.CRITICAL, "installation_token_mint_failed",
                            repo=args.repo, detected_at=utcnow(),
                            state=type(exc).__name__)
    return state


def check_auth_state(args, notifier):
    """Forward what the refresh path reported. Never probe the refresh."""
    path = Path(args.auth_state_file)
    if not path.exists():
        return {"state": "NOT_REPORTED",
                "note": "no refresh path has written auth-state.json; this is "
                        "not evidence that user authorization is healthy"}
    reported = json.loads(path.read_text() or "{}")
    state = reported.get("state")
    result = {"state": state, "generation": reported.get("generation"),
              "updated_at": reported.get("updated_at")}
    if notifier:
        for bad, cause in FORWARDED_AUTH_STATES.items():
            if state == bad:
                notifier.raise_(alerting.CRITICAL, cause, repo=args.repo,
                                detected_at=reported.get("updated_at") or utcnow(),
                                state=state)
            else:
                notifier.clear(cause, repo=args.repo, detected_at=utcnow())
    return result


def check_edge_receiver(args, notifier):
    """The primary's own view of the edge. The off-host uptime monitor is
    the independent one; this exists so the two disagree loudly rather than
    both being blind in the same way."""
    req = urllib.request.Request(f"{args.endpoint.rstrip('/')}/healthz",
                                 headers={"User-Agent": "governor-sentinel"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode() or "{}")
        state = {"state": "REACHABLE", "http_status": 200,
                 "last_primary_heartbeat": body.get("last_primary_heartbeat")}
        if notifier:
            notifier.clear("webhook_receiver_unavailable", repo=args.repo,
                           detected_at=utcnow())
    except Exception as exc:
        state = {"state": "UNREACHABLE", "error": type(exc).__name__}
        if notifier:
            notifier.raise_(alerting.WARNING, "webhook_receiver_unavailable",
                            repo=args.repo, detected_at=utcnow(),
                            state=type(exc).__name__)
    return state


def sweep(args, notifier):
    return {"checked_at": utcnow(),
            "reconciliation": check_reconciliation(args, notifier),
            "installation_token": check_installation_token(args, notifier),
            "user_authorization": check_auth_state(args, notifier),
            "edge_receiver": check_edge_receiver(args, notifier)}


def build_notifier(args):
    if args.no_alerts:
        return None
    transport = alerting.transport_from_config(CONFIG_DIR)
    if transport is None:
        print(json.dumps({"alerting": "NOT CONFIGURED",
                          "looked_in": str(CONFIG_DIR / "alerting.json"),
                          "effect": "nothing here will reach a human"}),
              flush=True)
        return None
    return alerting.Notifier(args.alerts_db, transport,
                             origin=f"primary sentinel · {args.repo}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="PhysShell/evm-from-scratch")
    ap.add_argument("--endpoint", default="https://192-248-184-141.sslip.io")
    ap.add_argument("--health-file",
                    default=str(CONFIG_DIR / "reconciliation-health.json"))
    ap.add_argument("--auth-state-file",
                    default=str(CONFIG_DIR / "auth-state.json"))
    ap.add_argument("--alerts-db", default=str(CONFIG_DIR / "alerts.sqlite3"))
    ap.add_argument("--reconciliation-max-age", type=int,
                    default=RECONCILIATION_MAX_AGE)
    ap.add_argument("--interval", type=int, default=20)
    ap.add_argument("--window", type=int, default=0,
                    help="0 means until stopped")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--no-alerts", action="store_true")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    notifier = build_notifier(args)
    try:
        if args.once:
            result = sweep(args, notifier)
        else:
            deadline = None if args.window <= 0 else time.time() + args.window
            last = None
            while deadline is None or time.time() < deadline:
                last = sweep(args, notifier)
                print(json.dumps(last, default=str), flush=True)
                time.sleep(args.interval)
            result = last or {}
    finally:
        if notifier:
            notifier.close()
    rendered = json.dumps(result, indent=2, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(rendered + "\n")
    if args.once:
        print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
