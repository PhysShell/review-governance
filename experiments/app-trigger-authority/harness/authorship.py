#!/usr/bin/env python3
"""The experiment parser under test: does a comment constitute an
APP-AUTHORED provider trigger?

App authorship requires the full recorded bot identity — login AND numeric
actor id AND type == "Bot". Command text alone must never qualify: the
negative control is an identical command body authored by an ordinary user,
which must classify as is_app_authored_trigger == False.
"""
import argparse
import json
import re
from pathlib import Path

TRIGGERS = {
    "codex": re.compile(r"(^|\s)@codex\s+review\b", re.IGNORECASE),
    "coderabbit": re.compile(r"(^|\s)@coderabbitai\s+full\s+review\b", re.IGNORECASE),
}


def classify(comment_user: dict, body: str, identity: dict) -> dict:
    provider = next(
        (name for name, rx in TRIGGERS.items() if rx.search(body or "")), None)
    is_app = (
        comment_user.get("type") == "Bot"
        and comment_user.get("login") == identity["bot_login"]
        and comment_user.get("id") == identity["bot_user_id"]
    )
    return {
        "trigger_for": provider,
        "is_app_authored": is_app,
        "is_app_authored_trigger": bool(provider) and is_app,
    }


def extract(comment_like: dict):
    """Accepts either a post_trigger envelope or a bare GitHub comment object."""
    comment = comment_like.get("request_comment", comment_like)
    return comment.get("user", {}), comment.get("body", "")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--identity", default=str(
        Path(__file__).resolve().parent.parent / "app-identity.json"))
    ap.add_argument("--fixture", required=True,
                    help="envelope or bare comment json")
    args = ap.parse_args()
    identity = json.loads(Path(args.identity).read_text())
    user, body = extract(json.loads(Path(args.fixture).read_text()))
    print(json.dumps(classify(user, body, identity), indent=2))


if __name__ == "__main__":
    main()
