"""The ACCEPT gate, run by the writer over a durable observation.

Three versions of this defect now:

    preconditions=[]        an empty list was accepted as proof of a gate
    GateEvaluation(...)     the class had a public constructor, so the
                            caller could build a passing result by hand
                            and `require_matching` — which compared only
                            repo, PR, head and authorization — took it

The lesson the second version taught is that no object can be the boundary.
A caller that can construct the evidence type can construct the evidence,
and a stricter type just moves the forgery one constructor along.

So there is no caller-supplied result any more. The durable writer is
handed an **observation id**, loads the row the driver wrote when it read
GitHub, and runs the gate itself over those stored fields. What the caller
supplies is a pointer to something already recorded, not a conclusion; the
carrier run and ruleset that the previous version carried but never
re-checked are now inputs the writer reads for itself.
"""
import datetime

import accept
import auth_policy
import observation as observation_mod


class GateError(Exception):
    """Raised where an acceptance would rest on an unevaluated gate."""


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


REQUIRED_FACTS = (
    "head_sha", "draft", "pr_state", "base_ref", "base_sha", "merge_base_sha",
    "ruleset_id", "ruleset_enforcement", "ruleset_visible_hash",
    "ruleset_bypass", "carrier_count", "carrier_run_id", "carrier_status",
    "carrier_conclusion", "carrier_external_id", "carrier_head_sha")


def evaluate_observation(observation, permission, *, epoch_id=None,
                         open_generations=()):
    """Run the gate over a stored reading, deriving every predicate.

    A6f-c4 removed the last two booleans a caller could set. The row no
    longer carries `ruleset_verified` or a carrier `state`; it carries the
    readbacks, and the answers are computed here from them — so a reader
    holding the row can re-derive the verdict instead of trusting a column.
    """
    facts = observation.get("facts")
    if not isinstance(facts, dict):
        raise GateError(
            "observation row carries no readback facts; a partial reading "
            "cannot be gated")
    missing = [f for f in REQUIRED_FACTS if f not in facts]
    if missing:
        raise GateError(
            f"observation is incomplete: {missing}; a partial reading cannot "
            "be gated")
    permission = auth_policy.require(permission)

    failures = accept.preconditions(
        repo=observation["repo"], pr_number=observation["pr_number"],
        head_sha=facts["head_sha"], draft=bool(facts["draft"]),
        # Derived from an ancestry read, not from the branch's name.
        base_current=not observation_mod.base_findings(facts),
        # Derived from the ruleset object itself.
        ruleset_verified=not observation_mod.ruleset_findings(facts),
        # Derived from the check runs on that exact head.
        carrier={"state": ("CONFIRMED"
                           if not observation_mod.carrier_findings(
                               facts, epoch_id=epoch_id) else "REFUSED"),
                 "head_sha": facts.get("carrier_head_sha"),
                 "check_run_id": facts.get("carrier_run_id")},
        permission=permission, open_generations=list(open_generations))

    # The derivations again, named, so a refusal says which reading caused
    # it rather than only that a precondition failed.
    for detail in observation_mod.base_findings(facts):
        failures.append(f"base: {detail}")
    for detail in observation_mod.ruleset_findings(facts):
        failures.append(f"ruleset: {detail}")
    for detail in observation_mod.carrier_findings(facts, epoch_id=epoch_id):
        failures.append(f"carrier: {detail}")
    if facts.get("pr_state") != "open":
        failures.append(f"PR state is {facts.get('pr_state')!r}")
    return failures


def require_scope(observation, *, repo, pr_number, head_sha):
    """The stored observation must be about the row being written.

    Checked separately from the gate itself because an observation of
    another PR can pass every precondition and still be the wrong evidence.
    """
    facts = observation.get("facts") or {}
    mismatches = []
    if observation["repo"] != repo:
        mismatches.append("repo")
    if int(observation["pr_number"]) != int(pr_number):
        mismatches.append("pr_number")
    if observation["head_sha"] != head_sha:
        mismatches.append("head_sha")
    if facts.get("head_sha") != head_sha:
        mismatches.append("facts.head_sha")
    # The carrier is a fact *within* the reading, not part of its scope.
    # Checking it here made "no applicable carrier" report as "wrong
    # observation", which is a true statement about the wrong thing.
    if mismatches:
        raise GateError(
            f"the stored observation is not about this acceptance: "
            f"{mismatches}")
    return observation
