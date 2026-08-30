# A6h — second disposable round: frozen preregistration

Written before the organism exists, and before any credential is spent.
Continues from `fc632bd`. Nothing here has been executed.

A6g ended INCONCLUSIVE and that outcome is immutable. This is not a retry
of it: it is a new experiment on a new disposable candidate, with the two
questions A6g left open decided **in advance** rather than discovered after
a provider answers.

## What A6g settled, and what it did not

```text
Codex clean-review shape          QUALIFIED LIVE      (A6g)
CodeRabbit sticky Run-ID shape    QUALIFIED OFFLINE   (A6f-c3)
CodeRabbit command-response       CONTENT PARSES      (A6g-c1)
                                  ASSOCIATION CLOSED  (A6g-c2, below)
provider surface is monotonic     FALSE               (A6g)
PR closure ends an acceptance     YES                 (A6g-c1)
runtime may move a git ref        NO                  (A6g cleanup, 403)
```

## The CodeRabbit association question, closed

`radioGroupId` carries the id of the comment that triggered the review the
carrier is currently showing. Derived from corpus, not documentation:

```text
#8  sticky updated 2026-08-29T11:10:50Z names 5461445560 (09:03:24Z)
    a later command at 11:57:33Z exists and did NOT rewrite the sticky,
    and is NOT named — so the handle tracks the trigger whose review
    produced the content, not merely the most recent command
#32 sticky names 5469066573, our request; the Codex request posted
    eleven seconds later is not named
```

It is admissible as `PROVIDER_NAMED_OUR_REQUEST` **only** under four
constraints. C1 is enforced one level up, in `collector.admissibility`,
which checks provider identity for every carrier before association is
considered at all; C2–C4 are enforced in `collector.associate`:

```text
C1  the carrier's App id is 347564
C2  exactly one new triggering id is named, and it is our request
C3  that id is absent from the pre-request baseline's triggering ids
      — the provider *began* naming us, a differential, not a mention
C4  the carrier was rewritten after our recorded intent
```

and it is **not transitive**. A carrier without the handle is not
associated because a sibling carrier has it. That is proximity, which is
the inference this programme has buried twice.

`v2:<hash>` remains `NOT_DERIVED` and reaches no decision.

Consequence, stated plainly: under this rule A6g would **still** have been
INCONCLUSIVE. The carrier bearing the handle carried no reviewable content;
the carrier with content bore no handle. The rule is for the next round,
not a retroactive repair of the last one.

## The candidate

```text
repo            PhysShell/evm-from-scratch
branch          probe/a6h-<suffix>          created from current main
PR              new, opened against main, non-draft
HEAD_A          recorded before any provider sees it
epoch_A         opened by the deployed runtime
run_A           created by the deployed runtime, failure / NOT_ESTABLISHED
```

`#8` is forbidden. `#12` is untouched. No existing PR is reused: CodeRabbit
states it does not re-review already-reviewed commits, so a used candidate
cannot produce an independent second observation.

The diff must be small, isolated, reviewable, and **chosen and recorded
before any provider sees it**. It is never edited to obtain a clean review.

Both the HEAD_A specimen and the HEAD_B mutation are frozen as bytes in
`a6h-freeze/`, with digests, before the branch exists — see
`a6h-freeze/FREEZE.md`. `base_sha` is
`047ff1a641e33e0bb8c6b9eea26bb80eea021e08`; if `main` has moved when A6h
begins, the freeze is void and a new one is written rather than rebased.

## Preregistered outcome mapping

Frozen here so no result can be reclassified after it arrives.

```text
both providers ADMISSIBLE and ADVISORY_POSITIVE
  + reconfirmation all STANDING at conclude
  + SUCCESS confirmed by independent readback
  + HEAD_B invalidation confirmed
  + cleanup confirmed
                                        -> A6h PASS

any provider ADMISSIBLE and reporting a finding
                                        -> A6h VALID_NEGATIVE
                                           no success, no retry

any provider UNASSOCIATED / unknown shape / ambiguous
                                        -> A6h INCONCLUSIVE
                                           no success, no retry

frozen evidence RETRACTED or SUPERSEDED at conclude
                                        -> A6h EVIDENCE_WITHDRAWN
                                           no success, no retry

success identity or readback mismatch   -> A6h FAIL, stop immediately
HEAD_B not fail-closed inside the SLO   -> A6h FAIL
```

`READY_FOR_REAL_#8` is set by A6h PASS and by nothing else.

## Sequence, and its authorization boundaries

Each numbered step needs its own GO. None is implied by the previous one.

```text
0a freeze the exact HEAD_A specimen and the exact HEAD_B mutation
   in review-governance, push, and read the commit back from the
   remote                                                      (no target write)

0b create the disposable branch from the frozen base and build
   HEAD_A from the frozen bytes; verify the tree with
   `a6h-freeze/verify.py --specimen`                            (write)

0c open the PR                                                  (write)

0d wait for the deployed runtime to open epoch_A and create
   run_A by itself, within 52 s                                (no write)

1  authorization preflight: at most one credential refresh,
   then exactly one GET /user                                  (read)
   owner-side ruleset read: active, bypass []                  (read)

2  observe_and_accept(epoch_A)                                 (no GitHub write)

3  both provider requests, in one process, inside the sixty-second
   permission: CodeRabbit then Codex, one POST each            (2 writes)

4  read-only polling, 30 s interval, 10 min window             (read)

5  conclude: reconfirm both surfaces, then the full guard chain,
   then at most one PATCH of run_A                             (<=1 write)

6  preregistered HEAD_B mutation, conditional on a confirmed
   SUCCESS on HEAD_A                                           (write)

7  cleanup: close PR unmerged, wait for runtime terminalization,
   then delete the branch under the owner credential           (2 writes)
```

The HEAD_B content is registered at **step 0a**, before the branch or the
pull request exists. The earlier ordering registered it "at step 0", which
was not the same claim: provider exposure begins at `pull_request.opened`,
and A6g watched CodeRabbit comment on `#32` six seconds after it opened.
A mutation registered after the PR exists is registered after the candidate
has been seen, and "we chose it in advance" becomes an argument about
commit timestamps instead of a fact on the remote.

## Forbidden throughout

```text
merge · auto-merge · bypass
provider retries of any kind
editing the candidate to obtain a clean review
touching #8 or #12
ruleset or App changes
a second acceptance to continue a first one
refresh after ACCEPT
```

## Carried into A6h as known limitations

```text
RULESET_BYPASS_BY_RUNTIME_IDENTITY   NOT_PROVIDED
  the App's view omits bypass_actors; owner-side sentinel compensates

CODEX_ASSOCIATION_STRENGTH           WEAK
  admitted on NEW_CARRIER_ABSENT_FROM_BASELINE, because the Codex surface
  offers nothing stronger once the acknowledgement reaction is withdrawn.
  Named WEAK in every record it appears in. A stronger Codex handle would
  be a worthwhile finding; none has been derived.
```

## Stop rule

Report at the boundary named in each GO. No step begins because the
previous one succeeded.
