"""Original public requirements must survive each actual prepared model request."""
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace

import pytest

from contractedge.development import public_demo
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.semantic_delivery import SemanticDeliveryError
from contractedge.orchestration.repair import revise_plan
from tests.test_stage_material_boundary import run_wire_fault, walk
from tests.test_stage_delivery_admission import ready_front, config
from tests.test_adopted_stage_history import monitored, current_feedback, DispatchReached


def remove_exact_text(value, original, *, changed=False):
    for key, item in list(value.items()):
        if isinstance(item, str) and item == original:
            if changed:
                value[key] = '[original requirement replaced by a different instruction]'
            else:
                value.pop(key)
        elif isinstance(item, list):
            value[key] = [part for part in item if part != original]


@pytest.mark.parametrize('stage,expected_calls', [
    ('diagnosis.', 0), ('changeset.', 1), ('topomen-source', 2), ('public-postcondition-review.', 3)])
@pytest.mark.parametrize('changed', [False, True])
def test_missing_or_changed_original_text_stops_each_actual_stage(tmp_path, monkeypatch, stage, expected_calls, changed):
    raw = (public_demo.SYSTEM_ROOT / 'examples/public-repair/reload-state/requirement.md').read_text()
    result, sent, altered, ledger = run_wire_fault(tmp_path, monkeypatch, stage=stage,
        edit=lambda value: remove_exact_text(value, raw, changed=changed))
    assert altered
    assert result['status'] == 'EVIDENCE_DELIVERY_INCOMPLETE', result
    assert len(sent) == len(ledger) == expected_calls


@pytest.mark.parametrize('changed', [False, True])
def test_plan_revision_keeps_original_requirement_independent_of_history(tmp_path, changed):
    method, source, trace = ready_front(tmp_path)
    def mutate(messages):
        return walk(messages, lambda value: remove_exact_text(value, method.requirement_text, changed=changed))
    port, budget, _ = monitored(tmp_path / 'revision', trace, mutate)
    with pytest.raises(SemanticDeliveryError):
        revise_plan(trace, method, source, current_feedback(trace), budget, len, config())
    assert port.changed and port.sent == budget.calls == []


def test_unchanged_actual_pipeline_retains_the_complete_original_requirement(tmp_path, monkeypatch):
    result, sent, altered, ledger = run_wire_fault(tmp_path, monkeypatch, stage='diagnosis.', edit=lambda value: None)
    assert result['status'] == 'PUBLIC_REVIEW_COMPLETE', result
    assert len(sent) == len(ledger) == 4 and not altered


def test_exact_single_copy_is_enough_on_plan_revision(tmp_path):
    method, source, trace = ready_front(tmp_path)
    port, budget, _ = monitored(tmp_path / 'revision', trace, lambda messages: messages)
    with pytest.raises(DispatchReached):
        revise_plan(trace, method, source, current_feedback(trace), budget, len, config())
    assert len(port.sent) == len(budget.calls) == 1
    payload = json.loads(port.sent[0].messages[1]['content'])
    assert payload['diagnosis_packet']['requirement_spec']['raw_text'] == method.requirement_text


@pytest.mark.parametrize('changed', [False, True])
def test_execution_correction_cannot_lose_original_requirement(tmp_path, monkeypatch, changed):
    raw = (public_demo.SYSTEM_ROOT / 'examples/public-repair/reload-state/requirement.md').read_text()
    correcting = False
    def mutate(value):
        nonlocal correcting
        if isinstance(value.get('previous_execution_attempt'), dict):
            correcting = True
        if correcting:
            remove_exact_text(value, raw, changed=changed)
    result, sent, altered, ledger = run_wire_fault(tmp_path, monkeypatch,
        stage='topomen-source', edit=mutate, reject_first=True)
    assert correcting and altered
    assert result['status'] == 'EVIDENCE_DELIVERY_INCOMPLETE', result
    assert len(sent) == len(ledger) == 3


@pytest.mark.parametrize('stage,prior_calls', [('diagnosis.', 1), ('public-postcondition-review.', 4)])
@pytest.mark.parametrize('remove_requirement', [False, True])
def test_diagnosis_and_review_format_correction_keep_original_text(
        tmp_path, monkeypatch, stage, prior_calls, remove_requirement):
    raw = (public_demo.SYSTEM_ROOT / 'examples/public-repair/reload-state/requirement.md').read_text()
    sent, mutations = [], []
    class Provider(public_demo.SyntheticExampleProvider):
        rejected = False

        def prepare_request(self, request):
            messages = json.loads(json.dumps(request.messages))
            if (remove_requirement and request.prompt_version.startswith(stage)
                    and any(message['role'] == 'assistant' for message in messages)):
                before = json.dumps(messages)
                messages = walk(messages, lambda value: remove_exact_text(value, raw))
                mutations.append(before != json.dumps(messages))
            body = json.dumps({'messages': messages}).encode()
            return SimpleNamespace(body=body, metadata={}, overhead='', output_schema=None,
                wire_schema=None, audit_record=lambda: {'body_sha256': hashlib.sha256(body).hexdigest()})

        def complete_prepared_json(self, request, prepared):
            sent.append(request.prompt_version)
            if request.prompt_version.startswith(stage) and not self.rejected:
                self.rejected = True
                return ModelResponse('{}', {'provider': 'offline-first-format-rejection', 'zero_cost': True})
            return super().complete_json(replace(request,
                messages=tuple(json.loads(prepared.body)['messages'])))
    monkeypatch.setattr(public_demo, '_provider', lambda settings, case, runner:
        (Provider(case, runner), public_demo.count_tokens))
    defaults = public_demo.default_config
    monkeypatch.setattr(public_demo, 'default_config', lambda case: {**defaults(case), 'max_format_retries': 1})
    result = public_demo.run_demo('reload-state', tmp_path / 'run')
    ledger = json.loads((tmp_path / 'run/repair/model_calls.json').read_text())
    assert len(ledger) == len(sent)
    if remove_requirement:
        assert mutations == [True]
        assert result['status'] == 'EVIDENCE_DELIVERY_INCOMPLETE', result
        assert len(sent) == prior_calls
    else:
        assert result['status'] == 'PUBLIC_REVIEW_COMPLETE', result
        assert len(sent) == 5
