"""Durable candidate effects, referencing the existing recorder and call ledger.

An intent is never evidence that its effect finished. Recovery performs no model
dispatch and incomplete trials retain their incumbent by default.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

from contractedge.contracts.validation import canonical_digest
from contractedge.orchestration.candidate_guard import checkpoint_candidate
from contractedge.source.snapshot import compute_snapshot


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_json(path, value, *, immutable=False):
    """Publish complete bytes atomically; no partially visible immutable record."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)+'\n').encode()
    descriptor, temporary = tempfile.mkstemp(prefix='.trial-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        if immutable:
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.is_symlink() or path.read_bytes() != data:
                    raise ValueError('Immutable trial record differs: '+str(path))
        else:
            os.replace(temporary, path)
        if os.name != 'nt':
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(descriptor)
            finally: os.close(descriptor)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


@contextmanager
def recovery_lock(recorder):
    """A kernel lock is released on process death; never steal a live writer."""
    path = recorder._parent('journal/trials/writer.lock')
    with path.open('a+b') as stream:
        try:
            if os.name == 'nt':
                import msvcrt
                stream.seek(0); stream.write(b'0'); stream.flush(); stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError('Candidate transaction already has a live writer') from exc
        try: yield
        finally:
            if os.name == 'nt':
                stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else: fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def run_lease(output):
    """Exclude concurrent fresh/resume processes for one output directory."""
    output=Path(output).absolute()
    output.parent.mkdir(parents=True,exist_ok=True)
    if any(path.is_symlink() for path in (output,*output.parents)):
        raise ValueError('Repair output and ancestors must not traverse symlinks')
    class Lease:
        def _parent(self, unused):
            return output.parent/('.'+output.name+'.repair.writer.lock')
    with recovery_lock(Lease()): yield


def method_identity():
    root = Path(__file__).resolve().parents[1]
    system=root.parents[1]
    roots=[root,system/'scripts',system/'vendor/topomen',system/'prompts']
    # Includes actual prompt/schema, observer helpers and vendored consumers.
    return canonical_digest({str(p.relative_to(system)):sha(p) for directory in roots if directory.exists()
        for p in sorted(directory.rglob('*')) if p.is_file()
        and p.suffix in {'.py','.json','.txt','.md','.jinja','.j2','.sh'}
        and not {'__pycache__','.git','.pytest_cache'}.intersection(p.parts)})


def recovery_identity(recorder, source, method_input, config, *, consumer_id=None):
    return {'schema_version':'ce.recovery_identity.v1', 'run_id':recorder.run_id,
        'input_digest':recorder.input_digest, 'method_input_digest':canonical_digest(method_input),
        'method_source_prompt_digest':method_identity(), 'config_digest':canonical_digest(dict(config)),
        'mode':config.get('method_mode', config.get('mode','Full')),
        'repair_consumer_id':consumer_id,
        'source_root':str(Path(source).resolve(strict=True)), 'output_root':str(recorder.root),
        'ledger_identity_sha256':sha(recorder._path('journal/run.identity.json')),
        'ledger_binding_digest':canonical_digest({key:value for key,value in config.items()
            if any(part in key.lower() for part in ('money','ledger','runtime','observer','session'))})}


def ledger_facts(recorder):
    """Read/verify the original dispatch facts; never reserve or settle a fee."""
    directory = recorder._path('journal/ledger')
    facts, calls = {}, {}
    if directory.exists():
        for path in sorted(directory.glob('segment-*.inflight.json')):
            index = int(path.name.split('-')[1].split('.')[0])
            state = recorder._pending(index)
            seals = recorder._seals(index)
            if seals: recorder._validate_extension(recorder._read(seals[-1].artifact_ref.relative_path),state['calls'])
            facts[str(path.relative_to(recorder.root))] = sha(path)
            for offset, call in enumerate(state['calls']):
                position=state['first_call_index']+offset
                if position in calls: raise ValueError('Overlapping original dispatch records')
                calls[position]=call
    ordered=[calls[key] for key in sorted(calls)]
    if sorted(calls) != list(range(len(calls))): raise ValueError('Noncontiguous original dispatch records')
    return {'files':facts, 'digest':canonical_digest(ordered), 'calls':ordered,
        'unknown_dispatch':any(not isinstance(call.get('response_content'),str) for call in ordered)}


class TrialSession:
    def __init__(self, recorder, trial_id):
        if not trial_id.startswith('round-') or not trial_id[6:].isdigit(): raise ValueError('Invalid trial identity')
        self.recorder, self.trial_id = recorder, trial_id
        self.directory = recorder._parent('journal/trials/'+trial_id+'/000000.json').parent

    def events(self):
        result=[]; previous=None
        for index,path in enumerate(sorted(self.directory.glob('[0-9]*.json'))):
            if path.name != str(index).zfill(6)+'.json' or path.is_symlink(): raise ValueError('Trial event chain differs')
            event=json.loads(path.read_bytes())
            if (event['sequence']!=index or event['previous_sha256']!=previous
                    or event['run_id']!=self.recorder.run_id or event['input_digest']!=self.recorder.input_digest
                    or event['trial_id']!=self.trial_id): raise ValueError('Trial event identity differs')
            previous=sha(path); result.append(event)
        return result

    def record(self, kind, **payload):
        with recovery_lock(self.recorder):
            events=self.events(); index=len(events)
            event={'schema_version':'ce.trial_event.v1','run_id':self.recorder.run_id,
                'input_digest':self.recorder.input_digest,'trial_id':self.trial_id,'sequence':index,
                'previous_sha256':sha(self.directory/(str(index-1).zfill(6)+'.json')) if events else None,
                'kind':kind, 'effect_id':canonical_digest([self.recorder.run_id,self.trial_id,index,kind]),
                **payload}
            atomic_json(self.directory/(str(index).zfill(6)+'.json'),event,immutable=True)
        return event

    @classmethod
    def begin(cls, recorder, trial_id, source, plan, identity, *, plan_path,
              incumbent_observation=None, incumbent_feedback=None, intent_effect_id=None, target_paths=None):
        from contractedge.execution.topomen import operation_path
        trial=cls(recorder,trial_id)
        if trial.events(): raise ValueError('Trial already began; use explicit recovery')
        snapshot=compute_snapshot(source)
        paths=list(target_paths) if target_paths is not None else [operation_path(op) for op in plan['operations']]
        checkpoint=checkpoint_candidate(source,paths,
            recorder._path('journal/trials/'+trial_id+'/checkpoint.json'))
        atomic_json(trial.directory/'checkpoint.json',checkpoint)
        trial.record('BEGIN_COMMITTED',identity=identity,source_root=str(Path(source).resolve()),
            parent_snapshot=snapshot.snapshot_id,parent_files=dict(snapshot.file_digests),
            plan=plan,plan_digest=canonical_digest(plan),plan_path=plan_path,
            checkpoint_sha256=sha(trial.directory/'checkpoint.json'),
            incumbent_observation=incumbent_observation,incumbent_feedback=incumbent_feedback,
            intent_effect_id=intent_effect_id,ledger=ledger_facts(recorder)['files'])
        return trial

    @property
    def begin_event(self): return self.events()[0]

    def checkpoint(self):
        path=self.directory/'checkpoint.json'
        if sha(path)!=self.begin_event['checkpoint_sha256']: raise ValueError('Trial checkpoint changed')
        return json.loads(path.read_bytes())

    def record_response(self, request, response):
        event=self.record('RESPONSE_DURABLE',request=asdict(request),response={'content':response.content,
            'metadata':dict(response.metadata)},plan_digest=self.begin_event['plan_digest'],
            source_snapshot=compute_snapshot(self.begin_event['source_root']).snapshot_id,
            ledger=ledger_facts(self.recorder)['files'])
        return event['effect_id']

    def record_authority(self, accepted):
        """Persist exact acceptance facts; recovery must independently re-accept."""
        item=accepted.execution_input;context=item.context
        return self.record('AUTHORITY_DURABLE',authority={
            'consumer_id':item.consumer_id,'intent_kind':item.intent_kind,
            'snapshot_id':item.snapshot_id,'input_digest':item.input_digest,
            'payload':item.payload,'target_paths':list(item.target_paths),
            'operation_coverage':item.operation_coverage,
            'context':{'source_root':str(context.source.resolve()),'policy':dict(context.policy),
                'config':dict(context.config),'requirement':context.requirement,
                'declared_artifacts':list(context.declared_artifacts),'observations':list(context.observations),
                'feedback':context.feedback,'previous_attempt':context.previous_attempt,
                'prior_execution':context.prior_execution,'rebind':context.rebind},
            'execution_digest':accepted.execution_digest,'acceptance':dict(accepted.acceptance),
            'acceptance_digest':accepted.acceptance_digest})

    def consume_response(self, response_id):
        events=self.events()
        response=next((event for event in events if event['kind']=='RESPONSE_DURABLE' and event['effect_id']==response_id),None)
        if response is None: raise ValueError('Missing durable response')
        if any(event['kind']=='RESPONSE_CONSUME_INTENT' and event['response_id']==response_id for event in events):
            raise ValueError('Durable response was already consumed or its edit outcome is unknown')
        if compute_snapshot(self.begin_event['source_root']).snapshot_id != response['source_snapshot']:
            raise ValueError('Durable response source snapshot differs')
        self.record('RESPONSE_CONSUME_INTENT',response_id=response_id,source_snapshot=response['source_snapshot'])

    def record_edit_intent(self, changed):
        snapshot=compute_snapshot(self.begin_event['source_root'])
        expected=dict(snapshot.file_digests)
        for relative,text in changed.items(): expected[relative]=hashlib.sha256(text.encode()).hexdigest()
        return self.record('EDIT_INTENT',before_files=dict(snapshot.file_digests),expected_files=expected,
            owned_paths=sorted(changed),parent_snapshot=snapshot.snapshot_id)

    def record_applied(self, execution):
        snapshot=compute_snapshot(self.begin_event['source_root'])
        return self.record('APPLIED',snapshot_id=snapshot.snapshot_id,file_digests=dict(snapshot.file_digests),
            execution=execution,ledger=ledger_facts(self.recorder)['files'])

    def record_observed(self, observation):
        snapshot=compute_snapshot(self.begin_event['source_root'])
        if observation.get('snapshot_id')!=snapshot.snapshot_id: raise ValueError('Trial observation source differs')
        return self.record('OBSERVED',snapshot_id=snapshot.snapshot_id,observation=observation,
            observation_digest=canonical_digest(observation),ledger=ledger_facts(self.recorder)['files'])

    def select(self, decision, feedback, patch):
        snapshot=compute_snapshot(self.begin_event['source_root'])
        if feedback and feedback.get('to_snapshot')!=snapshot.snapshot_id: raise ValueError('Selected feedback snapshot differs')
        if sha(patch['patch_path'])!=patch['patch_sha256']: raise ValueError('Selected patch differs')
        facts=ledger_facts(self.recorder)
        if facts['unknown_dispatch']: raise ValueError('Unknown dispatch cannot create a new candidate selection')
        intent=self.record('SELECTION_INTENT',selected_snapshot=snapshot.snapshot_id,
            file_digests=dict(snapshot.file_digests),preservation_comparison=decision,
            selected_feedback=feedback,patch_sha256=patch['patch_sha256'],exportable=True,
            public_semantic_status=(feedback or {}).get('semantic_outcome','UNKNOWN'),
            ledger=facts['files'],ledger_call_count=len(facts['calls']),ledger_call_digest=facts['digest'])
        self.recorder._write_pending('selected-candidate.json',{'snapshot_id':snapshot.snapshot_id,
            'selection':'TRIAL','trial_id':self.trial_id,'selection_effect_id':intent['effect_id']})
        self.record('SELECTION_COMPLETED',intent_effect_id=intent['effect_id'],snapshot_id=snapshot.snapshot_id)

    def rollback_intent(self):
        return self.record('ROLLBACK_INTENT',from_snapshot=compute_snapshot(self.begin_event['source_root']).snapshot_id,
            selected_snapshot=self.begin_event['parent_snapshot'])

    def record_rollback(self, *, error=None):
        snapshot=compute_snapshot(self.begin_event['source_root'])
        return self.record('ROLLBACK_FAILED' if error else 'ROLLBACK_COMPLETED',snapshot_id=snapshot.snapshot_id,
            exportable=error is None and snapshot.snapshot_id==self.begin_event['parent_snapshot'],
            error=str(error) if error else None)


def initialize_recovery(recorder, source, method_input, config, *, consumer_id=None):
    identity=recovery_identity(recorder,source,method_input,config,consumer_id=consumer_id)
    snapshot=compute_snapshot(source)
    atomic_json(recorder._path('journal/recovery.identity.json'),{'identity':identity,
        'started_unix':time.time(),'initial_snapshot':snapshot.snapshot_id,
        'initial_files':dict(snapshot.file_digests)},immutable=True)
    return identity
