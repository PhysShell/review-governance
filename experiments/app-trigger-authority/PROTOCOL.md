# A1 — App-authored provider trigger authority (preregistered protocol)

Status: **PREREGISTERED** — written before GitHub App registration and before
any live capture.
Program: `ai-final-review` · Control plane: `PhysShell/review-governance` ·
Branch: `experiment/app-trigger-authority`.

## Question

Do Codex and CodeRabbit accept a review trigger when the issue comment is
created by a **GitHub App installation identity** (`<slug>[bot]`), not a
human/OAuth user?

Tested independently:

```text
App -> @codex review
App -> @coderabbitai full review
```

Terminal verdict grammar (exactly one):

```text
APP_TRIGGER_AUTHORITY: PASS | PARTIAL | FAIL
```

## Frozen prerequisites (verified before the experiment)

- `PhysShell/evm-from-scratch` PR #12 — frozen shadow-pilot baseline; head
  must equal `e29621f54a63b50db4afb77b608d6c3a4d533812` and the PR must not
  be modified by this experiment (read-only evidence source).
- PR #11 — closed without merge; SHA chain immutable.
- If the frozen head diverges, the experiment stops and the divergence is
  reported as a separate documented follow-up; nothing is "fixed" in-band.

## Estimands — two per provider, never conflated

- **E-cmd (command handled):** the provider demonstrably processed the
  App-authored command — acknowledgement reaction on the exact request
  comment, acknowledgement comment, review started, or an explicit refusal
  addressed to the command (e.g. a rate-limit notice). `RATE_LIMITED` is a
  positive E-cmd observation.
- **E-term (terminal review):** the provider emitted a terminal review
  artifact attributable to the request.

The verdict is computed from **E-cmd only**. E-term is recorded but not
required; nobody waits for quota recovery to upgrade an outcome.

## Method

1. Register a minimal **private** GitHub App owned by `PhysShell`
   (spec: `app-manifest.json`). Permissions at most:
   `metadata: read`, `issues: write`, `pull_requests: read`,
   `checks: write` (reserved for A2). **Not granted:** statuses, contents,
   actions, administration, deployments, secrets. No webhook receiver in A1.
   Install on **only** `PhysShell/evm-from-scratch`.
2. Auth transport: private key → App JWT → installation access token →
   GitHub REST (`harness/app_api.py`). Secrets live only in
   `~/.config/review-governor/` (0600); tokens are minted in-process and
   never printed or stored.
3. Identity readback **before any trigger** (`harness/identity.py` +
   a benign no-mention probe comment): the acting author must be
   `<slug>[bot]` with `user.type == "Bot"`; record app id, installation id,
   bot login, bot numeric actor id into `app-identity.json`.
4. **Probe PR:** a new disposable **draft** PR in `evm-from-scratch`
   touching only `governor/pilot/app-trigger-probe.md`. Never merged.
   E1/E5 and all pilot artifacts untouched. Probe PR branch/PR creation uses
   ordinary user authority (git over ssh + `gh`); only the **trigger
   comments** must be App-authored — that is the estimand.
5. Baseline inventory snapshot of the probe PR (negative-control window)
   before the first trigger.
6. Trigger sequence, each via `harness/post_trigger.py` (installation
   token): fetch PR → record exact `head_sha` → post App-authored
   `@codex review` → capture envelope; then App-authored
   `@coderabbitai full review` → capture envelope. No OAuth fallback under
   any circumstances.
7. Observation (`harness/observe.py`, read-only): poll issue comments, PR
   reviews, inline review comments, reactions on the exact request comments.
   **Window per provider: 30 min primary; if that provider produced zero
   signals, one extension to 60 min total; then classify
   `NO_OBSERVED_START`.** Silence is never interpreted beyond that label.

## Classification (fixed before data)

Per provider, `command_handled = YES` iff a provider-authored artifact
exists that (a) was created after the request comment, and (b) is
attributable to it — a reaction on the exact request comment, a reply
referencing it, or a review/acknowledgement inside the observation window
with the baseline showing no competing trigger.

```text
                    command handled    terminal review
Codex                    YES/NO            YES/NO
CodeRabbit               YES/NO            YES/NO

APP_TRIGGER_AUTHORITY:
  PASS    = both providers command_handled YES
  PARTIAL = exactly one provider command_handled YES
  FAIL    = neither
```

`PASS` means both providers demonstrably processed commands authored by the
Governor App installation identity. It does **not** mean production
enforcement readiness.

## Stop rules

- If an App-authored trigger is not accepted, that **is** the result:
  no silent switch to OAuth user tokens or PATs to "make it green".
  The follow-up (GitHub App user-authorization flow) is a different trust
  model and a different experiment.
- No webhook server, no `pull_request.synchronize` handling, no required
  checks, no ruleset/branch-protection changes, no expected-source
  enforcement, no multi-repo rollout, no retry scheduler.
- PR #12 and the probe PR are never merged; the probe PR is closed without
  merge after captures.
- No artificial payloads to "fix" prior CodeRabbit CLEAN inference.

## Negative controls

- Pre-trigger baseline inventory of the probe PR.
- Authorship parser control (`harness/authorship.py` + replay tests): an
  identical command text authored by an ordinary user (sourced read-only
  from the frozen PR #12 pilot artifacts) must **not** classify as an
  App-authored trigger. App authorship requires login **and** numeric actor
  id **and** `type == "Bot"` to match the recorded App identity — command
  text alone never qualifies.

## Evidence plan

Sanitized fixtures in `experiments/app-trigger-authority/fixtures/`:

```text
app_request_codex.json
app_request_coderabbit.json
codex_response_*.json
coderabbit_response_*.json
```

Sanitized: no installation tokens, JWTs, private keys, authorization
headers, or irrelevant profile data. Retained: numeric actor IDs, logins,
comment IDs, timestamps, PR number, head SHA, provider structured fields.

Final report: `docs/experiments/app-trigger-authority.md` with sections
Question / Frozen prerequisites / App identity and permissions / Probe PR /
Codex observation / CodeRabbit observation / Raw evidence mapping /
Negative controls / Result / What this DOES prove / What this DOES NOT
prove / Next experiment.

## After the verdict

Stop. `PASS` unlocks a separate Stage A2 (installation webhook → signed
delivery → `synchronize` → STALE epochs → Governor-owned non-required check
run → exact HEAD binding). `PARTIAL`/`FAIL` routes to an authority-design
experiment first. Neither is part of A1.

## Amendments

- **A1-c1 (2026-08-21, pre-trigger):** Method step 1 listed
  `pull_requests: read`. Empirically, creating issue comments **on pull
  requests** is governed by the Pull requests permission, so the App was
  upgraded to `pull_requests: read & write` before any trigger was posted.
  Evidence and causal before/after pair: `CONTRACT-CORRECTIONS.md`.
  Estimands unaffected (no trigger had been attempted successfully under
  the old permission set).

- **A1-c2 (2026-08-21, mid-observation — after the Codex observation, before
  any CodeRabbit classification and before the final verdict; interpretation
  model refined on owner direction):**

  1. **Codex is scored on three axes, not one.** `command recognized` /
     `review authorized` / `terminal review`. Observed: `YES / NO / NO` —
     five seconds after the App-authored command, provider actor `199175422`
     returned the no-start response "create a Codex account and connect to
     github". Recorded as two separate results, more important than any
     aggregate:

     ```text
     CODEX_APP_COMMAND_ROUTING:  PASS
     CODEX_APP_REVIEW_AUTHORITY: FAIL
     ```

     Calibrated inference only: *in this observed request, Codex resolved
     the requesting actor and refused to start; Codex review authorization
     appears requester-identity-bound.* No claim is made about Codex's
     universal internal contract.

  2. **Verdict ceiling.** The Governor's architectural goal is to *initiate*
     reviews, not to elicit polite refusals. Since App installation identity
     is already proven insufficient to authorize a Codex review,
     `APP_TRIGGER_AUTHORITY` cannot be `PASS` regardless of the CodeRabbit
     outcome; the maximum is `PARTIAL`.

  3. **CodeRabbit draft confounder + matched positive control.** Probe
     PR #13 is a draft, and CodeRabbit's pre-trigger auto-comment already
     states "Review skipped / Draft detected". App-authored silence is
     therefore confounded (two variables: actor = App, PR state = draft).
     If the App observation window (30 + 30 min) ends with zero CodeRabbit
     signals, run exactly one matched positive control **after** the App
     window: a human/OAuth-authored `@coderabbitai full review` on the same
     PR, same unchanged HEAD, same draft state. Interpretation:

     ```text
     App silent + human handled  => evidence for App-author rejection
     App silent + human silent   => INCONCLUSIVE (draft/provider state
                                    remains a confounder)
     App handled                 => App command authority PASS,
                                    independent of CLEAN/FINDINGS/RATE_LIMITED
     ```

     A human-control `RATE_LIMITED` still counts as provider handling
     evidence. HEAD must not change and the PR must not leave draft between
     the App probe and the control.

  4. **Provider-gate normalization.** The observed Codex no-start response
     normalizes to `COMMAND_HANDLED` + `REVIEW_UNAVAILABLE_FOR_REQUESTOR`,
     gate state `UNAVAILABLE` — never `CLEAN`. "Failed to start a review"
     must never read as a passing gate. Regression-tested against the real
     fixture (`harness/provider_gate.py`, `tests/`).

  5. **Permission-correction epistemic status.** The A1-c1 causal pair
     proves that `pull_requests: write` was required for the operation to
     succeed *in the tested configuration*. It does **not** prove that the
     Issues permission is unnecessary — `issues: write` was never removed.
     Non-necessity is `DOCUMENTED + consistent with experiment` (GitHub
     permission reference), not an empirical subtraction result. A
     minimization test may happen later; permissions are not touched again
     in A1.

  6. **Next experiment naming.** `A1b: Codex user-attributed trigger
     authority` — candidate mechanism: GitHub App user authorization /
     user access token. Design only; not implemented in A1. A2 remains
     unstarted.
