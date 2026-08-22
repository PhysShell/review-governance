#!/usr/bin/env python3
"""The independent watchdog: the only component whose job is to make a green
check stop being green when nobody is left to revoke it.

Its capability is deliberately tiny, and the smallness is the design:

    allowed   : read PR and check state
                PATCH an existing Governor Check Run to a NON-PASSING state
    forbidden : provider triggers, success publication, commit statuses,
                merges, ruleset administration, user OAuth

It shares the App's installation identity — it needs no user credentials at
all — but it lives in its own runtime and its own failure domain, because a
watchdog that dies with the thing it watches is decoration.

The rule it enforces cannot be softened later without noticing:

    a returning primary does NOT restore a revoked success

During an outage nobody was watching the providers' mutable carriers, so
the old evidence is exactly as trustworthy as an unattended shop.
"""
import argparse
import datetime
import json
import os
import sys
import time
from pathlib import Path

import decisions as dec
import governor as primary

HEARTBEAT_DEFAULT = os.path.expanduser("~/.config/review-governor/heartbeat.json")
STALE_AFTER_SECONDS = 45          # preregistered in the protocol
NON_PASSING = frozenset({"failure", "cancelled", "action_required", "timed_out"})
CAUSE = "GOVERNOR_UNAVAILABLE"


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class WatchdogCapability(Exception):
    """Raised when the watchdog is asked to do something outside its role."""


def watchdog_write(method, path, bearer, body=None):
    """The capability boundary, enforced rather than documented."""
    if method == "GET":
        return primary.request(method, path, bearer, body)
    if "/check-runs" not in path:
        raise WatchdogCapability(
            f"watchdog may not write to {path}: only existing Governor check "
            "runs may be patched")
    if method != "PATCH":
        raise WatchdogCapability(
            f"watchdog may not {method} a check run: it may only patch an "
            "existing one to a non-passing state")
    conclusion = (body or {}).get("conclusion")
    if conclusion not in NON_PASSING:
        raise WatchdogCapability(
            f"watchdog may not set conclusion {conclusion!r}: it can only "
            "revoke, never publish a passing state")
    return primary.request(method, path, bearer, body)


def heartbeat_age(path):
    beat_path = Path(path)
    if not beat_path.exists():
        return None, None
    beat = json.loads(beat_path.read_text())
    stamped = datetime.datetime.strptime(beat["at"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=datetime.timezone.utc)
    age = (datetime.datetime.now(datetime.timezone.utc) - stamped).total_seconds()
    return beat, age


def standing_successes(history):
    """Confirmed successes the watchdog would have to revoke."""
    standing = {}
    for row in history.chain():
        standing[row["epoch_id"]] = row
    live = []
    for epoch_id, row in standing.items():
        if row["verdict"] != "SUCCESS":
            continue
        projection = history.projection(epoch_id)
        if projection and projection["state"] == "CONFIRMED" and \
                projection["observed_conclusion"] == "success":
            live.append({"epoch_id": epoch_id, "head_sha": row["head_sha"],
                         "check_run_id": projection["check_run_id"],
                         "decision_id": row["decision_id"],
                         "bundle_hash": row["bundle_hash"]})
    return live


def revoke(history, token, target, incident_at):
    """One revocation: durable decision first, then PATCH, then an
    independent readback — the same discipline as the primary (A3b-c4)."""
    decision_id = history.record(
        epoch_id=target["epoch_id"], head_sha=target["head_sha"],
        verdict="EVIDENCE_INVALIDATED", bundle_hash=None,
        bundle_schema="WatchdogIncident-v1", decision_rule_revision="a5a.1",
        auth_generation=0, decided_at=incident_at, cause=CAUSE,
        invalidates_decision_id=target["decision_id"],
        invalidates_bundle_hash=target["bundle_hash"])
    summary = "\n".join([
        "Governor verdict: EVIDENCE_INVALIDATED",
        f"Head: {target['head_sha']}",
        f"Cause: {CAUSE}",
        f"Invalidates bundle: {target['bundle_hash']}",
        "",
        "The primary Governor runtime stopped reporting liveness, so nobody "
        "was observing the providers' mutable evidence. This success is "
        "revoked by the independent watchdog.",
        "",
        "A returning primary does not restore it: fresh qualification is "
        "required.",
    ])
    history.project_pending(target["epoch_id"], target["head_sha"],
                            target["check_run_id"], "failure", decision_id,
                            utcnow())
    status, _ = watchdog_write(
        "PATCH", f"/repos/{primary.REPO}/check-runs/{target['check_run_id']}",
        token, {"status": "completed", "conclusion": "failure",
                "completed_at": utcnow(),
                "output": {"title": "Governor: EVIDENCE_INVALIDATED "
                                    "(watchdog)", "summary": summary}})
    read_status, readback = watchdog_write(
        "GET", f"/repos/{primary.REPO}/check-runs/{target['check_run_id']}",
        token)
    observed = (readback or {}).get("conclusion")
    settled = ("CONFIRMED" if read_status == 200 and observed == "failure"
               else "OUTCOME_UNKNOWN" if read_status != 200 else "FAILED")
    history.settle_projection(target["epoch_id"], state=settled,
                              observed_conclusion=observed, at=utcnow())
    return {"epoch_id": target["epoch_id"], "check_run_id": target["check_run_id"],
            "decision_id": decision_id, "patch_status": status,
            "observed": observed, "projection_state": settled}


def cmd_check(args):
    """One watchdog pass."""
    history = dec.History(args.db)
    try:
        beat, age = heartbeat_age(args.heartbeat_file)
        stale = beat is None or age is None or age > args.stale_after
        result = {"checked_at": utcnow(), "heartbeat": beat,
                  "heartbeat_age_seconds": None if age is None else round(age, 1),
                  "stale_after_seconds": args.stale_after, "primary_stale": stale,
                  "standing_successes": standing_successes(history),
                  "revocations": []}
        if not stale or not result["standing_successes"]:
            return result
        token = primary.installation_token()
        incident_at = utcnow()
        for target in result["standing_successes"]:
            result["revocations"].append(
                revoke(history, token, target, incident_at))
        result["incident"] = {"at": incident_at, "cause": CAUSE,
                              "revoked": len(result["revocations"]),
                              "restores_automatically": False}
        return result
    finally:
        history.close()


def cmd_watch(args):
    """Poll until the primary goes stale, act once, and stop."""
    deadline = time.time() + args.window
    while time.time() < deadline:
        result = cmd_check(args)
        if result["revocations"]:
            return result
        time.sleep(args.interval)
    return {"checked_at": utcnow(), "primary_stale": False,
            "note": "window elapsed without a stale primary"}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["check", "watch"])
    ap.add_argument("--heartbeat-file", default=HEARTBEAT_DEFAULT)
    ap.add_argument("--stale-after", type=int, default=STALE_AFTER_SECONDS)
    ap.add_argument("--db", default=".captures/a5a/decisions.sqlite3")
    ap.add_argument("--interval", type=int, default=10)
    ap.add_argument("--window", type=int, default=300)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    result = {"check": cmd_check, "watch": cmd_watch}[args.command](args)
    rendered = json.dumps(result, indent=2, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(rendered + "\n")
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
