"""Bounded shared acquisition, not extra model retries or a new budget owner."""
import copy
import hashlib
import json

import pytest

from contractedge.contracts.evidence import static_search_content_digest, supports_expected_missing_evidence
from contractedge.diagnosis.evidence import source_context_new, source_context_offer, source_context_continuation
from contractedge.model.budget import BudgetExceeded
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.runner import run_front_half
from tests.test_orchestration import PipelineFixtureProvider
from tests.test_planning_evidence_extension import front_fixture


CONFIG = dict(model={'model': 'fixture'}, max_context_tokens=220000,
    initial_diagnosis_context_tokens=120000, model_context_tokens=262144,
    max_output_tokens=16384, max_total_tokens=1200000, max_model_calls=12,
    max_execution_support_bytes=100000, max_elapsed_seconds=2400,
    max_evidence_extensions=2, diagnosis_format_retry_per_context=True)


class SequentialGaps(PipelineFixtureProvider):
    def __init__(self, template, *, repeat=False):
        super().__init__()
        self.requests = []
        self.diagnosis_count = 0
        self.repeat = repeat
        self._snapshot_digests = {'templates/serviceaccount.yaml': hashlib.sha256(template.read_bytes()).hexdigest()}

    def complete_json(self, request):
        self.requests.append(request)
        response = super().complete_json(request)
        if not request.prompt_version.startswith('diagnosis.'):
            return response
        self.diagnosis_count += 1
        body = json.loads(response.content)
        if self.diagnosis_count <= 2 or self.repeat:
            path = 'templates/optional-first.yaml' if self.repeat or self.diagnosis_count == 1 else 'templates/optional-second.yaml'
            body.update(status='EVIDENCE_GAP', evidence_gaps=[dict(id='G1', summary='Check the next exact optional template.', locator_hint={'path': path})])
        return ModelResponse(json.dumps(body), {'provider': 'sequential-gaps-fixture'})


def test_second_concrete_gap_uses_existing_second_acquisition(tmp_path):
    method, template = front_fixture(tmp_path)
    provider = SequentialGaps(template)
    trace = run_front_half(method, tmp_path, provider, CONFIG, lambda t: len(t.split()))
    assert trace.status == 'READY', trace.errors
    assert provider.diagnosis_count == 3
    assert len(provider.requests) == 4
    assert [r['round_id'] for r in trace.retrieval_rounds] == [1, 2, 3]
    final = trace.run_context['retrieval_config']['source_context']
    facts = [r for r in final['records'] if r['kind'] == 'STATIC_SEARCH']
    assert {r['locator']['query'] for r in facts} >= {'templates/optional-first.yaml', 'templates/optional-second.yaml'}
    # The fixture intentionally cites no search record. Planning must still see it.
    planning = json.loads(provider.requests[-1].messages[1]['content'])
    encoded = json.dumps(planning, sort_keys=True)
    assert all(r['excerpt'] in encoded and r['id'] in encoded for r in facts)


def test_repeat_without_actual_new_material_does_not_dispatch(tmp_path):
    method, template = front_fixture(tmp_path)
    provider = SequentialGaps(template, repeat=True)
    trace = run_front_half(method, tmp_path, provider, CONFIG, lambda t: len(t.split()))
    assert trace.status == 'EVIDENCE_GAP'
    assert provider.diagnosis_count == 2
    last = provider.requests[-1]
    assert trace.run_context['retrieval_config']['source_context'] == last.metadata['source_context']
    body = json.loads(last.messages[1]['content'])
    assert trace.requirement_spec == body['requirement_spec']
    assert list(trace.candidate_set.selected_ids) == body['candidate_set']['selected_ids']


def test_default_one_acquisition_stays_one(tmp_path):
    method, template = front_fixture(tmp_path)
    provider = SequentialGaps(template)
    config = {k: v for k, v in CONFIG.items() if k != 'max_evidence_extensions'}
    trace = run_front_half(method, tmp_path, provider, config, lambda t: len(t.split()))
    assert trace.status == 'EVIDENCE_GAP'
    assert provider.diagnosis_count == 2


def owner():
    return source_context_new(dict(run_id='r', case_id='c', input_digest='i', front_id='f'))


def source(index):
    text = f'func helper{index}() {{}}'
    return dict(id=f'SRC-{index}', kind='SOURCE_SPAN', snapshot_id='s',
        locator={'path': f'helpers/{index}.go', 'start_line': 1, 'end_line': 1},
        excerpt=text, content_digest=hashlib.sha256(text.encode()).hexdigest())


def search_record():
    locator = dict(scope='snapshot_file_content', query_kind='EXACT_TEXT_IN_FILE',
        query='MissingAPI', path='helpers/0.go', source_file_sha256='f' * 64,
        complete=True, truncated=False, match_count=0, matched_paths=[],
        inspected_file_count=1, inspected_byte_count=100)
    excerpt = 'The exact query was not found in this complete snapshot-bound file; no repository or external API absence is established.'
    digest = static_search_content_digest('s', locator, excerpt)
    return dict(id='SEARCH-' + digest[:16], kind='STATIC_SEARCH', snapshot_id='s',
        locator=locator, excerpt=excerpt, content_digest=digest)


def test_cumulative_source_capacity_uses_frozen_two_batch_profile():
    active = owner()
    active['limits'] = {'evidence_extensions': 2}
    first = source_context_offer(active, [source(i) for i in range(12)], producer='extension')
    second = source_context_offer(first, [source(i) for i in range(12, 24)], producer='extension')
    assert len(second['records']) == 24
    assert source_context_continuation(first, second) == second
    with pytest.raises(BudgetExceeded, match='admission limit'):
        source_context_offer(second, [source(24)], producer='extension')


def test_static_search_is_canonical_without_stealing_source_slots():
    active = source_context_offer(owner(), [source(i) for i in range(12)], producer='extension')
    fact = search_record()
    draft = source_context_offer(active, [fact], producer='extension')
    assert source_context_continuation(active, draft) == draft
    assert not supports_expected_missing_evidence(fact)
    corrupt = copy.deepcopy(fact)
    corrupt['locator']['scope'] = 'snapshot_file_inventory'
    with pytest.raises(ValueError):
        source_context_offer(active, [corrupt], producer='extension')


def test_continuation_cannot_increase_source_limits():
    active = owner()
    draft = copy.deepcopy(active)
    draft['limits'] = {'evidence_extensions': 2}
    with pytest.raises(ValueError, match='limit|policy'):
        source_context_continuation(active, draft)


@pytest.mark.parametrize('call_limit', [1, 2, 3])
def test_existing_call_limit_stops_continuation_without_reset(tmp_path, call_limit):
    method, template = front_fixture(tmp_path)
    provider = SequentialGaps(template)
    trace = run_front_half(method, tmp_path, provider, {**CONFIG, 'max_model_calls': call_limit}, lambda t: len(t.split()))
    assert trace.status == 'BUDGET_EXHAUSTED'
    assert len(provider.requests) == call_limit


def test_second_acquisition_cannot_reopen_used_extension_correction(tmp_path):
    method, template = front_fixture(tmp_path)

    class CorrectOnce(SequentialGaps):
        def complete_json(self, request):
            response = super().complete_json(request)
            if not request.prompt_version.startswith('diagnosis.'):
                return response
            body = json.loads(response.content)
            if self.diagnosis_count == 3:
                body.update(status='EVIDENCE_GAP', evidence_gaps=[dict(id='G1', summary='Next file.',
                    locator_hint={'path': 'templates/optional-second.yaml'})])
            if self.diagnosis_count in {2, 4}:
                body['violations'][0]['support_basis'] = 'INVALID'
            return ModelResponse(json.dumps(body), {})

    provider = CorrectOnce(template)
    trace = run_front_half(method, tmp_path, provider, CONFIG, lambda t: len(t.split()))
    assert trace.status == 'INVALID_OUTPUT'
    assert trace.structural_validation == 'FAIL'
    assert provider.diagnosis_count == 4
    assert sum(c['stage'] == 'DIAGNOSIS_FORMAT_RETRY' for c in trace.model_calls) == 1


def test_second_batch_selection_is_bounded_and_preserves_first_batch():
    from tests.test_extension_selection_context import textunit, record
    from contractedge.retrieval.candidates import build_candidate_set, extend_once

    initial = textunit('initial.txt', 'root')
    base = build_candidate_set('helper', None, [initial], [], top_n=12, max_context_tokens=100000, token_counter=len)
    active = owner(); active['limits'] = {'evidence_extensions': 2}
    active = source_context_offer(active, [record(initial)], producer='initial', candidate_ids=[initial['id']])
    gaps = [{'id': 'G', 'locator_hint': {'path': 'helper.go', 'search_term': 'helper'}}]
    first_units = [textunit(f'helpers/a{i}.txt', f'helper first {i}') for i in range(20)]
    first = extend_once(base, gaps, first_units, max_context_tokens=100000, token_counter=len, source_context=active)
    assert len(first.candidates) == 13
    active = source_context_offer(active, [record(u) for u in first.candidates], producer='extension', candidate_ids=first.selected_ids)
    second_units = [textunit(f'helpers/b{i}.txt', f'helper second {i}') for i in range(20)]
    second = extend_once(first, gaps, second_units, max_context_tokens=100000, token_counter=len,
        source_context=active, allow_reextension=True)
    assert len(second.candidates) == 25
    assert second.candidates[:13] == first.candidates


def test_single_batch_byte_limit_cannot_borrow_unused_future_batch():
    from tests.test_extension_selection_context import textunit, record
    from contractedge.retrieval.candidates import build_candidate_set, extend_once

    initial = textunit('initial.txt', 'root')
    base = build_candidate_set('helper', None, [initial], [], top_n=12, max_context_tokens=100000, token_counter=len)
    active = owner(); active['limits'] = {'evidence_extensions': 2}
    active = source_context_offer(active, [record(initial)], producer='initial', candidate_ids=[initial['id']])
    extra = [textunit(f'helpers/{i}.txt', 'helper ' + str(i) + 'é' * 4000) for i in range(6)]
    grown = extend_once(base, [{'id': 'G', 'locator_hint': {'path': 'helpers/0.txt'}}], extra,
        max_context_tokens=100000, token_counter=len, source_context=active)
    assert sum(len(u['text'].encode()) for u in grown.candidates[1:]) <= 24000
    assert len(grown.candidates) < 7


@pytest.mark.parametrize('failed_read', [False, True])
def test_scoped_no_match_uses_verified_complete_file_only(tmp_path, failed_read):
    from contractedge.source.snapshot import compute_snapshot
    from contractedge.retrieval.gap_delivery import locate_gap_roles

    path = tmp_path / 'local.go'
    path.write_text('package local\nfunc Exists() {}\n')
    snapshot = compute_snapshot(tmp_path)
    if failed_read:
        path.write_text('changed after snapshot')
    _, audit = locate_gap_roles(snapshot, [{'id': 'G1', 'locator_hint': {'path': 'local.go', 'search_term': 'AbsentAPI'}}], {})
    facts = audit['static_search_evidence']
    if failed_read:
        assert facts == []
    else:
        assert len(facts) == 1
        assert facts[0]['locator']['source_file_sha256'] == snapshot.file_digests['local.go']
        assert facts[0]['locator']['scope'] == 'snapshot_file_content'
        assert not supports_expected_missing_evidence(facts[0])


def test_unreferenced_search_fact_is_required_in_final_planning_bytes(tmp_path):
    from contractedge.orchestration.semantic_delivery import delivery_context, inspect_delivery, require_delivery, SemanticDeliveryError
    from contractedge.model.admission import quote_request
    from tests.test_execution_adopted_material_delivery import PreparedTransport

    method, template = front_fixture(tmp_path)
    provider = SequentialGaps(template)
    trace = run_front_half(method, tmp_path, provider, CONFIG, lambda t: len(t.split()))
    request = provider.requests[-1]
    assert trace.status == 'READY'
    frame = delivery_context(method, consumer='PLANNING', snapshot_id=trace.snapshot_id)
    require_delivery(inspect_delivery(request, quote_request(PreparedTransport(), request, len), frame))
    def drop(value):
        if isinstance(value, dict):
            if value.get('kind') == 'STATIC_SEARCH':
                value.pop('excerpt', None)
            for child in value.values(): drop(child)
        elif isinstance(value, list):
            for child in value: drop(child)
    with pytest.raises(SemanticDeliveryError):
        require_delivery(inspect_delivery(request, quote_request(PreparedTransport(drop), request, len), frame))


@pytest.mark.parametrize('mutation', ['remove', 'scope', 'query', 'snapshot', 'digest'])
def test_unreferenced_static_fact_survives_execution_and_final_byte_checks(tmp_path, mutation):
    from tests.test_source_context_execution import execution_helpers
    from tests.test_execution_adopted_material_delivery import REQUIREMENT, inspect
    from contractedge.execution.topomen import build_execution_request
    from contractedge.orchestration.semantic_delivery import require_delivery, SemanticDeliveryError

    plan, config, active, _ = execution_helpers(tmp_path)
    fact = search_record()
    active = source_context_offer(active, [fact], producer='extension')
    request = build_execution_request(plan, tmp_path, {'allowed_paths': ['app/**']}, len, config,
        requirement=REQUIREMENT, source_context=active)
    require_delivery(inspect(request, plan, tmp_path))
    def mutate(body):
        records = body['adopted_source_context']['records']
        item = next(r for r in records if r['kind'] == 'STATIC_SEARCH')
        if mutation == 'remove': records.remove(item)
        elif mutation in {'scope', 'query'}: item['locator'][mutation] = 'changed'
        elif mutation == 'snapshot': item['snapshot_id'] = 'changed'
        else: item['content_digest'] = '0' * 64
    with pytest.raises(SemanticDeliveryError):
        require_delivery(inspect(request, plan, tmp_path, mutate))


def test_static_fact_does_not_spend_execution_source_support_bytes(tmp_path):
    from tests.test_source_context_execution import execution_helpers
    from tests.test_execution_adopted_material_delivery import REQUIREMENT
    from contractedge.execution.topomen import build_execution_request

    plan, config, active, original = execution_helpers(tmp_path)
    original_body = json.loads(original.messages[1]['content'])
    used = sum(len(r['excerpt'].encode()) for r in original_body['read_only_source_support'])
    fact = search_record()
    active = source_context_offer(active, [fact], producer='extension')
    request = build_execution_request(plan, tmp_path, {'allowed_paths': ['app/**']}, len,
        {**config, 'max_execution_support_bytes': used}, requirement=REQUIREMENT, source_context=active)
    assert any(r['kind'] == 'STATIC_SEARCH' for r in request.metadata['source_context']['records'])


@pytest.mark.parametrize('producer', ['planning_helper', 'execution_helper', 'reread'])
def test_static_facts_cannot_be_disguised_as_helper_source(producer):
    with pytest.raises(ValueError, match='helper or reread'):
        source_context_offer(owner(), [search_record()], producer=producer)


def test_existing_token_limit_does_not_reset_after_first_extension(tmp_path):
    method, template = front_fixture(tmp_path)
    baseline = run_front_half(method, tmp_path, SequentialGaps(template), CONFIG, lambda t: len(t.split()))
    cap = sum(c['prompt_tokens_reserved'] + c['output_tokens_reserved'] for c in baseline.model_calls[:2]) + 32
    provider = SequentialGaps(template)
    trace = run_front_half(method, tmp_path, provider, {**CONFIG, 'max_total_tokens': cap}, lambda t: len(t.split()))
    assert trace.status == 'BUDGET_EXHAUSTED'
    assert len(provider.requests) == 2


def test_existing_deadline_does_not_restart_on_second_acquisition(tmp_path, monkeypatch):
    from contractedge.orchestration import runner
    now = [0.0]
    monkeypatch.setattr(runner.time, 'monotonic', lambda: now[0])
    method, template = front_fixture(tmp_path)
    class SlowFirstExtension(SequentialGaps):
        def complete_json(self, request):
            response = super().complete_json(request)
            if self.diagnosis_count == 2:
                now[0] = 2401.0
            return response
    provider = SlowFirstExtension(template)
    trace = run_front_half(method, tmp_path, provider, CONFIG, lambda t: len(t.split()))
    assert trace.status == 'BUDGET_EXHAUSTED'
    assert len(provider.requests) == 2


def test_planning_and_diagnosis_use_the_same_two_acquisition_pool(tmp_path):
    from tests.test_planning_evidence_extension import PlanningGapProvider
    method, template = front_fixture(tmp_path)
    class PlanningThenDiagnosis(PlanningGapProvider):
        def complete_json(self, request):
            response = super().complete_json(request)
            if request.prompt_version.startswith('diagnosis.') and self.diagnosis_calls == 2:
                body = json.loads(response.content)
                body.update(status='EVIDENCE_GAP', evidence_gaps=[dict(id='G1', summary='Check another exact file.',
                    locator_hint={'path': 'templates/optional-second.yaml'})])
                return ModelResponse(json.dumps(body), {})
            return response
    provider = PlanningThenDiagnosis()
    provider._snapshot_digests = {'templates/serviceaccount.yaml': hashlib.sha256(template.read_bytes()).hexdigest()}
    trace = run_front_half(method, tmp_path, provider, CONFIG, lambda t: len(t.split()))
    assert trace.status == 'READY', trace.errors
    assert provider.diagnosis_calls == 3 and provider.plan_calls == 2
    assert [r['round_id'] for r in trace.retrieval_rounds] == [1, 2, 3]


def test_complete_request_cannot_drop_adopted_records_to_fit(tmp_path):
    method, template = front_fixture(tmp_path)
    provider = SequentialGaps(template)
    baseline = run_front_half(method, tmp_path, provider, CONFIG, len)
    assert baseline.status == 'READY'
    first, second, third = [c['prompt_tokens_reserved'] for c in baseline.model_calls[:3]]
    assert third > second >= first
    limited = SequentialGaps(template)
    trace = run_front_half(method, tmp_path, limited,
        {**CONFIG, 'max_context_tokens': second + CONFIG['max_output_tokens'] + 1,
         'initial_diagnosis_context_tokens': first + 1}, len)
    assert trace.status == 'BUDGET_EXHAUSTED'
    assert len(limited.requests) == 2
    assert trace.run_context['retrieval_config']['source_context'] == limited.requests[-1].metadata['source_context']
