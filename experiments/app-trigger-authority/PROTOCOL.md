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
