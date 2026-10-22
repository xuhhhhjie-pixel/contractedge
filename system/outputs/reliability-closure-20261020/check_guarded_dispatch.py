"""Local controlled dispatch integration checks; no model, Docker or scorer calls."""
import importlib.util,json,tempfile
from pathlib import Path
spec=importlib.util.spec_from_file_location('guarded',Path(__file__).with_name('run_guarded_registered_group.py'));g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
original_root,original_out=g.ROOT,g.OUT
results=[]
for kind in ['candidate_failure','existing_pass_skip','partial_existing','bad_identity','score_missing','public_failure']:
 with tempfile.TemporaryDirectory() as td:
    root=Path(td);g.ROOT=root;g.OUT=root/'out';g.OUT.mkdir();method=root/'method';(method/'src').mkdir(parents=True);(method/'src/m.py').write_text('x=1\n')
    ledger=root/'ledger.json';g.write(ledger,dict(settings=dict(limit_micro_cny=150000000),calls=[]))
    cfg=root/'config.json';g.write(cfg,dict(money_budget=dict(path=str(ledger)),max_model_calls=12,max_output_tokens=100,execution_max_output_tokens=100,review_max_output_tokens=100,max_total_tokens=10000))
    reg=root/'registration.json';g.write(reg,dict(recovery_seed_paths={}))
    freeze=root/'freeze.json';value=dict(version='test',registration_path=str(reg),candidate_root=str(method),baseline_root=str(method),configuration_path=str(cfg),baseline_configuration_path=str(cfg),configuration_hashes={'candidate':g.sha(cfg),'baseline':g.sha(cfg)},files=g.files(method),baseline_source_files=g.files(method),harness_files={},worker_path='unused')
    if kind=='bad_identity':value['files']={}
    g.write(freeze,value);variants=['baseline','candidate','candidate'] if kind=='candidate_failure' else ['candidate'];schedule=[dict(case_id='case'+str(i),repeat=0,variant=v,name='slot'+str(i),output=str(root/('run'+str(i)))) for i,v in enumerate(variants)];g.write(g.OUT/'delivery-test-A-state.json',dict(schedule=schedule,records=[],status='HELD'));started=[]
    if kind in ['existing_pass_skip','partial_existing']:
        output=Path(schedule[0]['output']);output.mkdir();g.write(output/'batch.json',dict(records=[dict(case_id='case0',status='RUNNING' if kind=='partial_existing' else 'DONE')]))
    def worker(command,log):
        output=Path(command[command.index('--output')+1]);case=command[command.index('--case')+1];started.append(case);output.mkdir();row=dict(case_id=case,status='DONE')
        if kind=='public_failure':
            row['trace']='repair.trace.json';g.write(output/'repair.trace.json',dict(final_snapshot_id='S1',last_feedback=dict(to_snapshot='S1',semantic_outcome='PUBLIC_REVIEW_FAIL')))
        g.write(output/'batch.json',dict(records=[row]));return 0
    def scores():
        if kind=='score_missing':return []
        result=[]
        for i,item in enumerate(schedule):
            if (Path(item['output'])/'batch.json').exists():result.append(dict(group='A',case_id=item['case_id'],repeat=0,variant=item['variant'],status='SEMANTIC_FAIL' if kind=='candidate_failure' else 'PASS'))
        return result
    g.dispatch(freeze,'A',worker_run=worker,score_read=scores,pause=lambda _:None,score_timeout=0)
    state=g.read(g.OUT/'delivery-test-A-state.json')
    expected={'candidate_failure':'HELD_CANDIDATE_FAILURE','existing_pass_skip':'COMPLETE','partial_existing':'INCOMPLETE_RUN_REQUIRES_AUDIT','bad_identity':'FROZEN_IDENTITY_MISMATCH','score_missing':'SCORING_INCOMPLETE_REQUIRES_AUDIT','public_failure':'HELD_CANDIDATE_PUBLIC_FAILURE'}[kind]
    assert state['status']==expected,(kind,state)
    assert len(started)==({'candidate_failure':2,'existing_pass_skip':0,'partial_existing':0,'bad_identity':0,'score_missing':1,'public_failure':1}[kind])
    assert not (root/'run2').exists()
    results.append(dict(scenario=kind,controller_status=state['status'],worker_starts=started))
g.ROOT,g.OUT=original_root,original_out
g.write(original_out/'GUARDED_DISPATCH_VERIFICATION.json',dict(api_calls=0,actual_workers_started=0,checks=results,controller_sha256=g.sha(Path(__file__).with_name('run_guarded_registered_group.py'))));print(json.dumps(results))
