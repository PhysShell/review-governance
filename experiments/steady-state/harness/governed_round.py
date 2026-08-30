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

A6f-c3 removed the last places where a caller could hand in the *input* to
a proof instead of the proof. The driver had stopped accepting semantic
fields and started accepting raw comments, which is the same defect with
better manners: whoever chooses the bytes the parser sees chooses what the
parser concludes. Every GitHub read is now made here.

    durable observation of the PR, ruleset and carrier
    fresh authorization observation
    durable ACCEPT, gated by the store from that observation
    driver-owned baseline capture
    durable provider intent, bound to that capture
    ONE provider POST
    driver-owned terminal reads, per provider surface
    provider-specific normalization, identity preserved
    durable frozen snapshot
    association, causality and head binding
    provider predicate
    bundle built from durable snapshots and the standing acceptance
    request lineage re-read from the store
    candidate-bound health
    reducer
    pre-write carrier identity, then PATCH
    full independent readback
"""
import datetime

import auth_policy
import collector
import evidence
import gate as gate_mod
import health as health_mod
import observation as observation_mod
import parsers
import predicates
import publish
import revisions as rev_mod
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
    without a network. That injection is the transport, not the content: no
    caller supplies a comment, a reaction or a baseline, only the function
    that performs HTTP.
    """

    def __init__(self, *, repo, pr_number, read, post, auth_store,
                 round_store, snapshot_store, epoch_store, health_sources):
        self.repo, self.pr_number = repo, pr_number
        self.read, self.post = read, post
        self.auth, self.rounds = auth_store, round_store
        self.snapshots, self.epochs = snapshot_store, epoch_store
        self.health_sources = health_sources
        self.trace = []

    # -- step 1: what is true right now, read here and written down -------
    def observe(self, *, epoch_id, ruleset_id=None):
        """Read GitHub and record the reading durably.

        Every semantic callback is gone. `ruleset_verified_fn` and
        `carrier_fn` let a caller answer the two questions the gate cares
        most about and have the answers stored as if they had been read —
        an immutable lie, neatly indexed. The store performs the four
        readings itself now; what remains here is the scope.
        """
        try:
            row = self.rounds.record_observation(
                self.read, repo=self.repo, pr_number=self.pr_number,
                epoch_id=epoch_id, ruleset_id=ruleset_id)
        except rounds.RoundError as exc:
            return _stop("observe", str(exc))
        self.trace.append({"step": "observe",
                           "observation_id": row["observation_id"],
                           "head_sha": row["head_sha"],
                           "reads": [r["path"] for r in row["reads"]]})
        return row

    # -- step 2/3: authorization, then a gate the store runs --------------
    def accept_candidate(self, observation, *, epoch_id):
        if observation.get("state") == STOP:
            return observation
        permission = auth_policy.evaluate(self.auth)
        # Reported, never trusted: the store re-derives this from the same
        # row before it writes anything, with generations it derives itself.
        try:
            preview = gate_mod.evaluate_observation(
                observation, permission, epoch_id=epoch_id,
                open_generations=self.rounds.open_generations(
                    self.repo, self.pr_number))
        except gate_mod.GateError as exc:
            preview = [str(exc)]
        try:
            acceptance = self.rounds.record_acceptance(
                repo=self.repo, pr_number=self.pr_number, epoch_id=epoch_id,
                head_sha=observation["head_sha"], permission=permission,
                observation_id=observation["observation_id"])
        except rounds.RoundError as exc:
            return _stop("accept", str(exc), failures=preview,
                         authorization=permission.as_dict())
        self.trace.append({"step": "accept", "acceptance": acceptance})
        return {"acceptance": acceptance, "permission": permission}

    def observe_and_accept(self, *, epoch_id, ruleset_id=None):
        """The production entrypoint: read, then accept that reading.

        Offered so no caller has to hold an observation id at all. Choosing
        one is how a historical reading gets paired with a fresh
        permission, and the store refuses that — but an interface that
        makes it expressible invites somebody to try.
        """
        observation = self.observe(epoch_id=epoch_id, ruleset_id=ruleset_id)
        if observation.get("state") == STOP:
            return observation
        return self.accept_candidate(observation, epoch_id=epoch_id)

    # -- step 4: the baseline, read and frozen before asking --------------
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
        app = triggers.PROVIDER_IDENTITY[provider]["app_id"]
        payload = parsers.baseline(comments or [], provider_app=app)
        row = self.snapshots.capture_baseline(
            repo=self.repo, pr_number=self.pr_number, provider=provider,
            read_ok=True, payload=payload, captured_at=utcnow())
        self.trace.append({"step": "baseline", "provider": provider,
                           "baseline_id": row["baseline_id"],
                           "run_ids": payload["run_ids"]})
        return row

    # -- step 5/6: intent bound to that capture, then one request ---------
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
        try:
            intent = self.rounds.record_intent(
                acceptance_id=acceptance["acceptance_id"], repo=self.repo,
                pr_number=self.pr_number, provider=provider,
                generation=generation,
                requested_for_head=acceptance["head_sha"],
                permission=permission, baseline=baseline)
        except rounds.RoundError as exc:
            return _stop("request", str(exc))
        sent = triggers.send(self.post, self.rounds, request_row=intent,
                             permission=permission,
                             head_sha=acceptance["head_sha"])
        self.trace.append({"step": "request", "provider": provider,
                           "outcome": sent["state"],
                           "baseline_id": baseline["baseline_id"]})
        if sent["state"] != rounds.SENT:
            return _stop("request", sent.get("cause", "request not sent"),
                         outcome=sent["state"])
        return {"request": sent["request"], "permission": permission}

    # -- step 7: the terminal surface, read here ---------------------------
    def read_terminal_surface(self, request_row):
        """Every byte the parser will see, fetched by the driver.

        Previously `collect_evidence` took `raw_comments` and
        `raw_reactions`. Refusing derived fields while accepting the raw
        input to their derivation only moved the forgery: a plausible
        GitHub comment is as good as a fabricated verdict when the caller
        writes both.
        """
        status, comments = self.read(
            "GET", f"/repos/{self.repo}/issues/{self.pr_number}/comments?per_page=100")
        if status != 200:
            return _stop("collect", "cannot read the provider surface")
        reactions = []
        if request_row["provider"] == triggers.CODEX:
            # A clean Codex review can arrive as a reaction on our own
            # request comment, so the exact carrier id is read, not a list
            # of everything on the PR.
            carrier_id = request_row["request_carrier_id"]
            if carrier_id is None:
                return _stop("collect", "the request has no carrier id, so "
                                        "its reactions cannot be read")
            r_status, raw = self.read(
                "GET", f"/repos/{self.repo}/issues/comments/{carrier_id}/reactions")
            if r_status != 200:
                return _stop("collect",
                             "cannot read the reactions on our request "
                             "carrier; an unread surface is not an empty one")
            reactions = raw or []
        return {"comments": comments or [], "reactions": reactions}

    # -- step 8..12: normalize, freeze, admit, judge ----------------------
    def collect_evidence(self, sent, provider, generation):
        if sent.get("state") == STOP:
            return sent
        request_row = sent["request"]
        head = request_row["requested_for_head"]

        surface = self.read_terminal_surface(request_row)
        if surface.get("state") == STOP:
            return surface

        # The baseline comes from the request row, not from the caller: the
        # proof that a run id is new is only a proof against the reading
        # that preceded this request.
        baseline = self.snapshots.baseline(request_row["baseline_id"])
        if baseline is None:
            return _stop("collect", "the durable baseline this request cites "
                                    "is not in the store")
        if baseline["baseline_digest"] != request_row["baseline_digest"]:
            return _stop("collect", "the stored baseline does not match the "
                                    "digest the request was bound to")
        base = {**baseline["payload"], "baseline_id": baseline["baseline_id"],
                "read_ok": baseline["read_ok"],
                "captured_at": baseline["captured_at"]}

        if provider == triggers.CODERABBIT:
            observed = parsers.parse_coderabbit(
                surface["comments"], base=base, requested_head=head,
                generation=generation)
        else:
            observed = parsers.parse_codex(
                surface["comments"], surface["reactions"], base=base,
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
        # The revision that qualified this evidence, recorded so a later
        # re-read has something exact to compare against.
        frozen_revision = rev_mod.revision_of(observed, observed_at=utcnow())
        self.snapshots.record_revision(
            snapshot_id=snap["snapshot_id"], repo=self.repo,
            pr_number=self.pr_number, provider=provider, kind="FROZEN",
            revision=frozen_revision)
        record = {
            "provider": provider, "generation": generation,
            "requested_for_head": head, "state": "ANSWERED",
            "request_id": request_row["request_id"],
            "request_carrier_id": request_row["request_carrier_id"],
            # Read off the durable row, so the bundle's lineage claim can be
            # re-checked against the store rather than against itself.
            "acceptance_id": request_row["acceptance_id"],
            "baseline_id": request_row["baseline_id"],
            "terminal": verdict, "predicate": predicate,
            # The durable row's own digest, so the bundle cites what the
            # store actually holds rather than a second normalization.
            "snapshot_id": snap["snapshot_id"],
            "snapshot_digest": snap["snapshot_digest"],
            "frozen_revision": frozen_revision,
        }
        self.trace.append({"step": "collect", "provider": provider,
                           "snapshot_id": snap["snapshot_id"],
                           "head_binding": verdict["head_binding"],
                           "predicate": predicate["state"]})
        return record

    # -- step 13..17: reduce, guard, publish, read back -------------------
    def reconfirm_providers(self, records):
        """Does the surface still show what the frozen snapshots show?

        A6g watched a CodeRabbit run id appear and be withdrawn eighty
        seconds later by a rewrite of the same carrier, and watched Codex
        withdraw its acknowledgement reaction after commenting. A frozen
        snapshot stays a historical fact — at that moment the surface really
        did say this — but it stops being a standing verdict when the
        surface stops saying it, and only a re-read can tell the two apart.
        """
        results = []
        for rec in records:
            provider = rec["provider"]
            request_row = self.rounds.request(rec["request_id"])
            surface = self.read_terminal_surface(request_row)
            if surface.get("state") == STOP:
                results.append({"provider": provider,
                                "snapshot_id": rec["snapshot_id"],
                                "comparison": {"state": rev_mod.UNREADABLE,
                                               "cause": surface["cause"]}})
                continue
            baseline = self.snapshots.baseline(request_row["baseline_id"])
            base = {**baseline["payload"], "baseline_id": baseline["baseline_id"],
                    "read_ok": baseline["read_ok"],
                    "captured_at": baseline["captured_at"]}
            head = request_row["requested_for_head"]
            if provider == triggers.CODERABBIT:
                current = parsers.parse_coderabbit(
                    surface["comments"], base=base, requested_head=head,
                    generation=rec["generation"])
            else:
                current = parsers.parse_codex(
                    surface["comments"], surface["reactions"], base=base,
                    requested_head=head, generation=rec["generation"],
                    request_carrier_id=request_row["request_carrier_id"])
            current_rev = (rev_mod.revision_of(current, observed_at=utcnow())
                           if current else None)
            comparison = rev_mod.compare(rec["frozen_revision"], current_rev)
            self.snapshots.record_revision(
                snapshot_id=rec["snapshot_id"], repo=self.repo,
                pr_number=self.pr_number, provider=provider,
                kind=f"RECONFIRM_{comparison['state']}",
                revision=current_rev or {"observed_at": utcnow(),
                                         "carrier_id": None})
            results.append({"provider": provider,
                            "snapshot_id": rec["snapshot_id"],
                            "comparison": comparison,
                            "current_revision": current_rev})
        out = rev_mod.reconfirmation(results)
        self.trace.append({"step": "reconfirm", "all_standing": out["all_standing"],
                           "states": out["states"]})
        return out

    def reread_ruleset(self, *, ruleset_id=None):
        """The pre-success ruleset check, made by this driver.

        `ruleset_verified_fn` was the last callback on the path: the first
        ACCEPT and the final pre-success gate could both be answered by an
        external lambda, which made the strongest guard in the system a
        parameter.
        """
        rid = ruleset_id or observation_mod.PRODUCTION_RULESET_ID
        status, ruleset = self.read("GET", f"/repos/{self.repo}/rulesets/{rid}")
        if status != 200 or not ruleset:
            return {"verified": False,
                    "cause": f"cannot re-read ruleset {rid} ({status}); an "
                             "unread rule is not a verified one"}
        projection = observation_mod.project_ruleset(ruleset)
        facts = {"ruleset_id": rid,
                 "ruleset_enforcement": ruleset.get("enforcement"),
                 "ruleset_visible_hash": observation_mod.visible_hash(projection),
                 "ruleset_bypass": (ruleset["bypass_actors"]
                                    if "bypass_actors" in ruleset
                                    else observation_mod.BYPASS_UNOBSERVABLE)}
        problems = observation_mod.ruleset_findings(facts)
        return {"verified": not problems, "problems": problems, **facts}

    def conclude(self, records, *, epoch_id, existing_run, patch,
                 ruleset_id=None):
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
        ruleset = self.reread_ruleset(ruleset_id=ruleset_id)
        if not ruleset["verified"]:
            return _stop("conclude", "ruleset is no longer the reviewed policy",
                         ruleset=ruleset)

        permission = auth_policy.evaluate(self.auth)
        bundle = evidence.build_bundle(
            repo=self.repo, pr_number=self.pr_number, head_sha=head,
            lineage_records=records, acceptance_id=standing["acceptance_id"],
            # From the permission, not from a parameter: a caller that could
            # name the generation could make the bundle agree with itself.
            auth_generation=permission.auth_generation)
        lineage = evidence.verify_request_lineage(bundle, self.rounds, standing)
        replay = evidence.verify_against_snapshots(
            bundle, self.snapshots, predicates.evaluate)
        # The last thing checked before the verdict is whether the evidence
        # still exists on the surface it came from.
        reconfirmed = self.reconfirm_providers(records)
        reduction = evidence.reduce(bundle, current_head_sha=head,
                                    permission=permission,
                                    standing_acceptance=standing)
        if reduction["verdict"] == evidence.SUCCESS and not reconfirmed["all_standing"]:
            return _stop("conclude",
                         "frozen evidence is no longer standing on the "
                         "provider surface", reconfirmation=reconfirmed)
        if reduction["verdict"] == evidence.SUCCESS and not lineage["all_bound"]:
            return _stop("conclude",
                         "the cited requests do not belong to the standing "
                         "acceptance", lineage=lineage)
        if reduction["verdict"] == evidence.SUCCESS and not replay["all_reproduced"]:
            return _stop("conclude",
                         "the bundle does not replay from the durable "
                         "snapshots it cites", replay=replay)
        # Health is evaluated about *this* candidate, so "the reconciler is
        # alive" cannot stand in for "it compared this PR at this head".
        health = health_mod.evaluate(
            self.health_sources,
            candidate={"repo": self.repo, "pr_number": self.pr_number,
                       "head_sha": head})
        # The guard is consulted here rather than left to raise inside
        # `publish`. A refused success is not an error condition: it is a
        # verdict, and the carrier the gate reads must say so in red rather
        # than stay at whatever it happened to hold.
        checked = publish.guard(reduction=reduction, bundle=bundle,
                                current_head_sha=head, permission=permission,
                                health=health, existing_run=existing_run)
        conclusion = ("success" if reduction["verdict"] == evidence.SUCCESS
                      and checked["may_publish_success"] else "failure")
        result = publish.publish(
            patch, repo=self.repo, epoch_id=epoch_id, head_sha=head,
            conclusion=conclusion, bundle=bundle, reduction=reduction,
            current_head_sha=head, permission=permission, store=self.epochs,
            existing_run=existing_run, health=health)
        self.trace.append({"step": "conclude", "verdict": reduction["verdict"],
                           "projection": result["state"]})
        return {"bundle": bundle, "reduction": reduction, "health": health,
                "guard": checked, "ruleset": ruleset, "lineage": lineage,
                "reconfirmation": reconfirmed, "replay": replay,
                "publication": result,
                "standing_acceptance": standing["acceptance_id"]}
