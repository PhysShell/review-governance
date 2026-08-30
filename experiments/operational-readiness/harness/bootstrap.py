#!/usr/bin/env python3
"""A5b step 3: bootstrap every frozen head to a fail-closed Governor verdict.

The first production write in the programme. Two properties are enforced in
code rather than by intention:

**It can only publish `failure`.** Not a policy, a capability: `guarded()`
raises for any other conclusion, for any name other than the production
context, and for any method other than POST of a new check run. A bootstrap
that could accidentally emit a passing conclusion would open the gate on
every PR it touched, at the exact moment nobody is watching for that.

**It acts on the frozen inventory, never on a fresh list.** Re-reading
GitHub here would quietly make the bootstrap its own baseline, which is the
`pulls[0]` shape the programme keeps finding. Step 3b is where current
reality is consulted, and it stops the stage rather than absorbing drift.

**Ambiguity is never resolved by repeating the write.** A POST whose
response was lost may or may not have created a run. Re-POSTing turns that
into either a duplicate carrier or a second unknown, so the recovery is a
readback: exactly one matching carrier is CONFIRMED, zero or more than one
is UNCERTAIN and stops the stage for a human. The habit of curing a lost
response with another write has bought cloud providers a great many yachts.
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

import decisions as dec
import governor

PRODUCTION_CONTEXT = "ai/final-review"
VERDICT = "NOT_ESTABLISHED"
ONLY_CONCLUSION = "failure"

SUMMARY = "\n".join([
    f"Governor verdict: {VERDICT}",
    "",
    "Activation bootstrap.",
    "No final-review evidence has been established for this head.",
    "Fresh qualification is required before this check can pass.",
    "",
    "This is not a review result. No provider round was started, and none",
    "starts without an explicit ACCEPT-CANDIDATE transition.",
])


class BootstrapCapability(Exception):
    """Raised where this module is asked to step outside its one job."""


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def guarded(method, path, token, body=None):
    """The capability boundary, in code."""
    if method == "GET":
        return governor.request(method, path, token, body)
    if method != "POST" or not path.endswith("/check-runs"):
        raise BootstrapCapability(
            f"bootstrap may not {method} {path}: it may only create a check run")
    if (body or {}).get("name") != PRODUCTION_CONTEXT:
        raise BootstrapCapability(
            f"bootstrap may not write the name {(body or {}).get('name')!r}")
    if (body or {}).get("conclusion") != ONLY_CONCLUSION:
        raise BootstrapCapability(
            f"bootstrap may not set conclusion "
            f"{(body or {}).get('conclusion')!r}: it can only fail closed")
    return governor.request(method, path, token, body)


def carriers(token, repo, head_sha):
    """Every Governor-owned production-context run on this exact head."""
    status, body = guarded(
        "GET", f"/repos/{repo}/commits/{head_sha}/check-runs?per_page=100",
        token)
    if status != 200:
        return None
    return [r for r in (body or {}).get("check_runs", [])
            if r.get("name") == PRODUCTION_CONTEXT
            and (r.get("app") or {}).get("id") == governor.GOVERNOR_APP_ID]


def matches(run, head_sha):
    """Only an exact match counts. Name and app id are not enough on their
    own — a run bound to a different head would be evidence about a commit
    nobody bootstrapped."""
    return (run.get("head_sha") == head_sha
            and run.get("name") == PRODUCTION_CONTEXT
            and (run.get("app") or {}).get("id") == governor.GOVERNOR_APP_ID
            and run.get("conclusion") == ONLY_CONCLUSION
            and VERDICT in ((run.get("output") or {}).get("summary") or ""))


def bootstrap_one(token, repo, item, history):
    head = item["head_sha"]
    result = {"pr_number": item["pr_number"], "head_sha": head,
              "draft": item["draft"], "attempted_at": utcnow()}

    before = carriers(token, repo, head)
    if before is None:
        result["state"] = "UNCERTAIN"
        result["cause"] = "cannot read existing check runs; absence not established"
        return result
    if before:
        result["state"] = "REFUSED"
        result["cause"] = (f"{PRODUCTION_CONTEXT} already exists on this head "
                           f"({[r['id'] for r in before]}); zero point is not clean")
        return result

    epoch_id = f"bootstrap-{head[:12]}"
    decision_id = history.record(
        epoch_id=epoch_id, head_sha=head, verdict=VERDICT,
        decision_rule_revision="a5b.bootstrap.1",
        auth_generation=0, decided_at=utcnow(),
        cause="A5b activation bootstrap; no evidence established")
    history.project_pending(epoch_id, head, None, ONLY_CONCLUSION,
                            decision_id, utcnow())

    post_status, created = guarded(
        "POST", f"/repos/{repo}/check-runs", token,
        {"name": PRODUCTION_CONTEXT, "head_sha": head, "status": "completed",
         "conclusion": ONLY_CONCLUSION, "completed_at": utcnow(),
         "external_id": epoch_id,
         "output": {"title": f"Governor: {VERDICT}", "summary": SUMMARY}})
    result["post_status"] = post_status
    result["decision_id"] = decision_id

    # The POST response is never the confirmation (A3b-c4). Whatever it said,
    # and especially if it said nothing, the readback decides.
    after = carriers(token, repo, head)
    if after is None:
        result["state"] = "UNCERTAIN"
        result["cause"] = "readback failed; carrier count unknown"
        history.settle_projection(epoch_id, state="OUTCOME_UNKNOWN",
                                  observed_conclusion=None, at=utcnow())
        return result

    matching = [r for r in after if matches(r, head)]
    result["carriers_found"] = [r["id"] for r in after]
    result["matching"] = [r["id"] for r in matching]

    if len(matching) == 1:
        run = matching[0]
        result["state"] = "CONFIRMED"
        result["check_run_id"] = run["id"]
        result["observed"] = {"head_sha": run["head_sha"], "name": run["name"],
                              "app_id": (run.get("app") or {}).get("id"),
                              "conclusion": run["conclusion"],
                              "verdict_in_summary": True}
        history.settle_projection(epoch_id, state="CONFIRMED",
                                  observed_conclusion=run["conclusion"],
                                  check_run_id=run["id"], at=utcnow())
        return result

    result["state"] = "UNCERTAIN"
    result["cause"] = (
        f"{len(matching)} matching carriers after one POST; expected exactly "
        "one. NOT retrying — a repeated write would turn a lost response into "
        "a duplicate carrier or a second unknown.")
    history.settle_projection(epoch_id, state="OUTCOME_UNKNOWN",
                              observed_conclusion=None, at=utcnow())
    return result


def run(args):
    artifact = json.loads(Path(args.inventory).read_text())
    if not artifact.get("frozen"):
        return {"error": "inventory artifact is not a valid freeze",
                "refusal": artifact.get("refusal")}
    token = governor.installation_token()
    history = dec.History(args.db)
    try:
        results = [bootstrap_one(token, artifact["repo"], item, history)
                   for item in artifact["inventory"]]
    finally:
        history.close()
    confirmed = [r for r in results if r["state"] == "CONFIRMED"]
    return {
        "step": "A5b step 3 bootstrap",
        "inventory_hash": artifact["inventory_hash"],
        "inventory_file": str(args.inventory),
        "protocol_head": artifact.get("protocol_head"),
        "repo": artifact["repo"],
        "finished_at": utcnow(),
        "results": results,
        "frozen_inventory_bootstrapped":
            len(confirmed) == len(artifact["inventory"]),
        "all_confirmed_by_readback": all(r["state"] == "CONFIRMED"
                                         for r in results),
        "provider_rounds_started": 0,
        "note": "conclusion is failure on every head by construction; this "
                "module has no code path to any other conclusion",
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--inventory", required=True,
                    help="the frozen artifact. A fresh list is NOT accepted.")
    ap.add_argument("--db", default=str(governor.CONFIG_DIR / "decisions.sqlite3"))
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    result = run(args)
    rendered = json.dumps(result, indent=2, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(rendered + "\n")
    print(rendered)
    return 0 if result.get("frozen_inventory_bootstrapped") else 1


if __name__ == "__main__":
    sys.exit(main())
