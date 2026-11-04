"""Recover a closed classifier error without changing or resampling a method."""
import copy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from tests.test_fixed_panel import load as core
from tests.test_fixed_panel_driver import prepared, MethodTransport, load as driver


def terminal_fixture(tmp_path, chained=False):
    scripts=str(Path(__file__).resolve().parents[1]/'scripts')
    if scripts not in sys.path:sys.path.insert(0,scripts)
    from score_isolated_method import run as score
    panel,path,slot,_=prepared(tmp_path)
    legacy=tmp_path/'legacy';legacy.mkdir()
    program=legacy/'score_isolated_method.py'
    program.write_bytes((Path(__file__).parent/'fixtures/isolated_score_v1.py').read_bytes())
    terminal=legacy/'run_isolated_method.py'
    terminal.write_bytes((Path(__file__).parent/'fixtures/isolated_method_pre_gap_v2.py').read_bytes())
    workers={'run_isolated_method.py':core().sha(terminal)}
    panel['harness_files'].update({str(p):core().sha(p) for p in [program,terminal]})
    panel['execution_environment']['harness_files']=core().digest(workers)
    tool=Path(panel['benchmark_root'])/'tools/score_case.py'
    text=tool.read_text().replace("'status':'PASS'","'status':'PASS' if Path(a.patch).read_bytes() else 'SEMANTIC_FAIL'")
    tool.write_text(text+'\nraise SystemExit(0 if Path(a.patch).read_bytes() else 1)\n')
    panel['execution_environment']['public_tool_files']=core().digest({'score_case.py':core().sha(tool)})
    path.write_text(json.dumps(panel))
    class Bound(MethodTransport):
        def identity(self,args):return {**super().identity(args),'harness_files':workers}
    transport=Bound(panel,path,slot)
    transport.dispatch(['python','--execute','--panel-slot',slot['slot_id']],tmp_path/'unused')
    root=Path(panel['output_root']);receipt=root/'methods'/slot['slot_id']/'.isolated-fixture/receipt.json'
    spec=importlib.util.spec_from_file_location('legacy_terminal_fixture_score',program)
    oldscore=importlib.util.module_from_spec(spec);spec.loader.exec_module(oldscore)
    result=oldscore.run(receipt,panel['benchmark_root'],root/'independent'/slot['slot_id'])
    refs={'receipt':receipt,'trace':receipt.parent.parent/slot['case_id']/'repair.trace.json',
          'score':root/'independent'/slot['slot_id']/'result.json'}
    rows=[{**slot,'status':result['status'],'independent_status':result['status'],'public_decision':'PASS',
        'method_status':'PUBLIC_REVIEW_COMPLETE','terminal_verified':True,'counts_in_46_slot_panel':True,
        'formal_ready':False,'identity_sha256':core().digest(transport.identity(None)),
        'evidence_files':{k:{'path':str(p),'sha256':core().sha(p)} for k,p in refs.items()}}]
    (root/'records.json').write_text(json.dumps(rows))
    if chained:
        intermediate=copy.deepcopy(panel);intermediate['output_root']=str(tmp_path/'runs-scoring-r2')
        r2=tmp_path/'r2/score_isolated_method.py';r2.parent.mkdir()
        r2.write_bytes((Path(__file__).parent/'fixtures/isolated_score_r2.py').read_bytes())
        intermediate['harness_files'].pop(str(program));intermediate['harness_files'][str(r2)]=core().sha(r2)
        intermediate['predecessor']={'schema_version':'ce.fixed_panel_score_parent_amendment.v1',
            'panel':{'path':str(path),'sha256':core().sha(path)},
            'records':{'path':str(root/'records.json'),'sha256':core().sha(root/'records.json')},
            'scoring_program':{'path':str(program),'sha256':core().sha(program)},
            'worker_files_before':workers,'worker_files_after':workers,'interpretation_policy':'ce.frozen_score_exit.v1'}
        midpath=tmp_path/'panel-r2.json';midpath.write_text(json.dumps(intermediate))
        rows=core().verified_records(midpath);root=Path(intermediate['output_root']);root.mkdir()
        (root/'records.json').write_text(json.dumps(rows));panel,path,program=intermediate,midpath,r2
    slot=panel['slots'][1];transport=Bound(panel,path,slot)
    transport.dispatch(['python','--execute','--panel-slot',slot['slot_id']],tmp_path/'unused-2')
    receipt=root/'methods'/slot['slot_id']/'.isolated-fixture/receipt.json';raw=json.loads(receipt.read_text())
    case=receipt.parent.parent/slot['case_id'];patch=case/'candidate.patch';patch.write_bytes(b'')
    candidate={'patch_path':str(patch),'patch_sha256':core().sha(patch),'patch_bytes':0}
    trace={'case_id':slot['case_id'],'status':'EVIDENCE_GAP','candidate':candidate,
        'initial_snapshot_id':'S1','final_snapshot_id':'S1','errors':[],'rounds':[]}
    (case/'repair.trace.json').write_text(json.dumps(trace))
    raw.update(status='INFRASTRUCTURE_STOP',method_source_unchanged=True,
        terminal_validation={'classification':'INFRASTRUCTURE_STOP','method_status':'EVIDENCE_GAP',
            'unknown_requests':0,'not_dispatched_requests':0,'candidate_verified':True,
            'coordinator_policy_failures':[],'recovery_failure':None,'semantic_outcome_upgraded':False})
    receipt.write_text(json.dumps(raw))
    for name,op,result in [('export','public.export',candidate),('observe','public.observe',{'status':'EXECUTION_PASS'})]:
        (receipt.parent/'method-journal'/(name+'.json')).write_text(json.dumps({'op':op,'status':'DONE','result':result}))
    queue=root/'queue'/slot['slot_id']/'state.json';queue.parent.mkdir(parents=True)
    queue.write_text(json.dumps({'status':'METHOD_RETURNED_ERROR','returncode':2,
        'identity_sha256':core().digest(raw['identity']),'panel_sha256':core().sha(path)}))
    terminal_ref={'path':str(terminal),'sha256':core().sha(terminal)}
    result_dir=tmp_path/'recovered-independent'
    score(receipt,panel['benchmark_root'],result_dir,_terminal_program=terminal_ref)
    current=copy.deepcopy(panel);current['output_root']=str(tmp_path/'runs-terminal-r3')
    current['predecessor']={'schema_version':'ce.fixed_panel_method_terminal_amendment.v1',
        'interpretation_policy':'ce.pre_edit_evidence_gap_terminal.v1',
        'worker_files_before':workers,'worker_files_after':workers,
        **{k:{'path':str(p),'sha256':core().sha(p)} for k,p in [('panel',path),('records',root/'records.json'),
            ('scoring_program',program),('terminal_program',terminal),('terminal_receipt',receipt),
            ('terminal_score',result_dir/'result.json'),('queue_state',queue)]}}
    newpath=tmp_path/'panel-terminal.json';newpath.write_text(json.dumps(current))
    return panel,path,current,newpath


@pytest.mark.parametrize('chained',[False,True])
def test_terminal_recovery_retains_all_rows_and_never_reexecutes_method(tmp_path,chained):
    old,path,current,newpath=terminal_fixture(tmp_path,chained)
    before={str(p):p.read_bytes() for p in Path(old['output_root']).rglob('*') if p.is_file()}
    core().verify_panel(newpath);rows=core().verified_records(newpath)
    assert len(rows)==2 and [r['status'] for r in rows]==['PASS','SEMANTIC_FAIL']
    assert rows[1]['method_status']=='EVIDENCE_GAP' and rows[1]['recorded_status']=='INFRASTRUCTURE_STOP'
    assert rows[1]['public_decision']=='UNKNOWN'
    class NeverDispatch:
        def identity(self,*a):raise AssertionError('No readmission of a completed method')
        def dispatch(self,*a):raise AssertionError('No second method execution')
    assert driver().run_slot(newpath,rows[1]['slot_id'],tmp_path/'unused-key',transport=NeverDispatch())['status']=='SEMANTIC_FAIL'
    assert before=={str(p):p.read_bytes() for p in Path(old['output_root']).rglob('*') if p.is_file()}


@pytest.mark.parametrize('mutation',['omit','upgrade','source','runtime','worker','queue','receipt','score','another-chain'])
def test_terminal_correction_cannot_hide_or_replace_original_evidence(tmp_path,mutation):
    old,path,current,newpath=terminal_fixture(tmp_path)
    if mutation in {'omit','upgrade'}:
        root=Path(current['output_root']);root.mkdir();rows=core().verified_records(newpath)
        if mutation=='omit':rows=rows[1:]
        else:rows[-1]['status']='PASS';rows[-1]['independent_status']='PASS'
        (root/'records.json').write_text(json.dumps(rows))
    elif mutation=='source':current['variants']['candidate']['root']=str(tmp_path/'other-source')
    elif mutation=='runtime':current['runtime']['python']='/another/python'
    elif mutation=='worker':
        current['predecessor']['worker_files_after']={'run_isolated_method.py':'f'*64}
        current['execution_environment']['harness_files']=core().digest(current['predecessor']['worker_files_after'])
    elif mutation=='queue':
        p=Path(current['predecessor']['queue_state']['path']);v=json.loads(p.read_text());v['status']='DISPATCH_INTENT';p.write_text(json.dumps(v))
        current['predecessor']['queue_state']['sha256']=core().sha(p)
    elif mutation in {'receipt','score'}:
        key='terminal_'+mutation;p=Path(current['predecessor'][key]['path']);p.write_text('{}')
        current['predecessor'][key]['sha256']=core().sha(p)
    else:
        current['predecessor']['panel']={'path':str(newpath),'sha256':core().sha(newpath)}
    newpath.write_text(json.dumps(current))
    with pytest.raises((ValueError,KeyError,FileNotFoundError)):core().verified_records(newpath)
