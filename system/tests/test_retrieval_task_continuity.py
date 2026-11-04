"""A gap adds evidence without replacing the task or promoting optional context."""
from dataclasses import asdict, replace
import hashlib
import json

import pytest

from contractedge.retrieval.candidates import (
    CandidateSet, build_candidate_set, extend_once, requires_gap_delivery, trim_context_bundle,
)


def unit(identifier, text, *, path=None, **extra):
    return dict(id=identifier, path=path or identifier + '.go', text=text,
                snapshot_id='snap-0', content_digest=hashlib.sha256(text.encode()).hexdigest(),
                locator={'start_line': 1, 'end_line': len(text.splitlines())}, **extra)


def select(requirement, rows, *, top_n=2, observable=None, limit=10000):
    return build_candidate_set(requirement, observable, rows, [], top_n=top_n,
                               max_context_tokens=limit, token_counter=len)


def gap(identifier='G1', path='endpoint.go', symbol='Activate'):
    return dict(id=identifier, locator_hint=dict(path=path, symbol=symbol))


def endpoint(identifier='endpoint', gap_id='G1', symbol='Activate', **extra):
    return unit(identifier, f'func {symbol}() {{}}', symbol=symbol, declared_symbols=[symbol],
                kind='FUNCTION_OR_TYPE', gap_role_keys=[gap_id + ':anchor'],
                evidence_gap_refs=[gap_id], **extra)


@pytest.mark.parametrize(('requirement', 'source', 'path'), [
    ('Thread ConnectTimeout ReadTimeout WriteTimeout through worker construction',
     'type Config struct { ConnectTimeout, ReadTimeout, WriteTimeout int }', 'config/runtime.go'),
    ('Preserve connect_timeout read_timeout write_timeout when forwarding arguments',
     'def build(config):\n    return Client(config.connect_timeout, config.read_timeout, config.write_timeout)\n',
     'client/build.py'),
    ('Preserve serviceAccountName namespace releaseName when adding the new resource',
     'serviceAccountName: {{ .Values.serviceAccountName }}\nnamespace: {{ .Values.namespace }}\nreleaseName: {{ .Release.Name }}\n',
     'templates/role.yaml'),
])
def test_new_endpoint_does_not_discard_original_configuration_or_preservation(requirement, source, path):
    relevant = unit('z-task', source, path=path)
    noise = unit('a-noise', 'irrelevant diagnostic output')
    initial = select(requirement, [relevant, noise])
    assert initial.selected_ids[0] == 'z-task'
    result = extend_once(initial, [gap()], [endpoint()], max_context_tokens=10000, token_counter=len)
    assert result.selected_ids == ('endpoint', 'z-task')


def test_optional_role_does_not_bypass_more_relevant_configuration():
    requirement = 'Preserve `ConnectTimeout`, `ReadTimeout`, and `WriteTimeout` configuration.'
    config = unit('config', 'type Config struct { ConnectTimeout, ReadTimeout, WriteTimeout int }',
                  evidence_gap_refs=['G1'])
    optional = unit('telemetry', 'func Observe() { recordMetric() }',
                    gap_role_keys=['G1:context'], optional_gap_context=True,
                    evidence_gap_refs=['G1'], anchor_priority=4)
    assert not requires_gap_delivery(optional)
    assert select(requirement, [config, optional], top_n=1).selected_ids == ('config',)
    # Optional facts remain eligible when no more relevant evidence competes.
    assert select(requirement, [optional], top_n=1).selected_ids == ('telemetry',)


def test_directory_location_alone_does_not_raise_all_new_units_above_task_source():
    config = unit('config', 'ConnectTimeout ReadTimeout WriteTimeout')
    initial = select('Preserve ConnectTimeout ReadTimeout WriteTimeout', [config], top_n=2)
    noise = unit('noise', 'func Stats() { recordMetric() }', path='pkg/worker/metrics.go',
                 evidence_gap_refs=['G1'], scope_extension='EVIDENCE_GAP_DIRECTORY_PATH',
                 optional_gap_context=True)
    target = endpoint(path='pkg/worker/endpoint.go')
    result = extend_once(initial, [gap(path='pkg/worker')], [target, noise],
                         max_context_tokens=10000, token_counter=len)
    assert result.selected_ids == ('endpoint', 'config')
    assert result.ranking_features['noise']['anchor_priority'] == 0


def test_original_observable_survives_gap_reranking_as_secondary_evidence():
    task = unit('z-runtime', 'peerDisconnected')
    noise = unit('a-noise', 'unrelated')
    initial = select('Repair client', [task, noise], observable='peerDisconnected')
    result = extend_once(initial, [gap()], [endpoint()], max_context_tokens=10000, token_counter=len)
    assert result.ranking_features['z-runtime']['observable_term_hits'] > 0
    assert result.selected_ids == ('endpoint', 'z-runtime')


def test_exact_new_endpoint_still_replaces_old_task_context_at_top_one():
    initial = select('ConnectTimeout', [unit('old', 'ConnectTimeout')], top_n=1)
    result = extend_once(initial, [gap()], [endpoint()], max_context_tokens=10000, token_counter=len)
    assert result.selected_ids == ('endpoint',)
    assert result.required_endpoint_ids == ('endpoint',)


def test_required_prior_source_and_new_endpoint_both_keep_hard_priority():
    previous = unit('prior', 'prior source contract', required_for_gap_delivery=True)
    initial = select('configuration', [previous, unit('noise', 'configuration')], top_n=2)
    initial = replace(initial, required_endpoint_ids=('prior',))
    result = extend_once(initial, [gap()], [endpoint()], max_context_tokens=10000, token_counter=len)
    assert set(result.selected_ids) == {'prior', 'endpoint'}
    assert set(result.required_endpoint_ids) == {'prior', 'endpoint'}


def test_required_endpoint_over_budget_is_reported_missing_not_demoted():
    target = endpoint()
    initial = select('configuration', [], limit=5)
    result = extend_once(initial, [gap()], [target], max_context_tokens=5, token_counter=len)
    assert result.selected_ids == ()
    assert result.required_endpoint_ids == ('endpoint',)
    assert result.drop_reasons['endpoint'] == 'CONTEXT_BUDGET'


def test_optional_identity_merge_keeps_an_actual_anchor_required():
    target = endpoint()
    optional = {**target, 'gap_role_keys': ['G1:context'], 'optional_gap_context': True}
    initial = select('irrelevant', [], top_n=1)
    result = extend_once(initial, [gap()], [optional, target], max_context_tokens=10000, token_counter=len)
    assert result.selected_ids == ('endpoint',)
    assert result.required_endpoint_ids == ('endpoint',)
    assert requires_gap_delivery(result.candidates[0])


def test_reextension_and_trace_roundtrip_keep_original_ranking_task():
    requirement = 'Preserve ConnectTimeout ReadTimeout WriteTimeout'
    initial = select(requirement, [unit('config', 'ConnectTimeout ReadTimeout WriteTimeout'),
                                  unit('noise', 'irrelevant')], top_n=3, observable='connection stalled')
    first = extend_once(initial, [gap()], [endpoint()], max_context_tokens=10000, token_counter=len)
    restored = CandidateSet(**json.loads(json.dumps(asdict(first))))
    second = extend_once(restored, [gap('G2', 'second.go', 'Finalize')],
                         [endpoint('second', 'G2', 'Finalize')], max_context_tokens=10000,
                         token_counter=len, allow_reextension=True)
    assert set(second.selected_ids) == {'endpoint', 'second', 'config'}
    assert second.ranking_requirement == requirement
    assert second.ranking_observable == 'connection stalled'


def test_trim_and_legacy_trace_preserve_compatible_ranking_context():
    initial = select('ConnectTimeout', [unit('config', 'ConnectTimeout'), unit('noise', 'irrelevant')],
                     observable='connection stalled')
    trimmed = trim_context_bundle(initial, request_tokens=10001, limit=10000)
    assert trimmed.ranking_requirement == initial.ranking_requirement == 'ConnectTimeout'
    assert trimmed.ranking_observable == 'connection stalled'
    legacy = {k: v for k, v in asdict(initial).items()
              if k not in {'ranking_requirement', 'ranking_observable'}}
    restored = CandidateSet(**legacy)
    assert restored.ranking_requirement == ''
    result = extend_once(restored, [gap()], [endpoint()], max_context_tokens=10000, token_counter=len)
    assert 'endpoint' in result.selected_ids


def test_engine_ranking_context_does_not_enter_diagnosis_wire():
    from contractedge.contracts.models import RunContext
    from contractedge.contracts.validation import parse_method_input
    from contractedge.diagnosis.prompt import build_diagnosis_request
    from contractedge.requirement.normalize import normalize_requirement
    from tests.test_contracts import valid_method_input
    method = parse_method_input(valid_method_input())
    requirement = normalize_requirement({'requirement_text': method.requirement_text})
    context = RunContext('run', method.case_id, 0, 'snap-0', None, method.input_digest,
                         model_config={'model': 'fixture'}, budget={'max_output_tokens': 2000})
    candidates = select('ENGINE_ONLY_RANKING_REQUIREMENT', [unit('config', 'public source')],
                        observable='ENGINE_ONLY_OBSERVABLE')
    request = build_diagnosis_request(method, requirement, candidates, context)
    body = request.messages[1]['content']
    assert 'ENGINE_ONLY_RANKING_REQUIREMENT' not in body
    assert 'ENGINE_ONLY_OBSERVABLE' not in body
    assert 'ranking_requirement' not in body
    assert 'ranking_observable' not in body
