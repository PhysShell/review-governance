# A3a — Positive evidence qualification (preregistered)

Status: **PREREGISTERED** — committed before the probe PR existed and
before any provider was triggered.
Program: `ai-final-review` · Control plane: `PhysShell/review-governance` ·
Branch: `experiment/positive-evidence-qualification`.

## Central question

```text
Can the Governor construct a complete, current-head,
request-lineage-bound positive evidence snapshot for BOTH providers
that is sufficient to become a SUCCESS_CANDIDATE under a frozen policy?
```

```text
POSITIVE_EVIDENCE_QUALIFICATION: PASS | PARTIAL | FAIL
```

A3a publishes **no** green light. The GitHub Check Run stays
`ai/final-review-shadow` with `conclusion: failure`; `SUCCESS_CANDIDATE`
exists only inside Governor state. Publication of success is A3b, gated by
this experiment.

## Frozen prerequisites

`review-governance` PRs #1 `d4bf2918…`, #2 `7b6c6c9e…`, #3 `1d6b5ca2…`,
#4 `17ae1349…`, #5 `8d4d171f…`, #6 `2ccd261c…` — draft, unmerged,
untouched. Program state carried in:

```text
A1     APP_TRIGGER_AUTHORITY: PARTIAL
A1b    CODEX_USER_ATTRIBUTED_TRIGGER_AUTHORITY: PASS
A1b-R  CODERABBIT_USER_ATTRIBUTED_TRIGGER_AUTHORITY: PASS
A1c    USER_AUTH_LIFECYCLE: VIABLE_WITH_HUMAN_RECOVERY
A2a    WEBHOOK_CONTROL_PLANE_CONTRACT: PASS
A2b    GOVERNOR_SHADOW_CHECK_CONTRACT: PASS
PRODUCTION_ENFORCEMENT: NOT_READY
```

Authorization must be `AUTHORIZED` on the App-mediated user carrier
(`user = PhysShell/45852143/User`,
`performed_via_github_app = physshell-review-governor/4669438`),
credential generation G3.

## Probe and freeze

A new disposable **draft** PR in `PhysShell/evm-from-scratch`
(`probe/positive-evidence-qualification`), one harmless documentation file,
never merged. Before any trigger, the Governor freezes:

```text
repo_id · pr_number · base_sha · full head_sha (HEAD_H)
ReviewEpoch (epoch_id, generation)
auth_generation
decision_rule_revision = a3a.1
```

If HEAD changes at any point during the round, the whole bundle is `STALE`
and the round ends — no partial reuse.

## Fresh round, both providers, one unchanged head

`@codex review` and `@coderabbitai full review`, both posted with the App
**user access token**. Each request records `provider_request_id`, GitHub
comment id, `created_at`, exact HEAD at request, auth generation, review
generation. A retry after a rate limit is a **new request generation**,
never a blind re-send of the old one.

## Codex qualification — `CODEX_ADVISORY_POSITIVE`

Never called CLEAN. All of the following must hold simultaneously:

```text
request carrier == app_mediated_user
response actor id == 199175422
terminal response created after this request
"Reviewed commit: <prefix>" resolves UNIQUELY to HEAD_H
terminal body is a positive / no-major-issues carrier
no Codex-authored inline finding for this round / current head
no Codex-authored finding review for this round / current head
no refusal, no UNAVAILABLE, no malformed evidence
```

Uniqueness of the prefix is checked against the PR's own commit set: it
must prefix `HEAD_H` and must not prefix `base_sha` or any other commit of
the PR.

The immutable Governor snapshot (comment id, `created_at`, `updated_at`,
body hash, actor id, reviewed prefix, resolved full SHA, inventory of
Codex reviews and comments, snapshot timestamp) is the policy input —
never the mutable comment itself.

## CodeRabbit qualification — `CODERABBIT_ADVISORY_POSITIVE`

Never called CLEAN. `RATE_LIMITED` is **not** positive. All of:

```text
App-mediated request accepted
provider actor id == 136622811
fresh review activity after the exact request
review range terminates at HEAD_H
terminal / current provider surface says no actionable comments
no actionable inline or review findings for this fresh round / current head
no rate limit, no refusal
```

A check-run `status: success` is never used as cleanliness evidence — it
has already been observed alongside a finding. The sticky comment is
stored as `mutable_advisory_carrier` with comment id, `updated_at`, full
body hash, run id, review range and a full inventory snapshot.

## PositiveEvidenceBundle-v1

One canonical, immutable bundle:

```text
{ epoch_id, head_sha, auth_generation, decision_rule_revision,
  requests: {codex, coderabbit},
  observations: {codex, coderabbit},
  inventory_cutoff, evidence_hash }
```

The bundle qualifies as positive only if **all** hold:

```text
current GitHub HEAD == bundle.head_sha
auth == AUTHORIZED
both requests belong to the same ReviewEpoch
both providers qualified advisory-positive
no provider findings exist in the captured inventories
all referenced actor ids match
all head attestations / ranges match the current head
```

## Settling window

After the last terminal provider artifact, a preregistered **120-second**
settling interval, then a full re-fetch (PR head, issue comments, reviews,
inline comments, provider surfaces). Only if the snapshot is semantically
identical:

```text
EVIDENCE_SNAPSHOT_STABLE: PASS
```

This does not turn a mutable carrier into a certificate. It only prevents
deciding in the gap between "the summary was updated" and "a finding
arrived fifteen seconds later".

## Non-monotonicity invariant

```text
SUCCESS is not monotonic even while HEAD is unchanged.
```

If any referenced artifact changes after qualification — body hash,
`updated_at`, a new provider finding, a new review, a changed provider
result — then `SUCCESS_CANDIDATE → INVALIDATED` pending full
re-evaluation. Likewise for `HEAD` change (`→ STALE`), `AUTH_LOST`,
`REAUTH_REQUIRED`, `REFRESH_OUTCOME_UNKNOWN` (`→ INVALIDATED`). An
immutable decision must never sit on top of mutable inputs.

## Adversarial matrix (offline)

```text
Codex positive + Rabbit finding                 -> FAIL
Codex finding + Rabbit positive                 -> FAIL
one provider missing                            -> FAIL
one provider rate-limited                       -> FAIL
old-head positive                               -> STALE
wrong actor                                     -> FAIL
right text / wrong request generation           -> FAIL
right name / wrong App-mediated carrier         -> FAIL
Codex prefix not uniquely resolving HEAD        -> FAIL
Rabbit status success + actionable finding      -> FAIL
sticky positive then body mutation              -> INVALIDATED
positive bundle then HEAD push                  -> STALE
positive bundle then AUTH_LOST                  -> INVALIDATED
absence of findings without terminal positive evidence != positive
```

## Result matrix

```text
CODEX_ADVISORY_POSITIVE_QUALIFICATION       PASS/FAIL
CODERABBIT_ADVISORY_POSITIVE_QUALIFICATION  PASS/FAIL
REQUEST_LINEAGE_COMPLETE                    PASS/FAIL
CURRENT_HEAD_MATCH                          PASS/FAIL
AUTHORIZED_AT_DECISION                      PASS/FAIL
EVIDENCE_INVENTORY_COMPLETE                 PASS/FAIL
EVIDENCE_SNAPSHOT_STABLE                    PASS/FAIL
MUTATION_INVALIDATION                       PASS/FAIL
STALE_HEAD_INVALIDATION                     PASS/FAIL
NO_PROVIDER_PROVENANCE_OVERCLAIM            PASS/FAIL

POSITIVE_EVIDENCE_QUALIFICATION: PASS | PARTIAL | FAIL
```

## Forbidden in A3a

Publishing `conclusion: success` (or `neutral` / `skipped`); required
checks; rulesets; expected-source enforcement; branch protection;
merging anything, including the probe PR; auto-merge; writing the words
"provider CLEAN" as a state anywhere. Even with everything green,
`PRODUCTION_ENFORCEMENT` stays `NOT_READY`.

## Stop rule

After live captures, qualification, the settling re-check, adversarial
tests, sanitized fixtures, a secret scan and the report: close the probe PR
without merge, open a draft PR, stop. A3b (shadow SUCCESS publication and
its revocation) is a separate decision.
