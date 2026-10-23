"""Production execution and review must admit the final provider request."""
from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from contractedge.execution.review import ModelPublicReviewer
from contractedge.execution.topomen import build_execution_request, ModelPlanExecutor, PlanRejected
from contractedge.execution.topomen_agent import TopoMenAgentExecutor
from contractedge.model.admission import quote_request
from contractedge.model.provider import ModelResponse, OpenAICompatibleProvider
from tests.test_repair_execution import bound_plan


EDITS = {'edits': [{'change_id': 'C1', 'path': 'templates/serviceaccount.yaml',
    'operation': 'MODIFY', 'old_text': 'name: default', 'new_text': 'name: selected'}]}
REVIEW = {'checks': [{'id': 'P1', 'status': 'UNKNOWN', 'rationale': 'limited public evidence',
    'evidence': []}], 'failure_kind': 'UNKNOWN', 'rationale': 'public review only'}


class NativePort(OpenAICompatibleProvider):
    def __init__(self, responses=None):
        super().__init__('https://unused.invalid', 'UNUSED',
            output_mode='json_schema', supports_json_schema=True)
        self.preparations, self.dispatches = [], []
        self.responses = list(responses or [])

    def prepare_request(self, request):
        prepared = super().prepare_request(request)
        self.preparations.append((request, prepared))
        return prepared

    def complete_json(self, request):
        prepared = self._prepared_for(request)
        self.dispatches.append((request, prepared))
        if self.responses:
            content = self.responses.pop(0)
        else:
            content = json.dumps(REVIEW if request.prompt_version.startswith('public-') else EDITS)
        return ModelResponse(content, {'finish_reason': 'stop'})


def source_fixture(root):
    (root / 'templates').mkdir()
    (root / 'templates/serviceaccount.yaml').write_text('name: default\n')
    return bound_plan(root)


def config():
    return {'model': 'fixture', 'provider': 'openai_compatible',
        'max_context_tokens': 100000, 'execution_max_output_tokens': 1,
        'review_max_output_tokens': 1, 'schema_in_prompt': False}


def input_size(request):
    return sum(len(message['content']) for message in request.messages)


def test_direct_native_schema_overhead_rejects_before_dispatch(tmp_path):
    plan, settings = source_fixture(tmp_path), config()
    initial = build_execution_request(plan, tmp_path, {'allowed_paths': ['templates/**']},
        len, settings, requirement='Select configured name')
    settings['max_context_tokens'] = input_size(initial) + 2
    provider = NativePort()
    with pytest.raises(PlanRejected, match='context budget'):
        ModelPlanExecutor(provider, len, settings).execute(plan, tmp_path,
            {'allowed_paths': ['templates/**']}, requirement='Select configured name')
    assert not provider.dispatches
    assert (tmp_path / 'templates/serviceaccount.yaml').read_text() == 'name: default\n'


def test_direct_exact_input_cap_does_not_subtract_output_reservation(tmp_path):
    plan, settings = source_fixture(tmp_path), config()
    initial = build_execution_request(plan, tmp_path, {'allowed_paths': ['templates/**']},
        len, settings, requirement='Select configured name')
    probe = quote_request(NativePort(), initial, len)
    settings.update(max_context_tokens=probe.input_tokens,
        model_context_tokens=probe.total_tokens)
    provider = NativePort()
    result = ModelPlanExecutor(provider, len, settings).execute(plan, tmp_path,
        {'allowed_paths': ['templates/**']}, requirement='Select configured name')
    assert result['status'] == 'APPLIED'
    assert len(provider.preparations) == len(provider.dispatches) == 1
    assert provider.preparations[0][1] is provider.dispatches[0][1]


def test_optional_execution_support_is_trimmed_with_native_overhead(tmp_path):
    plan, settings = source_fixture(tmp_path), config()
    settings['execution_max_output_tokens'] = 32
    initial = build_execution_request(plan, tmp_path, {'allowed_paths': ['templates/**']},
        len, settings, requirement='Select configured name')
    base = quote_request(NativePort(), initial, len)
    excerpt = 'public helper interface\n' * 100
    (tmp_path / 'helper.txt').write_text(excerpt)
    import hashlib
    evidence = {'id': 'SRC-helper', 'kind': 'SOURCE_SPAN', 'snapshot_id': None,
        'locator': {'path': 'helper.txt'}, 'excerpt': excerpt,
        'content_digest': hashlib.sha256(excerpt.encode()).hexdigest()}
    # The source inventory changed, so bind both the plan and support to it.
    plan = bound_plan(tmp_path)
    evidence['snapshot_id'] = plan['snapshot_id']
    plan['coordination_context'] = {'source_support': [evidence]}
    settings['max_context_tokens'] = base.input_tokens + 1000
    provider = NativePort()
    request = build_execution_request(plan, tmp_path, {'allowed_paths': ['templates/**']},
        len, settings, requirement='Select configured name', provider=provider)
    assert not json.loads(request.messages[1]['content'])['read_only_source_support']
    assert any(row['result'] == 'FULL_REQUEST_CONTEXT_BUDGET'
        for row in request.metadata['read_only_support_audit'])
    assert not provider.dispatches


class Submitted(Exception):
    pass


class OneStepAgent:
    wrapper = ''
    def __init__(self, model, env, **kwargs):
        self.model, self.env = model, env

    def run(self, requirement, **values):
        messages = [{'role': 'system', 'content': values['execution_system']},
                    {'role': 'user', 'content': values['execution_input']}]
        if self.wrapper:
            messages.append({'role': 'user', 'content': self.wrapper})
        answer = self.model.query(messages)
        try:
            self.env.execute(answer['extra']['actions'][0])
        except Submitted:
            return {'exit_status': 'Submitted'}

    def serialize(self):
        return {'info': {'exit_status': 'Submitted'}}


def test_native_wrapping_rechecks_final_context_before_dispatch(tmp_path):
    plan, settings = source_fixture(tmp_path), config()
    settings['max_context_tokens'] = 15000
    provider = NativePort()
    class WrappedAgent(OneStepAgent):
        wrapper = 'native wrapper must also fit ' * 1000
    with patch('contractedge.execution.topomen_agent._native_agent',
            return_value=(WrappedAgent, Submitted, {'agent_class': 'fake.native.agent'})):
        with pytest.raises(PlanRejected, match='context budget'):
            TopoMenAgentExecutor(provider, len, settings).execute(plan, tmp_path,
                {'allowed_paths': ['templates/**']}, requirement='Select configured name')
    assert not provider.dispatches
    assert (tmp_path / 'templates/serviceaccount.yaml').read_text() == 'name: default\n'


def test_native_unchanged_messages_reuse_final_preparation(tmp_path):
    plan, provider = source_fixture(tmp_path), NativePort()
    with patch('contractedge.execution.topomen_agent._native_agent',
            return_value=(OneStepAgent, Submitted, {'agent_class': 'fake.native.agent'})):
        result = TopoMenAgentExecutor(provider, len, config()).execute(plan, tmp_path,
            {'allowed_paths': ['templates/**']}, requirement='Select configured name')
    assert result['status'] == 'APPLIED'
    assert len(provider.preparations) == len(provider.dispatches) == 1
    assert provider.preparations[0][1] is provider.dispatches[0][1]


def review_inputs():
    return ({'postconditions': [{'id': 'P1', 'statement': 'required field',
        'verification_kind': 'PUBLIC_STRUCTURE'}]},
        {'status': 'EXECUTION_PASS', 'observations': [{'id': 'OBS1',
            'evidence_kind': 'PUBLIC_STRUCTURE', 'excerpt': 'actual output'}]})


def test_review_schema_overhead_returns_context_limit_without_dispatch():
    plan, observation = review_inputs()
    provider, settings = NativePort(), config()
    ModelPublicReviewer(provider, len, settings).review('Required behavior', plan, observation)
    initial = provider.dispatches[0][0]
    provider = NativePort()
    settings['max_context_tokens'] = input_size(initial) + 2
    result = ModelPublicReviewer(provider, len, settings).review('Required behavior', plan, observation)
    assert result['decision'] == 'UNKNOWN'
    assert result['method'] == 'CONTEXT_LIMIT'
    assert not provider.dispatches


def test_review_retry_is_admitted_again_and_keeps_only_first_call():
    plan, observation = review_inputs()
    settings, probe = config(), NativePort()
    ModelPublicReviewer(probe, len, settings).review('Required behavior', plan, observation)
    quote = quote_request(NativePort(), probe.dispatches[0][0], len)
    settings.update(max_context_tokens=quote.input_tokens + 1, max_format_retries=1)
    provider = NativePort(['invalid JSON', json.dumps(REVIEW)])
    result = ModelPublicReviewer(provider, len, settings).review('Required behavior', plan, observation)
    assert result['method'] == 'CONTEXT_LIMIT'
    assert len(provider.dispatches) == 1
    assert len(provider.preparations) == 2


def test_review_uses_one_preparation_for_its_dispatched_request():
    plan, observation = review_inputs()
    provider = NativePort()
    result = ModelPublicReviewer(provider, len, config()).review('Required behavior', plan, observation)
    assert result['method'] == 'MODEL_PUBLIC_REVIEW'
    assert len(provider.preparations) == len(provider.dispatches) == 1
    assert provider.preparations[0][1] is provider.dispatches[0][1]
