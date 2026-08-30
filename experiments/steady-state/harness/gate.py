"""The ACCEPT gate as a result that carries its own evaluation.

`preconditions=[]` was a capability. A caller with a fresh permission could
hand the durable store an empty list and get an ACCEPTED row without the
gate ever running — the store checked that the list was empty, which is
not the same as checking that anything evaluated it.

So the gate returns an object that cannot be fabricated by supplying
something list-shaped: it names the exact repo, PR, head, carrier run,
ruleset and authorization observation it was computed against, and the
store re-checks that those match the row it is about to write.

    A prerequisite is not established merely because the caller supplies
    an object shaped like its result.
"""
import datetime

import accept
import auth_policy


class GateError(Exception):
    """Raised where an acceptance would rest on an unevaluated gate."""


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class GateEvaluation:
    """Only `gate.evaluate` produces one, and it records what it saw."""

    __slots__ = ("repo", "pr_number", "head_sha", "carrier_run_id",
                 "ruleset_id", "auth_observation_id", "auth_generation",
                 "failures", "evaluated_at")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    @property
    def passed(self):
        return self.failures == []

    def as_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}


def evaluate(*, repo, pr_number, head_sha, draft, base_ref, ruleset_id,
             ruleset_verified, carrier, permission, open_generations=()):
    """Run the full ACCEPT gate and bind the result to what it saw."""
    permission = auth_policy.require(permission)
    failures = accept.preconditions(
        repo=repo, pr_number=pr_number, head_sha=head_sha, draft=draft,
        base_current=base_ref == "main", ruleset_verified=ruleset_verified,
        carrier=carrier, permission=permission,
        open_generations=list(open_generations))
    return GateEvaluation(
        repo=repo, pr_number=pr_number, head_sha=head_sha,
        carrier_run_id=(carrier or {}).get("check_run_id")
                       or (carrier or {}).get("run_id"),
        ruleset_id=ruleset_id,
        auth_observation_id=permission.observation_id,
        auth_generation=permission.auth_generation,
        failures=failures, evaluated_at=utcnow())


def require_matching(evaluation, *, repo, pr_number, head_sha, permission):
    """What the durable store calls before writing anything.

    A gate evaluated for another PR, another head or another authorization
    observation is not this acceptance's gate, however green it looks.
    """
    if not isinstance(evaluation, GateEvaluation):
        raise GateError(
            f"expected a GateEvaluation produced by gate.evaluate, got "
            f"{type(evaluation).__name__}: an empty list is not evidence "
            "that the gate ran")
    mismatches = []
    if evaluation.repo != repo:
        mismatches.append("repo")
    if int(evaluation.pr_number) != int(pr_number):
        mismatches.append("pr_number")
    if evaluation.head_sha != head_sha:
        mismatches.append("head_sha")
    if evaluation.auth_observation_id != permission.observation_id:
        mismatches.append("auth_observation_id")
    if evaluation.auth_generation != permission.auth_generation:
        mismatches.append("auth_generation")
    if mismatches:
        raise GateError(
            f"the gate evaluation does not match this acceptance: "
            f"{mismatches}")
    if not evaluation.passed:
        raise GateError("acceptance refused by preconditions: "
                        + "; ".join(evaluation.failures))
    return evaluation
