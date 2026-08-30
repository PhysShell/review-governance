# A5b — production cutover: report

Program `ai-final-review` · Control plane `PhysShell/review-governance` ·
Branch `experiment/a5b-cutover`, based on the frozen A5b-preflight evidence
at `5827e04ebf008503ae1fb63dc5781128d0f424e7`.
Preregistered protocol: `experiments/cutover/PROTOCOL.md`, frozen at
`5a0842e` before any mutation and amended twice.

PR #12 (A5a) and PR #15 (A5b-preflight) are frozen evidence and were not
modified.

## Result

```text
A5b_PRODUCTION_CUTOVER: PASS
PRODUCTION_ENFORCEMENT: ACTIVE
```

`main` in `PhysShell/evm-from-scratch` is now gated by a required check
from integration `4669438`. The Governor decides whether that check passes;
it does not merge, and has no merge path in any module.

## The chain, which is the actual result

The refusal at the end is not the finding. The finding is that no link in
this chain rests on an inference:

```text
reviewed policy
  -> explicit creation assertion      the POST body carries every field
  -> independent disabled readback    hashes, not the create response
  -> fresh closure                    seconds before the write, not hours
  -> single activation attempt        one PUT, never repeated
  -> independent active readback      hashes, not the PUT response
  -> fresh, checkless, non-BEHIND fixture
  -> real owner merge attempt
  -> GitHub refuses
  -> main unchanged
```

## Step 2 — inventory freeze

```text
inventory_hash  93dd8e5b91bc81a0ce769df70078808265e276290e1692f65fc55f815d8edceb
observed        2026-08-26T16:02:57Z .. 16:02:59Z
quiescent       true

#8   8aeafa9c28b9679c6fec660101f37e1f8bd994bd   non-draft
#12  e29621f54a63b50db4afb77b608d6c3a4d533812   draft
ai/final-review on both heads: []
```

`inventory.py` has no write path — the only HTTP method in the module is
`GET`. The stage that follows mutates production enforcement, so the
producer of its input had to be impossible to confuse with the thing that
acts on it.

"Atomic" is stated for what it is. GitHub offers no transaction over a PR
list, so this is a **detected-quiescence** freeze: it enumerates, re-
enumerates, and refuses to emit an artifact if anything moved while it was
looking. That proves nothing changed during the observation — weaker than
atomicity, and true.

## Step 3 — bootstrap

The first production write. `ai/final-review` came into existence failing.

```text
#8   check run 98235786270  failure  NOT_ESTABLISHED  app 4669438
#12  check run 98235792852  failure  NOT_ESTABLISHED  app 4669438
```

It acted on the frozen artifact, never on a fresh list; a test asserts the
module contains no `/pulls` call at all, because re-reading there would
have made the bootstrap its own baseline.

Capability in code rather than intention: `guarded()` raises for any
conclusion other than `failure`, any name other than the production
context, and any method other than POST of a new run. A bootstrap able to
emit a passing conclusion would open the gate on every PR it touched, at
the exact moment nobody is watching for that.

The POST response is not the confirmation. Only an exact match on
`{head_sha, name, app.id, conclusion, verdict-in-summary}` counts, and zero
or more than one matching carrier stops the stage without a retry — a
second POST would turn a lost response into either a duplicate carrier or a
second unknown.

## Step 3b — pre-activation closure

Introduced by amendment **r1**, and run three times, because its content is
temporal: an old `CLOSED` stays true about its moment and stops answering
"what is closed now".

```text
03:03:24Z .. 03:03:27Z   CLOSED   after the r2 deletion
09:33:54Z .. 09:33:56Z   CLOSED   16 s before the recreate
09:38:45Z .. 09:38:48Z   CLOSED   7 s before the flip
```

Every way reality can move is a named category — `new_pr`, `closed_pr`,
`base_changed`, `head_moved`, `draft_changed`,
`changed_during_observation` — because "something changed" is not an
operator-actionable sentence. Carrier problems are named separately:
`MISSING`, `DUPLICATE`, `MISMATCH`, `AMBIGUOUS`, `UNREADABLE`. An
unreadable read never becomes absence.

Nothing is repaired there and no code path could: the outputs are `CLOSED`
and `STOP`, and `STOP` says to record the delta, freeze a new inventory as
an amendment, and repeat.

## Step 4 — the ruleset

```text
ruleset        21640654
POST asserted  strict true · do_not_enforce_on_create false
               context ai/final-review · integration_id 4669438
               bypass_actors [] · refs/heads/main · enforcement disabled

disabled readback   POLICY_HASH 7e086ae8…  DISABLED 3b907b82…   VERIFIED
flip                one PUT at 09:38:55Z, response not consulted
active readback     POLICY_HASH 7e086ae8…  unchanged across the flip
                    ACTIVE_RULESET_HASH 3f1ddeca…              VERIFIED
state               CONFIRMED, retry_performed false
```

The verdict comes from reading. `active-and-verified` is CONFIRMED,
`still-disabled` is DID_NOT_ESTABLISH, anything else is OUTCOME_UNKNOWN —
and none of the three re-PUTs. Building an architecture on "write is not
fact" and then trusting an HTTP client at the flip that closes production
would have been almost artistic.

`3f1ddeca…` stopped being a computed expectation at that readback and
became evidence.

## Step 5 — the negative smoke test

```text
probe head       c6de158e23dc72a7b557d7ea4d107c3b0b7378de
main_sha_before  047ff1a641e33e0bb8c6b9eea26bb80eea021e08
merge-base       047ff1a641e3…  == main_sha_before
                 -> the probe cannot be BEHIND; drift excluded by
                    construction, not by inspection afterwards
ai/final-review on the probe head: []   of ANY conclusion
ruleset          active, both hashes matching

attempt          one PUT at the exact head, by the OWNER
result           HTTP 405
                 "Repository rule violations found
                  Required status check "ai/final-review" is expected."
main_sha_after   unchanged
probe head       unchanged
merged           false
```

The outcome alone would have proved nothing. Under `strict`, base drift and
a missing required check return the identical message, so the alternative
had to be excluded before the attempt by a predicate frozen in the
protocol. `mergeStateStatus` read `BLOCKED` and is recorded as
corroboration only; had the causality rested on it, this would have been
the r1 defect with better manners.

The probe was built from the exact current `main` because `#8` reads
`BEHIND`. From an older point it would have produced a beautiful 405
proving base drift — a fire alarm tested by setting the neighbouring
building alight.

The owner attempted and the gate refused **the owner**. That is the only
arrangement in which the refusal means anything: a Governor that could
merge would be gating itself.

## Two amendments, and where they were found

### r1 — found in review, before any mutation

The frozen protocol contained two incompatible sentences about a head
moving after the freeze, and `BOOTSTRAP_COMPLETE` had no referent. Separately,
the smoke test required only that a merge be blocked, in a document that
warns three sections earlier that GitHub cannot distinguish the two causes.

Fixed by naming the referent, adding step 3b, and freezing the fixture
validity predicate. Both defects were caught by reading the preregistration
rather than by a surprising result — which is what preregistration is for.

### r2 — found by execution, at the first readback

The canonical policy omitted
`required_status_checks.parameters.do_not_enforce_on_create`, which GitHub
materialises on every ruleset and which decides policy: at `true`, branch
creation is exempted from the rule. Step 4 stopped on the hash mismatch,
left the object disabled, and enforced nothing.

```text
CLASS        POLICY_SPEC_INCOMPLETE
DECISION     false becomes explicit reviewed policy
CONSEQUENCE  all three canonical hashes intentionally change

old (A5a, historical, not rewritten)  d6a4fa26… / b6ea30b6… / fd77f989…
new (A5b-r2)                          7e086ae8… / 3b907b82… / 3f1ddeca…
```

The first witness, ruleset `21599682`, was preserved until the amendment
was reviewed and its exact normalized readback committed, so the defect
stays demonstrable without a live artefact demonstrating it.

Then it was deleted and the object recreated rather than reused, even
though its stored semantics already matched. Its `false` was **GitHub's
default**; the new one is **our assertion**, sent explicitly in the POST
body, which is recorded as evidence so the distinction is checkable rather
than merely claimed. Keeping the old object because the observed result was
convenient would have been a miniature of the post-hoc calibration this
programme exists to refuse.

## An operational trap worth recording

```text
PR #31   merged           false
         merged_at        null
         merge_commit_sha c89eafd2060fdc704777911653923344a5785730
```

The field is populated on a PR that was never merged. The commit object
exists, but it is `ahead` of `main` by 2 and is **not in main's history** —
GitHub stores a speculative test-merge there. `merged` and `merged_at` are
the load-bearing fields; `merge_commit_sha` is not one, despite reading
like the most authoritative name in the response.

Reading that field as evidence of a merge would have produced a
false-positive incident on the last step of the last stage, which would
have been thematically perfect and operationally humiliating.

## Acceptance matrix

```text
PROTOCOL_FROZEN_BEFORE_MUTATION          PASS
FRESH_INVENTORY_FREEZE                   PASS

FROZEN_INVENTORY_BOOTSTRAPPED            PASS
BOOTSTRAP_READBACK_CONFIRMED             PASS
PREACTIVATION_CURRENT_INVENTORY_CLOSED   PASS
NO_PROVIDER_ROUND_STARTED                PASS

RULESET_CREATED_DISABLED                 PASS
DISABLED_READBACK_HASHES_MATCH           PASS

ACTIVATED                                PASS
ACTIVE_READBACK_HASHES_MATCH             PASS
POLICY_HASH_UNCHANGED_ACROSS_FLIP        PASS

SMOKE_PROBE_BASE_FRESH                   PASS
SMOKE_PROBE_CHECK_ABSENT                 PASS
NEGATIVE_SMOKE_TEST_BLOCKED              PASS
PROBE_CLOSED_UNMERGED                    PASS
NOTHING_MERGED                           PASS

A5b_PRODUCTION_CUTOVER                   PASS
PRODUCTION_ENFORCEMENT                   ACTIVE
```

200 tests.

## What is now true, and what is not

Admissible:

> GitHub prevents merge into `main` whenever the latest-head Governor check,
> from integration 4669438, is not passing. The Governor fails closed for
> every invalidation it has observed, and a success does not outlive the
> runtime responsible for revoking it beyond 52 s.

Not admissible, and still not provable while providers emit mutable
advisory comments:

> A PR can never be merged after its provider evidence becomes invalid.

`G2_UNOBSERVED_INVALIDATION_ATOMICITY` remains `NOT_PROVIDED`, as recorded
since A4.

## State after the cutover

```text
ruleset 21640654   active · strict true · do_not_enforce_on_create false
                   context ai/final-review · integration_id 4669438
                   bypass_actors [] · refs/heads/main
main               047ff1a641e33e0bb8c6b9eea26bb80eea021e08
open PRs           #8 (BEHIND), #12 (BLOCKED)
successful ai/final-review runs anywhere: 0
```

Both open PRs are unmergeable until they get a real review round. That is
the intended state, not an incident.

## Boundary

The first governed review round is a **separate decision** and this stage
does not prepare it. A5b chooses no candidate, grants no
`ACCEPT-CANDIDATE`, starts no provider round and authorises no merge
through the gate.

The PR must be chosen from its actual state at that moment. After all of
this, reaching into a pocket for `pulls[0]` and calling it a roadmap would
be a poor way to finish.
