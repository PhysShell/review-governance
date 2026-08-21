#!/usr/bin/env python3
"""Narrow normalization of observed provider responses into gate states.

Only response patterns actually observed in A1 are normalized; anything
else maps to UNRECOGNIZED. A "failed to start a review" response must never
read as a passing gate — there is no code path from a no-start response to
CLEAN, and UNRECOGNIZED is not CLEAN either.
"""
CODEX_ACTOR_ID = 199175422          # chatgpt-codex-connector[bot], observed live
CODEX_ACTOR_LOGIN = "chatgpt-codex-connector[bot]"
CODEX_NO_START_MARKERS = ("to use codex here",)


def normalize_codex_response(comment: dict) -> dict:
    """comment: a GitHub issue-comment object (or the `response` member of
    the codex_response fixture)."""
    user = comment.get("user", {})
    body = (comment.get("body") or "").lower()
    is_codex_actor = (user.get("id") == CODEX_ACTOR_ID
                      and user.get("login") == CODEX_ACTOR_LOGIN
                      and user.get("type") == "Bot")
    if is_codex_actor and any(m in body for m in CODEX_NO_START_MARKERS):
        return {
            "provider": "codex",
            "command_handled": True,
            "review_state": "REVIEW_UNAVAILABLE_FOR_REQUESTOR",
            "gate": "UNAVAILABLE",
        }
    return {
        "provider": "codex",
        "command_handled": None,
        "review_state": "UNRECOGNIZED",
        "gate": "UNRECOGNIZED",
    }
