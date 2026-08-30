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


class GateError(Exception):
    """Raised where an acceptance would rest on an unevaluated gate."""


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


REQUIRED_OBSERVATION_FIELDS = (
    "observation_id", "repo", "pr_number", "head_sha", "draft", "base_ref",
    "ruleset_id", "ruleset_verified", "carrier_state", "carrier_head_sha",
    "carrier_run_id", "observed_at")


def evaluate_observation(observation, permission, *, open_generations=()):
    """Run the gate over a stored observation row.

    Returns the failure list. Callers do not get to construct this; the
    durable writer calls it, and the row it reads is one the driver wrote
    from an actual GitHub read.
    """
    missing = [f for f in REQUIRED_OBSERVATION_FIELDS if f not in observation]
    if missing:
        raise GateError(
            f"observation row is incomplete: {missing}; a partial reading "
            "cannot be gated")
    permission = auth_policy.require(permission)
    return accept.preconditions(
        repo=observation["repo"], pr_number=observation["pr_number"],
        head_sha=observation["head_sha"], draft=bool(observation["draft"]),
        base_current=observation["base_ref"] == "main",
        ruleset_verified=bool(observation["ruleset_verified"]),
        carrier={"state": observation["carrier_state"],
                 "head_sha": observation["carrier_head_sha"],
                 "check_run_id": observation["carrier_run_id"]},
        permission=permission, open_generations=list(open_generations))


def require_scope(observation, *, repo, pr_number, head_sha):
    """The stored observation must be about the row being written.

    Checked separately from the gate itself because an observation of
    another PR can pass every precondition and still be the wrong evidence.
    """
    mismatches = []
    if observation["repo"] != repo:
        mismatches.append("repo")
    if int(observation["pr_number"]) != int(pr_number):
        mismatches.append("pr_number")
    if observation["head_sha"] != head_sha:
        mismatches.append("head_sha")
    if observation["carrier_head_sha"] != head_sha:
        mismatches.append("carrier_head_sha")
    if mismatches:
        raise GateError(
            f"the stored observation is not about this acceptance: "
            f"{mismatches}")
    return observation


def preview(*, repo, pr_number, head_sha, draft, base_ref, ruleset_id,
            ruleset_verified, carrier, permission, open_generations=()):
    """What the gate would say, for reporting only.

    Deliberately returns a plain list and is named so that nothing reads as
    an authorisation. The durable path never consults this.
    """
    return accept.preconditions(
        repo=repo, pr_number=pr_number, head_sha=head_sha, draft=draft,
        base_current=base_ref == "main", ruleset_verified=ruleset_verified,
        carrier=carrier, permission=auth_policy.require(permission),
        open_generations=list(open_generations))
