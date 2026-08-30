"""Runtime health as provenance-carrying observations, not booleans.

The success guard took `health=None` and iterated whatever it was given,
so an absent health set passed exactly like a healthy one and a missing
key was indistinguishable from "this source is not required". That is the
disease just cured in authorization, reappearing one module over.

So: an exact required set, each answer carrying where it came from and how
old it is, and every way of not knowing failing closed.
"""
import datetime
import json
from pathlib import Path

REQUIRED = ("runtime", "reconciliation", "watchdog")

FRESH = "FRESH"
STALE = "STALE"
UNREADABLE = "UNREADABLE"
ABSENT = "ABSENT"
#: Recent, readable, and reporting that it did not do the thing.
UNSATISFIED = "UNSATISFIED"

DEFAULT_MAX_AGE = {"runtime": 120, "reconciliation": 120, "watchdog": 120}


class HealthObservation:
    __slots__ = ("source", "state", "observed_at", "age_seconds", "bound",
                 "evaluated_at", "cause", "semantics")

    def __init__(self, source, state, evaluated_at, observed_at=None,
                 age_seconds=None, bound=None, cause=None, semantics=None):
        self.source, self.state, self.evaluated_at = source, state, evaluated_at
        self.observed_at, self.age_seconds = observed_at, age_seconds
        self.bound, self.cause = bound, cause
        self.semantics = semantics

    @property
    def fresh(self):
        return self.state == FRESH

    def as_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}


def _runtime_semantics(blob, candidate):
    if blob.get("state") != "OK":
        return [f"runtime pass state is {blob.get('state')!r}, not OK"]
    return []


def _reconciliation_semantics(blob, candidate):
    """Recent is not reconciled.

    The producer writes the comparisons themselves and the sentinel already
    distinguishes NOT_COMPARED from HEALTHY. This consumer used to read only
    `last_complete_pass_at`, so a pass that compared nothing was FRESH for
    the success guard and NOT_COMPARED for the pager — the same file
    meaning two different things to the two readers that matter.
    """
    problems = []
    if blob.get("all_compared") is not True:
        problems.append(
            f"reconciliation pass compared "
            f"{blob.get('comparisons_performed')} of "
            f"{blob.get('comparisons_attempted')} PRs; a pass that compared "
            "nothing is as recent as one that compared everything")
    if candidate is None:
        return problems
    rows = [r for r in (blob.get("per_pr") or [])
            if int(r.get("pr_number", -1)) == int(candidate["pr_number"])]
    if not rows:
        problems.append(
            f"the candidate PR #{candidate['pr_number']} does not appear in "
            "the last reconciliation pass")
        return problems
    row = rows[-1]
    if row.get("comparison_performed") is not True:
        problems.append(
            f"no scoped comparison was performed for #{candidate['pr_number']} "
            f"(scope {row.get('scope_state')})")
    if row.get("stored_head") != candidate["head_sha"]:
        problems.append(
            f"stored head {row.get('stored_head')} is not the head being "
            f"published for")
    if row.get("github_head") != candidate["head_sha"]:
        problems.append(
            f"GitHub head at reconciliation {row.get('github_head')} is not "
            f"the head being published for")
    if row.get("drift_detected") is not False:
        problems.append(
            f"drift for #{candidate['pr_number']} is "
            f"{row.get('drift_detected')!r}, not a proven absence of drift")
    return problems


def _watchdog_semantics(blob, candidate):
    if blob.get("edge_reachable") is not True:
        return ["the edge was not reachable, so this is the primary's "
                "silence rather than the watchdog's value"]
    return []


SEMANTICS = {
    "runtime": _runtime_semantics,
    "reconciliation": _reconciliation_semantics,
    "watchdog": _watchdog_semantics,
}


def _parse(value):
    return datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=datetime.timezone.utc)


def observe(source, path, *, now=None, max_age=None,
            field="last_complete_pass_at", candidate=None):
    """Recent, readable, and reporting that it did its job.

    Freshness alone answers "is a process running". What a success guard
    needs is narrower — that the specific work this signal exists to
    attest actually happened, for the head being published — so each
    source's own semantics are evaluated too.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    bound = max_age if max_age is not None else DEFAULT_MAX_AGE.get(source, 120)
    p = Path(path)
    if not p.exists():
        return HealthObservation(source, ABSENT, at, bound=bound,
                                 cause=f"no health file at {p}")
    try:
        blob = json.loads(p.read_text() or "{}")
        observed_at = blob[field]
        age = (now - _parse(observed_at)).total_seconds()
    except (ValueError, KeyError, TypeError) as exc:
        return HealthObservation(source, UNREADABLE, at, bound=bound,
                                 cause=f"{type(exc).__name__}: unusable health file")
    if age < 0:
        return HealthObservation(source, UNREADABLE, at, observed_at,
                                 round(age), bound,
                                 "health timestamp is in the future")
    if age > bound:
        return HealthObservation(source, STALE, at, observed_at, round(age),
                                 bound, f"{round(age)}s > {bound}s")
    problems = SEMANTICS.get(source, lambda b, c: [])(blob, candidate)
    if problems:
        return HealthObservation(
            source, UNSATISFIED, at, observed_at, round(age), bound,
            "; ".join(problems), semantics=problems)
    return HealthObservation(source, FRESH, at, observed_at, round(age), bound,
                             semantics=[])


def evaluate(sources, *, now=None, required=REQUIRED, candidate=None):
    """`sources` maps a required name to a path. Anything missing from the
    map is ABSENT, not optional.

    `candidate` is `{repo, pr_number, head_sha}` — the thing about to be
    published. Passing it is what turns "reconciliation ran" into
    "reconciliation compared this PR at this head and found no drift".
    Omitting it evaluates the source-wide semantics only, which is right
    for a dashboard and not enough for a success.
    """
    observations = {}
    for name in required:
        path = sources.get(name)
        if path is None:
            observations[name] = HealthObservation(
                name, ABSENT,
                (now or datetime.datetime.now(datetime.timezone.utc))
                .strftime("%Y-%m-%dT%H:%M:%SZ"),
                cause="no source configured for a required health signal")
        else:
            observations[name] = observe(name, path, now=now,
                                         candidate=candidate)
    unexpected = sorted(set(sources) - set(required))
    return {
        "observations": {k: v.as_dict() for k, v in observations.items()},
        "all_fresh": all(o.fresh for o in observations.values()),
        "not_fresh": sorted(k for k, o in observations.items() if not o.fresh),
        "candidate": candidate,
        "candidate_bound": candidate is not None,
        "unexpected_sources": unexpected,
        "required": list(required),
        "note": "an absent, unreadable, stale or unsatisfied signal is not a "
                "healthy one",
    }
