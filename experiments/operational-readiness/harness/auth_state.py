"""Authoritative user-authorization state, and its human-facing mirror.

A5b-preflight found the wiring this closes: the sentinel understood
`AUTH_LOST` and `REFRESH_OUTCOME_UNKNOWN`, and nothing produced them. An
absent producer is an absent *entrance to a safety transition*, not an
absent green square — by A1c semantics those states forbid provider
triggers, invalidate standing successes and demand fresh qualification.

Two readers, deliberately not the same object:

    Governor lifecycle -> reads THIS STORE          -> safety transition
    sentinel           -> reads auth-state.json     -> alerts a human

`auth-state.json` is a projection, written from the store and never back
into it. Making the file the authority because a file is convenient to read
is exactly how a mirror quietly becomes a source of truth — and this file is
world-readable by design, so it would be a source of truth that anything on
the host could edit.

Append-only, like the decision history and for the same reason: the question
"when did authorization actually lapse" must survive whatever happened
afterwards.
"""
import json
import sqlite3
from pathlib import Path

AUTHORIZED = "AUTHORIZED"
AUTH_LOST = "AUTH_LOST"
REFRESH_OUTCOME_UNKNOWN = "REFRESH_OUTCOME_UNKNOWN"
STATES = (AUTHORIZED, AUTH_LOST, REFRESH_OUTCOME_UNKNOWN)

#: States in which the Governor may start provider work. Written as an
#: allowlist: a state nobody anticipated must fail closed, not fall through.
PERMITS_TRIGGERS = frozenset({AUTHORIZED})

#: States that demand invalidation of anything currently standing green.
DEMANDS_INVALIDATION = frozenset({AUTH_LOST, REFRESH_OUTCOME_UNKNOWN})

SOURCES = ("device_flow", "refresh", "authorization_webhook", "fixture")

SCHEMA = """
CREATE TABLE IF NOT EXISTS auth_observations (
    observation_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    state            TEXT NOT NULL,
    auth_generation  INTEGER NOT NULL,
    observed_at      TEXT NOT NULL,
    source           TEXT NOT NULL,
    cause            TEXT,
    previous_state   TEXT
);
CREATE TRIGGER IF NOT EXISTS auth_is_append_only_update
BEFORE UPDATE ON auth_observations
BEGIN
    SELECT RAISE(ABORT, 'authorization history is append-only');
END;
CREATE TRIGGER IF NOT EXISTS auth_is_append_only_delete
BEFORE DELETE ON auth_observations
BEGIN
    SELECT RAISE(ABORT, 'authorization history is append-only');
END;
"""


class UnknownAuthState(Exception):
    """A state outside the vocabulary is refused rather than stored."""


class AuthStore:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    def record(self, *, state, auth_generation, observed_at, source,
               cause=None):
        if state not in STATES:
            raise UnknownAuthState(f"{state!r} is not one of {STATES}")
        if source not in SOURCES:
            raise UnknownAuthState(f"{source!r} is not one of {SOURCES}")
        previous = self.current()
        cur = self.conn.execute(
            "INSERT INTO auth_observations (state, auth_generation,"
            " observed_at, source, cause, previous_state)"
            " VALUES (?,?,?,?,?,?)",
            (state, int(auth_generation), observed_at, source, cause,
             previous["state"] if previous else None))
        self.conn.commit()
        return cur.lastrowid

    def current(self):
        row = self.conn.execute(
            "SELECT * FROM auth_observations ORDER BY observation_id DESC "
            "LIMIT 1").fetchone()
        return dict(row) if row else None

    def history(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM auth_observations ORDER BY observation_id")]

    # --- the two questions the rest of the system is allowed to ask --------
    def state_permits_triggers(self) -> bool:
        """Fact-level only: is the *stored state* one that permits at all.

        This answers nothing about time and must never authorise an action
        on its own. No observation is not permission — but neither is a
        three-day-old one, and this function cannot tell the difference.
        Authorization is derived by `auth_policy.evaluate`.
        """
        row = self.current()
        return bool(row) and row["state"] in PERMITS_TRIGGERS

    def permits_triggers(self, *args, **kwargs):
        """Retired in A6c. Kept as a trap rather than deleted.

        It answered "is the last stored state AUTHORIZED" with no bound on
        age, and that answer travelled onward as a bare boolean that could
        license a provider round — or a production success — on a reading
        three days old. Deleting it would let a future caller reinvent the
        same name innocently; raising here makes the mistake loud at the
        call site.
        """
        raise AuthorizationRefused(
            "permits_triggers() is retired: it cannot bound the age of the "
            "observation it reports. Use auth_policy.evaluate(store) and "
            "check permission.permits_action, which carries the provenance "
            "this boolean threw away.")

    def demands_invalidation(self) -> bool:
        row = self.current()
        return bool(row) and row["state"] in DEMANDS_INVALIDATION

    # --- projection --------------------------------------------------------
    def project(self, path) -> dict:
        """Write the human-facing mirror. One direction only."""
        row = self.current()
        mirror = {
            "state": row["state"] if row else None,
            "auth_generation": row["auth_generation"] if row else None,
            "observed_at": row["observed_at"] if row else None,
            "source": row["source"] if row else None,
            "note": "projection of the authoritative auth store; read by the "
                    "sentinel for alerting only. Never a policy authority, "
                    "and never read back into the store.",
        }
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(mirror, indent=2) + "\n")
        return mirror


class AuthorizationRefused(Exception):
    """Raised where provider work would have started without authorization."""


def require_triggers_permitted(store, permission=None):
    """The guard a publishing path calls before doing anything.

    An exception rather than a boolean return: a caller that forgets to
    check a boolean proceeds, and a caller that forgets to catch an
    exception stops. Here the failure mode has to be the stopping one.

    `permission` is required rather than derived inside, so the freshness
    of the reading is decided at the call site that is about to act on it.
    Deriving it here would recreate the retired boolean one layer down —
    a function that answers "may I act" from a reading of unknown age.
    """
    row = store.current()
    if permission is None:
        raise AuthorizationRefused(
            "a derived AuthorizationPermission is required: this guard will "
            "not infer freshness on the caller's behalf. Use "
            "auth_policy.evaluate(store).")
    if getattr(permission, "permits_action", False):
        return row
    state = getattr(permission, "state", "UNKNOWN")
    raise AuthorizationRefused(
        f"provider triggers forbidden while the derived authorization "
        f"permission is {state} "
        f"(age={getattr(permission, 'age_seconds', None)}s); A1c requires "
        "human reauthorization and fresh qualification")
