#!/usr/bin/env python3
"""Normalization of observed Codex outcomes for A1b.

Two rules carry the weight:
  * a no-start/refusal response is `UNAVAILABLE`, never `CLEAN`;
  * a terminal review counts as evidence for the experiment's frozen HEAD
    only if it actually binds that HEAD — otherwise `STALE`.
"""
import re

CODEX_ACTOR_ID = 199175422
CODEX_ACTOR_LOGIN = "chatgpt-codex-connector[bot]"
NO_START_MARKERS = ("to use codex here",)


def _is_codex_actor(user: dict) -> bool:
    return (user.get("id") == CODEX_ACTOR_ID
            and user.get("login") == CODEX_ACTOR_LOGIN
            and user.get("type") == "Bot")


def normalize_comment(comment: dict) -> dict:
    user = comment.get("user", {})
    body = (comment.get("body") or "").lower()
    if _is_codex_actor(user) and any(m in body for m in NO_START_MARKERS):
        return {"provider": "codex", "command_handled": True,
                "review_state": "REVIEW_UNAVAILABLE_FOR_REQUESTOR",
                "gate": "UNAVAILABLE"}
    return {"provider": "codex", "command_handled": None,
            "review_state": "UNRECOGNIZED", "gate": "UNRECOGNIZED"}


REVIEW_RESULT_MARKER = "codex review:"
# the SHA arrives wrapped in markdown: **Reviewed commit:** `a4e756b032`
ATTESTED_COMMIT_RE = re.compile(r"reviewed\s+commit:?[\s*`]*([0-9a-f]{7,40})",
                                re.IGNORECASE)


def evaluate_review_comment(comment: dict, frozen_head: str) -> dict:
    """A Codex review *result posted as an issue comment*.

    This is evidence that a review executed, and it names the commit it
    reviewed — but the carrier is a mutable comment and the binding is a
    SHA prefix in free text, not a `commit_id` field. It therefore never
    yields `CLEAN`: the gate stays advisory regardless of the wording.
    """
    user = comment.get("user", {})
    body = comment.get("body") or ""
    if not (_is_codex_actor(user) and REVIEW_RESULT_MARKER in body.lower()):
        return {"is_codex_review_result": False, "gate": "UNRECOGNIZED"}
    match = ATTESTED_COMMIT_RE.search(body)
    attested = match.group(1).lower() if match else None
    binds = bool(attested and frozen_head
                 and frozen_head.lower().startswith(attested))
    return {
        "is_codex_review_result": True,
        "carrier": "issue_comment",
        "carrier_is_authoritative": False,
        "review_state": "REVIEW_EXECUTED",
        "attested_commit": attested,
        "attested_commit_is_prefix": bool(attested) and len(attested) < 40,
        "binds_frozen_head": binds,
        "gate": "ADVISORY_ONLY",
    }


def evaluate_terminal_review(review: dict, frozen_head: str) -> dict:
    """A review object is terminal evidence only when it binds the frozen HEAD."""
    if not _is_codex_actor(review.get("user", {})):
        return {"is_codex_review": False, "binds_frozen_head": False,
                "gate": "UNRECOGNIZED"}
    binds = bool(frozen_head) and review.get("commit_id") == frozen_head
    return {
        "is_codex_review": True,
        "review_id": review.get("id"),
        "commit_id": review.get("commit_id"),
        "binds_frozen_head": binds,
        "gate": "REVIEW_OBSERVED" if binds else "STALE",
    }
