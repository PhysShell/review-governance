#!/usr/bin/env python3
"""A5b step 5: prove the production gate refuses a merge, for the right reason.

A blocked merge on its own proves nothing here. Under `strict`, a probe
whose base has drifted is refused with the identical `"…is expected."` that
a missing required check produces, so recording a block as evidence for the
required-check path would be a guess wearing the shape of a proof. GitHub
cannot be asked which cause applied.

So the alternative is excluded **by construction, before the attempt**, by
a predicate frozen in the protocol rather than chosen while looking at a
result:

    main_sha_before   read now
    freshness         merge-base(probe, main) == main_sha_before
                      -> the probe cannot be BEHIND
    check absence     no ai/final-review of ANY conclusion on the probe head
                      -> not "no success"; the path under test is the
                         missing-check path, and a failing check would
                         block for a different reason
    ruleset           active, POLICY_HASH and ACTIVE_RULESET_HASH matching

`main_sha_after` is read too, so a base that moved *during* the attempt is
visible rather than assumed away.

A stale fixture is counted as neither PASS nor FAIL. This is a validity
predicate, not a retry loop: it can only invalidate a fixture, and it can
never convert a failed test into a passing one. A genuine block on a fresh,
checkless probe counts the first time.

The merge is attempted by the **owner**, never by the Governor, which has
no merge path in any module. The gate refusing the owner is the whole
point; a Governor that could merge would be gating itself.
"""
import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path

import cutover
import ruleset as rs

PRODUCTION_CONTEXT = "ai/final-review"


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def gh_raw(*args, expect_json=True):
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    payload = proc.stdout
    if expect_json:
        try:
            payload = json.loads(proc.stdout or "null")
        except ValueError:
            payload = {"raw": proc.stdout[:400]}
    return proc.returncode == 0, payload, proc.stderr.strip()[:400]


def main_sha(repo):
    ok, body, _ = gh_raw("api", f"repos/{repo}/git/ref/heads/main")
    return body["object"]["sha"] if ok else None


def merge_base(repo, base_sha, head_sha):
    ok, body, _ = gh_raw("api", f"repos/{repo}/compare/{base_sha}...{head_sha}")
    if not ok:
        return None
    return (body.get("merge_base_commit") or {}).get("sha")


def production_runs(repo, head_sha):
    ok, body, _ = gh_raw(
        "api", f"repos/{repo}/commits/{head_sha}/check-runs?per_page=100")
    if not ok:
        return None
    return [{"id": r["id"], "conclusion": r.get("conclusion"),
             "app_id": (r.get("app") or {}).get("id")}
            for r in (body or {}).get("check_runs", [])
            if r.get("name") == PRODUCTION_CONTEXT]


def fixture_predicate(repo, pr_number, ruleset_id):
    """All four, evaluated immediately before the attempt."""
    ok, pull, _ = gh_raw("api", f"repos/{repo}/pulls/{pr_number}")
    if not ok:
        return {"valid": False, "cause": "cannot read the probe PR"}
    head = pull["head"]["sha"]
    before = main_sha(repo)
    base = merge_base(repo, before, head) if before else None
    runs = production_runs(repo, head)
    verified = rs.verify(repo, ruleset_id, "active")

    checks = {
        "probe_head": head,
        "probe_base_ref": pull["base"]["ref"],
        "main_sha_before": before,
        "merge_base": base,
        "base_is_main": pull["base"]["ref"] == "main",
        "base_fresh": bool(before) and base == before,
        "production_runs_on_probe_head": runs,
        "check_absent": runs == [],
        "ruleset_active": verified.get("observed_enforcement") == "active",
        "ruleset_hashes_match": verified.get("state") == "VERIFIED",
        "ruleset_verification": verified,
    }
    checks["valid"] = all([checks["base_is_main"], checks["base_fresh"],
                           checks["check_absent"], checks["ruleset_active"],
                           checks["ruleset_hashes_match"]])
    if runs is None:
        checks["valid"] = False
        checks["cause"] = "check runs unreadable; absence not established"
    return checks


def attempt_merge(repo, pr_number, head_sha):
    """Exactly one attempt, at the exact head, by the owner."""
    ok, body, stderr = gh_raw(
        "api", "-X", "PUT", f"repos/{repo}/pulls/{pr_number}/merge",
        "-f", f"sha={head_sha}")
    return {"at": utcnow(), "merged_reported": bool(ok and (body or {}).get("merged")),
            "response": body, "stderr": stderr}


def run(args):
    repo = args.repo
    result = {"step": "A5b step 5 negative production smoke test",
              "repo": repo, "pr_number": args.pr, "started_at": utcnow()}

    predicate = fixture_predicate(repo, args.pr, args.ruleset_id)
    result["fixture_predicate"] = predicate
    if not predicate["valid"]:
        result["verdict"] = "SMOKE_FIXTURE_STALE"
        result["counted"] = False
        result["note"] = ("neither PASS nor FAIL; recreate or rebase the "
                          "probe and re-evaluate")
        return result

    head = predicate["probe_head"]
    attempt = attempt_merge(repo, args.pr, head)
    result["attempt"] = attempt

    after = main_sha(repo)
    result["main_sha_after"] = after
    if after != predicate["main_sha_before"]:
        result["verdict"] = "SMOKE_FIXTURE_STALE"
        result["counted"] = False
        result["note"] = "main moved during the attempt; the block cannot be " \
                         "attributed"
        return result

    ok, pull, _ = gh_raw("api", f"repos/{repo}/pulls/{args.pr}")
    result["probe_head_after"] = pull["head"]["sha"] if ok else None
    result["probe_merged"] = bool(ok and pull.get("merged"))
    if result["probe_head_after"] != head:
        result["verdict"] = "SMOKE_FIXTURE_STALE"
        result["counted"] = False
        return result

    ok, gql, _ = gh_raw("pr", "view", str(args.pr), "--repo", repo,
                        "--json", "mergeStateStatus,mergeable")
    result["merge_state_status"] = (gql or {}).get("mergeStateStatus")
    result["corroborating_only"] = (
        "mergeStateStatus is recorded as corroboration; the causality rests "
        "on the freshness predicate, which was established before the "
        "attempt")

    if attempt["merged_reported"] or result["probe_merged"]:
        result["verdict"] = "FAIL"
        result["counted"] = True
        result["incident"] = ("the probe merged through an active gate with "
                              "no ai/final-review; this is an incident, not a "
                              "test result")
        return result

    result["verdict"] = "NEGATIVE_SMOKE_TEST_BLOCKED"
    result["counted"] = True
    result["finished_at"] = utcnow()
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default=cutover.REPO)
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--ruleset-id", type=int, required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    result = run(args)
    rendered = json.dumps(result, indent=2, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(rendered + "\n")
    print(rendered)
    return 0 if result["verdict"] == "NEGATIVE_SMOKE_TEST_BLOCKED" else 1


if __name__ == "__main__":
    sys.exit(main())
