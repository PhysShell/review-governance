"""The A2a control-plane reducer: deliveries in, epoch and authorization
state out.

Deliberately narrow. It knows about review **epochs** (a PR head SHA that a
review round belongs to), about authorization state, and about one refusal
it must never make: there is no code path in this module that produces
`CLEAN`. Absent, malformed or uncertain evidence leaves the gate
`NOT_ESTABLISHED`, which is a failed gate, not a passed one.
"""
from dataclasses import dataclass, field

# epoch states
CURRENT = "CURRENT"
STALE = "STALE"

# gate states — note what is absent
NOT_ESTABLISHED = "NOT_ESTABLISHED"
UNCERTAIN = "UNCERTAIN"

# authorization states (mirrors A1c's reducer vocabulary)
AUTHORIZED = "AUTHORIZED"
AUTH_LOST = "AUTH_LOST"
REFRESH_OUTCOME_UNKNOWN = "REFRESH_OUTCOME_UNKNOWN"
REAUTH_REQUIRED = "REAUTH_REQUIRED"

TRIGGER_ALLOWED_AUTH_STATES = frozenset({AUTHORIZED})


@dataclass
class Epoch:
    repo: str
    pr: int
    head_sha: str
    state: str
    opened_by_delivery: str


@dataclass
class ControlPlane:
    """In-memory projection of the durable state. `seen_deliveries` is the
    idempotency key set: a delivery id is recorded only after a signature
    has been verified, and applying it twice must not change state twice."""
    epochs: list = field(default_factory=list)
    seen_deliveries: dict = field(default_factory=dict)
    auth_state: str = AUTHORIZED
    auth_reason: str = ""
    gate_states: dict = field(default_factory=dict)

    # --- queries ---------------------------------------------------------
    def current_epoch(self, repo: str, pr: int):
        for epoch in self.epochs:
            if epoch.repo == repo and epoch.pr == pr and epoch.state == CURRENT:
                return epoch
        return None

    def epochs_for(self, repo: str, pr: int):
        return [e for e in self.epochs if e.repo == repo and e.pr == pr]

    def gate_state(self, repo: str, pr: int, head_sha: str) -> str:
        """Never returns CLEAN: A2a establishes transport, not verdicts."""
        return self.gate_states.get((repo, pr, head_sha), NOT_ESTABLISHED)

    def may_trigger_providers(self, repo: str, pr: int, head_sha: str) -> bool:
        if self.auth_state not in TRIGGER_ALLOWED_AUTH_STATES:
            return False
        epoch = self.current_epoch(repo, pr)
        return bool(epoch) and epoch.head_sha == head_sha

    # --- reduction -------------------------------------------------------
    def apply(self, delivery_id: str, event: str, payload: dict) -> dict:
        """Apply one *already signature-verified* delivery.

        Callers must not reach this without verification: `receiver.py`
        verifies first and only then consumes the delivery id.
        """
        if delivery_id in self.seen_deliveries:
            return {"delivery_id": delivery_id, "effect": "DUPLICATE_IGNORED",
                    "first_seen_effect": self.seen_deliveries[delivery_id]}

        effect = self._dispatch(delivery_id, event, payload)
        self.seen_deliveries[delivery_id] = effect
        return {"delivery_id": delivery_id, "effect": effect}

    def _dispatch(self, delivery_id: str, event: str, payload: dict) -> str:
        if event == "pull_request":
            return self._pull_request(delivery_id, payload)
        if event == "github_app_authorization":
            if payload.get("action") == "revoked":
                self.auth_state = AUTH_LOST
                self.auth_reason = (
                    "github_app_authorization.revoked by "
                    f"{(payload.get('sender') or {}).get('login', 'unknown')}")
                return "AUTH_LOST"
            return "AUTH_EVENT_IGNORED"
        return "EVENT_IGNORED"

    def _pull_request(self, delivery_id: str, payload: dict) -> str:
        action = payload.get("action")
        pull = payload.get("pull_request") or {}
        repo = (payload.get("repository") or {}).get("full_name")
        number = pull.get("number")
        head_sha = (pull.get("head") or {}).get("sha")
        if not (repo and number and head_sha):
            # Malformed payloads change nothing — and specifically do not
            # advance any gate.
            return "MALFORMED_IGNORED"

        if action in ("synchronize", "opened", "reopened", "ready_for_review"):
            became_stale = 0
            for epoch in self.epochs:
                if (epoch.repo == repo and epoch.pr == number
                        and epoch.head_sha != head_sha
                        and epoch.state == CURRENT):
                    epoch.state = STALE
                    became_stale += 1
                    # a stale head can never carry a gate verdict forward
                    self.gate_states.pop((repo, number, epoch.head_sha), None)
            if not any(e.repo == repo and e.pr == number
                       and e.head_sha == head_sha for e in self.epochs):
                self.epochs.append(Epoch(repo, number, head_sha, CURRENT,
                                         delivery_id))
            else:
                for epoch in self.epochs:
                    if (epoch.repo == repo and epoch.pr == number
                            and epoch.head_sha == head_sha):
                        epoch.state = CURRENT
            return (f"EPOCH_OPENED head={head_sha[:10]} "
                    f"stale_marked={became_stale}")
        return f"PR_ACTION_IGNORED:{action}"

    # --- reconciliation --------------------------------------------------
    def note_reconciliation_gap(self, repo: str, pr: int, head_sha: str,
                                reason: str) -> str:
        """A missed or unverifiable delivery makes the gate UNCERTAIN —
        which is still not a pass."""
        self.gate_states[(repo, pr, head_sha)] = UNCERTAIN
        return f"UNCERTAIN:{reason}"

    def note_auth_state(self, state: str, reason: str = "") -> None:
        self.auth_state = state
        self.auth_reason = reason
