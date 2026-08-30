"""Shared fixtures. The helpers here go through the same public methods
production does — a test that reaches past them is testing a different
system."""
import datetime
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "harness"))
sys.path.insert(0, str(HERE.parents[0] / "operational-readiness" / "harness"))

import auth_policy as ap  # noqa: E402
import auth_state  # noqa: E402
import epochs as ep  # noqa: E402
import rounds  # noqa: E402
import snapshots  # noqa: E402

REPO = "PhysShell/evm-from-scratch"
A = "7ad19f5e72a13a8fbd10ba9f6a2b0ea4bf430f52"
B = "b" * 40
EPOCH = "pe-5a41d21a944c13836e1cc6ff"
RULESET = 21640654
CARRIER_RUN = 99104297860


def now_stamp(delta=0):
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(seconds=delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture()
def store(tmp_path):
    s = rounds.RoundStore(tmp_path / "rounds.sqlite3")
    yield s
    s.close()


@pytest.fixture()
def snaps(tmp_path):
    s = snapshots.SnapshotStore(tmp_path / "snapshots.sqlite3")
    yield s
    s.close()


@pytest.fixture()
def epochs(tmp_path):
    s = ep.EpochStore(tmp_path / "epochs.sqlite3")
    yield s
    s.close()


def permission_at(path, *, generation=5, observed_at=None, state="AUTHORIZED"):
    a = auth_state.AuthStore(path)
    a.record(state=state, auth_generation=generation,
             observed_at=observed_at or now_stamp(), source="refresh")
    out = ap.evaluate(a)
    a.close()
    return out


@pytest.fixture()
def fresh(tmp_path):
    return permission_at(tmp_path / "auth.sqlite3")


@pytest.fixture()
def stale(tmp_path):
    return permission_at(tmp_path / "stale-auth.sqlite3",
                         observed_at="2020-01-01T00:00:00Z")


def record_observation(store, *, head=A, pr=32, draft=False, base_ref="main",
                       ruleset_verified=True, carrier_state="CONFIRMED",
                       carrier_head=None, run_id=CARRIER_RUN, repo=REPO):
    """The durable reading the acceptance writer gates over."""
    return store.record_observation(
        repo=repo, pr_number=pr, head_sha=head, draft=draft, base_ref=base_ref,
        pr_state="open", ruleset_id=RULESET, ruleset_verified=ruleset_verified,
        carrier={"state": carrier_state,
                 "head_sha": head if carrier_head is None else carrier_head,
                 "check_run_id": run_id})


def accept(store, permission, *, head=A, pr=32, epoch_id=EPOCH, repo=REPO,
           observation=None, **observation_kwargs):
    """An acceptance written the way production writes one.

    There is no shortcut past the gate any more: the store loads the
    observation and evaluates it, so a test that wants a refusal changes
    what was observed rather than what it asserts.
    """
    obs = observation or record_observation(
        store, head=head, pr=pr, repo=repo, **observation_kwargs)
    return store.record_acceptance(
        repo=repo, pr_number=pr, epoch_id=epoch_id, head_sha=head,
        permission=permission, observation_id=obs["observation_id"])


def captured_baseline(snaps, comments=(), *, provider="coderabbit", pr=32,
                      read_ok=True, repo=REPO, captured_at=None):
    import parsers
    import triggers
    app = triggers.PROVIDER_IDENTITY[provider]["app_id"]
    return snaps.capture_baseline(
        repo=repo, pr_number=pr, provider=provider, read_ok=read_ok,
        payload=parsers.baseline(list(comments), provider_app=app),
        captured_at=captured_at or now_stamp(-5))


def flat_baseline(row):
    """The shape the parsers take: the payload plus its provenance."""
    return {**row["payload"], "baseline_id": row["baseline_id"],
            "read_ok": row["read_ok"], "captured_at": row["captured_at"]}


def intent(store, snaps, permission, *, acceptance, provider="codex",
           generation=1, head=A, pr=32, repo=REPO, baseline=None):
    baseline = baseline or captured_baseline(snaps, provider=provider, pr=pr,
                                             repo=repo)
    return store.record_intent(
        acceptance_id=acceptance["acceptance_id"], repo=repo, pr_number=pr,
        provider=provider, generation=generation, requested_for_head=head,
        permission=permission, baseline=baseline)
