#!/usr/bin/env python3
"""The safety transition A1c specifies, wired to the production runtime.

The producer records what authorization did. This applies what that means:

    AUTH_LOST / REFRESH_OUTCOME_UNKNOWN
        -> provider triggers forbidden
        -> standing SUCCESS invalidated
        -> failure projected by the installation identity
        -> human reauthorization required
        -> fresh qualification before any new success

It reads the **authoritative store**, never `auth-state.json`. The mirror
exists for the sentinel to alert from; if policy read the mirror, a file
anything on the host can edit would be deciding whether the gate opens.

Recovery is deliberately not symmetric. Returning to `AUTHORIZED` restores
the *ability to review*, never the *evidence*: during the loss nobody was
watching the providers' mutable carriers, so whatever was green was green
on the strength of an unwatched claim. There is no code path here that
raises a conclusion.
"""
import argparse
import datetime
import json
import os
import sys
from pathlib import Path

import auth_state
import decisions as dec
import governor

CONFIG_DIR = Path(os.environ.get(
    "GOVERNOR_CONFIG_DIR", os.path.expanduser("~/.config/review-governor")))
PASSING = frozenset({"success", "neutral", "skipped"})
NON_PASSING = frozenset({"failure", "cancelled", "action_required",
                         "timed_out"})
CAUSE = "USER_AUTHORIZATION_LOST"


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class GateCapability(Exception):
    """Raised where this module is asked to publish something passing."""


def guarded_write(method, path, token, body=None):
    """Monotone in the safe direction, enforced in code.

    Same shape as the edge watchdog's boundary and for the same reason: an
    authorization *loss* can only ever destroy an authorization, so a code
    path here that could write `success` is a bug with a very long fuse.
    """
    if method == "GET":
        return governor.request(method, path, token, body)
    if method != "PATCH" or "/check-runs/" not in path:
        raise GateCapability(
            f"auth gate may not {method} {path}: it may only patch an "
            "existing check run")
    if (body or {}).get("conclusion") not in NON_PASSING:
        raise GateCapability(
            f"auth gate may not set conclusion "
            f"{(body or {}).get('conclusion')!r}: it can only revoke")
    return governor.request(method, path, token, body)


def standing_successes(token, repo, context):
    """What is currently green from the Governor App, per GitHub.

    Read as a cleanup surface, never as policy: the question is "what
    authorization is visible and must be destroyed", not "what did we
    decide".
    """
    found = []
    status, pulls = governor.request(
        "GET", f"/repos/{repo}/pulls?state=open&per_page=100", token)
    if status != 200:
        return found, {"error": f"cannot list pulls: {status}"}
    for pull in pulls or []:
        head = pull["head"]["sha"]
        code, body = governor.request(
            "GET", f"/repos/{repo}/commits/{head}/check-runs?per_page=100",
            token)
        if code != 200:
            continue
        for run in (body or {}).get("check_runs", []):
            app = run.get("app") or {}
            if app.get("id") != governor.GOVERNOR_APP_ID:
                continue
            if run.get("name") != context:
                continue
            if run.get("conclusion") in PASSING:
                found.append({"pr_number": pull["number"], "head_sha": head,
                              "check_run_id": run["id"],
                              "conclusion": run.get("conclusion")})
    return found, None


def invalidate(token, repo, target, auth_row, history):
    """Revoke one standing success, then read back before believing it."""
    epoch_id = f"auth-{target['head_sha'][:12]}"
    summary = "\n".join([
        "Governor verdict: EVIDENCE_INVALIDATED",
        f"Head: {target['head_sha']}",
        f"Cause: {CAUSE}",
        f"Authorization state: {auth_row['state']}",
        f"Auth generation: {auth_row['auth_generation']}",
        "",
        "User authorization was lost or its refresh outcome is unknown, so "
        "nobody was in a position to observe the providers' mutable "
        "evidence. This authorization is revoked.",
        "",
        "Reauthorization restores the ability to review, not this evidence: "
        "fresh qualification is required.",
    ])
    decision_id = history.record(
        epoch_id=epoch_id, head_sha=target["head_sha"],
        verdict="EVIDENCE_INVALIDATED", decision_rule_revision="a5b-preflight.1",
        auth_generation=auth_row["auth_generation"], decided_at=utcnow(),
        cause=CAUSE)
    history.project_pending(epoch_id, target["head_sha"],
                            target["check_run_id"], "failure", decision_id,
                            utcnow())
    status, _ = guarded_write(
        "PATCH", f"/repos/{repo}/check-runs/{target['check_run_id']}", token,
        {"status": "completed", "conclusion": "failure",
         "completed_at": utcnow(),
         "output": {"title": "Governor: EVIDENCE_INVALIDATED (authorization)",
                    "summary": summary}})
    read_status, readback = guarded_write(
        "GET", f"/repos/{repo}/check-runs/{target['check_run_id']}", token)
    observed = (readback or {}).get("conclusion")
    settled = ("CONFIRMED" if read_status == 200 and observed == "failure"
               else "OUTCOME_UNKNOWN" if read_status != 200 else "FAILED")
    history.settle_projection(epoch_id, state=settled,
                              observed_conclusion=observed, at=utcnow())
    return {**target, "decision_id": decision_id, "patch_status": status,
            "observed": observed, "state": settled, "revoked_at": utcnow()}


def apply_transition(args, store, history):
    row = store.current()
    result = {"checked_at": utcnow(),
              "auth_state": row["state"] if row else "NEVER_OBSERVED",
              "auth_generation": row["auth_generation"] if row else None,
              "stored_state_permits_triggers": store.state_permits_triggers(),
              "demands_invalidation": store.demands_invalidation(),
              "context": args.context, "invalidations": []}
    if not store.demands_invalidation():
        result["note"] = ("no invalidation demanded; note that recovery never "
                          "restores a revoked success")
        return result
    token = governor.installation_token()
    targets, error = standing_successes(token, args.repo, args.context)
    result["standing_successes_found"] = targets
    if error:
        result["error"] = error
        return result
    result["invalidations"] = [invalidate(token, args.repo, t, row, history)
                               for t in targets]
    result["restores_automatically"] = False
    return result


def cmd_check_triggers(args, store):
    """What the provider-trigger path calls before starting any work."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]
                               / "steady-state" / "harness"))
        import auth_policy
        row = auth_state.require_triggers_permitted(
            store, permission=auth_policy.evaluate(store))
        return {"triggers_permitted": True, "auth_state": row["state"],
                "auth_generation": row["auth_generation"]}
    except auth_state.AuthorizationRefused as exc:
        return {"triggers_permitted": False, "refusal": str(exc)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["apply", "check-triggers"])
    ap.add_argument("--repo", default="PhysShell/evm-from-scratch")
    ap.add_argument("--context", default=governor.CONTEXT,
                    help="check-run name to police. Defaults to the probe "
                         "context; production is passed explicitly and only "
                         "after A5b activates it.")
    ap.add_argument("--auth-db", default=str(CONFIG_DIR / "auth.sqlite3"))
    ap.add_argument("--db", default=str(CONFIG_DIR / "decisions.sqlite3"))
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    store = auth_state.AuthStore(args.auth_db)
    history = dec.History(args.db)
    try:
        if args.command == "check-triggers":
            result = cmd_check_triggers(args, store)
        else:
            result = apply_transition(args, store, history)
    finally:
        store.close()
        history.close()
    rendered = json.dumps(result, indent=2, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(rendered + "\n")
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
