"""Replay the actual Cert diagnosis boundary; stop at prepared transport, zero HTTP."""
from dataclasses import asdict
import json,sys
from pathlib import Path
SYSTEM=Path(__file__).resolve().parents[2];OUT=Path(__file__).parent
sys.path[:0]=[str(SYSTEM/'src')]
from contractedge.cli import _build_provider
from contractedge.orchestration.runner import run_front_half
from contractedge.model.admission import quote_request
from contractedge.source.snapshot import compute_snapshot

class Captured(BaseException):pass

def main():
    case=Path('/home/xhj/contractedge-control-source060-v7-20261027/methods/RW-CERTMANAGER-9116/RW-CERTMANAGER-9116')
    state=next(case.parent.glob('.isolated-*'))
    source=next((state/'producer/workspace/source').glob('*/pkg/controller/certificates/issuing/issuing_controller.go')).parents[4]
    # source is .../source/source; use the same workspace relation as repair_case.
    actual=state/'producer/workspace/source/source'
    source=actual
    read=lambda p:json.loads(Path(p).read_text())
    method=read(case/'method_input.json');config=read(case/'rounds/0/front.config.json')
    original=read(case/'model_calls.json')[0]
    observations=read(case/'rounds/0/front.public_observations.json')
    real,counter=_build_provider(read(SYSTEM/'configs/fixed46-candidate-v060-budget150.json'))
    before=real.money_budget.summary();initial=compute_snapshot(source).snapshot_id
    class Capture:
        def prepare_request(self,request):return real.prepare_request(request)
        def complete_prepared_json(self,request,prepared):
            self.request,self.prepared=request,prepared
            raise Captured()
        def complete_json(self,request):raise AssertionError('Unprepared dispatch is forbidden')
    provider=Capture()
    try:run_front_half(method,source.parent,provider,config,counter,public_observations=observations)
    except Captured:pass
    else:raise AssertionError('No actual diagnosis request was captured')
    request=provider.request;wire=json.loads(provider.prepared.body)
    payload=json.loads(wire['messages'][1]['content'])
    texts='\n'.join(m['content'] for m in wire['messages'])
    assert 'PublicKeyMatchesCSR(pk.Public(), csr)' in texts
    assert 'CertificateRequestIsDenied(req)' in texts
    assert 'CertificateRequestHasInvalidRequest(req)' in texts
    quote=quote_request(real,request,counter)
    assert quote.total_tokens<=config['initial_diagnosis_context_tokens']
    assert real.money_budget.summary()==before and compute_snapshot(source).snapshot_id==initial
    record=dict(status='ACTUAL_CERT_INITIAL_DIAGNOSIS_ORDER_SOURCE_DELIVERY_PASS',new_api_calls=0,
        source_snapshot=initial,original_compare_present='PublicKeyMatchesCSR(' in original['messages'][1]['content'],
        compare_now_present=True,terminal_handlers_now_present=True,full_request_bound=quote.total_tokens,
        initial_context_cap=config['initial_diagnosis_context_tokens'],selected_ids=payload['candidate_set']['selected_ids'],
        transport_body_verified=wire['messages'][1]['content']==request.messages[1]['content'],formal_ready=False)
    for name,value in [('ORDER_SOURCE_REPLAY.json',record),('ordering-diagnosis.request.json',asdict(request))]:
        (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(record))

if __name__=='__main__':main()
