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


MAIN = "047ff1a641e33e0bb8c6b9eea26bb80eea021e08"
GOVERNOR_APP = 4669438


class FakeGitHub:
    """Answers the endpoints the driver chooses to call, in GitHub's shapes.

    Since A6f-c4 there is no way to write an observation except by serving
    these responses, which is the point: a test that wants a failing
    precondition changes what GitHub says, not what the test asserts.
    """

    def __init__(self, *, head=A, pr=32, draft=False, base_ref="main",
                 base_sha=MAIN, merge_base=None, pr_state="open",
                 enforcement="active", ruleset_id=RULESET, ruleset_over=None,
                 bypass_visible=False, carrier_runs=None,
                 carrier_conclusion="failure", carrier_status="completed",
                 carrier_head=None, carrier_external_id=EPOCH,
                 coderabbit_answers=True, codex_answers="reaction",
                 epoch_id=EPOCH):
        self.head, self.pr, self.draft = head, pr, draft
        self.base_ref, self.base_sha = base_ref, base_sha
        self.merge_base = base_sha if merge_base is None else merge_base
        self.pr_state, self.epoch_id = pr_state, epoch_id
        self.ruleset_id, self.enforcement = ruleset_id, enforcement
        self.ruleset_over = ruleset_over or {}
        self.bypass_visible = bypass_visible
        self.coderabbit_answers = coderabbit_answers
        self.codex_answers = codex_answers
        self.calls, self.patched = [], []
        self.next_comment_id = 6000
        self.comments = [dict(STICKY_BEFORE_CARRIER)]
        self.reactions = {}
        self.check_runs = carrier_runs if carrier_runs is not None else [{
            "id": CARRIER_RUN, "name": "ai/final-review",
            "app": {"id": GOVERNOR_APP},
            "head_sha": carrier_head or head, "external_id": carrier_external_id,
            "status": carrier_status, "conclusion": carrier_conclusion}]

    # -- transport -------------------------------------------------------
    def read(self, method, path):
        return self.request(method, path, None)

    def post(self, path, body):
        return self.request("POST", path, body)

    def ruleset_object(self):
        import observation as obs_mod
        canonical = obs_mod.canonical_visible_ruleset(self.enforcement)
        obj = {**canonical, "id": self.ruleset_id, "node_id": "RRS_x",
               "source": REPO, "source_type": "Repository",
               "created_at": "t", "updated_at": "t", "_links": {},
               "current_user_can_bypass": "never"}
        if self.bypass_visible:
            obj["bypass_actors"] = []
        obj.update(self.ruleset_over)
        return obj

    def request(self, method, path, body=None):
        self.calls.append((method, path))
        if method == "GET" and path == f"/repos/{REPO}/pulls/{self.pr}":
            return 200, {"number": self.pr, "head": {"sha": self.head},
                         "draft": self.draft, "base": {"ref": self.base_ref},
                         "state": self.pr_state}
        if method == "GET" and path == f"/repos/{REPO}/commits/{self.base_ref}":
            return 200, {"sha": self.base_sha}
        if method == "GET" and "/compare/" in path:
            return 200, {"status": "ahead" if self.merge_base == self.base_sha
                         else "diverged",
                         "merge_base_commit": {"sha": self.merge_base},
                         "ahead_by": 1,
                         "behind_by": 0 if self.merge_base == self.base_sha else 3}
        if method == "GET" and "/rulesets/" in path:
            rid = int(path.rsplit("/", 1)[1])
            return (200, self.ruleset_object()) if rid == self.ruleset_id \
                else (404, None)
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

    def _governor_asks(self, body):
        """The Governor's request comment, and the provider's reply to it."""
        self.next_comment_id += 1
        cid = self.next_comment_id
        text = body["body"]
        self.comments.append({
            "id": cid, "body": text,
            "user": {"login": "PhysShell", "id": 111},
            "performed_via_github_app": {"id": GOVERNOR_APP},
            "created_at": now_stamp(), "updated_at": now_stamp()})
        answered_at = now_stamp(5)
        import triggers
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
                    "id": cid + 500, "body": f"Found 2 issues in {A}",
                    "user": {"login": "chatgpt-codex-connector[bot]",
                             "id": 199175422},
                    "performed_via_github_app": {"id": 1144995},
                    "created_at": answered_at, "updated_at": answered_at})
        return 201, {"id": cid}


STICKY_ID = 5349895008
SKIP_RUN = "a3d2af24-8685-49a2-9e6e-728a59d8dcd4"
REVIEW_RUN = "a765cb7e-2018-4a07-b66f-66539b83f8cd"
BASE_COMMIT = "add0a0975eb499491eefe9f83d971152153d8106"

STICKY_BEFORE = (
    "<!-- This is an auto-generated comment: skip review by coderabbit.ai -->\n"
    "> This repository does not receive automatic reviews.\n"
    f"**Run ID**: `{SKIP_RUN}`\n"
    "<!-- end of auto-generated comment: skip review by coderabbit.ai -->\n")

STICKY_AFTER = STICKY_BEFORE + (
    "**Actionable comments posted: 0**\n"
    f"Reviewing files that changed from the base of the PR and between "
    f"{BASE_COMMIT} and {A}.\n"
    f"**Run ID**: `{REVIEW_RUN}`\n"
    "Example uuid quoted from the diff: 6ba7b810-9dad-11d1-80b4-00c04fd430c8\n")

STICKY_BEFORE_CARRIER = {
    "id": STICKY_ID, "body": STICKY_BEFORE,
    "user": {"login": "coderabbitai[bot]", "id": 136622811},
    "performed_via_github_app": {"id": 347564},
    "created_at": "2026-08-20T01:03:30Z",
    "updated_at": "2026-08-20T01:03:30Z"}


def record_observation(store, *, github=None, pr=32, repo=REPO,
                       epoch_id=EPOCH, **github_kwargs):
    """The durable reading the acceptance writer gates over.

    Takes a fake GitHub rather than field values: since A6f-c4 the store
    performs the reads, so there is no way to write a reading that was not
    made.
    """
    gh = github or FakeGitHub(pr=pr, **github_kwargs)
    return store.record_observation(gh.read, repo=repo, pr_number=pr,
                                    epoch_id=epoch_id, ruleset_id=gh.ruleset_id)


def accept(store, permission, *, head=A, pr=32, epoch_id=EPOCH, repo=REPO,
           observation=None, github=None, **github_kwargs):
    """An acceptance written the way production writes one.

    There is no shortcut past the gate any more: the store loads the
    observation and evaluates it, so a test that wants a refusal changes
    what GitHub returned rather than what it asserts.
    """
    obs = observation or record_observation(
        store, github=github, pr=pr, repo=repo, epoch_id=epoch_id,
        head=head, **github_kwargs)
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
