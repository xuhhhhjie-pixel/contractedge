"""Bounded public repair loop: O/E/V/C/P -> concrete patch -> S1 -> feedback.

This module has no evaluator-root or private-score input. Hidden scoring is a
separate command/process after final candidate selection.
"""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import asdict, is_dataclass, replace
import json
import os
import time
from pathlib import Path
import uuid
from typing import Mapping

from contractedge.changeset.adapter import adapt_model_changeset, bind_changeset_envelope
from contractedge.changeset.prompt import build_changeset_request
from contractedge.changeset.topomen import translate_for_topomen
from contractedge.changeset.validate import validate_semantic_changeset
from contractedge.contracts.execution_capabilities import resolve_execution_capabilities
from contractedge.execution.runtime_preflight import check_runtime, scoped_runtime_environment
from contractedge.contracts.models import RunContext
from contractedge.contracts.validation import canonical_digest, parse_method_input, validate_run_trace
from contractedge.dataset.benchmark import execution_contract, editable_policy, file_sha, method_input_from_task, public_task
from contractedge.diagnosis.validate import validate_diagnosis
from contractedge.execution.public import BenchmarkPublicRunner
from contractedge.execution.review import ModelPublicReviewer
from contractedge.execution.topomen import ModelPlanExecutor, PlanRejected, accept_plan
from contractedge.model.budget import BudgetedProvider, BudgetExceeded
from contractedge.model.admission import quote_request, context_fits, complete_quoted
from contractedge.orchestration.evidence_delivery import select_observations_delivery
from contractedge.orchestration.front_delivery import build_front_delivery
from contractedge.orchestration.recorder import RunRecorder, ProviderJournal, preserve_error
from contractedge.orchestration.semantic_delivery import SemanticDeliveryError, delivery_context, install_monitor
from contractedge.contracts.stages import StageResult, front_status, feedback_status, stop_status
from contractedge.orchestration.state import RepairEngine
from contractedge.execution.topomen import execution_review_mapping
from contractedge.orchestration.feedback import make_feedback, validate_feedback, review_plan_with_retained, feedback_prompt_view
from contractedge.orchestration.runner import run_front_half, required_planning_source_support, required_source_prerequisites
from contractedge.retrieval.candidates import CandidateSet
from contractedge.source.snapshot import compute_snapshot


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def run_front_recorded(method_input, workspace, provider, config, token_counter, *,
                       directory, execution_feedback=None, public_observations=(), evidence_delivery=None,
                       journal=None, attempt_id="front"):
    """Persist exact effective front inputs, without synthesizing legacy traces."""
    directory = Path(directory)
    write_json(directory / 'front.method_input.json', asdict(method_input) if is_dataclass(method_input) else dict(method_input))
    write_json(directory / 'front.config.json', dict(config))
    write_json(directory / 'front.public_observations.json', list(public_observations))
    write_json(directory / 'front.execution_feedback.json', execution_feedback)
    start = len(provider.calls)
    active_error = None
    try:
        return run_front_half(method_input, workspace, provider, config, token_counter,
                              execution_feedback=execution_feedback, public_observations=public_observations,
                              **({'journal': journal, 'attempt_id': attempt_id} if journal is not None else {}))
    except BaseException as exc:
        active_error = exc
        raise
    finally:
        active_error = active_error or (journal.primary_error if journal is not None else None)
        try:
            write_json(directory / 'front.replay.json', {'schema_version': 'ce.front_replay_inputs.v1',
                'model_call_range': [start, len(provider.calls)],
                'scope': 'front_semantic_failure', 'semantic_reviewer': 'disabled'})
            write_json(directory / 'front.delivery.json', build_front_delivery(
                directory, method_input, provider, start, evidence_delivery))
            if journal is not None:
                relative = lambda path: Path(path).relative_to(journal.recorder.root).as_posix()
                projections = (journal.capture(relative(directory / 'front.delivery.json'), "FRONT_DELIVERY"),)
                journal.record("front_delivery", attempt_id=attempt_id, snapshot_id=None,
                    status="RECORDED", call_start=len(provider.calls),
                    input_refs=tuple(journal.latest[key] for key in ('front',) if key in journal.latest),
                    output_refs=(journal.capture(relative(directory / 'front.replay.json'), "FRONT_REPLAY"),),
                    projection_refs=projections)
        except Exception as recording_error:
            if active_error is None:
                raise
            # A recording failure must not replace the original provider/error.
            active_error.delivery_recording_error = str(recording_error)


def select_observations(observations, query: str, token_counter, max_tokens: int = 4000):
    """Compatibility tuple projection; stage callers persist the full receipt."""
    return select_observations_delivery(observations, query, token_counter, max_tokens).records


def validate_ready_trace(trace: Mapping, method_input, source: Path, *, capabilities: Mapping | None = None):
    validate_run_trace(trace)
    snapshot = compute_snapshot(source)
    if (trace['case_id'] != method_input.case_id or trace['input_digest'] != method_input.input_digest
            or trace['snapshot_id'] != snapshot.snapshot_id):
        raise ValueError('Ready trace belongs to another case/input/source snapshot')
    if trace['status'] not in {'READY', 'READY_PARTIAL'} or not trace.get('candidate_set'):
        raise ValueError('Trace does not contain a validated executable plan')
    context = RunContext(**trace['run_context'])
    recorded = resolve_execution_capabilities(context.retrieval_config.get('public_execution_capabilities'))
    if capabilities is not None:
        current = resolve_execution_capabilities(capabilities)
        if any(recorded[key] != current[key] for key in ('consumer_id', 'supported_operations')):
            raise ValueError('Ready trace execution capabilities differ from current consumer')
    candidates = CandidateSet(**trace['candidate_set'])
    # Revalidate source excerpts in a resumed trace against actual current files.
    for candidate in candidates.candidates:
        if candidate.get('origin_kind') == 'DECLARED_ARTIFACT':
            continue
        if candidate['path'] not in snapshot.file_digests:
            raise ValueError('Resumed trace cites a path outside the current source inventory')
        path = (source / candidate['path']).resolve(strict=True)
        path.relative_to(source.resolve())
        text = path.read_text()
        fragment = candidate['text']
        if fragment not in text or canonical_text_digest(fragment) != candidate['content_digest']:
            raise ValueError('Resumed trace source excerpt does not match current source')
    # Runtime evidence lives in the trace, not a mutation of original MethodInput.
    public = tuple(e for e in trace['diagnosis']['evidence_refs'] if e['kind'] == 'PUBLIC_OBSERVATION')
    run_input = replace(method_input, public_evidence=public or method_input.public_evidence)
    expected_support = required_planning_source_support(run_input,candidates,snapshot.snapshot_id)
    expected_prerequisites = required_source_prerequisites(candidates)
    if (context.retrieval_config.get('necessary_source_support', []) != expected_support
            or context.retrieval_config.get('source_prerequisites', []) != expected_prerequisites):
        raise ValueError('Ready trace prerequisite context differs from actually supplied current source')
    if context.retrieval_config.get('production_path_review_enabled'):
        from contractedge.retrieval.frontier import production_frontier
        expected_frontier = production_frontier(candidates.candidates, method_input.requirement_text)
        if expected_frontier != context.retrieval_config.get('production_path_frontier'):
            raise ValueError('Resumed trace production frontier differs from actually supplied source')
    packet = validate_diagnosis(trace['diagnosis'], run_input, context, candidates)
    validation = validate_semantic_changeset(trace['changeset'], packet, snapshot,
        policy=(context.retrieval_config.get('public_execution_capabilities') or {}).get('editable_policy'),
        frontier=context.retrieval_config.get('production_path_frontier'),
        prerequisites=context.retrieval_config.get('source_prerequisites'),
        capabilities=context.retrieval_config.get('public_execution_capabilities'))
    if not validation.valid:
        raise ValueError('Ready trace plan failed consumer preflight: ' + str(validation.errors))
    return packet, context


def canonical_text_digest(text):
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()


def revise_plan(trace: Mapping, method_input, source: Path, feedback: Mapping,
                provider, token_counter, config: Mapping) -> dict:
    packet, context = validate_ready_trace(trace, method_input, source, capabilities=config.get('public_execution_capabilities'))
    request = build_changeset_request(packet, context, [{'code': 'PUBLIC_EXECUTION_PLAN_FEEDBACK',
                                                        'message': json.dumps(feedback_prompt_view(feedback), ensure_ascii=False)}])
    quote = quote_request(provider, request, token_counter)
    if not context_fits(quote, int(config['max_context_tokens']), config.get('model_context_tokens')):
        raise BudgetExceeded('Plan revision exceeds the full context budget')
    monitor = getattr(provider, '_semantic_delivery_monitor', None)
    semantic = delivery_context(method_input, consumer='FEEDBACK_CHANGESET_REVISION',
        snapshot_id=context.snapshot_id, diagnosis=trace.get('diagnosis'), changeset=trace.get('changeset'),
        feedback=feedback, necessary_support=context.retrieval_config.get('necessary_source_support', ()),
        prerequisites=context.retrieval_config.get('source_prerequisites', ()))
    with monitor.scope(semantic) if monitor is not None else nullcontext():
        response = complete_quoted(provider, request, quote)
    payload = json.loads(response.content)
    payload, binding = bind_changeset_envelope(payload, packet, context.run_id, context.snapshot_id, context.input_digest)
    payload = adapt_model_changeset(payload, packet, compute_snapshot(source))
    validation = validate_semantic_changeset(payload, packet, compute_snapshot(source),
        policy=(config.get('public_execution_capabilities') or {}).get('editable_policy'),
        frontier=context.retrieval_config.get('production_path_frontier'),
        prerequisites=context.retrieval_config.get('source_prerequisites'),
        capabilities=context.retrieval_config.get('public_execution_capabilities'))
    if not validation.valid:
        raise PlanRejected('Feedback plan revision failed: ' + str(validation.errors))
    call = {'stage': 'EXECUTION_PLAN_REVISION', 'prompt_version': request.prompt_version,
            'response_content': response.content, 'response_metadata': dict(response.metadata), 'runner_binding': binding,
            'prompt_tokens_reserved': quote.input_tokens,
            'output_tokens_reserved': request.max_output_tokens,
            'elapsed_seconds': provider.summary()['elapsed_seconds'] if isinstance(provider, BudgetedProvider) else 0}
    return {**trace, 'changeset': payload, 'status': payload['status'],
            'model_calls': list(trace['model_calls']) + [call]}


def rebind_execution_plan(plan: Mapping, source: Path, feedback: Mapping, *, prior_execution=None) -> dict:
    """Keep semantic intent for EXECUTE retries, bind only fresh concrete targets."""
    snapshot = compute_snapshot(source)
    rebound = json.loads(json.dumps(plan))
    old_digest = canonical_digest(plan)
    rebound['snapshot_id'] = snapshot.snapshot_id
    for op in rebound['operations']:
        target = op['target']
        if target['kind'] == 'EXISTING':
            if target['path'] not in snapshot.file_digests:
                raise PlanRejected('Execution retry lost an existing target; rediagnosis required')
            target['snapshot_id'] = snapshot.snapshot_id
            target['content_digest'] = snapshot.file_digests[target['path']]
        elif (source / target['proposed_path']).exists():
            # CREATE already materialized; revise its concrete implementation.
            op['operation'] = 'MODIFY'
            op['target'] = {'kind': 'EXISTING', 'path': target['proposed_path'],
                            'snapshot_id': snapshot.snapshot_id,
                            'content_digest': snapshot.file_digests[target['proposed_path']],
                            'unit_refs': [], 'locator': {}}
    rebound['execution_continuation'] = {'prior_plan_digest': old_digest,
        'feedback_digest': canonical_digest(feedback), 'route': 'EXECUTE',
        'interpretation': 'Semantic intent retained; old diagnosis evidence is historical, not rebound as fresh facts.'}
    proof=prior_execution or {}
    if (proof.get('status')=='APPLIED' and proof.get('to_snapshot')==snapshot.snapshot_id
            and proof.get('from_snapshot')==plan['snapshot_id']
            and proof.get('acceptance',{}).get('plan_digest')==old_digest):
        edited={r['change_id'] for r in proof.get('semantic_edit_trace',[])
                if any(e.get('old_text')!=e.get('new_text') for e in r.get('literal_edits',[]))}
        eligible={}
        for op in rebound['operations']:
            target=op['target'];cid=op['change_id'];path=target.get('path')
            if (cid in edited and cid in proof.get('change_ids',[]) and target['kind']=='EXISTING'
                    and proof.get('file_digests',{}).get(path)==snapshot.file_digests.get(path)):
                eligible[cid]=dict(path=path,snapshot_id=snapshot.snapshot_id,content_digest=snapshot.file_digests[path])
        rebound['execution_continuation'].update(prior_applied_operations=eligible,
            prior_execution_digest=canonical_digest(proof),
            coverage_boundary='Previously applied source transaction, not semantic completion. All postconditions and preservation remain required.')
    return rebound


def repair_case(public_root: str | Path, tools_root: str | Path, case_id: str,
                output: str | Path, provider, token_counter, config: Mapping, *,
                initial_trace: Mapping | None = None, public_runner=None,
                executor=None, reviewer=None) -> dict:
    isolated = (config.get('public_runtime') == 'isolated'
        or getattr(public_runner, 'requires_public_profile', False) is True)
    if isolated:
        if config.get('observe_broken', True) is not True:
            raise ValueError('Isolated public execution requires the actual Broken observation before model dispatch')
        if getattr(public_runner, 'runtime_kind', None) != 'isolated-public-process':
            raise ValueError('Isolated public execution requires its trusted isolated runner; host fallback is prohibited')
    # Frozen public runtime helpers inherit environment. Keep API/context changes
    # scoped to this serial task process, including method probes and cleanup.
    with scoped_runtime_environment():
        settings = config.get('runtime_preflight')
        result, started = None, None
        if settings is not None:
            if not isinstance(settings, Mapping) or set(settings) - {'cpus', 'memory_mib', 'context', 'verify_image', 'timeout_seconds'}:
                raise ValueError('runtime_preflight contains unsupported settings')
            started = time.monotonic()
            options = dict(settings)
            configured_timeout = options.get('timeout_seconds', 20)
            if type(configured_timeout) not in {int, float} or not 0 < configured_timeout <= 120:
                raise ValueError('runtime_preflight timeout_seconds must be within (0,120]')
            options['timeout_seconds'] = min(configured_timeout,
                                            float(config.get('max_elapsed_seconds', 2400)))
            result = check_runtime(**options)
        with scoped_runtime_environment((result or {}).get('environment_overrides')):
            return _repair_case_impl(public_root, tools_root, case_id, output, provider, token_counter, config,
                initial_trace=initial_trace, public_runner=public_runner, executor=executor, reviewer=reviewer,
                _runtime_preflight=result, _runtime_started=started)


def _repair_case_impl(public_root: str | Path, tools_root: str | Path, case_id: str,
                output: str | Path, provider, token_counter, config: Mapping, *,
                initial_trace: Mapping | None = None, public_runner=None,
                executor=None, reviewer=None, _runtime_preflight=None, _runtime_started=None) -> dict:
    root = Path(public_root).resolve(strict=True)
    from contractedge.dataset.admission import check_case_admission
    check_case_admission(root, case_id, config)
    out = Path(output).resolve()
    if out.is_relative_to(root) or root.is_relative_to(out):
        raise ValueError('Repair output must be independent of frozen public tasks')
    out.mkdir(parents=True, exist_ok=False)
    method_value, projection = method_input_from_task(root, case_id)
    backend = config.get('execution_backend', 'direct')
    configured = resolve_execution_capabilities(config.get('public_execution_capabilities'), backend=backend)
    projected = resolve_execution_capabilities(projection.get('public_execution_capabilities'), backend=backend)
    projected['supported_operations'] = [op for op in projected['supported_operations'] if op in configured['supported_operations']]
    config = {**dict(config), 'public_execution_capabilities': resolve_execution_capabilities(projected, backend=backend)}
    method_input = parse_method_input(method_value)
    _, task_root, task, manifest = public_task(root, case_id)
    policy = editable_policy(task, execution_contract(task_root, task, manifest))
    run_id = initial_trace['run_id'] if initial_trace else str(config.get('run_id') or uuid.uuid4())
    journal = ProviderJournal(RunRecorder(out, run_id=run_id, input_digest=method_input.input_digest),
                              prior_calls=initial_trace['model_calls'] if initial_trace else ())
    budget = BudgetedProvider(provider, token_counter, max_tokens=int(config.get('max_total_tokens', 160000)),
        max_seconds=float(config.get('max_elapsed_seconds', 2400)), max_calls=int(config.get('max_model_calls', 24)),
        on_update=lambda calls: journal.callback(calls, lambda actual: write_json(out / 'model_calls.json', actual)))
    journal.attach(budget)
    semantic_monitor = install_monitor(budget, journal)
    if _runtime_started is not None:
        budget.started = _runtime_started
    runner = public_runner or BenchmarkPublicRunner(root, tools_root,
        lock_root=config.get('lock_root', '/tmp/contractedge-system-native-locks'),
        timeout_seconds=float(config.get('public_timeout_seconds', 600)), query=method_input.requirement_text,
        method_probe_profiles=config.get('method_probe_profiles', []))
    requires_public_profile = (config.get('public_runtime') == 'isolated'
        or getattr(runner, 'requires_public_profile', False) is True)
    if executor is None:
        backend = config.get('execution_backend', 'direct')
        if backend == 'topomen_agent':
            from contractedge.execution.topomen_agent import TopoMenAgentExecutor
            executor = TopoMenAgentExecutor(budget, token_counter, config,
                on_trace=lambda trace: write_json(out / 'topomen-native-last-trace.json', trace))
        elif backend == 'direct':
            executor = ModelPlanExecutor(budget, token_counter, config)
        else:
            raise ValueError('Unknown execution backend: ' + str(backend))
    reviewer = reviewer or ModelPublicReviewer(budget, token_counter, config)
    workspace = out / 'workspace'
    trace, plan, feedback, current_observation = None, None, None, None
    last_execution_attempt = None
    last_applied_execution = None
    execution_format_corrections = 0
    rejected_trials = []
    active_checkpoint = None
    active_incumbent = None
    retry_restored_candidate = False
    source_integrity_verified = True
    rounds, errors = [], []
    primary_error = None
    status = 'STARTING'
    source = None
    initial_snapshot = method_input.initial_snapshot_id
    engine = journal.engine = RepairEngine(journal.recorder, initial_snapshot, config)
    max_rounds = int(config.get('max_rounds', 2))
    if not 1 <= max_rounds <= 16:
        raise ValueError('max_rounds must be between1and16')
    write_json(out / 'method_input.json', method_value)
    write_json(out / 'projection.json', projection)
    journal.record('begin', attempt_id='run', snapshot_id=initial_snapshot, status='STARTING',
        input_refs=(journal.capture('method_input.json', 'METHOD_INPUT', snapshot_id=initial_snapshot),),
        projection_refs=(journal.capture('projection.json', 'PUBLIC_PROJECTION', snapshot_id=initial_snapshot),))
    def record_stage(stage, *, snapshot_id=None, status='RECORDED', call_start=None,
                     inputs=(), outputs=(), projections=(), predecessor=(), attempt_id=None):
        def capture_file(path, kind, *, fallback_snapshot=None):
            # An earlier plan/trace remains historical when consumed after an
            # edit. Never relabel its embedded snapshot as the current stage.
            bound_snapshot = None
            if Path(path).suffix == '.json':
                value = json.loads((out / path).read_bytes())
                if isinstance(value, Mapping):
                    bound_snapshot = (value.get('snapshot_id') or value.get('to_snapshot')
                                      or value.get('final_snapshot_id'))
            return journal.capture(path, kind, snapshot_id=bound_snapshot or fallback_snapshot)
        return journal.record(stage, attempt_id=attempt_id or ('round-' + str(round_id)),
            snapshot_id=snapshot_id, status=status,
            call_start=journal.position() if call_start is None else call_start,
            input_refs=tuple(capture_file(path, 'STAGE_INPUT') for path in inputs)
                + tuple(journal.latest[key] for key in predecessor if key in journal.latest),
            output_refs=tuple(capture_file(path, 'STAGE_OUTPUT', fallback_snapshot=snapshot_id) for path in outputs),
            projection_refs=tuple(capture_file(path, 'DELIVERY_PROJECTION') for path in projections))
    def semantic_context_for(consumer, *, evidence=(), mandatory_refs=(), source_requirements=True):
        retrieval = ((trace or {}).get('run_context') or {}).get('retrieval_config') or {}
        return delivery_context(method_input, consumer=consumer,
            snapshot_id=compute_snapshot(source).snapshot_id if source is not None else initial_snapshot,
            diagnosis=(trace or {}).get('diagnosis'), changeset=(trace or {}).get('changeset'),
            plan=plan, feedback=feedback, necessary_support=retrieval.get('necessary_source_support', ()),
            prerequisites=(plan or {}).get('precondition_review', ()), evidence=evidence,
            mandatory_refs=mandatory_refs, source_requirements=source_requirements, source_root=source)
    def reserve_public_time():
        if budget.remaining_seconds <= 0:
            raise BudgetExceeded('Global wall-clock budget exhausted before public execution')
        if hasattr(runner, 'timeout_seconds'):
            runner.timeout_seconds = min(float(config.get('public_timeout_seconds', 600)), budget.remaining_seconds)
    def observe_recorded(destination, record_path, *, stage, attempt_id, predecessor):
        destination, record_path = Path(destination), Path(record_path)
        relative_record = record_path.relative_to(out).as_posix()
        try:
            observed = runner.observe(workspace, destination)
        except BaseException as exc:
            failed = record_path.with_name(record_path.stem + '.error.json')
            def record_failure():
                actual = compute_snapshot(source).snapshot_id
                write_json(failed, {'status': 'OBSERVATION_ERROR', 'snapshot_id': actual,
                    'observation_produced': False, 'error': type(exc).__name__ + ': ' + str(exc)})
                record_stage(stage, attempt_id=attempt_id, snapshot_id=actual, status='ERROR',
                    outputs=(failed.relative_to(out).as_posix(),), predecessor=predecessor)
                if stage == 'observe':
                    record_stage('review', attempt_id=attempt_id, snapshot_id=actual, status='NOT_RUN',
                        outputs=(failed.relative_to(out).as_posix(),), predecessor=('observe',))
            preserve_error(exc, record_failure)
            raise
        actual = compute_snapshot(source).snapshot_id
        identity_path = record_path.with_name(record_path.stem + '.identity.json')
        matched = observed.get('snapshot_id') == actual
        write_json(identity_path, {'snapshot_id': actual,
            'observed_snapshot_id': observed.get('snapshot_id'), 'matches_current_source': matched})
        outputs = [relative_record, identity_path.relative_to(out).as_posix()]
        if not matched:
            raw_path = record_path.with_name(record_path.stem + '.raw.json')
            write_json(raw_path, observed)
            outputs.append(raw_path.relative_to(out).as_posix())
            observed = {**observed, 'status': 'INVALID_OBSERVATION',
                'errors': [*observed.get('errors', []), 'Observation belongs to another current source snapshot']}
        write_json(record_path, observed)
        record_stage(stage, attempt_id=attempt_id, snapshot_id=actual, status=observed['status'],
            outputs=tuple(outputs), predecessor=predecessor)
        observation_status = ('ENVIRONMENT_ERROR' if observed['status'] in {'INFRA_ERROR', 'INVALID_OBSERVATION'} else
                              'BUDGET_EXHAUSTED' if observed['status'] == 'BUDGET_EXCEEDED' else 'COMPLETE')
        engine.step(StageResult('incumbent' if stage == 'observe_initial' else 'observation',
            observation_status, journal.latest[stage], snapshot_id=actual))
        return observed
    def restore_recorded(checkpoint, destination, *, attempt_id, predecessor):
        from contractedge.orchestration.candidate_guard import restore_candidate
        trial = compute_snapshot(source).snapshot_id
        destination = Path(destination)
        try:
            restore_candidate(source, checkpoint)
        except BaseException as exc:
            def record_failure():
                actual = compute_snapshot(source).snapshot_id
                write_json(destination, {'status': 'RESTORE_FAILED', 'from_snapshot': trial,
                    'to_snapshot': actual, 'expected_snapshot': checkpoint['snapshot_id'],
                    'error': type(exc).__name__ + ': ' + str(exc)})
                record_stage('rollback', attempt_id=attempt_id, snapshot_id=actual, status='RESTORE_FAILED',
                    outputs=(destination.relative_to(out).as_posix(),), predecessor=predecessor)
            preserve_error(exc, record_failure)
            raise
        actual = compute_snapshot(source).snapshot_id
        write_json(destination, {'status': 'RESTORED', 'from_snapshot': trial, 'to_snapshot': actual,
                                 'expected_snapshot': checkpoint['snapshot_id']})
        record_stage('rollback', attempt_id=attempt_id, snapshot_id=actual, status='RESTORED',
            outputs=(destination.relative_to(out).as_posix(),), predecessor=predecessor)
        engine.step(StageResult('rollback', 'COMPLETE', journal.latest['rollback'], snapshot_id=actual))
    try:
        if initial_trace:
            calls = initial_trace['model_calls']
            for call in calls:
                for field in ['prompt_tokens_reserved', 'output_tokens_reserved']:
                    if not isinstance(call.get(field), int) or call[field] < 0:
                        raise ValueError('Resuming requires explicit nonnegative prior token reservations')
                if not isinstance(call.get('elapsed_seconds'), (int, float)) or call['elapsed_seconds'] < 0:
                    raise ValueError('Resuming requires explicit nonnegative prior elapsed time')
            budget.reserved_tokens += sum(c['prompt_tokens_reserved'] + c['output_tokens_reserved'] for c in calls)
            budget.prior_calls = len(calls)
            budget.started -= max((float(c['elapsed_seconds']) for c in calls), default=0)
            if budget.reserved_tokens > budget.max_tokens or budget.prior_calls >= budget.max_calls:
                raise BudgetExceeded('Resumed trace already exhausted the declared model budget')
        if _runtime_preflight is not None:
            write_json(out / 'runtime-preflight.json', _runtime_preflight)
            if _runtime_preflight.get('status') != 'PASS' or _runtime_preflight.get('runtime_verified') is not True:
                raise RuntimeError('RUNTIME_PREFLIGHT_FAILED: ' + '; '.join(_runtime_preflight.get('errors', []))
                                   + '; inspect ' + str(out / 'runtime-preflight.json'))
        engine.project(status='PREPARING', case_id=case_id)
        reserve_public_time()
        record = runner.prepare(case_id, workspace, out / 'prepare')
        source = Path(record['source_root']).resolve(strict=True)
        if compute_snapshot(source).snapshot_id != initial_snapshot:
            raise ValueError('Public prepare source tree differs from frozen MethodInput S0')
        if config.get('method_probe_profiles'):
            from contractedge.execution.probe_specs import select_probes
            specs = select_probes(method_input.requirement_text, source, config['method_probe_profiles'])
            descriptions = [{k:v for k,v in spec.items() if k!='program'} for spec in specs]
            config['public_execution_capabilities'] = {**config['public_execution_capabilities'],
                'method_behavior_probes': descriptions}
            write_json(out / 'method-probe-specifications.json', descriptions)
        from contractedge.execution.observations import select_observation_adapters
        adapters = select_observation_adapters(public_root, case_id)
        if adapters:
            config['public_execution_capabilities'] = {**config['public_execution_capabilities'],
                'registered_public_observation_adapters': adapters}
            write_json(out / 'public-observation-adapters.json', adapters)
        if initial_trace:
            validate_ready_trace(initial_trace, method_input, source, capabilities=config.get('public_execution_capabilities'))
            trace = dict(initial_trace)
            # Resuming a real development trace does not replenish model budgets.
            write_json(out / 'initial-front.trace.json', trace)
            seal = journal.recorder.seal_ledger(segment_index=0)
            journal.recorder.record_stage('front_import', attempt_id='ready', snapshot_id=initial_snapshot,
                status='LEGACY_RECORDED', input_refs=(journal.latest['begin'],),
                output_refs=(journal.capture('initial-front.trace.json', 'IMPORTED_FRONT_TRACE', snapshot_id=initial_snapshot),),
                call_refs=seal.call_refs,
                issues=({'code': 'LEGACY_PARAMETERS_NOT_SYNTHESIZED',
                         'missing_request_indices': [i for i,c in enumerate(initial_trace['model_calls'])
                                                     if not isinstance(c.get('request'), Mapping)]},))
        if config.get('observe_broken', True):
            engine.project(status='OBSERVING_BROKEN', case_id=case_id)
            reserve_public_time()
            current_observation = observe_recorded(out / 'broken-public', out / 'initial-public-observation.json',
                stage='observe_initial', attempt_id='broken', predecessor=('begin',))
            if current_observation['status'] in {'INFRA_ERROR', 'INVALID_OBSERVATION', 'BUDGET_EXCEEDED'}:
                status = current_observation['status']
                raise ValueError('Initial public observation could not be produced')
        for round_id in range(max_rounds):
            if budget.remaining_seconds <= 0:
                raise BudgetExceeded('Global wall-clock budget exhausted')
            directory = out / 'rounds' / str(round_id)
            directory.mkdir(parents=True)
            engine.project(status='PLANNING', case_id=case_id, round_id=round_id)
            delivery = select_observations_delivery((current_observation or {}).get('observations', []),
                method_input.requirement_text, token_counter, int(config.get('max_public_evidence_tokens', 4000)),
                snapshot_id=compute_snapshot(source).snapshot_id)
            write_json(directory / 'public-evidence.delivery.json', delivery.receipt)
            if delivery.receipt['status'] != 'COMPLETE':
                status = 'EVIDENCE_DELIVERY_INCOMPLETE'
                engine.step(StageResult('delivery', 'DELIVERY_INCOMPLETE',
                    journal.capture(directory.relative_to(out).as_posix() + '/public-evidence.delivery.json',
                                    'PUBLIC_EVIDENCE_DELIVERY', snapshot_id=compute_snapshot(source).snapshot_id),
                    snapshot_id=compute_snapshot(source).snapshot_id))
                errors.append('Public evidence delivery ' + delivery.receipt['status'] + ': '
                    + json.dumps({'coverage': delivery.receipt['mandatory_coverage'],
                                  'errors': delivery.receipt['errors']}, ensure_ascii=False))
                break
            observations = delivery.records
            if trace is not None and engine.state.diagnosis_snapshot is None and trace.get('diagnosis'):
                imported = (journal.capture('initial-front.trace.json', 'IMPORTED_FRONT_TRACE',
                                           snapshot_id=trace['snapshot_id']) if initial_trace else journal.latest.get('front'))
                engine.step(StageResult('diagnosis', front_status(trace['diagnosis']['status']), imported,
                    snapshot_id=trace['snapshot_id']))
            next_route = ('EXECUTE' if retry_restored_candidate else feedback['route'] if feedback else
                          'EXECUTE' if trace is not None else 'DIAGNOSIS')
            round_decision = engine.start_round(StageResult('round_start', 'READY',
                journal.latest.get('feedback') or journal.latest.get('front_import'),
                snapshot_id=compute_snapshot(source).snapshot_id, route=next_route), repeated=round_id > 0)
            if round_decision.action == 'DIAGNOSE':
                round_config = {**dict(config), 'run_id': run_id,
                    'max_total_tokens': max(1, budget.max_tokens - budget.reserved_tokens),
                    'max_elapsed_seconds': budget.remaining_seconds,
                    'model': {'model': config['model'], 'provider': config.get('provider', 'configured'),
                              'schema_in_prompt': config.get('schema_in_prompt', True)}}
                trace = run_front_recorded(method_input, source if method_input.source_root == '.' else source.parent,
                    budget, round_config, token_counter, execution_feedback=feedback,
                    public_observations=observations, directory=directory, evidence_delivery=delivery.receipt,
                    journal=journal, attempt_id='round-' + str(round_id)).to_wire()
                plan = None
            if trace['status'] not in {'READY', 'READY_PARTIAL'}:
                unknown = bool(budget.calls and budget.calls[-1].get('error')
                    and 'response_content' not in budget.calls[-1] and trace['status'] != 'BUDGET_EXHAUSTED')
                preserve_error(journal.primary_error, lambda: engine.step(StageResult('front',
                    front_status(trace['status'], response_unknown=unknown), journal.latest.get('front'),
                    snapshot_id=trace['snapshot_id'],
                    call_refs=journal.refs(max(0, len(budget.calls)-1)) if unknown else ())))
                write_json(directory / 'front.trace.json', trace)
                status = trace['status']
                errors.extend(trace.get('errors', []))
                break
            if round_decision.action in {'REVISE_PLAN', 'REDIAGNOSE'}:
                if round_decision.action == 'REVISE_PLAN':
                    revision_call_start = journal.position()
                    trace = revise_plan(trace, method_input, source, feedback, budget, token_counter, config)
                    revision_value = journal.recorder.capture_value(trace['changeset'], 'FINAL_SEMANTIC_PLAN',
                                                                  snapshot_id=trace['snapshot_id'])
                    journal.record('planning', attempt_id='round-' + str(round_id) + '.revision',
                        snapshot_id=trace['snapshot_id'], status=trace['status'], call_start=revision_call_start,
                        input_refs=tuple(journal.latest[k] for k in ('planning','feedback') if k in journal.latest),
                        output_refs=(revision_value,))
                    plan = None
                else:
                    # Old source evidence cannot be relabelled as current solely to reuse a diagnosis.
                    feedback = {**feedback, 'route': 'DIAGNOSIS', 'failure_kind': 'DIAGNOSIS',
                                'rationale': 'Plan feedback requires rediagnosis because source evidence is stale.'}
                    trace = run_front_recorded(method_input, source if method_input.source_root == '.' else source.parent,
                        budget, {**dict(config), 'run_id': run_id, 'model': {'model': config['model'],
                            'schema_in_prompt': config.get('schema_in_prompt', True)}},
                        token_counter, execution_feedback=feedback, public_observations=observations,
                        directory=directory, evidence_delivery=delivery.receipt,
                        journal=journal, attempt_id='round-' + str(round_id) + '.rediagnosis').to_wire()
                    plan = None
            write_json(directory / 'front.trace.json', trace)
            if trace['status'] not in {'READY', 'READY_PARTIAL'}:
                status = trace['status']
                errors.extend(trace.get('errors', []))
                break
            if ((feedback and feedback['route'] == 'EXECUTE') or retry_restored_candidate) and plan:
                plan = rebind_execution_plan(plan, source, feedback or {}, prior_execution=last_applied_execution)
            else:
                validate_ready_trace(trace, method_input, source, capabilities=config.get('public_execution_capabilities'))
                plan = translate_for_topomen(trace['changeset'], 'contractedge.topomen_plan.v1',
                    diagnosis=trace.get('diagnosis'), public_capabilities=config.get('public_execution_capabilities'),
                    source_support=trace['run_context']['retrieval_config'].get('necessary_source_support'))
            before = compute_snapshot(source).snapshot_id
            retry_restored_candidate = False
            incumbent_observation, incumbent_feedback = current_observation, feedback
            incumbent_execution = last_applied_execution
            checkpoint = None
            if requires_public_profile or config.get('candidate_guard_enabled'):
                from contractedge.orchestration.candidate_guard import checkpoint_candidate
                from contractedge.execution.topomen import operation_path
                checkpoint = checkpoint_candidate(source,[operation_path(op) for op in plan['operations']],
                    directory/'incumbent-checkpoint.json')
                active_checkpoint = checkpoint
                active_incumbent = (current_observation,feedback)
            write_json(directory / 'plan.json', plan)
            review_plan = review_plan_with_retained({**trace['changeset'],
                'declared_artifacts': list(method_input.declared_artifacts),
                'public_execution_capabilities': config.get('public_execution_capabilities')}, feedback)
            write_json(directory / 'public-review-plan.json', review_plan)
            relative = directory.relative_to(out).as_posix()
            record_stage('consumer_projection', snapshot_id=before,
                inputs=(relative + '/front.trace.json',),
                outputs=(relative + '/plan.json', relative + '/public-review-plan.json'),
                projections=(relative + '/public-evidence.delivery.json',),
                predecessor=('planning', 'front_delivery', 'begin'))
            engine.step(StageResult('plan', 'READY',
                journal.capture(relative + '/plan.json', 'CONSUMER_PLAN', snapshot_id=before), snapshot_id=before))
            engine.project(status='EXECUTING', case_id=case_id, round_id=round_id)
            execution_call_start = journal.position()
            execution_status, review = 'INVALID_OBSERVATION', None
            rejection_kind = None
            try:
                acceptance = accept_plan(plan, source, policy)
                write_json(directory / 'consumer-acceptance.json', acceptance)
            except PlanRejected as exc:
                rejection_kind = 'PLAN'
                execution_result = {'status': 'PLAN_REJECTED', 'error': str(exc)}
            else:
                attempts = []
                while True:
                    calls_before_execution = len(budget.calls)
                    rejection_kind = None
                    try:
                        with semantic_monitor.scope(semantic_context_for('EXECUTION_ROUND_' + str(round_id),
                                evidence=observations)):
                            execution_result = executor.execute(plan, source, policy,
                                requirement=method_input.requirement_text, declared_artifacts=method_input.declared_artifacts,
                                observations=observations, feedback=feedback, previous_attempt=last_execution_attempt)
                    except SemanticDeliveryError:
                        raise
                    except (PlanRejected, ValueError) as exc:
                        rejection_kind = 'EXECUTION'
                        execution_result = {'status': 'CONCRETE_EDIT_REJECTED', 'error': str(exc)}
                        if getattr(executor, 'last_trace', None) is not None:
                            execution_result['topomen_trace'] = executor.last_trace
                    completed_response = False
                    if len(budget.calls) > calls_before_execution:
                        call = budget.calls[-1]
                        if (call.get('prompt_version') == 'topomen-source-execution.v1'
                                and isinstance(call.get('response_content'), str) and not call.get('error')):
                            completed_response = True
                            last_execution_attempt = {'model_response': call['response_content'],
                                'response_metadata': call.get('response_metadata', {}),
                                'validation_error': execution_result.get('error'),
                                'plan_digest': canonical_digest(plan), 'source_snapshot': before}
                            execution_result['execution_attempt'] = last_execution_attempt
                    attempts.append(execution_result)
                    write_json(directory / 'execution-attempts' / (str(len(attempts) - 1) + '.json'), execution_result)
                    record_stage('execute_attempt', snapshot_id=compute_snapshot(source).snapshot_id,
                        status=execution_result['status'], call_start=calls_before_execution,
                        inputs=(relative + '/plan.json',),
                        outputs=(relative + '/execution-attempts/' + str(len(attempts)-1) + '.json',),
                        predecessor=('consumer_projection',),
                        attempt_id='round-' + str(round_id) + '.attempt-' + str(len(attempts)-1))
                    can_correct = (config.get('execution_format_retry_in_round', False)
                        and rejection_kind == 'EXECUTION' and completed_response
                        and compute_snapshot(source).snapshot_id == before
                        and execution_format_corrections < max(0, int(config.get('max_format_retries', 1))))
                    if not can_correct:
                        break
                    # One existing concrete-generation correction belongs inside
                    # the round: no S1 or fresh execution feedback exists yet.
                    # Never retry a missing/unknown HTTP response. Shared case
                    # tokens, calls, wall time and money still guard the request.
                    execution_format_corrections = engine.policy.consume('format_correction', 'execution:case')
                execution_result = {**execution_result, 'generation_attempts': len(attempts),
                                    'format_corrections_used': execution_format_corrections}
            write_json(directory / 'execution.json', execution_result)
            if execution_result.get('status')=='APPLIED':
                last_applied_execution=execution_result
            patch_info = runner.export(workspace, directory / 'candidate.patch', directory / 'export')
            write_json(directory / 'patch.json', patch_info)
            record_stage('execute', snapshot_id=compute_snapshot(source).snapshot_id,
                status=execution_result['status'], call_start=execution_call_start,
                inputs=(relative + '/plan.json',),
                outputs=(relative + '/execution.json', relative + '/patch.json', relative + '/candidate.patch'),
                predecessor=('consumer_projection', 'execute_attempt'))
            engine.step(StageResult('execute', 'EDIT_REJECTED' if rejection_kind == 'EXECUTION' else
                'PLAN_INVALID' if rejection_kind == 'PLAN' else 'APPLIED', journal.latest.get('execute'),
                snapshot_id=compute_snapshot(source).snapshot_id))
            if rejection_kind is None:
                engine.project(status='PUBLIC_CHECK', case_id=case_id, round_id=round_id)
                reserve_public_time()
                current_observation = observe_recorded(directory / 'public', directory / 'public-observation.json',
                    stage='observe', attempt_id='round-' + str(round_id), predecessor=('execute',))
                execution_status = current_observation['status']
                review_delivery = select_observations_delivery(
                    current_observation['observations'], method_input.requirement_text + '\n' + '\n'.join(
                        p['statement'] for kind in ['postconditions', 'preserve_constraints', 'open_obligations']
                        for p in review_plan.get(kind, [])),
                    token_counter, int(config.get('max_review_evidence_tokens', 9000)),
                    snapshot_id=compute_snapshot(source).snapshot_id)
                write_json(directory / 'review-evidence.delivery.json', review_delivery.receipt)
                review_observation = {**current_observation, 'declared_artifacts': list(method_input.declared_artifacts),
                    'public_execution_capabilities': config.get('public_execution_capabilities'),
                    'execution_mapping': execution_review_mapping(execution_result),
                    'observations': list(review_delivery.records)}
                from contractedge.execution.public_observer import isolated_observation_complete
                review_call_start = journal.position()
                if (current_observation['status'] in {'INFRA_ERROR','INVALID_OBSERVATION','BUDGET_EXCEEDED'}
                        or requires_public_profile and not isolated_observation_complete(current_observation)):
                    review = {'decision': 'UNKNOWN', 'checks': [], 'failure_kind': 'UNKNOWN',
                        'method': 'ISOLATED_OBSERVATION_INCOMPLETE',
                        'rationale': 'No review dispatch is admitted without complete isolated runs, public checks and confirmed cleanup.'}
                elif review_delivery.receipt['status'] != 'COMPLETE':
                    review = {'decision': 'UNKNOWN', 'checks': [], 'failure_kind': 'UNKNOWN',
                        'method': 'EVIDENCE_DELIVERY_INCOMPLETE',
                        'rationale': 'Required public review evidence was not completely delivered.',
                        'delivery_receipt_digest': review_delivery.receipt['receipt_digest']}
                else:
                    try:
                        with semantic_monitor.scope(semantic_context_for('PUBLIC_REVIEW_ROUND_' + str(round_id),
                                evidence=review_delivery.records,
                                mandatory_refs=review_delivery.receipt.get('mandatory_refs', ()),
                                source_requirements=False)):
                            review = reviewer.review(method_input.requirement_text, review_plan, review_observation)
                    except SemanticDeliveryError:
                        raise
                    except (ValueError, KeyError) as exc:
                        review = {'decision': 'UNKNOWN', 'checks': [], 'failure_kind': 'UNKNOWN',
                                  'method': 'PUBLIC_REVIEW_INVALID', 'rationale': str(exc)}
                from contractedge.execution.public_observer import enforce_public_profile
                review = enforce_public_profile(review, current_observation,
                    required=requires_public_profile)
                write_json(directory / 'public-review.json', review)
                record_stage('review', snapshot_id=compute_snapshot(source).snapshot_id,
                    status=review['decision'], call_start=review_call_start,
                    inputs=(relative + '/public-review-plan.json', relative + '/public-observation.json'),
                    outputs=(relative + '/public-review.json',),
                    projections=(relative + '/review-evidence.delivery.json',), predecessor=('observe',))
            else:
                write_json(directory / 'observation-review-skipped.json', {'status': 'NOT_RUN',
                    'snapshot_id': compute_snapshot(source).snapshot_id, 'reason': rejection_kind,
                    'observation_invoked': False, 'review_invoked': False})
                record_stage('observe', snapshot_id=compute_snapshot(source).snapshot_id, status='NOT_RUN',
                    outputs=(relative + '/observation-review-skipped.json',), predecessor=('execute',))
                record_stage('review', snapshot_id=compute_snapshot(source).snapshot_id, status='NOT_RUN',
                    outputs=(relative + '/observation-review-skipped.json',), predecessor=('observe',))
            after = compute_snapshot(source).snapshot_id
            raw_observations = (current_observation or {}).get('observations', [])
            fresh = [o for o in raw_observations if o['snapshot_id'] == after]
            from contractedge.execution.public import public_failure_diagnostics
            feedback = make_feedback(case_id=case_id, run_id=run_id, input_digest=method_input.input_digest,
                round_id=round_id, from_snapshot=before, to_snapshot=after,
                patch_digest=patch_info['patch_sha256'], execution_status=execution_status,
                observations=fresh, plan=review_plan, review=review, diagnosis=trace.get('diagnosis'),
                observation_diagnostics=public_failure_diagnostics(current_observation or {}))
            if rejection_kind:
                feedback.update(build_state='NOT_RUN', failure_kind=rejection_kind,
                    route='CHANGESET' if rejection_kind == 'PLAN' else 'EXECUTE',
                    route_confidence='HIGH', rationale=execution_result['error'])
                validate_feedback(feedback, case_id=case_id, input_digest=method_input.input_digest, to_snapshot=after)
            if execution_result.get('helper_preconditions'):
                feedback['helper_preconditions']=execution_result['helper_preconditions']
                validate_feedback(feedback,case_id=case_id,input_digest=method_input.input_digest,to_snapshot=after)
            if rejection_kind is None and config.get('operation_consistency_enabled'):
                from contractedge.orchestration.feedback import reassess_unchanged_operations, reassess_failed_target_predicates
                feedback=reassess_unchanged_operations(feedback,incumbent_observation,current_observation)
                feedback=reassess_failed_target_predicates(feedback,incumbent_observation,current_observation,review_plan)
                validate_feedback(feedback,case_id=case_id,input_digest=method_input.input_digest,to_snapshot=after)
            write_json(directory / 'feedback.json', feedback)
            record_stage('feedback', snapshot_id=after, status=feedback['route'],
                inputs=(relative + '/public-review-plan.json', relative + '/execution.json'),
                outputs=(relative + '/feedback.json',),
                predecessor=('execute', 'review') if rejection_kind is None else ('execute',))
            semantic_monitor.outcome(semantic_context_for('RESULT_ROUND_' + str(round_id),
                    source_requirements=False),
                artifacts=tuple(journal.latest[key] for key in ('consumer_projection', 'execute', 'observe', 'review', 'feedback')
                                if key in journal.latest),
                execution={'result': execution_result, 'patch': patch_info,
                           'mapping': execution_review_mapping(execution_result) if rejection_kind is None else None},
                review=review, observations=current_observation)
            feedback_decision = engine.step(StageResult('feedback',
                'EDIT_REJECTED' if rejection_kind == 'EXECUTION' else 'PLAN_INVALID' if rejection_kind == 'PLAN' else
                feedback_status(feedback), journal.latest['feedback'], snapshot_id=after, route=feedback['route'],
                terminal_status=stop_status(feedback)))
            if checkpoint and rejection_kind is None and incumbent_observation:
                from contractedge.orchestration.candidate_guard import compare_candidate_checks, preservation_check_ids, restore_candidate
                protected_checks = preservation_check_ids(review_plan, incumbent_feedback)
                if requires_public_profile:
                    # Registered checks do not depend on which conditions a
                    # model chooses to include. Every observed PASS is retained.
                    protected_checks = sorted(set(protected_checks) | {
                        row['behavior_check']['id'] for row in incumbent_observation.get('observations', [])
                        if isinstance(row.get('behavior_check'), Mapping) and row['behavior_check'].get('id')})
                decision = compare_candidate_checks({**incumbent_observation,'feedback':incumbent_feedback},
                    {**current_observation,'feedback':feedback}, protected_checks)
                if requires_public_profile and not isolated_observation_complete(current_observation):
                    decision = {**decision, 'decision': 'UNCOMPARABLE',
                        'uncomparable': [*decision.get('uncomparable', []), {
                            'check_id': 'REQUIRED_ISOLATED_RUNTIME',
                            'reason': 'Independent observation or runtime cleanup is incomplete; trial cannot be promoted.'}],
                        'interpretation': 'Incomplete isolated observation; retain the incumbent without further review dispatch.'}
                write_json(directory/'candidate-decision.json',decision)
                record_stage('candidate_decision', snapshot_id=after, status=decision['decision'],
                    outputs=(relative + '/candidate-decision.json',), predecessor=('feedback', 'observe', 'semantic_outcome'))
                engine.step(StageResult('candidate_selection',
                    'COMPLETE' if decision['decision'] == 'PROMOTE' else 'CANDIDATE_REJECTED',
                    journal.latest['candidate_decision'], snapshot_id=after))
                if decision['decision']!='PROMOTE':
                    # A registered production startup panic is actionable even
                    # when it prevented completing preservation checks. Keep
                    # their status UNKNOWN, restore the incumbent, and use only
                    # an already available edit round; generic probe/infra
                    # failures still stop without guessing a source repair.
                    from contractedge.execution.public import has_candidate_runtime_failure
                    runtime_failed = (current_observation.get('snapshot_id')==decision['trial_snapshot']
                        and has_candidate_runtime_failure(current_observation))
                    # Keep the trial artifacts above, but export only the coherent
                    # incumbent below. Failed trial evidence retains its own snapshot.
                    rejected_context={'snapshot_id':decision['trial_snapshot'],
                        'feedback':feedback_prompt_view(feedback),'decision':decision,
                        'patch_sha256':patch_info['patch_sha256'],
                        'interpretation':'Rejected historical trial. These observations describe the trial, not the restored current source.'}
                    restore_recorded(checkpoint, directory / 'rollback.json',
                        attempt_id='round-' + str(round_id), predecessor=('candidate_decision','feedback'))
                    last_applied_execution=incumbent_execution
                    rejected_trials.append({'round_id':round_id,**decision})
                    current_observation=incumbent_observation
                    feedback=incumbent_feedback
                    active_checkpoint=None
                    after=before
                    retry_restored_candidate=(round_id+1<max_rounds and (
                        decision['decision']=='REJECT_REGRESSION'
                        or decision['decision']=='UNCOMPARABLE' and runtime_failed))
                    if retry_restored_candidate:
                        last_execution_attempt={**(last_execution_attempt or {}),'source_snapshot':before,
                            'validation_error':('Candidate rejected: a protected current behavior regressed. '
                                if decision['decision']=='REJECT_REGRESSION' else
                                'Candidate rejected: production startup failed and protected behavior could not be verified. ')
                                +'Revise the accepted intent on the restored source using historical trial evidence; preserve every current condition.',
                            'rejected_trial':rejected_context}
                    rounds.append(dict(round_id=round_id,from_snapshot=before,
                        trial_snapshot=decision['trial_snapshot'],to_snapshot=before,
                        route='EXECUTE' if retry_restored_candidate else 'STOP',candidate_decision=decision['decision'],
                        feedback=str((directory/'feedback.json').relative_to(out))))
                    status='CANDIDATE_REGRESSION_REJECTED' if decision['decision']=='REJECT_REGRESSION' else 'CANDIDATE_COMPARISON_UNCERTAIN'
                    write_json(out/'selected-candidate.json',dict(snapshot_id=before,selection='INCUMBENT',rejected_trials=rejected_trials))
                    if retry_restored_candidate:continue
                    break
            active_checkpoint=None
            rounds.append({'round_id': round_id, 'from_snapshot': before, 'to_snapshot': after,
                           'feedback': str((directory / 'feedback.json').relative_to(out)),
                           'patch_sha256': patch_info['patch_sha256'], 'route': feedback['route']})
            if (config.get('execution_format_retry_in_round', False) and rejection_kind == 'EXECUTION' and before == after):
                status = 'INVALID_OUTPUT'
                errors.append('Concrete generation could not produce an accepted edit within its existing correction allowance')
                break
            if feedback_decision.action == 'STOP':
                status = stop_status(feedback)
                break
            status = 'ROUND_LIMIT'
    except SemanticDeliveryError as exc:
        primary_error = exc
        status = 'EVIDENCE_DELIVERY_INCOMPLETE'
        errors.append(str(exc))
    except BudgetExceeded as exc:
        primary_error = exc
        status = 'BUDGET_EXHAUSTED'
        errors.append(str(exc))
        preserve_error(exc, lambda: engine.step(StageResult('failure', 'BUDGET_EXHAUSTED',
            journal.latest.get('execute') or journal.latest.get('front'),
            snapshot_id=compute_snapshot(source).snapshot_id if source else initial_snapshot,
            call_refs=journal.refs(max(0, len(budget.calls)-1))
                if budget.calls and budget.calls[-1].get('error') else ())))
    except Exception as exc:
        primary_error = exc
        if budget.remaining_seconds <= 0:
            status = 'BUDGET_EXHAUSTED'
        elif status not in {'INFRA_ERROR', 'INVALID_OBSERVATION', 'BUDGET_EXCEEDED'}:
            status = 'INFRASTRUCTURE_ERROR'
        errors.append(type(exc).__name__ + ': ' + str(exc))
        failure_status = ('PLAN_INVALID' if isinstance(exc, PlanRejected) else
            'RESPONSE_UNKNOWN' if budget.calls and budget.calls[-1].get('error')
                and 'response_content' not in budget.calls[-1] else 'ENVIRONMENT_ERROR')
        preserve_error(exc, lambda: engine.step(StageResult('failure', failure_status,
            journal.latest.get('execute') or journal.latest.get('front'),
            snapshot_id=compute_snapshot(source).snapshot_id if source else initial_snapshot,
            call_refs=journal.refs(max(0, len(budget.calls)-1))
                if budget.calls and budget.calls[-1].get('error') else ())))
    if active_checkpoint is not None and source is not None:
        # Transport errors, budget exits and failed observations must not bypass
        # promotion. The previous candidate is selected until a trial completes.
        try:
            from contractedge.orchestration.candidate_guard import restore_candidate
            trial_snapshot=compute_snapshot(source).snapshot_id
            restore_recorded(active_checkpoint, out / 'emergency-rollback.json',
                attempt_id='incomplete-trial', predecessor=('candidate_decision','feedback','execute'))
            current_observation,feedback=active_incumbent
            rejected_trials.append(dict(decision='INCOMPLETE_TRIAL_REJECTED',
                from_snapshot=active_checkpoint['snapshot_id'],trial_snapshot=trial_snapshot,
                reason='Trial did not complete public review/promotion'))
            write_json(out/'selected-candidate.json',dict(snapshot_id=active_checkpoint['snapshot_id'],
                selection='INCUMBENT',rejected_trials=rejected_trials))
        except Exception as exc:
            source_integrity_verified=False
            errors.append('Candidate restoration failed; export prohibited: '+str(exc))
            status='INFRASTRUCTURE_ERROR'
    final_patch = None
    if source is not None and source_integrity_verified:
        try:
            final_patch = runner.export(workspace, out / 'candidate.patch', out / 'final-export')
        except Exception as exc:
            status = 'INFRASTRUCTURE_ERROR'
            errors.append('Final patch export: ' + str(exc))
    report = {'schema_version': 'ce.repair_trace.v1', 'case_id': case_id,
              'release_id': method_input.release_id, 'run_id': run_id,
              'input_digest': method_input.input_digest, 'requirement_digest': canonical_text_digest(method_input.requirement_text),
              'initial_snapshot_id': initial_snapshot,
              'final_snapshot_id': compute_snapshot(source).snapshot_id if source else None,
              'status': status, 'candidate': final_patch, 'rounds': rounds,
              'candidate_selection': {'rejected_trials':len(rejected_trials),'decisions':rejected_trials},
              'last_feedback': feedback, 'budget': budget.summary(), 'errors': errors,
              'execution_format_corrections_used': execution_format_corrections,
              'hidden_evaluation': 'NOT_RUN; final patch must be scored in an independent evaluator process',
              'scope': 'Public repair execution. A public review is an uncertain method signal, not a benchmark PASS.'}
    if initial_trace:
        report['resumed_front_trace_digest'] = canonical_digest(initial_trace)
    write_json(out / 'model_calls.json', budget.calls)
    write_json(out / 'repair.trace.json', report)
    preserve_error(primary_error or journal.primary_error, lambda: engine.project(status=status, case_id=case_id, rounds_completed=len(rounds)))
    selection_outputs = ['repair.trace.json']
    write_json(out / 'selected-feedback.json', report['last_feedback'])
    selection_outputs.append('selected-feedback.json')
    if final_patch is not None:
        selection_outputs.append('candidate.patch')
    if (out / 'selected-candidate.json').is_file():
        selection_outputs.append('selected-candidate.json')
    primary_error = primary_error or journal.primary_error
    preserve_error(primary_error, lambda: record_stage('selection', attempt_id='final',
        snapshot_id=report['final_snapshot_id'], status=status,
        outputs=tuple(selection_outputs), predecessor=('feedback','candidate_decision','execute','begin',
            'semantic_outcome','semantic_delivery','front')))
    preserve_error(primary_error, lambda: engine.step(StageResult('selection', 'COMPLETE',
        journal.latest.get('selection'), snapshot_id=report['final_snapshot_id'], terminal_status=status)))
    preserve_error(primary_error or journal.primary_error, lambda: engine.project(status=status, case_id=case_id, rounds_completed=len(rounds)))
    if primary_error is not None and getattr(primary_error, 'recording_errors', ()):
        report['recording_errors'] = list(primary_error.recording_errors)
        write_json(out / 'repair.trace.json', report)
    return report
