"""Execution identities survive the actual public review request and retry."""
from copy import deepcopy
import hashlib
import json

import pytest

from contractedge.contracts.input_context import project_declared_artifacts, DECLARED_ARTIFACT_CONTENT_LIMIT
from contractedge.execution.consumer import execution_review_mapping
from contractedge.execution.review import ModelPublicReviewer
from contractedge.model.budget import BudgetExceeded
from tests.test_public_review_retry import ReviewSequence


def executed(*, literal=False):
    result = dict(status='APPLIED', from_snapshot='source-before', to_snapshot='source-after')
    if literal:
        result['literal_edit_trace'] = [dict(key='C1', file='worker.py', before='old', after='new')]
    else:
        result['semantic_edit_trace'] = [dict(change_id='C1', path='worker.py',
            literal_edits=[dict(old_text='old', new_text='new')])]
    return result


def review(provider, mapping, *, snapshot='source-after'):
    return ModelPublicReviewer(provider, len, dict(model='fixture', max_context_tokens=20000,
        review_max_output_tokens=256, max_format_retries=1)).review('Required behavior',
        {'postconditions': [{'id': 'P1', 'statement': 'actual output exists'}]},
        dict(status='EXECUTION_PASS', snapshot_id=snapshot, execution_mapping=mapping,
            observations=[dict(id='OBS1', snapshot_id=snapshot,
                evidence_kind='PUBLIC_STRUCTURE', excerpt='actual output')]))


@pytest.mark.parametrize('literal', [False, True])
def test_real_edit_identity_reaches_review_and_format_correction(literal):
    result = executed(literal=literal)
    before = deepcopy(result)
    mapping = execution_review_mapping(result)
    assert all(row['from_snapshot'] == 'source-before' and row['to_snapshot'] == 'source-after'
               for row in mapping)
    provider = ReviewSequence()
    assert review(provider, mapping)['decision'] == 'PASS'
    assert len(provider.requests) == 2
    for request in provider.requests:
        body = json.loads(request.messages[1]['content'])
        assert body['execution_mapping'] == mapping
        assert 'from_snapshot and to_snapshot are the endpoints of the complete ordered edit transaction' in request.messages[0]['content']
        assert 'Intermediate old_text and new_text may be absent from either endpoint' in request.messages[0]['content']
    assert result == before


@pytest.mark.parametrize('key', ['from_snapshot', 'to_snapshot'])
def test_an_edit_without_both_snapshot_identities_cannot_be_projected(key):
    result = executed()
    result.pop(key)
    with pytest.raises(ValueError, match='snapshot'):
        execution_review_mapping(result)


def test_stale_edit_mapping_stops_review_before_model_dispatch():
    provider = ReviewSequence()
    mapping = execution_review_mapping(executed())
    with pytest.raises(ValueError, match='snapshot'):
        review(provider, mapping, snapshot='another-candidate')
    assert provider.requests == []


def test_mixed_edit_trials_cannot_be_reviewed_as_one_patch():
    provider = ReviewSequence()
    mapping = execution_review_mapping(executed())
    mapping.append({**mapping[0], 'from_snapshot': 'other-original'})
    with pytest.raises(ValueError, match='snapshot'):
        review(provider, mapping)
    assert provider.requests == []


@pytest.mark.parametrize('literal', [False, True])
def test_actual_sequential_transaction_keeps_intermediate_edits_in_review(tmp_path, literal):
    from contractedge.execution.consumers.direct import ModifyOnlyConsumer
    from contractedge.execution.consumers.topomen import TopoMenRepairConsumer
    from tests.test_repair_consumers import LiteralProvider, run_consumer, semantic, source

    original = source(tmp_path)
    path = 'templates/serviceaccount.yaml'
    pairs = [('name: default', 'name: intermediate'), ('name: intermediate', 'name: selected')]
    if literal:
        consumer = ModifyOnlyConsumer()
        body = {'replacements': [dict(key='C1', file=path, before=old, after=new) for old, new in pairs]}
    else:
        consumer = TopoMenRepairConsumer()
        body = {'edits': [dict(change_id='C1', path=path, operation='MODIFY', old_text=old, new_text=new)
                          for old, new in pairs]}
    _, _, result = run_consumer(consumer, semantic(tmp_path), tmp_path, LiteralProvider(body))
    assert (tmp_path / path).read_text() == 'name: selected\n'
    assert result['from_snapshot'] == original.snapshot_id
    assert result['to_snapshot'] != original.snapshot_id
    mapping = execution_review_mapping(result)
    projected = [(edit['old_text'], edit['new_text']) for row in mapping for edit in row['literal_edits']]
    assert projected == pairs
    provider = ReviewSequence()
    assert review(provider, mapping, snapshot=result['to_snapshot'])['decision'] == 'PASS'
    for request in provider.requests:
        assert json.loads(request.messages[1]['content'])['execution_mapping'] == mapping
        assert 'Intermediate old_text and new_text may be absent from either endpoint' in request.messages[0]['content']


def test_direct_transaction_projection_uses_execution_order_not_response_order(tmp_path):
    from contractedge.contracts.repair_intent import DirectPlanIntent
    from contractedge.execution.consumers.direct import ModifyOnlyConsumer
    from tests.test_repair_consumers import LiteralProvider, run_consumer, source

    original = source(tmp_path)
    path = 'templates/serviceaccount.yaml'
    intent = DirectPlanIntent(dict(schema_version='ce.direct_plan.v1', snapshot_id=original.snapshot_id,
        input_digest='independent-input', goal='selected name', steps=[
            dict(id='C1', path=path, operation='MODIFY', intent='Stage rename.', depends_on=[]),
            dict(id='C2', path=path, operation='MODIFY', intent='Finish rename.', depends_on=['C1'])]))
    body = {'replacements': [
        dict(key='C2', file=path, before='name: intermediate', after='name: selected'),
        dict(key='C1', file=path, before='name: default', after='name: intermediate')]}
    _, _, result = run_consumer(ModifyOnlyConsumer(), intent, tmp_path, LiteralProvider(body))
    assert (tmp_path / path).read_text() == 'name: selected\n'
    assert [row['key'] for row in result['literal_edit_trace']] == ['C2', 'C1']
    mapping = execution_review_mapping(result)
    assert [row['literal_edit_id'] for row in mapping] == ['C1', 'C2']
    provider = ReviewSequence()
    assert review(provider, mapping, snapshot=result['to_snapshot'])['decision'] == 'PASS'
    assert json.loads(provider.requests[0].messages[1]['content'])['execution_mapping'] == mapping


def test_mechanical_pass_still_requires_current_transaction_snapshot():
    from tests.test_check_coverage import CheckCoverageTests, obs

    provider = ReviewSequence()
    reviewer = ModelPublicReviewer(provider, len, {'model': 'fixture'})
    plan = {'postconditions': [dict(id='P', statement='Both checks hold',
                                   **CheckCoverageTests().obligation())]}
    observation = dict(status='EXECUTION_PASS', snapshot_id='S1', observations=[obs('a', 'PASS'), obs('b', 'PASS')])
    assert reviewer.review('Required behavior', plan, observation)['decision'] == 'PASS'
    mapping = execution_review_mapping(executed())
    with pytest.raises(ValueError, match='snapshot'):
        reviewer.review('Required behavior', plan, {**observation, 'execution_mapping': mapping})
    current = [{**row, 'to_snapshot': 'S1'} for row in mapping]
    assert reviewer.review('Required behavior', plan, {**observation, 'execution_mapping': current})['decision'] == 'PASS'
    assert provider.requests == []


def artifact(identifier, content):
    return dict(id=identifier, path=identifier + '.yaml', media_type='text/yaml',
        content=content, content_digest=hashlib.sha256(content.encode()).hexdigest())


def test_declared_input_overflow_is_explicit_instead_of_a_missing_content_marker():
    rows = [artifact('A1', 'x' * DECLARED_ARTIFACT_CONTENT_LIMIT), artifact('A2', 'y')]
    with pytest.raises(BudgetExceeded, match='required_bytes=65537.*allowed_bytes=65536'):
        project_declared_artifacts(rows)


def test_declared_inputs_share_only_exact_copies():
    row = artifact('A1', 'x' * DECLARED_ARTIFACT_CONTENT_LIMIT)
    assert project_declared_artifacts([row, deepcopy(row)]) == [row]


def test_declared_input_identity_cannot_hide_conflicting_content():
    with pytest.raises(ValueError, match='Conflicting'):
        project_declared_artifacts([artifact('A1', 'one'), artifact('A1', 'two')])
