"""Durable parent dispatch and grading use actual persisted terminal artifacts.

Only the external method subprocess is replaced. Existing independent scoring
and finalization run against a finite public fixture tool, never a model.
"""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import os
import signal
import subprocess
import time

import pytest

from tests.test_fixed_panel import fixture, slot_identity


def load():
    scripts=Path(__file__).resolve().parents[1]/'scripts';path=scripts/'run_fixed_panel.py'
    assert path.is_file(), 'Durable isolated panel driver is not implemented'
    sys.path.insert(0,str(scripts))
    spec=importlib.util.spec_from_file_location('fixed_panel_driver_test',path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    return m


class MethodTransport:
    def __init__(self,panel,path,slot,*,candidate=True):
        self.panel,self.path,self.slot,self.candidate=panel,path,slot,candidate
        self.dispatches=0;self.grader_calls=0;self.fail_after_grade=False

    def identity(self,args):
        tools=Path(self.panel['benchmark_root'])/'tools'
        tool_files={p.relative_to(tools).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in tools.rglob('*') if p.is_file() and p.suffix in {'.py','.json'}}
        return {**slot_identity(self.panel,self.slot),
                'panel_registration_sha256':hashlib.sha256(self.path.read_bytes()).hexdigest(),
                'panel_slot_id':self.slot['slot_id'],'public_release_files':{},'public_tool_files':tool_files}

    def dispatch(self,command,log):
        self.dispatches+=1
        assert '--execute' in command and command[command.index('--panel-slot')+1]==self.slot['slot_id']
        root=Path(self.panel['output_root']);case=root/'methods'/self.slot['slot_id']/self.slot['case_id'];case.mkdir(parents=True)
        state=case.parent/'.isolated-fixture';(state/'method-journal').mkdir(parents=True)
        write=lambda p,v:p.write_text(json.dumps(v))
        identity=self.identity(None)
        candidate=None
        if self.candidate:
            patch=case/'candidate.patch';patch.write_text('finite public fixture patch')
            candidate={'patch_path':str(patch),'patch_bytes':patch.stat().st_size,'patch_sha256':hashlib.sha256(patch.read_bytes()).hexdigest()}
        write(case/'repair.trace.json',{'case_id':self.slot['case_id'],'status':'PUBLIC_REVIEW_COMPLETE',
            'candidate':candidate,'final_snapshot_id':'S1','last_feedback':{'semantic_outcome':'PUBLIC_REVIEW_PASS'}})
        write(state/'method-journal/export.json',{'status':'DONE','op':'public.export','result':candidate})
        write(state/'receipt.json',{'case_id':self.slot['case_id'],'identity':identity,
            'state_root':str(state),'status':'METHOD_COMPLETE_PENDING_INDEPENDENT_SCORING','method_returncode':0,
            'cleanup':{'status':'OWNED_CONTAINERS_CLOSED'},'terminal_validation':{'classification':'METHOD_RESULT','candidate_verified':self.candidate},
            'counts_in_46_slot_panel':True,'panel_registration_sha256':identity['panel_registration_sha256'],'panel_slot':self.slot})
        return 0

    def score(self,receipt,benchmark,out,*,phase):
        from score_isolated_method import run
        self.grader_calls+=1
        if self.fail_after_grade:
            run(receipt,benchmark,out,timeout=10,phase='run');self.fail_after_grade=False
            raise RuntimeError('Parent stopped after completed score, before aggregation')
        return run(receipt,benchmark,out,timeout=10,phase=phase)


def prepared(tmp_path,*,candidate=True):
    panel,path,_=fixture(tmp_path);panel['runtime']={'python':sys.executable,'dependency_root':str(tmp_path/'deps')}
    bench=Path(panel['benchmark_root']);(bench/'tools').mkdir();(bench/'evaluation').mkdir()
    manifest=bench/'evaluation/manifest.jsonl';manifest.write_text('finite independent fixture registry')
    entry=bench/'RELEASE_ENTRY.json';entry.write_text(json.dumps({'evaluation_manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest()}))
    panel['data_files']['RELEASE_ENTRY.json']=hashlib.sha256(entry.read_bytes()).hexdigest()
    tool=bench/'tools/score_case.py';tool.write_text('''import argparse,json,hashlib
from pathlib import Path
p=argparse.ArgumentParser()
for k in ['evaluation-root','case-id','patch','output']:p.add_argument('--'+k,required=True)
a=p.parse_args();o=Path(a.output);o.mkdir(parents=True)
(o/'score.json').write_text(json.dumps({'case_id':a.case_id,'patch_sha256':hashlib.sha256(Path(a.patch).read_bytes()).hexdigest(),'status':'PASS'}))
''')
    tools={'score_case.py':hashlib.sha256(tool.read_bytes()).hexdigest()}
    panel['execution_environment']['public_tool_files']=hashlib.sha256(json.dumps(tools,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    path.write_text(json.dumps(panel));slot=panel['slots'][0]
    return panel,path,slot,MethodTransport(panel,path,slot,candidate=candidate)


def test_completed_slot_is_verified_and_never_dispatches_again(tmp_path):
    panel,path,slot,transport=prepared(tmp_path);m=load()
    result=m.run_slot(path,slot['slot_id'],tmp_path/'unused-key',transport=transport)
    assert result['status']=='PASS' and result['terminal_verified']
    assert result['counts_in_46_slot_panel'] is True
    assert m.run_slot(path,slot['slot_id'],tmp_path/'unused-key',transport=transport)==result
    assert transport.dispatches==1 and transport.grader_calls==1
    persisted=json.loads((Path(panel['output_root'])/'records.json').read_text())
    assert len(persisted)==1 and persisted[0]['slot_id']==slot['slot_id']


def test_no_candidate_is_recorded_without_invoking_grader(tmp_path):
    panel,path,slot,transport=prepared(tmp_path,candidate=False);m=load()
    def forbidden(*a,**k):raise AssertionError('No patch means no grader dispatch')
    transport.score=forbidden
    result=m.run_slot(path,slot['slot_id'],tmp_path/'unused-key',transport=transport)
    assert result['status']=='NO_CANDIDATE' and result['terminal_verified']
    assert transport.dispatches==1


def test_uncertain_dispatch_cannot_be_restarted_by_controller(tmp_path):
    panel,path,slot,transport=prepared(tmp_path);m=load()
    def interrupted(*a,**k):raise RuntimeError('Exit during subprocess admission')
    transport.dispatch=interrupted
    with pytest.raises(RuntimeError):m.run_slot(path,slot['slot_id'],tmp_path/'unused-key',transport=transport)
    with pytest.raises(ValueError,match='uncertain'):
        m.run_slot(path,slot['slot_id'],tmp_path/'unused-key',transport=transport)


def test_completed_grading_finalizes_after_parent_exit_without_method_retry(tmp_path):
    panel,path,slot,transport=prepared(tmp_path);m=load();transport.fail_after_grade=True
    with pytest.raises(RuntimeError):m.run_slot(path,slot['slot_id'],tmp_path/'unused-key',transport=transport)
    result=m.run_slot(path,slot['slot_id'],tmp_path/'unused-key',transport=transport)
    assert result['status']=='PASS' and transport.dispatches==1
    assert transport.grader_calls==2 # all, then read-only finalize


def test_handwritten_terminal_flags_cannot_open_B_dispatch(tmp_path):
    from tests.test_fixed_panel import records,load as core
    panel,path,_,transport=prepared(tmp_path);m=load()
    root=Path(panel['output_root']);root.mkdir();(root/'records.json').write_text(json.dumps(records(panel)))
    slot=next(s for s in panel['slots'] if s['group']=='B')
    with pytest.raises((ValueError,KeyError)):
        m.run_slot(path,slot['slot_id'],tmp_path/'unused-key',transport=transport)
    assert transport.dispatches==0


def test_rehashed_cached_PASS_cannot_replace_a_real_failure(tmp_path):
    panel,path,slot,transport=prepared(tmp_path,candidate=False);m=load()
    m.run_slot(path,slot['slot_id'],tmp_path/'unused-key',transport=transport)
    target=Path(panel['output_root'])/'records.json';rows=json.loads(target.read_text());rows[0]['status']='PASS';rows[0]['independent_status']='PASS';target.write_text(json.dumps(rows))
    with pytest.raises(ValueError):m.run_slot(path,slot['slot_id'],tmp_path/'unused-key',transport=transport)
    assert transport.dispatches==1


def test_pair_budget_counts_uncertain_reservations_and_reserves_both_variants(tmp_path):
    panel,path,slot,transport=prepared(tmp_path);m=load();ledger=tmp_path/'money.json'
    money={'path':str(ledger),'limit_cny':0.01,'input_cny_per_million':'2','output_cny_per_million':'8'}
    for variant in panel['variants'].values():
        config=json.loads(Path(variant['config']).read_text());config.update(money_budget=money,
            max_total_tokens=1000,max_output_tokens=10,execution_max_output_tokens=10,review_max_output_tokens=10)
        Path(variant['config']).write_text(json.dumps(config))
    pair=[s for s in panel['slots'] if s['group']==slot['group'] and s['case_id']==slot['case_id']]
    ledger.write_text(json.dumps({'settings':{'limit_micro_cny':10000},'calls':[{'charged_micro_cny':4000,'reserved_micro_cny':6000}]}))
    assert m.pair_budget(panel,pair)=={'admitted':True,'required_micro_cny':5440,'remaining_micro_cny':6000}
    ledger.write_text(json.dumps({'settings':{'limit_micro_cny':10000},'calls':[{'reserved_micro_cny':5000}]}))
    assert m.pair_budget(panel,pair)['admitted'] is False


def test_closed_scored_slot_retires_only_its_reproducible_public_workspace(tmp_path):
    panel,path,slot,transport=prepared(tmp_path);m=load()
    panel['transient_workspace_policy']='RETIRE_CLOSED_SCORED_PUBLIC_WORKSPACE'
    path.write_text(json.dumps(panel));dispatch=transport.dispatch
    def wrapped(command,log):
        result=dispatch(command,log)
        root=Path(panel['output_root'])/'methods'/slot['slot_id']
        workspace=root/'.isolated-fixture/producer/workspace';workspace.mkdir(parents=True)
        (workspace/'reconstructible-source').write_text('public source/cache only')
        logs=root/'.isolated-fixture/producer/observations';logs.mkdir();(logs/'public.log').write_text('keep actual observations')
        foreign=root/'.foreign-state';foreign.mkdir();(foreign/'keep').write_text('not owned')
        return result
    transport.dispatch=wrapped
    record=m.run_slot(path,slot['slot_id'],tmp_path/'unused-key',transport=transport)
    root=Path(panel['output_root'])/'methods'/slot['slot_id']
    assert not (root/'.isolated-fixture/producer/workspace').exists()
    assert (root/'.isolated-fixture/producer/observations/public.log').is_file()
    assert (root/'.foreign-state/keep').is_file()
    assert (root/'.isolated-fixture/method-journal/export.json').is_file()
    assert m.run_slot(path,slot['slot_id'],tmp_path/'unused-key',transport=transport)==record
    assert transport.dispatches==1


def test_workspace_retirement_refuses_a_foreign_symlink_target(tmp_path):
    panel,path,slot,transport=prepared(tmp_path);m=load()
    panel['transient_workspace_policy']='RETIRE_CLOSED_SCORED_PUBLIC_WORKSPACE';path.write_text(json.dumps(panel))
    dispatch=transport.dispatch;foreign=tmp_path/'foreign';foreign.mkdir();(foreign/'keep').write_text('keep')
    def wrapped(command,log):
        result=dispatch(command,log)
        producer=Path(panel['output_root'])/'methods'/slot['slot_id']/'.isolated-fixture/producer';producer.mkdir()
        (producer/'workspace').symlink_to(foreign,target_is_directory=True)
        return result
    transport.dispatch=wrapped
    with pytest.raises(ValueError,match='workspace'):
        m.run_slot(path,slot['slot_id'],tmp_path/'unused-key',transport=transport)
    assert (foreign/'keep').read_text()=='keep'


def test_unresolved_grading_stops_queue_before_dispatching_another_method(tmp_path,monkeypatch):
    panel,path,slot,transport=prepared(tmp_path);m=load()
    tool=Path(panel['benchmark_root'])/'tools/score_case.py';tool.write_text(tool.read_text().replace("'status':'PASS'","'status':'UNKNOWN'"))
    mapping={'score_case.py':hashlib.sha256(tool.read_bytes()).hexdigest()}
    panel['execution_environment']['public_tool_files']=hashlib.sha256(json.dumps(mapping,sort_keys=True,separators=(',',':')).encode()).hexdigest();path.write_text(json.dumps(panel))
    monkeypatch.setattr(m,'Transport',lambda:transport)
    monkeypatch.setattr(m,'pair_budget',lambda *a:{'admitted':True})
    assert m.run_panel(path,'A',tmp_path/'unused-key')==2
    assert transport.dispatches==1
    assert json.loads((Path(panel['output_root'])/'state.json').read_text())['status']=='SCORING_INFRASTRUCTURE_STOP'


def test_controller_interrupt_allows_owned_method_to_record_cleanup(tmp_path):
    scripts=Path(__file__).resolve().parents[1]/'scripts';ready=tmp_path/'ready';clean=tmp_path/'closed'
    child=tmp_path/'child.py';child.write_text('''import signal,sys
from pathlib import Path
def close(*args):Path(sys.argv[2]).write_text('closed');raise SystemExit(0)
signal.signal(signal.SIGTERM,close)
Path(sys.argv[1]).write_text('ready')
while True:signal.pause()
''')
    helper=tmp_path/'parent.py';helper.write_text('''import sys
from pathlib import Path
from run_fixed_panel import Transport
Transport().dispatch([sys.executable,sys.argv[1],sys.argv[2],sys.argv[3]],Path(sys.argv[4]))
''')
    process=subprocess.Popen([sys.executable,str(helper),str(child),str(ready),str(clean),str(tmp_path/'method.log')],
        env={**os.environ,'PYTHONPATH':str(scripts),'PYTHONDONTWRITEBYTECODE':'1'},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        deadline=time.monotonic()+10
        while not ready.exists() and time.monotonic()<deadline and process.poll() is None:time.sleep(.01)
        assert ready.exists()
        os.kill(process.pid,signal.SIGINT);process.wait(timeout=10)
        assert clean.read_text()=='closed'
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=5)


@pytest.mark.parametrize('variant',['baseline','candidate'])
def test_public_failure_blocks_admission_without_rewriting_independent_metric(tmp_path,variant):
    panel,path,_,transport=prepared(tmp_path);m=load()
    slot=next(s for s in panel['slots'] if s['group']=='A' and s['variant']==variant);transport.slot=slot
    dispatch=transport.dispatch
    def wrapped(command,log):
        result=dispatch(command,log)
        trace=Path(panel['output_root'])/'methods'/slot['slot_id']/slot['case_id']/'repair.trace.json'
        body=json.loads(trace.read_text());body['last_feedback']['semantic_outcome']='PUBLIC_REVIEW_FAIL';trace.write_text(json.dumps(body))
        return result
    transport.dispatch=wrapped
    result=m.run_slot(path,slot['slot_id'],tmp_path/'unused-key',transport=transport)
    assert result['status']=='PASS' and result['independent_status']=='PASS'
    assert result['public_decision']=='FAIL'
