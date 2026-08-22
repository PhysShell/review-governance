# A3b — Shadow SUCCESS publication, revocation and requalification: final report

Program `ai-final-review` · Control plane `PhysShell/review-governance` ·
Branch `experiment/shadow-success-lifecycle` · Date: 2026-08-22 (UTC).
Preregistered protocol: `experiments/shadow-success-lifecycle/PROTOCOL.md`
(amendments A3b-c1, A3b-c2 recorded from the live run).

## Question

Can the Governor publish its **own** `success`, derived only from a
qualified immutable evidence bundle, and then reliably revoke it the moment
any validity predicate is lost?

## Frozen prerequisites

`review-governance` PRs #1–#7 draft, unmerged, untouched. Auth: credential
generation **G3**, `AUTHORIZED`, App-mediated user carrier. Decision rule
`a3b.1`. Probe PR **#20**, draft, never merged, `HEAD_A =
448dd46f9d73b0c15e15057f48651fda7c2b7048`, epoch `epoch-d6743339efed1671`.

## The lifecycle, as it happened

| time | step | result |
|---|---|---|
| 06:36:45–47 | requests generation 1 (both providers, app-mediated user carrier) | Codex qualified; **CodeRabbit rate-limited** |
| 07:12:58 | CodeRabbit **request generation 2**, after its stated 26-minute window | accepted |
| 07:14:20 | both providers advisory-positive on `HEAD_A` | — |
| ~07:17 | `bundle_1` = `01e8ed0927c5dbc4…` | — |
| 07:19:32 | pre-publication guard | passed, no failures |
| 07:19:34 | **first Governor `success`** on run `96998302115` | HTTP 200 |
| 07:19:37 | post-publication guard | passed |
| 07:19:52 | new **Codex request generation 2** posted on the same head | — |
| 07:20:48 | newer generation detected | `EVIDENCE_INVALIDATED` |
| 07:20:49 | **success → failure**, same run, same head | HTTP 200 |
| 07:21:57 | requalified: both providers positive again | — |
| ~07:22 | `bundle_2` = `ef6f89dd74d49891…` (different hash, new Codex lineage) | — |
| 07:24:19 | **failure → success**, same run | HTTP 200 |
| ~07:26 | head moved to `HEAD_B = 561b7f72bbdafbce63a844fb709ccf5e5b44b4dd` | — |
| ~07:26 | old run **success → cancelled**, still bound to `HEAD_A`; new run `96998879863` on `HEAD_B` → `failure` | HTTP 200 |

`RATE_LIMITED` was again scored as *not positive*, and recovery was a new
request generation with its own comment id and lineage — never a resend.

## Publication guard and provenance

The published success carried, in its own output: the Governor verdict, the
**full** head SHA, the **full** bundle SHA-256, the decision rule, the
epoch, the authorization generation, both provider advisory states, and the
sentence that this is a Governor verdict derived from frozen advisory
evidence and not provider-issued CLEAN provenance. The code enforces this
rather than trusting the caller: `success` cannot be written without the
64-character evidence hash, and is rejected unless that hash appears in the
output. `neutral` and `skipped` remain refused outright.

## Same-head revocation — the point of the experiment

A2b had already shown that a *head change* can invalidate a check. A3b
tested the harder property: revoking a green verdict while the commit stays
current.

```text
07:19:34  success   run 96998302115  head 448dd46f…  bundle 01e8ed09…
07:19:52  newer Codex request generation posted
07:20:48  detected
07:20:49  failure   run 96998302115  head 448dd46f…  (same run, same head)
```

The revocation is caused by the **existence** of a newer request for a
mandatory provider, not by its outcome — the Governor did not wait for that
review to finish, and the whole detection-to-revocation step took one
second. GitHub accepted `success → failure` and later `failure → success`
on the same completed run: the lifecycle stays **one logical Governor
check**, not a graveyard of green corpses.

## Head-change revocation

```text
old run 96998302115  head 448dd46f…  conclusion cancelled   (never migrated)
new run 96998879863  head 561b7f72…  conclusion failure     epoch-78deabcd…
any success on either head: none
```

## Append-only decision history

```text
D1 SUCCESS               bundle 01e8ed09…
D2 EVIDENCE_INVALIDATED  cause newer_provider_request_generation, invalidates D1/01e8ed09…
D3 SUCCESS               bundle ef6f89dd…
D4 STALE                 cause head_superseded, invalidates D3/ef6f89dd…
D5 NOT_ESTABLISHED       head 561b7f72…
```

Exactly the preregistered sequence, fully linked by `previous_decision_id`,
with `UPDATE` and `DELETE` blocked by database triggers. Both successes
survive in history — revocation adds a row, it never erases one. Replay
reproduces the current projection (`cancelled` on `HEAD_A`, `failure` on
`HEAD_B`) from the chain alone, and that projection matches what GitHub
shows, without ever parsing check output as authority.

## Harness corrections found by the live run

- **A3b-c1** — the "no newer request generation" predicate was first
  evaluated against live state, which the act of issuing a new request
  overwrites. Live consequence: the newer Codex request became its own
  baseline and the guard reported no newer generation, so the standing
  success would have survived its own invalidation. The baseline now comes
  from the immutable bundle. This is precisely the failure class this
  program exists to catch — a validity predicate read from mutable state
  erases the evidence that it has been violated.
- **A3b-c2** — construction and re-verification used two slightly different
  bundle builders (rule `a3a.1` vs `a3b.1`), and the guard rejected the
  mismatch immediately. Both now share one canonical builder: "the hash
  recomputes" must mean something stronger than "two builders happened to
  agree".

## TOCTOU — measured, not eliminated

```text
decision -> publication          2 s   (07:19:32 -> 07:19:34)
                                 1 s   (07:24:18 -> 07:24:19)
publication -> post-validation   3 s   (07:19:34 -> 07:19:37)
invalidation detected -> revoked 1 s   (07:20:48 -> 07:20:49)
```

The guard, the GitHub write and the re-check are three separate operations,
so:

> A mutable provider can change after a success has been published and
> before the Governor observes that change.

A3b does not remove that window; it bounds and records it. Detection here
was driven by an explicit reconciliation call — with polling or webhooks
the detection lag, not the revocation itself, becomes the dominant term.
For a **required** check the consequence is concrete: the check is green
during that window, and a human can press Merge inside it. That is an input
to the A4 design gate, not something A3b resolves.

## Correction round A3b-c3 / A3b-c4 (probe PR #21)

The first run proved a standing success is *revocable*; it did not prove
the Governor never knowingly leaves one standing. Two gaps were closed.

**c3 — pre-request invalidation.** In the first run the new Codex request
was posted at `07:19:52` and the success was revoked at `07:20:49`: the
green verdict stood for ~57 seconds after the Governor itself performed
the act that made its basis non-current. Ordering is now structural — a
plain `trigger` is refused while a success stands, and the only path to a
new round is `rerun`, which invalidates, patches, confirms, and only then
asks the provider. Live, on head `d20d3706…`:

```text
invalidation_decided_at        08:50:47Z
failure_patch_attempted_at     08:50:47Z
failure_confirmed_at           08:50:49Z   (projection CONFIRMED, observed failure)
provider_request_created_at    08:50:49Z   (comment 5379406791)
```

The last two collide at GitHub's one-second resolution, so strictness is
**not** claimed from those stamps. It rests on the local monotonic gap
measured around the calls — `1.434 s` between confirmation and the request
— and is corroborated independently by GitHub's own clock, where the check
run's `completed_at` (`08:50:47Z`, conclusion `failure`,
`Governor: EVIDENCE_INVALIDATED`) precedes the request's `created_at`
(`08:50:49Z`). If the provider POST's outcome had been indeterminate, the
recorded state would be `REQUEST_OUTCOME_UNKNOWN` with the check left
failing; the old success is never restored automatically.

**c4 — projection confirmation.** Publication previously treated the PATCH
response body as proof. Projections now carry `PENDING` → `CONFIRMED` /
`OUTCOME_UNKNOWN` / `FAILED`, and every write runs one path: durable
decision → `PENDING` → PATCH → **independent GET of that exact run** →
settle. Live: run `97008202609` reached `CONFIRMED` only after the readback
returned `success` with matching head, app, `external_id` and bundle hash.

Adversarially: a lost PATCH response with a readback showing `success`
settles `CONFIRMED`; a PATCH that never took effect settles `FAILED`; an
unreadable run settles `OUTCOME_UNKNOWN` and never resolves upward.

**Terminology fixed with it.** Two questions that must never share a
variable:

```text
external_success_may_exist   GitHub may be showing green right now
                             -> the Governor must clean up before acting
effective_gate_validity      ESTABLISHED only for a confirmed success
                             -> every uncertain projection is NOT_ESTABLISHED
```

`may_authorize_action` is the only field that may gate an action, and it is
true solely for a confirmed success. An unsettled projection carries an
explicit hazard note: it is *neither* an established success *nor* an
established revocation. Without that split, a well-meaning
`if standing_success:` eventually becomes permission.

## Result

```text
PRE_REQUEST_SUCCESS_INVALIDATION       PASS (live)
REQUEST_OUTCOME_UNKNOWN_FAIL_CLOSED    PASS (offline)
PROJECTION_PENDING_STATE               PASS
PROJECTION_EXACT_READBACK              PASS (live)
AMBIGUOUS_PROJECTION_RECONCILIATION    PASS (offline)

FRESH_POSITIVE_BUNDLE                  PASS
PRE_PUBLICATION_GUARD                  PASS
DURABLE_SUCCESS_DECISION               PASS
SHADOW_SUCCESS_PUBLICATION             PASS
GOVERNOR_APP_PROVENANCE                PASS
FULL_HEAD_AND_BUNDLE_HASH_PROJECTION   PASS
POST_PUBLICATION_REVALIDATION          PASS
SAME_HEAD_SUCCESS_REVOCATION           PASS
SAME_HEAD_REQUALIFICATION              PASS
HEAD_CHANGE_SUCCESS_REVOCATION         PASS
NEW_HEAD_FAIL_CLOSED                   PASS
APPEND_ONLY_DECISION_HISTORY           PASS
RESTART_DURABILITY                     PASS
AUTH_LOSS_REVOKES_SUCCESS              PASS (offline)
PROVIDER_MUTATION_REVOKES_SUCCESS      PASS (offline)
NO_PROVIDER_PROVENANCE_OVERCLAIM       PASS
TOCTOU_WINDOW_RECORDED                 PASS

GOVERNOR_SHADOW_SUCCESS_CONTRACT: PASS
```

61 tests pass (17 adversarial + 33 live replay + 11 c3/c4 adversarial and live); secret scan clean.

## What this DOES prove

- A Governor `success` can be made structurally inseparable from its basis:
  the code cannot write one without the evidence hash, and the published
  artifact names the exact head and bundle it came from.
- A green verdict is revocable **while the commit stays current**, in about
  a second, triggered by the existence of newer provider activity rather
  than by its result.
- GitHub permits `success → failure → success → cancelled` on one check
  run, so the Governor can keep a single logical check per head instead of
  accumulating stale green artifacts.
- The audit trail is separable from the projection: an append-only chain
  reproduces the current state without trusting anything read back from
  GitHub.

## What this DOES NOT prove

- Nothing about enforcement. The check was never required; no ruleset,
  branch protection, auto-merge or expected-source enforcement exists.
- Not that revocation is timely under adversarial conditions: detection was
  an explicit call, not a race against a merge button. c3 removes the case
  where the Governor *itself* causes the invalidation — it says nothing
  about invalidation arriving from outside.
- Not that the wording heuristics for "advisory positive" are stable — they
  are today's provider phrasings, not a contract.
- Not multi-repo, multi-PR or concurrent-worker behaviour; one PR, one
  epoch chain, one process at a time.
- `PRODUCTION_ENFORCEMENT` remains `NOT_READY`.

## Production consequence

```text
frozen bundle -> guard -> durable decision -> published success (+hash, +head)
             -> newer provider generation / mutation / auth loss -> failure
             -> requalification -> success
             -> head change -> cancelled, new head fails closed
```

The lower and middle parts of the chain now exist end to end, and the
Governor no longer creates its own window: any invalidation it causes is
published and confirmed before the cause exists. The remaining gap is not
"can the light be turned off" but "can it be turned off before someone acts
on it" when the cause comes from outside — a merge-semantics problem, not a
Checks API problem.

## Next gate

```text
A4-design (gated, separate decision): TOCTOU and merge semantics
    - what a required check actually blocks, and when
    - expected-source enforcement (which App may satisfy the check)
    - what happens to an in-flight merge when a success is revoked
    - whether a shadow-to-required transition needs a quiet period

  Constraints already known and to be verified there, not assumed:
    - a required check binds to the latest commit SHA; a green older head
      does not satisfy a new one
    - success, skipped and neutral all count as passing for required
      checks, which is why excluding neutral/skipped structurally was a
      necessity rather than taste
    - expected-source exists (a required check can be bound to a specific
      App via the ruleset integration_id), but selecting an App reportedly
      requires statuses: write and a recent check run — the Governor holds
      checks: write only, so this needs its own qualification rather than a
      quiet permission widening
    - ruleset Evaluate mode must be measured on this account rather than
      assumed available

A4 (gated): required check / expected source enforcement
```

Carried forward: a Check Run publishes the Governor's own verdict and never
upgrades a provider carrier into authoritative provenance (A1b-c3); only a
known-current `AUTHORIZED` state may trigger providers (A1c); authorization
loss renders the gate failed, never passed (A1c/A2a); absence of findings is
not positive evidence (A3a); a validity predicate must be evaluated against
the frozen bundle, never against live state (A3b-c1).
