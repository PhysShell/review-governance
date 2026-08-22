# A4-design — Enforcement semantics and residual TOCTOU

Program `ai-final-review` · Control plane `PhysShell/review-governance` ·
Branch `experiment/enforcement-design` · Date: 2026-08-22 (UTC).
Scope note: `experiments/enforcement-design/PROTOCOL.md`.

**Design stage only.** Nothing in this stage wrote to GitHub: no
permissions, no rulesets, no branch protection, no auto-merge, no merge, no
check-name change. `ai/final-review` remains reserved and unused.

## Question

What exactly can GitHub required-check enforcement guarantee for a Governor
verdict derived from **mutable external evidence**, and what race remains
fundamentally outside that guarantee?

## Frozen prerequisites

```text
A1     trigger identity                  PARTIAL
A1b    Codex user-mediated trigger       PASS
A1b-R  CodeRabbit user-mediated trigger  PASS
A1c    user-auth lifecycle               PASS / HUMAN RECOVERY
A2a    webhook control plane             PASS
A2b    durable shadow check              PASS
A3a    positive evidence qualification   PASS
A3b    shadow SUCCESS lifecycle          PASS / FROZEN
PRODUCTION_ENFORCEMENT: NOT_READY
```

Control-plane PRs #1–#8: draft, open, unmerged, untouched. Freezing an
experiment is not accepting a branch — merging PR #8 still requires a fresh
Codex + CodeRabbit round on its exact current head.

## Four state domains

```text
1. provider reality              CodeRabbit / Codex mutable carriers
2. Governor observed state       durable, append-only, hash-anchored
3. GitHub Check Run projection   what GitHub can see
4. GitHub merge decision         what GitHub acts on
```

Every earlier correction in this program came from treating two of these as
one. They are never atomic with each other, and the design's whole job is to
say precisely which guarantees survive that.

## Two guarantees, deliberately separate

```text
G1  OBSERVED_STATE_ENFORCEMENT
    If the Governor has observed a condition invalidating its SUCCESS, that
    condition is projected as non-success before any Governor-initiated
    action, and GitHub blocks merge once the projection is confirmed.

G2  UNOBSERVED_EXTERNAL_INVALIDATION_ATOMICITY
    If a provider mutates evidence and the Governor has not yet observed it,
    can GitHub nevertheless prevent merge?
```

**G1 is achievable and already half-demonstrated.** A3b-c3 showed the
Governor extinguishing its own success and confirming the failure *before*
performing the act that invalidated the basis (live: check `completed_at`
08:50:47Z, request `created_at` 08:50:49Z). A3b-c4 showed a projection is
only trusted after an independent readback. What remains for A4-live is the
other half — that GitHub actually blocks once that failure is confirmed.

**G2 is not provided by required checks alone.** A required check answers
from domain 3 only. If CodeRabbit mutates its sticky at T0 and the Governor
observes it at T0+Δ, GitHub has no information during Δ that would let it
refuse a merge: the check it can see still says success, and nothing in the
Checks API represents "a provider changed its mind two seconds ago". This
is not a defect in either system; it is what "advisory mutable carrier"
means. A3a observed exactly this shape live — a CodeRabbit comment that
simultaneously announced a skipped review for the new head and "no
actionable comments" for the old range.

The normative predicate (`harness/policy.py`, 21 adversarial tests)
encodes the asymmetry directly: `residual_window()` flags `hazardous` when
GitHub would allow a merge the Governor's own state does not authorize.

## Normative authorization predicate

```text
may_authorize_action :=
       epoch == CURRENT
    && epoch.head == current_full_HEAD
    && auth == AUTHORIZED
    && decision == SUCCESS
    && projection == CONFIRMED
    && projection.conclusion == success
    && projection.head_sha == current_full_HEAD   (40 chars)
    && projection.app.id == 4669438
    && projection.bundle_hash == decision.bundle_hash
    && no locally-known invalidation exists
```

`external_success_may_exist` is **never** an authorization predicate; it
answers only "must the Governor clean up before acting?" (A3b-c4).

## What GitHub gives us, established read-only

| fact | value | source |
|---|---|---|
| target repo owner type | **User** (`PhysShell`) | live API |
| repo visibility | public | live API |
| rulesets currently defined | **0** | live API |
| branch protection on `main` | none | live API (404) |
| repo-level auto-merge | **disabled** | live API |
| Governor App permissions | `checks: write`, `issues: write`, `pull_requests: write`, `metadata: read` | live API |
| Governor `statuses` permission | **absent** | live API |

Documented GitHub behaviour this design relies on, to be **verified in
A4-live rather than assumed**:

- a required check must pass on the **latest** commit SHA; a green older
  head does not satisfy it;
- `success`, `skipped` and `neutral` all count as passing for required
  status checks — which makes the program's structural exclusion of
  `neutral`/`skipped` a **production invariant**, not a stylistic choice;
- expected source exists: a required check can be bound to a specific App
  (`context` + `integration_id` in the ruleset model), and a same-named
  check from another source then does not satisfy the rule;
- selecting an App as expected source reportedly requires the App to hold
  `statuses: write`, be installed, have recently sent a check run, and be
  associated with an existing required check;
- the merge REST API accepts an expected `sha`, which rejects the merge if
  the PR head has moved — a guard against head drift only, not against
  evidence change on the same head;
- merge queues are available to org-owned public repos or Enterprise Cloud
  private org repos; this repository is **User-owned**, so a queue is not a
  candidate here.

## Expected-source design and the permission delta

Target production rule, once qualified:

```text
required_status_checks:
  context: ai/final-review
  integration_id: 4669438
```

Current state: the Governor holds `checks: write` and **no** `statuses`
permission. The documented bootstrap requirement therefore blocks
expected-source selection today.

```text
STATUS_PERMISSION_DELTA: REQUIRED   (documented; unverified locally)
```

The correct sequence is a separate **A4a permission qualification**, not a
quiet widening:

1. demonstrate, with the current permissions, that expected-source cannot be
   selected — an observed refusal, not an inference from documentation;
2. stop, and obtain explicit owner approval for exactly
   `Commit statuses: Read and write`, with the reason recorded;
3. after the change, prove that the Governor's *behaviour* did not widen:
   it still writes only Check Runs, no Governor policy path calls the
   Commit Status API, the ruleset readback contains
   `integration_id = 4669438`, and a same-context check from another source
   still fails to satisfy the rule.

A permission may be a bootstrap requirement of GitHub's ruleset machinery
without becoming a new Governor output channel. That distinction has to be
enforced by tests, because nothing in GitHub enforces it.

## Safe isolated probe

Never on `main`. A4-live gets its own disposable target:

```text
branch:  refs/heads/governor/a4-enforcement-target
ruleset: matches ONLY that ref
context: ai/final-review-enforcement-probe      (not ai/final-review)
```

Because Evaluate mode is not guaranteed on this account — it is documented
as an Enterprise feature and must be *measured*, not assumed — the design
assumes `enforcement: active` and relies on **exact ref scoping** for
safety. `main`, the pilot artifacts, and every existing PR must fall
outside the rule's `include` set, which is verified by ruleset readback
before any test merge is attempted. Rollback is deleting one ruleset that
never matched anything else.

## A4-live matrix (to be executed later, not now)

```text
no Governor check                          -> merge blocked
Governor failure                           -> merge blocked
same-name success, wrong source            -> merge blocked
Governor success, exact latest HEAD        -> merge allowed
Governor success on previous HEAD          -> new HEAD blocked
success -> failure on same current HEAD    -> blocked after failure CONFIRMED
projection PENDING / OUTCOME_UNKNOWN       -> Governor treats action as unauthorized
```

Every merge attempt passes the exact expected `sha`.

## Three ordering experiments

Distributed atomicity is not demonstrated by racing randomly. Three
causally distinguishable orders:

```text
Case A   failure CONFIRMED -> merge request            MUST BLOCK
Case B   merge completes -> invalidation observed      merge already irreversible
Case C   provider mutates -> Governor has not observed -> merge attempted
         while the check still reads success           THE ONE THAT MATTERS
```

If Case C merges, that is neither a Governor bug nor a GitHub bug:

```text
RESIDUAL_UNOBSERVED_INVALIDATION_WINDOW: OBSERVED
```

If it does *not* merge, the specific GitHub mechanism must be identified —
required checks are not to be credited with telepathy.

## Measuring the residual window

For an externally caused invalidation, record:

```text
provider_artifact_mutated_at    (when observable)
observed_at                     (webhook or poll)
durable_invalidation_at
failure_patch_at
failure_confirmed_at
```

and derive `detection_lag`, `revocation_lag`, `total_exposure_window`.
A3b already measured revocation at ~1 s and publication at 1–2 s, so the
dominant term is detection lag. With A2a's webhook path the detection lag
is bounded by delivery latency plus reconciliation interval; with polling
it is the poll period. Neither is zero, and the design must state the
number rather than the adjective.

## Quarantine is mitigation, not proof

A delay of N seconds between settling and publishing success is:

```text
RISK_REDUCTION
```

and never:

```text
ATOMIC_SAFETY
```

No finite N proves the provider will not mutate at N+1. A quarantine buys
probability, not a guarantee, and must be reported as such.

## Auto-merge and merge queue

**Auto-merge: excluded from the pilot.** It merges as soon as requirements
are met, which converts the first green instant into an immediate
irreversible action — it accelerates the G2 race rather than closing it.
Repo-level auto-merge is currently disabled and stays that way.

**Merge queue:** `MERGE_QUEUE: NOT_AVAILABLE_FOR_CURRENT_REPO_OWNER_TYPE`
— this repository is User-owned. Moving repositories into an organization
solely to obtain a queue is not a v1 consideration; it is recorded as a
future alternative for org-owned repos only.

## Admissible security claim

If A4-live succeeds, the strongest defensible statement is:

> GitHub prevents merge whenever the latest-head Governor check, from the
> expected Governor App, is not in a passing state. The Governor fails
> closed for every invalidation it has observed.

The forbidden statement, which this program's own evidence contradicts:

> A PR can never be merged after its provider evidence becomes invalid.

While provider carriers are mutable and invalidation is discovered
asynchronously, the second sentence is not provable.

## Decision: MODEL O or MODEL A

```text
MODEL O — observational enforcement
  required, exact-head, expected-source Governor check
  webhook + reconciliation, measured revocation SLO
  accepts a measured residual detection window
  no claim of atomic provider finality

MODEL A — mediated merge
  direct human merge ceases to be the authority
  the Governor (or another broker) performs the merge behind a final
  synchronous guard
  still leaves a provider-mutation race unless provider evidence becomes
  immutable
```

**Recommendation: MODEL O for v1.** Not because the residual window is
comfortable, but because MODEL A converts the Governor from a reviewer
coordinator into a merge authority with write access to a protected branch
— a large jump in blast radius, credentials and failure modes, in exchange
for closing a window that stays open anyway while the providers hand out
mutable comments instead of certificates. MODEL A also inherits the entire
A1c lifecycle problem at a much higher severity: today an authorization
loss means the gate fails closed; under MODEL A it would mean merges stop
entirely.

MODEL A becomes the right answer only if the residual window is
unacceptable *as a class*. In that case the conclusion is not another
ruleset flag but that the current provider interfaces are too weak for the
guarantee being asked for, and the evidence source itself must change.

## Result

```text
EXPECTED_SOURCE_DESIGN             READY        (design complete; execution gated on A4a)
STATUS_PERMISSION_DELTA            REQUIRED     (documented; unverified locally)
LATEST_HEAD_ENFORCEMENT            SUPPORTED    (documented; to be verified in A4-live)
KNOWN_INVALIDATION_ENFORCEMENT     SUPPORTED    (G1; Governor half already live in A3b)
UNOBSERVED_INVALIDATION_ATOMICITY  NOT_PROVIDED (G2; structural, not a defect)
MERGE_QUEUE_CURRENT_REPO           UNAVAILABLE  (User-owned repository)
SAFE_ISOLATED_RULESET_PROBE        DESIGNED     (dedicated ref, probe context, exact scoping)

A4_ENFORCEMENT_DESIGN: READY_FOR_ISOLATED_LIVE_PROBE
PRODUCTION_ENFORCEMENT: NOT_READY
```

## What this design does NOT settle

- Whether GitHub's expected-source binding actually behaves as documented —
  A4-live must show a same-named check from another App failing to satisfy
  the rule.
- Whether `statuses: write` is truly required, or only required by the UI
  path — A4a must observe the refusal first.
- Whether Evaluate mode exists on this account.
- The actual detection lag under webhook delivery, which is the number the
  residual window ultimately reduces to.

## Next gates

```text
A4a   permission qualification (observe refusal -> explicit owner approval
      -> prove no behavioural widening)                            GATED
A4-live  isolated ruleset probe, enforcement matrix, three ordering
      experiments, residual-window measurement                     GATED BY A4a
A4-prod  production context, expected source, rollout              GATED
```

Carried forward unchanged: a Check Run publishes the Governor's own verdict
and never upgrades a provider carrier into authoritative provenance
(A1b-c3); only a known-current `AUTHORIZED` state may trigger providers
(A1c); authorization loss renders the gate failed, never passed (A1c/A2a);
absence of findings is not positive evidence (A3a); validity predicates are
evaluated against the frozen bundle, never live state (A3b-c1); a write is
not a fact until it has been read back (A3b-c4); `neutral` and `skipped`
are never written, now for the additional reason that GitHub counts them as
passing.
