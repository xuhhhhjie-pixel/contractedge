"""The parent must enforce the same input/total contract as the method."""
from dataclasses import asdict
import json
from types import SimpleNamespace

import pytest

from contractedge.model.admission import context_fits, quote_request
from contractedge.model.provider import ModelRequest, ModelResponse
from tests.test_isolated_method import load


class PublicPort:
    timeout_seconds = 240
    def __init__(self): self.dispatches = 0
    def request_overhead(self, request): return 'schema'
    def complete_json(self, request):
        self.dispatches += 1
        return ModelResponse('{}', {'model': 'fixture'})


def config(**changes):
    return dict(model='fixture', max_model_calls=2, max_total_tokens=200,
                max_elapsed_seconds=30, max_context_tokens=10, model_context_tokens=18,
                max_output_tokens=10, execution_max_output_tokens=10,
                review_max_output_tokens=10, timeout_seconds=240, **changes)


@pytest.mark.parametrize('stage', ['diagnosis.initial', 'diagnosis.rediagnosis',
                                  'changeset.revision', 'topomen-source-execution.v1', 'review'])
@pytest.mark.parametrize('thinking', [None, 'disabled'])
def test_parent_accepts_exact_input_and_total_limits_for_every_stage(tmp_path, stage, thinking):
    port = PublicPort()
    request = ModelRequest('fixture', stage, ({'role': 'user', 'content': 'data'},), 8, thinking=thinking)
    assert context_fits(quote_request(port, request, len), 10, 18)
    broker = load('run_isolated_method').ModelBroker(port, len, config(), tmp_path/'calls.json')
    assert broker.complete({'request': asdict(request), 'timeout_seconds': 1})['content'] == '{}'
    assert port.dispatches == 1 and broker.policy_failures == []
    ledger = json.loads((tmp_path/'calls.json').read_text())
    assert ledger[0]['prompt_tokens_reserved'] == 10 and ledger[0]['output_tokens_reserved'] == 8


@pytest.mark.parametrize('content,output,total_limit', [('extra', 8, 30), ('data', 9, 18)])
def test_exceeding_either_input_or_total_limit_never_dispatches(tmp_path, content, output, total_limit):
    port = PublicPort(); values = config(); values['model_context_tokens'] = total_limit
    request = ModelRequest('fixture', 'diagnosis.rediagnosis', ({'role': 'user', 'content': content},), output)
    assert not context_fits(quote_request(port, request, len), 10, total_limit)
    broker = load('run_isolated_method').ModelBroker(port, len, values, tmp_path/'calls.json')
    with pytest.raises(ValueError, match='context'): broker.complete({'request': asdict(request), 'timeout_seconds': 1})
    assert port.dispatches == 0 and broker.budget.calls == []
    assert not (tmp_path/'calls.json').exists()


def test_input_only_legacy_configuration_keeps_shared_admission_semantics(tmp_path):
    port = PublicPort(); values = config(); values.pop('model_context_tokens')
    request = ModelRequest('fixture', 'legacy', ({'role': 'user', 'content': 'data'},), 8)
    broker = load('run_isolated_method').ModelBroker(port, len, values, tmp_path/'calls.json')
    assert broker.complete({'request': asdict(request), 'timeout_seconds': 1})['content'] == '{}'
    assert port.dispatches == 1


def test_parent_admits_and_dispatches_the_same_once_prepared_bytes(tmp_path):
    class Prepared(PublicPort):
        def __init__(self): super().__init__(); self.prepares = 0; self.prepared = None
        def prepare_request(self, request):
            self.prepares += 1
            return SimpleNamespace(body=b'finite prepared bytes', overhead='schema',
                metadata={'scope': 'test'}, audit_record=lambda: {'body': 'finite fixture'})
        def request_overhead(self, request): raise AssertionError('Already prepared overhead must not be recounted')
        def complete_prepared_json(self, request, prepared):
            self.dispatches += 1; self.prepared = prepared
            return ModelResponse('{}', {})
        def complete_json(self, request): raise AssertionError('Do not rebuild a committed request')
    port = Prepared(); request = ModelRequest('fixture', 'diagnosis.rediagnosis', ({'role': 'user', 'content': 'data'},), 8)
    broker = load('run_isolated_method').ModelBroker(port, len, config(), tmp_path/'calls.json')
    assert broker.complete({'request': asdict(request), 'timeout_seconds': 1})['content'] == '{}'
    assert port.prepares == port.dispatches == 1
