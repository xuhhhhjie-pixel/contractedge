import copy
import hashlib
import json

import pytest

from contractedge.execution.probe_specs import registered_programs
from contractedge.execution.probes import behavior_records, probe_command
from contractedge.execution.support import deterministic_check_result


PID = 'alertmanager-reload-v4'
CHECK = 'groups.unique_for_declared_route'


def report():
    program, statements = registered_programs()[PID]
    return {'status':'PARTIAL','snapshot_id':'current','source_unchanged':True,
        'cleanup':{'status':'OWNED_CONTAINERS_CLOSED'},'probes':[{
            'probe_id':PID,'snapshot_id':'current','status':'PARTIAL',
            'probe_sha256':hashlib.sha256(program.encode()).hexdigest(),
            'scenario':'method-'+PID,'boundary':'finite local public API',
            'checks':[{'id':CHECK,'status':'FAIL','statement':statements[CHECK],
                'observed':{'counterexample_kind':'DUPLICATE_GROUP_IDENTITIES_IN_BOUNDED_PREFIX',
                    'response_complete':False,'byte_limit':8388608,'observed_bytes':8388609,
                    'complete_prefix_groups':5,'duplicate_identities':2,'unique_identities':3}}]}]}


def test_partial_negative_fact_reaches_necessary_failure_without_asserting_visibility(tmp_path):
    (tmp_path/PID).mkdir()
    records=behavior_records(report(),tmp_path)
    assert len(records)==1
    observations=[]
    for path,meta in records.items():
        observations.append({'id':'PREFIX','snapshot_id':'current','excerpt':path.read_text(),**meta})
    obligation={'behavior_check_refs':[PID+':'+CHECK,PID+':groups.visible_after_reload'],
                'check_coverage':'NECESSARY_ONLY','verification_kind':'PUBLIC_BEHAVIOR'}
    result=deterministic_check_result(obligation,observations,snapshot_id='current')
    assert result['status']=='FAIL'
    visible={**obligation,'behavior_check_refs':[PID+':groups.visible_after_reload']}
    assert deterministic_check_result(visible,observations,snapshot_id='current') is None


@pytest.mark.parametrize('change', ['pass','unrelated','old_profile','wrong_digest','old_snapshot',
    'dirty_source','cleanup_failed','missing_counts','contradictory_counts','not_over_bound','complete_claim'])
def test_partial_output_cannot_upgrade_unknown_or_forge_scope(tmp_path, change):
    d=report();p=d['probes'][0];c=p['checks'][0];o=c['observed']
    if change=='pass':c['status']='PASS'
    elif change=='unrelated':c['id']='groups.visible_after_reload'
    elif change=='old_profile':p['probe_id']='alertmanager-reload-v3'
    elif change=='wrong_digest':p['probe_sha256']='a'*64
    elif change=='old_snapshot':p['snapshot_id']='old'
    elif change=='dirty_source':d['source_unchanged']=False
    elif change=='cleanup_failed':d['cleanup']['status']='OWNED_CLEANUP_FAILED'
    elif change=='missing_counts':o.pop('duplicate_identities')
    elif change=='contradictory_counts':o['unique_identities']=99
    elif change=='not_over_bound':o['observed_bytes']=3
    elif change=='complete_claim':o['response_complete']=True
    (tmp_path/PID).mkdir()
    assert behavior_records(d,tmp_path)=={}


def test_v4_keeps_old_programs_and_offline_dispatch_unchanged():
    registry=registered_programs()
    assert hashlib.sha256(registry['alertmanager-reload-v3'][0].encode()).hexdigest()=='cc9f5496072b4f4e922d702c3c74a48e35831c7cba16efdbac5ef8efe7e4ae4d'
    assert registry[PID][1]==registry['alertmanager-reload-v3'][1]
    assert probe_command(PID)==probe_command('alertmanager-reload-v3')
