"""Adversarial matrix for A3a qualification.

Every row of the preregistered matrix, plus the one that matters most:
absence of findings is not positive evidence. Inventories are built from
the shapes actually observed on GitHub in this program.
"""
import copy
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "harness"))

import qualify  # noqa: E402

HEAD_H = "a3274d7e7222c3ee9a63c70379a0a06ac5208ba6"
BASE_SHA = "047ff1a641e33e0bb8c6b9eea26bb80eea021e08"
OTHER = [BASE_SHA]
GOVERNOR = {"id": 4669438, "slug": "physshell-review-governor"}
USER = {"login": "PhysShell", "id": 45852143, "type": "User"}
CODEX_BOT = {"login": "chatgpt-codex-connector[bot]", "id": 199175422,
             "type": "Bot"}
RABBIT_BOT = {"login": "coderabbitai[bot]", "id": 136622811, "type": "Bot"}

T0 = "2026-08-22T05:32:00Z"
T1 = "2026-08-22T05:32:10Z"
T2 = "2026-08-22T05:33:20Z"
T3 = "2026-08-22T05:34:00Z"


def comment(cid, user, body, created, updated=None, via=GOVERNOR):
    return {"id": cid, "created_at": created, "updated_at": updated or created,
            "body": body, "user": user, "performed_via_github_app": via}


def request_comment(cid=1, user=USER, via=GOVERNOR, created=T1):
    return comment(cid, user, "@codex review", created, via=via)


def codex_terminal(prefix="a3274d7e72", created=T2, cid=10,
                   body=None, updated=None):
    text = body or (f"Codex Review: Didn't find any major issues. Bravo.\n\n"
                    f"**Reviewed commit:** `{prefix}`")
    return comment(cid, CODEX_BOT, text, created, updated,
                   via={"id": 1, "slug": "chatgpt-codex-connector"})


def rabbit_sticky(created="2026-08-22T05:31:00Z", updated=T3, cid=20,
                  head=HEAD_H, body=None):
    text = body or ("No actionable comments were generated in the recent review."
                    f"\n\nReviewing files that changed from the base of the PR "
                    f"and between {BASE_SHA} and {head}.")
    return comment(cid, RABBIT_BOT, text, created, updated,
                   via={"id": 2, "slug": "coderabbitai"})


def rabbit_ack(created=T2, cid=22):
    """CodeRabbit's observed positive path: a fresh acknowledgement comment
    for the command, then an edit of the sticky summary."""
    return comment(cid, RABBIT_BOT,
                   "<!-- CodeRabbit review command invocation: 09d22001 -->\n"
                   "Action performed: Full review triggered.", created,
                   via={"id": 2, "slug": "coderabbitai"})


def rabbit_rate_limited(created=T2, cid=21):
    return comment(cid, RABBIT_BOT,
                   "Review rate limited. Your next included review will be "
                   "available in 30 minutes.", created,
                   via={"id": 2, "slug": "coderabbitai"})


def inventory(issue_comments, reviews=(), review_comments=()):
    return {"captured_at": "2026-08-22T05:40:00Z",
            "issue_comments": list(issue_comments),
            "reviews": list(reviews),
            "review_comments": list(review_comments)}


def codex_review_finding(created=T2):
    return {"id": 900, "state": "COMMENTED", "submitted_at": created,
            "commit_id": HEAD_H, "body": "Found a problem", "user": CODEX_BOT}


def rabbit_inline_finding(created=T3):
    return {"id": 901, "created_at": created, "path": "a.py",
            "commit_id": HEAD_H, "body": "this leaks", "user": RABBIT_BOT}


def bundle_with(codex_obs, rabbit_obs, head=HEAD_H, epoch="epoch-x"):
    return qualify.build_bundle(
        {"epoch_id": epoch, "generation": 1}, head, 3,
        {"codex": {"epoch_id": epoch, "request_generation": 1},
         "coderabbit": {"epoch_id": epoch, "request_generation": 1}},
        {"codex": codex_obs, "coderabbit": rabbit_obs},
        "2026-08-22T05:40:00Z")


def good_codex():
    return qualify.qualify_codex(
        request_comment(),
        inventory([request_comment(), codex_terminal()]), HEAD_H, OTHER)


def good_rabbit():
    return qualify.qualify_coderabbit(
        request_comment(2, created=T1),
        inventory([request_comment(2, created=T1), rabbit_ack(),
                   rabbit_sticky()]),
        HEAD_H, BASE_SHA)


# --- the happy shapes, so the negatives mean something ----------------------

def test_codex_qualifies_on_a_clean_current_head_round():
    result = good_codex()
    assert result["qualified"] is True
    assert result["state"] == "CODEX_ADVISORY_POSITIVE"
    assert result["terminal_comment"]["resolved_full_sha"] == HEAD_H
    assert result["terminal_comment"]["carrier_kind"] == "mutable_advisory_carrier"


def test_coderabbit_qualifies_when_the_surface_terminates_at_the_head():
    result = good_rabbit()
    assert result["qualified"] is True
    assert result["state"] == "CODERABBIT_ADVISORY_POSITIVE"
    assert result["mutable_advisory_carrier"]["review_range"]["to"] == HEAD_H


def test_neither_provider_state_is_ever_called_clean():
    for result in (good_codex(), good_rabbit()):
        assert "CLEAN" not in result["state"]
    source = (BASE / "harness" / "qualify.py").read_text()
    assert '"CLEAN"' not in source and "'CLEAN'" not in source


# --- the adversarial matrix -------------------------------------------------

def test_codex_positive_but_rabbit_finding_fails():
    rabbit = qualify.qualify_coderabbit(
        request_comment(2), inventory([request_comment(2), rabbit_sticky()],
                                      review_comments=[rabbit_inline_finding()]),
        HEAD_H, BASE_SHA)
    decision = qualify.evaluate(bundle_with(good_codex(), rabbit), HEAD_H,
                                "AUTHORIZED")
    assert decision["verdict"] == qualify.NOT_ESTABLISHED
    assert any("inline finding" in r for r in decision["reasons"])


def test_codex_finding_but_rabbit_positive_fails():
    codex = qualify.qualify_codex(
        request_comment(),
        inventory([request_comment(), codex_terminal()],
                  reviews=[codex_review_finding()]), HEAD_H, OTHER)
    decision = qualify.evaluate(bundle_with(codex, good_rabbit()), HEAD_H,
                                "AUTHORIZED")
    assert decision["verdict"] == qualify.NOT_ESTABLISHED


def test_one_provider_missing_fails():
    silent = qualify.qualify_coderabbit(
        request_comment(2), inventory([request_comment(2)]), HEAD_H, BASE_SHA)
    assert silent["qualified"] is False
    decision = qualify.evaluate(bundle_with(good_codex(), silent), HEAD_H,
                                "AUTHORIZED")
    assert decision["verdict"] == qualify.NOT_ESTABLISHED


def test_rate_limited_is_not_positive():
    limited = qualify.qualify_coderabbit(
        request_comment(2),
        inventory([request_comment(2), rabbit_rate_limited(), rabbit_sticky()]),
        HEAD_H, BASE_SHA)
    assert limited["qualified"] is False
    assert any("rate-limited" in r for r in limited["reasons"])


def test_positive_evidence_from_an_old_head_is_stale():
    decision = qualify.evaluate(bundle_with(good_codex(), good_rabbit()),
                                "b" * 40, "AUTHORIZED")
    assert decision["verdict"] == qualify.STALE


def test_wrong_actor_never_qualifies():
    impostor = codex_terminal()
    impostor["user"] = {"login": "someone", "id": 42, "type": "User"}
    result = qualify.qualify_codex(
        request_comment(), inventory([request_comment(), impostor]),
        HEAD_H, OTHER)
    assert result["qualified"] is False


def test_terminal_artifact_predating_the_request_does_not_count():
    """Right text, wrong request generation: the artifact is older than the
    request it would have to answer."""
    stale_terminal = codex_terminal(created="2026-08-22T05:00:00Z")
    result = qualify.qualify_codex(
        request_comment(created=T1),
        inventory([request_comment(created=T1), stale_terminal]),
        HEAD_H, OTHER)
    assert result["qualified"] is False
    assert any("no Codex response after this request" in r
               for r in result["reasons"])


def test_plain_user_carrier_is_not_the_app_mediated_carrier():
    plain = request_comment(via=None)
    result = qualify.qualify_codex(
        request_comment(via=None), inventory([plain, codex_terminal()]),
        HEAD_H, OTHER)
    assert result["request_carrier"] == "plain_user"
    assert result["qualified"] is False


def test_prefix_that_does_not_uniquely_resolve_fails():
    """Two commits of the PR share the attested prefix, so it attests
    nothing in particular."""
    ambiguous_head = "abcdef1200000000000000000000000000000000"
    sibling = "abcdef1211111111111111111111111111111111"
    result = qualify.qualify_codex(
        request_comment(),
        inventory([request_comment(), codex_terminal(prefix="abcdef12")]),
        ambiguous_head, [sibling])
    assert result["qualified"] is False
    assert any("uniquely" in r for r in result["reasons"])


def test_positive_wording_with_an_actionable_finding_still_fails():
    """The provider's own success-shaped signal is never cleanliness
    evidence — an actionable finding disqualifies regardless."""
    result = qualify.qualify_coderabbit(
        request_comment(2),
        inventory([request_comment(2), rabbit_sticky()],
                  review_comments=[rabbit_inline_finding()]),
        HEAD_H, BASE_SHA)
    assert result["qualified"] is False
    assert any("inline finding" in r for r in result["reasons"])


def test_absence_of_findings_without_terminal_evidence_is_not_positive():
    """The single most important negative: nothing bad happened, and that
    is not the same as something good being established."""
    quiet = qualify.qualify_coderabbit(
        request_comment(2), inventory([request_comment(2)]), HEAD_H, BASE_SHA)
    assert quiet["findings_seen"] == {"reviews": 0, "inline": 0}
    assert quiet["qualified"] is False
    quiet_codex = qualify.qualify_codex(
        request_comment(), inventory([request_comment()]), HEAD_H, OTHER)
    assert quiet_codex["findings_seen"] == {"reviews": 0, "inline": 0}
    assert quiet_codex["qualified"] is False


# --- non-monotonicity -------------------------------------------------------

def test_sticky_body_mutation_invalidates_a_qualified_bundle():
    bundle = bundle_with(good_codex(), good_rabbit())
    assert qualify.evaluate(bundle, HEAD_H, "AUTHORIZED")["verdict"] == \
        qualify.SUCCESS_CANDIDATE

    mutated = qualify.qualify_coderabbit(
        request_comment(2),
        inventory([request_comment(2),
                   rabbit_sticky(updated="2026-08-22T05:45:00Z",
                                 body="No actionable comments were generated "
                                      "in the recent review. (edited)\n\n"
                                      f"Reviewing files that changed from the "
                                      f"base of the PR and between {BASE_SHA} "
                                      f"and {HEAD_H}.")]),
        HEAD_H, BASE_SHA)
    mutation = qualify.detect_mutation(bundle, {"codex": good_codex(),
                                                "coderabbit": mutated})
    assert mutation["stable"] is False
    assert mutation["verdict"] == qualify.INVALIDATED
    assert any("body changed" in c for c in mutation["changes"])


def test_a_new_finding_after_qualification_invalidates():
    bundle = bundle_with(good_codex(), good_rabbit())
    later = qualify.qualify_coderabbit(
        request_comment(2),
        inventory([request_comment(2), rabbit_sticky()],
                  review_comments=[rabbit_inline_finding("2026-08-22T05:50:00Z")]),
        HEAD_H, BASE_SHA)
    mutation = qualify.detect_mutation(bundle, {"codex": good_codex(),
                                                "coderabbit": later})
    assert mutation["stable"] is False
    assert any("finding inventory changed" in c for c in mutation["changes"])


def test_auth_loss_invalidates_even_with_perfect_evidence():
    bundle = bundle_with(good_codex(), good_rabbit())
    for lost in ("AUTH_LOST", "REAUTH_REQUIRED", "REFRESH_OUTCOME_UNKNOWN"):
        decision = qualify.evaluate(bundle, HEAD_H, lost)
        assert decision["verdict"] == qualify.INVALIDATED
        assert decision["publishable"] is False


def test_success_candidate_is_never_publishable_in_a3a():
    decision = qualify.evaluate(bundle_with(good_codex(), good_rabbit()),
                                HEAD_H, "AUTHORIZED")
    assert decision["verdict"] == qualify.SUCCESS_CANDIDATE
    assert decision["publishable"] is False


def test_requests_from_different_epochs_do_not_combine():
    bundle = bundle_with(good_codex(), good_rabbit())
    bundle["requests"]["coderabbit"]["epoch_id"] = "epoch-other"
    decision = qualify.evaluate(bundle, HEAD_H, "AUTHORIZED")
    assert decision["verdict"] == qualify.NOT_ESTABLISHED
    assert any("different review epochs" in r for r in decision["reasons"])


def test_evidence_hash_changes_when_any_input_changes():
    first = bundle_with(good_codex(), good_rabbit())
    second = copy.deepcopy(first)
    second["observations"]["codex"]["terminal_comment"]["body_hash"] = "deadbeef"
    rebuilt = qualify.build_bundle(
        {"epoch_id": second["epoch_id"], "generation": 1}, second["head_sha"],
        second["auth_generation"], second["requests"], second["observations"],
        second["inventory_cutoff"])
    assert rebuilt["evidence_hash"] != first["evidence_hash"]
