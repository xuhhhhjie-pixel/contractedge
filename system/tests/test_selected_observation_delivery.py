"""Current selected observations and explicit history have separate identities."""
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace

import pytest

from contractedge.development import public_demo
from contractedge.model.admission import complete_quoted, quote_request
from contractedge.orchestration.semantic_delivery import delivery_context, require_delivery, SemanticDeliveryError
from tests.test_semantic_delivery_closure import budget_with_monitor, inspect as inspect_request, request


def observation(snapshot='current', text='The preservation control passed.', **extra):
    return dict(id='OBS-CONTROL', kind='PUBLIC_OBSERVATION', snapshot_id=snapshot,
        locator={'path': 'public.json', 'field': 'control'}, excerpt=text,
        content_digest=hashlib.sha256(text.encode()).hexdigest(),
        behavior_check={'id': 'public:control', 'status': 'PASS'}, **extra)


def ref(record):
    return {key: record[key] for key in ('id', 'snapshot_id', 'content_digest')}


def test_same_local_id_in_current_and_historical_snapshot_keeps_both_full_bodies():
    current, historical = observation(), observation('old', 'The earlier preservation control failed.')
    frame = delivery_context({}, consumer='REVIEW', snapshot_id='current',
        diagnosis={'snapshot_id': 'old', 'evidence_refs': [historical]}, evidence=[current],
        mandatory_refs=[ref(current), ref(historical)])
    report = inspect_request({'public_observations': [current],
        'previous_attempt': {'snapshot_id': 'old', 'public_observations': [historical]}}, frame)
    assert frame['source_conflicts'] == []
    assert report['required_content_count'] == 2
    assert {row['original']['snapshot_id'] for row in report['subjects'] if row['kind'] == 'EVIDENCE'} == {'old', 'current'}
    require_delivery(report)


def test_current_required_observation_does_not_select_the_first_historical_same_id():
    current, historical = observation(), observation('old', 'Previous result.')
    frame = delivery_context({}, consumer='REVIEW', snapshot_id='current',
        diagnosis={'snapshot_id': 'old', 'evidence_refs': [historical]}, evidence=[current],
        mandatory_refs=[ref(current)])
    report = inspect_request({'public_observations': [current],
        'previous_attempt': {'snapshot_id': 'old', 'public_observations': [historical]}}, frame)
    required = [row for row in report['subjects'] if row['required_content_gate']]
    assert len(required) == 1 and required[0]['original'] == current
    require_delivery(report)


@pytest.mark.parametrize('change', [{'excerpt': 'different'}, {'scenario': 'different'}])
def test_same_snapshot_record_conflict_is_never_hidden_by_the_other_full_copy(change):
    current = observation(scenario='control')
    changed = {**current, **change}
    changed['content_digest'] = hashlib.sha256(changed['excerpt'].encode()).hexdigest()
    frame = delivery_context({}, consumer='REVIEW', snapshot_id='current',
        diagnosis={'snapshot_id': 'current', 'evidence_refs': [current]}, evidence=[changed],
        mandatory_refs=[ref(current)])
    report = inspect_request({'public_observations': [current, changed]}, frame)
    with pytest.raises(SemanticDeliveryError, match='CONFLICTING_ORIGINAL_SOURCE'):
        require_delivery(report)


def test_mandatory_reference_digest_mismatch_cannot_silently_remove_its_gate():
    current = observation()
    frame = delivery_context({}, consumer='REVIEW', snapshot_id='current', evidence=[current],
        mandatory_refs=[{**ref(current), 'content_digest': '0' * 64}])
    report = inspect_request({'public_observations': [current]}, frame)
    assert report['required_content_count'] == 1
    with pytest.raises(SemanticDeliveryError):
        require_delivery(report)


def test_selected_ordinary_pass_body_is_required_at_the_actual_dispatch_boundary(tmp_path):
    current = observation()
    frame = delivery_context({}, consumer='REVIEW', snapshot_id='current', evidence=[current],
        mandatory_refs=[ref(current)])
    provider, budget, _, monitor = budget_with_monitor(tmp_path)
    body = {'public_observations': [{key: value for key, value in current.items() if key != 'excerpt'}]}
    req = request(body)
    quote = quote_request(budget, req, len)
    with monitor.scope(frame), pytest.raises(SemanticDeliveryError):
        complete_quoted(budget, req, quote)
    assert provider.requests == [] and budget.calls == [] and budget.reserved_tokens == 0


def test_same_pass_body_cannot_relabel_the_public_scenario_in_final_messages():
    current = observation(scenario='preservation')
    frame = delivery_context({}, consumer='REVIEW', snapshot_id='current', evidence=[current],
        mandatory_refs=[ref(current)])
    report = inspect_request({'public_observations': [dict(current, scenario='unrelated')]}, frame)
    with pytest.raises(SemanticDeliveryError, match='CONTENT_DRIFT'):
        require_delivery(report)


def test_real_planning_boundary_rejects_uncited_selected_pass_body_removed_from_wire(tmp_path, monkeypatch):
    dispatched = []

    class FinalProjectionProvider(public_demo.SyntheticExampleProvider):
        def prepare_request(self, req):
            messages = json.loads(json.dumps(req.messages))
            if req.prompt_version.startswith('changeset.'):
                def remove_control_body(value):
                    if isinstance(value, dict):
                        if value.get('kind') == 'PUBLIC_OBSERVATION' and value.get('id') == 'OBS-stable':
                            value.pop('excerpt', None)
                        for child in value.values():
                            remove_control_body(child)
                    elif isinstance(value, list):
                        for child in value:
                            remove_control_body(child)
                for message in messages:
                    try:
                        body = json.loads(message['content'])
                    except (ValueError, TypeError):
                        continue
                    remove_control_body(body)
                    message['content'] = json.dumps(body)
            body = json.dumps({'messages': messages}).encode()
            return SimpleNamespace(body=body, metadata={}, overhead='', output_schema=None, wire_schema=None,
                audit_record=lambda: {'body_sha256': hashlib.sha256(body).hexdigest()})

        def complete_prepared_json(self, req, prepared):
            dispatched.append(req.prompt_version)
            return super().complete_json(replace(req, messages=tuple(json.loads(prepared.body)['messages'])))

    monkeypatch.setattr(public_demo, '_provider', lambda settings, case, runner:
                        (FinalProjectionProvider(case, runner), public_demo.count_tokens))
    result = public_demo.run_demo('reload-state', tmp_path / 'run')
    assert result['status'] == 'EVIDENCE_DELIVERY_INCOMPLETE', result
    assert len(dispatched) == 1 and dispatched[0].startswith('diagnosis.')
    ledger = json.loads((tmp_path / 'run/repair/model_calls.json').read_text())
    assert len(ledger) == 1
