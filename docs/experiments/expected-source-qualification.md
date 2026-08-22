# A4a-1 — Expected-source qualification under current permissions: report

Program `ai-final-review` · Control plane `PhysShell/review-governance` ·
Branch `experiment/expected-source-qualification` · Date: 2026-08-22 (UTC).
Preregistered protocol:
`experiments/expected-source-qualification/PROTOCOL.md`.

## Question and result

Can a required status check be bound to the Governor App as expected source
(`context` + `integration_id`) while the App holds **no** `statuses`
permission?

```text
CURRENT_PERMISSION_EXPECTED_SOURCE: PASS
```

**GitHub accepted the binding.** This is outcome B — the falsification
branch — so the preregistered consequence applies:

```text
statuses permission: MUST NOT be added
A4a-2 (permission delta): CANCELLED as designed
```

No permission was changed at any point, and none should be.

## What was done, in order

1. **Isolated ref** created from `main`'s head:
   `refs/heads/governor/a4a-expected-source-target` at
   `047ff1a641e33e0bb8c6b9eea26bb80eea021e08`.
2. **Fresh Governor check run** published on that SHA with the probe context
   `ai/final-review-expected-source-probe`, via the **installation token and
   the Checks API only**: run `97011529988`, app `4669438`
   (`physshell-review-governor`), conclusion `failure` (no evidence exists
   for that ref, so it fails closed as everywhere else in this program).
   No Commit Status API call was made.
3. **Active ruleset** `21191731` created with the **owner's** credentials —
   the Governor has no `administration` permission and must never acquire
   one — requiring the probe context **without** `integration_id`, i.e. any
   source. This establishes the documented "pre-existing required check"
   prerequisite.
4. **Isolation readback** (below).
5. **One attempt** to change the requirement to
   `context + integration_id: 4669438`.

## Isolation evidence

```text
ruleset 21191731  enforcement: active
  conditions.ref_name.include = ["refs/heads/governor/a4a-expected-source-target"]
  conditions.ref_name.exclude = []
rulesets in repository        : 1
main branch protection        : 404 "Branch not protected"
```

The documented endpoint `GET /repos/{owner}/{repo}/rules/branch/{branch}`
answers **404 on this account for every ref, including `main`**, so it was
recorded as unavailable and **not** read as "no rules apply". Isolation
rests on what did answer: the ruleset's own scope, the complete ruleset
list, and the continued absence of branch protection on `main`.

## The attempt

```text
09:22:37Z  PUT /repos/PhysShell/evm-from-scratch/rulesets/21191731
           required_status_checks: [{context: ai/final-review-expected-source-probe,
                                     integration_id: 4669438}]
           App permissions at attempt: checks/issues/pull_requests write,
                                       metadata read, statuses ABSENT

           -> HTTP 200
           -> readback: integration_id 4669438 present and persisted
```

The response and the independent readback agree, so this is a stored
configuration, not an echo.

## What this DOES prove

- The REST ruleset API **binds a specific GitHub App as expected source
  without requiring that App to hold `statuses: write`**, at least on this
  account and this path.
- The documented prerequisite list (installed, recently sent a check run,
  associated with an existing required check, **and** `statuses: write`)
  does not bind here as written. The first three were deliberately
  satisfied first, so the refusal — had one come — could not have been
  attributed to them; instead there was no refusal at all.
- Therefore the program does **not** need to widen the Governor's
  permissions to reach expected-source enforcement, and widening them now
  would be an unforced increase in blast radius. `Commit statuses: write`
  is a real additional output channel, not a decorative checkbox.

## What this DOES NOT prove

- **Nothing about enforcement behaviour.** What was accepted is a
  *configuration*. Whether a same-named check from a different source then
  fails to satisfy the rule, and whether a Governor check on the latest head
  satisfies it, is exactly what A4-live must observe. A binding that is
  stored but ineffective would look identical from here.
- Not that the documented requirement is wrong in general — it may bind on
  the **web UI** path (an App picker) rather than the REST path, or differ
  by account type or plan. The claim is scoped to what was observed.
- Nothing about `main`, merges, auto-merge, or the production context: none
  were touched.

## Consequences for the plan

```text
A4a-1  CURRENT_PERMISSION_EXPECTED_SOURCE: PASS
A4a-2  permission delta                     CANCELLED (not required)
STATUS_PERMISSION_DELTA                     NOT_REQUIRED (for REST configuration;
                                            behaviour still unverified)
```

A4-live inherits one extra obligation created by this result: because the
binding was accepted without the permission, it must **prove the binding is
effective**, not merely present. The wrong-source control moves from
"nice to have" to load-bearing — if a foreign check satisfies the rule, the
stored `integration_id` is decoration.

For that control, a wrong-source artifact may be produced with the owner's
credentials (a plain commit status or another App's check), but it must
**not** pass through the Governor runtime. The Governor's own write path
enforces this structurally: its allowlist contains `/check-runs` and
nothing else, and an attempted commit-status write raises rather than
sending — asserted by test.

## Residual state left behind, deliberately

```text
ref     refs/heads/governor/a4a-expected-source-target   (created, unused)
ruleset 21191731  active, matching that one ref, now with integration_id 4669438
check   97011529988  Governor, failure, on 047ff1a6…
```

The protocol preregistered leaving these in place. They match one dedicated
empty ref, `main` is untouched and unprotected, and no PR is affected.
Removal is one call — `DELETE /repos/PhysShell/evm-from-scratch/rulesets/21191731`
plus deleting the ref — but that is a mutation, and the stop rule says the
next mutation is the owner's decision.

## Result matrix

```text
ISOLATED_REF_AND_CONTEXT              ESTABLISHED
GOVERNOR_CHECK_VIA_CHECKS_API_ONLY    PASS
PRE_EXISTING_REQUIRED_CHECK           ESTABLISHED
SCOPE_VERIFIED_MAIN_UNAFFECTED        PASS
EXPECTED_SOURCE_BINDING_ACCEPTED      PASS  (HTTP 200, persisted in readback)
STATUS_PERMISSION_DELTA               NOT_REQUIRED (configuration path)
BINDING_EFFECTIVENESS                 UNVERIFIED (A4-live)
GOVERNOR_STATUS_API_ABSTINENCE        ENFORCED (allowlist + tests)

CURRENT_PERMISSION_EXPECTED_SOURCE: PASS
PRODUCTION_ENFORCEMENT: NOT_READY
```

12 replay tests pass; no secrets in fixtures.

## Next gate

```text
A4-live (gated, owner's decision):
    isolated ref refs/heads/governor/a4-enforcement-target
    context ai/final-review-enforcement-probe
    enforcement matrix incl. the now load-bearing wrong-source control
    three ordering cases A / B / C
    residual-window measurement (detection lag dominates)
```

Carried forward unchanged: `neutral`/`skipped` are never written (GitHub
counts them as passing); a required check binds to the latest SHA; the
Governor writes Check Runs and nothing else.
