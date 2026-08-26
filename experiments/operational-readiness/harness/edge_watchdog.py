#!/usr/bin/env python3
"""Edge watchdog — the only writer on the edge host, and it only writes
downward.

It differs from the A5a in-host prototype in one decisive way: it does not
read the primary's authoritative decision store. When the primary stops
reporting, the watchdog enumerates from GitHub the open PRs on governed
branches, their current heads, and the Governor's own check runs there, and
extinguishes any that are passing.

That is licensed by an asymmetry, not by trust:

    FORBIDDEN   GitHub says success            => conclude policy SUCCESS
    PERMITTED   GitHub shows a passing Governor run AND the primary is
                unavailable                    => destroy that authorization

Every operation available here is monotone in the safe direction. A
false-positive revoke costs a review round; a false-positive success costs
the entire point of the program.

The edge holds no user OAuth credentials. On a compromised edge host the
capability boundary below is a program, not a cryptographic sandbox — that
risk is recorded in the protocol rather than implied away.
"""
import argparse
import base64
import datetime
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import alerting
import edge_store

API = "https://api.github.com"
CONFIG_DIR = Path(os.environ.get(
    "GOVERNOR_EDGE_CONFIG", os.path.expanduser("~/.config/review-governor-edge")))
GOVERNOR_APP_ID = 4669438
GOVERNOR_INSTALLATION_ID = 155393018
PRODUCTION_CONTEXT = "ai/final-review"
STALE_AFTER_SECONDS = 45
NON_PASSING = frozenset({"failure", "cancelled", "action_required", "timed_out"})
PASSING = frozenset({"success", "neutral", "skipped"})
CAUSE = "GOVERNOR_UNAVAILABLE"


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class WatchdogCapability(Exception):
    """Raised when the watchdog is asked to step outside its role."""


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def app_jwt():
    public = json.loads((CONFIG_DIR / "app-public.json").read_text())
    now = int(time.time())
    header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"},
                             separators=(",", ":")).encode())
    payload = _b64(json.dumps({"iat": now - 60, "exp": now + 540,
                               "iss": str(public["app_id"])},
                              separators=(",", ":")).encode())
    signing = f"{header}.{payload}".encode()
    signature = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", str(public["pem_path"])],
        input=signing, capture_output=True, check=True).stdout
    return f"{signing.decode()}.{_b64(signature)}"


def request(method, path, bearer, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": "governor-edge-watchdog",
               "Authorization": f"Bearer {bearer}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API + path, method=method, data=data,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw[:400]}


def guarded(method, path, bearer, body=None):
    """The capability boundary, in code."""
    if method == "GET":
        return request(method, path, bearer, body)
    if "/check-runs/" not in path:
        raise WatchdogCapability(
            f"edge watchdog may not write to {path}: only an existing "
            "Governor check run may be patched")
    if method != "PATCH":
        raise WatchdogCapability(
            f"edge watchdog may not {method} a check run: it may only patch "
            "an existing one")
    conclusion = (body or {}).get("conclusion")
    if conclusion not in NON_PASSING:
        raise WatchdogCapability(
            f"edge watchdog may not set conclusion {conclusion!r}: it can "
            "only revoke, never publish a passing state")
    return request(method, path, bearer, body)


class InstallationMismatch(Exception):
    """The App is installed somewhere this watchdog was not told about."""


def installation_token(installation_id=GOVERNOR_INSTALLATION_ID):
    """Mint against a pinned installation; see the note in governor.py.

    On the edge this matters twice over: the watchdog writes, and a runtime
    that quietly follows an unexpected installation would be writing
    somewhere nobody reviewed.
    """
    jwt = app_jwt()
    status, installs = request("GET", "/app/installations", jwt)
    assert status == 200 and installs, (status, installs)
    ids = [i["id"] for i in installs]
    if installation_id not in ids:
        raise InstallationMismatch(
            f"expected installation {installation_id}, GitHub reports {ids}")
    status, minted = request(
        "POST", f"/app/installations/{installation_id}/access_tokens", jwt)
    assert status == 201, (status, minted)
    return minted["token"]


def heartbeat_age(store):
    beat = store.latest_heartbeat()
    if not beat:
        return None, None
    age = datetime.datetime.now(datetime.timezone.utc).timestamp() - \
        beat["last_seen_epoch"]
    return beat, age


def passing_governor_runs(token, repo, branches, context=PRODUCTION_CONTEXT):
    """Cleanup surface: what is currently green, according to GitHub.

    Never interpreted as policy — only as "this authorization is visible and
    must be destroyed while nobody is watching the evidence".
    """
    found = []
    status, pulls = request(
        "GET", f"/repos/{repo}/pulls?state=open&per_page=100", token)
    if status != 200:
        return found, {"error": f"cannot list pulls: {status}"}
    for pull in pulls or []:
        if branches and pull["base"]["ref"] not in branches:
            continue
        head = pull["head"]["sha"]
        code, body = request(
            "GET", f"/repos/{repo}/commits/{head}/check-runs?per_page=100",
            token)
        if code != 200:
            continue
        for run in (body or {}).get("check_runs", []):
            app = run.get("app") or {}
            if app.get("id") != GOVERNOR_APP_ID or run.get("name") != context:
                continue
            if run.get("conclusion") in PASSING:
                found.append({"pr_number": pull["number"], "head_sha": head,
                              "check_run_id": run["id"],
                              "conclusion": run.get("conclusion")})
    return found, None


def revoke(token, repo, target, detected_at):
    summary = "\n".join([
        "Governor verdict: EVIDENCE_INVALIDATED",
        f"Head: {target['head_sha']}",
        f"Cause: {CAUSE}",
        "",
        "The primary Governor stopped reporting liveness, so nobody was "
        "observing the providers' mutable evidence. This authorization is "
        "revoked by the independent edge watchdog.",
        "",
        "A returning primary does not restore it: fresh qualification is "
        "required.",
    ])
    status, _ = guarded(
        "PATCH", f"/repos/{repo}/check-runs/{target['check_run_id']}", token,
        {"status": "completed", "conclusion": "failure",
         "completed_at": utcnow(),
         "output": {"title": "Governor: EVIDENCE_INVALIDATED (edge watchdog)",
                    "summary": summary}})
    read_status, readback = guarded(
        "GET", f"/repos/{repo}/check-runs/{target['check_run_id']}", token)
    observed = (readback or {}).get("conclusion")
    settled = ("CONFIRMED" if read_status == 200 and observed == "failure"
               else "OUTCOME_UNKNOWN" if read_status != 200 else "FAILED")
    return {**target, "patch_status": status, "observed": observed,
            "state": settled, "detected_at": detected_at,
            "revoked_at": utcnow()}


def alert_on_heartbeat(notifier, args, result, age):
    """Heartbeat thresholds, as transitions rather than as a log.

    Ordered worst-first and mutually exclusive: a primary that is CRITICAL
    must not also be sitting in the WARNING state, or the recovery for one
    of them never arrives and the operator is left holding a stale red.
    """
    if notifier is None:
        return
    common = {"repo": args.repo, "detected_at": result["checked_at"],
              "state": f"heartbeat_age={result['heartbeat_age_seconds']}"}
    if age is None or age > args.stale_after:
        notifier.clear("heartbeat_age_warning", **common)
        notifier.raise_(alerting.CRITICAL, "heartbeat_age_critical", **common)
    elif age > args.warn_after:
        notifier.clear("heartbeat_age_critical", **common)
        notifier.raise_(alerting.WARNING, "heartbeat_age_warning", **common)
    else:
        notifier.clear("heartbeat_age_critical", **common)
        notifier.clear("heartbeat_age_warning", **common)


def alert_on_stuck_deliveries(notifier, args, store, now):
    """A delivery that sat in RECEIVED past its budget means the fast path
    is not draining — the mailbox is filling and nobody is opening it."""
    if notifier is None:
        return
    stuck = []
    for row in store.deliveries(state=edge_store.RECEIVED):
        try:
            age = (alerting.parse_ts(now) -
                   alerting.parse_ts(row["received_at"])).total_seconds()
        except ValueError:
            continue
        if age > args.delivery_budget:
            stuck.append(row["delivery_guid"])
    if stuck:
        notifier.raise_(alerting.WARNING, "delivery_stuck", repo=args.repo,
                        detected_at=now, state=f"{len(stuck)} beyond "
                        f"{args.delivery_budget}s")
    else:
        notifier.clear("delivery_stuck", repo=args.repo, detected_at=now)


def cmd_check(args, notifier=None):
    store = edge_store.EdgeStore(args.db)
    try:
        beat, age = heartbeat_age(store)
        stale = beat is None or age is None or age > args.stale_after
        result = {"checked_at": utcnow(),
                  "primary_instance_id": beat["primary_instance_id"] if beat else None,
                  "heartbeat_age_seconds": None if age is None else round(age, 1),
                  "stale_after_seconds": args.stale_after,
                  "primary_stale": stale, "revocations": []}
        alert_on_heartbeat(notifier, args, result, age)
        alert_on_stuck_deliveries(notifier, args, store, result["checked_at"])
        if not stale:
            return result
        try:
            token = installation_token()
        except Exception as exc:
            # Losing the ability to mint is losing the ability to revoke.
            # It must page, and it must not look like a quiet no-op poll.
            result["error"] = {"installation_token": type(exc).__name__}
            if notifier:
                notifier.raise_(alerting.CRITICAL,
                                "installation_token_mint_failed",
                                repo=args.repo, detected_at=result["checked_at"],
                                state=type(exc).__name__)
            return result
        if notifier:
            notifier.clear("installation_token_mint_failed", repo=args.repo,
                           detected_at=result["checked_at"])
        targets, error = passing_governor_runs(token, args.repo, args.branches,
                                               context=args.context)
        result["passing_runs_found"] = targets
        if error:
            result["error"] = error
            return result
        if not targets:
            return result
        detected_at = utcnow()
        result["revocations"] = [revoke(token, args.repo, t, detected_at)
                                 for t in targets]
        result["incident_id"] = store.open_incident(
            detected_at=detected_at, stale_age=age or -1,
            primary_instance_id=result["primary_instance_id"],
            affected=[t["check_run_id"] for t in targets],
            results=result["revocations"])
        result["restores_automatically"] = False
        alert_on_incident(notifier, args, result, detected_at)
        return result
    finally:
        store.close()


def alert_on_incident(notifier, args, result, detected_at):
    """An incident always pages. A revocation that did not land pages
    separately and louder: `OUTCOME_UNKNOWN` means the authorization may
    still be standing, which is the one state nobody may assume away."""
    if notifier is None:
        return
    notifier.raise_(alerting.CRITICAL, "watchdog_incident", repo=args.repo,
                    incident_id=result.get("incident_id"),
                    detected_at=detected_at,
                    state=f"{len(result['revocations'])} revoked")
    for revocation in result["revocations"]:
        if revocation["state"] == "OUTCOME_UNKNOWN":
            notifier.raise_(alerting.CRITICAL,
                            "watchdog_revocation_outcome_unknown",
                            repo=args.repo,
                            pr_number=revocation.get("pr_number"),
                            check_run_id=revocation.get("check_run_id"),
                            incident_id=result.get("incident_id"),
                            detected_at=detected_at, state="OUTCOME_UNKNOWN")
        elif revocation["state"] == "FAILED":
            notifier.raise_(alerting.CRITICAL, "watchdog_revocation_failed",
                            repo=args.repo,
                            pr_number=revocation.get("pr_number"),
                            check_run_id=revocation.get("check_run_id"),
                            incident_id=result.get("incident_id"),
                            detected_at=detected_at, state="FAILED")


def build_notifier(args):
    """No channel configured is a state worth seeing, not a silent default.

    The deployed unit must never reach this returning None; `--no-alerts` is
    how a fixture says so out loud.
    """
    if args.no_alerts:
        return None
    transport = alerting.transport_from_config(CONFIG_DIR)
    if transport is None:
        print(json.dumps({"alerting": "NOT CONFIGURED",
                          "looked_in": str(CONFIG_DIR / "alerting.json"),
                          "effect": "incidents will not reach a human"}),
              flush=True)
        return None
    return alerting.Notifier(args.alerts_db, transport,
                             origin=f"edge watchdog · {args.repo}")


def cmd_watch(args):
    """Poll for as long as it is supposed to be watching.

    The A5a-c2-1 rerun found the defect this fixes. This loop used to stop
    after its first revocation, which is right for a bounded fixture and
    wrong for the thing it was deployed as: the unit exited 0, systemd's
    `Restart=on-failure` correctly did nothing, and the watchdog was gone.
    A supervisor that stops supervising after one incident is worse than
    none, because the dashboard still says it ran.

    So: `--window 0` means run until stopped, and stopping after an incident
    is now something a fixture asks for explicitly rather than something the
    production path inherits by accident.

    The window-elapsed result reports the *last observed* state rather than
    a hardcoded one: a run that ends without revoking must still say whether
    the primary looked alive, or the operator learns nothing from it.
    """
    deadline = None if args.window <= 0 else time.time() + args.window
    last = None
    polls = 0
    incidents = []
    notifier = build_notifier(args)
    while deadline is None or time.time() < deadline:
        last = cmd_check(args, notifier)
        polls += 1
        if last.get("revocations"):
            incidents.append({"incident_id": last.get("incident_id"),
                              "detected_at": last.get("checked_at"),
                              "revocations": last["revocations"]})
            if args.stop_after_incident:
                last["polls"] = polls
                return last
            print(json.dumps({**last, "polls": polls}, default=str), flush=True)
        time.sleep(args.interval)
    return {**(last or {}), "polls": polls, "revocations": [],
            "incidents_this_run": incidents,
            "note": "window elapsed; last observed state above"}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["check", "watch"])
    ap.add_argument("--repo", default="PhysShell/evm-from-scratch")
    ap.add_argument("--branches", nargs="*", default=["main"])
    ap.add_argument("--context", default=PRODUCTION_CONTEXT,
                    help="check-run name to police; probe contexts are used "
                         "for qualification without repointing production")
    ap.add_argument("--db", default=str(CONFIG_DIR / "edge.sqlite3"))
    ap.add_argument("--stale-after", type=int, default=STALE_AFTER_SECONDS)
    ap.add_argument("--interval", type=int, default=10)
    ap.add_argument("--window", type=int, default=600,
                    help="seconds to keep watching; 0 means until stopped, "
                         "which is what the deployed unit uses")
    ap.add_argument("--stop-after-incident", action="store_true",
                    help="exit after the first revocation. For bounded "
                         "fixtures only — a deployed watchdog that stops "
                         "after one incident has stopped watching.")
    ap.add_argument("--warn-after", type=int, default=30,
                    help="heartbeat age that raises a WARNING before the "
                         "CRITICAL threshold at --stale-after")
    ap.add_argument("--delivery-budget", type=int, default=120,
                    help="seconds a delivery may sit in RECEIVED before the "
                         "fast path is presumed not to be draining")
    ap.add_argument("--alerts-db", default=str(CONFIG_DIR / "alerts.sqlite3"))
    ap.add_argument("--no-alerts", action="store_true",
                    help="run without a notifier. For fixtures that must not "
                         "page a human, never for the deployed unit.")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    if args.command == "check":
        result = cmd_check(args, build_notifier(args))
    else:
        result = cmd_watch(args)
    rendered = json.dumps(result, indent=2, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(rendered + "\n")
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
