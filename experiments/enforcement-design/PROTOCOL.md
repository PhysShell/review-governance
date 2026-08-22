# A4-design — Enforcement semantics and residual TOCTOU (preregistered scope)

Status: **PREREGISTERED** — design stage only.
Program: `ai-final-review` · Control plane: `PhysShell/review-governance` ·
Branch: `experiment/enforcement-design`.

## Central question

```text
What exactly can GitHub required-check enforcement guarantee for a Governor
verdict derived from mutable external evidence, and what race remains
fundamentally outside that guarantee?
```

Verdict:

```text
A4_ENFORCEMENT_DESIGN:
  READY_FOR_ISOLATED_LIVE_PROBE | NEEDS_ARCHITECTURAL_CHANGE | INCONCLUSIVE
```

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

`review-governance` PRs #1–#8 are frozen evidence: draft, unmerged, and not
to be touched. Freezing an experiment is not accepting a branch — merging
PR #8 still requires a fresh Codex + CodeRabbit round on its exact current
head under the program's own bootstrap invariant.

## Forbidden in this stage

No live enforcement. No permission change. No ruleset creation, mutation or
deletion. No branch protection. No auto-merge. No merge. No change to any
check name. No `ai/final-review` usage — the production context stays
reserved and untouched. Nothing in this stage writes to GitHub at all
beyond read-only queries.

## Two guarantees, stated separately

The design must never say "the Governor prevents invalid merges". It must
distinguish:

```text
G1  OBSERVED_STATE_ENFORCEMENT
    If the Governor has observed any condition invalidating its SUCCESS,
    that condition is projected as non-success before any Governor-initiated
    action, and GitHub blocks merge once that projection is confirmed.

G2  UNOBSERVED_EXTERNAL_INVALIDATION_ATOMICITY
    If a provider mutates evidence and the Governor has not yet observed it,
    can GitHub nevertheless prevent merge?
```

Working hypothesis to be argued and, where possible, evidenced: **G1
achievable, G2 not provided by required checks alone.** A4 must measure the
residual risk surface rather than "fix" G2 with a delay.

## Four state domains, never assumed atomic

```text
provider reality
Governor observed / durable state
GitHub Check Run projection
GitHub merge decision
```

## Normative authorization predicate

```text
may_authorize_action :=
       epoch == CURRENT
    && auth == AUTHORIZED
    && decision == SUCCESS
    && projection == CONFIRMED
    && projection.head_sha == current_full_HEAD
    && projection.app.id == GOVERNOR_APP_ID
    && projection.bundle_hash == decision.bundle_hash
    && no locally-known invalidation exists
```

`external_success_may_exist` is never an authorization predicate (A3b-c4).
This stage implements the predicate as a pure, offline, adversarially
tested function — specification as code, with no network access.

## Deliverables

1. This scope note.
2. `harness/policy.py` — the normative predicate, pure and side-effect free.
3. Adversarial tests for every clause of the predicate.
4. Read-only fact fixtures: App permissions, target repo ownership and
   settings.
5. `docs/design/a4-enforcement-semantics.md` — the design report, ending in
   the required matrix:

```text
EXPECTED_SOURCE_DESIGN             READY / BLOCKED
STATUS_PERMISSION_DELTA            REQUIRED / NOT_REQUIRED
LATEST_HEAD_ENFORCEMENT            SUPPORTED
KNOWN_INVALIDATION_ENFORCEMENT     SUPPORTED
UNOBSERVED_INVALIDATION_ATOMICITY  NOT_PROVIDED / UNKNOWN
MERGE_QUEUE_CURRENT_REPO           UNAVAILABLE
SAFE_ISOLATED_RULESET_PROBE        DESIGNED / NOT_DESIGNED

A4_ENFORCEMENT_DESIGN: ...
PRODUCTION_ENFORCEMENT: NOT_READY
```

## Stop rule

After the design report: open a draft PR, stop. No permissions, rulesets or
check-name changes. A4-live is a separate decision, and A4a (permission
qualification) precedes it.
