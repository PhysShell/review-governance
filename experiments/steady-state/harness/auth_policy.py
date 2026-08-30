"""Derived authorization permission. Separate from the stored fact on purpose.

The defect this closes conflated two different sentences:

    AUTHORIZED   GitHub confirmed the authorization at time T
    permitted    that confirmation is recent enough to license a new
                 dangerous action right now

`permits_triggers()` answered the second by checking only the first, so a
71-hour-old observation licensed provider triggers — and, through the same
boolean, could have licensed publishing a production SUCCESS while the
authorization behind it was dead. Writing `STALE` into the store would have
repeated the mistake in the other direction: an inference recorded as an
observation.

So the store keeps facts:

    AUTHORIZED · AUTH_LOST · REFRESH_OUTCOME_UNKNOWN

and this derives, at the moment of asking:

    FRESH_AUTHORIZED · STALE · FORBIDDEN · UNOBSERVED

**Sixty seconds, not eight hours.** Eight hours is how long GitHub honours
an access token; it is not how long the Governor may treat an old reading
as current. A safe read-only `/user` probe exists and can run immediately
before any dangerous action, so there is no reason to carry a permission
around for hours. Sixty seconds is the tolerance of an operation, not the
lifetime of a credential — the same shape as the minute-scale freshness
bounds already used for reconciliation and watchdog liveness.

**STALE is not AUTH_LOST.** Staleness blocks *new* acceptances, triggers
and success publications. It does not assert revocation and does not
invalidate a standing success, because otherwise every quiet night would
turn into revoke-refresh-revoke and the alerts would be trained out of
meaning. Only evidential states invalidate.
"""
import datetime

AUTH_PERMISSION_MAX_AGE_SECONDS = 60

FRESH_AUTHORIZED = "FRESH_AUTHORIZED"
STALE = "STALE"
FORBIDDEN = "FORBIDDEN"
UNOBSERVED = "UNOBSERVED"

#: The only derived state that may license a new dangerous action.
PERMITS_ACTION = frozenset({FRESH_AUTHORIZED})

#: States that assert an authorization problem, as opposed to an old
#: reading. Only these invalidate standing evidence.
ASSERTS_LOSS = frozenset({FORBIDDEN})


class PermissionRequired(TypeError):
    """Raised where a bare boolean is offered in place of a permission.

    The interface refuses it rather than trusting callers, because the
    original defect was exactly a boolean arriving with its provenance
    stripped off.
    """


class AuthorizationPermission:
    """A permission carries where it came from, or it is not a permission."""

    __slots__ = ("state", "observation_id", "auth_generation", "observed_at",
                 "age_seconds", "source", "evaluated_at", "cause")

    def __init__(self, *, state, evaluated_at, observation_id=None,
                 auth_generation=None, observed_at=None, age_seconds=None,
                 source=None, cause=None):
        self.state = state
        self.observation_id = observation_id
        self.auth_generation = auth_generation
        self.observed_at = observed_at
        self.age_seconds = age_seconds
        self.source = source
        self.evaluated_at = evaluated_at
        self.cause = cause

    @property
    def permits_action(self) -> bool:
        return self.state in PERMITS_ACTION

    @property
    def asserts_loss(self) -> bool:
        return self.state in ASSERTS_LOSS

    def as_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}

    def __repr__(self):
        return (f"AuthorizationPermission({self.state}, "
                f"age={self.age_seconds}s, gen={self.auth_generation})")


def require(permission):
    """Every critical interface calls this on whatever it was handed.

    A bare `True` is refused here rather than deeper down, so the failure
    is at the call site that lost the provenance.
    """
    if not isinstance(permission, AuthorizationPermission):
        raise PermissionRequired(
            f"expected an AuthorizationPermission carrying its own "
            f"provenance, got {type(permission).__name__}: a boolean cannot "
            "say when it was observed or from which generation")
    return permission


def _parse(value):
    return datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=datetime.timezone.utc)


def evaluate(store, *, now=None, max_age_seconds=AUTH_PERMISSION_MAX_AGE_SECONDS):
    """Derive the permission. Never writes, never records a derived state."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    evaluated_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    row = store.current()
    if not row:
        return AuthorizationPermission(
            state=UNOBSERVED, evaluated_at=evaluated_at,
            cause="no authorization has ever been observed")

    common = {"observation_id": row.get("observation_id"),
              "auth_generation": row.get("auth_generation"),
              "observed_at": row.get("observed_at"),
              "source": row.get("source"),
              "evaluated_at": evaluated_at}

    if row["state"] != "AUTHORIZED":
        return AuthorizationPermission(
            state=FORBIDDEN,
            cause=f"stored state is {row['state']}", **common)

    try:
        observed = _parse(row["observed_at"])
    except (TypeError, ValueError):
        return AuthorizationPermission(
            state=UNOBSERVED,
            cause="observation timestamp is missing or malformed; age "
                  "cannot be established, so freshness cannot be claimed",
            **common)

    age = (now - observed).total_seconds()
    if age < 0:
        return AuthorizationPermission(
            state=UNOBSERVED, age_seconds=round(age),
            cause="observation is in the future; a clock disagreement is "
                  "not a fresh permission",
            **common)
    if age > max_age_seconds:
        return AuthorizationPermission(
            state=STALE, age_seconds=round(age),
            cause=f"observation is {round(age)}s old, bound is "
                  f"{max_age_seconds}s. This blocks new actions; it does "
                  "not assert that authorization was lost",
            **common)
    return AuthorizationPermission(
        state=FRESH_AUTHORIZED, age_seconds=round(age), **common)
