"""A6f-c3: one round, end to end, through the public methods A6g will use.

Every previous stage qualified components and then discovered that the
composition could not run: parser green, collector green, parser → collector
refused. So the criterion here is not a module returning the right thing. It
is a single governed round driven from a fake GitHub whose responses have
the shape the live API actually returns — including the two provider shapes
that were structurally impossible to admit before this stage.

The fake is a transport, not a fixture of conclusions: the driver decides
which endpoints to call and the fake answers as GitHub would. Nothing here
hands the parser its input or the store its verdict.
"""
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "harness"))

import collector  # noqa: E402
import evidence  # noqa: E402
import governed_round as gr  # noqa: E402
import health as health_mod  # noqa: E402
import predicates  # noqa: E402
import publish  # noqa: E402
import rounds  # noqa: E402
import triggers  # noqa: E402
from conftest import (A, B, CARRIER_RUN, EPOCH, REPO, RULESET,  # noqa: E402
                      now_stamp, permission_at)

GOVERNOR_APP = 4669438
STICKY_ID = 5349895008
SKIP_RUN = "a3d2af24-8685-49a2-9e6e-728a59d8dcd4"
REVIEW_RUN = "a765cb7e-2018-4a07-b66f-66539b83f8cd"
BASE_COMMIT = "add0a0975eb499491eefe9f83d971152153d8106"

#: The real shape: a skip block for one run sitting above nothing yet.
STICKY_BEFORE = (
    "<!-- This is an auto-generated comment: skip review by coderabbit.ai -->\n"
    "> This repository does not receive automatic reviews.\n"
    f"**Run ID**: `{SKIP_RUN}`\n"
    "<!-- end of auto-generated comment: skip review by coderabbit.ai -->\n")

#: The same carrier after a real review: the old skip block is still there.
STICKY_AFTER = STICKY_BEFORE + (
    "**Actionable comments posted: 0**\n"
    f"Reviewing files that changed from the base of the PR and between "
    f"{BASE_COMMIT} and {A}.\n"
    f"**Run ID**: `{REVIEW_RUN}`\n"
    "Example uuid quoted from the diff: 6ba7b810-9dad-11d1-80b4-00c04fd430c8\n")


class FakeGitHub:
    """Answers the endpoints the driver chooses to call, in GitHub's shapes.

    Provider answers are staged as mutations that fire when the Governor
    posts, so causality in the test comes from the order of events rather
    than from timestamps chosen to make an assertion pass.
    """

    def __init__(self, *, head=A, pr=32, draft=False, base_ref="main",
                 coderabbit_answers=True, codex_answers="reaction",
                 carrier_conclusion="failure", carrier_head=None,
                 carrier_runs=None, epoch_id=EPOCH):
        self.head, self.pr, self.draft, self.base_ref = head, pr, draft, base_ref
        self.coderabbit_answers = coderabbit_answers
        self.codex_answers = codex_answers
        self.epoch_id = epoch_id
        self.calls = []
        self.next_comment_id = 6000
        self.patched = []
        self.comments = [{
            "id": STICKY_ID, "body": STICKY_BEFORE,
            "user": {"login": "coderabbitai[bot]", "id": 136622811},
            "performed_via_github_app": {"id": 347564},
            "created_at": "2026-08-20T01:03:30Z",
            "updated_at": "2026-08-20T01:03:30Z"}]
        self.reactions = {}
        self.check_runs = carrier_runs if carrier_runs is not None else [{
            "id": CARRIER_RUN, "name": "ai/final-review",
            "app": {"id": GOVERNOR_APP},
            "head_sha": carrier_head or head, "external_id": epoch_id,
            "conclusion": carrier_conclusion}]

    # -- transport -------------------------------------------------------
    def read(self, method, path):
        return self.request(method, path, None)

    def post(self, path, body):
        return self.request("POST", path, body)

    def request(self, method, path, body=None):
        self.calls.append((method, path))
        if method == "GET" and path == f"/repos/{REPO}/pulls/{self.pr}":
            return 200, {"number": self.pr, "head": {"sha": self.head},
                         "draft": self.draft, "base": {"ref": self.base_ref},
                         "state": "open"}
        if method == "GET" and path.startswith(
                f"/repos/{REPO}/issues/{self.pr}/comments"):
            return 200, [dict(c) for c in self.comments]
        if method == "GET" and path.startswith(f"/repos/{REPO}/issues/comments/"):
            cid = int(path.split("/issues/comments/")[1].split("/")[0])
            return 200, [dict(r) for r in self.reactions.get(cid, [])]
        if method == "POST" and path.startswith(
                f"/repos/{REPO}/issues/{self.pr}/comments"):
            return self._governor_asks(body)
        if method == "GET" and "/check-runs/" in path:
            rid = int(path.rsplit("/", 1)[1])
            run = next((r for r in self.check_runs if r["id"] == rid), None)
            return (200, dict(run)) if run else (404, None)
        if method == "GET" and path.startswith(f"/repos/{REPO}/commits/"):
            sha = path.split("/commits/")[1].split("/")[0]
            return 200, {"check_runs": [dict(r) for r in self.check_runs
                                        if r["head_sha"] == sha]}
        if method == "PATCH" and "/check-runs/" in path:
            rid = int(path.rsplit("/", 1)[1])
            self.patched.append(rid)
            for r in self.check_runs:
                if r["id"] == rid:
                    r.update({k: v for k, v in body.items()
                              if k in ("conclusion", "head_sha", "external_id",
                                       "name")})
            return 200, {}
        return 404, None

    # -- the provider answering ------------------------------------------
    def _governor_asks(self, body):
        """The Governor's request comment, and the provider's reply to it.

        The provider answer is staged here, at the moment the request is
        posted, so a later `updated_at` is a consequence of the request
        rather than a number chosen to satisfy the collector.
        """
        self.next_comment_id += 1
        cid = self.next_comment_id
        text = body["body"]
        self.comments.append({
            "id": cid, "body": text,
            "user": {"login": "PhysShell", "id": 111},
            "performed_via_github_app": {"id": GOVERNOR_APP},
            "created_at": now_stamp(), "updated_at": now_stamp()})
        answered_at = now_stamp(5)
        if triggers.INVOCATION[triggers.CODERABBIT] in text \
                and self.coderabbit_answers:
            for c in self.comments:
                if c["id"] == STICKY_ID:
                    c["body"] = STICKY_AFTER
                    c["updated_at"] = answered_at
        if triggers.INVOCATION[triggers.CODEX] in text:
            if self.codex_answers == "reaction":
                self.reactions[cid] = [{
                    "id": 7001, "content": "+1", "created_at": answered_at,
                    "user": {"login": "chatgpt-codex-connector[bot]",
                             "id": 199175422}}]
            elif self.codex_answers == "findings":
                self.comments.append({
                    "id": cid + 500,
                    "body": f"Found 2 issues in {A}",
                    "user": {"login": "chatgpt-codex-connector[bot]",
                             "id": 199175422},
                    "performed_via_github_app": {"id": 1144995},
                    "created_at": answered_at, "updated_at": answered_at})
        return 201, {"id": cid}


def health_files(tmp_path, *, pr=32, head=A, all_compared=True,
                 comparison_performed=True, drift=False, edge=True,
                 runtime_state="OK", age=5):
    (tmp_path / "runtime-health.json").write_text(json.dumps({
        "last_complete_pass_at": now_stamp(-age), "state": runtime_state,
        "pr_count": 2, "writes_last_pass": 0}))
    (tmp_path / "reconciliation-health.json").write_text(json.dumps({
        "last_complete_pass_at": now_stamp(-age),
        "comparisons_attempted": 2, "comparisons_performed": 2,
        "all_compared": all_compared,
        "per_pr": [{"pr_number": pr, "scope_state": "RESOLVED",
                    "stored_head": head, "github_head": head,
                    "comparison_performed": comparison_performed,
                    "drift_detected": drift}],
        "source": "steady-state runtime, scoped reconciliation"}))
    (tmp_path / "watchdog-health.json").write_text(json.dumps({
        "last_complete_pass_at": now_stamp(-age), "watchdog_polls": 32171,
        "source": "edge /healthz, value produced by the watchdog process",
        "relayed_by": "primary sentinel", "edge_reachable": edge}))
    return {n: str(tmp_path / f"{n}-health.json")
            for n in ("runtime", "reconciliation", "watchdog")}


def driver(tmp_path, store, snaps, epochs, github, *, permission_path=None):
    import auth_state
    auth = auth_state.AuthStore(permission_path or tmp_path / "auth.sqlite3")
    if not auth.current():
        auth.record(state="AUTHORIZED", auth_generation=5,
                    observed_at=now_stamp(), source="refresh")
    d = gr.GovernedRound(
        repo=REPO, pr_number=github.pr, read=github.read, post=github.post,
        auth_store=auth, round_store=store, snapshot_store=snaps,
        epoch_store=epochs, health_sources=health_files(tmp_path,
                                                        pr=github.pr,
                                                        head=github.head))
    d._auth = auth
    return d


class FakeEpochs:
    def __init__(self):
        self.projections = []

    def record_decision(self, **kw):
        return 1

    def project(self, **kw):
        self.projections.append(kw)


def run_round(d, github, *, providers=("coderabbit", "codex")):
    """The full sequence, exactly as A6g would drive it."""
    observation = d.observe(ruleset_id=RULESET,
                            ruleset_verified_fn=lambda: True,
                            carrier_fn=lambda h: {
                                "state": "CONFIRMED", "head_sha": h,
                                "check_run_id": CARRIER_RUN})
    accepted = d.accept_candidate(observation, epoch_id=EPOCH)
    if accepted.get("state") == gr.STOP:
        return {"stopped": accepted}
    records = []
    for provider in providers:
        baseline = d.capture_baseline(provider)
        sent = d.request_provider(accepted, provider, 1, baseline=baseline)
        records.append(d.collect_evidence(sent, provider, 1))
    d.epochs = FakeEpochs()
    concluded = d.conclude(records, epoch_id=EPOCH, existing_run=CARRIER_RUN,
                           ruleset_verified_fn=lambda: True,
                           patch=github.request)
    return {"observation": observation, "accepted": accepted,
            "records": records, "concluded": concluded}


# --- the round that must be possible ------------------------------------------

def test_a_full_governed_round_reaches_success(tmp_path, store, snaps, epochs):
    """The positive path, driven through the same methods A6g will call.

    CodeRabbit answers by rewriting a sticky that predates the request;
    Codex answers a clean review with a reaction on the exact request
    carrier. Both shapes were structurally inadmissible before this stage.
    """
    github = FakeGitHub()
    d = driver(tmp_path, store, snaps, epochs, github)
    out = run_round(d, github)

    assert out["accepted"]["acceptance"]["state"] == rounds.ACCEPTED
    for rec in out["records"]:
        assert rec.get("state") == "ANSWERED", rec
        assert rec["terminal"]["admissible"] is True, rec["terminal"]
        assert rec["predicate"]["state"] == predicates.POSITIVE, rec["predicate"]

    concluded = out["concluded"]
    assert concluded.get("state") != gr.STOP, concluded
    assert concluded["reduction"]["verdict"] == evidence.SUCCESS, \
        concluded["reduction"]["refusals"]
    assert concluded["lineage"]["all_bound"] is True
    assert concluded["replay"]["all_reproduced"] is True
    assert concluded["health"]["all_fresh"] is True
    assert concluded["publication"]["state"] == "CONFIRMED"
    assert github.patched == [CARRIER_RUN]
    assert d._auth.close() is None


def test_the_two_provider_shapes_bind_their_heads_differently(tmp_path, store,
                                                              snaps, epochs):
    github = FakeGitHub()
    out = run_round(driver(tmp_path, store, snaps, epochs, github), github)
    by_provider = {r["provider"]: r for r in out["records"]}
    assert by_provider["coderabbit"]["terminal"]["head_binding"] == \
        collector.ATTESTED
    assert by_provider["coderabbit"]["terminal"]["causality"] == \
        "POST_REQUEST_REWRITE"
    assert by_provider["codex"]["terminal"]["head_binding"] == \
        collector.REQUEST_DERIVED


def test_the_driver_reads_the_terminal_surface_itself(tmp_path, store, snaps,
                                                      epochs):
    """No caller supplies a comment or a reaction. The endpoints appear in
    the transport log because the driver chose to call them."""
    github = FakeGitHub()
    run_round(driver(tmp_path, store, snaps, epochs, github), github)
    paths = [p for m, p in github.calls if m == "GET"]
    assert any(f"/issues/32/comments" in p for p in paths)
    assert any("/issues/comments/" in p and p.endswith("/reactions")
               for p in paths)
    import inspect
    sig = inspect.signature(gr.GovernedRound.collect_evidence)
    assert "raw_comments" not in sig.parameters
    assert "raw_reactions" not in sig.parameters
    assert "baseline" not in sig.parameters


def test_codex_findings_do_not_reach_success(tmp_path, store, snaps, epochs):
    github = FakeGitHub(codex_answers="findings")
    out = run_round(driver(tmp_path, store, snaps, epochs, github), github)
    codex = [r for r in out["records"] if r.get("provider") == "codex"][0]
    assert codex["predicate"]["state"] == predicates.NOT_POSITIVE
    assert out["concluded"]["reduction"]["verdict"] == evidence.NOT_ESTABLISHED
    assert github.patched == [CARRIER_RUN]
    assert github.check_runs[0]["conclusion"] == "failure"


def test_a_silent_provider_stops_the_round(tmp_path, store, snaps, epochs):
    github = FakeGitHub(coderabbit_answers=False)
    out = run_round(driver(tmp_path, store, snaps, epochs, github), github)
    cr = [r for r in out["records"] if r.get("provider") != "codex"][0]
    assert cr["state"] == gr.STOP
    assert "no coderabbit answer attributable" in cr["cause"]
    assert out["concluded"]["state"] == gr.STOP
    assert github.patched == []


# --- adversarial: each row the reviewer named ---------------------------------

def test_a_forged_gate_result_has_nowhere_to_go(store, fresh):
    """The previous boundary was a type, so a caller could construct it.
    There is no result parameter left to forge."""
    import inspect
    sig = inspect.signature(rounds.RoundStore.record_acceptance)
    assert "preconditions" not in sig.parameters
    assert "observation_id" in sig.parameters
    with pytest.raises(rounds.RoundError) as exc:
        store.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                                head_sha=A, permission=fresh,
                                observation_id="obs-made-up")
    assert "never from a claim about one" in str(exc.value)


def test_the_writer_gates_over_the_stored_carrier_not_a_claim(store, fresh):
    """`carrier_run_id` and `ruleset_id` were carried and never re-checked.
    Now they come from the row the writer loads."""
    from conftest import record_observation
    obs = record_observation(store, carrier_state="PENDING")
    with pytest.raises(rounds.RoundError) as exc:
        store.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                                head_sha=A, permission=fresh,
                                observation_id=obs["observation_id"])
    assert "CONFIRMED failure carrier" in str(exc.value)
    ok = record_observation(store)
    acc = store.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                                  head_sha=A, permission=fresh,
                                  observation_id=ok["observation_id"])
    assert acc["carrier_run_id"] == CARRIER_RUN
    assert acc["ruleset_id"] == RULESET


def test_an_observation_of_another_pr_cannot_gate_this_one(store, fresh):
    from conftest import record_observation
    other = record_observation(store, pr=8)
    with pytest.raises(rounds.RoundError) as exc:
        store.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                                head_sha=A, permission=fresh,
                                observation_id=other["observation_id"])
    assert "not about this acceptance" in str(exc.value)


def test_a_request_must_cite_the_baseline_that_preceded_it(store, snaps, fresh):
    from conftest import accept, captured_baseline
    acc = accept(store, fresh)
    with pytest.raises(rounds.RoundError) as exc:
        store.record_intent(acceptance_id=acc["acceptance_id"], repo=REPO,
                            pr_number=32, provider="codex", generation=1,
                            requested_for_head=A, permission=fresh,
                            baseline={"run_ids": []})
    assert "durable baseline capture" in str(exc.value)

    later = captured_baseline(snaps, provider="codex",
                              captured_at=now_stamp(300))
    with pytest.raises(rounds.RoundError) as exc:
        store.record_intent(acceptance_id=acc["acceptance_id"], repo=REPO,
                            pr_number=32, provider="codex", generation=1,
                            requested_for_head=A, permission=fresh,
                            baseline=later)
    assert "captured after this intent" in str(exc.value)


def test_a_baseline_for_another_pr_cannot_bind_this_request(store, snaps, fresh):
    from conftest import accept, captured_baseline
    acc = accept(store, fresh)
    with pytest.raises(rounds.RoundError) as exc:
        store.record_intent(acceptance_id=acc["acceptance_id"], repo=REPO,
                            pr_number=32, provider="codex", generation=1,
                            requested_for_head=A, permission=fresh,
                            baseline=captured_baseline(snaps, provider="codex",
                                                       pr=8))
    assert "another scope" in str(exc.value)


def test_two_readings_of_an_unchanged_surface_are_two_captures(snaps):
    """A baseline is an event. Content-addressing made a second reading
    return the first one's timestamp, so a request could cite a capture
    that happened long before it."""
    from conftest import captured_baseline
    first = captured_baseline(snaps, captured_at="2026-08-30T04:00:00Z")
    second = captured_baseline(snaps, captured_at="2026-08-30T04:05:00Z")
    assert first["baseline_id"] != second["baseline_id"]
    assert first["baseline_digest"] == second["baseline_digest"]
    assert second["captured_at"] == "2026-08-30T04:05:00Z"


def test_collection_uses_the_request_bound_baseline(tmp_path, store, snaps,
                                                    epochs):
    """The collector no longer takes a baseline. It loads the one the
    durable request names, so 'capture X, post, collect against Y' has no
    expression."""
    import inspect
    assert "baseline" not in inspect.signature(
        gr.GovernedRound.collect_evidence).parameters
    github = FakeGitHub()
    d = driver(tmp_path, store, snaps, epochs, github)
    out = run_round(d, github)
    for rec in out["records"]:
        row = store.request(rec["request_id"])
        assert rec["baseline_id"] == row["baseline_id"]
        assert snaps.baseline(row["baseline_id"])["baseline_digest"] == \
            row["baseline_digest"]


def test_a_recent_pass_that_compared_nothing_refuses_success(tmp_path, store,
                                                             snaps, epochs):
    github = FakeGitHub()
    d = driver(tmp_path, store, snaps, epochs, github)
    d.health_sources = health_files(tmp_path, all_compared=False,
                                    comparison_performed=False)
    out = run_round(d, github)
    obs = out["concluded"]["health"]["observations"]["reconciliation"]
    assert obs["state"] == health_mod.UNSATISFIED
    assert out["concluded"]["publication"]["intended"] == "failure"


def test_health_for_another_head_refuses_success(tmp_path, store, snaps,
                                                 epochs, fresh):
    """The reconciliation row must be about the head being published for."""
    sources = health_files(tmp_path, head=B)
    out = health_mod.evaluate(sources, candidate={"repo": REPO,
                                                  "pr_number": 32,
                                                  "head_sha": A})
    assert out["observations"]["reconciliation"]["state"] == \
        health_mod.UNSATISFIED
    checked = publish.guard(reduction={"verdict": "SUCCESS", "head_sha": A},
                            bundle={"head_sha": A}, current_head_sha=A,
                            permission=fresh, health=out,
                            existing_run=CARRIER_RUN)
    assert checked["may_publish_success"] is False


def test_health_evaluated_without_a_candidate_refuses_success(tmp_path, fresh):
    """A pass over somebody else's PRs is as recent as one over ours."""
    out = health_mod.evaluate(health_files(tmp_path))
    assert out["all_fresh"] is True
    checked = publish.guard(reduction={"verdict": "SUCCESS", "head_sha": A},
                            bundle={"head_sha": A}, current_head_sha=A,
                            permission=fresh, health=out,
                            existing_run=CARRIER_RUN)
    assert checked["may_publish_success"] is False
    assert any("without the candidate" in r for r in checked["refusals"])


def test_an_unreachable_edge_is_not_a_watchdog_value(tmp_path):
    out = health_mod.evaluate(health_files(tmp_path, edge=False),
                              candidate={"repo": REPO, "pr_number": 32,
                                         "head_sha": A})
    assert out["observations"]["watchdog"]["state"] == health_mod.UNSATISFIED


def test_evidence_from_an_invalidated_acceptance_does_not_migrate(
        tmp_path, store, snaps, epochs):
    """A -> B -> A. The acceptance does not resurrect, and neither may the
    requests made under it."""
    github = FakeGitHub()
    d = driver(tmp_path, store, snaps, epochs, github)
    first = run_round(d, github)
    old_records = first["records"]
    old_acceptance = first["accepted"]["acceptance"]["acceptance_id"]

    store.invalidate_for_head_move(REPO, 32, B)
    store.invalidate_for_head_move(REPO, 32, A)
    from conftest import record_observation
    import auth_policy
    fresh2 = auth_policy.evaluate(d.auth)
    obs = record_observation(store)
    new_acc = store.record_acceptance(repo=REPO, pr_number=32, epoch_id=EPOCH,
                                      head_sha=A, permission=fresh2,
                                      observation_id=obs["observation_id"])
    assert new_acc["acceptance_id"] != old_acceptance

    d.epochs = FakeEpochs()
    out = d.conclude(old_records, epoch_id=EPOCH, existing_run=CARRIER_RUN,
                     ruleset_verified_fn=lambda: True, patch=github.request)
    assert out["standing_acceptance"] == new_acc["acceptance_id"]
    assert out["reduction"]["verdict"] == evidence.NOT_ESTABLISHED
    assert any("other acceptances" in r or "belongs to acceptance" in r
               for r in out["reduction"]["refusals"]), out["reduction"]


def test_the_generation_comes_from_the_permission_not_a_parameter(store, snaps,
                                                                  fresh):
    """A caller holding generation 6 could pass 5, build a bundle at 5, and
    watch the reducer confirm that two of its arguments agreed."""
    import inspect
    assert "auth_generation" not in inspect.signature(
        evidence.reduce).parameters
    assert "auth_generation" not in inspect.signature(
        gr.GovernedRound.conclude).parameters
    from conftest import accept
    acc = accept(store, fresh)
    bundle = evidence.build_bundle(repo=REPO, pr_number=32, head_sha=A,
                                   lineage_records=[], auth_generation=4,
                                   acceptance_id=acc["acceptance_id"])
    red = evidence.reduce(bundle, current_head_sha=A, permission=fresh,
                          standing_acceptance=acc)
    assert red["verdict"] == evidence.NOT_ESTABLISHED
    assert any("the permission carries 5" in r for r in red["refusals"])


def test_a_wrong_existing_run_writes_nothing(tmp_path, store, snaps, epochs,
                                             fresh):
    """The identity check used to run on the readback: given a wrong id the
    Governor patched somebody else's carrier and then reported that it had."""
    github = FakeGitHub(carrier_runs=[
        {"id": CARRIER_RUN, "name": "ai/final-review",
         "app": {"id": GOVERNOR_APP}, "head_sha": A, "external_id": EPOCH,
         "conclusion": "failure"},
        {"id": 555, "name": "ci/build", "app": {"id": 15368},
         "head_sha": A, "external_id": None, "conclusion": "success"}])
    with pytest.raises(publish.PublishRefused) as exc:
        publish.publish(github.request, repo=REPO, epoch_id=EPOCH, head_sha=A,
                        conclusion="failure",
                        bundle={"head_sha": A, "bundle_hash": "h",
                                "schema": evidence.SCHEMA_NAME},
                        reduction={"verdict": "NOT_ESTABLISHED",
                                   "head_sha": A},
                        current_head_sha=A, permission=fresh,
                        store=FakeEpochs(), existing_run=555, health=None)
    assert "nothing was written" in str(exc.value)
    assert github.patched == []


def test_two_governor_carriers_on_a_head_refuse_the_write(tmp_path, fresh):
    github = FakeGitHub(carrier_runs=[
        {"id": CARRIER_RUN, "name": "ai/final-review",
         "app": {"id": GOVERNOR_APP}, "head_sha": A, "external_id": EPOCH,
         "conclusion": "failure"},
        {"id": CARRIER_RUN + 1, "name": "ai/final-review",
         "app": {"id": GOVERNOR_APP}, "head_sha": A, "external_id": EPOCH,
         "conclusion": "failure"}])
    checked = publish.preread_carrier(github.request, repo=REPO,
                                      run_id=CARRIER_RUN, head_sha=A,
                                      epoch_id=EPOCH)
    assert checked["may_patch"] is False
    assert any("exactly one applicable carrier" in r
               for r in checked["refusals"])
    assert github.patched == []


def test_a_stale_permission_stops_before_anything_is_written(tmp_path, store,
                                                             snaps, epochs):
    github = FakeGitHub()
    d = driver(tmp_path, store, snaps, epochs, github,
               permission_path=tmp_path / "stale.sqlite3")
    d.auth.record(state="AUTHORIZED", auth_generation=5,
                  observed_at="2020-01-01T00:00:00Z", source="refresh")
    out = run_round(d, github)
    assert out["stopped"]["state"] == gr.STOP
    assert "STALE" in out["stopped"]["cause"]
    assert store.acceptances_for(REPO, 32) == []
    assert github.patched == []
    assert not any(m == "POST" for m, _ in github.calls)
