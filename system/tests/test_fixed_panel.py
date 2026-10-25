"""The original46 denominator and dispatch authority survive isolation migration."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ENVIRONMENT = {'python_environment': {'interpreter_sha256': 'a'*64},
    'dependency_files': {}, 'public_tool_files': {}, 'public_release_files': {},
    'harness_files': {'worker.py':'b'*64}, 'coordinator_code_files': {'pkg.py':'c'*64}}


def load():
    scripts=Path(__file__).resolve().parents[1]/'scripts'
    path=scripts/'fixed_panel.py'
    assert path.is_file(), 'Strict fixed-panel adapter not implemented'
    spec=importlib.util.spec_from_file_location('fixed_panel_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def fixture(tmp_path):
    write=lambda p,v:p.write_text(json.dumps(v))
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    original={'A':[{'case_id':'A'+str(i)} for i in range(7)],
              'B':[{'case_id':'B'+str(i)} for i in range(10)],
              'key_repeat_cases':['A0','A1'], 'recovery_seed_paths':{'A2':'old-kpt','A3':'old-alert'}}
    original_path=tmp_path/'original.json';write(original_path,original)
    benchmark=tmp_path/'benchmark';(benchmark/'public').mkdir(parents=True);(benchmark/'partition').mkdir()
    (benchmark/'public/manifest.jsonl').write_text('\n'.join(json.dumps({'case_id':r['case_id']}) for group in ['A','B'] for r in original[group]))
    (benchmark/'partition/member_groups.jsonl').write_text('\n'.join(json.dumps({'case_id':r['case_id'],'split':'development'}) for group in ['A','B'] for r in original[group]))
    write(benchmark/'RELEASE_ENTRY.json',{'schema':'fixture'})
    seeds={}
    for case in original['recovery_seed_paths']:
        p=tmp_path/(case+'.patch');p.write_text('public failure seed '+case)
        seeds[case]={'path':str(p),'sha256':sha(p)}
    original['recovery_seed_calibration']={c:{'seed_sha256':v['sha256']} for c,v in seeds.items()}
    write(original_path,original)
    variants={}
    for variant in ['baseline','candidate']:
        root=tmp_path/variant;(root/'src/pkg').mkdir(parents=True);source=root/'src/pkg/__init__.py';source.write_text(variant)
        config=tmp_path/(variant+'.json');write(config,{'model':'same-model','max_model_calls':12,'max_rounds':2})
        files={'pkg/__init__.py':sha(source)}
        variants[variant]={'root':str(root),'source_files':files,'source_sha256':hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
            'config':str(config),'config_sha256':sha(config)}
    tools=tmp_path/'driver.py';tools.write_text('frozen parent tool')
    panel={'schema_version':'ce.isolated_fixed_panel.v1','panel_id':'panel-test',
           'original_registration':{'path':str(original_path),'sha256':sha(original_path)},
           'benchmark_root':str(benchmark),'data_files':{name:sha(benchmark/name) for name in ['public/manifest.jsonl','partition/member_groups.jsonl','RELEASE_ENTRY.json']},
           'variants':variants,'seeds':seeds,'harness_files':{str(tools):sha(tools)},
           'output_root':str(tmp_path/'runs'),'model_call_cap':12,'fresh_edit_cap':2,'formal_ready':False}
    panel['execution_environment']={k:hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest() for k,v in ENVIRONMENT.items()}
    panel['slots']=load().build_schedule(original,panel['panel_id'])
    path=tmp_path/'panel.json';write(path,panel)
    return panel,path,original


def test_original_schedule_is46_unique_and_deterministically_paired(tmp_path):
    panel,path,original=fixture(tmp_path);module=load()
    assert len(panel['slots'])==46
    assert len({r['slot_id'] for r in panel['slots']})==46
    assert {g:sum(r['group']==g for r in panel['slots']) for g in ['A','B','repeat','recovery']}=={'A':14,'B':20,'repeat':4,'recovery':8}
    assert panel['slots']==module.build_schedule(original,'panel-test')
    assert module.verify_panel(path)['panel_id']=='panel-test'


@pytest.mark.parametrize('mutation',['duplicate','replace','reorder','extra','cap','source','config','seed','harness','partition','original'])
def test_panel_rejects_schedule_or_identity_drift(tmp_path,mutation):
    panel,path,_=fixture(tmp_path)
    if mutation=='duplicate': panel['slots'][1]=copy.deepcopy(panel['slots'][0])
    if mutation=='replace': panel['slots'][0]['case_id']='OUTSIDE'
    if mutation=='reorder': panel['slots'][0],panel['slots'][1]=panel['slots'][1],panel['slots'][0]
    if mutation=='extra': panel['slots'].append(copy.deepcopy(panel['slots'][0]))
    if mutation=='cap': panel['model_call_cap']=13
    if mutation=='source': (Path(panel['variants']['baseline']['root'])/'src/pkg/__init__.py').write_text('changed')
    if mutation=='config': Path(panel['variants']['candidate']['config']).write_text('{}')
    if mutation=='seed': Path(panel['seeds']['A2']['path']).write_text('changed')
    if mutation=='harness': Path(next(iter(panel['harness_files']))).write_text('changed')
    if mutation=='partition': (Path(panel['benchmark_root'])/'partition/member_groups.jsonl').write_text('changed')
    if mutation=='original': Path(panel['original_registration']['path']).write_text('{}')
    path.write_text(json.dumps(panel))
    with pytest.raises(ValueError):load().verify_panel(path)


def test_public_partition_cannot_be_bypassed_by_rehashing_validation_member(tmp_path):
    panel,path,_=fixture(tmp_path);p=Path(panel['benchmark_root'])/'partition/member_groups.jsonl'
    p.write_text(p.read_text().replace('"development"','"validation"',1))
    panel['data_files']['partition/member_groups.jsonl']=hashlib.sha256(p.read_bytes()).hexdigest();path.write_text(json.dumps(panel))
    with pytest.raises(ValueError,match='development'):load().verify_panel(path)


def test_rehashed_recovery_seed_cannot_replace_original_public_failure(tmp_path):
    panel,path,_=fixture(tmp_path);seed=panel['seeds']['A2'];Path(seed['path']).write_text('different patch')
    seed['sha256']=hashlib.sha256(Path(seed['path']).read_bytes()).hexdigest();path.write_text(json.dumps(panel))
    with pytest.raises(ValueError,match='original'):load().verify_panel(path)


def test_current_seed_protocol_cannot_dispatch_without_both_variant_compatibility_proof(tmp_path):
    panel,path,_=fixture(tmp_path)
    for variant in panel['variants'].values():
        p=Path(variant['config']);config=json.loads(p.read_text());config['seed_planning_snapshot']='CURRENT_CANDIDATE';p.write_text(json.dumps(config))
        variant['config_sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
    path.write_text(json.dumps(panel))
    with pytest.raises(ValueError,match='compatibility'):load().verify_panel(path)


def slot_identity(panel,slot):
    v=panel['variants'][slot['variant']]
    return {**copy.deepcopy(ENVIRONMENT),'case_id':slot['case_id'],'attempt_id':slot['slot_id'],
            'method_files':v['source_files'],'config_sha256':v['config_sha256'],
            'output_path':str(Path(panel['output_root'])/'methods'/slot['slot_id']/slot['case_id']),
            **({'seed_sha256':panel['seeds'][slot['case_id']]['sha256']} if slot['group']=='recovery' else {})}


def test_slot_authority_requires_exact_panel_and_identity(tmp_path):
    panel,path,_=fixture(tmp_path);slot=panel['slots'][0];identity=slot_identity(panel,slot)
    record={**identity,'role':'ISOLATED_FIXED_PANEL_SLOT','allow_paid':True,'counts_in_46_slot_panel':True,
            'panel_registration_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'panel_slot_id':slot['slot_id']}
    load().validate_slot_registration(record,identity,path)
    for key,value in [('case_id','OTHER'),('attempt_id','new-attempt'),('output_path',str(tmp_path/'other')),('method_files',{'fake':'x'}),('config_sha256','changed')]:
        bad={**identity,key:value}
        with pytest.raises(ValueError):load().validate_slot_registration({**record,**bad},bad,path)
    with pytest.raises(ValueError):load().validate_slot_registration({**record,'panel_registration_sha256':'bad'},identity,path)


@pytest.mark.parametrize('field',list(ENVIRONMENT))
def test_next_slot_cannot_silently_use_a_different_common_environment(tmp_path,field):
    panel,path,_=fixture(tmp_path);slot=panel['slots'][0];identity=slot_identity(panel,slot)
    identity[field]={'changed':'after panel freeze'}
    record={**identity,'role':'ISOLATED_FIXED_PANEL_SLOT','allow_paid':True,'counts_in_46_slot_panel':True,
            'panel_registration_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'panel_slot_id':slot['slot_id']}
    with pytest.raises(ValueError,match='environment'):load().validate_slot_registration(record,identity,path)


def records(panel,group=None,status='PASS'):
    return [{**r,'status':status,'public_decision':'PASS','terminal_verified':True} for r in panel['slots'] if group is None or r['group']==group]


def test_B_requires_verified_A_repeat_and_recovery_without_public_failure(tmp_path):
    panel,_,_=fixture(tmp_path);module=load()
    assert module.b_gate(panel,[])['open'] is False
    rows=[r for r in records(panel) if r['group']!='B']
    assert module.b_gate(panel,rows)['open'] is True
    for mutation in ['fail','unknown','unverified','public-fail','duplicate']:
        bad=copy.deepcopy(rows);candidate=next(r for r in bad if r['variant']=='candidate')
        if mutation=='fail':candidate['status']='SEMANTIC_FAIL'
        if mutation=='unknown':candidate['status']='NOT_RUN'
        if mutation=='unverified':candidate['terminal_verified']=False
        if mutation=='public-fail':candidate['public_decision']='FAIL'
        if mutation=='duplicate':bad.append(copy.deepcopy(bad[0]))
        assert module.b_gate(panel,bad)['open'] is False


def test_unknown_baseline_outcome_cannot_count_as_completed_prerequisite(tmp_path):
    panel,_,_=fixture(tmp_path);rows=[r for r in records(panel) if r['group']!='B']
    next(r for r in rows if r['variant']=='baseline')['status']='UNKNOWN'
    assert load().b_gate(panel,rows)['open'] is False


def test_qualification_registration_still_cannot_claim_a_panel_slot(tmp_path):
    scripts=Path(__file__).resolve().parents[1]/'scripts';sys.path.insert(0,str(scripts))
    try:
        from run_isolated_method import check_registration
        panel,path,_=fixture(tmp_path);slot=panel['slots'][0];identity=slot_identity(panel,slot)
        record={**identity,'role':'ISOLATED_FIXED_PANEL_SLOT','allow_paid':True,'counts_in_46_slot_panel':True,
                'panel_registration_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'panel_slot_id':slot['slot_id']}
        run=tmp_path/'run.json';run.write_text(json.dumps(record))
        with pytest.raises(ValueError):check_registration(run,identity)
        check_registration(run,identity,panel_path=path)
    finally:sys.path.pop(0)
