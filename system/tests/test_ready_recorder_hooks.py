"""Real READY entry and fresh-process continuation preserve original stage facts."""
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from contractedge.development import public_demo
from contractedge.orchestration.recorder import RunRecorder


def read(path):return json.loads(Path(path).read_bytes())


def stage(root,name):
    return [read(p) for p in sorted((root/'journal/stages'/name).glob('*/*.json'))]


def verify(root):
    identity=read(root/'journal/run.identity.json')
    recorder=RunRecorder(root,run_id=identity['run_id'],input_digest=identity['input_digest'])
    for p in (root/'journal/stages').glob('*/*/*.json'):
        value=read(p)
        for field in ('input_refs','output_refs','projection_refs'):
            for ref in value[field]:recorder.verify_artifact(ref)
        for ref in value['call_refs']:recorder.verify_call(ref)
    return recorder


def test_save_ready_records_real_final_diagnosis_planning_and_exact_budget_ledger(tmp_path):
    saved=tmp_path/'saved'
    result=public_demo.run_demo('reload-state',saved,save_ready=True)
    assert result['status']=='READY'
    trace,checkpoint=read(saved/'ready.trace.json'),read(saved/'ready.checkpoint.json')
    assert read(saved/'journal/run.identity.json')['run_id']==trace['run_id']==checkpoint['run_id']
    recorder=verify(saved)
    assert stage(saved,'diagnosis')[-1]['status']=='PRODUCED'
    assert stage(saved,'planning')[-1]['status']=='PRODUCED'
    assert stage(saved,'diagnosis')[-1]['call_refs'][0]['absolute_call_index']==0
    assert stage(saved,'planning')[-1]['call_refs'][0]['absolute_call_index']==1
    ready=stage(saved,'ready')[-1]
    assert ready['status']=='READY' and len(ready['call_refs'])==2
    ledger=read(saved/ready['call_refs'][-1]['ledger_segment_path'])
    planning=stage(saved,'planning')[-1]
    assert planning['call_refs'][0]['ledger_segment_path'] != ready['call_refs'][-1]['ledger_segment_path']
    old_ledger=read(saved/planning['call_refs'][0]['ledger_segment_path'])
    assert old_ledger[-1]['elapsed_seconds'] <= ledger[-1]['elapsed_seconds']
    assert ledger==trace['model_calls']==read(saved/'model_calls.json')
    assert checkpoint['budget']['model_calls']==2
    assert checkpoint['budget']['reserved_tokens']==sum(c['prompt_tokens_reserved']+c['output_tokens_reserved'] for c in ledger)
    for ref in checkpoint['stage_journal']['stage_receipts'].values():recorder.verify_artifact(ref)


def test_fresh_process_continuation_keeps_original_stage_refs_and_does_not_redispatch_front(tmp_path):
    saved,continued=tmp_path/'saved',tmp_path/'continued'
    public_demo.run_demo('reload-state',saved,save_ready=True)
    checkpoint=read(saved/'ready.checkpoint.json')
    binding=checkpoint['stage_journal']
    original={name:(saved/name).read_bytes() for name in binding['files']}
    command=[sys.executable,str(public_demo.SYSTEM_ROOT/'scripts/run_public_demo.py'),
             '--resume-ready',str(saved),'--output',str(continued)]
    result=subprocess.run(command,capture_output=True,text=True,timeout=90)
    assert result.returncode==0,result.stdout+result.stderr
    assert original=={name:(saved/name).read_bytes() for name in original}
    assert original=={name:(continued/name).read_bytes() for name in original}
    verify(continued)
    provenance=read(continued/'continuation.json')['initial_stage_provenance']
    assert provenance['status']=='VERIFIED_STAGE_IMPORT'
    assert stage(continued,'diagnosis')[-1]==stage(saved,'diagnosis')[-1]
    assert stage(continued,'planning')[-1]==stage(saved,'planning')[-1]
    imported=stage(continued,'ready_import')[-1]
    assert imported['status']=='VERIFIED_STAGE_IMPORT'
    assert [r['absolute_call_index'] for r in imported['call_refs']]==[0,1]
    calls=read(continued/'combined.model_calls.json')
    assert calls[:2]==read(saved/'model_calls.json')
    assert len(calls)==4
    assert [c['prompt_version'] for c in calls[2:]]==['topomen-source-execution.v1','public-postcondition-review.v1']
    report=read(continued/'repair/repair.trace.json')
    assert report['status']=='PUBLIC_REVIEW_COMPLETE'
    assert report['budget']['resumed_model_calls']==2
    assert report['budget']['reserved_tokens']==sum(c['prompt_tokens_reserved']+c['output_tokens_reserved'] for c in calls)
    assert stage(continued/'repair','front_import')[-1]['status']=='LEGACY_RECORDED'


def test_legacy_ready_has_explicit_import_scope_and_no_invented_original_stages(tmp_path):
    saved,continued=tmp_path/'saved',tmp_path/'continued'
    public_demo.run_demo('reload-state',saved,save_ready=True)
    checkpoint=read(saved/'ready.checkpoint.json')
    checkpoint.pop('stage_journal',None)
    checkpoint.pop('run_id',None)
    public_demo.write_json(saved/'ready.checkpoint.json',checkpoint)
    result=public_demo.run_demo(None,continued,resume_ready=saved)
    assert result['status']=='PUBLIC_REVIEW_COMPLETE'
    provenance=read(continued/'continuation.json')['initial_stage_provenance']
    assert provenance['status']=='LEGACY_IMPORT'
    assert not stage(continued,'diagnosis') and not stage(continued,'planning')
    assert stage(continued/'repair','front_import')[-1]['status']=='LEGACY_RECORDED'


def test_modified_original_stage_evidence_is_rejected_before_claim_and_provider(tmp_path):
    saved=tmp_path/'saved'
    public_demo.run_demo('reload-state',saved,save_ready=True)
    binding=read(saved/'ready.checkpoint.json')['stage_journal']
    name=next(n for n in binding['files'] if n.startswith('journal/stages/planning/'))
    (saved/name).write_bytes((saved/name).read_bytes()+b' ')
    with patch.object(public_demo,'_provider') as factory:
        with pytest.raises(ValueError,match='journal|stage|receipt'):
            public_demo.run_demo(None,tmp_path/'continued',resume_ready=saved)
    factory.assert_not_called()
    assert not (saved/'continuation.claim.json').exists()


def test_unknown_front_dispatch_has_receipt_but_no_ready_checkpoint_or_retry(tmp_path):
    class Unknown:
        calls=0
        def complete_json(self,request):
            self.calls+=1
            raise TimeoutError('explicit unknown READY fixture')
    provider=Unknown()
    saved=tmp_path/'saved'
    with patch.object(public_demo,'_provider',return_value=(provider,public_demo.count_tokens)):
        result=public_demo.run_demo('reload-state',saved,save_ready=True)
    assert provider.calls==1
    assert result['status']=='INFRASTRUCTURE_ERROR'
    verify(saved)
    assert stage(saved,'dispatch')[-1]['status']=='ERROR'
    assert not stage(saved,'diagnosis')[-1]['output_refs']
    assert not stage(saved,'planning')[-1]['output_refs']
    assert not (saved/'ready.checkpoint.json').exists()
    assert len(read(saved/'model_calls.json'))==1


@pytest.mark.parametrize('bad_name',[
    '/tmp/forbidden-ready-stage.json','../outside.json','journal/../run.config.json',
    'run.config.json','journal/ledger/segment-000000.inflight.json',
    'journal/artifacts/'+'a'*64+'/../outside.json'])
def test_stage_inventory_cannot_escape_or_copy_mutable_control_files(tmp_path,bad_name):
    saved=tmp_path/'saved'
    public_demo.run_demo('reload-state',saved,save_ready=True)
    checkpoint=read(saved/'ready.checkpoint.json')
    checkpoint['stage_journal']['files'][bad_name]='a'*64
    public_demo.write_json(saved/'ready.checkpoint.json',checkpoint)
    with patch.object(public_demo,'_provider') as factory:
        with pytest.raises(ValueError,match='path|journal|relative|Artifact'):
            public_demo.run_demo(None,tmp_path/'continued',resume_ready=saved)
    factory.assert_not_called()
    assert not (saved/'continuation.claim.json').exists()
    assert not (tmp_path/'continued/run.config.json').exists()


@pytest.mark.parametrize('parent_link',[False,True])
def test_stage_inventory_rejects_symlink_before_reading_external_target(tmp_path,parent_link):
    saved=tmp_path/'saved'
    public_demo.run_demo('reload-state',saved,save_ready=True)
    outside=tmp_path/'outside'
    outside.mkdir()
    sentinel=outside/'sensitive.json'
    sentinel.write_text('fictional forbidden sentinel')
    prefix=saved/'journal/artifacts'/('b'*64)
    prefix.mkdir()
    if parent_link:
        (prefix/'linked').symlink_to(outside,target_is_directory=True)
        name=(prefix/'linked/sensitive.json').relative_to(saved).as_posix()
    else:
        (prefix/'linked.json').symlink_to(sentinel)
        name=(prefix/'linked.json').relative_to(saved).as_posix()
    checkpoint=read(saved/'ready.checkpoint.json')
    checkpoint['stage_journal']['files'][name]='a'*64
    public_demo.write_json(saved/'ready.checkpoint.json',checkpoint)
    original=public_demo.file_sha
    def guard(path):
        if Path(path).resolve()==sentinel.resolve():raise AssertionError('External target must not be read')
        return original(path)
    with patch.object(public_demo,'_provider') as factory, patch.object(public_demo,'file_sha',guard):
        with pytest.raises(ValueError,match='symlink'):
            public_demo.run_demo(None,tmp_path/'continued',resume_ready=saved)
    factory.assert_not_called()
    assert not (saved/'continuation.claim.json').exists()
    assert sentinel.read_text()=='fictional forbidden sentinel'


def test_import_recording_failure_does_not_claim_or_create_provider(tmp_path):
    saved=tmp_path/'saved'
    public_demo.run_demo('reload-state',saved,save_ready=True)
    files=read(saved/'ready.checkpoint.json')['stage_journal']['files']
    before={name:(saved/name).read_bytes() for name in files}
    original=RunRecorder.record_stage
    def fail(recorder,name,**kwargs):
        if name=='ready_import':raise OSError('explicit READY import recording failure')
        return original(recorder,name,**kwargs)
    with patch.object(public_demo,'_provider') as factory,patch.object(RunRecorder,'record_stage',fail):
        with pytest.raises(OSError,match='import recording failure'):
            public_demo.run_demo(None,tmp_path/'continued',resume_ready=saved)
    factory.assert_not_called()
    assert not (saved/'continuation.claim.json').exists()
    assert before=={name:(saved/name).read_bytes() for name in before}


def test_rewritten_outer_config_hash_does_not_rebind_original_stage_configuration(tmp_path):
    saved=tmp_path/'saved'
    public_demo.run_demo('reload-state',saved,save_ready=True)
    config=read(saved/'run.config.json')
    config['max_rounds']=1
    public_demo.write_json(saved/'run.config.json',config)
    freeze=read(saved/'run.freeze.json')
    freeze['config_digest']=public_demo.canonical_digest(config)
    public_demo.write_json(saved/'run.freeze.json',freeze)
    checkpoint=read(saved/'ready.checkpoint.json')
    for name in ('run.config.json','run.freeze.json'):
        checkpoint['files'][name]=public_demo.file_sha(saved/name)
    public_demo.write_json(saved/'ready.checkpoint.json',checkpoint)
    with patch.object(public_demo,'_provider') as factory:
        with pytest.raises(ValueError,match='configuration'):
            public_demo.run_demo(None,tmp_path/'continued',resume_ready=saved)
    factory.assert_not_called()
    assert not (saved/'continuation.claim.json').exists()
