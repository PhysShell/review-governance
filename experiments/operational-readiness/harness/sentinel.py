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
WATCHDOG_MAX_AGE = 60
STARTUP_GRACE = 90
STARTED_AT = time.monotonic()
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


def in_startup_grace(args):
    """Has this sentinel had time to see anything yet?

    Only ever applied to NOT_REPORTED — the absence of data. A timestamp
    that exists and is old is a real condition and pages immediately, at
    startup like any other time. Without this split, every restart of the
    stack pages the operator for the gap between the sentinel coming up and
    the first sweep landing, and an operator who is woken by routine
    restarts stops reading the alerts that matter.
    """
    return (time.monotonic() - STARTED_AT) < args.startup_grace


def _raise_or_hold(notifier, args, state, cause, severity, detail):
    """Raise, unless this is absence-of-data inside the startup window."""
    if state == "NOT_REPORTED" and in_startup_grace(args):
        return "HELD_STARTUP_GRACE"
    notifier.raise_(severity, cause, repo=args.repo, detected_at=utcnow(),
                    state=detail)
    return "RAISED"


# --- individual checks -------------------------------------------------------

def check_reconciliation(args, notifier):
    """Recent *and* actually reconciled.

    Two things were wrong here. The field was `last_complete_sweep_at`,
    written by the legacy reconcile loop the A6e cut-in replaced — so after
    the cut-in this read a name nobody produces and would have paged about
    a loop that was running perfectly. And freshness alone says a process
    ran, not that it compared anything: a pass in which every PR was
    UNRESOLVED writes a timestamp exactly as recent as a pass that
    compared them all.

    So the age is taken from the field the producer writes, and a pass that
    performed no scoped comparison is NOT_COMPARED rather than HEALTHY.
    """
    path = Path(args.health_file)
    if not path.exists():
        state = {"state": "NOT_REPORTED", "age_seconds": None}
    else:
        health = json.loads(path.read_text() or "{}")
        age = age_of(health.get("last_complete_pass_at"))
        attempted = health.get("comparisons_attempted")
        performed = health.get("comparisons_performed")
        if age is None or age > args.reconciliation_max_age:
            verdict = "STALE"
        elif health.get("all_compared") is not True:
            verdict = "NOT_COMPARED"
        else:
            verdict = "HEALTHY"
        state = {"state": verdict,
                 "age_seconds": None if age is None else round(age, 1),
                 "last_complete_pass_at": health.get("last_complete_pass_at"),
                 "comparisons_attempted": attempted,
                 "comparisons_performed": performed,
                 "pr_count": attempted}
    if notifier:
        if state["state"] == "HEALTHY":
            notifier.clear("reconciliation_stale", repo=args.repo,
                           detected_at=utcnow())
        else:
            state["alert"] = _raise_or_hold(
                notifier, args, state["state"], "reconciliation_stale",
                alerting.CRITICAL,
                f"{state['state']} age={state['age_seconds']}")
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
    observed_at = reported.get("observed_at")
    result = {"state": state,
              "auth_generation": reported.get("auth_generation"),
              "observed_at": observed_at,
              "source": reported.get("source"),
              "observation_age_seconds": age_of(observed_at)}
    if notifier:
        for bad, cause in FORWARDED_AUTH_STATES.items():
            if state == bad:
                notifier.raise_(alerting.CRITICAL, cause, repo=args.repo,
                                detected_at=observed_at or utcnow(),
                                state=f"{state} gen={result['auth_generation']}")
            else:
                notifier.clear(cause, repo=args.repo, detected_at=utcnow())
    return result


def check_watchdog(args, notifier, healthz):
    """Is the watchdog *turning*, not merely running.

    Nothing else covers this. The off-host uptime monitor watches the
    receiver, `Restart=always` covers a crash but not a hang, and
    `systemctl is-active` is true for a loop that stopped looping. So the
    independent failure domain could have gone quiet with every dashboard
    green — which is the exact failure this whole program exists to refuse.
    """
    last = (healthz or {}).get("last_watchdog_poll")
    age = age_of(last)
    if last is None:
        state = {"state": "NOT_REPORTED", "last_poll_at": None}
    else:
        state = {"state": "POLLING" if age is not None and age <= args.watchdog_max_age
                          else "NOT_POLLING",
                 "last_poll_at": last,
                 "age_seconds": None if age is None else round(age, 1),
                 "polls": (healthz or {}).get("watchdog_polls")}
    if notifier:
        if state["state"] == "POLLING":
            notifier.clear("watchdog_not_polling", repo=args.repo,
                           detected_at=utcnow())
        else:
            state["alert"] = _raise_or_hold(
                notifier, args, state["state"], "watchdog_not_polling",
                alerting.CRITICAL,
                f"{state['state']} age={state.get('age_seconds')}")
    return state


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
        body = None
        state = {"state": "UNREACHABLE", "error": type(exc).__name__}
        if notifier:
            notifier.raise_(alerting.WARNING, "webhook_receiver_unavailable",
                            repo=args.repo, detected_at=utcnow(),
                            state=type(exc).__name__)
    return state, body


def write_watchdog_health(path, healthz, at):
    """The watchdog's own record, carried across rather than restated.

    `last_watchdog_poll` originates in the edge database, written by the
    watchdog process itself on the host that runs it. The primary copies
    that value with its provenance; it does not offer an opinion about
    whether its watchdog seems well, which is not a thing the primary can
    know.
    """
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    poll = (healthz or {}).get("last_watchdog_poll")
    Path(path).write_text(json.dumps({
        "last_complete_pass_at": poll,
        "watchdog_polls": (healthz or {}).get("watchdog_polls"),
        "source": "edge /healthz, value produced by the watchdog process",
        "relayed_by": "primary sentinel",
        "relayed_at": at,
        "edge_reachable": healthz is not None,
    }, indent=2) + "\n")


def sweep(args, notifier):
    receiver, healthz = check_edge_receiver(args, notifier)
    if healthz is not None:
        write_watchdog_health(args.watchdog_health, healthz, utcnow())
    return {"checked_at": utcnow(),
            "reconciliation": check_reconciliation(args, notifier),
            "installation_token": check_installation_token(args, notifier),
            "user_authorization": check_auth_state(args, notifier),
            "edge_receiver": receiver,
            "edge_watchdog": check_watchdog(args, notifier, healthz)}


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
    ap.add_argument("--watchdog-health",
                    default=str(CONFIG_DIR / "watchdog-health.json"))
    ap.add_argument("--alerts-db", default=str(CONFIG_DIR / "alerts.sqlite3"))
    ap.add_argument("--reconciliation-max-age", type=int,
                    default=RECONCILIATION_MAX_AGE)
    ap.add_argument("--startup-grace", type=int, default=STARTUP_GRACE,
                    help="how long after start an absence of data is treated "
                         "as not-yet-observed rather than as an incident. "
                         "Never applied to data that exists and is stale.")
    ap.add_argument("--watchdog-max-age", type=int, default=WATCHDOG_MAX_AGE,
                    help="how stale the edge watchdog's last poll may be "
                         "before it is presumed to have stopped watching")
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
