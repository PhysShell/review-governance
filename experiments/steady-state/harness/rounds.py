"""Durable acceptances and provider requests. Append-only, scoped.

A6a returned an acceptance as a Python dict and serialised part of it on
request. That is not a transition — nothing survived the process, so
"ACCEPT-CANDIDATE recorded durably" was a sentence in a docstring rather
than a fact on disk.

Two relations, both append-only:

    acceptances       one commit, one authorization observation
    provider_requests intent recorded BEFORE any network call

The ordering in the second is the whole safety property. A request whose
POST response was lost may or may not have reached the provider, and the
only way to tell afterwards is to have written down that we were about to
try. A row written after a successful post is a log; a row written before
is a record.

An acceptance is never updated. A head move produces a new acceptance or
none, and `state` moves only through append: there is no UPDATE anywhere
in this module and the triggers enforce it.
"""
import datetime
import hashlib
import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    observation_id      TEXT PRIMARY KEY,
    repo                TEXT NOT NULL,
    pr_number           INTEGER NOT NULL,
    epoch_id            TEXT,
    head_sha            TEXT NOT NULL,
    -- Readbacks, not conclusions. There is no `ruleset_verified` column
    -- any more: a load-bearing boolean is a verdict nobody can re-check,
    -- and the gate derives its answer from the facts below.
    facts               TEXT NOT NULL,
    reads               TEXT NOT NULL,
    observed_at         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS acceptances (
    acceptance_id       TEXT PRIMARY KEY,
    repo                TEXT NOT NULL,
    pr_number           INTEGER NOT NULL,
    epoch_id            TEXT NOT NULL,
    head_sha            TEXT NOT NULL,
    accepted_at         TEXT NOT NULL,
    auth_observation_id INTEGER,
    auth_generation     INTEGER,
    observation_id      TEXT NOT NULL,
    carrier_run_id      INTEGER,
    ruleset_id          INTEGER,
    state               TEXT NOT NULL,
    -- One acceptance per reading. Keying on `accepted_at` collapsed two
    -- acceptances written in the same second, which is exactly the
    -- A -> B -> A case: a genuinely new acceptance of a genuinely new
    -- observation, refused because a clock had one-second resolution.
    UNIQUE (repo, pr_number, head_sha, observation_id)
);
CREATE TABLE IF NOT EXISTS provider_requests (
    request_id           TEXT PRIMARY KEY,
    acceptance_id        TEXT NOT NULL,
    repo                 TEXT NOT NULL,
    pr_number            INTEGER NOT NULL,
    provider             TEXT NOT NULL,
    generation           INTEGER NOT NULL,
    requested_for_head   TEXT NOT NULL,
    auth_observation_id  INTEGER,
    auth_generation      INTEGER,
    baseline_id          TEXT NOT NULL,
    baseline_digest      TEXT NOT NULL,
    baseline_captured_at TEXT NOT NULL,
    intent_recorded_at   TEXT NOT NULL,
    request_carrier_id   INTEGER,
    request_outcome      TEXT NOT NULL,
    outcome_recorded_at  TEXT,
    UNIQUE (acceptance_id, provider, generation)
);
CREATE TRIGGER IF NOT EXISTS observations_no_update
BEFORE UPDATE ON observations
BEGIN SELECT RAISE(ABORT, 'an observation records what was read and cannot be rewritten'); END;
CREATE TRIGGER IF NOT EXISTS observations_no_delete
BEFORE DELETE ON observations
BEGIN SELECT RAISE(ABORT, 'observations are append-only'); END;
CREATE TRIGGER IF NOT EXISTS requests_no_baseline_update
BEFORE UPDATE OF baseline_id, baseline_digest, baseline_captured_at
ON provider_requests
BEGIN SELECT RAISE(ABORT, 'a request is bound to the capture that preceded it'); END;
CREATE TRIGGER IF NOT EXISTS acceptances_no_delete
BEFORE DELETE ON acceptances
BEGIN SELECT RAISE(ABORT, 'acceptances are append-only'); END;
CREATE TRIGGER IF NOT EXISTS acceptances_no_resurrection
BEFORE UPDATE OF state ON acceptances
WHEN OLD.state IN ('INVALIDATED', 'TERMINATED') AND NEW.state = 'ACCEPTED'
BEGIN SELECT RAISE(ABORT, 'a terminated acceptance does not return to ACCEPTED; reopening the PR on the same commit requires a fresh reading and a fresh acceptance'); END;
CREATE TRIGGER IF NOT EXISTS acceptances_no_scope_update
BEFORE UPDATE OF repo, pr_number, head_sha, epoch_id ON acceptances
BEGIN SELECT RAISE(ABORT, 'an acceptance is about one commit and cannot be repointed'); END;
CREATE TRIGGER IF NOT EXISTS requests_no_delete
BEFORE DELETE ON provider_requests
BEGIN SELECT RAISE(ABORT, 'provider requests are append-only'); END;
CREATE TRIGGER IF NOT EXISTS requests_no_intent_update
BEFORE UPDATE OF requested_for_head, provider, generation, acceptance_id
ON provider_requests
BEGIN SELECT RAISE(ABORT, 'recorded intent cannot be rewritten'); END;
"""

#: How old a GitHub reading may be when it authorises an acceptance.
#:
#: The same shape as the 60-second authorization bound from A6c, and for
#: the same reason: a stored fact that was true at some point is not a
#: statement about now. Without it, a fresh OAuth permission could be
#: paired with an arbitrarily old observation.
OBSERVATION_MAX_AGE_SECONDS = 60

ACCEPTED = "ACCEPTED"
INVALIDATED = "INVALIDATED"
#: The transition A6g-c1 found missing. Closing a PR removed it from the
#: runtime's view — which lists open PRs — before the acceptance about it
#: had any terminal state, so cleanup would have deleted the object and
#: left the permission standing.
TERMINATED = "TERMINATED"
TERMINAL_STATES = (INVALIDATED, TERMINATED)

INTENT_RECORDED = "INTENT_RECORDED"
SENT = "SENT"
OUTCOME_UNKNOWN = "REQUEST_OUTCOME_UNKNOWN"


class RoundError(Exception):
    """Raised where a record would be written without provable scope."""


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ident(prefix, payload):
    return prefix + hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24]


def _age_seconds(stamp, now=None):
    try:
        at = datetime.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return None
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return round((now - at).total_seconds())


class RoundStore:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._migrate()
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def _migrate(self):
        """A6f-c3 added columns `CREATE TABLE IF NOT EXISTS` will not add.

        An empty legacy table is recreated; a populated one is not touched
        and the store refuses to open. Silently running new code against a
        table missing the columns that carry the new proofs would produce
        rows that look complete and are not.
        """
        wanted = {
            "acceptances": {"observation_id", "carrier_run_id", "ruleset_id"},
            "provider_requests": {"baseline_id", "baseline_digest",
                                  "baseline_captured_at"},
            # Readings expire by construction — the acceptance bound is 60
            # seconds — so a legacy observations table holds nothing an
            # acceptance could still use. It is archived beside the store
            # rather than dropped in silence.
            "observations": {"facts", "reads", "epoch_id"},
        }
        for table, columns in wanted.items():
            present = {r[1] for r in self.conn.execute(
                f"PRAGMA table_info({table})")}
            if not present or columns <= present:
                continue
            rows = self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if rows and table == "observations":
                archive = Path(self.path).with_suffix(".retired-observations.json")
                archive.write_text(json.dumps(
                    [dict(r) for r in self.conn.execute(
                        f"SELECT * FROM {table}")], indent=2, default=str) + "\n")
                rows = 0
            if rows:
                raise RoundError(
                    f"{self.path}: table {table} predates A6f-c3 and holds "
                    f"{rows} rows; missing {sorted(columns - present)}. "
                    "Refusing to open: a migration that drops recorded "
                    "evidence is not a migration.")
            for trigger in self.conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger' "
                    "AND tbl_name=?", (table,)).fetchall():
                self.conn.execute(f"DROP TRIGGER {trigger[0]}")
            self.conn.execute(f"DROP TABLE {table}")
        self.conn.commit()

    def close(self):
        self.conn.close()

    # --- observations -----------------------------------------------------
    def record_observation(self, read, *, repo, pr_number, epoch_id,
                           ruleset_id=None, base=None):
        """Perform the reading, then write it. There is no other order.

        The previous signature took `head_sha`, `draft`, `ruleset_verified`
        and `carrier` from the caller, so an immutable row could be written
        from four supplied values and an acceptance faithfully re-derived
        from it. That is `preconditions=[]` moved one table to the right.

        `read` is the transport, the same injection point as `post` and
        `patch`. Every semantic value is computed from what it returns.
        """
        import observation as obs_mod
        kw = {}
        if ruleset_id is not None:
            kw["ruleset_id"] = ruleset_id
        if base is not None:
            kw["base"] = base
        facts = obs_mod.read_live(read, repo=repo, pr_number=pr_number,
                                  epoch_id=epoch_id, **kw)
        if facts["state"] != obs_mod.RESOLVED:
            raise RoundError(
                f"observation not recorded: {facts['cause']}. An unread "
                "surface is not an observed one.")
        # An observation is an event, like a baseline capture. Two readings
        # a second apart that find the same state are still two readings,
        # and collapsing them would let an acceptance cite a reading made
        # before the thing it is about.
        seq = self.conn.execute(
            "SELECT COUNT(*) FROM observations WHERE repo=? AND pr_number=?",
            (repo, int(pr_number))).fetchone()[0]
        oid = _ident("obs-", {"repo": repo, "pr": int(pr_number),
                              "head": facts["head_sha"],
                              "at": facts["observed_at"], "seq": seq})
        self.conn.execute(
            "INSERT INTO observations (observation_id, repo, pr_number,"
            " epoch_id, head_sha, facts, reads, observed_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (oid, repo, int(pr_number), epoch_id, facts["head_sha"],
             json.dumps({k: v for k, v in facts.items() if k != "reads"},
                        sort_keys=True),
             json.dumps(facts["reads"]), facts["observed_at"]))
        self.conn.commit()
        return self.observation(oid)

    def observation(self, observation_id):
        row = self.conn.execute(
            "SELECT * FROM observations WHERE observation_id=?",
            (observation_id,)).fetchone()
        if not row:
            return None
        out = dict(row)
        out["facts"] = json.loads(out["facts"])
        out["reads"] = json.loads(out["reads"])
        return out

    def latest_observation(self, repo, pr_number):
        row = self.conn.execute(
            "SELECT observation_id FROM observations WHERE repo=? AND "
            "pr_number=? ORDER BY rowid DESC LIMIT 1",
            (repo, int(pr_number))).fetchone()
        return self.observation(row[0]) if row else None

    def open_generations(self, repo, pr_number):
        """Derived from this store's own rows, never supplied.

        `open_generations=[]` was the last themed instance of the empty
        list: the gate asked whether any incompatible generation was open
        and the caller answered. An acceptance that has not been
        invalidated, together with any request made under it, is what
        "open" means, and only this store knows.
        """
        return [{"head_sha": a["head_sha"],
                 "acceptance_id": a["acceptance_id"],
                 "generations": sorted(
                     int(r["generation"])
                     for r in self.requests_for(a["acceptance_id"]))}
                for a in self.acceptances_for(repo, pr_number)
                if a["state"] == ACCEPTED]

    # --- acceptances ------------------------------------------------------
    def record_acceptance(self, *, repo, pr_number, epoch_id, head_sha,
                          permission, observation_id, accepted_at=None,
                          observation_max_age=OBSERVATION_MAX_AGE_SECONDS):
        """The writer runs the gate itself, over a row it loads.

        Three earlier shapes were refused here and all were one mistake:
        `preconditions=[]`, a hand-built `GateEvaluation`, and a durable
        observation assembled from caller-supplied fields. None could be
        fixed by checking the argument harder.

        `observation_id` now points at a row that could only have been
        written by performing the reads, and the row must additionally be
        the **latest** for this PR and younger than the operational bound.
        A reading that is merely on file says what was true once; an
        acceptance is about now.
        """
        import gate as gate_mod
        observation = self.observation(observation_id)
        if observation is None:
            raise RoundError(
                f"no observation {observation_id!r}: an acceptance is "
                "written from a recorded reading, never from a claim about "
                "one")
        latest = self.latest_observation(repo, pr_number)
        if latest is None or latest["observation_id"] != observation_id:
            raise RoundError(
                f"observation {observation_id} is not the latest reading for "
                f"{repo}#{pr_number}; a superseded reading cannot authorise "
                "an action taken now")
        age = _age_seconds(observation["observed_at"])
        if age is None:
            raise RoundError("the observation carries no usable timestamp")
        if age > observation_max_age:
            raise RoundError(
                f"observation is {age}s old, bound is {observation_max_age}s; "
                "re-read before accepting rather than accepting what was "
                "true earlier")
        try:
            gate_mod.require_scope(observation, repo=repo, pr_number=pr_number,
                                   head_sha=head_sha)
            failures = gate_mod.evaluate_observation(
                observation, permission, epoch_id=epoch_id,
                open_generations=self.open_generations(repo, pr_number))
        except gate_mod.GateError as exc:
            raise RoundError(str(exc)) from None
        if failures:
            raise RoundError("acceptance refused by preconditions: "
                             + "; ".join(failures))
        if len(head_sha or "") != 40:
            raise RoundError("an acceptance must name the full head")
        if not getattr(permission, "permits_action", False):
            raise RoundError(
                f"acceptance refused: authorization permission is "
                f"{getattr(permission, 'state', 'MISSING')}")
        accepted_at = accepted_at or utcnow()
        aid = _ident("acc-", {"repo": repo, "pr": int(pr_number),
                              "head": head_sha, "at": accepted_at,
                              "observation": observation_id})
        self.conn.execute(
            "INSERT INTO acceptances (acceptance_id, repo, pr_number,"
            " epoch_id, head_sha, accepted_at, auth_observation_id,"
            " auth_generation, observation_id, carrier_run_id, ruleset_id,"
            " state) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (aid, repo, int(pr_number), epoch_id, head_sha, accepted_at,
             permission.observation_id, permission.auth_generation,
             observation_id, observation["facts"]["carrier_run_id"],
             observation["facts"]["ruleset_id"], ACCEPTED))
        self.conn.commit()
        return self.acceptance(aid)

    def acceptance(self, acceptance_id):
        row = self.conn.execute(
            "SELECT * FROM acceptances WHERE acceptance_id=?",
            (acceptance_id,)).fetchone()
        return dict(row) if row else None

    def acceptances_for(self, repo, pr_number):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM acceptances WHERE repo=? AND pr_number=? "
            "ORDER BY rowid", (repo, int(pr_number)))]

    def current_acceptance(self, repo, pr_number, head_sha):
        """Only an acceptance about *this* commit counts.

        Deliberately filtered by head rather than by recency: the most
        recent acceptance for a PR may be about a commit that no longer
        exists, and returning it would let evidence gathered for one head
        authorise work on another.
        """
        rows = [a for a in self.acceptances_for(repo, pr_number)
                if a["head_sha"] == head_sha and a["state"] == ACCEPTED]
        return rows[-1] if rows else None

    def terminalize(self, repo, pr_number, *, cause, at=None):
        """End every standing acceptance for a PR that is no longer eligible.

        Established from observed GitHub state by the caller that read it,
        never from an operator boolean. The state is terminal in the strong
        sense: a schema trigger refuses any move back to ACCEPTED, so
        reopening the PR on the very same commit cannot revive it. That is
        the resurrection A6g-c1 found — `current_acceptance` selects on
        exact head and ACCEPTED, and nothing in it knew the PR had closed.
        """
        at = at or utcnow()
        standing = [a for a in self.acceptances_for(repo, pr_number)
                    if a["state"] == ACCEPTED]
        for a in standing:
            self.conn.execute(
                "UPDATE acceptances SET state=? WHERE acceptance_id=?",
                (TERMINATED, a["acceptance_id"]))
        self.conn.commit()
        return [{"acceptance_id": a["acceptance_id"], "was_for_head": a["head_sha"],
                 "state": TERMINATED, "cause": cause, "at": at}
                for a in standing]

    def prs_with_standing_acceptances(self, repo):
        return sorted({a["pr_number"] for a in self.conn.execute(
            "SELECT pr_number FROM acceptances WHERE repo=? AND state=?",
            (repo, ACCEPTED))})

    def invalidate_for_head_move(self, repo, pr_number, current_head, at=None):
        """Mark acceptances about vanished commits, without repointing any.

        `state` is the one column that may move, and only to INVALIDATED.
        The schema trigger refuses any attempt to change what an acceptance
        is about.
        """
        at = at or utcnow()
        stale = [a for a in self.acceptances_for(repo, pr_number)
                 if a["state"] == ACCEPTED and a["head_sha"] != current_head]
        for a in stale:
            self.conn.execute(
                "UPDATE acceptances SET state=? WHERE acceptance_id=?",
                (INVALIDATED, a["acceptance_id"]))
        self.conn.commit()
        return [{"acceptance_id": a["acceptance_id"],
                 "was_for_head": a["head_sha"], "current_head": current_head,
                 "state": INVALIDATED, "at": at} for a in stale]

    # --- provider requests -------------------------------------------------
    def record_intent(self, *, acceptance_id, repo, pr_number, provider,
                      generation, requested_for_head, permission, baseline,
                      intent_recorded_at=None):
        """Written BEFORE the network call, and bound to the capture.

        `baseline` is the durable capture row, not a dict. Without it here
        the sequence "capture X, post, collect against Y" was legal: the
        request knew nothing about which reading preceded it, so the proof
        that a run id is *new* rested on whichever baseline the collector
        happened to be handed.
        """
        if not self.acceptance(acceptance_id):
            raise RoundError("no such acceptance")
        acc = self.acceptance(acceptance_id)
        if acc["state"] != ACCEPTED:
            raise RoundError(f"acceptance is {acc['state']}")
        if acc["head_sha"] != requested_for_head:
            raise RoundError(
                "a request may only be made for the head its acceptance is "
                "about")
        if not getattr(permission, "permits_action", False):
            raise RoundError(
                f"request intent refused: authorization permission is "
                f"{getattr(permission, 'state', 'MISSING')}")
        if not isinstance(baseline, dict) or not baseline.get("baseline_id"):
            raise RoundError(
                "a provider request must name the durable baseline capture "
                "that preceded it; without one, 'this run id is new' is a "
                "claim about an unspecified reading")
        if not baseline.get("read_ok"):
            raise RoundError(
                "the baseline capture this request would cite did not read "
                "successfully")
        if (baseline.get("repo"), int(baseline.get("pr_number", -1)),
                baseline.get("provider")) != (repo, int(pr_number), provider):
            raise RoundError(
                "the baseline capture is for another scope: "
                f"{baseline.get('repo')}#{baseline.get('pr_number')} "
                f"{baseline.get('provider')}")
        at = intent_recorded_at or utcnow()
        if baseline["captured_at"] > at:
            raise RoundError(
                "the baseline was captured after this intent; a reading that "
                "follows the request cannot establish what preceded it")
        rid = _ident("req-", {"acc": acceptance_id, "provider": provider,
                              "gen": int(generation)})
        self.conn.execute(
            "INSERT INTO provider_requests (request_id, acceptance_id, repo,"
            " pr_number, provider, generation, requested_for_head,"
            " auth_observation_id, auth_generation, baseline_id,"
            " baseline_digest, baseline_captured_at, intent_recorded_at,"
            " request_carrier_id, request_outcome)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, acceptance_id, repo, int(pr_number), provider,
             int(generation), requested_for_head,
             getattr(permission, "observation_id", None),
             getattr(permission, "auth_generation", None),
             baseline["baseline_id"], baseline["baseline_digest"],
             baseline["captured_at"], at, None, INTENT_RECORDED))
        self.conn.commit()
        return self.request(rid)

    def settle_request(self, request_id, *, outcome, carrier_id=None, at=None):
        if outcome not in (SENT, OUTCOME_UNKNOWN):
            raise RoundError(f"unknown request outcome {outcome!r}")
        self.conn.execute(
            "UPDATE provider_requests SET request_outcome=?,"
            " request_carrier_id=?, outcome_recorded_at=? WHERE request_id=?",
            (outcome, carrier_id, at or utcnow(), request_id))
        self.conn.commit()
        return self.request(request_id)

    def request(self, request_id):
        row = self.conn.execute(
            "SELECT * FROM provider_requests WHERE request_id=?",
            (request_id,)).fetchone()
        return dict(row) if row else None

    def requests_for(self, acceptance_id):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM provider_requests WHERE acceptance_id=? "
            "ORDER BY rowid", (acceptance_id,))]
