#!/usr/bin/env python3
"""Cutover instruments — planning only, in A5a.

Two things are produced here and neither is applied:

  * the canonical production ruleset, with a stable hash, so that A5b can
    create exactly the reviewed object and a break-glass restore can be
    verified against it rather than against memory;
  * the bootstrap plan for existing open PRs, as a dry run.

`ai/final-review` is never created by this module. It emits the plan; a
separate, separately approved stage executes it.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO = "PhysShell/evm-from-scratch"
GOVERNOR_APP_ID = 4669438
PRODUCTION_CONTEXT = "ai/final-review"

ALLOWED_CONCLUSIONS = ["success", "failure", "cancelled", "action_required",
                       "timed_out"]
FORBIDDEN_CONCLUSIONS = ["neutral", "skipped"]

BOOTSTRAP_SUMMARY = "\n".join([
    "Activation bootstrap.",
    "No final-review evidence established for this head.",
    "Fresh qualification required.",
])


def gh_json(*args):
    result = subprocess.run(["gh", *args], capture_output=True, text=True,
                            check=True)
    return json.loads(result.stdout)


def canonical_ruleset() -> dict:
    """The exact object A5b may create — nothing more, nothing less."""
    return {
        "name": "ai-final-review-enforcement",
        "target": "branch",
        "enforcement": "active",
        "bypass_actors": [],
        "conditions": {"ref_name": {"include": ["refs/heads/main"],
                                    "exclude": []}},
        "rules": [{
            "type": "required_status_checks",
            "parameters": {
                "required_status_checks": [
                    {"context": PRODUCTION_CONTEXT,
                     "integration_id": GOVERNOR_APP_ID}],
                "strict_required_status_checks_policy": True,
            },
        }],
    }


def canonical_hash(ruleset: dict) -> str:
    return hashlib.sha256(
        json.dumps(ruleset, sort_keys=True).encode()).hexdigest()


def policy_hash(ruleset: dict) -> str:
    """Hash of everything except `enforcement`.

    A5a-c2 found the defect this fixes: the cutover sequence creates the
    ruleset disabled, hashes the readback, and only then flips it active —
    but a hash that covers `enforcement` cannot match across that flip by
    construction. Splitting the hashes lets a state transition stop looking
    like a policy change, and lets the policy be proven identical on both
    sides of it.
    """
    without_enforcement = {k: v for k, v in ruleset.items() if k != "enforcement"}
    return hashlib.sha256(
        json.dumps(without_enforcement, sort_keys=True).encode()).hexdigest()


def ruleset_with(enforcement: str) -> dict:
    return {**canonical_ruleset(), "enforcement": enforcement}


def hashes() -> dict:
    """The three values A5b verifies against."""
    active = ruleset_with("active")
    disabled = ruleset_with("disabled")
    return {
        "POLICY_HASH": policy_hash(active),
        "DISABLED_RULESET_HASH": canonical_hash(disabled),
        "ACTIVE_RULESET_HASH": canonical_hash(active),
    }


def cmd_ruleset(args):
    ruleset = canonical_ruleset()
    digests = hashes()
    return {
        "canonical_ruleset": ruleset,
        "canonical_hash": canonical_hash(ruleset),
        "hashes": digests,
        "verification_sequence": [
            "create DISABLED -> readback matches DISABLED_RULESET_HASH and POLICY_HASH",
            "flip ACTIVE     -> readback matches ACTIVE_RULESET_HASH and POLICY_HASH",
            "POLICY_HASH must be identical on both sides of the flip",
        ],
        "created": False,
        "note": "generated for review; A5b creates it DISABLED first, hashes "
                "the readback, then flips enforcement to active",
        "conclusions_allowed": ALLOWED_CONCLUSIONS,
        "conclusions_forbidden": FORBIDDEN_CONCLUSIONS,
        "statuses_permission": "remains ABSENT",
    }


def cmd_bootstrap_plan(args):
    """Freeze the inventory and say exactly what would be written."""
    pulls = gh_json("pr", "list", "--repo", REPO, "--state", "open",
                    "--base", "main", "--json",
                    "number,headRefOid,isDraft,title,headRefName")
    observed_at = subprocess.run(["date", "-u", "+%Y-%m-%dT%H:%M:%SZ"],
                                 capture_output=True, text=True).stdout.strip()
    frozen = [{"pr_number": p["number"], "head_sha": p["headRefOid"],
               "draft": p["isDraft"], "branch": p["headRefName"],
               "observed_at": observed_at} for p in pulls]
    planned = [{
        "pr_number": item["pr_number"],
        "head_sha": item["head_sha"],
        "check": {"name": PRODUCTION_CONTEXT, "app_id": GOVERNOR_APP_ID,
                  "conclusion": "failure", "verdict": "NOT_ESTABLISHED",
                  "output_summary": BOOTSTRAP_SUMMARY},
        "provider_round": "NOT started",
        "reason": ("draft PRs stay failing and consume no provider quota"
                   if item["draft"] else
                   "non-draft PRs are not auto-reviewed either; a provider "
                   "round starts only on an explicit ACCEPT-CANDIDATE "
                   "transition"),
    } for item in frozen]
    return {"observed_at": observed_at, "frozen_inventory": frozen,
            "planned_bootstrap": planned, "applied": False,
            "production_context_used": False,
            "note": "dry run; no check run was created and ai/final-review "
                    "remains unused"}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["ruleset", "bootstrap-plan"])
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    result = {"ruleset": cmd_ruleset,
              "bootstrap-plan": cmd_bootstrap_plan}[args.command](args)
    rendered = json.dumps(result, indent=2, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(rendered + "\n")
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
