#!/usr/bin/env python3
"""A5b step 3b: pre-activation closure. Read-only, and it can only refuse.

Step 3 establishes that the *frozen* inventory was bootstrapped. This
establishes something different and weaker: that GitHub, right now, is
closed — every currently open PR against the base carries exactly one
Governor `ai/final-review` failure on its *current* head.

The two claims were conflated in the first draft of the protocol, which is
what the r1 review caught. `BOOTSTRAP_COMPLETE` without a referent is
unfalsifiable, so there are now two referents and two names.

**No delta is repaired here.** Not a new PR, not a moved head, not a missing
carrier. Bootstrapping a delta in place would patch a snapshot mid-flight
and leave nobody able to say afterwards what was frozen and what was
mended. The only outputs are "closed" and "stop".

**This is an observation, not a lock.** GitHub offers no transaction
spanning a PR list and the ruleset API, so nothing here promises that
reality holds still between this read and the flip that follows. It does
not need to: a PR that appears afterwards carries no check and fails
closed. Claiming more would be quietly reasserting G2, which this programme
recorded as NOT_PROVIDED a long time ago.
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

import bootstrap
import governor
import inventory as inv


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def identity(item):
    """The tuple the freeze committed to. `draft` is included because the
    freeze included it, not because it changes the gate."""
    return (item["pr_number"], item["base"], item["head_sha"], item["draft"])


def diff_inventories(frozen, current):
    """Every way reality can have moved, named separately.

    Reported as categories rather than a single boolean, because "something
    changed" is not an operator-actionable sentence.
    """
    frozen_by_pr = {i["pr_number"]: i for i in frozen}
    current_by_pr = {i["pr_number"]: i for i in current}
    deltas = []
    for pr in sorted(set(current_by_pr) - set(frozen_by_pr)):
        deltas.append({"kind": "new_pr", "pr_number": pr,
                       "head_sha": current_by_pr[pr]["head_sha"]})
    for pr in sorted(set(frozen_by_pr) - set(current_by_pr)):
        deltas.append({"kind": "closed_pr", "pr_number": pr,
                       "frozen_head_sha": frozen_by_pr[pr]["head_sha"]})
    for pr in sorted(set(frozen_by_pr) & set(current_by_pr)):
        was, now = frozen_by_pr[pr], current_by_pr[pr]
        if was["base"] != now["base"]:
            deltas.append({"kind": "base_changed", "pr_number": pr,
                           "was": was["base"], "now": now["base"]})
        if was["head_sha"] != now["head_sha"]:
            deltas.append({"kind": "head_moved", "pr_number": pr,
                           "was": was["head_sha"], "now": now["head_sha"]})
        if was["draft"] != now["draft"]:
            deltas.append({"kind": "draft_changed", "pr_number": pr,
                           "was": was["draft"], "now": now["draft"]})
    return deltas


def carrier_state(token, repo, item):
    """Exactly one matching carrier on the CURRENT head, or a named problem."""
    head = item["head_sha"]
    runs = bootstrap.carriers(token, repo, head)
    if runs is None:
        return {"pr_number": item["pr_number"], "head_sha": head,
                "state": "UNREADABLE",
                "cause": "check runs unreadable; presence not established"}
    matching = [r for r in runs if bootstrap.matches(r, head)]
    base = {"pr_number": item["pr_number"], "head_sha": head,
            "carriers_found": [r["id"] for r in runs],
            "matching": [r["id"] for r in matching]}
    if len(matching) == 1:
        return {**base, "state": "CONFIRMED", "check_run_id": matching[0]["id"]}
    if not runs:
        return {**base, "state": "MISSING",
                "cause": "no Governor ai/final-review on this head"}
    if len(matching) > 1:
        return {**base, "state": "DUPLICATE",
                "cause": f"{len(matching)} matching carriers; expected one"}
    return {**base, "state": "MISMATCH",
            "cause": "a carrier exists but does not match on conclusion, "
                     "head or verdict",
            "observed": [{"id": r["id"], "conclusion": r.get("conclusion"),
                          "head_sha": r.get("head_sha")} for r in runs]}


def close(args):
    frozen_artifact = json.loads(Path(args.inventory).read_text())
    frozen = frozen_artifact["inventory"]
    repo = frozen_artifact["repo"]
    base = frozen_artifact["base"]

    token = governor.installation_token()
    started_at = utcnow()
    current = inv.enumerate_open(token, repo, base)
    states = [carrier_state(token, repo, item) for item in current]
    second = inv.enumerate_open(token, repo, base)
    finished_at = utcnow()

    moved_during = [identity(i) for i in current] != [identity(i) for i in second]
    deltas = diff_inventories(frozen, current)
    if moved_during:
        deltas.append({"kind": "changed_during_observation",
                       "cause": "the PR set moved while 3b was reading it"})

    bad = [s for s in states if s["state"] != "CONFIRMED"]
    closed = not deltas and not bad and len(current) == len(frozen)

    result = {
        "step": "A5b step 3b pre-activation closure",
        "repo": repo, "base": base,
        "frozen_inventory_hash": frozen_artifact["inventory_hash"],
        "frozen_inventory_file": str(args.inventory),
        "observed_at": started_at, "observation_finished_at": finished_at,
        "frozen_count": len(frozen), "current_count": len(current),
        "current_inventory": current,
        "carrier_states": states,
        "deltas": deltas,
        "preactivation_current_inventory_closed": closed,
        "verdict": "CLOSED" if closed else "STOP",
    }
    if not closed:
        result["required_action"] = (
            "STOP. Do not create or activate the ruleset. Do not bootstrap "
            "the delta in place. Record it, freeze a new inventory as an "
            "amendment, and repeat steps 3 and 3b from there.")
    else:
        result["scope_note"] = (
            "This is a closure observation at a point in time, not a lock. "
            "A PR appearing after this read carries no check and fails "
            "closed; claiming otherwise would reassert G2.")
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--inventory", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    result = close(args)
    rendered = json.dumps(result, indent=2, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(rendered + "\n")
    print(rendered)
    return 0 if result["preactivation_current_inventory_closed"] else 1


if __name__ == "__main__":
    sys.exit(main())
