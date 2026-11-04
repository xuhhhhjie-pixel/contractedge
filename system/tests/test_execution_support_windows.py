"""A target path is not proof that another support span was sent to execution."""
import hashlib
import json

import pytest

from contractedge.execution.topomen import build_execution_request, PlanRejected
from contractedge.model.provider import OpenAICompatibleProvider
from contractedge.model.budget import BudgetExceeded
from contractedge.source.snapshot import compute_snapshot
from tests.test_repair_execution import bound_plan


def fixture(root, *, language='py', support_start=16, support_end=18,
            target_start=205, target_end=207, required=True, historical=False):
    (root / 'templates').mkdir()
    (root / 'templates/serviceaccount.yaml').write_text('name: default\n')
    path = 'worker.' + language
    lines = ['# context\n' if language == 'py' else '// context\n'] * 310
    helper = ('def required_helper(value):\n    """Preserve the input identity."""\n    return value\n'
              if language == 'py' else 'func RequiredHelper(value int) int {\n    // Preserve the input identity.\n    return value\n}\n')
    helper_lines = helper.splitlines(keepends=True)
    if language == 'go':
        support_end = support_start + len(helper_lines) - 1
        lines[0] = 'package worker\n'
    lines[support_start - 1:support_end] = helper_lines
    target = ('def consume(value):\n    return required_helper(value)\n\n'
              if language == 'py' else 'func Consume(value int) int {\n    return RequiredHelper(value)\n}\n')
    lines[target_start - 1:target_end] = target.splitlines(keepends=True)
    (root / path).write_text(''.join(lines))
    plan = bound_plan(root)
    snapshot = compute_snapshot(root)
    plan['operations'][0]['target'].update(path=path, content_digest=snapshot.file_digests[path],
        locator={'start_line': target_start, 'end_line': target_end})
    excerpt = ''.join(lines[support_start - 1:support_end])
    record = dict(id='SUPPORT-helper', kind='SOURCE_SPAN', snapshot_id='prior' if historical else snapshot.snapshot_id,
                  locator={'path': path, 'start_line': support_start, 'end_line': support_end},
                  content_digest=hashlib.sha256(excerpt.encode()).hexdigest(), excerpt=excerpt,
                  origin='Exact public helper source', delivery_required=required)
    plan['coordination_context'] = {'source_support': [record]}
    config = dict(model='fixture', max_context_tokens=100000, execution_max_output_tokens=1000,
                  max_execution_support_bytes=24000)
    return plan, config, record


def request(root, plan, config):
    built = build_execution_request(plan, root, {'allowed_paths': ['worker.*']}, len, config, requirement='Preserve input identity')
    # Serialize the final compatible request without reading credentials or sending.
    provider = OpenAICompatibleProvider('https://fixture.invalid', 'UNUSED_FIXTURE_CREDENTIAL')
    wire = json.loads(provider.prepare_request(built).body)
    return built, json.loads(wire['messages'][1]['content'])


@pytest.mark.parametrize('helper_lookup', [False, True])
def test_same_path_disjoint_support_is_delivered_without_relying_on_helper_lookup(tmp_path, helper_lookup):
    plan, config, record = fixture(tmp_path)
    config['execution_helper_support_enabled'] = helper_lookup
    built, body = request(tmp_path, plan, config)
    target = body['current_target_files'][0]
    assert record['excerpt'] not in target['content']
    assert target['window_segments'] == [[185, 227]]
    assert body['read_only_source_support'] == [record]
    assert built.metadata['read_only_support_audit'][0]['result'] == 'INCLUDED_READ_ONLY'
    assert 'excerpt' not in body['accepted_plan']['coordination_context']['source_support'][0]


def test_fully_covered_same_path_support_is_not_duplicated(tmp_path):
    plan, config, record = fixture(tmp_path, support_start=195, support_end=197)
    config['max_execution_support_bytes'] = 0
    built, body = request(tmp_path, plan, config)
    assert record['excerpt'] in body['current_target_files'][0]['content']
    assert body['read_only_source_support'] == []
    assert built.metadata['read_only_support_audit'][0]['result'] == 'TARGET_ALREADY_SUPPLIED'


def test_partial_same_path_overlap_still_delivers_the_whole_support_record(tmp_path):
    plan, config, record = fixture(tmp_path, support_start=184, support_end=186)
    built, body = request(tmp_path, plan, config)
    assert record['excerpt'] not in body['current_target_files'][0]['content']
    assert body['read_only_source_support'] == [record]


def test_go_prefix_and_target_envelope_does_not_claim_support_in_omitted_gap(tmp_path):
    plan, config, record = fixture(tmp_path, language='go', support_start=150, support_end=153,
                                   target_start=260, target_end=262)
    built, body = request(tmp_path, plan, config)
    target = body['current_target_files'][0]
    assert target['line_start'] < record['locator']['start_line'] < target['line_end']
    assert target['window_segments'] == [[1, 120], [240, 282]]
    assert record['excerpt'] not in target['content']
    assert body['read_only_source_support'] == [record]


def test_duplicate_bytes_at_another_visible_location_do_not_cover_the_support_locator(tmp_path):
    plan, config, record = fixture(tmp_path)
    record.update(excerpt='# context\n', content_digest=hashlib.sha256(b'# context\n').hexdigest(),
                  locator={'path': 'worker.py', 'start_line': 10, 'end_line': 10})
    built, body = request(tmp_path, plan, config)
    assert record['excerpt'] in body['current_target_files'][0]['content']
    assert body['read_only_source_support'] == [record]


@pytest.mark.parametrize('required', [False, True])
def test_selected_support_requires_complete_delivery_regardless_of_old_required_flag(tmp_path, required):
    plan, config, _ = fixture(tmp_path, required=required)
    config['max_execution_support_bytes'] = 1
    with pytest.raises(BudgetExceeded, match='read-only support budget'):
        request(tmp_path, plan, config)


@pytest.mark.parametrize('changed', [False, True])
def test_historical_required_same_path_support_keeps_exact_reread_rules(tmp_path, changed):
    plan, config, record = fixture(tmp_path, historical=True)
    if changed:
        record['excerpt'] += '# not in current source\n'
        record['content_digest'] = hashlib.sha256(record['excerpt'].encode()).hexdigest()
        plan['precondition_review'] = [{'source_evidence_ref': record['id'], 'disposition': 'REQUIRED'}]
        with pytest.raises(PlanRejected, match='source was not delivered'):
            request(tmp_path, plan, config)
    else:
        built, body = request(tmp_path, plan, config)
        delivered = body['read_only_source_support'][0]
        assert delivered['excerpt'] == record['excerpt']
        assert delivered['snapshot_id'] == plan['snapshot_id']
        assert delivered['locator'] == record['locator']


def test_covered_same_path_record_cannot_bypass_content_digest_validation(tmp_path):
    plan, config, _ = fixture(tmp_path, support_start=195, support_end=197)
    plan['coordination_context']['source_support'][0]['content_digest'] = '0' * 64
    with pytest.raises(PlanRejected, match='source was not delivered'):
        request(tmp_path, plan, config)


@pytest.mark.parametrize('valid_digest', [False, True])
def test_full_file_same_path_support_requires_valid_bytes_before_dedup(tmp_path, valid_digest):
    plan, config, record = fixture(tmp_path)
    plan['operations'][0]['target'].pop('locator')
    config['max_execution_support_bytes'] = 0
    if not valid_digest:
        record['content_digest'] = '0' * 64
        with pytest.raises(PlanRejected, match='source was not delivered'):
            request(tmp_path, plan, config)
    else:
        built, body = request(tmp_path, plan, config)
        assert 'window_segments' not in body['current_target_files'][0]
        assert body['read_only_source_support'] == []
        assert built.metadata['read_only_support_audit'][0]['result'] == 'TARGET_ALREADY_SUPPLIED'


def test_historical_same_path_ambiguous_source_cannot_be_reread(tmp_path):
    plan, config, record = fixture(tmp_path, historical=True)
    record.update(excerpt='# context\n', content_digest=hashlib.sha256(b'# context\n').hexdigest())
    with pytest.raises(PlanRejected, match='source was not delivered'):
        request(tmp_path, plan, config)


@pytest.mark.parametrize('support_budget', [0, 24000])
def test_historical_covered_source_retains_explicit_fresh_reread_identity(tmp_path, support_budget):
    plan, config, record = fixture(tmp_path, historical=True, support_start=195, support_end=197)
    record['locator'].update(start_line=16, end_line=18)
    config['max_execution_support_bytes'] = support_budget
    if not support_budget:
        with pytest.raises(BudgetExceeded, match='read-only support budget'):
            request(tmp_path, plan, config)
    else:
        built, body = request(tmp_path, plan, config)
        assert record['excerpt'] in body['current_target_files'][0]['content']
        delivered = body['read_only_source_support'][0]
        assert delivered['snapshot_id'] == plan['snapshot_id']
        assert delivered['locator'] == {'path': 'worker.py', 'start_line': 195, 'end_line': 197}
        assert built.metadata['read_only_support_audit'][0]['result'] == 'INCLUDED_READ_ONLY'


def test_precondition_review_requires_uncovered_same_path_source(tmp_path):
    plan, config, record = fixture(tmp_path, required=False)
    plan['precondition_review'] = [{'source_evidence_ref': record['id'], 'disposition': 'REQUIRED'}]
    config['max_execution_support_bytes'] = 0
    with pytest.raises(BudgetExceeded, match='read-only support budget'):
        request(tmp_path, plan, config)


def test_covered_excerpt_without_trailing_newline_is_not_duplicated(tmp_path):
    plan, config, record = fixture(tmp_path, support_start=195, support_end=197)
    record['excerpt'] = record['excerpt'].rstrip('\n')
    record['content_digest'] = hashlib.sha256(record['excerpt'].encode()).hexdigest()
    config['max_execution_support_bytes'] = 0
    built, body = request(tmp_path, plan, config)
    assert body['read_only_source_support'] == []
    assert built.metadata['read_only_support_audit'][0]['result'] == 'TARGET_ALREADY_SUPPLIED'


@pytest.mark.parametrize('explicit_precondition,valid_digest', [(False, True), (True, True), (False, False)])
def test_historical_selection_can_continue_on_complete_current_target_without_relabeling_old_source(
        tmp_path, explicit_precondition, valid_digest):
    plan, config, record = fixture(tmp_path, historical=True)
    plan['operations'][0]['target'].pop('locator')
    record['excerpt'] += '# Changed by the prior accepted edit.\n'
    record['content_digest'] = hashlib.sha256(record['excerpt'].encode()).hexdigest() if valid_digest else '0' * 64
    if explicit_precondition:
        plan['precondition_review'] = [{'source_evidence_ref': record['id'], 'disposition': 'REQUIRED'}]
    config['max_execution_support_bytes'] = 0
    if explicit_precondition or not valid_digest:
        with pytest.raises(PlanRejected, match='source was not delivered'):
            request(tmp_path, plan, config)
    else:
        built, body = request(tmp_path, plan, config)
        assert body['read_only_source_support'] == []
        assert record['excerpt'] not in body['current_target_files'][0]['content']
        assert body['current_target_files'][0]['content'] == (tmp_path / 'worker.py').read_text()
        assert built.metadata['read_only_support_audit'][0]['result'] == 'HISTORICAL_SNAPSHOT'
        assert body['accepted_plan']['coordination_context']['source_support'][0]['snapshot_id'] == 'prior'


def test_required_same_path_support_cannot_be_trimmed_to_fit_full_request_budget(tmp_path):
    plan, config, record = fixture(tmp_path)
    initial, body = request(tmp_path, plan, config)
    assert record in body['read_only_source_support']
    config['max_context_tokens'] = sum(len(message['content']) for message in initial.messages) - 1
    with pytest.raises(BudgetExceeded, match='full context budget'):
        request(tmp_path, plan, config)
