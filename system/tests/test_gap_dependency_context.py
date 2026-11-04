"""Diagnosis can read a pinned imported API without inventing compiler failures."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from contractedge.source.dependency import gap_dependency_support, PublicGoDependencies
from contractedge.source.snapshot import compute_snapshot
from tests.test_compiler_dependency_context import fixture, replace_archive


API = '''package check
type Client struct { BareTransport bool }
type Choice func(*Client)
func NewClient(options ...Choice) *Client {
    c := &Client{}
    for _, apply := range options { apply(c) }
    return c
}
func WithoutTLS() Choice {
    return func(c *Client) { c.BareTransport = true }
}
func WithIgnoredSetting() Choice { return func(c *Client) {} }
'''
LOCAL = '''package service
import wire "example.org/toolbox/pkg/check"
func update() { _ = wire.NewClient() }
'''


def setup(tmp_path, *, body=API, source_text=LOCAL):
    snapshot, feedback, inputs, source = fixture(tmp_path, body=body)
    (source / 'main.go').write_text(source_text)
    snapshot = compute_snapshot(source)
    units = [dict(path='main.go', text=source_text, snapshot_id=snapshot.snapshot_id,
        content_digest=hashlib.sha256(source_text.encode()).hexdigest(),
        locator=dict(start_char=0, end_char=len(source_text), start_line=1,
            end_line=len(source_text.splitlines()), truncated=False))]
    gaps = [dict(id='G1', summary='Need the wire.NewClient API and option for BareTransport',
        locator_hint=dict(path='main.go', symbol='NewClient', search_term='BareTransport'))]
    return snapshot, feedback, inputs, source, units, gaps


def lookup(state, **kwargs):
    snapshot, feedback, _, _, units, gaps = state
    return gap_dependency_support(snapshot, gaps, units, case_id='CASE',
        public_execution_capabilities=feedback['public_execution_capabilities'], **kwargs)


def test_complete_function_type_and_option_are_read_before_any_compiler_failure(tmp_path):
    state = setup(tmp_path, body='//' + 'padding' * 4000 + '\n' + API)
    records, audit = lookup(state)
    assert len(records) == 3
    text = '\n'.join(r['excerpt'] for r in records)
    assert 'func NewClient(options ...Choice)' in text
    assert 'type Choice func(*Client)' in text
    assert 'func WithoutTLS() Choice' in text
    assert 'WithIgnoredSetting' not in text and 'padding' not in text
    assert sum(len(r['excerpt'].encode()) for r in records) < 1000
    assert len({r['id'] for r in records}) == 3
    member_text = '//' + 'padding' * 4000 + '\n' + API
    for row in records:
        assert row['kind'] == 'PUBLIC_DEPENDENCY_DECLARATION' and row['read_only'] is True
        assert row['member_content_digest'] == hashlib.sha256(member_text.encode()).hexdigest()
        assert row['content_digest'] == hashlib.sha256(row['excerpt'].encode()).hexdigest()
        assert member_text[row['locator']['start_char']:row['locator']['end_char']] == row['excerpt']
        assert row['locator']['truncated'] is False
        assert row['current_source_snapshot'] == state[0].snapshot_id
    scans = [r for r in audit if r['result'] == 'PUBLIC_DEPENDENCY_PACKAGE_SCAN']
    assert scans and scans[0]['inspected_source_bytes'] > 24000 and scans[0]['complete'] is True


def test_real_diagnosis_and_planning_requests_keep_api_context_outside_causal_catalog(tmp_path):
    from contractedge.diagnosis.evidence import source_context_new, source_context_offer
    from contractedge.diagnosis.prompt import build_diagnosis_request
    from contractedge.changeset.prompt import build_changeset_request
    from contractedge.contracts.validation import parse_method_input, validate_diagnosis
    from contractedge.retrieval.candidates import build_candidate_set
    from tests.test_contracts import valid_method_input, valid_diagnosis
    state = setup(tmp_path)
    rows, _ = lookup(state)
    assert rows
    method = parse_method_input(valid_method_input())
    adopted = source_context_offer(source_context_new(dict(run_id='run', case_id=method.case_id,
        input_digest=method.input_digest, front_id='front')), rows, producer='extension')
    context = SimpleNamespace(run_id='run', case_id=method.case_id, snapshot_id=state[0].snapshot_id,
        input_digest=method.input_digest, model_config={'model':'fixture'}, budget={'max_output_tokens':1024},
        retrieval_config={'source_context':adopted, 'necessary_source_support':[]})
    candidates = build_candidate_set('', None, [], [], top_n=12, max_context_tokens=10000, token_counter=len)
    request = build_diagnosis_request(method, valid_diagnosis()['requirement_spec'], candidates, context)
    body = json.loads(request.messages[1]['content'])
    assert all(row in body['adopted_source_context']['records'] for row in rows)
    assert not set(r['id'] for r in rows) & set(body['allowed_evidence_ids'])
    plan = build_changeset_request(validate_diagnosis(valid_diagnosis(), method, None), context)
    assert all(row in json.loads(plan.messages[1]['content'])['adopted_source_context']['records'] for row in rows)


@pytest.mark.parametrize('kind', ['comment', 'string', 'shadow', 'multi-shadow', 'grouped-var',
    'parameter', 'named-return', 'field-selector', 'spaced-field-selector',
    'wrong-alias', 'wrong-file', 'wrong-snapshot', 'wrong-digest', 'unselected', 'source-changed'])
def test_gap_prose_cannot_invent_a_current_imported_call(tmp_path, kind):
    text = LOCAL
    if kind == 'comment': text = text.replace('_ = wire.NewClient()', '// wire.NewClient()\n')
    if kind == 'string': text = text.replace('_ = wire.NewClient()', '_ = "wire.NewClient()"')
    if kind == 'shadow': text = text.replace('_ = wire.NewClient()', 'wire := getLocal(); _ = wire.NewClient()')
    if kind == 'multi-shadow': text = text.replace('_ = wire.NewClient()', 'wire, err := getLocal(); _ = wire.NewClient(); _ = err')
    if kind == 'grouped-var': text = text.replace('func update()', 'var (\n wire SomeType\n)\nfunc update()')
    if kind == 'parameter': text = text.replace('func update()', 'func update(wire, other SomeType)')
    if kind == 'named-return': text = text.replace('func update()', 'func update() (wire, other SomeType)')
    if kind == 'field-selector': text = text.replace('wire.NewClient()', 'x.wire.NewClient()')
    if kind == 'spaced-field-selector': text = text.replace('wire.NewClient()', 'x . wire.NewClient()')
    if kind == 'wrong-alias': text = text.replace('wire "', 'other "').replace('wire.NewClient()', 'other.NewClient()')
    state = setup(tmp_path, source_text=text)
    if kind == 'wrong-file': state[5][0]['locator_hint']['path'] = 'other.go'
    if kind == 'wrong-snapshot': state[4][0]['snapshot_id'] = 'stale'
    if kind == 'wrong-digest': state[4][0]['content_digest'] = '0' * 64
    if kind == 'unselected': state[4].clear()
    if kind == 'source-changed': (state[3] / 'main.go').write_text(LOCAL + '// changed')
    assert lookup(state)[0] == []


def test_pure_local_gap_does_not_open_public_dependency_authority(tmp_path, monkeypatch):
    state = setup(tmp_path)
    state[5][0]['summary'] = 'Find the local implementation of NewClient'
    monkeypatch.setattr(PublicGoDependencies, '__init__', lambda *a, **k: pytest.fail('Unbound query opened dependency reader'))
    assert lookup(state) == ([], [])


def test_explicit_qualified_search_term_is_a_query_not_ignored_prose(tmp_path):
    state = setup(tmp_path)
    state[5][0]['summary'] = 'Need the exact option API'
    state[5][0]['locator_hint']['search_term'] = 'wire.NewClient BareTransport'
    assert any('func NewClient(' in row['excerpt'] for row in lookup(state)[0])


def test_existing_imported_option_type_can_select_its_matching_factory(tmp_path):
    state = setup(tmp_path, source_text=LOCAL.replace('wire.NewClient()', '[]wire.Choice{}'))
    state[5][0]['summary'] = 'Need the wire.Choice API for BareTransport'
    state[5][0]['locator_hint']['symbol'] = 'Choice'
    rows, _ = lookup(state)
    assert len(rows) == 2
    assert any('type Choice func(*Client)' in row['excerpt'] for row in rows)
    assert any('func WithoutTLS() Choice' in row['excerpt'] for row in rows)


@pytest.mark.parametrize('kind', ['archive-hash', 'environment-hash', 'replace', 'exclude', 'workspace', 'vendor', 'version', 'oversized', 'build-tag', 'platform', 'duplicate', 'duplicate-type', 'test-only'])
def test_dependency_uncertainty_remains_unavailable_at_diagnosis(tmp_path, kind):
    state = setup(tmp_path)
    snapshot, feedback, inputs, source, units, gaps = state
    prefix = 'gomod/example.org/toolbox@v1.2.3/pkg/check/'
    if kind == 'archive-hash': (inputs / 'dependencies.tar.gz').write_bytes(b'changed')
    elif kind == 'environment-hash': feedback['public_execution_capabilities']['source']['sha256'] = '0' * 64
    elif kind in {'replace', 'exclude'}:
        with (source / 'go.mod').open('a') as f: f.write(kind + ' example.org/toolbox ' + (' => ../other' if kind == 'replace' else ' v1.2.3') + '\n')
    elif kind == 'workspace': (source.parent / 'go.work').write_text('use ./repo\n')
    elif kind == 'vendor':
        (source / 'vendor').mkdir(); (source / 'vendor/modules.txt').write_text('# toolbox')
    elif kind == 'version': (source / 'go.mod').write_text('module example.org/service\nrequire example.org/toolbox v2.0.0\n')
    elif kind == 'oversized': replace_archive(inputs, feedback, [(prefix + 'api.go', API), (prefix + 'other.go', '//'+ 'x'*66000)])
    elif kind == 'build-tag': replace_archive(inputs, feedback, [(prefix + 'api.go', API), (prefix + 'other.go', '//go:build linux\npackage check')])
    elif kind == 'platform': replace_archive(inputs, feedback, [(prefix + 'api_linux.go', API)])
    elif kind == 'duplicate': replace_archive(inputs, feedback, [(prefix + 'api.go', API), (prefix + 'api.go', API)])
    elif kind == 'duplicate-type': replace_archive(inputs, feedback, [(prefix + 'api.go', API), (prefix + 'other.go', API)])
    elif kind == 'test-only': replace_archive(inputs, feedback, [(prefix + 'api_test.go', API)])
    snapshot = compute_snapshot(source)
    units[0]['snapshot_id'] = snapshot.snapshot_id
    assert lookup((snapshot, feedback, inputs, source, units, gaps))[0] == []


def test_delivery_limits_never_crop_a_declaration_or_reset_adopted_record_quota(tmp_path):
    state = setup(tmp_path)
    assert lookup(state, max_bytes=8)[0] == []
    rows, _ = lookup(state, max_records=1)
    assert len(rows) == 1 and rows[0]['excerpt'].endswith('}')
    assert lookup(state, max_records=0)[0] == []
    all_rows, _ = lookup(state)
    none, audit = lookup(state, already_supplied=lambda row: row in all_rows)
    assert none == []
    assert len([a for a in audit if a['result'] == 'ALREADY_DELIVERED_DEPENDENCY_API']) == 3


def test_grouped_function_type_retains_complete_group_and_factory(tmp_path):
    group = 'type (\n Choice func(*Client)\n Unused struct {\n FakeName int\n }\n)'
    state = setup(tmp_path, body=API.replace('type Choice func(*Client)', group))
    rows, audit = lookup(state)
    assert any(row['excerpt'] == group for row in rows)
    assert any('func WithoutTLS()' in row['excerpt'] for row in rows)
    assert not any('FakeName' in row.get('declaration_names', []) for row in audit)


def test_comment_or_string_factory_and_wrong_return_type_are_not_selected(tmp_path):
    body = API + '''
// func Fake() Choice { BareTransport }
var note = `func FakeTwo() Choice { BareTransport }`
func Other() int { /* BareTransport */ return 1 }
func WrongReturn() int { BareTransport := 1; return BareTransport }
'''
    rows, _ = lookup(setup(tmp_path, body=body))
    assert len(rows) == 3
    assert not any('Fake' in row['excerpt'] or 'WrongReturn' in row['excerpt'] for row in rows)
