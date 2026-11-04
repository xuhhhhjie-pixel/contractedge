"""Gap endpoints stay minimal without losing bounded locator semantics."""
import hashlib

from contractedge.retrieval.candidates import build_candidate_set, extend_once


def unit(identifier, text, *, path='worker.go', line=1, symbol=None, **extra):
    return dict(id=identifier, path=path, text=text, snapshot_id='S0',
                content_digest=hashlib.sha256(text.encode()).hexdigest(),
                locator={'start_line': line, 'end_line': line},
                kind='FUNCTION_OR_TYPE' if symbol else 'SOURCE_TEXT',
                symbol=symbol, declared_symbols=[symbol] if symbol else [],
                evidence_gap_refs=['G1'], **extra)


def select(gap, units):
    initial = build_candidate_set('worker', None, [], [], top_n=1,
                                  max_context_tokens=10000, token_counter=len)
    return extend_once(initial, [dict(id='G1', locator_hint=gap)], units,
                       max_context_tokens=10000, token_counter=len)


def test_path_only_gap_pins_one_qualified_representative_without_outside_path():
    result = select({'path': 'worker.go'}, [
        unit('header', '// worker implementation'),
        unit('body', 'func Work() {}', symbol='Work', line=10),
        unit('other', 'func Earlier() {}', symbol='Earlier', path='other.go'),
    ])
    assert result.required_endpoint_ids == ('body',)
    assert result.selected_ids == ('body',)


def test_multitoken_gap_reuses_bounded_case_exact_identifier_projection():
    result = select({'path': 'worker.go',
                     'search_term': 'NextPrivateKeySecretName comparison'}, [
        unit('wrong', 'func Wrong() { use(nextPrivateKeySecretName) }', symbol='Wrong'),
        unit('substring', 'func Other() { use(PreviousNextPrivateKeySecretName) }', symbol='Other', line=2),
        unit('body', 'func Work() { use(NextPrivateKeySecretName) }', symbol='Work', line=10),
    ])
    assert result.required_endpoint_ids == ('body',)
    assert result.selected_ids == ('body',)


def test_search_query_prefers_complete_declaration_over_earlier_comment():
    result = select({'path': 'worker.go', 'search_term': 'http.MethodOptions'}, [
        unit('header', '// The next function calls http.MethodOptions'),
        unit('body', 'func Work() { call(http.MethodOptions) }', symbol='Work', line=10),
    ])
    assert result.required_endpoint_ids == ('body',)
    assert result.selected_ids == ('body',)


def test_explicit_anchor_prevents_second_lexical_endpoint_for_same_gap():
    result = select({'path': 'worker.go', 'search_term': 'http.MethodOptions'}, [
        unit('anchor', '// verified bounded locator', gap_role_keys=['G1:anchor'], line=20),
        unit('lexical', 'func Work() { call(http.MethodOptions) }', symbol='Work', line=10),
    ])
    assert result.required_endpoint_ids == ('anchor',)
    assert result.selected_ids == ('anchor',)


def test_directory_query_prefers_implementation_to_earlier_test_match():
    result = select({'path': 'pkg/worker', 'search_term': 'http.MethodOptions'}, [
        unit('test', 'func TestProbe() { call(http.MethodOptions) }',
             path='pkg/worker/send_test.go', symbol='TestProbe', line=5),
        unit('body', 'func Probe() { call(http.MethodOptions) }',
             path='pkg/worker/send.go', symbol='Probe', line=400),
    ])
    assert result.required_endpoint_ids == ('body',)
    assert result.selected_ids == ('body',)


def test_explicit_test_locator_and_test_only_evidence_remain_available():
    matches = [
        unit('test', 'func TestProbe() { call(http.MethodOptions) }',
             path='pkg/worker/send_test.go', symbol='TestProbe'),
        unit('body', 'func Probe() { call(http.MethodOptions) }',
             path='pkg/worker/send.go', symbol='Probe'),
    ]
    targeted = select({'path': 'pkg/worker/send_test.go', 'search_term': 'http.MethodOptions'}, matches)
    assert targeted.required_endpoint_ids == ('test',)
    only_test = select({'path': 'pkg/worker', 'search_term': 'http.MethodOptions'}, matches[:1])
    assert only_test.required_endpoint_ids == ('test',)


def test_query_named_file_is_not_a_source_body_match():
    result = select({'path': 'pkg/worker', 'search_term': 'ProbeOptions'}, [
        unit('filename', 'func Unrelated() {}', path='pkg/worker/ProbeOptions.go',
             symbol='Unrelated'),
        unit('body', 'func Work() { ProbeOptions() }', path='pkg/worker/worker.go',
             symbol='Work', line=80),
    ])
    assert result.required_endpoint_ids == ('body',)
