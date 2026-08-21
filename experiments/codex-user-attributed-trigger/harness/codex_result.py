#!/usr/bin/env python3
"""Normalization of observed Codex outcomes for A1b.

Two rules carry the weight:
  * a no-start/refusal response is `UNAVAILABLE`, never `CLEAN`;
  * a terminal review counts as evidence for the experiment's frozen HEAD
    only if it actually binds that HEAD — otherwise `STALE`.
"""
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
