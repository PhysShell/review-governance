"""Exact-head evidence bundle and the reducer that fails closed.

A3a's finding is the reason this exists in the shape it does: after a head
moved, re-observing the providers *without* the frozen bundle still
reported both as qualified, while the frozen bundle correctly showed the
evidence was about a commit nobody had reviewed. So the bundle, not the
live carrier, is the policy input — and the bundle is bound to a full head
and hashed.

The reducer is deliberately dull. It returns SUCCESS only when every named
condition holds, and every other combination — incomplete, stale,
ambiguous, unauthorized — reduces to NOT_ESTABLISHED. There is no branch
that reaches SUCCESS by exhaustion of alternatives.
"""
import datetime
import hashlib
import json

import auth_policy

SCHEMA_NAME = "ProductionEvidenceBundle-v1"

SUCCESS = "SUCCESS"
NOT_ESTABLISHED = "NOT_ESTABLISHED"
REQUIRED_PROVIDERS = ("codex", "coderabbit")


class BundleError(Exception):
    """Raised where a bundle would be built without the head it is about."""


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_bundle(*, repo, pr_number, head_sha, lineage_records,
                 auth_generation, frozen_at=None):
    if len(head_sha or "") != 40:
        raise BundleError("a bundle must be bound to a full head SHA")
    payload = {
        "schema": SCHEMA_NAME,
        "repo": repo, "pr_number": pr_number, "head_sha": head_sha,
        "auth_generation": auth_generation,
        "providers": sorted(
            [{"provider": r["provider"], "generation": r["generation"],
              "requested_for_head": r["requested_for_head"],
              "state": r["state"],
              "qualified": bool((r.get("qualification") or {}).get("qualified"))}
             for r in lineage_records],
            key=lambda p: (p["provider"], p["generation"])),
        "frozen_at": frozen_at or utcnow(),
    }
    payload["bundle_hash"] = hashlib.sha256(
        json.dumps({k: v for k, v in payload.items() if k != "frozen_at"},
                   sort_keys=True).encode()).hexdigest()
    return payload


def reduce(bundle, *, current_head_sha, permission, auth_generation):
    """The only function permitted to conclude SUCCESS, and it rarely does.

    Every refusal is named. The list is the point: a reducer that returns a
    bare boolean teaches callers to ask the wrong question, and a reducer
    that reaches SUCCESS as its default branch is a gate that opens when
    confused.
    """
    # The reducer takes a permission rather than a boolean for the same
    # reason the guard does: a durable SUCCESS decision recorded on a stale
    # reading would be a durable record of something that was not true.
    permission = auth_policy.require(permission)
    reasons = []
    if bundle.get("schema") != SCHEMA_NAME:
        reasons.append(f"unknown bundle schema {bundle.get('schema')!r}")
    if bundle.get("head_sha") != current_head_sha:
        reasons.append("bundle is bound to a head that is no longer current")
    if not permission.permits_action:
        reasons.append(
            f"authorization permission is {permission.state} "
            f"(age={permission.age_seconds}s)")
    if bundle.get("auth_generation") != auth_generation:
        reasons.append(
            f"bundle was built under auth generation "
            f"{bundle.get('auth_generation')}, current is {auth_generation}")

    providers = {p["provider"] for p in bundle.get("providers", [])}
    missing = [p for p in REQUIRED_PROVIDERS if p not in providers]
    if missing:
        reasons.append(f"no evidence from {missing}")
    unqualified = [p["provider"] for p in bundle.get("providers", [])
                   if not p["qualified"]]
    if unqualified:
        reasons.append(f"not qualified: {sorted(set(unqualified))}")
    wrong_head = [p["provider"] for p in bundle.get("providers", [])
                  if p["requested_for_head"] != current_head_sha]
    if wrong_head:
        reasons.append(f"evidence requested for another head: "
                       f"{sorted(set(wrong_head))}")
    duplicates = [p for p in REQUIRED_PROVIDERS
                  if len([x for x in bundle.get("providers", [])
                          if x["provider"] == p]) > 1]
    if duplicates:
        reasons.append(f"ambiguous: several generations present for "
                       f"{duplicates}; the applicable one is not determined")

    verdict = SUCCESS if not reasons else NOT_ESTABLISHED
    return {
        "verdict": verdict,
        "refusals": reasons,
        "bundle_hash": bundle.get("bundle_hash"),
        "head_sha": bundle.get("head_sha"),
        "evaluated_at": utcnow(),
        "note": "SUCCESS requires every condition; NOT_ESTABLISHED is the "
                "default and is reached by any single failure",
    }
