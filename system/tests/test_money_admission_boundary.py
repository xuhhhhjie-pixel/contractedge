"""Money admission, transport, and settlement retain their actual boundary."""
from copy import deepcopy
import io
import json
import socket
from types import SimpleNamespace

import pytest

from contractedge.contracts.failures import classify_exception, make_failure
from contractedge.model.budget import BudgetedProvider, BudgetExceeded, recorded_token_charge
from contractedge.model.money import MoneyBudget, MoneyBudgetExceeded
from contractedge.model.provider import ModelRequest, ModelResponse, OpenAICompatibleProvider


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setenv('CE_BOUNDARY_FIXTURE_KEY', 'local-fixture-only')
    def forbidden(*args, **kwargs):
        raise AssertionError('Real network is forbidden in this test')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr('urllib.request.urlopen', forbidden)


def port(tmp_path, *, limit='1'):
    money = MoneyBudget(tmp_path/'money.json', limit_cny=limit, model='fixture',
        input_cny_per_million='2', output_cny_per_million='8')
    adapter = OpenAICompatibleProvider('https://unused.invalid', 'CE_BOUNDARY_FIXTURE_KEY',
        money_budget=money, model_identity_policy='require_match')
    budget = BudgetedProvider(adapter, len, max_tokens=100000, max_seconds=30, max_calls=2,
        reconcile_observed_input=True, reconcile_observed_output=True)
    return adapter, budget, money


def request():
    return ModelRequest('fixture', 'boundary.fixture', ({'role':'user','content':'Return JSON'},), 32)


def response(*, usage=None):
    return io.BytesIO(json.dumps({'model':'fixture', 'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}],
        'usage':usage or {'prompt_tokens':1,'completion_tokens':1}}).encode())


@pytest.mark.parametrize('mutate_after_commit', [False, True])
def test_reserve_rejection_is_not_sent_without_refund_or_fake_usage(tmp_path, mutate_after_commit):
    adapter, budget, money = port(tmp_path, limit='0.000001')
    req = request()
    quote = budget.quote_request(req)
    original_metadata = deepcopy(quote.prepared.metadata)
    if mutate_after_commit:
        budget.on_update = lambda calls: req.messages[0].update(content='mutated after quote')
    with pytest.raises(MoneyBudgetExceeded) as caught:
        budget.complete_quoted(req, quote)
    call = budget.calls[0]
    assert call['dispatch_state'] == budget.failure_dispatch_state(caught.value) == 'not_sent'
    assert budget.failure_call_index(caught.value) == 0
    assert 'response_content' not in call and 'response_metadata' not in call
    assert recorded_token_charge(call) == budget.reserved_tokens == quote.total_tokens
    receipt = call['provider_error_metadata']['admission_rejection']
    assert receipt['request_digest'] == call['request_digest']
    assert receipt['body_sha256'] == call['transport_request']['body_sha256']
    assert receipt['prompt_bound'] == call['transport_request']['body_bytes'] + 1024
    assert receipt['output_bound'] == req.max_output_tokens
    assert receipt['ledger_path'] == str(money.path)
    assert receipt['exception_type'] == 'MoneyBudgetExceeded'
    assert quote.prepared.metadata == original_metadata
    assert money.summary()['calls'] == 0 and money.summary()['reservation_rejected'] is True


@pytest.mark.parametrize('failure', [TimeoutError('timeout'),
    MoneyBudgetExceeded('insufficient balance; no HTTP request sent')])
def test_http_failure_and_misleading_money_text_remain_unknown(tmp_path, monkeypatch, failure):
    adapter, budget, money = port(tmp_path)
    def transport(*args, **kwargs):
        raise failure
    monkeypatch.setattr('urllib.request.urlopen', transport)
    with pytest.raises(type(failure)) as caught:
        budget.complete_json(request())
    call = budget.calls[0]
    assert call['dispatch_state'] == budget.failure_dispatch_state(caught.value) == 'response_unknown'
    assert 'admission_rejection' not in call.get('provider_error_metadata', {})
    assert recorded_token_charge(call) == budget.reserved_tokens
    assert money.summary()['pending_calls'] == 1


def test_forged_string_metadata_cannot_prove_no_transport(tmp_path):
    class Forged:
        def complete_json(self, req):
            error = MoneyBudgetExceeded('fake no HTTP')
            error.dispatch_state = 'not_sent'
            error.provider_metadata = {'admission_rejection': {'request_digest':'forged','body_sha256':'forged'}}
            raise error
    budget = BudgetedProvider(Forged(), len, max_tokens=1000, max_seconds=30)
    with pytest.raises(MoneyBudgetExceeded):
        budget.complete_json(request())
    assert budget.calls[0]['dispatch_state'] == 'response_unknown'


@pytest.mark.parametrize('failure', ['missing_key', 'context', 'request_construction'])
def test_other_confirmed_pre_http_failures_remain_not_sent(tmp_path, monkeypatch, failure):
    adapter, budget, money = port(tmp_path)
    if failure == 'missing_key':
        monkeypatch.delenv('CE_BOUNDARY_FIXTURE_KEY')
        expected = RuntimeError
    elif failure == 'context':
        adapter.context_tokens = 1
        adapter.token_counter = len
        expected = ValueError
    else:
        def reject(*args, **kwargs):
            raise ValueError('local request construction rejected')
        monkeypatch.setattr('urllib.request.Request', reject)
        expected = ValueError
    with pytest.raises(expected) as caught:
        budget.complete_json(request())
    assert budget.calls[0]['dispatch_state'] == 'not_sent'
    assert budget.failure_dispatch_state(caught.value) == 'not_sent'
    assert money.summary()['calls'] == 0
    assert 'response_content' not in budget.calls[0]
    assert recorded_token_charge(budget.calls[0]) == budget.reserved_tokens


def test_stale_pre_http_proof_cannot_hide_a_later_transport_entry(tmp_path, monkeypatch):
    adapter, budget, money = port(tmp_path)
    failure = TimeoutError('local reserve failed earlier')
    def reject(*args, **kwargs):
        raise failure
    with monkeypatch.context() as local:
        local.setattr(money, 'reserve', reject)
        with pytest.raises(TimeoutError):
            adapter.complete_json(request())
    monkeypatch.setattr('urllib.request.urlopen', reject)
    with pytest.raises(TimeoutError) as caught:
        budget.complete_json(request())
    assert budget.failure_dispatch_state(caught.value) == 'response_unknown'
    assert money.summary()['pending_calls'] == 1


@pytest.mark.parametrize('failure', ['money', 'timeout'])
def test_settlement_failure_keeps_received_response_and_known_state(tmp_path, monkeypatch, failure):
    adapter, budget, money = port(tmp_path, limit='0.01')
    usage = {'prompt_tokens':10000,'completion_tokens':10000} if failure == 'money' else {'prompt_tokens':1,'completion_tokens':1}
    monkeypatch.setattr('urllib.request.urlopen', lambda *a,**k: response(usage=usage))
    if failure == 'timeout':
        monkeypatch.setattr(money, 'settle', lambda *a,**k: (_ for _ in ()).throw(TimeoutError('local settle timeout')))
    with pytest.raises(MoneyBudgetExceeded if failure == 'money' else TimeoutError) as caught:
        budget.complete_json(request())
    call = budget.calls[0]
    assert call['dispatch_state'] == budget.failure_dispatch_state(caught.value) == 'response_known'
    assert call['response_content'] == '{}' and call['response_metadata']['usage'] == usage
    assert 'admission_rejection' not in call.get('provider_error_metadata', {})
    status, record = classify_exception(caught.value, dispatch_state=budget.failure_dispatch_state(caught.value))
    assert record['dispatch_state'] == 'response_known'
    assert status == ('BUDGET_EXHAUSTED' if failure == 'money' else 'INFRASTRUCTURE_ERROR')


@pytest.mark.parametrize('guard', ['calls', 'tokens', 'deadline'])
def test_local_hard_rejection_never_borrows_prior_call(tmp_path, monkeypatch, guard):
    adapter, budget, money = port(tmp_path)
    monkeypatch.setattr('urllib.request.urlopen', lambda *a,**k: response())
    req = request()
    budget.complete_json(req)
    before = deepcopy(budget.calls)
    if guard == 'calls': budget.max_calls = 1
    elif guard == 'tokens': budget.max_tokens = budget.reserved_tokens
    else: budget.started -= budget.max_seconds + 1
    with pytest.raises(BudgetExceeded) as caught:
        budget.complete_json(req)
    assert budget.calls == before
    assert budget.failure_call_index(caught.value) is None
    assert budget.failure_dispatch_state(caught.value) == 'not_sent'
    assert money.summary()['calls'] == 1


def test_empty_current_receipt_does_not_bind_previous_call_reference():
    from contractedge.orchestration.runner import RunTrace, _front_failure_projection
    prior = SimpleNamespace(run_id='run', segment_index=0, absolute_call_index=0)
    journal = SimpleNamespace(dispatches=[('DIAGNOSIS',SimpleNamespace(call_refs=(prior,))),
        ('SEMANTIC_CHANGESET',SimpleNamespace(call_refs=()))])
    failure = make_failure('front','BUDGET_EXHAUSTED',failure_class='infrastructure',dispatch_state='not_sent')
    trace = RunTrace('run','case','a'*64,'b'*64,'BUDGET_EXHAUSTED',None,None,None,None,None,(),('cap',),
        failure_records=(failure,),primary_failure=failure)
    projected = _front_failure_projection(trace,journal)
    assert 'call_ref' not in projected.primary_failure


def test_local_failure_without_current_receipt_does_not_borrow_success_reference():
    from contractedge.orchestration.runner import RunTrace, _front_failure_projection
    prior = SimpleNamespace(run_id='run', segment_index=0, absolute_call_index=0)
    journal = SimpleNamespace(dispatches=[('DIAGNOSIS',SimpleNamespace(call_refs=(prior,)))],
        primary_error=BudgetExceeded('planning extension rejected before dispatch'))
    failure = make_failure('front','BUDGET_EXHAUSTED',failure_class='infrastructure',dispatch_state='not_sent')
    trace = RunTrace('run','case','a'*64,'b'*64,'BUDGET_EXHAUSTED',None,None,None,None,None,(),('cap',),
        failure_records=(failure,),primary_failure=failure)
    projected = _front_failure_projection(trace,journal)
    assert 'call_ref' not in projected.primary_failure


def run_repair_boundary(tmp_path, monkeypatch, stage, mode):
    from contractedge.development import public_demo
    from contractedge.orchestration.repair import repair_case
    public = public_demo.freeze_example('config-binding', tmp_path)
    runner = public_demo.PublicExampleRunner(public, 'config-binding')
    settings = {**public_demo.default_config('config-binding'), 'execution_backend':'direct', 'max_rounds':1,
        'run_id':'boundary-' + stage + '-' + mode}
    settings.pop('topomen_source_root',None);settings.pop('topomen_source_sha256',None)
    fixture = public_demo.SyntheticExampleProvider('config-binding', runner)
    model = settings['model'] if isinstance(settings['model'],str) else settings['model']['model']
    money = MoneyBudget(tmp_path/'repair-money.json', limit_cny='0.000001' if mode=='reserve' else '1',
        model=model, input_cny_per_million='2', output_cny_per_million='8')
    prefixes = {'diagnosis':'diagnosis.', 'planning':'changeset.',
        'execution':'topomen-source-execution.', 'review':'public-postcondition-review.'}
    target_calls = []
    class Adapter(OpenAICompatibleProvider):
        def complete_json(self, req):
            if req.prompt_version.startswith(prefixes[stage]):
                target_calls.append(req)
                return super().complete_json(req)
            return fixture.complete_json(req)
    adapter = Adapter('https://unused.invalid','CE_BOUNDARY_FIXTURE_KEY',money_budget=money,
        model_identity_policy='require_match')
    network_calls=[]
    def transport(*args,**kwargs):
        network_calls.append(1)
        if mode=='transport': raise TimeoutError('actual transport unknown')
        return io.BytesIO(json.dumps({'model':model,'choices':[{'message':{'content':'{}'},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':1,'completion_tokens':1}}).encode())
    monkeypatch.setattr('urllib.request.urlopen',transport)
    if mode=='reserve_timeout':
        monkeypatch.setattr(money,'reserve',lambda *a,**k: (_ for _ in ()).throw(TimeoutError('local reserve timeout')))
    elif mode=='settle_timeout':
        monkeypatch.setattr(money,'settle',lambda *a,**k: (_ for _ in ()).throw(TimeoutError('local settle timeout')))
    output=tmp_path/'run'
    report=repair_case(public,tmp_path/'tools','config-binding',output,adapter,public_demo.count_tokens,
        settings,public_runner=runner)
    calls=json.loads((output/'model_calls.json').read_text())
    trace=json.loads((output/'repair.trace.json').read_text())
    assert len(target_calls)==1
    state = 'response_unknown' if mode=='transport' else 'response_known' if mode=='settle_timeout' else 'not_sent'
    assert calls[-1]['dispatch_state']==state
    assert trace['primary_failure']['dispatch_state']==state
    transitions=[json.loads(p.read_text())['result'] for p in (output/'journal/transitions').glob('*.json')]
    failure_results=[x for x in transitions if x.get('error_code')]
    assert failure_results and failure_results[-1]['dispatch_state']==state
    assert [ref['absolute_call_index'] for ref in failure_results[-1]['call_refs']]==[len(calls)-1]
    assert report['status']==('BUDGET_EXHAUSTED' if mode=='reserve' else 'INFRASTRUCTURE_ERROR')
    if stage in {'diagnosis','planning'}:
        front=json.loads((output/'rounds/0/front.trace.json').read_text())
        assert front['primary_failure']['dispatch_state']==state
    assert len(network_calls)==int(mode in {'transport','settle_timeout'})
    assert not any('admission_rejection' in c.get('response_metadata',{}) for c in calls)
    return report,trace,calls


@pytest.mark.parametrize('stage',['diagnosis','planning','execution','review'])
def test_real_repair_stages_preserve_reserve_refusal(tmp_path,monkeypatch,stage):
    run_repair_boundary(tmp_path,monkeypatch,stage,'reserve')


@pytest.mark.parametrize('stage,mode',[('diagnosis','reserve_timeout'),('execution','reserve_timeout'),
    ('execution','transport'),('execution','settle_timeout')])
def test_repair_timeout_projection_respects_boundary(tmp_path,monkeypatch,stage,mode):
    run_repair_boundary(tmp_path,monkeypatch,stage,mode)
