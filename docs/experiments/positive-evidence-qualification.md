# A3a — Positive evidence qualification: final report

Program `ai-final-review` · Control plane `PhysShell/review-governance` ·
Branch `experiment/positive-evidence-qualification` · Date: 2026-08-22
(UTC). Preregistered protocol:
`experiments/positive-evidence-qualification/PROTOCOL.md`.

## Question

Can the Governor construct a complete, current-head, request-lineage-bound
positive evidence snapshot for **both** providers, sufficient to become a
`SUCCESS_CANDIDATE` under a frozen policy — while publishing no green
light at all?

## Frozen prerequisites

`review-governance` PRs #1–#6 draft, unmerged, untouched. Authorization:
credential generation **G3**, `AUTHORIZED`, App-mediated user carrier
(`PhysShell/45852143/User` + `performed_via_github_app
physshell-review-governor/4669438`). Decision rule `a3a.1`.

## Probe and freeze

Probe PR **#19**, draft, never merged.

```text
repo_id   1335599563
base_sha  047ff1a641e33e0bb8c6b9eea26bb80eea021e08
HEAD_H    a3274d7e7222c3ee9a63c70379a0a06ac5208ba6   (frozen before any trigger)
epoch     epoch-26cd2742db0dab2c  generation 1
auth      generation 3, app_mediated_user
```

## The round

| time | event |
|---|---|
| 05:32:10 | `@codex review` — request generation 1, app-mediated user carrier |
| 05:32:11 | `@coderabbitai full review` — generation 1, same carrier |
| ~05:33 | Codex terminal artifact: "Didn't find any major issues", **Reviewed commit `a3274d7e72`** |
| 05:32:17 | CodeRabbit: **rate limited** — "Review limit reached… next review available in 30 minutes" |
| 06:03:31 | `@coderabbitai full review` — **request generation 2**, issued after the stated window |
| ~06:05 | CodeRabbit sticky updated: "No actionable comments were generated", range `047ff1a6…` → `a3274d7e…` |

`RATE_LIMITED` was scored as **not positive**, exactly as preregistered,
and the retry was a *new request generation* — a distinct comment with its
own id and lineage — never a blind resend of the first.

## Codex qualification — `CODEX_ADVISORY_POSITIVE`

All clauses held simultaneously: request on the app-mediated user carrier;
response actor `199175422`; terminal artifact created after the request;
attested prefix `a3274d7e72` **uniquely** resolving to `HEAD_H` (checked
against `base_sha` and every other commit of the PR); positive wording; no
Codex review and no Codex inline comment for the round; no refusal.

## CodeRabbit qualification — `CODERABBIT_ADVISORY_POSITIVE`

Request accepted on the app-mediated user carrier; actor `136622811`;
fresh activity after the generation-2 request; the surface's review range
terminates at `HEAD_H`; "no actionable comments"; zero inline findings; no
rate limit for this generation. The sticky is stored as
`mutable_advisory_carrier` (id, `updated_at`, full body hash, review
range), never as a certificate, and no check-run status was consulted as
cleanliness evidence.

Neither provider state is called CLEAN anywhere — asserted by test over
both the source and the bundle.

## PositiveEvidenceBundle-v1

```text
epoch_id                epoch-26cd2742db0dab2c
head_sha                a3274d7e7222c3ee9a63c70379a0a06ac5208ba6
auth_generation         3
decision_rule_revision  a3a.1
evidence_hash           4924f18a315bd71691edc3740a2089cd59b22c3f2af69434df6bcd97003d4263
```

Both requests belong to the same epoch; both observations qualified; the
inventory (7 issue comments, 0 reviews, 0 inline comments) contains no
provider findings. The bundle — not the live carriers — is the policy
input.

## Settling window

120 seconds after the last terminal artifact, everything was re-fetched
and compared: `snapshot_stable: true`, no changes, head unchanged, verdict
unchanged. The settling window does not turn a mutable carrier into a
certificate; it only prevents deciding in the gap between "the summary was
updated" and "a finding arrived fifteen seconds later".

## Internal verdict, no green light

```text
Governor verdict: SUCCESS_CANDIDATE   (internal only, publishable: false)
Published check:  ai/final-review-shadow  conclusion = failure
                  head a3274d7e…, app 4669438 physshell-review-governor
                  check run 96990553407
```

The check output states plainly that positive evidence was qualified
experimentally and that publication of success is intentionally disabled
in A3a. `success`, `neutral` and `skipped` are outside the allowed
conclusions in code, so the experiment could not have published a green
light even by mistake.

## Live invalidation — the most useful result

After the round closed, one commit moved the head to
`1d1de2522a602f80f2c696f97d2b2eea931297f2`. Re-evaluating the **frozen
bundle**:

```text
verdict against new head : STALE
mutation check           : INVALIDATED
                           coderabbit: carrier body changed
                           coderabbit: carrier updated_at changed
verdict if AUTH_LOST     : INVALIDATED
```

And the CodeRabbit carrier did not merely change — it changed into
something actively misleading. The same comment now reads:

```text
> ## Review skipped
> Draft detected.
No actionable comments were generated in the recent review. 🎉
Reviewing files that changed from the base of the PR and between
047ff1a641e33e0bb8c6b9eea26bb80eea021e08 and a3274d7e7222c3ee9a63c70379a0a06ac5208ba6.
```

One artifact simultaneously announces that the **new** head's review was
skipped and that "no actionable comments" were generated — for the **old**
range. A human, or a naive gate, reading that comment right now would
conclude the current head is clean. It was never reviewed.

The trap is measurable: re-observing the providers *without* the frozen
bundle still reports both as qualified, because the mutated carrier still
displays its positive line. Only binding the decision to the frozen head
and evidence hash catches the change — which is precisely why the bundle,
and not the carrier, is the policy input.

## Result

```text
CODEX_ADVISORY_POSITIVE_QUALIFICATION       PASS
CODERABBIT_ADVISORY_POSITIVE_QUALIFICATION  PASS  (generation 2)
REQUEST_LINEAGE_COMPLETE                    PASS
CURRENT_HEAD_MATCH                          PASS
AUTHORIZED_AT_DECISION                      PASS
EVIDENCE_INVENTORY_COMPLETE                 PASS
EVIDENCE_SNAPSHOT_STABLE                    PASS
MUTATION_INVALIDATION                       PASS  (observed live)
STALE_HEAD_INVALIDATION                     PASS  (observed live)
NO_PROVIDER_PROVENANCE_OVERCLAIM            PASS

POSITIVE_EVIDENCE_QUALIFICATION: PASS
```

34 tests pass (20 adversarial + 14 live replay); secret scan clean.

## What this DOES prove

- A complete positive evidence snapshot **can** be assembled: two
  providers, one unchanged head, request lineage on a single carrier,
  actor identities, head attestations, and an empty finding inventory,
  frozen into one hashed bundle.
- A rate limit is cleanly separable from a negative result, and recovering
  from it as a *new request generation* preserves lineage.
- The qualification is falsifiable in practice, not only in theory: the
  same policy that produced `SUCCESS_CANDIDATE` produced `STALE` and
  `INVALIDATED` minutes later, from real GitHub state.
- Mutable provider surfaces really do mutate under a decision — observed,
  not modelled — and can end up displaying a positive line about a head
  that was never reviewed.

## What this DOES NOT prove

- Nothing about publishing success. No `success` was written, and the
  revocability of a green check remains entirely untested — that is A3b.
- Not that `SUCCESS_CANDIDATE` is a *sufficient* rule for a real gate. It
  is one frozen rule that one round satisfied; adversarial cases like a
  provider silently amending a review hours later are covered only by the
  invalidation path, not by experience.
- Not that the wording heuristics are robust. "Didn't find any major
  issues" and "no actionable comments" are provider phrasings observed
  today; they are not a contract, and a provider may change them without
  notice.
- Not multi-round or concurrent behaviour; one PR, one epoch, one session.
- `PRODUCTION_ENFORCEMENT` remains `NOT_READY`.

## Production consequence

```text
frozen head + epoch + auth generation
   -> requests with lineage on one carrier
   -> per-provider advisory qualification (never CLEAN)
   -> immutable hashed bundle
   -> settling re-check
   -> internal SUCCESS_CANDIDATE
   -> published: failure (A3a), by construction
```

Two constraints fall out for whatever publishes a green light later. First,
any published success must carry the evidence hash and head it was derived
from, or it cannot be revoked correctly. Second, a green light must be
re-validated against the bundle — not re-derived from the carriers — since
the carriers can move under it while still looking positive.

## Next gate

```text
A3b (gated, separate decision): shadow SUCCESS publication and revocation
    qualified immutable bundle -> Governor SUCCESS
    -> shadow check success on the exact head
    -> provider mutation / auth loss / head change
    -> success revoked or cancelled

A4 (gated): required check / expected source enforcement
```

Carried forward: a Check Run publishes the Governor's own verdict and never
upgrades a provider carrier into authoritative provenance (A1b-c3); only a
known-current `AUTHORIZED` state may trigger providers (A1c); authorization
loss renders the gate failed, never passed (A1c/A2a); absence of findings
is not positive evidence (A3a).
