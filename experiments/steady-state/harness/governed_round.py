"""The governed round, as one driver rather than seven modules and a habit.

Every defect this stage closed lived between components, not inside them:
an ACCEPT gate that nothing forced the durable store to consult, a request
authorised by one observation and posted under another, evidence admitted
because a check was written against a field GitHub does not return. Each
module was locally right. The order in which they had to be called existed
only as an instruction to a human.

So the order lives here, and no function below may reconstruct a missing
prerequisite. Where a step cannot establish its input, it stops; it does
not derive it, default it, or infer it from silence.

    fresh PR / main / ruleset / carrier read
    fresh authorization observation
    complete ACCEPT preconditions
    durable ACCEPT
    re-read head, same observation and generation
    durable provider intent
    ONE provider POST
    raw GitHub provider surface
    provider-specific normalization
    durable frozen snapshot
    association and admissibility
    provider predicate
    bundle built from durable snapshots
    fresh head / auth / ruleset / health / invalidation reads
    reducer
    PATCH the existing run
    full independent readback
"""
import datetime

import accept
import auth_policy
import collector
import evidence
import gate as gate_mod
import health as health_mod
import parsers
import predicates
import publish
import rounds
import triggers

STOP = "STOP"


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stop(step, cause, **extra):
    return {"state": STOP, "stopped_at": step, "cause": cause,
            "at": utcnow(), **extra}


class GovernedRound:
    """One PR, one head, one generation.

    `read`, `post` and `patch` are injected so the driver is exercised
    without a network. Nothing here reaches GitHub on its own initiative.
    """

    def __init__(self, *, repo, pr_number, read, post, auth_store,
                 round_store, snapshot_store, epoch_store, health_sources):
        self.repo, self.pr_number = repo, pr_number
        self.read, self.post = read, post
        self.auth, self.rounds = auth_store, round_store
        self.snapshots, self.epochs = snapshot_store, epoch_store
        self.health_sources = health_sources
        self.trace = []

    # -- step 1: what is true right now -----------------------------------
    def observe(self, *, ruleset_verified_fn, carrier_fn):
        status, pull = self.read("GET", f"/repos/{self.repo}/pulls/{self.pr_number}")
        if status != 200:
            return _stop("observe", "cannot read the PR")
        head = pull["head"]["sha"]
        obs = {
            "head_sha": head, "draft": bool(pull["draft"]),
            "base_ref": pull["base"]["ref"], "state": pull["state"],
            "ruleset_verified": ruleset_verified_fn(),
            "carrier": carrier_fn(head),
            "observed_at": utcnow(),
        }
        self.trace.append({"step": "observe", **obs})
        return obs

    # -- step 2/3/4: authorization, preconditions, durable acceptance -----
    def accept_candidate(self, observation, *, epoch_id, ruleset_id,
                         open_generations=()):
        if observation.get("state") == STOP:
            return observation
        permission = auth_policy.evaluate(self.auth)
        evaluation = gate_mod.evaluate(
            repo=self.repo, pr_number=self.pr_number,
            head_sha=observation["head_sha"], draft=observation["draft"],
            base_ref=observation["base_ref"], ruleset_id=ruleset_id,
            ruleset_verified=observation["ruleset_verified"],
            carrier=observation["carrier"], permission=permission,
            open_generations=list(open_generations))
        if not evaluation.passed:
            return _stop("accept", "preconditions refused",
                         failures=evaluation.failures,
                         authorization=permission.as_dict())
        # The store re-checks the evaluation against what it is about to
        # write; it cannot be handed an empty list instead.
        acceptance = self.rounds.record_acceptance(
            repo=self.repo, pr_number=self.pr_number, epoch_id=epoch_id,
            head_sha=observation["head_sha"], permission=permission,
            preconditions=evaluation)
        self.trace.append({"step": "accept", "acceptance": acceptance})
        return {"acceptance": acceptance, "permission": permission}

    def capture_baseline(self, provider):
        """Read the provider's surface and freeze it durably, before asking.

        Owned here rather than accepted as an argument: a caller-supplied
        dict shaped like a baseline is not a reading, and an unread surface
        is indistinguishable from an empty one unless the read itself is
        recorded.
        """
        status, comments = self.read(
            "GET", f"/repos/{self.repo}/issues/{self.pr_number}/comments?per_page=100")
        if status != 200:
            return _stop("baseline", "cannot read the provider surface; an "
                                     "unread baseline is not an empty one")
        app = triggers.PROVIDER_APP_ID[provider]
        payload = parsers.baseline(comments or [], provider_app=app)
        row = self.snapshots.capture_baseline(
            repo=self.repo, pr_number=self.pr_number, provider=provider,
            read_ok=True, payload=payload, captured_at=utcnow())
        self.trace.append({"step": "baseline", "provider": provider,
                           "baseline_id": row["baseline_id"],
                           "run_ids": payload["run_ids"]})
        return row

    # -- step 5/6/7: intent, then exactly one request ---------------------
    def request_provider(self, accepted, provider, generation, *, baseline):
        if accepted.get("state") == STOP:
            return accepted
        if baseline.get("state") == STOP or not baseline.get("baseline_id"):
            return _stop("request", "no durable baseline for this provider")
        acceptance = accepted["acceptance"]
        # The head is re-read before each provider, not once per round.
        status, pull = self.read("GET", f"/repos/{self.repo}/pulls/{self.pr_number}")
        if status != 200 or pull["head"]["sha"] != acceptance["head_sha"]:
            return _stop("request", "head moved after acceptance",
                         accepted_for=acceptance["head_sha"],
                         current=(pull or {}).get("head", {}).get("sha"))
        permission = auth_policy.evaluate(self.auth)
        if permission.observation_id != acceptance["auth_observation_id"]:
            return _stop(
                "request",
                "the acceptance was authorised by another observation; a new "
                "reading is a new acceptance, not a continuation")
        intent = self.rounds.record_intent(
            acceptance_id=acceptance["acceptance_id"], repo=self.repo,
            pr_number=self.pr_number, provider=provider, generation=generation,
            requested_for_head=acceptance["head_sha"], permission=permission)
        sent = triggers.send(self.post, self.rounds, request_row=intent,
                             permission=permission,
                             head_sha=acceptance["head_sha"])
        self.trace.append({"step": "request", "provider": provider,
                           "outcome": sent["state"],
                           "baseline_id": baseline.get("baseline_id")})
        if sent["state"] != rounds.SENT:
            return _stop("request", sent.get("cause", "request not sent"),
                         outcome=sent["state"])
        return {"request": sent["request"], "permission": permission}

    # -- step 8/9/10/11/12: observe, normalize, freeze, admit, judge ------
    def collect_evidence(self, sent, provider, generation, *, baseline,
                         raw_comments, raw_reactions=()):
        if sent.get("state") == STOP:
            return sent
        request_row = sent["request"]
        head = request_row["requested_for_head"]
        base = {**baseline["payload"], "baseline_id": baseline["baseline_id"],
                "read_ok": baseline["read_ok"],
                "captured_at": baseline["captured_at"]}
        if provider == triggers.CODERABBIT:
            observed = parsers.parse_coderabbit(
                raw_comments, base=base, requested_head=head,
                generation=generation)
        else:
            observed = parsers.parse_codex(
                raw_comments, raw_reactions, base=base,
                requested_head=head, generation=generation,
                request_carrier_id=request_row["request_carrier_id"])
        if observed is None:
            return _stop("collect", f"no {provider} answer attributable to "
                                    "this request")
        if observed.get("ambiguous"):
            return _stop("collect", observed["cause"],
                         new_run_ids=observed["new_run_ids"])
        snap = self.snapshots.freeze(
            repo=self.repo, pr_number=self.pr_number, head_sha=head,
            provider=provider, generation=generation,
            request_id=request_row["request_id"], payload=observed,
            frozen_at=utcnow())
        verdict = collector.admissibility(observed, request_row,
                                          head_sha=head, generation=generation)
        if not verdict["admissible"]:
            return _stop("collect", "evidence is not admissible",
                         admissibility=verdict, snapshot_id=snap["snapshot_id"])
        predicate = predicates.evaluate(provider, snap["payload"])
        record = {
            "provider": provider, "generation": generation,
            "requested_for_head": head, "state": "ANSWERED",
            "request_id": request_row["request_id"],
            "request_carrier_id": request_row["request_carrier_id"],
            "terminal": verdict, "predicate": predicate,
            # The durable row's own digest, so the bundle cites what the
            # store actually holds rather than a second normalization.
            "snapshot_id": snap["snapshot_id"],
            "snapshot_digest": snap["snapshot_digest"],
            "baseline_id": baseline["baseline_id"],
        }
        self.trace.append({"step": "collect", "provider": provider,
                           "snapshot_id": snap["snapshot_id"],
                           "predicate": predicate["state"]})
        return record

    # -- step 13..17: reduce, guard, publish, read back -------------------
    def conclude(self, records, *, epoch_id, existing_run, auth_generation,
                 ruleset_verified_fn, patch):
        bad = [r for r in records if r.get("state") == STOP]
        if bad:
            return _stop("conclude", "a provider round did not complete",
                         failures=bad)
        status, pull = self.read("GET", f"/repos/{self.repo}/pulls/{self.pr_number}")
        if status != 200:
            return _stop("conclude", "cannot re-read the PR")
        head = pull["head"]["sha"]
        if any(r["requested_for_head"] != head for r in records):
            return _stop("conclude", "head moved after the evidence was frozen")
        # A matching SHA is not a standing acceptance. After A -> B -> A the
        # head agrees again while the acceptance for the first A was
        # invalidated, and reviving it would let evidence outlive the
        # transition that ended it.
        standing = self.rounds.current_acceptance(self.repo, self.pr_number, head)
        if standing is None:
            return _stop("conclude",
                         "no standing acceptance for this head; a head that "
                         "returned to a previous value does not revive the "
                         "acceptance that was invalidated")
        if not ruleset_verified_fn():
            return _stop("conclude", "ruleset is no longer verified active")

        permission = auth_policy.evaluate(self.auth)
        bundle = evidence.build_bundle(
            repo=self.repo, pr_number=self.pr_number, head_sha=head,
            lineage_records=records, auth_generation=auth_generation)
        reduction = evidence.reduce(bundle, current_head_sha=head,
                                    permission=permission,
                                    auth_generation=auth_generation)
        replay = evidence.verify_against_snapshots(
            bundle, self.snapshots, predicates.evaluate)
        if reduction["verdict"] == evidence.SUCCESS and not replay["all_reproduced"]:
            return _stop("conclude",
                         "the bundle does not replay from the durable "
                         "snapshots it cites", replay=replay)
        health = health_mod.evaluate(self.health_sources)
        conclusion = "success" if reduction["verdict"] == evidence.SUCCESS \
            else "failure"
        result = publish.publish(
            patch, repo=self.repo, epoch_id=epoch_id, head_sha=head,
            conclusion=conclusion, bundle=bundle, reduction=reduction,
            current_head_sha=head, permission=permission, store=self.epochs,
            existing_run=existing_run, health=health)
        self.trace.append({"step": "conclude", "verdict": reduction["verdict"],
                           "projection": result["state"]})
        return {"bundle": bundle, "reduction": reduction, "health": health,
                "replay": replay, "publication": result}
