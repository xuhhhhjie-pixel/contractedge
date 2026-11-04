"""Corrupt actual prepared messages, then exercise the production stage loop."""
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace

import pytest

from contractedge.development import public_demo
from contractedge.model.provider import ModelResponse


def walk(value, edit):
    if isinstance(value, dict):
        edit(value)
        for key, child in list(value.items()):
            value[key] = walk(child, edit)
    elif isinstance(value, list):
        value = [walk(child, edit) for child in value]
    elif isinstance(value, str) and value[:1] in {'{', '['}:
        try:
            parsed = json.loads(value)
        except ValueError:
            return value
        original = json.dumps(parsed, sort_keys=True)
        changed = walk(parsed, edit)
        if json.dumps(changed, sort_keys=True) != original:
            value = json.dumps(changed)
    return value


def run_wire_fault(tmp_path, monkeypatch, *, stage, edit, case='reload-state', reject_first=False):
    sent, removed = [], []

    class PreparedProvider(public_demo.SyntheticExampleProvider):
        def prepare_request(self, req):
            messages = json.loads(json.dumps(req.messages))
            if req.prompt_version.startswith(stage):
                def record_edit(value):
                    before = json.dumps(value, sort_keys=True)
                    edit(value)
                    if json.dumps(value, sort_keys=True) != before:
                        removed.append(req.prompt_version)
                messages = walk(messages, record_edit)
            body = json.dumps({'messages': messages}).encode()
            return SimpleNamespace(body=body, metadata={}, overhead='', output_schema=None,
                wire_schema=None, audit_record=lambda: {'body_sha256': hashlib.sha256(body).hexdigest()})

        def complete_prepared_json(self, req, prepared):
            sent.append(req.prompt_version)
            if reject_first and req.prompt_version.startswith('topomen-source') and sent.count(req.prompt_version) == 1:
                return ModelResponse('{}', {'provider': 'synthetic-invalid-first', 'zero_cost': True})
            return super().complete_json(replace(req, messages=tuple(json.loads(prepared.body)['messages'])))

    monkeypatch.setattr(public_demo, '_provider', lambda settings, case, runner:
        (PreparedProvider(case, runner), public_demo.count_tokens))
    if reject_first:
        original = public_demo.default_config
        monkeypatch.setattr(public_demo, 'default_config', lambda case: {
            **original(case), 'execution_format_retry_in_round': True, 'max_format_retries': 1})
    result = public_demo.run_demo(case, tmp_path / 'run')
    ledger_path = tmp_path / 'run/repair/model_calls.json'
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else []
    assert len(ledger) == len(sent), result
    return result, sent, removed, ledger


def test_initial_diagnosis_selected_source_is_required_on_final_wire(tmp_path, monkeypatch):
    def strip(value):
        if value.get('kind') == 'SOURCE_SPAN':
            value.pop('excerpt', None)
    result, sent, removed, ledger = run_wire_fault(tmp_path, monkeypatch, stage='diagnosis.', edit=strip)
    assert removed
    assert result['status'] == 'EVIDENCE_DELIVERY_INCOMPLETE', result
    assert sent == ledger == []


@pytest.mark.parametrize('stage,expected_calls', [
    ('diagnosis.', 0), ('changeset.', 1), ('topomen-source', 2), ('public-postcondition-review.', 3)])
def test_declared_artifact_body_is_required_at_each_real_stage(tmp_path, monkeypatch, stage, expected_calls):
    def strip(value):
        for row in value.get('declared_artifacts', []):
            row.pop('content', None)
    result, sent, removed, _ = run_wire_fault(tmp_path, monkeypatch, stage=stage, edit=strip)
    assert removed, (stage, sent)
    assert result['status'] == 'EVIDENCE_DELIVERY_INCOMPLETE', result
    assert len(sent) == expected_calls


def test_adopted_feedback_cannot_lose_rationale_while_keeping_observations(tmp_path, monkeypatch):
    def strip(value):
        feedback = value.get('execution_feedback')
        if isinstance(feedback, dict):
            feedback.pop('rationale', None)
    result, sent, removed, _ = run_wire_fault(tmp_path, monkeypatch,
        stage='topomen-source', edit=strip, case='two-consumers')
    assert removed
    assert result['status'] == 'EVIDENCE_DELIVERY_INCOMPLETE', result
    assert sum(version.startswith('topomen-source') for version in sent) == 1


def test_adopted_rejected_response_cannot_lose_its_body_on_correction(tmp_path, monkeypatch):
    def strip(value):
        history = value.get('previous_execution_attempt')
        if isinstance(history, dict):
            history.pop('model_response', None)
    result, sent, removed, _ = run_wire_fault(tmp_path, monkeypatch,
        stage='topomen-source', edit=strip, reject_first=True)
    assert removed
    assert result['status'] == 'EVIDENCE_DELIVERY_INCOMPLETE', result
    assert sum(version.startswith('topomen-source') for version in sent) == 1


def test_review_transaction_identity_cannot_be_removed_from_prepared_request(tmp_path, monkeypatch):
    def strip(value):
        for row in value.get('execution_mapping', []):
            row.pop('from_snapshot', None)
    result, sent, removed, _ = run_wire_fault(tmp_path, monkeypatch, stage='public-postcondition-review.', edit=strip)
    assert removed
    assert result['status'] == 'EVIDENCE_DELIVERY_INCOMPLETE', result
    assert not any(version.startswith('public-postcondition-review.') for version in sent)


def test_unchanged_prepared_messages_allow_complete_feedback_and_retry(tmp_path, monkeypatch):
    result, sent, removed, _ = run_wire_fault(tmp_path, monkeypatch,
        stage='topomen-source', edit=lambda value: None, case='two-consumers')
    assert result['status'] == 'PUBLIC_REVIEW_COMPLETE', result
    assert sum(version.startswith('topomen-source') for version in sent) == 2
    assert removed == []
