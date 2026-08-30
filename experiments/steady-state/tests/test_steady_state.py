"""A6a offline qualification.

The positive path must exist before a review starts, and the negative
paths must be the ones that are hard to escape. So most of this file is
about what cannot happen: success from stale evidence, adoption of
somebody else's carrier, a comparison reported as a result, an acceptance
following a branch to a new commit.
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "harness"))
sys.path.insert(0, str(HERE.parents[0] / "operational-readiness" / "harness"))

import accept  # noqa: E402
import auth_policy as ap  # noqa: E402
import auth_state  # noqa: E402
import carrier  # noqa: E402
import epochs as ep  # noqa: E402
import evidence  # noqa: E402
import lineage  # noqa: E402
import migrate  # noqa: E402
import publish  # noqa: E402
import scoped_reconcile as sr  # noqa: E402

REPO = "PhysShell/evm-from-scratch"
H8 = "2d8348703924c7470ba82f525cafc9afe720aee2"
H8_OLD = "8aeafa9c28b9679c6fec660101f37e1f8bd994bd"
H12 = "e29621f54a63b50db4afb77b608d6c3a4d533812"


@pytest.fixture()
def store(tmp_path):
    s = ep.EpochStore(tmp_path / "prod.sqlite3")
    yield s
    s.close()


@pytest.fixture()
def fresh(tmp_path):
    """A permission that carries its provenance, as every critical
    interface now requires."""
    a = auth_state.AuthStore(tmp_path / "auth.sqlite3")
    a.record(state="AUTHORIZED", auth_generation=4,
             observed_at=ap.datetime.datetime.now(
                 ap.datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             source="device_flow")
    yield ap.evaluate(a)
    a.close()


def stale_permission(tmp_path, name="stale.sqlite3"):
    a = auth_state.AuthStore(tmp_path / name)
    a.record(state="AUTHORIZED", auth_generation=4,
             observed_at="2020-01-01T00:00:00Z", source="device_flow")
    p = ap.evaluate(a)
    a.close()
    return p


# --- scope is identity ---------------------------------------------------------

def test_epoch_id_refuses_an_abbreviated_head():
    with pytest.raises(ep.ScopeError):
        ep.epoch_id(REPO, 8, "2d834870", 1)


def test_epoch_id_changes_with_every_part_of_the_identity():
    base = ep.epoch_id(REPO, 8, H8, 1)
    assert ep.epoch_id("other/repo", 8, H8, 1) != base
    assert ep.epoch_id(REPO, 12, H8, 1) != base
    assert ep.epoch_id(REPO, 8, H12, 1) != base
    assert ep.epoch_id(REPO, 8, H8, 2) != base


def test_epoch_id_is_not_a_head_prefix():
    """`bootstrap-8aeafa9c` looked like an identifier and was a head
    prefix, which is how scope came to be inferred from a substring."""
    assert H8[:12] not in ep.epoch_id(REPO, 8, H8, 1)


def test_history_is_append_only(store):
    e = store.open_epoch(repo=REPO, pr_number=8, head_sha=H8, opened_at="t")
    store.record_decision(epoch_id=e["epoch_id"], verdict="NOT_ESTABLISHED",
                          decided_at="t")
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("UPDATE production_epochs SET pr_number=12")
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("DELETE FROM production_decisions")


# --- the tri-state lookup, and cross-PR confusion -----------------------------

def test_no_epoch_is_not_drift(store):
    r = store.last_known_head(REPO, 8)
    assert r["state"] == ep.NO_EPOCH


def test_resolved_returns_this_prs_head_only(store):
    store.open_epoch(repo=REPO, pr_number=8, head_sha=H8_OLD, opened_at="t1")
    store.open_epoch(repo=REPO, pr_number=12, head_sha=H12, opened_at="t2")
    r8 = store.last_known_head(REPO, 8)
    r12 = store.last_known_head(REPO, 12)
    assert r8["state"] == ep.RESOLVED and r8["head_sha"] == H8_OLD
    assert r12["state"] == ep.RESOLVED and r12["head_sha"] == H12


def test_cross_pr_confusion_control(store):
    """The exact failure the naive prefix fix would have produced: asked
    about #8, answered with #12's head."""
    store.open_epoch(repo=REPO, pr_number=8, head_sha=H8_OLD, opened_at="t1")
    store.open_epoch(repo=REPO, pr_number=12, head_sha=H12, opened_at="t2")
    assert store.last_known_head(REPO, 8)["head_sha"] != H12


def test_unmapped_legacy_makes_the_answer_unresolved_not_absent(store):
    """A comparison that cannot be scoped must not report absence, because
    absence reads as 'no drift'."""
    store.record_migration(legacy_epoch="bootstrap-x", legacy_head="y" * 40,
                           mapped_to=None, justification="no match",
                           source_artifact="h", at="t")
    r = store.last_known_head(REPO, 8)
    assert r["state"] == ep.UNRESOLVED
    assert "may be among them" in r["cause"]


# --- migration is provable, never positional -----------------------------------

def _legacy(tmp_path, rows):
    path = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE decisions (decision_id INTEGER PRIMARY KEY,"
                 " epoch_id TEXT, head_sha TEXT, verdict TEXT, decided_at TEXT)")
    conn.executemany("INSERT INTO decisions VALUES (?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return path


def _artifact(tmp_path, entries):
    path = tmp_path / "inv.json"
    path.write_text(json.dumps({"inventory_hash": "93dd8e5b",
                                "inventory": entries}))
    return path


def test_migration_maps_by_full_head_and_says_why(tmp_path):
    legacy = _legacy(tmp_path, [
        (1, "bootstrap-8aeafa9c28b9", H8_OLD, "NOT_ESTABLISHED", "t1"),
        (2, "bootstrap-e29621f54a63", H12, "NOT_ESTABLISHED", "t2")])
    art = _artifact(tmp_path, [
        {"repo": REPO, "pr_number": 8, "head_sha": H8_OLD},
        {"repo": REPO, "pr_number": 12, "head_sha": H12}])

    class Args:
        pass
    Args.legacy, Args.inventory, Args.db = legacy, art, tmp_path / "p.sqlite3"
    result = migrate.run(Args)
    assert result["mapped"] == 2 and result["unmapped"] == 0
    assert result["legacy_store_modified"] is False
    by_epoch = {r["legacy_epoch"]: r for r in result["results"]}
    assert by_epoch["bootstrap-8aeafa9c28b9"]["pr_number"] == 8
    assert by_epoch["bootstrap-e29621f54a63"]["pr_number"] == 12
    assert "matches exactly one" in by_epoch["bootstrap-8aeafa9c28b9"]["justification"]


def test_migration_refuses_when_order_is_the_only_signal(tmp_path):
    """Two rows and two PRs that line up positionally, but whose heads do
    not match anything. Positional pairing would have 'worked'."""
    legacy = _legacy(tmp_path, [
        (1, "bootstrap-aaa", "a" * 40, "NOT_ESTABLISHED", "t1"),
        (2, "bootstrap-bbb", "b" * 40, "NOT_ESTABLISHED", "t2")])
    art = _artifact(tmp_path, [
        {"repo": REPO, "pr_number": 8, "head_sha": H8_OLD},
        {"repo": REPO, "pr_number": 12, "head_sha": H12}])

    class Args:
        pass
    Args.legacy, Args.inventory, Args.db = legacy, art, tmp_path / "p.sqlite3"
    result = migrate.run(Args)
    assert result["mapped"] == 0 and result["unmapped"] == 2


def test_migration_refuses_an_abbreviated_legacy_head(tmp_path):
    legacy = _legacy(tmp_path, [(1, "e", "2d834870", "NOT_ESTABLISHED", "t")])
    art = _artifact(tmp_path, [{"repo": REPO, "pr_number": 8, "head_sha": H8}])

    class Args:
        pass
    Args.legacy, Args.inventory, Args.db = legacy, art, tmp_path / "p.sqlite3"
    result = migrate.run(Args)
    assert result["unmapped"] == 1
    assert "refusing prefix matching" in result["results"][0]["justification"]


def test_migration_does_not_write_to_the_legacy_store(tmp_path):
    legacy = _legacy(tmp_path, [
        (1, "bootstrap-x", H8_OLD, "NOT_ESTABLISHED", "t1")])
    before = legacy.read_bytes()
    art = _artifact(tmp_path, [{"repo": REPO, "pr_number": 8,
                                "head_sha": H8_OLD}])

    class Args:
        pass
    Args.legacy, Args.inventory, Args.db = legacy, art, tmp_path / "p.sqlite3"
    migrate.run(Args)
    assert legacy.read_bytes() == before


# --- the carrier lifecycle -----------------------------------------------------

def good_run(head=H8, run_id=1, conclusion="failure", summary=None):
    return {"id": run_id, "head_sha": head, "name": "ai/final-review",
            "app": {"id": 4669438}, "conclusion": conclusion,
            "output": {"summary": carrier.SUMMARY if summary is None else summary}}


def fake_request(state):
    calls = {"post": 0, "get": 0}

    def request(method, path, token, body=None):
        if method == "GET":
            calls["get"] += 1
            nxt = state.pop(0)
            return (500, None) if nxt is None else (200, {"check_runs": nxt})
        calls["post"] += 1
        return 201, {"id": 99}
    return request, calls


def test_zero_carriers_posts_exactly_once_and_confirms(store):
    request, calls = fake_request([[], [good_run(run_id=99)]])
    r = carrier.ensure(request, REPO, 8, H8, "tok", store)
    assert r["state"] == "CONFIRMED" and r["carrier"] == 99
    assert calls["post"] == 1
    assert store.projection(r["epoch_id"])["state"] == "CONFIRMED"


def test_exactly_one_valid_carrier_is_adopted_without_writing(store):
    request, calls = fake_request([[good_run()]])
    r = carrier.ensure(request, REPO, 8, H8, "tok", store)
    assert r["state"] == "ADOPTED" and r["wrote"] is False
    assert calls["post"] == 0


def test_two_carriers_stop_without_writing(store):
    request, calls = fake_request([[good_run(run_id=1), good_run(run_id=2)]])
    r = carrier.ensure(request, REPO, 8, H8, "tok", store)
    assert r["state"] == "AMBIGUOUS" and calls["post"] == 0


def test_unreadable_carriers_stop_without_writing(store):
    request, calls = fake_request([None])
    r = carrier.ensure(request, REPO, 8, H8, "tok", store)
    assert r["state"] == "OUTCOME_UNKNOWN" and calls["post"] == 0


def test_a_foreign_carrier_is_not_adopted(store):
    """Adopting a run this producer did not author would claim somebody
    else's state as its own verdict."""
    request, calls = fake_request([[good_run(conclusion="cancelled")]])
    r = carrier.ensure(request, REPO, 8, H8, "tok", store)
    assert r["state"] == "MISMATCH" and calls["post"] == 0


def test_a_carrier_on_the_old_head_does_not_satisfy_the_new_one(store):
    request, calls = fake_request([[good_run(head=H8_OLD)],
                                   [good_run(run_id=99)]])
    r = carrier.ensure(request, REPO, 8, H8, "tok", store)
    assert r["state"] == "CONFIRMED", "old-head evidence must not be adopted"
    assert calls["post"] == 1


def test_lost_readback_after_one_post_is_not_retried(store):
    request, calls = fake_request([[], None])
    r = carrier.ensure(request, REPO, 8, H8, "tok", store)
    assert r["state"] == "OUTCOME_UNKNOWN"
    assert r["retry_performed"] is False
    assert calls["post"] == 1


@pytest.mark.parametrize("body", [
    {"name": "ai/final-review", "conclusion": "success"},
    {"name": "ai/final-review", "conclusion": "neutral"},
    {"name": "other", "conclusion": "failure"},
])
def test_carrier_producer_cannot_publish_anything_else(body):
    with pytest.raises(carrier.CarrierCapability):
        carrier.guarded(lambda *a, **k: None, "POST", "/repos/x/check-runs",
                        "t", body)


# --- ACCEPT-CANDIDATE ----------------------------------------------------------

def ok_checks(permission, **over):
    base = {"draft": False, "base_current": True, "ruleset_verified": True,
            "carrier": {"state": "CONFIRMED", "head_sha": H8},
            "permission": permission, "open_generations": []}
    base.update(over)
    return base


def test_acceptance_requires_every_condition(fresh):
    r = accept.accept(repo=REPO, pr_number=8, head_sha=H8, **ok_checks(fresh))
    assert r["state"] == accept.ACCEPTED
    assert r["provider_round"] == "NOT_STARTED"


@pytest.mark.parametrize("override,fragment", [
    ({"draft": True}, "draft"),
    ({"base_current": False}, "not current with its intended base"),
    ({"ruleset_verified": False}, "ruleset is not verified"),

    ({"carrier": {"state": "ABSENT"}}, "no CONFIRMED failure carrier"),
    ({"carrier": {"state": "CONFIRMED", "head_sha": H8_OLD}},
     "bound to a different head"),
    ({"open_generations": [{"head_sha": H8_OLD}]}, "open generation"),
])
def test_each_precondition_refuses_separately(override, fragment, fresh):
    r = accept.accept(repo=REPO, pr_number=8, head_sha=H8,
                      **ok_checks(fresh, **override))
    assert r["state"] == accept.REFUSED
    assert any(fragment in f for f in r["failures"]), r["failures"]


def test_head_move_invalidates_and_never_repoints(fresh):
    a = accept.accept(repo=REPO, pr_number=8, head_sha=H8, **ok_checks(fresh))
    v = accept.still_valid(a, current_head_sha="f" * 40)
    assert v["valid"] is False and v["state"] == accept.INVALIDATED
    assert "not a re-pointing" in v["required_action"]
    assert a["head_sha"] == H8, "the acceptance itself must be unchanged"


def test_no_function_repoints_an_acceptance():
    source = (HERE / "harness" / "accept.py").read_text()
    for name in ("def refresh", "def rebind", "def repoint", "def update_head"):
        assert name not in source


# --- provider lineage ----------------------------------------------------------

def test_a_request_must_name_the_head_it_is_about(fresh):
    with pytest.raises(lineage.LineageError):
        lineage.request(repo=REPO, pr_number=8, provider="codex",
                        requested_for_head="2d834870", generation=1,
                        accepted_at="t", permission=fresh)


def test_a_lost_request_response_is_outcome_unknown(fresh):
    r = lineage.request(repo=REPO, pr_number=8, provider="codex",
                        requested_for_head=H8, generation=1, accepted_at="t", permission=fresh)
    assert r["state"] == lineage.OUTCOME_UNKNOWN


def test_attestation_and_binding_stay_separate(fresh):
    """A1b-c3: a comment mentioning a SHA has not been bound to it."""
    r = lineage.request(repo=REPO, pr_number=8, provider="codex",
                        requested_for_head=H8, generation=1, accepted_at="t", permission=fresh,
                        request_carrier_id=5)
    r = lineage.attest(r, carrier_id=9, carrier_head_claim=H8,
                       carrier_updated_at="t", current_head=H8)
    assert r["attestation"]["TERMINAL_HEAD_ATTESTATION_MATCH"] is True
    assert r["attestation"]["AUTHORITATIVE_HEAD_BINDING"] is False


def test_evidence_for_an_old_head_does_not_qualify(fresh):
    r = lineage.request(repo=REPO, pr_number=8, provider="codex",
                        requested_for_head=H8_OLD, generation=1,
                        accepted_at="t", permission=fresh, request_carrier_id=5)
    r = lineage.attest(r, carrier_id=9, carrier_head_claim=H8_OLD,
                       carrier_updated_at="t", current_head=H8)
    r = lineage.qualify(r, current_head=H8)
    assert r["qualification"]["qualified"] is False
    assert r["state"] == lineage.STALE


def test_no_module_here_can_post_a_provider_request():
    import re
    for path in (HERE / "harness").glob("*.py"):
        source = path.read_text()
        assert not re.search(r'\(\s*"POST"\s*,\s*f?"[^"]*/issues/', source), path.name
        assert not re.search(r'\(\s*"POST"\s*,\s*f?"[^"]*/comments', source), path.name


# --- the reducer and the positive path ----------------------------------------

def qualified_records(permission, head=H8):
    """A6f shape: admissibility and a provider predicate, not the A6a
    boolean. The old helper produced records whose `qualified` said nothing
    about findings, which is precisely the defect A6f closed."""
    import collector
    import predicates
    out = []
    for provider in ("codex", "coderabbit"):
        r = lineage.request(repo=REPO, pr_number=8, provider=provider,
                            requested_for_head=head, generation=1,
                            accepted_at="t", permission=permission,
                            request_carrier_id=1)
        r = lineage.attest(r, carrier_id=2, carrier_head_claim=head,
                           carrier_updated_at="t", current_head=head)
        r = lineage.qualify(r, current_head=head)
        r["request_id"] = "req-x"
        r["request_carrier_id"] = 1
        r["terminal"] = {"carrier_id": 2, "state": collector.ADMISSIBLE,
                         "admissible": True}
        r["predicate"] = predicates.evaluate(provider, {
            "id": 2, "body": "no issues found", "review_ran": True,
            "findings": [], "head_claim": head})
        out.append(r)
    return out


def test_the_positive_path_exists_end_to_end(store, fresh):
    """The whole point of A6a: a success must be reachable before a review
    begins, or the system starts work it cannot finish."""
    bundle = evidence.build_bundle(repo=REPO, pr_number=8, head_sha=H8,
                                   lineage_records=qualified_records(fresh),
                                   auth_generation=4)
    reduction = evidence.reduce(bundle, current_head_sha=H8, permission=fresh,
                                auth_generation=4)
    assert reduction["verdict"] == evidence.SUCCESS

    e = store.open_epoch(repo=REPO, pr_number=8, head_sha=H8, opened_at="t")
    seen = {}

    def request(method, path, body=None):
        if method == "GET":
            return 200, {"id": 77, "name": "ai/final-review",
                         "app": {"id": 4669438}, "head_sha": H8,
                         "external_id": e["epoch_id"], "conclusion": "success"}
        seen["method"] = method
        return 200, {}

    r = publish.publish(request, repo=REPO, epoch_id=e["epoch_id"],
                        head_sha=H8, conclusion="success", bundle=bundle,
                        reduction=reduction, current_head_sha=H8,
                        permission=fresh, store=store, existing_run=77,
                        health={"observations": {n: {"state": "FRESH"} for n in
                                 ("runtime", "reconciliation", "watchdog")},
                                "all_fresh": True, "not_fresh": []})
    assert r["state"] == "CONFIRMED" and r["observed"] == "success"
    assert store.projection(e["epoch_id"])["state"] == "CONFIRMED"


@pytest.mark.parametrize("kwargs,fragment", [
    ({"current_head_sha": "f" * 40}, "no longer current"),
    ({"auth_generation": 5}, "auth generation"),
])
def test_stale_or_unauthorized_cannot_reduce_to_success(kwargs, fragment,
                                                        fresh):
    bundle = evidence.build_bundle(repo=REPO, pr_number=8, head_sha=H8,
                                   lineage_records=qualified_records(fresh),
                                   auth_generation=4)
    base = {"current_head_sha": H8, "permission": fresh, "auth_generation": 4}
    base.update(kwargs)
    reduction = evidence.reduce(bundle, **base)
    assert reduction["verdict"] == evidence.NOT_ESTABLISHED
    assert any(fragment in r for r in reduction["refusals"]), reduction


def test_a_stale_permission_cannot_reduce_to_success(tmp_path, fresh):
    """The reducer is the layer below the guard, and the same boolean trap
    lived here too."""
    bundle = evidence.build_bundle(repo=REPO, pr_number=8, head_sha=H8,
                                   lineage_records=qualified_records(fresh),
                                   auth_generation=4)
    reduction = evidence.reduce(bundle, current_head_sha=H8,
                                permission=stale_permission(tmp_path),
                                auth_generation=4)
    assert reduction["verdict"] == evidence.NOT_ESTABLISHED
    assert any("STALE" in r for r in reduction["refusals"])


def test_missing_a_provider_cannot_reduce_to_success(fresh):
    records = qualified_records(fresh)[:1]
    bundle = evidence.build_bundle(repo=REPO, pr_number=8, head_sha=H8,
                                   lineage_records=records, auth_generation=4)
    reduction = evidence.reduce(bundle, current_head_sha=H8, permission=fresh,
                                auth_generation=4)
    assert reduction["verdict"] == evidence.NOT_ESTABLISHED


def test_ambiguous_generations_cannot_reduce_to_success(fresh):
    records = qualified_records(fresh) + qualified_records(fresh)
    for i, r in enumerate(records):
        r["generation"] = i + 1
    bundle = evidence.build_bundle(repo=REPO, pr_number=8, head_sha=H8,
                                   lineage_records=records, auth_generation=4)
    reduction = evidence.reduce(bundle, current_head_sha=H8, permission=fresh,
                                auth_generation=4)
    assert reduction["verdict"] == evidence.NOT_ESTABLISHED
    assert any("ambiguous" in r for r in reduction["refusals"])


def test_a_bundle_must_be_bound_to_a_full_head():
    with pytest.raises(evidence.BundleError):
        evidence.build_bundle(repo=REPO, pr_number=8, head_sha="2d834870",
                              lineage_records=[], auth_generation=4)


# --- publication guards --------------------------------------------------------

def test_guard_refuses_success_when_the_head_moved(store, fresh):
    bundle = evidence.build_bundle(repo=REPO, pr_number=8, head_sha=H8,
                                   lineage_records=qualified_records(fresh),
                                   auth_generation=4)
    reduction = evidence.reduce(bundle, current_head_sha=H8, permission=fresh,
                                auth_generation=4)
    e = store.open_epoch(repo=REPO, pr_number=8, head_sha=H8, opened_at="t")
    with pytest.raises(publish.PublishRefused):
        publish.publish(lambda *a, **k: (201, {"id": 1}), repo=REPO,
                        epoch_id=e["epoch_id"], head_sha=H8,
                        conclusion="success", bundle=bundle,
                        reduction=reduction, current_head_sha="f" * 40,
                        permission=fresh, store=store)


@pytest.mark.parametrize("conclusion", ["neutral", "skipped"])
def test_conclusions_that_read_as_passing_are_excluded(conclusion, store):
    e = store.open_epoch(repo=REPO, pr_number=8, head_sha=H8, opened_at="t")
    with pytest.raises(publish.PublishRefused):
        publish.publish(lambda *a, **k: (201, {"id": 1}), repo=REPO,
                        epoch_id=e["epoch_id"], head_sha=H8,
                        conclusion=conclusion, bundle={}, reduction={},
                        current_head_sha=H8, permission=fresh, store=store)


def test_failure_needs_no_guard(store, fresh):
    """Refusing to fail closed while unauthorized would strand a green
    check exactly when nobody is watching it."""
    e = store.open_epoch(repo=REPO, pr_number=8, head_sha=H8, opened_at="t")

    def request(method, path, body=None):
        if method == "GET":
            return 200, {"id": 5, "name": "ai/final-review",
                         "app": {"id": 4669438}, "head_sha": H8,
                         "external_id": e["epoch_id"], "conclusion": "failure"}
        return 201, {"id": 5}

    r = publish.publish(request, repo=REPO, epoch_id=e["epoch_id"],
                        head_sha=H8, conclusion="failure",
                        bundle={"head_sha": H8, "schema": "s",
                                "bundle_hash": "h"},
                        reduction={"verdict": "NOT_ESTABLISHED"},
                        current_head_sha=H8, permission=fresh, store=store)
    assert r["state"] == "CONFIRMED"


def test_production_projection_is_not_the_probe_module():
    """Renaming CONTEXT in governor.py would have made a probe instrument
    into a production gate by editing a string.

    Asserted against code with docstrings blanked: the module explains
    which probe machinery it is deliberately not, so a prose ban would
    forbid it from saying so."""
    import ast
    tree = ast.parse((HERE / "harness" / "publish.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            node.value.value = ""
    code = ast.unparse(tree)
    assert "ReadinessProbeEvidence" not in code
    assert "readiness-probe" not in code
    assert "ai/final-review" in code
    assert "PRODUCTION_CONTEXT" in code


# --- scoped reconciliation -----------------------------------------------------

def reconcile_request(head, runs=()):
    def request(method, path, body=None):
        if "/pulls/" in path:
            return 200, {"head": {"sha": head}}
        return 200, {"check_runs": list(runs)}
    return request


def test_reconciliation_compares_and_says_that_it_did(store):
    store.open_epoch(repo=REPO, pr_number=8, head_sha=H8, opened_at="t")
    r = sr.reconcile(reconcile_request(H8), REPO, 8, store)
    assert r["comparison_performed"] is True
    assert r["drift_detected"] is False
    assert r["stored_pr_number"] == 8 and r["stored_repo"] == REPO


def test_reconciliation_detects_real_drift(store):
    store.open_epoch(repo=REPO, pr_number=8, head_sha=H8_OLD, opened_at="t")
    r = sr.reconcile(reconcile_request(H8), REPO, 8, store)
    assert r["drift_detected"] is True
    assert r["stored_head"] == H8_OLD


def test_unresolvable_scope_never_reports_no_drift(store):
    store.record_migration(legacy_epoch="x", legacy_head="y" * 40,
                           mapped_to=None, justification="j",
                           source_artifact="a", at="t")
    r = sr.reconcile(reconcile_request(H8), REPO, 8, store)
    assert r["scope_state"] == ep.UNRESOLVED
    assert r["comparison_performed"] is False
    assert r["drift_detected"] is None, "None means unknown, not False"


def test_no_epoch_never_reports_no_drift(store):
    r = sr.reconcile(reconcile_request(H8), REPO, 8, store)
    assert r["comparison_performed"] is False
    assert r["drift_detected"] is None


def test_drift_is_only_reported_beside_the_fact_it_was_computed():
    source = (HERE / "harness" / "scoped_reconcile.py").read_text()
    assert source.count("comparison_performed") >= 4


def test_another_prs_epoch_cannot_answer_for_this_one(store):
    store.open_epoch(repo=REPO, pr_number=12, head_sha=H12, opened_at="t")
    r = sr.reconcile(reconcile_request(H8), REPO, 8, store)
    assert r["scope_state"] == ep.NO_EPOCH
    assert r["stored_head"] is None
