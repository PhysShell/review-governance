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

DEFAULT_MAX_AGE = {"runtime": 120, "reconciliation": 120, "watchdog": 120}


class HealthObservation:
    __slots__ = ("source", "state", "observed_at", "age_seconds", "bound",
                 "evaluated_at", "cause")

    def __init__(self, source, state, evaluated_at, observed_at=None,
                 age_seconds=None, bound=None, cause=None):
        self.source, self.state, self.evaluated_at = source, state, evaluated_at
        self.observed_at, self.age_seconds = observed_at, age_seconds
        self.bound, self.cause = bound, cause

    @property
    def fresh(self):
        return self.state == FRESH

    def as_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}


def _parse(value):
    return datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=datetime.timezone.utc)


def observe(source, path, *, now=None, max_age=None, field="last_complete_pass_at"):
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
    state = FRESH if age <= bound else STALE
    return HealthObservation(source, state, at, observed_at, round(age), bound,
                             None if state == FRESH else f"{round(age)}s > {bound}s")


def evaluate(sources, *, now=None, required=REQUIRED):
    """`sources` maps a required name to a path. Anything missing from the
    map is ABSENT, not optional."""
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
            observations[name] = observe(name, path, now=now)
    unexpected = sorted(set(sources) - set(required))
    return {
        "observations": {k: v.as_dict() for k, v in observations.items()},
        "all_fresh": all(o.fresh for o in observations.values()),
        "not_fresh": sorted(k for k, o in observations.items() if not o.fresh),
        "unexpected_sources": unexpected,
        "required": list(required),
        "note": "an absent or unreadable signal is not a healthy one",
    }
