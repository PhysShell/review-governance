"""ACCEPT-CANDIDATE: the explicit transition that permits a provider round.

Implemented here, executed nowhere in A6a.

An acceptance is a statement about a **commit**, not about a pull request.
That distinction is the whole design: if the head moves between acceptance
and trigger, the acceptance is not re-pointed at the new head — it is
invalidated, because the thing it was about no longer exists. Re-pointing
would let evidence gathered for one commit authorise work on another,
which is the shape of every carrier defect this programme has found.

Nothing here can start a provider round. It produces a record that a
trigger path would require; the trigger path is `lineage.py`, and neither
fires in this stage.
"""
import datetime
import json

import auth_policy

ACCEPTED = "ACCEPTED"
REFUSED = "REFUSED"
INVALIDATED = "INVALIDATED"


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def preconditions(*, repo, pr_number, head_sha, draft, base_current,
                  ruleset_verified, carrier, permission, open_generations):
    """Every condition named, every failure named separately.

    Reported as a list rather than a boolean because "not eligible" is not
    an operator-actionable sentence, and because a single flag invites the
    habit of checking the wrong one.
    """
    failures = []
    if len(head_sha or "") != 40:
        failures.append("head is not a full SHA")
    if draft:
        failures.append("PR is a draft")
    if not base_current:
        failures.append("PR is not current with its intended base")
    if not ruleset_verified:
        failures.append("production ruleset is not verified active")
    if (carrier or {}).get("state") != "CONFIRMED":
        failures.append(
            f"no CONFIRMED failure carrier on this head "
            f"(state={(carrier or {}).get('state')})")
    elif carrier.get("head_sha") != head_sha:
        failures.append("the carrier is bound to a different head")
    permission = auth_policy.require(permission)
    if not permission.permits_action:
        failures.append(
            f"authorization permission is {permission.state} "
            f"(age={permission.age_seconds}s): {permission.cause}")
    incompatible = [g for g in (open_generations or [])
                    if g.get("head_sha") != head_sha]
    if incompatible:
        failures.append(
            f"{len(incompatible)} open generation(s) on other heads: "
            f"{[g.get('head_sha', '')[:12] for g in incompatible]}")
    return failures


def accept(*, repo, pr_number, head_sha, **checks):
    failures = preconditions(repo=repo, pr_number=pr_number,
                             head_sha=head_sha, **checks)
    if failures:
        return {"state": REFUSED, "repo": repo, "pr_number": pr_number,
                "head_sha": head_sha, "failures": failures,
                "at": utcnow(),
                "note": "a refusal is never repaired by relaxing a condition "
                        "in the same run"}
    permission = checks["permission"]
    return {"state": ACCEPTED, "repo": repo, "pr_number": pr_number,
            "head_sha": head_sha, "accepted_at": utcnow(),
            "authorization": permission.as_dict(),
            "provider_round": "NOT_STARTED",
            "note": "acceptance authorises a provider round for THIS commit "
                    "only"}


def still_valid(acceptance, *, current_head_sha):
    """An acceptance survives only while its commit is still the head.

    Deliberately not `refresh()` or `rebind()`. There is no operation here
    that moves an acceptance to a new head, because there is no honest one:
    the conditions were evaluated against a commit that is no longer
    current, and re-evaluating them is just making a new acceptance.
    """
    if acceptance.get("state") != ACCEPTED:
        return {"valid": False, "reason": "not an acceptance"}
    if acceptance["head_sha"] != current_head_sha:
        return {"valid": False, "state": INVALIDATED,
                "reason": "head moved after acceptance",
                "accepted_for": acceptance["head_sha"],
                "current_head": current_head_sha,
                "required_action": "a new acceptance on the new head, not a "
                                   "re-pointing of this one"}
    return {"valid": True, "head_sha": current_head_sha}


def as_record(acceptance):
    """The durable form a trigger path would read."""
    return json.dumps({k: v for k, v in acceptance.items()
                       if k in ("state", "repo", "pr_number", "head_sha",
                                "accepted_at")}, sort_keys=True)
