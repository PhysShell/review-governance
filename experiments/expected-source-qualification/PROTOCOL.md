# A4a-1 — Expected-source qualification under CURRENT permissions

Status: **PREREGISTERED** — committed before the isolated ref, the probe
check run or the ruleset existed.
Program: `ai-final-review` · Control plane: `PhysShell/review-governance` ·
Branch: `experiment/expected-source-qualification`.

## Question

Can a required status check be bound to the Governor App as **expected
source** (`context` + `integration_id`) while the App holds its current
permissions — that is, **without** `statuses: write`?

```text
CURRENT_PERMISSION_EXPECTED_SOURCE: PASS | FAIL
```

Neither outcome is preregistered as the expected one. Both are honest, and
they lead to opposite next steps.

## Permission change is forbidden in A4a-1

```text
Governor App today:  checks: write · issues: write · pull_requests: write
                     metadata: read · statuses: ABSENT
PERMISSION CHANGE:   FORBIDDEN in this stage
```

The reason is methodological, not ceremonial: adding the permission first
destroys the negative control. Rolling it back afterwards does not restore
it, because the App and installation state would already have changed and
we would have manufactured our own confounder.

## Isolation

A dedicated, disposable ref — deliberately **not** the ref reserved for
A4-live — and a probe-only context:

```text
ref:      refs/heads/governor/a4a-expected-source-target
context:  ai/final-review-expected-source-probe
```

`ai/final-review` and `ai/final-review-enforcement-probe` stay untouched.
The ruleset must match that one ref and nothing else; `main`, the pilot
artifacts and every existing PR must be verifiably outside it, checked by
readback **and** by asking GitHub which rules apply to `main`.

Ruleset administration is performed with the **owner's** credentials, not
the Governor's: the Governor App has no `administration` permission and
must never acquire one. Creating the rule is an owner act; satisfying it is
the Governor's.

## Sequence

Each documented prerequisite is established separately, so that a refusal
cannot be dismissed as "GitHub disliked something unspecified".

1. Create the isolated ref from the current `main` head; freeze its SHA.
2. The Governor publishes a **fresh Check Run** with the probe context on
   that SHA, using its installation token and the Checks API only. No
   Commit Status API call is made, now or later.
3. Create an **active** ruleset scoped to that ref alone, requiring the
   probe context **without** `integration_id` — any source. This
   establishes the pre-existing required check the documentation names as a
   prerequisite.
4. Readback: confirm the exact ref scope, and confirm `main` is subject to
   no rules.
5. **One** attempt to change the requirement to
   `context + integration_id: 4669438`, with the App still lacking
   `statuses: write`. Capture the exact request, response status and body,
   plus a ruleset readback afterwards.

## Outcomes, both preregistered

```text
A. GitHub rejects the expected source
   -> CURRENT_PERMISSION_EXPECTED_SOURCE: FAIL
   -> capture the exact response and readback
   -> STOP before any permission change; return to the owner for an
      explicit decision on exactly `Commit statuses: Read and write`

B. GitHub accepts integration_id = 4669438
   -> CURRENT_PERMISSION_EXPECTED_SOURCE: PASS
   -> the documented requirement does not bind on this live path
   -> the statuses permission MUST NOT be added
   -> STOP and redesign A4a accordingly
```

Outcome B is the falsification branch that matters most: if the API accepts
the binding without `statuses: write`, the App must not be handed an extra
write capability merely because the documentation was generous.

## What A4a-2 will need from this stage

If the result is FAIL, the follow-up must be a **matched pair** — same
repo, same isolated ref, same check context, same App id, same recent
Governor check run, same pre-existing required check, same ruleset payload
— differing only in the *effective installation* permission. The ruleset
and ref created here are therefore left in place deliberately: tearing them
down would destroy the matched baseline. They match one empty dedicated
ref and nothing else.

That stage must also respect GitHub's two-step permission model: changing
the App registration does **not** grant the installation anything until the
installation owner approves the update, so `statuses: write` must be proven
by *installation* readback, not by the registration page.

## Forbidden in A4a-1

Any permission change; any Commit Status API call; touching `main`,
`ai/final-review`, the A4-live ref or any existing PR; merging anything;
auto-merge; branch protection; Codex or CodeRabbit participation — no
provider is involved in this stage at all.

## Stop rule

After the single attempt and its captures: fixtures, replay tests, report,
draft PR, stop. The next permitted mutation is decided by the owner from
this evidence.
