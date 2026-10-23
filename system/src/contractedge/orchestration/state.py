"""Engine-owned state and bounded policy counters; no model budget or fee ledger."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import os

from contractedge.contracts.artifacts import ArtifactRef, identity, index


class PolicyLedger:
    """Declare allowances once. Explicit scopes preserve the existing Full protocol.

    Front/context corrections have their historical scopes; execution correction
    is case-wide. New evidence cannot clear an already consumed counter.
    BudgetedProvider alone owns global calls, tokens, time and money admission.
    """
    def __init__(self, config):
        rounds = max(0, int(config.get("max_rounds", 2)) - 1)
        self.limits = {"format_correction": max(0, int(config.get("max_format_retries", 1))),
                       "truncation_recovery": (1 if config.get("planning_truncation_separate_allowance", False)
                                               else max(0, int(config.get("max_format_retries", 1)))),
                       "evidence_extension": 1,
                       "repair_round": rounds, "rediagnosis": rounds,
                       "plan_revision": rounds, "execution_retry": rounds}
        self._used = {}

    def limit(self, name):
        return self.limits[name]

    def used(self, name, scope):
        return self._used.get((name, scope), 0)

    def available(self, name, scope):
        return self.used(name, scope) < self.limit(name)

    def consume(self, name, scope):
        identity(scope)
        if not self.available(name, scope):
            raise ValueError("Existing policy allowance exhausted: " + name + "/" + scope)
        used = self.used(name, scope) + 1
        self._used[name, scope] = used
        callback = getattr(self, 'on_update', None)
        if callback is not None:
            callback(self.snapshot())
        return used

    def snapshot(self):
        return tuple({"policy": name, "scope": scope, "used": count, "limit": self.limits[name]}
                     for (name, scope), count in sorted(self._used.items()))


@dataclass(frozen=True)
class RepairState:
    current_snapshot: str | None
    incumbent: ArtifactRef | None = None
    trial: ArtifactRef | None = None
    selected: ArtifactRef | None = None
    obligation_refs: tuple[ArtifactRef, ...] = ()
    policy_usage: tuple[dict, ...] = ()
    artifact_refs: tuple[ArtifactRef, ...] = ()
    diagnosis_ref: ArtifactRef | None = None
    diagnosis_snapshot: str | None = None
    revision: int = 0
    status: str = "STARTING"
    schema_version: str = field(default="ce.repair_state.v1", init=False)

    def __post_init__(self):
        index(self.revision)
        if self.current_snapshot is not None:
            identity(self.current_snapshot)


def commit_transition(recorder, expected_revision, decision):
    """Single-writer CAS bridge. An intent is durable before its effect is run.

    This is a decision log, not arbitrary crash recovery or an exactly-once
    dispatch promise. Inflight/unknown dispatches retain their actual ledger.
    """
    index(expected_revision)
    if decision.next_state.revision != expected_revision + 1:
        raise ValueError("Transition next revision differs")
    for reference in decision.next_state.artifact_refs:
        recorder.verify_artifact(reference)
    if decision.result.payload_ref is not None:
        recorder.verify_artifact(decision.result.payload_ref)
    for reference in decision.result.call_refs:
        recorder.verify_call(reference)
    lock = recorder._parent("journal/transitions/writer.lock")
    descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        paths = sorted(lock.parent.glob("revision-*.json"))
        if len(paths) != expected_revision:
            raise ValueError("Transition revision conflict")
        value = {"schema_version": "ce.transition_commit.v1", "run_id": recorder.run_id,
                 "input_digest": recorder.input_digest, "expected_revision": expected_revision,
                 "committed_revision": expected_revision + 1, "effect_id": decision.effect_id,
                 "action": decision.action, "reason": decision.reason,
                 "result": asdict(decision.result), "next_state": asdict(decision.next_state),
                 "effect_status": "INTENT_COMMITTED; actual outcome is a later stage result"}
        if paths:
            previous = recorder._read(paths[-1].relative_to(recorder.root).as_posix())
            if previous["committed_revision"] != expected_revision:
                raise ValueError("Transition revision chain differs")
        recorder._write_once("journal/transitions/revision-" + str(expected_revision + 1).zfill(6) + ".json", value)
        recorder._write_pending("state.json", asdict(decision.next_state))
        return expected_revision + 1
    finally:
        os.close(descriptor)
        lock.unlink()
        recorder._sync_directory(lock.parent)


class RepairEngine:
    """Only production orchestration holds this commit capability."""
    def __init__(self, recorder, snapshot_id, config):
        self.recorder, self.policy = recorder, PolicyLedger(config)
        self.state = RepairState(snapshot_id)

    @classmethod
    def restore_committed(cls, recorder, snapshot_id, config, *, policy_usage=()):
        """Rehydrate the original state/counters before any recovery transition."""
        if recorder._path('journal/transitions/writer.lock').exists():
            raise ValueError('Unresolved transition writer lock; recovery cannot claim commit authority')
        engine=cls(recorder,snapshot_id,config)
        paths=sorted(recorder._path('journal/transitions').glob('revision-*.json'))
        previous=None
        for revision,path in enumerate(paths,1):
            commit=recorder._read(path.relative_to(recorder.root).as_posix())
            if (path.name!='revision-'+str(revision).zfill(6)+'.json'
                    or commit['run_id']!=recorder.run_id or commit['input_digest']!=recorder.input_digest
                    or commit['expected_revision']!=revision-1 or commit['committed_revision']!=revision):
                raise ValueError('Recovered transition chain differs')
            previous=commit['next_state']
        if previous:
            data=dict(previous);data.pop('schema_version',None)
            for key in ('incumbent','trial','selected','diagnosis_ref'):
                if data.get(key) is not None:data[key]=recorder.verify_artifact(data[key])
            for key in ('obligation_refs','artifact_refs'):
                data[key]=tuple(recorder.verify_artifact(ref) for ref in data[key])
            data['policy_usage']=tuple(data['policy_usage'])
            engine.state=RepairState(**data)
        usage={}
        for row in [*engine.state.policy_usage,*policy_usage]:
            if (row['policy'] not in engine.policy.limits or row['limit']!=engine.policy.limit(row['policy'])
                    or type(row['used']) is not int or not 0<=row['used']<=row['limit']):
                raise ValueError('Recovered policy allowance differs')
            key=(row['policy'],row['scope']);usage[key]=max(usage.get(key,0),row['used'])
        engine.policy._used=usage
        engine.state=replace(engine.state,policy_usage=engine.policy.snapshot())
        return engine

    def step(self, result, *, current_snapshot=None):
        from contractedge.orchestration.transitions import transition
        current = replace(self.state, policy_usage=self.policy.snapshot())
        if current_snapshot is not None:
            current = replace(current, current_snapshot=current_snapshot)
        decision = transition(current, result)
        self.recorder.commit_transition(current.revision, decision)
        self.state = decision.next_state
        return decision

    def project(self, **view):
        self.recorder._write_pending("state.json", {**asdict(self.state), **view})

    def start_round(self, result, *, repeated=False):
        if repeated:
            from contractedge.orchestration.transitions import transition
            decision = transition(self.state, result)
            policy = {"DIAGNOSE": "rediagnosis", "REDIAGNOSE": "rediagnosis",
                      "REVISE_PLAN": "plan_revision", "EXECUTE": "execution_retry"}[decision.action]
            self.policy.consume("repair_round", "case")
            self.policy.consume(policy, "case")
        return self.step(result)
