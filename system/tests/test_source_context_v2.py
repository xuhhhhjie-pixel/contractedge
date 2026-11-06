"""Source context v2: one adoption record without independent count/byte gates.

Front-half redesign batch 2 (spec sections 5, 7.1, 7.5): identity, digest and
no-eviction rules are kept; per-producer quotas and repeated downstream source
quotas are gone; bodies are re-read byte-for-byte; versions never mix.
"""
import copy
import hashlib

import pytest

from contractedge.diagnosis.evidence import (
    source_context_contract, source_context_continuation, source_context_new, source_context_offer,
    source_context_records, source_context_restart, source_context_validate, source_record_from_read,
    verify_source_record)
from contractedge.execution.helper_context import planning_helper_source_support
from contractedge.model.budget import BudgetExceeded
from contractedge.source.navigation import SourceNavigator
from contractedge.source.parsers import parse_source_units
from contractedge.source.snapshot import compute_snapshot

BINDING = dict(run_id='run', case_id='case', input_digest='input', front_id='front')


def span(index, size=100, snapshot='snap'):
    excerpt = f'// unit {index}\n' + 'x' * size + '\n'
    return dict(id=f'SRC-{index}', kind='SOURCE_SPAN', snapshot_id=snapshot,
                locator=dict(path=f'f{index}.go', start_line=1, end_line=2),
                content_digest=hashlib.sha256(excerpt.encode()).hexdigest(), excerpt=excerpt)


def test_v1_producer_quotas_remain_for_old_records_and_v2_has_none():
    many = [span(i, 3000) for i in range(30)]          # 30 records, ~90 KB
    with pytest.raises(BudgetExceeded):
        source_context_offer(source_context_new(BINDING), many[:13], producer='initial')
    with pytest.raises(BudgetExceeded):
        source_context_offer(source_context_new(BINDING), many[:13], producer='extension')
    v2 = source_context_new(BINDING, version=2)
    for producer in ('initial', 'extension', 'planning_helper', 'execution_helper', 'locator'):
        adopted = source_context_offer(v2, many, producer=producer)
        assert len(source_context_records(adopted)) == 30
    assert 'limits' not in v2 and v2['contract'] == 'front_half.source_context.v2'


def test_v2_keeps_identity_digest_and_no_eviction_rules():
    v2 = source_context_offer(source_context_new(BINDING, version=2), [span(1), span(2)], producer='locator')
    assert source_context_validate(v2) == v2
    tampered = copy.deepcopy(span(3)); tampered['excerpt'] += 'changed'
    with pytest.raises(ValueError, match='digest'):
        source_context_offer(v2, [tampered], producer='locator')
    shrunk = copy.deepcopy(v2)
    shrunk['records'].pop(); shrunk['producer_refs']['locator'].pop()
    with pytest.raises(ValueError, match='removed'):
        source_context_continuation(v2, shrunk)
    legacy = {**copy.deepcopy(v2), 'limits': {'evidence_extensions': 2}}
    with pytest.raises(ValueError, match='legacy quota'):
        source_context_validate(legacy)
    with pytest.raises(ValueError, match='Unknown source producer'):
        source_context_offer(source_context_new(BINDING), [span(1)], producer='locator')


def test_contract_versions_never_mix_on_continuation_or_restart():
    v1 = source_context_offer(source_context_new(BINDING), [span(1)], producer='initial')
    v2 = source_context_offer(source_context_new(BINDING, version=2), [span(1)], producer='initial')
    with pytest.raises(ValueError, match='contract'):
        source_context_continuation(v1, v2)
    with pytest.raises(ValueError, match='another contract version'):
        source_context_restart(v1, BINDING, {'evidence': ['SRC-1']}, version=2)
    restarted = source_context_restart(v2, BINDING, {'evidence': [{'id': 'SRC-1', 'snapshot_id': 'snap'}]},
                                       version=2)
    assert restarted['version'] == 2 and [r['id'] for r in source_context_records(restarted)] == ['SRC-1']


def test_v2_config_contract_rejects_superseded_material_gates():
    assert source_context_contract({}) == 1
    assert source_context_contract({'source_context_contract': 2, 'max_context_tokens': 1}) == 2
    for key in ('initial_diagnosis_context_tokens', 'max_execution_support_bytes', 'top_n', 'max_evidence_extensions'):
        with pytest.raises(ValueError, match=key):
            source_context_contract({'source_context_contract': 2, key: 1})
    with pytest.raises(ValueError):
        source_context_contract({'source_context_contract': 3})


# --- navigation reads become canonical records that are re-read byte-for-byte

def navigation(tmp_path, files):
    root = tmp_path / 'repo'
    for name, body in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(body.encode())
    snapshot = compute_snapshot(root)
    return SourceNavigator(snapshot), snapshot, root


def test_navigation_read_record_is_adopted_and_verified_exactly(tmp_path):
    body = 'package p\r\n// 注释\r\nfunc Main() {\r\n' + '\tx := 1\r\n' * 1500 + '}\r\n'
    nav, snapshot, _ = navigation(tmp_path, {'cmd/main.go': body})
    read = nav.read(path='cmd/main.go', symbol='Main')
    record = source_record_from_read(read, current_snapshot=snapshot.snapshot_id)
    assert record['excerpt'] == read['text'] and record['locator']['truncated'] is False
    adopted = source_context_offer(source_context_new(BINDING, version=2), [record], producer='locator')
    assert source_context_records(adopted)[0]['excerpt'].endswith('}')
    assert verify_source_record(record, snapshot)['verified'] == 'EXACT_BYTE_RANGE'


def test_cut_body_with_recomputed_digest_is_detected(tmp_path):
    nav, snapshot, root = navigation(tmp_path, {'a.go': 'package a\nfunc A() {\n' + '\tx()\n' * 400 + '}\n'})
    record = source_record_from_read(nav.read(path='a.go', symbol='A'), current_snapshot=snapshot.snapshot_id)
    cut = copy.deepcopy(record)
    cut['excerpt'] = cut['excerpt'][:200]
    cut['content_digest'] = hashlib.sha256(cut['excerpt'].encode()).hexdigest()
    with pytest.raises(ValueError, match='byte coordinates'):
        source_context_offer(source_context_new(BINDING, version=2), [cut], producer='locator')
    with pytest.raises(ValueError, match='bytes differ'):
        verify_source_record(cut, snapshot)
    (root / 'a.go').write_text('package a\nfunc A() {}\n')
    with pytest.raises(ValueError, match='changed'):
        verify_source_record(record, snapshot)


def test_dependency_read_record_stays_in_current_snapshot_namespace(tmp_path):
    from tests.test_source_navigation import dependency_navigator
    nav, update = dependency_navigator(tmp_path, None)
    listing = nav.follow_dependency(update, selector='wire')
    read = nav.read(next(h for h in listing['hits'] if h['symbol'] == 'Pull')['handle'])
    record = source_record_from_read(read, current_snapshot=nav.snapshot.snapshot_id)
    assert record['kind'] == 'PUBLIC_DEPENDENCY_DECLARATION' and record['read_only']
    adopted = source_context_offer(source_context_new(BINDING, version=2), [record], producer='locator')
    assert source_context_records(adopted, kind='PUBLIC_DEPENDENCY_DECLARATION')[0]['version'] == 'v1.2.3'


# --- downstream: planning helpers are no longer capped at 8 records / 24 KB

def helper_fixture(tmp_path, count):
    (tmp_path / 'go.mod').write_bytes(b'module demo\n')
    (tmp_path / 'app').mkdir(); (tmp_path / 'z').mkdir()
    (tmp_path / 'z/z.go').write_bytes(('package z\n' + ''.join(
        f'func F{i}() {{\n' + ('\t_ = "' + 'x' * 24 + '"\n') * 200 + '}\n' for i in range(1, count + 1))).encode())
    base = 'package app\nimport "demo/z"\nfunc Base() { ' + '; '.join(f'z.F{i}()' for i in range(1, count + 1)) + ' }\n'
    (tmp_path / 'app/base.go').write_bytes(base.encode())
    snap = compute_snapshot(tmp_path)
    seeds = [u for u in parse_source_units('app/base.go', base, snap.snapshot_id) if u.get('symbol') == 'Base']
    return snap, seeds


def test_planning_helpers_beyond_eight_and_24k_are_kept_under_v2(tmp_path):
    snap, seeds = helper_fixture(tmp_path, 10)
    v1, audit = planning_helper_source_support(snap, seeds, source_context=source_context_new(BINDING))
    # Historical contract unchanged: 200-line helpers exceed its 80-line window.
    assert v1 == [] and audit
    v2_context = source_context_new(BINDING, version=2)
    v2, _ = planning_helper_source_support(snap, seeds, max_bytes=None, source_context=v2_context)
    assert len(v2) == 10 and sum(len(r['excerpt'].encode()) for r in v2) > 24000
    adopted = source_context_offer(v2_context, v2, producer='planning_helper')
    again, _ = planning_helper_source_support(snap, seeds, max_bytes=None, source_context=adopted)
    assert again == v2                                      # rediscovery is not re-adoption


def test_execution_support_bytes_are_not_rechecked_for_v2_adopted_bodies(tmp_path):
    import json
    from contractedge.execution.topomen import build_execution_request
    from tests.test_source_context_execution import execution_helpers
    from tests.test_execution_adopted_material_delivery import REQUIREMENT
    plan, config, active, original = execution_helpers(tmp_path)
    used = sum(len(r['excerpt'].encode())
               for r in json.loads(original.messages[1]['content'])['read_only_source_support'])
    tight = {**config, 'max_execution_support_bytes': used // 2}

    def supplied(source_context):
        request = build_execution_request(plan, tmp_path, {'allowed_paths': ['app/**']}, len, tight,
                                          requirement=REQUIREMENT, source_context=source_context)
        return json.loads(request.messages[1]['content'])['read_only_source_support']
    v1 = supplied(active)                                   # v1 silently stops at its byte quota
    v2 = supplied(source_context_new(dict(active['binding']), version=2))
    assert sum(len(r['excerpt'].encode()) for r in v1) <= used // 2
    assert sum(len(r['excerpt'].encode()) for r in v2) >= used and len(v2) > len(v1)


# --- runner: one request boundary; over-window requests are refused, not cut

def _v2_config(**overrides):
    from tests.test_bounded_retrieval_continuation import CONFIG
    from contractedge.diagnosis.evidence import SUPERSEDED_BY_SOURCE_CONTEXT_V2
    base = {k: v for k, v in CONFIG.items() if k not in SUPERSEDED_BY_SOURCE_CONTEXT_V2}
    return {**base, 'source_context_contract': 2, **overrides}


def test_v2_runner_uses_one_request_boundary(tmp_path):
    from contractedge.orchestration.runner import run_front_half
    from tests.test_bounded_retrieval_continuation import CONFIG, SequentialGaps
    from tests.test_front_half_locator_runner import V2Provider
    from tests.test_planning_evidence_extension import front_fixture
    method, template = front_fixture(tmp_path)
    small_first = run_front_half(method, tmp_path, SequentialGaps(template),
                                 {**CONFIG, 'initial_diagnosis_context_tokens': 50}, lambda t: len(t.split()))
    assert small_first.status == 'BUDGET_EXHAUSTED'          # v1 smaller first allowance trims everything
    trace = run_front_half(method, tmp_path, V2Provider(template), _v2_config(), lambda t: len(t.split()))
    assert trace.status == 'READY', trace.errors
    assert trace.run_context['retrieval_config']['source_context']['version'] == 2


def test_v2_over_window_diagnosis_is_refused_unsent_without_trimming(tmp_path):
    from contractedge.orchestration.runner import run_front_half
    from tests.test_front_half_locator_runner import V2Provider
    from tests.test_planning_evidence_extension import front_fixture
    method, template = front_fixture(tmp_path)
    provider = V2Provider(template)
    # Large enough for the locator turns, too small for the complete diagnosis request.
    trace = run_front_half(method, tmp_path, provider, _v2_config(max_context_tokens=1200, max_output_tokens=256),
                           lambda t: len(t.split()))
    assert trace.status == 'BUDGET_EXHAUSTED', trace.errors
    assert [r.prompt_version for r in provider.requests] == ['locator.v1', 'locator.v1']
    failure = [a['admission_failure'] for a in trace.candidate_set.context_audit if 'admission_failure' in a][-1]
    assert failure['sent'] is False and failure['limit'] == 1200 and failure['actual'] > 1200
    assert len(trace.candidate_set.selected_ids) == 1          # nothing was cut to make it fit


def test_v2_config_with_superseded_gate_fails_before_any_call(tmp_path):
    from contractedge.orchestration.runner import run_front_half
    from tests.test_bounded_retrieval_continuation import SequentialGaps
    from tests.test_planning_evidence_extension import front_fixture
    method, template = front_fixture(tmp_path)
    provider = SequentialGaps(template)
    with pytest.raises(ValueError, match='superseded'):
        run_front_half(method, tmp_path, provider, _v2_config(initial_diagnosis_context_tokens=120000),
                       lambda t: len(t.split()))
    assert provider.requests == []


# --- the fixed 12-item precondition review is lifted only for v2

def test_thirteen_real_prerequisites_are_reviewed_under_v2_only(monkeypatch):
    from contractedge.orchestration.runner import required_source_prerequisites
    from contractedge.retrieval.candidates import CandidateSet
    units = tuple(dict(id=f'U{i}', path=f'f{i}.go', text='func F() {}', precondition_review_required=True,
                       gap_role_keys=[]) for i in range(13))
    candidates = CandidateSet(candidates=units, selected_ids=tuple(u['id'] for u in units), dropped_ids=(),
                              ranking_features={}, edges=(), context_tokens=0, max_context_tokens=1, top_n=13)
    with pytest.raises(BudgetExceeded):
        required_source_prerequisites(candidates)
    rows = required_source_prerequisites(candidates, limit=None)
    assert len(rows) == 13 and len({r['source_evidence_ref'] for r in rows}) == 13

    from contractedge.contracts import validation
    seen = []
    real = validation.Draft202012Validator
    monkeypatch.setattr(validation, 'Draft202012Validator', lambda schema: seen.append(schema) or real(schema))
    for unbounded in ((), ('precondition_review',)):
        try:
            validation._check_schema({}, 'ce.semantic_changeset.v1_1', unbounded=unbounded)
        except validation.ContractError:
            pass
    v1_rule, v2_rule = (s['properties']['precondition_review'] for s in seen)
    assert v1_rule['maxItems'] == 12 and 'maxItems' not in v2_rule
    assert v1_rule['items'] == v2_rule['items']                 # every per-review rule is kept
