# A2b — Governor-owned shadow Check Run and reconciliation: final report

Program `ai-final-review` · Control plane `PhysShell/review-governance` ·
Branch `experiment/governor-shadow-check` · Date: 2026-08-22 (UTC).
Preregistered protocol: `experiments/governor-shadow-check/PROTOCOL.md`.

## Question

Can the Governor create a Check Run bound to the exact full HEAD SHA, prove
it is the source, refuse to carry an old epoch's verdict onto a new HEAD,
recover after a **missed** `synchronize` webhook, publish its verdict with
provenance, and fail closed when evidence or authorization is absent?

## Frozen prerequisites

`review-governance` PRs #1 `d4bf2918…`, #2 `7b6c6c9e…`, #3 `1d6b5ca2…`,
#4 `17ae1349…`, #5 `8d4d171f…` — draft, unmerged, untouched. Governor App
`4669438`, `checks: write`, installation `155393018`. No provider commands
were issued; no required check, ruleset, branch protection or auto-merge
was touched. `PRODUCTION_ENFORCEMENT` stays `NOT_READY`.

## Two objects, kept apart

```text
ReviewEpoch.status = STALE     Governor's internal supersession marker
Check Run conclusion           GitHub's object
```

GitHub writes `conclusion: stale` itself for long-unfinished runs; an
integrator cannot. Supersession is therefore expressed as `cancelled`, and
the string `stale` appears nowhere in the Governor's write path (asserted
by test).

## Probe PR and epochs

`PhysShell/evm-from-scratch` PR **#18**, draft, never merged.

```text
HEAD_A = c9416bd778b0ec375c8b7e40470192d48f645894   epoch-ccab3cc4c15085e2  gen 1
HEAD_B = 11b0b5d143b3c787a543cb5d7c014a4ab629fd75   epoch-a22f7efbe6ecfe9d  gen 2
```

The epoch is opened from the **PR read out of GitHub**, never from webhook
state — which is what makes reconciliation possible at all.

## Check creation and provenance

`05:16:37Z` — check run **96985023054**, name `ai/final-review-shadow`,
`head_sha` = full 40-character `HEAD_A`, `status: in_progress`,
`external_id` = epoch id, created with the **installation** token.
Read-back showed `app.id 4669438` / slug `physshell-review-governor`.
The name is never accepted as provenance; `app.id` is.

## Fail-closed verdict

`05:16:49Z` — epoch A concluded:

```text
Governor verdict: NOT_ESTABLISHED
Epoch: epoch-ccab3cc4c15085e2 (generation 1)
Head: c9416bd778b0ec375c8b7e40470192d48f645894
Authorization: AUTHORIZED
Codex evidence: ABSENT
CodeRabbit evidence: ABSENT
Decision rule: a2b.1
Gate: FAIL CLOSED
```

GitHub conclusion: **failure**. `success`, `neutral` and `skipped` are
absent from the allowed set by construction — `neutral`/`skipped` can read
as passing for dependent checks, which would poison a future fail-closed
gate. The decision rule has no path to success at all: every combination of
authorization state and (hypothetical) provider state returns `failure`,
asserted by test.

## Controlled missed webhook

The A2a receiver was stopped and its tunnel dead before `HEAD_B` was
pushed. `GET /app/hook/deliveries` after the push records **no delivery at
all** for it, so the Governor demonstrably received nothing. Its state
still read `CURRENT = HEAD_A` — the intended stale-state condition.

Incidental observation from the same delivery log: the two attempts made at
`05:04`–`05:05`, after the A2a secret had been rotated but while the
receiver was briefly still alive, were logged by GitHub as
`Invalid HTTP Response: 401` — the receiver correctly rejecting genuine
GitHub deliveries signed with a secret it no longer held.

## Reconciliation

One reconciler pass, comparing stored head against GitHub:

```text
stored_head  c9416bd7…      github_head  11b0b5d1…
  epoch-ccab3cc4… -> STALE
  check 96985023054 -> cancelled            (HTTP 200)
  epoch-a22f7efb… opened on 11b0b5d1…
  check 96985225301 created on 11b0b5d1…
  verdict NOT_ESTABLISHED -> failure
```

Verified against GitHub afterwards:

| head | run | app | conclusion | external_id |
|---|---|---|---|---|
| `c9416bd7…` (A) | 96985023054 | 4669438 governor | **cancelled** | `epoch-ccab3cc4c15085e2` |
| `11b0b5d1…` (B) | 96985225301 | 4669438 governor | **failure** | `epoch-a22f7efbe6ecfe9d` |

Exactly one Governor run per head. The old check never migrated: its
`head_sha` is still `HEAD_A`. A completed run's conclusion *can* be changed
by the integrator — `failure → cancelled` returned HTTP 200 — which is how
supersession is expressed without touching `stale`.

## Idempotency, durability, mapping recovery

- **Second pass**: `changed: false`, single `noop` action, no third check,
  no duplicate epoch.
- **Durability**: every CLI invocation is a separate process reading the
  SQLite store, so the live sequence already crossed process boundaries
  three times. A dedicated test also kills a Governor between epoch
  creation and reconciliation and asserts the restarted process still
  supersedes correctly and creates no duplicate run.
- **Mapping recovery**: `check_run_id` for epoch B was deliberately erased;
  reconciliation restored it (96985225301) by matching `app.id` **and**
  `external_id` **and** `head_sha` **and** name — creating nothing new.
  Tests cover the adversarial variants: a same-named run from another
  `app.id` is not adopted, and two matching Governor runs produce
  `UNCERTAIN` with the mapping left empty (fail closed).

## Stale-head invariant

The invariant this stage exists for, asserted offline where it can be
posed safely: an epoch carrying a **hypothetical** complete provider CLEAN
is superseded on a new head, and no success check appears on that new head
— the new epoch is a different `external_id`, its own run, and its own
`failure`. No synthetic CLEAN artifact was ever published to GitHub.

## Authorization loss

Reusing A1c/A2a semantics without revoking anything again: `AUTH_LOST`,
`REFRESH_OUTCOME_UNKNOWN` and `REAUTH_REQUIRED` all yield
`AUTHORIZATION_UNAVAILABLE` with conclusion `failure`, and no state permits
provider triggers.

## Reconciliation repairs state, never evidence

Reconciliation restores current head, epoch topology and check presence.
It never writes provider evidence: every decision recorded in the live run
carries `{"codex": "ABSENT", "coderabbit": "ABSENT"}` and an empty
`provider_evidence` list, asserted by test. No provider adapter is called
in A2b at all.

## Provenance contract

The durable record (`review_epochs`, `governor_decisions`, `check_runs`,
`reconciliation_runs`) is the source of truth; the Check output is a
projection carrying epoch id, full head SHA, authorization state, per-
provider evidence status, decision-rule revision and evidence refs. The
Governor never parses a Check Run back into authoritative state — the one
exception being recovery of a lost `check_run_id`, which requires full
identity matching and fails closed on ambiguity.

## Result

```text
GOVERNOR_CHECK_CREATION              PASS
GOVERNOR_APP_PROVENANCE              PASS
FULL_HEAD_SHA_BINDING                PASS
FAIL_CLOSED_NOT_ESTABLISHED          PASS
MISSED_WEBHOOK_RECONCILIATION        PASS
OLD_EPOCH_STALE                      PASS
OLD_CHECK_CANCELLED                  PASS
NEW_HEAD_NEW_CHECK                   PASS
RECONCILIATION_IDEMPOTENCY           PASS
PROCESS_RESTART_DURABILITY           PASS
AUTH_LOSS_FAIL_CLOSED                PASS
PROVIDER_EVIDENCE_NOT_MANUFACTURED   PASS
SPOOFED_CHECK_REJECTED               PASS

GOVERNOR_SHADOW_CHECK_CONTRACT: PASS
```

26 tests pass (15 adversarial + 11 live replay); secret scan clean.

## What this DOES prove

- The Governor can bind a check run to an exact full head SHA and prove
  authorship by `app.id`, not by a name anyone could copy.
- A missed webhook is survivable: comparing stored head against GitHub is
  enough to supersede the old epoch, cancel its run, and build a fresh one
  — no delivery required.
- Supersession is expressible without `stale`: `failure → cancelled` on a
  completed run is accepted by GitHub.
- Uniqueness of "one logical Governor check per (repo, PR, head, name)"
  must be — and can be — enforced by the Governor, since the API permits
  duplicates.
- Fail-closed is structural here, not a policy string: there is no code
  path to `success`, and lost mappings or ambiguity resolve to "not
  established", never to a pass.

## What this DOES NOT prove

- Nothing about enforcement: no check was required, no ruleset touched.
- Nothing about positive verdicts. A2b never establishes anything, so the
  hard question — how advisory provider evidence may become a Governor
  `success` — is untouched and remains the next real design problem.
- Not multi-repo, not concurrent reconcilers: the store is a single-host
  SQLite file with no cross-host locking.
- Not `check_run.rerequested` handling: not exercised live.
- Not long-run behaviour: two epochs, one PR, one session.

## Production consequence

```text
signed GitHub event  (A2a)
   -> durable exact-HEAD epoch      (A2b)
   -> Governor policy state         (A2b)
   -> Governor-owned exact-HEAD Check Run projection  (A2b)
   -> reconciliation when the event never arrives     (A2b)
```

The lower half of the chain now exists end to end and fails closed at every
joint. What is still missing is the top half: a defensible rule turning
advisory, mutable provider evidence into a Governor `success` — without
pretending the provider issued a certificate it never issued.

## Next gate

```text
A3 (gated, separate decision): positive verdict round —
    complete current-head provider evidence bundle
    -> Governor SUCCESS policy
    -> only then: expected-source / required-check design
```

Carried forward: a Check Run publishes the Governor's own verdict and never
upgrades a provider carrier into authoritative provider provenance
(A1b-c3); only a known-current `AUTHORIZED` state may trigger providers
(A1c); authorization loss renders the gate failed, never passed (A1c/A2a);
`PRODUCTION_ENFORCEMENT: NOT_READY`.
