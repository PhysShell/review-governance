"""The normative Governor authorization predicate — specification as code.

Pure and offline: no network, no clock, no state of its own. It takes a
snapshot of the four state domains and answers one question, `may the
Governor treat this head as authorized right now?`

The predicate is deliberately conjunctive and deliberately boring. Every
clause exists because an earlier experiment showed what happens without it:

    epoch CURRENT ................ A2b: a superseded epoch may not speak
    auth AUTHORIZED .............. A1c: authorization loss fails closed
    decision SUCCESS ............. A3a: absence of findings is not positive
    projection CONFIRMED ......... A3b-c4: a write is not a fact until read
    projection head == HEAD ...... A2b/A3b: a green older head is not this head
    projection app == Governor ... A2b: the display name is not provenance
    bundle hashes agree .......... A3b: a success names the basis it came from
    no known invalidation ........ A3b-c3: never stand on evidence we know is stale
"""
from dataclasses import dataclass, field

GOVERNOR_APP_ID = 4669438

# authorization states (A1c vocabulary)
AUTHORIZED = "AUTHORIZED"

# projection states (A3b-c4)
PENDING = "PENDING"
CONFIRMED = "CONFIRMED"
OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
FAILED = "FAILED"
UNSETTLED = frozenset({PENDING, OUTCOME_UNKNOWN})


@dataclass(frozen=True)
class Snapshot:
    """One evaluation of the four domains at one instant."""
    current_full_head: str                 # GitHub: the PR's head right now
    epoch_state: str                       # Governor: CURRENT | STALE
    epoch_head: str                        # Governor: head the epoch covers
    auth_state: str                        # Governor: A1c authorization state
    decision_verdict: str                  # Governor: latest durable verdict
    decision_bundle_hash: str              # Governor: basis of that verdict
    projection_state: str                  # Governor: A3b-c4 projection state
    projection_conclusion: str             # GitHub: conclusion last read back
    projection_head: str                   # GitHub: head that run is bound to
    projection_app_id: int                 # GitHub: app that owns that run
    projection_bundle_hash: str            # Governor: hash the run published
    known_invalidations: tuple = field(default_factory=tuple)


def evaluate(snapshot: Snapshot) -> dict:
    """Returns the authorization decision and every reason it was withheld.

    Reasons are accumulated rather than short-circuited: an operator reading
    a refusal should see all of it, not the first clause that happened to
    fail.
    """
    reasons = []

    if snapshot.epoch_state != "CURRENT":
        reasons.append(f"epoch is {snapshot.epoch_state}, not CURRENT")
    if snapshot.epoch_head != snapshot.current_full_head:
        reasons.append("epoch covers a different head than the current one")
    if snapshot.auth_state != AUTHORIZED:
        reasons.append(f"authorization is {snapshot.auth_state}")
    if snapshot.decision_verdict != "SUCCESS":
        reasons.append(f"latest durable verdict is {snapshot.decision_verdict}")
    if snapshot.projection_state != CONFIRMED:
        reasons.append(f"projection is {snapshot.projection_state}, not CONFIRMED")
    if snapshot.projection_conclusion != "success":
        reasons.append("projected conclusion is not success "
                       f"({snapshot.projection_conclusion})")
    if snapshot.projection_head != snapshot.current_full_head:
        reasons.append("published check is bound to a different head")
    if len(snapshot.current_full_head or "") != 40:
        reasons.append("current head is not a full 40-character SHA")
    if snapshot.projection_app_id != GOVERNOR_APP_ID:
        reasons.append("published check is not owned by the Governor App")
    if snapshot.projection_bundle_hash != snapshot.decision_bundle_hash:
        reasons.append("published bundle hash differs from the decision's basis")
    if snapshot.known_invalidations:
        reasons.append("locally known invalidation(s): "
                       + "; ".join(snapshot.known_invalidations))

    return {
        "may_authorize_action": not reasons,
        "reasons": reasons,
        # visibility is a separate question and is never authorization
        "external_success_may_exist": external_success_may_exist(snapshot),
    }


def external_success_may_exist(snapshot: Snapshot) -> bool:
    """Whether GitHub may currently be showing a green check for this run.

    Answers "must the Governor clean up before acting?" — never "may the
    Governor proceed?" (A3b-c4).
    """
    if snapshot.projection_conclusion == "success":
        return True
    return (snapshot.decision_verdict == "SUCCESS"
            and snapshot.projection_state in UNSETTLED)


def enforcement_expectation(snapshot: Snapshot) -> str:
    """What GitHub is expected to do with a required Governor check, given
    the same snapshot. This is a *prediction* to be tested in A4-live, not a
    claim of what GitHub does.

    Note the asymmetry that the whole design rests on: GitHub answers only
    from the projection domain. It cannot know about a Governor decision
    that has not been projected, nor about a provider mutation the Governor
    has not yet observed.
    """
    if snapshot.projection_app_id != GOVERNOR_APP_ID:
        return "BLOCK: required context not satisfied by the expected source"
    if snapshot.projection_head != snapshot.current_full_head:
        return "BLOCK: no passing required check on the latest head"
    if snapshot.projection_conclusion == "success":
        return "ALLOW: latest head carries a passing Governor check"
    return f"BLOCK: latest-head check is {snapshot.projection_conclusion}"


def residual_window(snapshot: Snapshot) -> dict:
    """The gap between what the Governor knows and what GitHub can act on.

    `hazardous` is true exactly when GitHub would allow a merge that the
    Governor's own state says is not authorized — which is the residual
    surface A4 exists to measure.
    """
    decision = evaluate(snapshot)
    github = enforcement_expectation(snapshot)
    github_allows = github.startswith("ALLOW")
    return {
        "governor_authorizes": decision["may_authorize_action"],
        "github_expectation": github,
        "hazardous": github_allows and not decision["may_authorize_action"],
        "reasons": decision["reasons"],
    }
