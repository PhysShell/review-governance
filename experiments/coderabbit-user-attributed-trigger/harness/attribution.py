#!/usr/bin/env python3
"""The A1b-R parser under test: which carrier authored a CodeRabbit command?

Carried over from A1b (frozen there) with the CodeRabbit trigger pattern.
The three carriers must stay distinguishable — the whole experiment is
about the third one:

    app_installation_bot  author is the App bot itself        (A1 carrier)
    app_mediated_user     author is the user, performed via the Governor App
                          (A1b carrier — the one under test here)
    plain_user            author is the user, no App mediation observed
                          (the carrier CodeRabbit already accepts)

Identity match requires login AND numeric id AND type. Command text alone
never qualifies; anything unrecognized fails closed.
"""
import re

CODERABBIT_TRIGGER = re.compile(r"(^|\s)@coderabbitai\s+full\s+review\b",
                                re.IGNORECASE)


def _via_slug(comment: dict):
    via = comment.get("performed_via_github_app")
    if isinstance(via, dict):
        return via.get("slug")
    return via


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

    is_trigger = bool(CODERABBIT_TRIGGER.search(body))
    return {
        "trigger_for_coderabbit": is_trigger,
        "user_identity_match": user_identity_match,
        "app_mediation_observed": via is not None,
        "app_mediation_matches_governor": mediated_by_governor,
        "authorship_class": authorship,
        "is_user_attributed_app_mediated_trigger": (
            is_trigger and authorship == "app_mediated_user"),
    }


def extract(comment_like: dict) -> dict:
    return comment_like.get("request_comment", comment_like)
