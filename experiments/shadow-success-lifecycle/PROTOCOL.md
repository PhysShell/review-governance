# A3b — Shadow SUCCESS publication, revocation and requalification

Status: **PREREGISTERED** — committed before the probe PR existed and
before any provider was triggered.
Program: `ai-final-review` · Control plane: `PhysShell/review-governance` ·
Branch: `experiment/shadow-success-lifecycle`.

## Central question

Can the Governor publish its **own** `success`, derived only from a
qualified immutable evidence bundle, and then reliably **revoke** it the
moment any validity predicate is lost?

```text
GOVERNOR_SHADOW_SUCCESS_CONTRACT: PASS | PARTIAL | FAIL
```

The lifecycle under test, in one line:

```text
qualified bundle -> SUCCESS -> published shadow success
  -> same-HEAD invalidation -> failure
  -> requalification -> SUCCESS again
  -> HEAD change -> cancelled
```

First stage where `ai/final-review-shadow` may carry
`conclusion: success`. Still forbidden: `ai/final-review`, required
checks, rulesets, branch protection, auto-merge, expected-source
enforcement. `PRODUCTION_ENFORCEMENT` stays `NOT_READY`.

## Frozen prerequisites

`review-governance` PRs #1 `d4bf2918…`, #2 `7b6c6c9e…`, #3 `1d6b5ca2…`,
#4 `17ae1349…`, #5 `8d4d171f…`, #6 `2ccd261c…`, #7 `374263d0…` — draft,
unmerged, untouched.

```text
A1 PARTIAL · A1b PASS · A1b-R PASS · A1c VIABLE_WITH_HUMAN_RECOVERY
A2a PASS · A2b PASS · A3a POSITIVE_EVIDENCE_QUALIFICATION: PASS
```

Authorization: credential generation **G3**, `AUTHORIZED`, App-mediated
user carrier. `decision_rule_revision = a3b.1`.

## Fresh round (no reuse of the A3a bundle)

New disposable draft probe PR, merge NEVER. `HEAD_A` read from GitHub and
frozen with `epoch_A` and `auth_generation`. Both providers triggered on
`HEAD_A` through the App-mediated user carrier under A3a's lineage rules:
`RATE_LIMITED` is not positive, a retry is a **new request generation**
after the provider's stated window, and the head must not move. A fresh
`PositiveEvidenceBundle-v1` plus the same 120-second settling re-fetch.

## Pre-publication guard

Immediately before writing `success`, re-verify **all** of:

```text
GitHub current HEAD == bundle.head_sha
epoch == CURRENT
auth == AUTHORIZED
evidence_hash recomputes exactly
both provider qualifications still positive
referenced carrier hashes / updated_at unchanged
no newer provider request generation exists
no new provider finding, review or comment appeared
```

Any single failure → **do not publish success**. There is no best-effort
green state.

## Durable decision before GitHub projection

An append-only SQLite record is committed **before** the Check Run is
touched:

```text
decision_id · epoch_id · head_sha · verdict · bundle_hash · bundle_schema
decision_rule_revision · auth_generation · decided_at · previous_decision_id
```

`evidence_hash` is not audit garnish — it is the identifier of the basis of
the decision. Every `SUCCESS` references its bundle hash, and every later
revocation names which success and which bundle it invalidates.

The Check Run is then updated as a projection: name
`ai/final-review-shadow`, `app.id 4669438`, `head_sha HEAD_A`,
`external_id epoch_A`, `conclusion success`, with output carrying the
Governor verdict, full head, **full bundle SHA-256**, decision rule, epoch,
authorization generation, both provider advisory states, and the explicit
sentence that this is a Governor verdict derived from frozen advisory
evidence and is not provider-issued CLEAN provenance.

## Post-publication re-validation and TOCTOU

The guard, the GitHub write and the re-check are not one transaction. All
three timestamps are recorded — `pre_publish_validation_at`,
`github_success_at`, `post_publish_validation_at` — and the guard is run
again immediately after publication. If it no longer holds, the success is
revoked at once.

A3b does **not** claim to eliminate TOCTOU. It measures and minimises it,
and records explicitly:

> A mutable provider can change after a success has been published and
> before the Governor observes that change.

Measured latencies (decision→publication, publication→post-validation,
invalidation-detection→revocation) become an input to the A4 design gate.

## Phase R1 — same-HEAD revocation

With `HEAD_A` unchanged, a **new** `@codex review` request generation is
posted on the App-mediated user carrier. The mere existence of a newer
request generation for a mandatory provider means the previous bundle is no
longer current — the Governor does not wait for the new review's outcome:

```text
SUCCESS -> EVIDENCE_SUPERSEDED
decision: verdict = EVIDENCE_INVALIDATED
          cause = newer_provider_request_generation
          previous_bundle_hash = <hash of bundle 1>
check:    success -> failure   (same run, same HEAD_A)
```

If GitHub refuses `success → failure`, no workaround is invented silently:
that is an architectural result and is reported as such.

## Phase R1b — requalification on the same HEAD

After the new Codex terminal artifact arrives and qualifies, `bundle_2` is
assembled. CodeRabbit evidence may be reused **only** if the head is
unchanged, its carrier snapshot is unchanged, no newer CodeRabbit
generation exists, the inventory is still complete, and qualification still
passes on a fresh fetch. `bundle_2` necessarily hashes differently because
the Codex lineage changed. Settling window and full guard again, then
`failure → success` on the same run. This also measures whether GitHub
permits `success → failure → success` on one completed check run, and
whether it stays **one** logical Governor check rather than a collection of
green corpses.

## Phase R2 — HEAD revocation

Only after the second live success: a harmless commit moves `HEAD_A →
HEAD_B`. Then bundle 1/2 → `STALE`, epoch A → `STALE`, the old run
`success → cancelled` — and it stays bound to `HEAD_A` forever. A new epoch
opens on `HEAD_B` with a new `ai/final-review-shadow` carrying
`failure` / `NOT_ESTABLISHED`, since no provider evidence exists there.
Live proof required that no `success` exists on `HEAD_B`.

## Append-only decision history

The Check Run is mutable and therefore **not** an audit log. SQLite must
retain the full chain, with no row ever updated or deleted:

```text
D1 SUCCESS               bundle_1
D2 EVIDENCE_INVALIDATED  newer Codex generation
D3 SUCCESS               bundle_2
D4 STALE                 HEAD_A superseded
D5 NOT_ESTABLISHED       HEAD_B
```

A restart must replay the chain into the current projection without ever
parsing the Check Run output as authority.

## Offline adversarial cases

```text
bundle hash mismatch                  -> no success
wrong current HEAD                    -> no success
AUTH_LOST / REAUTH_REQUIRED / REFRESH_OUTCOME_UNKNOWN -> revoke success
newer Codex request generation        -> revoke success
newer CodeRabbit request generation   -> revoke success
provider carrier body hash mutation   -> revoke success
new provider finding                  -> revoke success
wrong provider actor                  -> revoke success
incomplete inventory                  -> revoke success
stale epoch with a perfect old bundle -> no success on the new head
same-named foreign check              -> ignored
old immutable bundle remains audit evidence after the carrier mutates
```

## Verdict matrix

```text
FRESH_POSITIVE_BUNDLE                  PASS/FAIL
PRE_PUBLICATION_GUARD                  PASS/FAIL
DURABLE_SUCCESS_DECISION               PASS/FAIL
SHADOW_SUCCESS_PUBLICATION             PASS/FAIL
GOVERNOR_APP_PROVENANCE                PASS/FAIL
FULL_HEAD_AND_BUNDLE_HASH_PROJECTION   PASS/FAIL
POST_PUBLICATION_REVALIDATION          PASS/FAIL
SAME_HEAD_SUCCESS_REVOCATION           PASS/FAIL
SAME_HEAD_REQUALIFICATION              PASS/FAIL
HEAD_CHANGE_SUCCESS_REVOCATION         PASS/FAIL
NEW_HEAD_FAIL_CLOSED                   PASS/FAIL
APPEND_ONLY_DECISION_HISTORY           PASS/FAIL
RESTART_DURABILITY                     PASS/FAIL
AUTH_LOSS_REVOKES_SUCCESS              PASS/FAIL
PROVIDER_MUTATION_REVOKES_SUCCESS      PASS/FAIL
NO_PROVIDER_PROVENANCE_OVERCLAIM       PASS/FAIL
TOCTOU_WINDOW_RECORDED                 PASS/FAIL

GOVERNOR_SHADOW_SUCCESS_CONTRACT: PASS | PARTIAL | FAIL
```

## Amendments (harness corrections found by the live run)

- **A3b-c1 — the "no newer request generation" predicate must be anchored
  to the bundle, not to live state.** The first implementation compared
  incoming trigger comments against `state["requests"]`, which the act of
  issuing a new request overwrites. Consequence, observed live: posting a
  newer Codex request made that request its own baseline, so the guard
  reported *no* newer generation and the standing success would have
  survived its own invalidation. The baseline is now taken from
  `bundle.observations[provider].request_comment_id` — an immutable field
  of the frozen bundle. This is exactly the failure class the program
  exists to catch: a validity predicate evaluated against mutable state
  erases the evidence that it has been violated.

- **A3b-c2 — one canonical bundle builder.** Construction used a builder
  stamped `a3a.1` and then overwrote the rule revision to `a3b.1` before
  re-hashing, while re-verification called the original builder. The guard
  caught it immediately ("evidence hash does not recompute") — correctly,
  since two slightly different builders agreeing proves nothing. There is
  now a single `build_bundle` used by construction and by every
  re-verification.

## Stop rule

After fixtures, replay and adversarial tests, live check captures, the
decision history, and the report: close the probe PR without merge, open a
draft PR, stop. A4 is not started; a short A4 **design** gate on
TOCTOU/merge semantics and expected-source comes before any required
check.
