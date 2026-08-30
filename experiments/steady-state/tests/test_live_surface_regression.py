"""The two real carriers, byte for byte, through the real pipeline.

Every provider defect this programme has found was invisible to fixtures
because the fixtures were written by the same person who wrote the check.
`performed_via_github_app: {"id": 199175422}` passed for two stages; GitHub
puts 1144995 there and 199175422 in `user.id`, so no real Codex carrier
could ever have been admitted.

So these two tests read `fixtures/live-provider-carriers.json` — captured
verbatim from the API and never edited — and drive it through
`parse -> admissibility -> predicate`, which is the sequence a governed
round performs.
"""
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "harness"))

import collector  # noqa: E402
import parsers  # noqa: E402
import predicates  # noqa: E402
from conftest import REPO, captured_baseline, flat_baseline  # noqa: E402

LIVE = json.loads(
    (HERE / "fixtures" / "live-provider-carriers.json").read_text())
STICKY = next(c for c in LIVE["carriers"] if c["id"] == 5349895008)
CODEX = next(c for c in LIVE["carriers"] if c["id"] == 5462308601)
#: The current head of `#8`. The Codex carrier attests this one.
H8 = "2d8348703924c7470ba82f525cafc9afe720aee2"
#: The head the sticky's reviewed range actually ends at — an *older*
#: commit. Discovered by reading the fixture rather than by assuming: the
#: review that produced REVIEW_RUN ran before the last push, so the real
#: carrier attests a commit that is no longer the head. That is the A3a
#: finding standing in the fixture, and the test asserts what the range
#: says instead of what would be convenient.
H8_REVIEWED = "8aeafa9c28b9679c6fec660101f37e1f8bd994bd"

SKIP_RUN = "a3d2af24-8685-49a2-9e6e-728a59d8dcd4"
REVIEW_RUN = "a765cb7e-2018-4a07-b66f-66539b83f8cd"


def request_row(**over):
    base = {"provider": "coderabbit", "requested_for_head": H8_REVIEWED,
            "intent_recorded_at": "2026-08-29T10:00:00Z",
            "request_carrier_id": 5462012129,
            "acceptance_id": "acc-live", "baseline_id": "base-live"}
    base.update(over)
    return base


def _pre_review_sticky():
    """The sticky as it stood before the review that produced REVIEW_RUN.

    Reconstructed by removing exactly the block that run added, so the
    baseline is what a capture taken before the request would have found.
    """
    blocks = parsers.split_run_blocks(STICKY["body"])
    assert REVIEW_RUN in blocks["review_run_ids"], \
        "the captured sticky no longer contains the review run this test is about"
    return {**STICKY, "body": parsers.SKIP_BLOCK.search(STICKY["body"]).group(0),
            "updated_at": "2026-08-22T05:23:27Z"}


# --- 1. the historical CodeRabbit sticky --------------------------------------

def test_the_real_sticky_is_admissible_and_positive(snaps):
    """Baseline holds the skip run; the carrier is rewritten with a review
    run, a clean recent-review block, and a `created_at` nine days older
    than the request. Every one of those was individually fatal before."""
    baseline = captured_baseline(snaps, [_pre_review_sticky()],
                                 captured_at="2026-08-29T10:00:00Z")
    base = flat_baseline(baseline)
    assert base["run_ids"] == [SKIP_RUN], base["run_ids"]

    observed = parsers.parse_coderabbit(
        [STICKY], base=base, requested_head=H8_REVIEWED, generation=1)
    assert observed is not None, "the real sticky produced no observation"
    assert observed["new_run_ids"] == [REVIEW_RUN]
    assert SKIP_RUN in observed["skipped_run_ids"]
    assert observed["carrier_was_rewritten"] is True
    assert observed["reviewed_range"]["to"] == H8_REVIEWED
    assert observed["head_claim"] == H8_REVIEWED
    assert observed["findings"] == []
    assert observed["author_app_id"] == 347564
    assert observed["author_user_id"] == 136622811

    verdict = collector.admissibility(observed, request_row(),
                                      head_sha=H8_REVIEWED, generation=1)
    assert verdict["admissible"] is True, verdict["refusals"]
    assert verdict["causality"] == "POST_REQUEST_REWRITE"
    assert verdict["head_binding"] == collector.ATTESTED

    assert predicates.evaluate("coderabbit", observed)["state"] == \
        predicates.POSITIVE


def test_the_real_sticky_attests_only_the_head_it_reviewed(snaps):
    """The same carrier, asked about the current head of #8. Its range ends
    at an earlier commit, so it attests nothing here — the A3a finding, in
    a real body rather than a constructed one."""
    baseline = captured_baseline(snaps, [_pre_review_sticky()],
                                 captured_at="2026-08-29T10:00:00Z")
    observed = parsers.parse_coderabbit(
        [STICKY], base=flat_baseline(baseline), requested_head=H8,
        generation=1)
    assert observed["reviewed_range"]["to"] == H8_REVIEWED
    assert observed["head_claim"] is None
    v = collector.admissibility(observed, request_row(requested_for_head=H8),
                                head_sha=H8, generation=1)
    assert v["admissible"] is False
    assert any(r["code"] == collector.WRONG_HEAD for r in v["refusals"])


def test_the_real_sticky_created_before_the_request_is_still_causal(snaps):
    """`created_at` 2026-08-20, request 2026-08-29. The flat ordering veto
    refused exactly the carrier CodeRabbit actually uses."""
    assert STICKY["created_at"] < "2026-08-29T10:00:00Z"
    assert STICKY["updated_at"] > "2026-08-29T10:00:00Z"
    baseline = captured_baseline(snaps, [_pre_review_sticky()],
                                 captured_at="2026-08-29T10:00:00Z")
    observed = parsers.parse_coderabbit([STICKY], base=flat_baseline(baseline),
                                        requested_head=H8_REVIEWED,
                                        generation=1)
    v = collector.admissibility(observed, request_row(),
                                head_sha=H8_REVIEWED, generation=1)
    assert not any(r["code"] == collector.PREEXISTING for r in v["refusals"])


def test_the_same_sticky_without_a_post_request_rewrite_is_refused(snaps):
    """The rewrite is what carries causality, so a baseline that already
    holds the review run leaves nothing new to attribute."""
    baseline = captured_baseline(snaps, [STICKY],
                                 captured_at="2026-08-29T10:00:00Z")
    assert set(flat_baseline(baseline)["run_ids"]) == {SKIP_RUN, REVIEW_RUN}
    assert parsers.parse_coderabbit([STICKY], base=flat_baseline(baseline),
                                    requested_head=H8_REVIEWED,
                                    generation=1) is None


def test_uuids_quoted_in_the_reviewed_diff_are_not_run_ids():
    """13 bare UUIDs across the real carriers, 2 labelled run ids."""
    labelled = set(parsers.RUN_ID.findall(STICKY["body"]))
    bare = set(parsers.BARE_UUID.findall(STICKY["body"]))
    assert labelled == {SKIP_RUN, REVIEW_RUN}
    assert len(bare - labelled) >= 5
    assert "6ba7b810-9dad-11d1-80b4-00c04fd430c8" in bare - labelled


# --- 2. the real Codex clean review -------------------------------------------

def test_the_real_codex_clean_comment_is_admissible_and_positive(snaps):
    """"Codex Review: Didn't find any major issues. Swish!" with
    `**Reviewed commit:** ` and a ten-character abbreviation.

    `SHA40` never matched that, so `head_claim` was None on every real
    Codex carrier; the identity table held the bot user id where GitHub
    puts the App id; and "didn't find any major issues" matched no
    clean-review phrase. Three independent refusals of the same comment."""
    baseline = captured_baseline(snaps, [], provider="codex",
                                 captured_at="2026-08-29T11:00:00Z")
    observed = parsers.parse_codex(
        [CODEX], [], base=flat_baseline(baseline), requested_head=H8,
        generation=1, request_carrier_id=5462292078)
    assert observed is not None
    assert observed["author_app_id"] == 1144995
    assert observed["author_user_id"] == 199175422
    assert observed["head_claim"] == H8, "abbreviated commit did not attest"
    assert observed["findings"] == []
    assert observed["review_ran"] is True
    assert observed["absent_from_baseline"] is True

    verdict = collector.admissibility(
        observed, request_row(provider="codex", request_carrier_id=5462292078,
                              requested_for_head=H8,
                              intent_recorded_at="2026-08-29T11:00:00Z"),
        head_sha=H8, generation=1)
    assert verdict["admissible"] is True, verdict["refusals"]

    assert predicates.evaluate("codex", observed)["state"] == \
        predicates.POSITIVE


def test_an_abbreviation_of_another_commit_attests_nothing(snaps):
    baseline = captured_baseline(snaps, [], provider="codex",
                                 captured_at="2026-08-29T11:00:00Z")
    observed = parsers.parse_codex(
        [CODEX], [], base=flat_baseline(baseline), requested_head="f" * 40,
        generation=1, request_carrier_id=5462292078)
    assert observed["head_claim"] is None


@pytest.mark.parametrize("abbrev", ["2d8", "2d83487", "2d83487039"])
def test_a_prefix_must_be_long_enough_to_mean_something(abbrev):
    body = f"**Reviewed commit:** `{abbrev}`"
    claim = parsers._codex_head_claim(body, H8)
    assert claim == (H8 if len(abbrev) >= 7 else None)


def test_the_codex_reaction_shape_binds_through_the_request(snaps):
    """Codex documents reacting 👍 when it has nothing to say. A reaction
    carries no App id and no text, so identity comes from the bot user and
    the head from the request GitHub attached it to."""
    baseline = captured_baseline(snaps, [], provider="codex",
                                 captured_at="2026-08-29T11:00:00Z")
    observed = parsers.parse_codex(
        [], [{"id": 7001, "content": "+1",
              "created_at": "2026-08-29T11:05:00Z",
              "user": {"login": "chatgpt-codex-connector[bot]",
                       "id": 199175422}}],
        base=flat_baseline(baseline), requested_head=H8, generation=1,
        request_carrier_id=5462292078)
    assert observed["author_app_id"] is None
    assert observed["author_user_id"] == 199175422
    assert observed["head_binding"] == collector.REQUEST_DERIVED
    assert observed["head_claim"] is None

    v = collector.admissibility(
        observed, request_row(provider="codex", request_carrier_id=5462292078,
                              requested_for_head=H8,
                              intent_recorded_at="2026-08-29T11:00:00Z"),
        head_sha=H8, generation=1)
    assert v["admissible"] is True, v["refusals"]

    stray = collector.admissibility(
        observed, request_row(provider="codex", request_carrier_id=999999,
                              requested_for_head=H8,
                              intent_recorded_at="2026-08-29T11:00:00Z"),
        head_sha=H8, generation=1)
    assert stray["admissible"] is False
    assert any(r["code"] in (collector.UNASSOCIATED, collector.WRONG_HEAD)
               for r in stray["refusals"])


# --- 3. the identity table itself ---------------------------------------------

def test_the_identity_table_matches_the_live_carriers():
    """Both numbers, from the carriers themselves rather than from memory."""
    import triggers
    assert triggers.PROVIDER_IDENTITY["coderabbit"] == {
        "app_id": STICKY["performed_via_github_app"]["id"],
        "bot_user_id": STICKY["user"]["id"],
        "login": STICKY["user"]["login"]}
    assert triggers.PROVIDER_IDENTITY["codex"] == {
        "app_id": CODEX["performed_via_github_app"]["id"],
        "bot_user_id": CODEX["user"]["id"],
        "login": CODEX["user"]["login"]}
    assert triggers.PROVIDER_IDENTITY["codex"]["app_id"] != \
        triggers.PROVIDER_IDENTITY["codex"]["bot_user_id"], \
        "the two identifiers the old table conflated"


def test_the_retired_conflated_constant_raises():
    import triggers
    assert not hasattr(triggers, "PROVIDER_APP_ID")
    with pytest.raises(triggers.TriggerRefused):
        triggers._unused_provider_app_id()
