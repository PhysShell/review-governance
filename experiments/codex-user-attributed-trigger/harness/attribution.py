#!/usr/bin/env python3
"""The A1b parser under test: how is a comment attributed, and is it an
App-mediated user-attributed trigger?

Three carriers must stay distinguishable, because the whole experiment
rests on telling them apart:

    app_installation_bot  author is the App bot itself      (A1 path)
    app_mediated_user     author is the user, performed via the Governor App
                          (the A1b hypothesis — user access token)
    plain_user            author is the user, no App mediation observed
                          (ordinary OAuth/gh path)

Identity match requires login AND numeric id AND type — never command text,
never login alone. Anything unrecognized fails closed.
"""
import re

CODEX_TRIGGER = re.compile(r"(^|\s)@codex\s+review\b", re.IGNORECASE)


def _via_slug(comment: dict):
    via = comment.get("performed_via_github_app")
    if isinstance(via, dict):
        return via.get("slug")
    return via  # None, or an already-flattened slug string


def classify(comment: dict, expected_user: dict, governor_app_slug: str) -> dict:
    user = comment.get("user") or {}
    body = comment.get("body") or ""
    via = _via_slug(comment)

    user_identity_match = (
        user.get("login") == expected_user["login"]
        and user.get("id") == expected_user["id"]
        and user.get("type") == "User"
    )
    is_governor_bot = (
        user.get("type") == "Bot"
        and user.get("login") == expected_user.get("governor_bot_login")
        and user.get("id") == expected_user.get("governor_bot_id")
    )
    mediated_by_governor = via == governor_app_slug

    if is_governor_bot and mediated_by_governor:
        authorship = "app_installation_bot"
    elif user_identity_match and mediated_by_governor:
        authorship = "app_mediated_user"
    elif user_identity_match and via is None:
        authorship = "plain_user"
    else:
        authorship = "other"

    return {
        "trigger_for_codex": bool(CODEX_TRIGGER.search(body)),
        "user_identity_match": user_identity_match,
        "app_mediation_observed": via is not None,
        "app_mediation_matches_governor": mediated_by_governor,
        "authorship_class": authorship,
        "is_user_attributed_app_mediated_trigger": (
            bool(CODEX_TRIGGER.search(body)) and authorship == "app_mediated_user"),
    }


def extract(comment_like: dict) -> dict:
    """Accepts a request envelope or a bare GitHub comment object."""
    return comment_like.get("request_comment", comment_like)
