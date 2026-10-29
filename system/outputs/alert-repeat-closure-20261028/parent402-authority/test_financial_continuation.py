"""Neutral error/budget guards plus optional real saved-trace admission replay."""
import copy,json,os,sys
from pathlib import Path
import pytest
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'src'),str(Path(__file__).resolve().parents[1]/'scripts')]
import financial_continuation as f
from contractedge.contracts.validation import canonical_digest


def prefix():
 cfg={'base_url':'https://api.deepseek.com','model':'deepseek-flash','max_model_calls':12,'max_rounds':2,'max_total_tokens':2000000,'max_elapsed_seconds':9000}
 rows=[]
 for i in range(4):
  stage='diagnosis.v1' if i==0 else 'changeset.v1'
  request={'model':cfg['model'],'prompt_version':stage,'messages':[{'role':'user','content':'same correction' if i>=2 else str(i)}]}
  c={'model':cfg['model'],'prompt_version':stage,'request':request,'messages':request['messages'],'request_digest':canonical_digest(request),'prompt_tokens_reserved':100,'output_tokens_reserved':100,'elapsed_seconds':i+1}
  if i==2:c['error']='HTTPError: HTTP Error 402: Payment Required'
  else:c.update(response_content='{}',response_metadata={'model_identity_verified':True,'reported_model':cfg['model']})
  rows.append(c)
 return rows,cfg


def test_exact_rejection_remains_reserved():
 rows,cfg=prefix();assert f.validate_prefix(rows,cfg)==(800,4)
 assert 'response_content' not in rows[2] and rows[2]['error'].endswith('Payment Required')


@pytest.mark.parametrize('kind',['timeout','403','received-402','different-correction','unverified-model','wrong-reported-model','negative-reservation','missing-time','exhausted-time','changed-request-digest','extra-call','execution-in-prefix','unofficial-host'])
def test_no_unknown_or_changed_prefix_can_receive_financial_exception(kind):
 rows,cfg=prefix()
 if kind=='timeout':rows[2]['error']='TimeoutError: request outcome unknown'
 if kind=='403':rows[2]['error']='HTTPError: HTTP Error 403: Forbidden'
 if kind=='received-402':rows[2]['response_content']=''
 if kind=='different-correction':rows[3]['request']['messages'][0]['content']='new evidence and a fresh plan'
 if kind=='unverified-model':rows[1]['response_metadata']['model_identity_verified']=False
 if kind=='wrong-reported-model':rows[1]['response_metadata']['reported_model']='another-model'
 if kind=='negative-reservation':rows[2]['prompt_tokens_reserved']=-1
 if kind=='missing-time':rows[1].pop('elapsed_seconds')
 if kind=='exhausted-time':rows[3]['elapsed_seconds']=9000
 if kind=='changed-request-digest':rows[1]['request_digest']='0'*64
 if kind=='extra-call':rows.append(copy.deepcopy(rows[3]))
 if kind=='execution-in-prefix':rows[1]['prompt_version']='topomen-source-execution.v1'
 if kind=='unofficial-host':cfg['base_url']='https://relay.invalid'
 with pytest.raises((ValueError,KeyError)):f.validate_prefix(rows,cfg)


def test_general_case_accounting_never_refills_an_unknown_reservation():
 rows,cfg=prefix();parent={'max_calls':12,'max_tokens':2000000,'max_seconds':9000,'model_calls':4,'resumed_model_calls':4,'reserved_tokens':800,'elapsed_seconds':5}
 assert f.validate_accumulation(rows,[],cfg,parent,parent)==(4,800)
 for key,value in [('model_calls',3),('reserved_tokens',600),('elapsed_seconds',2),('max_calls',13),('max_tokens',2000200)]:
  bad={**parent,key:value}
  with pytest.raises(ValueError):f.validate_accumulation(rows,[],cfg,bad,parent)


@pytest.fixture
def real_panel():
 if os.environ.get('CONTRACTEDGE_REAL_402_REPLAY')!='1':pytest.skip('Real saved official402 inputs are server integration resources, not bundled test fixtures')
 from prepare_financial_panel import build
 return build(Path('/home/xhj/contractedge-fixed46-official-v068-20261028/panel.json'),Path('/home/xhj/contractedge-ack402-continuation-v068-20261028'),Path('/home/xhj/contractedge-fixed46-official-v068-resumed-20261028'))


def test_real_complete_same_source_prefix_and_replay_bind_without_new_call(real_panel):
 panel,rows=real_panel
 assert len(rows)==14 and len(panel['slots'])==46
 assert {r['slot_id'] for r in rows}=={s['slot_id'] for s in panel['slots'] if s['group']=='A'}
 assert rows[-1]['recorded_status']=='INFRASTRUCTURE_STOP'
 assert rows[-1]['evidence_files']['original_receipt']!=rows[-1]['evidence_files']['receipt']


@pytest.mark.parametrize('target,mutation',[
 ('combined_calls','timeout'),('combined_calls','response-for-refusal'),('combined_calls','regenerate-first'),
 ('combined_calls','correction-request'),('new_calls','new-http'),('imported_ready','drop-response'),
 ('continuation_receipt','parent-refund-call'),('continuation_receipt','parent-refund-token'),
 ('continuation_receipt','parent-refund-time'),('continuation_receipt','parent-larger-cap'),
 ('continuation_trace','inner-reset-cap'),('continuation_trace','inner-refund-token'),
 ('continuation_trace','inner-refund-time'),('effective_config','extra-permission'),
 ('projection','lost-failure'),('projection','wrong-index'),('policy_registration','pretend-preregistered'),
 ('policy_registration','outcome-selection'),('failed_selection_trace','different-failure'),
 ('resource_calls','drop-failed-cost'),('queue_state','unknown-queue')])
def test_real_rehashed_bad_evidence_still_rejected(real_panel,monkeypatch,target,mutation):
 panel,_=real_panel;bad=copy.deepcopy(panel);ref=bad['predecessor'][target];real_checked=f.checked;value=copy.deepcopy(real_checked(ref))
 if mutation=='timeout':value[2]['error']='TimeoutError: outcome unknown'
 if mutation=='response-for-refusal':value[2]['response_content']=''
 if mutation=='regenerate-first':value[0]['response_content']='new model response'
 if mutation=='correction-request':value[3]['request']['messages'][0]['content']='resampled request'
 if mutation=='new-http':value.append({'new_request':'not replay'})
 if mutation=='drop-response':value['model_calls'].pop()
 if mutation=='parent-refund-call':value['parent_budget']['model_calls']-=1
 if mutation=='parent-refund-token':value['parent_budget']['reserved_tokens']-=142142
 if mutation=='parent-refund-time':value['parent_budget']['elapsed_seconds']=1
 if mutation=='parent-larger-cap':value['parent_budget']['max_calls']=13
 if mutation=='inner-reset-cap':value['budget']['max_calls']=12
 if mutation=='inner-refund-token':value['budget']['reserved_tokens']-=1
 if mutation=='inner-refund-time':value['budget']['elapsed_seconds']=1
 if mutation=='extra-permission':value['max_rounds']=3
 if mutation=='lost-failure':value['outer_model_calls']=4
 if mutation=='wrong-index':value['transport_rejected_index']=1
 if mutation=='pretend-preregistered':value['prospectively_registered_original_protocol']=True
 if mutation=='outcome-selection':value['outcome_selection_allowed']=True
 if mutation=='different-failure':value['errors']=['RuntimeError: unknown provider timeout']
 if mutation=='drop-failed-cost':value.pop(2)
 if mutation=='unknown-queue':value['status']='DISPATCH_INTENT'
 def injected(r):return copy.deepcopy(value) if r==ref else real_checked(r)
 monkeypatch.setattr(f,'checked',injected)
 with pytest.raises((ValueError,KeyError)):f.financial_predecessor_rows(bad)


@pytest.mark.parametrize('mutation',['drop-old-row','reorder-slot','new-source','new-budget','new-seed','changed-environment'])
def test_administrative_binding_cannot_change_original_design(real_panel,mutation):
 panel,_=real_panel;bad=copy.deepcopy(panel)
 if mutation=='drop-old-row':bad['predecessor']['records']={'path':'/does-not-exist','sha256':'0'*64}
 if mutation=='reorder-slot':bad['slots'][0],bad['slots'][1]=bad['slots'][1],bad['slots'][0]
 if mutation=='new-source':bad['variants']['candidate']['source_sha256']='0'*64
 if mutation=='new-budget':bad['model_call_cap']=13
 if mutation=='new-seed':bad['seeds']={}
 if mutation=='changed-environment':bad['execution_environment']['coordinator_code_files']='0'*64
 with pytest.raises((ValueError,KeyError,OSError)):f.financial_predecessor_rows(bad)


def test_semantic_failure_is_imported_not_retried_or_discarded(real_panel,monkeypatch):
 import score_isolated_method
 panel,_=real_panel;ref=panel['predecessor']['continuation_score'];real_checked=f.checked;raw=real_checked(ref);negative={**raw,'status':'SEMANTIC_FAIL','native_score_status':'SEMANTIC_FAIL'}
 monkeypatch.setattr(f,'checked',lambda r:copy.deepcopy(negative) if r==ref else real_checked(r))
 original=score_isolated_method.run
 terminal=panel['predecessor']['continuation_receipt']['path']
 monkeypatch.setattr(score_isolated_method,'run',lambda receipt,*a,**kw:copy.deepcopy(negative) if str(receipt)==terminal else original(receipt,*a,**kw))
 rows=f.financial_predecessor_rows(panel)
 assert len(rows)==14 and rows[-1]['status']=='SEMANTIC_FAIL'
 from fixed_panel import b_gate
 assert b_gate({**panel,'slots':[s for s in panel['slots'] if s['group']=='A']},rows)['open'] is False


def test_inherited_prefix_is_revalidated_once_per_read_not_once_per_row(tmp_path,monkeypatch):
 import fixed_panel
 rows=[{'slot_id':str(i),'predecessor_panel_sha256':'a'*64} for i in range(14)]
 (tmp_path/'records.json').write_text(json.dumps(rows));calls=[]
 monkeypatch.setattr(fixed_panel,'predecessor_rows',lambda p:(calls.append('fresh') or copy.deepcopy(rows)))
 monkeypatch.setattr(fixed_panel,'verify_record',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('Inherited proof must not be reverified quadratically')))
 assert fixed_panel.verified_records(tmp_path/'panel.json',{'output_root':str(tmp_path)})==rows
 assert calls==['fresh']
 # Each new read obtains its own fresh verification, even if no data changed.
 assert fixed_panel.verified_records(tmp_path/'panel.json',{'output_root':str(tmp_path)})==rows
 assert calls==['fresh','fresh']
 bad=copy.deepcopy(rows);bad[0]['status']='fabricated PASS';(tmp_path/'records.json').write_text(json.dumps(bad))
 with pytest.raises(ValueError):fixed_panel.verified_records(tmp_path/'panel.json',{'output_root':str(tmp_path)})
