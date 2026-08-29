#!/usr/bin/env python3
"""A6a live qualification: the single permitted production write.

Only the fail-closed path, only on the exact head named at authorisation.
If either head has moved, the fixture is not adapted — that is a STOP and a
new decision, because adapting a fixture to whatever is currently there is
how a test comes to measure the present instead of the hypothesis.
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parents[1] / "operational-readiness" / "harness"))

import carrier
import epochs as ep
import governor
import ruleset as rs
import scoped_reconcile as sr

EXPECTED_PR = 8
EXPECTED_HEAD = "2d8348703924c7470ba82f525cafc9afe720aee2"
EXPECTED_MAIN = "047ff1a641e33e0bb8c6b9eea26bb80eea021e08"
RULESET_ID = 21640654


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_request(token):
    """A GitHub caller shaped for the injected-request modules."""
    def request(method, path, tok=None, body=None):
        return governor.request(method, path, token, body)
    return request


def pre_read(request, repo, args):
    status, pull = request("GET", f"/repos/{repo}/pulls/{args.pr}")
    head = pull["head"]["sha"] if status == 200 else None
    _, ref = request("GET", f"/repos/{repo}/git/ref/heads/main")
    main = (ref or {}).get("object", {}).get("sha")
    verified = rs.verify(repo, args.ruleset_id, "active")
    runs = carrier.read_carriers(request, repo, head, None) if head else None
    checks = {
        "pr_number": args.pr,
        "head_observed": head,
        "head_expected": args.expect_head,
        "head_unchanged": head == args.expect_head,
        "main_observed": main,
        "main_expected": args.expect_main,
        "main_unchanged": main == args.expect_main,
        "ruleset_verified_active": verified.get("state") == "VERIFIED"
        and verified.get("observed_enforcement") == "active",
        "ruleset_verification": verified,
        "carriers_on_head": None if runs is None else [r["id"] for r in runs],
        "carriers_absent": runs == [],
        "at": utcnow(),
    }
    checks["ready"] = all([checks["head_unchanged"], checks["main_unchanged"],
                           checks["ruleset_verified_active"],
                           checks["carriers_absent"]])
    return checks


def run(args):
    repo = args.repo
    token = governor.installation_token()
    request = make_request(token)
    store = ep.EpochStore(args.db)
    result = {"step": "A6a live qualification", "repo": repo,
              "started_at": utcnow()}
    try:
        checks = pre_read(request, repo, args)
        result["pre_read"] = checks
        if not checks["ready"]:
            result["verdict"] = "STOP_FIXTURE_CHANGED"
            result["note"] = ("the fixture is not adapted to what is there "
                              "now; this needs a new decision")
            return result

        head = checks["head_observed"]
        produced = carrier.ensure(request, repo, args.pr, head, None, store)
        result["carrier"] = produced
        if produced["state"] != "CONFIRMED":
            result["verdict"] = f"STOP_{produced['state']}"
            return result

        # independent readback of that exact run, not of the head listing
        _, run_body = request("GET",
                              f"/repos/{repo}/check-runs/{produced['carrier']}")
        result["independent_readback"] = {
            "check_run_id": (run_body or {}).get("id"),
            "app_id": ((run_body or {}).get("app") or {}).get("id"),
            "head_sha": (run_body or {}).get("head_sha"),
            "conclusion": (run_body or {}).get("conclusion"),
            "title": ((run_body or {}).get("output") or {}).get("title"),
            "exact_head_match": (run_body or {}).get("head_sha") == head,
            "app_matches": ((run_body or {}).get("app") or {}).get("id") == 4669438,
        }

        epoch = store.epoch(produced["epoch_id"])
        result["durable_epoch"] = {
            "epoch_id": epoch["epoch_id"], "repo": epoch["repo"],
            "pr_number": epoch["pr_number"], "head_sha": epoch["head_sha"],
            "generation": epoch["generation"],
            "identifies_pr": epoch["pr_number"] == args.pr,
        }

        result["reconciliation"] = sr.reconcile(request, repo, args.pr, store)

        # cross-PR negative control: #12's state must never answer for #8
        other = store.last_known_head(repo, 12)
        result["cross_pr_control"] = {
            "pr_12_state": other.get("state"),
            "pr_12_head": other.get("head_sha"),
            "pr_8_head": result["reconciliation"].get("stored_head"),
            "distinct": other.get("head_sha") != result["reconciliation"].get("stored_head"),
        }

        rb = result["independent_readback"]
        rec = result["reconciliation"]
        result["verdict"] = ("QUALIFIED" if all([
            rb["exact_head_match"], rb["app_matches"],
            rb["conclusion"] == "failure",
            result["durable_epoch"]["identifies_pr"],
            rec.get("comparison_performed") is True,
            rec.get("drift_detected") is False,
            result["cross_pr_control"]["distinct"],
        ]) else "STOP_QUALIFICATION_INCOMPLETE")
        result["finished_at"] = utcnow()
        return result
    finally:
        store.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="PhysShell/evm-from-scratch")
    ap.add_argument("--pr", type=int, default=EXPECTED_PR)
    ap.add_argument("--expect-head", default=EXPECTED_HEAD)
    ap.add_argument("--expect-main", default=EXPECTED_MAIN)
    ap.add_argument("--ruleset-id", type=int, default=RULESET_ID)
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    result = run(args)
    rendered = json.dumps(result, indent=2, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(rendered + "\n")
    print(rendered)
    return 0 if result["verdict"] == "QUALIFIED" else 1


if __name__ == "__main__":
    sys.exit(main())
