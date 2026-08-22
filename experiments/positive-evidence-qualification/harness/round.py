#!/usr/bin/env python3
"""Runs one A3a evidence round.

    freeze     record repo/PR/base/head/epoch/auth before anything is triggered
    trigger    post both provider requests on the app-mediated user carrier
    inventory  capture the full artifact inventory
    qualify    build the immutable bundle and evaluate it
    settle     wait the preregistered interval, re-capture, compare
    publish    publish the shadow check — failure, always

Nothing here can publish success: `gh.ALLOWED_CONCLUSIONS` excludes it.
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import gh
import qualify

SETTLING_SECONDS = 120
STATE_DEFAULT = ".captures/a3a/round.json"


def slim_comment(comment: dict) -> dict:
    via = comment.get("performed_via_github_app")
    return {
        "id": comment["id"], "created_at": comment["created_at"],
        "updated_at": comment.get("updated_at"), "body": comment.get("body"),
        "user": {"login": comment["user"]["login"], "id": comment["user"]["id"],
                 "type": comment["user"].get("type")},
        "performed_via_github_app": ({"id": via.get("id"), "slug": via.get("slug")}
                                     if isinstance(via, dict) else None),
    }


def load_state(path: str) -> dict:
    return json.loads(Path(path).read_text())


def save_state(path: str, state: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(state, indent=2) + "\n")


def capture_inventory(repo: gh.Repo, pr: int) -> dict:
    return {
        "captured_at": gh.utcnow(),
        "issue_comments": [slim_comment(c) for c in repo.issue_comments(pr)],
        "reviews": [{"id": r["id"], "state": r["state"],
                     "submitted_at": r.get("submitted_at"),
                     "commit_id": r.get("commit_id"), "body": r.get("body"),
                     "user": {"login": r["user"]["login"], "id": r["user"]["id"],
                              "type": r["user"].get("type")}}
                    for r in repo.reviews(pr)],
        "review_comments": [{"id": c["id"], "created_at": c["created_at"],
                             "path": c.get("path"), "commit_id": c.get("commit_id"),
                             "body": c.get("body"),
                             "user": {"login": c["user"]["login"],
                                      "id": c["user"]["id"],
                                      "type": c["user"].get("type")}}
                            for c in repo.review_comments(pr)],
    }


def observe(state: dict, inventory: dict) -> dict:
    head = state["head_sha"]
    others = [state["base_sha"]] + [c for c in state.get("pr_commit_shas", [])
                                    if c != head]
    return {
        "codex": qualify.qualify_codex(state["requests"]["codex"]["comment"],
                                       inventory, head, others),
        "coderabbit": qualify.qualify_coderabbit(
            state["requests"]["coderabbit"]["comment"], inventory, head,
            state["base_sha"]),
    }


def cmd_freeze(args) -> dict:
    repo = gh.Repo(args.repo)
    me = repo.whoami()
    assert me["id"] == gh.EXPECTED_USER["id"], me
    pull = repo.pull_request(args.pr)
    commits = repo.commits(args.pr)
    head_sha = pull["head"]["sha"]
    creds = gh.user_credentials()
    epoch_id = "epoch-" + hashlib.sha256(
        f"{pull['base']['repo']['id']}:{args.pr}:{head_sha}".encode()
    ).hexdigest()[:16]
    state = {
        "frozen_at": gh.utcnow(),
        "repo": args.repo,
        "repo_id": pull["base"]["repo"]["id"],
        "pr_number": args.pr,
        "base_sha": pull["base"]["sha"],
        "head_sha": head_sha,
        "pr_commit_shas": [c["sha"] for c in commits],
        "draft": pull["draft"],
        "epoch": {"epoch_id": epoch_id, "generation": 1},
        "auth": {"generation": creds["generation"], "label": creds["label"],
                 "state": "AUTHORIZED", "carrier": "app_mediated_user"},
        "decision_rule_revision": qualify.DECISION_RULE_REVISION,
        "requests": {},
    }
    save_state(args.state, state)
    return {k: state[k] for k in ("repo", "pr_number", "base_sha", "head_sha",
                                  "epoch", "auth", "decision_rule_revision")}


def cmd_trigger(args) -> dict:
    state = load_state(args.state)
    repo = gh.Repo(state["repo"])
    pull = repo.pull_request(state["pr_number"])
    if pull["head"]["sha"] != state["head_sha"]:
        raise SystemExit("head moved before triggering; round is STALE")

    bodies = {"codex": "@codex review",
              "coderabbit": "@coderabbitai full review"}
    generation = args.generation
    for provider, text in bodies.items():
        if provider in state["requests"] and provider != args.only:
            continue
        if args.only and provider != args.only:
            continue
        comment = repo.comment_as_user(state["pr_number"], text)
        carrier = qualify.carrier_of(slim_comment(comment))
        if carrier != "app_mediated_user":
            raise SystemExit(f"request carrier is {carrier}, not app-mediated")
        state["requests"][provider] = {
            "provider_request_id": f"{state['epoch']['epoch_id']}:{provider}:g{generation}",
            "request_generation": generation,
            "epoch_id": state["epoch"]["epoch_id"],
            "auth_generation": state["auth"]["generation"],
            "head_at_request": state["head_sha"],
            "comment": slim_comment(comment),
        }
    save_state(args.state, state)
    return {p: {"comment_id": r["comment"]["id"],
                "created_at": r["comment"]["created_at"],
                "carrier": qualify.carrier_of(r["comment"]),
                "generation": r["request_generation"]}
            for p, r in state["requests"].items()}


def cmd_inventory(args) -> dict:
    state = load_state(args.state)
    repo = gh.Repo(state["repo"])
    pull = repo.pull_request(state["pr_number"])
    inventory = capture_inventory(repo, state["pr_number"])
    inventory["head_at_capture"] = pull["head"]["sha"]
    state["inventory"] = inventory
    state["observations"] = observe(state, inventory)
    save_state(args.state, state)
    return {"head_at_capture": inventory["head_at_capture"],
            "counts": {k: len(inventory[k]) for k in
                       ("issue_comments", "reviews", "review_comments")},
            "observations": {p: {"qualified": o["qualified"], "state": o["state"],
                                 "reasons": o["reasons"]}
                             for p, o in state["observations"].items()}}


def cmd_qualify(args) -> dict:
    state = load_state(args.state)
    repo = gh.Repo(state["repo"])
    pull = repo.pull_request(state["pr_number"])
    bundle = qualify.build_bundle(
        state["epoch"], state["head_sha"], state["auth"]["generation"],
        {p: {k: v for k, v in r.items() if k != "comment"}
         for p, r in state["requests"].items()},
        state["observations"], state["inventory"]["captured_at"])
    decision = qualify.evaluate(bundle, pull["head"]["sha"],
                                state["auth"]["state"])
    state["bundle"] = bundle
    state["decision"] = decision
    save_state(args.state, state)
    return {"evidence_hash": bundle["evidence_hash"], "decision": decision}


def cmd_settle(args) -> dict:
    state = load_state(args.state)
    wait = args.seconds if args.seconds is not None else SETTLING_SECONDS
    time.sleep(wait)
    repo = gh.Repo(state["repo"])
    pull = repo.pull_request(state["pr_number"])
    inventory = capture_inventory(repo, state["pr_number"])
    fresh = observe(state, inventory)
    mutation = qualify.detect_mutation(state["bundle"], fresh)
    decision = qualify.evaluate(state["bundle"], pull["head"]["sha"],
                                state["auth"]["state"])
    state["settling"] = {
        "waited_seconds": wait, "settled_at": gh.utcnow(),
        "head_after_settling": pull["head"]["sha"],
        "snapshot_stable": mutation["stable"], "changes": mutation["changes"],
        "fresh_observations": {p: {"qualified": o["qualified"], "state": o["state"],
                                   "reasons": o["reasons"]}
                               for p, o in fresh.items()},
        "decision_after_settling": decision,
    }
    save_state(args.state, state)
    return state["settling"]


def cmd_publish(args) -> dict:
    state = load_state(args.state)
    repo = gh.Repo(state["repo"])
    decision = state["settling"]["decision_after_settling"]
    bundle = state["bundle"]
    summary = "\n".join([
        f"Governor verdict: {decision['verdict']}",
        f"Epoch: {bundle['epoch_id']}",
        f"Head: {bundle['head_sha']}",
        f"Authorization: {state['auth']['state']} (generation "
        f"{state['auth']['generation']}, carrier app_mediated_user)",
        f"Codex: {state['observations']['codex']['state']}",
        f"CodeRabbit: {state['observations']['coderabbit']['state']}",
        f"Decision rule: {bundle['decision_rule_revision']}",
        f"Evidence bundle: {bundle['bundle_version']} "
        f"{bundle['evidence_hash'][:16]}",
        f"Snapshot stable after settling: "
        f"{state['settling']['snapshot_stable']}",
        "",
        "Positive evidence qualified experimentally. Publication of success "
        "is intentionally disabled in A3a: this check is published as "
        "failure regardless of the internal verdict.",
        "",
        "Provider states above are ADVISORY observations of what each "
        "provider said. They are not provider certificates, and neither is "
        "ever recorded as CLEAN.",
    ])
    run = repo.create_check(bundle["head_sha"], bundle["epoch_id"],
                            {"title": f"Governor: {decision['verdict']} "
                                      f"(publication disabled in A3a)",
                             "summary": summary})
    status, body = repo.conclude_check(
        run["id"], "failure",
        {"title": f"Governor: {decision['verdict']} (publication disabled in A3a)",
         "summary": summary})
    app = (body.get("app") or {})
    state["shadow_check"] = {
        "check_run_id": body.get("id"), "head_sha": body.get("head_sha"),
        "conclusion": body.get("conclusion"), "status": body.get("status"),
        "app": {"id": app.get("id"), "slug": app.get("slug")},
        "external_id": body.get("external_id"),
        "internal_verdict": decision["verdict"],
        "published_conclusion_is_failure_by_design": True,
    }
    save_state(args.state, state)
    return {"http_status": status, **state["shadow_check"]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["freeze", "trigger", "inventory",
                                        "qualify", "settle", "publish"])
    ap.add_argument("--repo", default="PhysShell/evm-from-scratch")
    ap.add_argument("--pr", type=int)
    ap.add_argument("--state", default=STATE_DEFAULT)
    ap.add_argument("--generation", type=int, default=1)
    ap.add_argument("--only", default=None)
    ap.add_argument("--seconds", type=int, default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    result = {"freeze": cmd_freeze, "trigger": cmd_trigger,
              "inventory": cmd_inventory, "qualify": cmd_qualify,
              "settle": cmd_settle, "publish": cmd_publish}[args.command](args)
    rendered = json.dumps(result, indent=2, default=str)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(rendered + "\n")
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
