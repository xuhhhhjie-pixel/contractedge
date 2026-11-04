"""Requirement literals keep bounded priority without repository path tables."""
import hashlib

import pytest

from contractedge.retrieval.candidates import (
    build_candidate_set, extend_once, literal_identifier_hits, literal_identifiers, trim_context_bundle,
)
from contractedge.source.entrypoints import probe_entrypoint_hints
from contractedge.source.scopes import resolve_scope
from contractedge.source.snapshot import compute_snapshot
from contractedge.source.units import extract_units


def write(root, path, text):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def candidate(identifier, text, **extra):
    return dict(id=identifier, path=identifier + '.go', text=text, snapshot_id='S',
                locator={'start_line': 1, 'end_line': len(text.splitlines())},
                content_digest=hashlib.sha256(text.encode()).hexdigest(),
                kind='FUNCTION_OR_TYPE', **extra)


@pytest.mark.parametrize('operation', ['OPTIONS', 'REBALANCE'])
@pytest.mark.parametrize('path', ['engine/channels/consumer.go', 'other_product/transport/reader.go'])
def test_renamed_requirement_body_reaches_scope_pool_and_top_selection(tmp_path, operation, path):
    requirement = f'The timeout for {operation} discovery requests should be configurable.'
    write(tmp_path, path, f'package transport\nfunc discover() {{ record("{operation}") }}\n')
    for index in range(6):
        write(tmp_path, f'a_docs/{index}.md', 'timeout discovery requests configurable Options\n' * 8)
    write(tmp_path, 'a_types/options.go', 'package options\ntype Options struct { Timeout int }\n')
    snapshot = compute_snapshot(tmp_path)
    scope = resolve_scope(tmp_path, snapshot, [{'kind': 'concept', 'value': requirement}], max_matches_per_hint=2)
    assert scope.seed_paths[0] == path
    doc_paths = sorted(p for p in snapshot.file_digests if p.endswith('.md'))
    units = extract_units(snapshot, {'seed_paths': [*doc_paths, path, 'a_types/options.go']},
                          query=requirement, max_units=2, max_extension_files=0)
    assert len(units) == 2
    assert units[0]['path'] == path and units[0]['symbol'] == 'discover'
    assert not units[0]['truncated']
    result = build_candidate_set(requirement, 'Options timeout discovery requests configurable', units, [],
                                 top_n=1, max_context_tokens=2000, token_counter=len)
    assert result.candidates[0]['path'] == path
    assert result.context_tokens <= 2000 and result.selected_count == result.top_n == 1


@pytest.mark.parametrize('text,expected', [
    ('"OPTIONS"', 1), ('OPTIONS(', 1), ('options', 0), ('Options', 0),
    ('PreOPTIONS', 0), ('OPTIONSExtra', 0), ('OPTIONS_suffix', 0),
    ('$OPTIONS', 0), ('OPTIONS$', 0), ('OPTIONS2', 0),
])
def test_uppercase_literals_use_exact_case_and_identifier_boundaries(text, expected):
    identifiers = literal_identifiers('Make OPTIONS configurable.')
    assert identifiers == {'OPTIONS'}
    assert literal_identifier_hits(text, identifiers) == expected


def test_existing_quoted_camel_and_underscore_identifier_matching_is_preserved():
    identifiers = literal_identifiers('Use `wire.Mode`, requestTimeout and timeout_value for SYNC.')
    assert {'wire.mode', 'requesttimeout', 'timeout_value', 'SYNC'} <= identifiers
    assert literal_identifier_hits('WIRE.MODE RequestTimeout TIMEOUT_VALUE "SYNC"', identifiers) == 4
    assert literal_identifier_hits('WIRE.MODE RequestTimeout TIMEOUT_VALUE Sync', identifiers) == 3
    assert 'The' not in literal_identifiers('The request should be bounded.')


def test_exact_body_signal_precedes_arbitrary_observation_prose_overlap():
    words = ' '.join('observation' + str(i) for i in range(100))
    exact = candidate('production', 'func discover() { record("OPTIONS") }', scope_path_rank=900)
    prose = candidate('description', 'type Options struct {} // ' + words)
    result = build_candidate_set('Configure OPTIONS.', words, [prose, exact], [],
                                 top_n=1, max_context_tokens=10000, token_counter=len)
    assert result.selected_ids == ('production',)
    assert result.ranking_features['production']['literal_identifier_hits'] == 1
    assert result.ranking_features['description']['literal_identifier_hits'] == 0


def test_complete_production_literal_precedes_literal_boilerplate_in_pool(tmp_path):
    for index in range(5):
        write(tmp_path, f'docs/{index}.md', 'OPTIONS timeout discovery configurable\n' * 100)
    write(tmp_path, 'renamed/action.go', 'package action\nfunc perform() { emit("OPTIONS") }\n')
    snapshot = compute_snapshot(tmp_path)
    units = extract_units(snapshot, {'seed_paths': sorted(snapshot.file_digests)},
                          query='Configure OPTIONS.', max_units=1, max_extension_files=0)
    assert len(units) == 1 and units[0]['symbol'] == 'perform'


@pytest.mark.parametrize('limits', [dict(max_search_files=1), dict(max_search_bytes=1),
                                   dict(max_search_file_bytes=1)])
def test_literal_priority_does_not_expand_scope_read_caps(tmp_path, limits):
    write(tmp_path, 'a.go', 'package a\ntype Options struct {}\n')
    write(tmp_path, 'z.go', 'package z\nfunc run() { emit("OPTIONS") }\n')
    snapshot = compute_snapshot(tmp_path)
    scope = resolve_scope(tmp_path, snapshot, [{'kind': 'concept', 'value': 'Configure OPTIONS.'}], **limits)
    audit = scope.search_audit[0]
    assert audit['coverage_incomplete'] and 'z.go' not in scope.seed_paths
    if 'max_search_files' in limits:
        assert audit['indexed_files_total'] <= limits['max_search_files']
    if 'max_search_bytes' in limits:
        assert audit['indexed_bytes_total'] <= limits['max_search_bytes']
    if 'max_search_file_bytes' in limits:
        assert audit['indexed_bytes_total'] == 0


def test_explicit_task_api_and_direct_reference_support_remain_available(tmp_path):
    write(tmp_path, 'task/settings.yaml', 'enabled: true\n')
    write(tmp_path, 'publicapi/api.go', 'package publicapi\nfunc Entry() {}\n')
    write(tmp_path, 'renamed/action.go', 'package renamed\nfunc perform() { connector(); emit("SYNC") }\n')
    write(tmp_path, 'renamed/connector.go', 'package renamed\nfunc connector() {}\n')
    snapshot = compute_snapshot(tmp_path)
    hints, focused, _ = probe_entrypoint_hints(snapshot, [{
        'sha256': 'a' * 64, 'api_entrypoints': [{'package': 'publicapi', 'symbol': 'Entry'}]}])
    scope = resolve_scope(tmp_path, snapshot, [
        {'kind': 'path', 'value': 'task/settings.yaml'}, *hints,
        {'kind': 'concept', 'value': 'Configure SYNC.'}])
    assert {'task/settings.yaml', 'publicapi/api.go', 'renamed/action.go'} <= set(scope.seed_paths)
    units = extract_units(snapshot, {'seed_paths': scope.seed_paths}, query='Configure SYNC.',
                          focused_paths=focused, max_units=12, max_extension_files=1)
    assert {'Entry', 'perform', 'connector'} <= {unit.get('symbol') for unit in units}
    selected = build_candidate_set('Configure SYNC.', None, units, [],
                                  top_n=12, max_context_tokens=10000, token_counter=len)
    assert {'task/settings.yaml', 'publicapi/api.go', 'renamed/action.go', 'renamed/connector.go'} <= {
        unit['path'] for unit in selected.candidates}


def test_literal_and_boundary_labels_cannot_override_context_budget_or_required_source():
    literal = candidate('literal', 'func discover() { record("OPTIONS") }',
                        boundary_scope_anchor=True, boundary_scope_critical=True)
    required = candidate('required', 'func convert() {}', required_for_gap_delivery=True,
                         gap_role_keys=['G1:anchor'])
    selected = build_candidate_set('Configure OPTIONS.', None, [literal, required], [],
                                  top_n=2, max_context_tokens=1000, token_counter=len)
    trimmed = trim_context_bundle(selected, request_tokens=1001, limit=1000)
    assert trimmed.selected_ids == ('required',)
    bounded = build_candidate_set('Configure OPTIONS.', None, [literal], [],
                                 top_n=1, max_context_tokens=1, token_counter=len)
    assert bounded.selected_ids == () and bounded.drop_reasons['literal'] == 'CONTEXT_BUDGET'


@pytest.mark.parametrize('operation,folder', [('OPTIONS', 'renamed_transport'), ('REBALANCE', 'different_engine')])
def test_noisy_test_literals_do_not_displace_production_context_before_pool_cut(tmp_path, operation, folder):
    requirement = f'The timeout for binding {operation} discovery requests should be configurable.'
    for index in range(30):
        write(tmp_path, f'tests/examples/{index}/app.go',
              f'package app\nfunc Run() {{ record("{operation}") /* binding timeout discovery requests configurable */ }}\n')
    write(tmp_path, f'{folder}/action.go',
          f'package engine\nfunc discover() {{ applyDeadline(); record("{operation}") }}\n')
    write(tmp_path, f'{folder}/config.go',
          'package engine\ntype Settings struct { BindingTimeout int /* configurable binding discovery timeout */ }\n')
    write(tmp_path, f'{folder}/construct.go',
          'package engine\nfunc construct(c Settings) { newChannel(c.BindingTimeout) /* binding discovery timeout */ }\n')
    snapshot = compute_snapshot(tmp_path)
    scope = resolve_scope(tmp_path, snapshot, [{'kind': 'concept', 'value': requirement}], max_matches_per_hint=4)
    production_paths = {f'{folder}/{name}.go' for name in ('action', 'config', 'construct')}
    assert production_paths <= set(scope.seed_paths)
    # All bodies already read: generic literal examples must not consume the
    # intermediate pool before nonliteral production configuration gets ranked.
    units = extract_units(snapshot, {'seed_paths': sorted(snapshot.file_digests)},
                          query=requirement, max_units=4, max_extension_files=0)
    assert len(units) == 4 and production_paths <= {unit['path'] for unit in units}
    result = build_candidate_set(requirement, 'Run examples handle requests', units, [],
                                 top_n=3, max_context_tokens=10000, token_counter=len)
    assert {unit['path'] for unit in result.candidates} == production_paths


def test_explicit_test_path_without_query_overlap_retains_existing_focus_admission(tmp_path):
    write(tmp_path, 'tests/requested.go', 'package checks\nfunc VerifyCompatibility() {}\n')
    write(tmp_path, 'engine.go', 'package engine\nfunc discover() { record("REBALANCE") }\n')
    snapshot = compute_snapshot(tmp_path)
    units = extract_units(snapshot, {'seed_paths': ['engine.go', 'tests/requested.go']},
                          query='Configure REBALANCE.', focused_paths=['tests/requested.go'],
                          max_units=1, max_extension_files=0)
    assert len(units) == 1 and units[0]['path'] == 'tests/requested.go'


@pytest.mark.parametrize('query', ['Configure REBALANCE.', 'Fix TestRequestedOperation.'])
def test_test_only_or_explicit_test_symbol_matches_remain_eligible(tmp_path, query):
    write(tmp_path, 'tests/example.go', 'package checks\nfunc TestRequestedOperation() { record("REBALANCE") }\n')
    write(tmp_path, 'engine.go', 'package engine\nfunc unrelated() {}\n')
    snapshot = compute_snapshot(tmp_path)
    units = extract_units(snapshot, {'seed_paths': ['engine.go', 'tests/example.go']},
                          query=query, max_units=1, max_extension_files=0)
    assert units[0]['path'] == 'tests/example.go'
    selected = build_candidate_set(query, None, units, [], top_n=1, max_context_tokens=1000, token_counter=len)
    assert selected.selected_count == 1


@pytest.mark.parametrize('operation', ['OPTIONS', 'REBALANCE'])
def test_broad_gap_priority_does_not_exempt_generic_tests_or_displace_explicit_context(operation):
    owner = candidate('owner', f'func Probe() {{ issue("{operation}") }}',
                      symbol='Probe', declared_symbols=['Probe'], truncated=False)
    owner['path'] = 'pkg/worker/probe.go'
    carrier = candidate('carrier', 'type Options struct { Timeout time.Duration }',
                        symbol='Options', declared_symbols=['Options'], truncated=False,
                        anchor_priority=2, source_focus_basis='PUBLIC_ENTRY_OR_EXPLICIT_RESOURCE_FILE; bounded relevant declaration, not edit permission')
    carrier['path'] = 'pkg/worker/options.go'
    example = candidate('example', f'func TestProbe() {{ issue("{operation}") }}',
                        symbol='TestProbe', declared_symbols=['TestProbe'], truncated=False)
    example['path'] = 'pkg/worker/probe_test.go'
    initial = build_candidate_set('configured timeout', None, [owner, carrier], [],
                                  top_n=2, max_context_tokens=10000, token_counter=len)
    extensions = [{**unit, 'evidence_gap_refs': ['G1'], 'scope_extension': 'EVIDENCE_GAP_DIRECTORY_PATH'}
                  for unit in (owner, example)]
    result = extend_once(initial, [{'id': 'G1', 'locator_hint': {'path': 'pkg/worker', 'search_term': operation}}],
                         extensions, max_context_tokens=10000, token_counter=len)
    assert set(result.selected_ids) == {'owner', 'carrier'}
    assert result.required_endpoint_ids == ('owner',)


@pytest.mark.parametrize('resource_kind', ['Worker', 'Task'])
def test_declared_input_fragments_do_not_erase_distinct_resource_siblings(resource_kind):
    requirement = (f'When create and patch {resource_kind}s are enabled, global routeSelector, '
                   f'tolerations and affinity must appear in both {resource_kind} pod specs.')
    resources = []
    for index, name in enumerate(('create', 'patch')):
        unit = candidate(name, f'kind: {resource_kind}\nmetadata:\n  name: {name}\nspec:\n  pod: true\n',
                         anchor_priority=2)
        unit.update(path='deployment/resources.yaml', kind='SOURCE_TEXT', resource_kind=resource_kind,
                    locator={'start_line': index * 10 + 1, 'end_line': index * 10 + 5})
        resources.append(unit)
    inputs = []
    for index in range(8):
        unit = candidate('input' + str(index), 'routeSelector tolerations affinity',
                         anchor_priority=1, origin_kind='DECLARED_ARTIFACT')
        unit.update(path=f'inputs/values{index}.yaml', kind='DECLARED_INPUT')
        inputs.append(unit)
    result = build_candidate_set(requirement, None, resources + inputs, [],
                                 top_n=4, max_context_tokens=12000, token_counter=len)
    assert {'create', 'patch'} <= set(result.selected_ids)
    assert any(unit.get('origin_kind') == 'DECLARED_ARTIFACT' for unit in result.candidates)
    assert result.selected_count == 4 and result.context_tokens <= 12000


@pytest.mark.parametrize('anchor_priority', [0, 20])
def test_declared_input_remains_selectable_for_exact_input_query_and_high_priority(anchor_priority):
    declared = candidate('declared', 'routeSelector: selected\n', origin_kind='DECLARED_ARTIFACT',
                         anchor_priority=anchor_priority)
    declared.update(path='inputs/settings.yaml', kind='DECLARED_INPUT')
    source = candidate('source', 'func prepare() {}')
    result = build_candidate_set('Inspect the declared routeSelector input.', None, [source, declared], [],
                                 top_n=1, max_context_tokens=1000, token_counter=len)
    assert result.selected_ids == ('declared',)
