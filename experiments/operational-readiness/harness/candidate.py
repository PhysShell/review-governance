#!/usr/bin/env python3
"""First governed review: establish the candidate. Read-only.

This module selects nothing by memory and nothing by position. It takes a
fresh snapshot, applies stated eligibility rules to it, and emits a
commit-bound artifact bound to a **full** head SHA.

Three refusals are structural rather than advisory:

**No implicit ordering.** A candidate is chosen because it satisfies named
conditions, never because it came first out of an API. `pulls[0]` is the
defect this programme has found more times than any other, and a roadmap
naming a PR in advance is the same defect wearing a schedule.

**No draft.** A draft PR is not a merge candidate, so it cannot be the
first governed review.

**No carrier from another head.** A check run bound to a different commit
is evidence about that commit. Evidence does not migrate when a branch
moves, which is exactly why a moved head must fail closed rather than
inherit.

It cannot start a provider round: there is no trigger code in this module
or anywhere on this branch, which is asserted by test rather than promised
in prose.
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

import bootstrap
import cutover
import ruleset as rs

PRODUCTION_CONTEXT = "ai/final-review"
RULESET_ID = 21640654


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def gate_state(repo, ruleset_id=RULESET_ID):
    """The production gate, established by readback rather than by memory."""
    named, error = rs.find_by_name(repo, cutover.canonical_ruleset()["name"])
    if named is None:
        return {"state": "STOP_PRODUCTION_GATE_STATE_CHANGED",
                "cause": "cannot list rulesets", "error": error}
    if len(named) != 1:
        return {"state": "STOP_PRODUCTION_GATE_STATE_CHANGED",
                "cause": f"expected exactly one ruleset, found "
                         f"{[r['id'] for r in named]}"}
    if named[0]["id"] != ruleset_id:
        return {"state": "STOP_PRODUCTION_GATE_STATE_CHANGED",
                "cause": f"ruleset id is {named[0]['id']}, expected {ruleset_id}"}
    verified = rs.verify(repo, ruleset_id, "active")
    if verified["state"] != "VERIFIED" or \
            verified.get("observed_enforcement") != "active":
        return {"state": "STOP_PRODUCTION_GATE_STATE_CHANGED",
                "cause": "ruleset is not a verified active object",
                "verification": verified}
    return {"state": "VERIFIED_ACTIVE", "ruleset_id": ruleset_id,
            "POLICY_HASH": verified["POLICY_HASH"]["observed"],
            "ACTIVE_RULESET_HASH": verified["FULL_HASH"]["observed"],
            "verification": verified}


def carrier_for(repo, head_sha, token):
    """The production carrier on this exact head, if there is exactly one."""
    runs = bootstrap.carriers(token, repo, head_sha)
    if runs is None:
        return {"state": "UNREADABLE"}
    exact = [r for r in runs if r.get("head_sha") == head_sha]
    if not exact:
        return {"state": "ABSENT", "runs_on_head": []}
    if len(exact) > 1:
        return {"state": "AMBIGUOUS", "runs_on_head": [r["id"] for r in exact]}
    run = exact[0]
    return {"state": "PRESENT", "run_id": run["id"],
            "head_sha": run["head_sha"],
            "app_id": (run.get("app") or {}).get("id"),
            "conclusion": run.get("conclusion"),
            "verdict": (run.get("output") or {}).get("title")}


def ancestry(repo, pre_update_head, head_sha, main_sha):
    """Re-derive the base update from GitHub rather than transcribe it.

    Three separate facts, because "the merge worked" is not one of them:
    the old head is an ancestor (history was not rewritten), main is an
    ancestor (the branch is current), and the merge base equals main (the
    branch is not BEHIND, so a later block cannot be drift).
    """
    def compare(a, b):
        ok, body = rs.gh("api", f"repos/{repo}/compare/{a}...{b}")
        if not ok:
            return None
        return {"status": body.get("status"),
                "ahead_by": body.get("ahead_by"),
                "behind_by": body.get("behind_by"),
                "merge_base": (body.get("merge_base_commit") or {}).get("sha")}

    from_old = compare(pre_update_head, head_sha) if pre_update_head else None
    from_main = compare(main_sha, head_sha)
    return {
        "pre_update_head": pre_update_head,
        "main_sha": main_sha,
        "old_head_to_new": from_old,
        "main_to_new": from_main,
        "old_head_is_ancestor": bool(from_old) and from_old["behind_by"] == 0,
        "main_is_ancestor": bool(from_main) and from_main["behind_by"] == 0,
        "merge_base_is_main": bool(from_main) and from_main["merge_base"] == main_sha,
        "history_rewritten": not (bool(from_old) and from_old["behind_by"] == 0),
    }


def eligible(item):
    """Named conditions, evaluated per PR. Order is never one of them."""
    reasons = []
    if item["draft"]:
        reasons.append("draft")
    if item["base"] != "main":
        reasons.append(f"base is {item['base']}, not main")
    return {"eligible": not reasons, "excluded_because": reasons}


def select(inventory):
    """Exactly one eligible PR, or no selection at all.

    Two eligible candidates is not a tie to be broken by position — it is a
    question for a human, because picking one would be `pulls[0]` with
    extra steps.
    """
    scored = [{**item, **eligible(item)} for item in inventory]
    eligible_items = [i for i in scored if i["eligible"]]
    if len(eligible_items) == 1:
        return {"selected": eligible_items[0], "scored": scored}
    if not eligible_items:
        return {"selected": None, "scored": scored,
                "cause": "no open non-draft PR against main"}
    return {"selected": None, "scored": scored,
            "cause": f"{len(eligible_items)} eligible candidates "
                     f"({[i['pr_number'] for i in eligible_items]}); selection "
                     "is a human decision, not an ordering"}


def build(repo, expected_pr, token, inventory, gate, pre_update_head=None,
          main_sha=None, method=None):
    """Assemble the artifact. Binding is always to a full head SHA."""
    chosen = select(inventory)
    artifact = {
        "artifact": "FirstGovernedReviewCandidate-v1",
        "repo": repo,
        "observed_at": utcnow(),
        "gate": gate,
        "inventory": chosen["scored"],
        "selection_cause": chosen.get("cause"),
    }
    selected = chosen["selected"]
    if selected is None:
        artifact["candidate_state"] = "STOP_NO_UNIQUE_CANDIDATE"
        return artifact
    if expected_pr is not None and selected["pr_number"] != expected_pr:
        artifact["candidate_state"] = "STOP_PRESELECTION_STALE"
        artifact["cause"] = (f"the named preselection was #{expected_pr}; the "
                             f"snapshot selects #{selected['pr_number']}")
        return artifact

    head = selected["head_sha"]
    if len(head) != 40:
        artifact["candidate_state"] = "STOP_ABBREVIATED_HEAD"
        return artifact
    artifact["pr_number"] = selected["pr_number"]
    artifact["base_ref"] = selected["base"]
    artifact["head_sha"] = head
    artifact["draft"] = selected["draft"]
    artifact["carrier"] = carrier_for(repo, head, token)
    if main_sha:
        artifact["base_update"] = {
            "method": method or "merge main -> candidate branch",
            "rebase_used": False, "force_push_used": False,
            **ancestry(repo, pre_update_head, head, main_sha)}
    artifact["provider_round"] = "NOT_STARTED"
    artifact["candidate_state"] = "READY_FOR_ACCEPT_CANDIDATE"
    return artifact


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=cutover.REPO)
    ap.add_argument("--expect-pr", type=int, default=None,
                    help="the preselection to confirm. A mismatch is a STOP, "
                         "never an adjustment.")
    ap.add_argument("--pre-update-head", default=None,
                    help="the head before the base update, so ancestry is "
                         "re-derived from GitHub rather than transcribed")
    ap.add_argument("--method", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    import governor
    import inventory as inv
    token = governor.installation_token()
    gate = gate_state(args.repo)
    if gate["state"] != "VERIFIED_ACTIVE":
        result = {"artifact": "FirstGovernedReviewCandidate-v1",
                  "repo": args.repo, "observed_at": utcnow(), "gate": gate,
                  "candidate_state": gate["state"]}
    else:
        ok, ref = rs.gh("api", f"repos/{args.repo}/git/ref/heads/main")
        main_sha = ref["object"]["sha"] if ok else None
        result = build(args.repo, args.expect_pr, token,
                       inv.enumerate_open(token, args.repo, "main"), gate,
                       pre_update_head=args.pre_update_head,
                       main_sha=main_sha, method=args.method)
    rendered = json.dumps(result, indent=2, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(rendered + "\n")
    print(rendered)
    return 0 if result["candidate_state"] == "READY_FOR_ACCEPT_CANDIDATE" else 1


if __name__ == "__main__":
    sys.exit(main())
