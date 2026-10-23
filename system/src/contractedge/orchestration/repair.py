"""Bounded public repair loop: O/E/V/C/P -> concrete patch -> S1 -> feedback.

This module has no evaluator-root or private-score input. Hidden scoring is a
separate command/process after final candidate selection.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass, replace
import json
import os
import sys
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
                       directory, execution_feedback=None, public_observations=(), evidence_delivery=None):
    """Persist exact effective front inputs, without synthesizing legacy traces."""
    directory = Path(directory)
    write_json(directory / 'front.method_input.json', asdict(method_input) if is_dataclass(method_input) else dict(method_input))
    write_json(directory / 'front.config.json', dict(config))
    write_json(directory / 'front.public_observations.json', list(public_observations))
    write_json(directory / 'front.execution_feedback.json', execution_feedback)
    start = len(provider.calls)
    try:
        return run_front_half(method_input, workspace, provider, config, token_counter,
                              execution_feedback=execution_feedback, public_observations=public_observations)
    finally:
        active_error = sys.exc_info()[1]
        try:
            write_json(directory / 'front.replay.json', {'schema_version': 'ce.front_replay_inputs.v1',
                'model_call_range': [start, len(provider.calls)],
                'scope': 'front_semantic_failure', 'semantic_reviewer': 'disabled'})
            write_json(directory / 'front.delivery.json', build_front_delivery(
                directory, method_input, provider, start, evidence_delivery))
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
    budget = BudgetedProvider(provider, token_counter, max_tokens=int(config.get('max_total_tokens', 160000)),
        max_seconds=float(config.get('max_elapsed_seconds', 2400)), max_calls=int(config.get('max_model_calls', 24)),
        on_update=lambda calls: write_json(out / 'model_calls.json', calls))
    if _runtime_started is not None:
        budget.started = _runtime_started
    runner = public_runner or BenchmarkPublicRunner(root, tools_root,
        lock_root=config.get('lock_root', '/tmp/contractedge-system-native-locks'),
        timeout_seconds=float(config.get('public_timeout_seconds', 600)), query=method_input.requirement_text,
        method_probe_profiles=config.get('method_probe_profiles', []))
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
    status = 'STARTING'
    source = None
    initial_snapshot = method_input.initial_snapshot_id
    max_rounds = int(config.get('max_rounds', 2))
    if not 1 <= max_rounds <= 16:
        raise ValueError('max_rounds must be between1and16')
    write_json(out / 'method_input.json', method_value)
    write_json(out / 'projection.json', projection)
    def reserve_public_time():
        if budget.remaining_seconds <= 0:
            raise BudgetExceeded('Global wall-clock budget exhausted before public execution')
        if hasattr(runner, 'timeout_seconds'):
            runner.timeout_seconds = min(float(config.get('public_timeout_seconds', 600)), budget.remaining_seconds)
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
        write_json(out / 'state.json', {'status': 'PREPARING', 'case_id': case_id})
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
        if config.get('observe_broken', True):
            write_json(out / 'state.json', {'status': 'OBSERVING_BROKEN', 'case_id': case_id})
            reserve_public_time()
            current_observation = runner.observe(workspace, out / 'broken-public')
            if current_observation['status'] in {'INFRA_ERROR', 'INVALID_OBSERVATION', 'BUDGET_EXCEEDED'}:
                status = current_observation['status']
                raise ValueError('Initial public observation could not be produced')
        for round_id in range(max_rounds):
            if budget.remaining_seconds <= 0:
                raise BudgetExceeded('Global wall-clock budget exhausted')
            directory = out / 'rounds' / str(round_id)
            directory.mkdir(parents=True)
            write_json(out / 'state.json', {'status': 'PLANNING', 'case_id': case_id, 'round_id': round_id})
            delivery = select_observations_delivery((current_observation or {}).get('observations', []),
                method_input.requirement_text, token_counter, int(config.get('max_public_evidence_tokens', 4000)),
                snapshot_id=compute_snapshot(source).snapshot_id)
            write_json(directory / 'public-evidence.delivery.json', delivery.receipt)
            if delivery.receipt['status'] != 'COMPLETE':
                status = 'EVIDENCE_DELIVERY_INCOMPLETE'
                errors.append('Public evidence delivery ' + delivery.receipt['status'] + ': '
                    + json.dumps({'coverage': delivery.receipt['mandatory_coverage'],
                                  'errors': delivery.receipt['errors']}, ensure_ascii=False))
                break
            observations = delivery.records
            if trace is None or (feedback and feedback['route'] == 'DIAGNOSIS' and not retry_restored_candidate):
                round_config = {**dict(config), 'run_id': run_id,
                    'max_total_tokens': max(1, budget.max_tokens - budget.reserved_tokens),
                    'max_elapsed_seconds': budget.remaining_seconds,
                    'model': {'model': config['model'], 'provider': config.get('provider', 'configured'),
                              'schema_in_prompt': config.get('schema_in_prompt', True)}}
                trace = run_front_recorded(method_input, source if method_input.source_root == '.' else source.parent,
                    budget, round_config, token_counter, execution_feedback=feedback,
                    public_observations=observations, directory=directory, evidence_delivery=delivery.receipt).to_wire()
                plan = None
            if trace['status'] not in {'READY', 'READY_PARTIAL'}:
                write_json(directory / 'front.trace.json', trace)
                status = trace['status']
                errors.extend(trace.get('errors', []))
                break
            if feedback and feedback['route'] == 'CHANGESET' and not retry_restored_candidate:
                if trace['snapshot_id'] == compute_snapshot(source).snapshot_id:
                    trace = revise_plan(trace, method_input, source, feedback, budget, token_counter, config)
                    plan = None
                else:
                    # Old source evidence cannot be relabelled as current solely to reuse a diagnosis.
                    feedback = {**feedback, 'route': 'DIAGNOSIS', 'failure_kind': 'DIAGNOSIS',
                                'rationale': 'Plan feedback requires rediagnosis because source evidence is stale.'}
                    trace = run_front_recorded(method_input, source if method_input.source_root == '.' else source.parent,
                        budget, {**dict(config), 'run_id': run_id, 'model': {'model': config['model'],
                            'schema_in_prompt': config.get('schema_in_prompt', True)}},
                        token_counter, execution_feedback=feedback, public_observations=observations,
                        directory=directory, evidence_delivery=delivery.receipt).to_wire()
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
            if config.get('candidate_guard_enabled'):
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
            write_json(out / 'state.json', {'status': 'EXECUTING', 'case_id': case_id, 'round_id': round_id})
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
                        execution_result = executor.execute(plan, source, policy,
                            requirement=method_input.requirement_text, declared_artifacts=method_input.declared_artifacts,
                            observations=observations, feedback=feedback, previous_attempt=last_execution_attempt)
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
                    execution_format_corrections += 1
                execution_result = {**execution_result, 'generation_attempts': len(attempts),
                                    'format_corrections_used': execution_format_corrections}
            write_json(directory / 'execution.json', execution_result)
            if execution_result.get('status')=='APPLIED':
                last_applied_execution=execution_result
            patch_info = runner.export(workspace, directory / 'candidate.patch', directory / 'export')
            write_json(directory / 'patch.json', patch_info)
            if rejection_kind is None:
                write_json(out / 'state.json', {'status': 'PUBLIC_CHECK', 'case_id': case_id, 'round_id': round_id})
                reserve_public_time()
                current_observation = runner.observe(workspace, directory / 'public')
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
                if review_delivery.receipt['status'] != 'COMPLETE':
                    review = {'decision': 'UNKNOWN', 'checks': [], 'failure_kind': 'UNKNOWN',
                        'method': 'EVIDENCE_DELIVERY_INCOMPLETE',
                        'rationale': 'Required public review evidence was not completely delivered.',
                        'delivery_receipt_digest': review_delivery.receipt['receipt_digest']}
                else:
                    try:
                        review = reviewer.review(method_input.requirement_text, review_plan, review_observation)
                    except (ValueError, KeyError) as exc:
                        review = {'decision': 'UNKNOWN', 'checks': [], 'failure_kind': 'UNKNOWN',
                                  'method': 'PUBLIC_REVIEW_INVALID', 'rationale': str(exc)}
                write_json(directory / 'public-review.json', review)
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
            if checkpoint and rejection_kind is None and incumbent_observation:
                from contractedge.orchestration.candidate_guard import compare_candidate_checks, preservation_check_ids, restore_candidate
                decision = compare_candidate_checks({**incumbent_observation,'feedback':incumbent_feedback},
                    {**current_observation,'feedback':feedback},preservation_check_ids(review_plan,incumbent_feedback))
                write_json(directory/'candidate-decision.json',decision)
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
                    restore_candidate(source,checkpoint)
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
            if feedback['route'] == 'STOP':
                status = ('PUBLIC_REVIEW_COMPLETE' if feedback['semantic_outcome'] == 'PUBLIC_REVIEW_PASS' else
                          'PUBLIC_EXECUTION_COMPLETE' if feedback['build_state'] == 'PASSED' else
                          'INFRASTRUCTURE_ERROR' if feedback['failure_kind'] == 'ENVIRONMENT' else 'STOPPED_UNCERTAIN')
                break
            status = 'ROUND_LIMIT'
    except BudgetExceeded as exc:
        status = 'BUDGET_EXHAUSTED'
        errors.append(str(exc))
    except Exception as exc:
        if budget.remaining_seconds <= 0:
            status = 'BUDGET_EXHAUSTED'
        elif status not in {'INFRA_ERROR', 'INVALID_OBSERVATION', 'BUDGET_EXCEEDED'}:
            status = 'INFRASTRUCTURE_ERROR'
        errors.append(type(exc).__name__ + ': ' + str(exc))
    if active_checkpoint is not None and source is not None:
        # Transport errors, budget exits and failed observations must not bypass
        # promotion. The previous candidate is selected until a trial completes.
        try:
            from contractedge.orchestration.candidate_guard import restore_candidate
            trial_snapshot=compute_snapshot(source).snapshot_id
            restore_candidate(source,active_checkpoint)
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
    write_json(out / 'state.json', {'status': status, 'case_id': case_id, 'rounds_completed': len(rounds)})
    return report
