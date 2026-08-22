#!/usr/bin/env python3
"""Normalization of observed CodeRabbit responses for A1b-R.

The experiment measures **handling**, not review quality, so several
distinct outcomes all count as handling: an acknowledgement, a rate-limit
notice, an explicit refusal, or a review object. None of them ever yields
`CLEAN` — CodeRabbit's summary/sticky surface stayed disqualified as an
authoritative carrier after A1 showed it is mutable and not append-only.
"""
CODERABBIT_ACTOR_ID = 136622811
CODERABBIT_ACTOR_LOGIN = "coderabbitai[bot]"

ACK_MARKERS = ("full review triggered", "review command invocation",
               "action performed")
RATE_LIMIT_MARKERS = ("rate limit", "rate-limit", "quota")
REFUSAL_MARKERS = ("cannot", "can't", "not allowed", "unable to")


def _is_coderabbit(user: dict) -> bool:
    return (user.get("id") == CODERABBIT_ACTOR_ID
            and user.get("login") == CODERABBIT_ACTOR_LOGIN
            and user.get("type") == "Bot")


def normalize_comment(comment: dict) -> dict:
    """Classify one CodeRabbit comment as evidence of command handling."""
    if not _is_coderabbit(comment.get("user", {})):
        return {"provider": "coderabbit", "command_handled": None,
                "handling_kind": "NOT_PROVIDER_AUTHORED", "gate": "UNRECOGNIZED"}
    body = (comment.get("body") or "").lower()
    if any(m in body for m in RATE_LIMIT_MARKERS):
        kind = "RATE_LIMITED"
    elif any(m in body for m in ACK_MARKERS):
        kind = "ACKNOWLEDGED"
    elif any(m in body for m in REFUSAL_MARKERS):
        kind = "REFUSED"
    else:
        return {"provider": "coderabbit", "command_handled": None,
                "handling_kind": "UNRECOGNIZED_PROVIDER_TEXT",
                "gate": "UNRECOGNIZED"}
    return {"provider": "coderabbit", "command_handled": True,
            "handling_kind": kind, "gate": "ADVISORY_ONLY"}


def evaluate_review_object(review: dict, frozen_head: str) -> dict:
    """A pull_request_review is the only carrier with a real commit binding."""
    if not _is_coderabbit(review.get("user", {})):
        return {"is_coderabbit_review": False, "gate": "UNRECOGNIZED"}
    binds = bool(frozen_head) and review.get("commit_id") == frozen_head
    return {
        "is_coderabbit_review": True,
        "review_id": review.get("id"),
        "commit_id": review.get("commit_id"),
        "binds_frozen_head": binds,
        "command_handled": True,
        "gate": "REVIEW_OBSERVED" if binds else "STALE",
    }
