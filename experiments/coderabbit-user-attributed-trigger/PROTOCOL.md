# A1b-R — CodeRabbit user-attributed trigger authority (preregistered)

Status: **PREREGISTERED** — committed before the probe PR carried any
command and before any observation of this experiment.
Program: `ai-final-review` · Control plane: `PhysShell/review-governance` ·
Branch: `experiment/coderabbit-user-attributed-trigger`.

## Question

A1b established a **third carrier** that A1 never tested against
CodeRabbit:

```text
1. installation      user = physshell-review-governor[bot]   via = physshell-review-governor
2. plain human       user = PhysShell                        via = null
3. App-mediated user user = PhysShell                        via = physshell-review-governor
```

A1 tested CodeRabbit on carrier 1 (rejected, silently) against carrier 2
(handled in 5 s). Carrier 3 is unexamined. Hypothesis worth killing or
confirming: CodeRabbit may filter on `user.type == "Bot"`, in which case an
App-mediated user (`type == "User"`) passes — and one GitHub App user
authorization would carry trigger authority for **both** providers.

Single question:

```text
Does CodeRabbit process @coderabbitai full review when the comment is
  user = PhysShell
  performed_via_github_app = physshell-review-governor
```

Terminal verdict grammar:

```text
CODERABBIT_USER_ATTRIBUTED_TRIGGER_AUTHORITY: PASS | FAIL | INCONCLUSIVE
```

Codex does not participate. No lifecycle work. A2 is not started.

## Frozen prerequisites

Re-verified before preregistration; any mismatch stops the experiment as
prerequisite drift.

| Item | Frozen value |
|---|---|
| A1 experiment PR | `review-governance` #1, draft, head `d4bf2918ae495ab1dbc651560a05be0a791aead7` |
| A1b experiment PR | `review-governance` #2, draft (head recorded at capture time) |
| Governor App | id `4669438`, slug `physshell-review-governor` |
| Governor bot actor | `physshell-review-governor[bot]` = `319376779` |
| GitHub user | `PhysShell` = `45852143`, type `User` |
| CodeRabbit actor | `coderabbitai[bot]` = `136622811` |
| Probe repository | `PhysShell/evm-from-scratch`, id `1335599563` |
| Prior probes | PRs #11, #13, #14 closed unmerged; #12 OPEN draft at `e29621f5…` — all untouched |

A1 and A1b verdicts are frozen evidence; A1b-R does not rewrite them.

## Authentication model

Primary request uses the **GitHub App user access token** established in
A1b (Device Flow, `ghu_`, 8 h expiry, refresh token unused). No `gh`
token, OAuth app token, PAT, installation token or Actions token is used
for the primary request. If the token has expired, it is re-obtained
through the same Device Flow — never substituted with another credential.
Secrets stay in `~/.config/review-governor/` at 0600.

## Method

1. New **disposable draft probe PR** in `PhysShell/evm-from-scratch`
   (branch `probe/coderabbit-user-attributed-trigger`), one harmless
   document, never merged, HEAD frozen for the duration. PRs #11/#12/#13/#14
   untouched; no repository configuration changed.
2. **Identity verification before the trigger**: `GET /user` must return
   `PhysShell` / `45852143` / `User`; a benign comment (no
   `@coderabbitai`) is posted with the user token and read back, and must
   show `performed_via_github_app = physshell-review-governor`. If that
   field is absent on this carrier, record
   `APP_MEDIATION_OBSERVABILITY: NOT_AVAILABLE_ON_OBSERVED_CARRIER` rather
   than inventing a substitute.
3. **Settle baseline**: full inventory (issue comments, reviews, inline
   comments, reactions, HEAD) plus a short settle window, so provider
   auto-activity on PR creation is separated from the experiment response.
   If the benign comment itself provokes CodeRabbit activity, stop and
   investigate contamination.
4. **Primary trigger**: `@coderabbitai full review` posted **only** with
   the user access token; comment read back immediately and the envelope
   frozen (repository_id, pr_number, head_sha, comment id, created_at,
   user.*, performed_via_github_app, auth_model).
5. **Observation**: 30 min primary window, one extension to 60 min total if
   nothing is observed, then `NO_OBSERVED_START`.

## Classification (fixed before data)

`PASS` requires only demonstrated **provider handling** of the
App-mediated-user command — any of:

```text
acknowledgement ("Full review triggered" or equivalent)
RATE_LIMITED or other explicit refusal attributable to this command
a review object
```

`CLEAN` is not required and is not sought: this measures authority, not
review quality. No attempt is made to re-establish a CodeRabbit `CLEAN`
contract; sticky/summary comments remain disqualified as authoritative
carriers.

If the App-mediated command draws nothing within the preregistered window,
run one **matched control** on the same PR, same HEAD, same draft state,
authored by the ordinary human/OAuth path (`gh`, `performed_via_github_app
= null`):

```text
mediated silent + plain human handled  -> FAIL
both silent                            -> INCONCLUSIVE
mediated handled                       -> PASS  (control not needed)
```

The control never becomes the primary result. HEAD must not change and the
PR must not leave draft between primary and control.

## Evidence plan

Sanitized fixtures in
`experiments/coderabbit-user-attributed-trigger/fixtures/`: identity
readback, benign App-mediated comment, primary request envelope, provider
response(s), settle baseline, final inventory, matched control pair (only
if required), and read-only reference inputs from frozen A1/A1b evidence.
No token, refresh token, device code or private key in any fixture.

Replay tests must prove, at minimum: `plain_user` and `app_mediated_user`
are **different carriers** (identical body and identical user identity,
distinguished only by `performed_via_github_app`); the installation-bot
carrier is neither; spoofed identity or foreign app slug fails closed; an
observed acknowledgement is scored as handling but never as `CLEAN`.

Report: `docs/experiments/coderabbit-user-attributed-trigger.md` with
Question / Frozen prerequisites / Authentication model / Probe PR /
Attribution observation / CodeRabbit observation / Raw evidence mapping /
Negative controls / Result / What this DOES prove / What this DOES NOT
prove / Architectural consequence / Next gate.

## Architectural outcomes (decided in advance)

- **PASS** → one GitHub App user authorization carries trigger authority
  for both providers; the split-authority architecture stays clean
  (installation identity for webhooks/state/check runs, user access token
  for provider triggers). Still not production-ready — lifecycle unproven.
- **FAIL** → the architecture is asymmetrical: Codex accepts the
  App-mediated user while CodeRabbit requires plain-human attribution or
  another mechanism. A1c must then be designed knowing exactly which
  user dependency is being carried.
- **INCONCLUSIVE** → no architectural conclusion; design a stronger
  discrimination experiment first.

## Forbidden in A1b-R

Codex participation; token lifecycle/refresh work; webhook server; Check
Run creation; ruleset or branch-protection changes; required checks;
scheduler; dedicated GitHub user; PAT; rewriting A1 or A1b; merging PR #1,
PR #2 or the probe PR; starting A2.

## Git discipline

Real chronology as separate commits: preregistration → harness → live
attribution evidence → provider capture → corrections (if any) →
fixtures/tests → report. No squashing of evidence chronology. Probe PR
closed without merge; A1b-R opens its own draft PR. Stop after the verdict.
