import hashlib
import json
from contractedge.orchestration.repair import select_observations
from contractedge.execution.probe_specs import registered_programs
from contractedge.execution.probes import behavior_records


def test_failed_registered_check_precedes_lexically_dense_successes():
    observations=[]
    for i in range(10):
        observations.append(dict(id='OBS'+str(i),excerpt='reload groups '*40,
            locator={'path':f'{i}.json'},behavior_check={'id':str(i),'status':'PASS'}))
    failed=dict(id='OBS-last',excerpt='target predicate false',locator={'path':'last.json'},
                behavior_check={'id':'target','status':'FAIL'})
    chosen=select_observations([*observations,failed],'reload groups',len,1000)
    assert failed in chosen
    assert sum(len(o['excerpt']) for o in chosen)<=1000


def test_v4_projection_retains_atomic_failure_without_repeating_entire_scenario(tmp_path):
    program,checks=registered_programs()['kpt-configpath-v4'];digest=hashlib.sha256(program.encode()).hexdigest()
    state={'functions':{'owner':{'ordinary_inputs':['settings','workload'],'function_config':'settings'}},
           'rendered':{'settings':{'metadata':{'name':'settings'},'data':{'initial':'config'}}},'render_error':'<nil>'}
    report={'status':'COMPLETE','snapshot_id':'S0','probes':[{'probe_id':'kpt-configpath-v4','status':'COMPLETE',
        'scenario':'method-kpt-configpath-v4',
        'probe_sha256':digest,'boundary':'public local fixture','checks':[
            {'id':name,'status':'FAIL' if name=='mutator.owner_excludes_config' else 'PASS','observed':state}
            for name in checks]}]}
    (tmp_path/'kpt-configpath-v4').mkdir()
    result=behavior_records(report,tmp_path)
    values=[json.loads(path.read_text()) for path in result]
    failed=next(v for v in values if v['id']=='mutator.owner_excludes_config')
    assert failed['status']=='FAIL'
    assert failed['observed']=={'render_error':'<nil>','ordinary_inputs':['settings','workload']}
    assert failed['raw_source']=='stdout'
