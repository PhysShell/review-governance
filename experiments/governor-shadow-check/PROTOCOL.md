# A2b — Governor-owned shadow Check Run and reconciliation (preregistered)

Status: **PREREGISTERED** — committed before any Check Run was created and
before the probe PR existed.
Program: `ai-final-review` · Control plane: `PhysShell/review-governance` ·
Branch: `experiment/governor-shadow-check`.

## Primary question

Can the Governor

1. create its own Check Run bound to the **exact current full HEAD SHA**;
2. prove the run's source is the Governor App itself;
3. refuse to carry an old epoch's verdict onto a new HEAD;
4. restore correct state after a **missed** `synchronize` webhook;
5. publish its verdict with provenance to the inputs it used;
6. fail closed when provider evidence or authorization is absent?

```text
GOVERNOR_SHADOW_CHECK_CONTRACT: PASS | PARTIAL | FAIL
```

## Frozen prerequisites

Verified before preregistration: `review-governance` PRs #1 `d4bf2918…`,
#2 `7b6c6c9e…`, #3 `1d6b5ca2…`, #4 `17ae1349…`, #5 `8d4d171f…` — all draft,
unmerged, untouched. Governor App `4669438` (`physshell-review-governor`),
`checks: write`, installation `155393018` scoped to
`PhysShell/evm-from-scratch`.

```text
A1     APP_TRIGGER_AUTHORITY: PARTIAL
A1b    CODEX_USER_ATTRIBUTED_TRIGGER_AUTHORITY: PASS
A1b-R  CODERABBIT_USER_ATTRIBUTED_TRIGGER_AUTHORITY: PASS
A1c    USER_AUTH_LIFECYCLE: VIABLE_WITH_HUMAN_RECOVERY
A2a    WEBHOOK_CONTROL_PLANE_CONTRACT: PASS
PRODUCTION_ENFORCEMENT: NOT_READY
```

## Two objects that must never be conflated

```text
ReviewEpoch.status = STALE          Governor's internal supersession marker
Check Run conclusion               GitHub's object
```

GitHub sets `conclusion: stale` itself for long-unfinished runs; an
integrator cannot write it. So on a superseded HEAD:

```text
internal epoch  -> STALE
old Check Run   -> cancelled        (preregistered non-success conclusion)
```

No attempt is ever made to PATCH `stale`.

## Check identity and naming

Created with the **installation token** (not the user access token), which
is what `checks: write` belongs to. Name: `ai/final-review-shadow`. The
production name `ai/final-review` stays reserved and unused.

After every create and every read-back:

```text
check_run.app.id   == 4669438
check_run.app.slug == physshell-review-governor
```

The check's *name* is never accepted as provenance.

## No success, by construction

A2b measures Governor-owned check mechanics, not a positive provider
contract. There is no complete current-head provider evidence bundle in
this experiment, so the only terminal Governor verdicts available are:

```text
NOT_ESTABLISHED            -> GitHub conclusion: failure
AUTHORIZATION_UNAVAILABLE  -> GitHub conclusion: failure
superseded epoch           -> GitHub conclusion: cancelled
```

`success`, `neutral` and `skipped` are forbidden: GitHub may treat
`neutral`/`skipped` as passing for dependent checks, which makes them a bad
foundation for a fail-closed gate. No synthetic `CLEAN` artifact is ever
published to GitHub; hypothetical-CLEAN reasoning lives only in offline
adversarial tests.

## Provenance contract

The Check Run is a **projection**, never the source of truth. The durable
record holds `epoch_id`, `repo_id`, `pr_number`, `head_sha`, `generation`,
`auth_state`, provider-state snapshot, `governor_verdict`,
`decision_rule_revision`, `evidence_refs`, `check_run_id`, timestamps. The
Check output carries only a safe projection of that record.

```text
Governor state -> Check Run          always
Check Run -> Governor state          never (except id-mapping recovery,
                                      see "missed check recovery")
```

## Method

1. **Probe PR**: new disposable draft PR in `PhysShell/evm-from-scratch`
   (`probe/governor-shadow-check`), one documentation file, never merged,
   no provider activity requested. Freeze `HEAD_A` (full 40 characters).
2. **Epoch A**: read the PR **from GitHub**, not from webhook state; open
   `ReviewEpoch A` on `HEAD_A`; create `ai/final-review-shadow` on `HEAD_A`
   with `status: in_progress` and an opaque `external_id` referencing
   Governor state (no secret). Read back and verify head SHA and App
   identity.
3. **Fail-closed verdict**: conclude epoch A with
   `NOT_ESTABLISHED` / `failure`, output naming the epoch, full head,
   authorization state, and `ABSENT` for both providers.
4. **Controlled missed webhook**: push a harmless commit `HEAD_A → HEAD_B`.
   The A2a receiver is not running and its tunnel is dead, so GitHub's
   delivery attempt cannot reach the Governor. The delivery's failure is
   read back from `GET /app/hook/deliveries` as positive evidence that a
   `synchronize` really was missed rather than never sent. Governor state
   deliberately still says `CURRENT = HEAD_A`.
5. **Reconciliation**: the reconciler reads the current PR from GitHub,
   sees `github_head = HEAD_B` against `stored_current = HEAD_A`, and must
   mark epoch A `STALE`, move check A to `cancelled`, open epoch B on
   `HEAD_B`, and create a **new** check on `HEAD_B`. Check A never migrates
   to `HEAD_B`. Epoch B concludes `NOT_ESTABLISHED` / `failure`.
6. **Idempotency**: re-running reconciliation on `HEAD_B` creates no third
   check, no duplicate epoch, and mutates no state. Uniqueness for
   `(repo_id, pr_number, head_sha, check_name)` is enforced by the Governor,
   not by the API — GitHub happily allows many same-named runs on a commit.
7. **Missed-check recovery**: with epoch B present but its `check_run_id`
   missing locally, the reconciler may restore the mapping only from a
   Governor-owned run matching `app.id` **and** `external_id`/epoch
   identity **and** `head_sha` **and** name. Zero matches → create a new
   check; more than one → `UNCERTAIN`, fail closed. A run is never adopted
   because its name matches.
8. **Durability**: state lives in SQLite (`review_epochs`,
   `governor_decisions`, `check_runs`, `reconciliation_runs`). A process
   restart between epoch creation and reconciliation must preserve state
   and produce no duplicate check — otherwise "reconciliation after a
   missed webhook" only works while the Governor never crashes.
9. **Offline adversarial tests**: stale epoch carrying a *hypothetical*
   provider CLEAN must never yield a success check on the new head;
   `AUTH_LOST` and `REFRESH_OUTCOME_UNKNOWN` yield
   `AUTHORIZATION_UNAVAILABLE` / `failure` with triggers forbidden; a
   same-named run from a different `app.id` is rejected as provenance;
   reconciliation repairs control-plane state and never manufactures
   provider evidence.

`check_run.rerequested` is not used to open epochs; if handled at all it
maps to a reconciliation request, never to a verdict. Not tested live.

## Result matrix

```text
GOVERNOR_CHECK_CREATION              PASS/FAIL
GOVERNOR_APP_PROVENANCE              PASS/FAIL
FULL_HEAD_SHA_BINDING                PASS/FAIL
FAIL_CLOSED_NOT_ESTABLISHED          PASS/FAIL
MISSED_WEBHOOK_RECONCILIATION        PASS/FAIL
OLD_EPOCH_STALE                      PASS/FAIL
OLD_CHECK_CANCELLED                  PASS/FAIL
NEW_HEAD_NEW_CHECK                   PASS/FAIL
RECONCILIATION_IDEMPOTENCY           PASS/FAIL
PROCESS_RESTART_DURABILITY           PASS/FAIL
AUTH_LOSS_FAIL_CLOSED                PASS/FAIL
PROVIDER_EVIDENCE_NOT_MANUFACTURED   PASS/FAIL
SPOOFED_CHECK_REJECTED               PASS/FAIL

GOVERNOR_SHADOW_CHECK_CONTRACT: PASS | PARTIAL | FAIL
```

## Forbidden in A2b

Triggering Codex or CodeRabbit; publishing `success` (or `neutral` /
`skipped`); creating `ai/final-review`; making any check required;
ruleset or branch-protection changes; auto-merge; testing production
expected-source enforcement; multi-repo rollout; merging PRs #1–#5 or the
probe PR; rewriting frozen experiments. Even with everything green,
`PRODUCTION_ENFORCEMENT` stays `NOT_READY`.

## Stop rule

After live check captures, the missed-webhook push, reconciliation, the
restart test, sanitized fixtures, adversarial tests, a secret scan, and the
report: close the probe PR without merge, open a draft PR, stop. The next
boundary is a separate decision.
