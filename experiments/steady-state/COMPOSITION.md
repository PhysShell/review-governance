# A6e — production composition cut-in: frozen contract

Frozen before deployment. Continues from `c8cb451`.

## What A6a actually established, restated honestly

```text
A6a_COMPONENTS_IMPLEMENTED           PASS
A6a_FAIL_CLOSED_PATH_LIVE_QUALIFIED  PASS
PRODUCTION_STEADY_STATE_COMPOSITION  NOT_ESTABLISHED
PRODUCTION_RECONCILIATION_CUTOVER    FAIL
CAUSE                                STEADY_STATE_RUNTIME_NOT_WIRED
```

Verified before writing this: the deployed
`governor-reconcile.service` runs `reconcile.py --loop` against
`decisions.sqlite3`, and that module still contains

```python
if row["epoch_id"].startswith("a5a-"):
```

`scoped_reconcile` is imported by exactly one file, `qualify.py`, which is
a qualification harness invoked by hand. So the defect A6a reported as
closed is still the one running in production, and the 308 tests were
describing modules the Governor does not execute.

The claim "the production runtime is ready" was stronger than the evidence
allowed. This stage is the correction.

## The composition path, named

One loop owns all of it, with an ordinary service lifecycle. A script that
a human runs, or that a timer happens to invoke, is not a composition
root.

```text
observe current open PR state
  -> resolve repo + PR + full current HEAD
  -> resolve scoped durable epoch
  -> ensure exactly one failure carrier when required
  -> detect HEAD drift independently of webhook delivery
  -> reject stale epoch state
```

## Per non-draft PR, per pass

```text
scope RESOLVED, same HEAD, exactly one valid carrier
    -> adopt by reading, ZERO writes

scope RESOLVED, older HEAD
    -> record the head transition
    -> open an epoch for the current full HEAD
    -> ensure failure / NOT_ESTABLISHED

NO_EPOCH
    -> open a scoped epoch
    -> ensure failure / NOT_ESTABLISHED

UNRESOLVED
    -> STOP for that PR, fail closed, no speculative write
```

Draft PRs are observed and not written to. `#12` is untouched.

## Replacement, not rewriting

`reconcile.py` is A5a evidence and is left byte-identical. It stops being a
production entrypoint instead of being edited: a new `runtime.py` becomes
the service, and the systemd unit is switched to it.

Editing a historical artifact to fix a present defect would leave the old
report describing code that no longer exists.

## The old auth surface

`AuthStore.permits_triggers()` answers the question A6c retired: "is the
last stored state AUTHORIZED", with no bound on age. No critical interface
uses it any more, but leaving a public function with that name next to a
new composition root is an efficient way to wire it back by accident.

It becomes a trap that raises, and `auth_producer` reports the derived
permission — state, age, generation — instead of a boolean.

## Deployment proof obligations

```text
deployed source == the commit this qualification claims
service restarted after that commit
legacy entrypoint no longer running
```

A daemon started before a commit is an old daemon however green
`systemctl` looks.

## The negative control

After the cut-in, on `#8` at `2d8348703924c7470ba82f525cafc9afe720aee2`
with existing run `99099324325`:

```text
scope        RESOLVED
stored_head  == GitHub HEAD
carrier      ADOPTED
writes       0
drift        false
```

A second `ai/final-review` appearing on `#8` fails the stage. That is the
control distinguishing a lifecycle from a check-run generator on a timer.

## The automation test, which is the point

One disposable PR from the exact current `main`, small, isolated,
reviewable, chosen and recorded before any provider sees it, never
intended for merge. Its first head is `PROBE_HEAD_A`.

`carrier.ensure` is **not** invoked by hand. If the failure carrier appears
only after a manual run:

```text
STEADY_STATE_AUTOMATION_NOT_ESTABLISHED
A6e FAIL
```

Within the operational SLO the running service must produce, by itself:

```text
epoch    repo · pr_number · head_sha == PROBE_HEAD_A
carrier  app 4669438 · head PROBE_HEAD_A · failure · NOT_ESTABLISHED
count    exactly 1
```

## Forbidden

```text
ACCEPT-CANDIDATE          Codex          CoderRabbit
provider request comments ai/final-review success
merge · auto-merge · bypass
ruleset or App changes
touching #12
editing the disposable PR to obtain a clean review
```

## Acceptance

```text
PRODUCTION_COMPOSITION_ROOT          PASS / FAIL
DEPLOYED_SCOPED_RECONCILIATION       PASS / FAIL
LEGACY_RECONCILE_NOT_IN_PRODUCTION   PASS / FAIL
DEPLOYED_SOURCE_MATCHES_COMMIT       PASS / FAIL

#8 EXISTING_CARRIER_ADOPTED          PASS / FAIL
#8 DUPLICATE_WRITES                  0

DISPOSABLE_PR_CREATED                PASS / FAIL
DISPOSABLE_HEAD_A_SCOPED             PASS / FAIL
AUTOMATIC_FAILURE_CARRIER            PASS / FAIL
AUTOMATIC_FAILURE_WITHIN_SLO         PASS / FAIL
INDEPENDENT_RECONCILIATION           PASS / FAIL

#12 UNTOUCHED · main UNCHANGED · ruleset UNCHANGED
ACCEPT_CANDIDATE NOT_STARTED · PROVIDER_REQUESTS 0
PRODUCTION_SUCCESS NOT_PUBLISHED
```

## Stop rule

The disposable PR stays open with its red carrier; it is the fixture for
A6f. Report, stop.
