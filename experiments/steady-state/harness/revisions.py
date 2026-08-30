"""Provider surfaces as revision history, not as a growing set.

A6g found this live. At 13:49:29 the CodeRabbit sticky on `#32` carried
`**Run ID**: 8dcf9c5c…`; rewrites at 13:50:25 and 13:50:31 removed it, and
the acknowledgement reaction Codex had left on our request was withdrawn
after it posted its comment. Nothing was lost from our store — the frozen
snapshot is still there — but the baseline model was wrong about what the
snapshot means.

    new_run_ids = current - baseline

is a statement about one reading. It answers "did a marker appear", and
was being used to answer "does a marker stand". Those differ whenever a
provider rewrites its own carrier, which is the normal case for both of
these providers.

So a carrier is a sequence of revisions, and the disappearance of a marker
that was once observed is a first-class outcome:

    STANDING    the marker that qualified this evidence is still there
    SUPERSEDED  the carrier moved on and now qualifies a different thing
    RETRACTED   the qualifying marker is gone and nothing replaced it
    ABSENT      the carrier itself is gone from the surface
    UNREADABLE  the surface could not be re-read, which is not agreement

A frozen snapshot remains a historical fact: at time T the surface really
did show this. It stops being a *standing* provider verdict the moment the
surface stops showing it, and the re-read before SUCCESS is what turns the
first into the second.
"""
import datetime

STANDING = "STANDING"
SUPERSEDED = "SUPERSEDED"
RETRACTED = "RETRACTED"
ABSENT = "ABSENT"
UNREADABLE = "UNREADABLE"

#: Outcomes that permit a frozen observation to be used as current evidence.
QUALIFYING = frozenset({STANDING})


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def revision_of(observed, *, observed_at=None):
    """One revision of one carrier, in the terms the comparison needs.

    Deliberately narrow. A revision is not the whole parsed observation: it
    is the identity of the carrier, the state of the markers that made it
    evidence, and when we looked.
    """
    return {
        "carrier_id": observed.get("id"),
        "updated_at": observed.get("updated_at"),
        "body_digest": observed.get("observed_digest"),
        "provider": observed.get("provider"),
        # The markers that carried causality and content, kept separately so
        # a diagnosis can say which one went away.
        "correlation_markers": sorted(
            set(observed.get("new_run_ids") or [])
            | set(str(x) for x in (observed.get("triggering_comment_ids") or []))
            | ({f"reaction:{observed['reaction_on_request_carrier']}"}
               if observed.get("reaction_on_request_carrier") is not None
               else set())),
        "head_claim": observed.get("head_claim"),
        "head_binding": observed.get("head_binding"),
        "review_ran": observed.get("review_ran"),
        "findings_count": (len(observed["findings"])
                           if isinstance(observed.get("findings"), list)
                           else None),
        "observed_at": observed_at or utcnow(),
    }


def compare(frozen, current):
    """Does the revision that qualified the evidence still stand?

    `frozen` is the revision recorded when the snapshot was taken;
    `current` is a revision built from a fresh read, or None when the
    carrier is no longer on the surface.
    """
    if current is None:
        return {"state": ABSENT, "cause": "the carrier is no longer on the "
                                          "provider surface"}
    if current.get("carrier_id") != frozen.get("carrier_id"):
        return {"state": SUPERSEDED,
                "cause": f"re-read a different carrier "
                         f"({current.get('carrier_id')})"}
    lost = sorted(set(frozen["correlation_markers"])
                  - set(current["correlation_markers"]))
    if lost:
        # The distinction that matters to an operator: did the carrier move
        # on to other evidence, or did it simply stop asserting ours?
        moved = bool(set(current["correlation_markers"])
                     - set(frozen["correlation_markers"]))
        return {"state": SUPERSEDED if moved else RETRACTED,
                "lost_markers": lost,
                "current_markers": current["correlation_markers"],
                "cause": ("the carrier was rewritten and now carries other "
                          "markers" if moved else
                          "the marker that attributed this evidence to our "
                          "request is no longer on the surface")}
    if frozen.get("head_claim") != current.get("head_claim"):
        return {"state": SUPERSEDED,
                "cause": f"the carrier now attests "
                         f"{current.get('head_claim')!r}"}
    if frozen.get("findings_count") != current.get("findings_count"):
        return {"state": SUPERSEDED,
                "cause": f"the carrier now reports "
                         f"{current.get('findings_count')} finding(s), "
                         f"not {frozen.get('findings_count')}"}
    if frozen.get("body_digest") and current.get("body_digest") \
            and frozen["body_digest"] != current["body_digest"]:
        # The body moved but every load-bearing marker survived. Recorded
        # rather than refused: providers rewrite boilerplate constantly, and
        # treating that as a retraction would make the guard unusable.
        return {"state": STANDING, "body_changed": True,
                "cause": "the body was rewritten; every qualifying marker "
                         "survived the rewrite"}
    return {"state": STANDING, "body_changed": False}


def reconfirmation(records):
    """Roll several per-provider comparisons into one answer."""
    states = {r["provider"]: r["comparison"]["state"] for r in records}
    not_standing = sorted(p for p, s in states.items() if s not in QUALIFYING)
    return {
        "all_standing": bool(records) and not not_standing,
        "not_standing": not_standing,
        "states": states,
        "results": records,
        "note": "a frozen snapshot is what the surface showed at the time; "
                "only a re-read says whether it still shows it",
    }
