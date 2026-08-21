# A1 — App-authored provider trigger authority: final report

Program `ai-final-review` · Control plane `PhysShell/review-governance` ·
Experiment branch `experiment/app-trigger-authority` · Date: 2026-08-21 (all
times UTC). Preregistered protocol: `experiments/app-trigger-authority/PROTOCOL.md`
(amendments A1-c1, A1-c2 applied before the corresponding classifications).

## Question

Do Codex and CodeRabbit accept a review trigger when the issue comment is
created by a GitHub App installation identity (`physshell-review-governor[bot]`),
not a human/OAuth user? Tested independently:

```text
App -> @codex review
App -> @coderabbitai full review
```

## Frozen prerequisites

Verified before the experiment and untouched throughout:

- `PhysShell/evm-from-scratch` PR #12 — OPEN, draft, head
  `e29621f54a63b50db4afb77b608d6c3a4d533812` (exact match with the frozen
  baseline), branch `claude/ai-final-review-governor-pilot-vohn85`.
- PR #11 — CLOSED, `mergedAt: null`; read-only source for negative-control
  fixtures. PR #11 was **not** a draft (`isDraft: false`) — which is why the
  pilot's user-trigger successes could not rule out the draft confounder and
  a matched control was required (A1-c2).

## App identity and permissions

- App `physshell-review-governor`, id `4669438`, owner `PhysShell`
  (id 45852143), private, no webhook in A1.
- Installation `155393018`, `repository_selection: selected`, repositories:
  exactly `PhysShell/evm-from-scratch`.
- Bot actor: `physshell-review-governor[bot]`, numeric id `319376779`,
  type `Bot` — verified twice: API readback (`app-identity.json`) and live
  comment authorship (identity probe, comment `5366890619`).
- Permissions finally granted: `checks: write`, `issues: write`,
  `metadata: read`, `pull_requests: write`. The upgrade of `pull_requests`
  from `read` to `write` mid-experiment (before any successful trigger) is
  correction **A1-c1**: creating issue comments on PRs is governed by the
  Pull requests permission; with `pull_requests: read` the identical POST
  returned 403 "Resource not accessible by integration" while
  `issues: write` was granted. Epistemic status per claim in
  `CONTRACT-CORRECTIONS.md` (`pull_requests: write` required — EMPIRICAL;
  Issues insufficient — EMPIRICAL; Issues unnecessary — DOCUMENTED only,
  no subtraction test).
- Secrets: private key and credentials only in `~/.config/review-governor/`
  (0600); never in the repo, chat, or fixtures. Tokens minted in-process.

## Probe PR

`PhysShell/evm-from-scratch` PR **#13**, draft, branch
`probe/app-trigger-authority`, touching only
`governor/pilot/app-trigger-probe.md`. Head throughout the experiment:
`3b022724d737feeae0a89e0450e6ea11f949d2e3` (never changed; draft state never
changed). Closed without merge after captures. PR #11/#12 untouched.

## Codex observation

- `07:54:40Z` — App-authored `@codex review` (comment `5366971213`, author
  `physshell-review-governor[bot]`/`319376779`, `performed_via_github_app:
  physshell-review-governor`).
- `07:54:45Z` — **5 seconds later** — provider actor
  `chatgpt-codex-connector[bot]` (id `199175422`,
  `performed_via_github_app: chatgpt-codex-connector`) replied:

  > "To use Codex here, [create a Codex account and connect to github](…)."

- No reaction on the request comment, no review, no further artifacts
  (final reconciliation `08:59:55Z`).

Scored per A1-c2 (three axes): command recognized **YES**, review authorized
**NO**, terminal review **NO**. Normalization: `COMMAND_HANDLED` +
`REVIEW_UNAVAILABLE_FOR_REQUESTOR`, provider gate `UNAVAILABLE` — never
`CLEAN` (`harness/provider_gate.py`, regression-tested on the real fixture).

Calibrated statement: *in this observed App-authored request, Codex resolved
the requesting actor and returned the "create a Codex account and connect to
github" no-start response.* Admissible inference: *Codex review
authorization appears requester-identity-bound.* No claim is made about
Codex's universal internal contract.

Context, not proof: during the pilot the same repo produced a real Codex
review for a user-authored trigger (PR #11, review `4989404627`,
`03:16:53Z`, ~98 s end-to-end), and a different no-start text on PR #12
("create an environment for this repo"). Recorded as context only.

## CodeRabbit observation

- `07:44:28Z` (pre-trigger) — CodeRabbit auto-comment on PR creation
  contains "Review skipped / Draft detected" (fixture
  `coderabbit_auto_summary_draft_skip.json`) — establishing the draft
  confounder addressed by A1-c2.
- `07:55:27Z` — App-authored `@coderabbitai full review` (comment
  `5366982391`, author id `319376779`).
- **Zero signals in 61 minutes** across the two preregistered windows
  (primary polls `07:55:37Z`→`08:26:06Z`, 38 polls; extension
  `08:26:53Z`→`08:56:57Z`, 29 polls): no reaction on the request comment,
  no comment, no review, no inline comment, no rate-limit notice, and no
  edit of the existing sticky summary comment (`updated_at` still
  `07:44:28Z` at the final extension poll `08:56:57Z`).
  Per protocol: `NO_OBSERVED_START`.
- `08:57:57Z` — **matched positive control** (A1-c2): identical text,
  same PR, same head `3b02272…`, same draft state, human author
  (`PhysShell`, id `45852143`, type `User`, via gh OAuth — a control for
  attribution, not a trigger fallback).
- `08:58:02Z` — **5 seconds later** — `coderabbitai[bot]` (id `136622811`)
  acknowledged: "CodeRabbit review command invocation:
  `7e8c43b8-be68-4f6d-974c-0b6b80ed3447` / Action performed: Full review
  triggered." The acknowledgement is the handling evidence; review
  completion is not required by the estimand.
- `08:59:16Z` — **post-acknowledgement, context only.** The mutable sticky
  summary comment (`5366833615`) was edited — its first edit of the entire
  experiment: `updated_at` was still `07:44:28Z` at `08:56:57Z` (end of the
  App window) and still `07:44:28Z` at `08:58:07Z` (one minute after the
  human command), then changed ~79 s after that command. The rewritten body
  reports "No actionable comments were generated in the recent review" over
  the range `047ff1a6…` → `3b022724…`, and the earlier "Review skipped /
  Draft detected" text is gone — the surface overwrote its own prior state
  (preserved only in our fixtures). **No `pull_request_review` object was
  emitted** (reviews list empty, verified locally at `08:59:55Z` and live
  afterwards). This post-ack surface does not establish the CodeRabbit
  `CLEAN` contract: mutable sticky comments are not accepted positive
  evidence carriers — the disqualification predates this experiment
  (PR #12 pilot) and is unchanged by it.

Interpretation per the preregistered matrix: **App silent + human handled ⇒
evidence for App-author rejection.** The matched control also shows the
draft state does not block explicit commands, eliminating the confounder.
Scored: command recognized **NO**, review authorized — not reached,
terminal review **NO**.

## Raw evidence mapping

Complete comment inventory of probe PR #13 at final reconciliation
(`08:59:55Z`) — seven artifacts, nothing retroactive, reviews list empty:

| # | comment id | author (numeric id) | created | role |
|---|-----------|---------------------|---------|------|
| 1 | 5366833615 | `coderabbitai[bot]` (136622811) | 07:44:28 | mutable sticky auto-summary: "Review skipped / Draft detected" (pre-trigger baseline); **edited 08:59:16**, after the human control, to "No actionable comments were generated" — original text no longer retrievable on GitHub |
| 2 | 5366890619 | `physshell-review-governor[bot]` (319376779) | 07:48:46 | App identity probe (benign, no mentions; triggered nothing) |
| 3 | 5366971213 | `physshell-review-governor[bot]` (319376779) | 07:54:40 | App-authored `@codex review` |
| 4 | 5366972130 | `chatgpt-codex-connector[bot]` (199175422) | 07:54:45 | Codex no-start response (+5 s) |
| 5 | 5366982391 | `physshell-review-governor[bot]` (319376779) | 07:55:27 | App-authored `@coderabbitai full review` — never answered |
| 6 | 5367779912 | `PhysShell` (45852143, User) | 08:57:57 | matched human control, identical text |
| 7 | 5367780817 | `coderabbitai[bot]` (136622811) | 08:58:02 | control acknowledged, "Full review triggered" (+5 s) |

Sanitized fixtures (no tokens/JWTs/auth headers):
`experiments/app-trigger-authority/fixtures/` — App request envelopes
(identity probe, codex, coderabbit), Codex no-start response, CodeRabbit
non-response window summary, matched control envelope + acknowledgement,
and user-authored negative controls sourced read-only from frozen PR #11.
The mutable sticky comment is preserved in **both** of its observed states —
`coderabbit_auto_summary_draft_skip.json` (pre-trigger, the only surviving
copy of the "Review skipped / Draft detected" text) and
`coderabbit_sticky_after_human_control.json` (post-edit `08:59:16Z`) —
precisely because that surface is not append-only. A1-c1 evidence:
`CONTRACT-CORRECTIONS.md`. Raw observation snapshots exist locally
(`.captures/`, gitignored); canonical artifacts live on GitHub under the
comment ids above.

## Negative controls

- Pre-trigger settle window (5 polls): only the CodeRabbit auto-summary;
  the App's benign no-mention comment triggered nothing.
- Authorship replay tests (15 passing): App authorship requires login AND
  numeric id AND `type == "Bot"` to match the recorded identity — identical
  command text authored by `PhysShell` (real fixtures from PR #11 and the
  live control) never classifies as App-authored; spoof guards (wrong id /
  wrong login / wrong type) all fail closed; provider responses classify as
  neither triggers nor App-authored; the observed no-start body from a
  non-Codex actor normalizes to `UNRECOGNIZED`, and no input normalizes to
  `CLEAN`.

## Result

```text
                 command recognized   review authorized   terminal review
Codex                  YES                  NO                  NO
CodeRabbit             NO                   not reached         NO

CODEX_APP_COMMAND_ROUTING:       PASS
CODEX_APP_REVIEW_AUTHORITY:      FAIL
CODERABBIT_APP_COMMAND_ROUTING:  FAIL  (matched control isolates the author)

APP_TRIGGER_AUTHORITY: PARTIAL
```

The aggregate is `PARTIAL` under both the original grammar (exactly one
provider demonstrably processed the App-authored command) and the A1-c2
ceiling (App installation identity is already proven insufficient to
*authorize* a Codex review, so `PASS` was unreachable regardless of
CodeRabbit).

## What this DOES prove

- The Governor App can act as a first-class GitHub actor: authenticated
  transport (`pem → RS256 JWT → installation token`), verified comment
  authorship as `physshell-review-governor[bot]`/`319376779`.
- Codex's connector **routes** commands from a GitHub App bot author: it
  parsed and answered the App-authored `@codex review` in 5 seconds — but
  refused to start a review for this requester, with an account-linkage
  onboarding response.
- CodeRabbit did **not** process the App-authored command in 61 minutes,
  while processing the identical human-authored command on the same draft
  PR at the same HEAD in 5 seconds. This is strong matched-pair evidence of
  author-based rejection (consistent with silent bot-author filtering).
- Draft PR state does not block CodeRabbit explicit review commands.
- Creating issue comments on PRs via an App installation token requires
  `pull_requests: write` in the tested configuration (`issues: write` alone
  gave 403).
- A "failed to start" provider response is representable and tested as
  `UNAVAILABLE` — it can never be conflated with `CLEAN`.
- CodeRabbit's mutable sticky surface reports outcomes its
  machine-authoritative carrier does not: at `08:59:16Z` the sticky claimed
  "No actionable comments were generated" for a specific commit range while
  the `pull_request_review` list stayed empty, and the same edit destroyed
  the surface's earlier content. This is a second, independent reason to
  keep sticky comments disqualified as `CLEAN` carriers: they are neither
  authoritative nor append-only.

## What this DOES NOT prove

- Not that Codex universally authenticates every trigger via the author's
  ChatGPT account — one observed request, one repo, one App identity.
- Not that CodeRabbit ignores *all* bots or *why* it ignored this one — the
  matched pair isolates the author variable, not the provider's internal
  rule; no refusal artifact exists.
- Not that `issues: write` is unnecessary for PR comments (documented-only;
  never subtracted).
- Not production enforcement readiness. `PRODUCTION_ENFORCEMENT` remains
  `NOT_READY_FOR_ENFORCEMENT`. No webhook, check-run, ruleset, or rollout
  work was done or validated.
- Not the CodeRabbit control's review *outcome* (CLEAN/FINDINGS) — the
  estimand was satisfied by acknowledgement; a later mutable summary
  reported no actionable comments, but no accepted terminal `CLEAN` carrier
  was observed.

## Architectural consequence

```text
Governor App identity
  -> Codex command parser   YES
  -> Codex review auth      NO   (requester-identity-bound, as observed)
  -> CodeRabbit command     NO   (author-based rejection, matched-pair)

=> A central Governor App remains viable as coordinator and future owner
   of gate/check/webhook machinery, but NOT as the direct trigger identity
   for these providers. Trigger authority needs a user-attributed model —
   a different trust model, hence a separate experiment.
```

## Next experiment

```text
A1b: Codex user-attributed trigger authority
     candidate mechanism: GitHub App user authorization / user access token
     (design only — not implemented in A1)
```

A2 (installation webhook → signed delivery → `synchronize` → STALE epochs →
Governor-owned non-required check run → exact HEAD binding) remains gated
and unstarted, per the stop rule. A1b runs first: CodeRabbit has already
answered clearly (App author is not accepted), while for Codex there is a
concrete candidate mechanism for crossing the boundary — which must now be
either proven or killed by experiment.

## Freeze status

```text
A1: COMPLETE
APP_TRIGGER_AUTHORITY: PARTIAL

Codex:
  routing            PASS
  App review auth    FAIL

CodeRabbit:
  App routing        FAIL

Governor App:
  coordinator        VIABLE
  direct trigger ID  NOT VIABLE

Production:
  NOT READY
```

Frozen 2026-08-21 after one documentation-only correction (the
post-acknowledgement sticky edit above). No conclusion changed. Evidence
artifacts — probe PR #13 comments, this report, the fixtures — are
read-only from here on; further work happens in new experiments.
