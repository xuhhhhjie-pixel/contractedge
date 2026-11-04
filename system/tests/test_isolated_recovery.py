"""Crash boundaries preserve unknown paid calls and reject partial candidates."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from contractedge.source.snapshot import compute_snapshot
from tests.test_isolated_public_worker import session


def modules():
    scripts=Path(__file__).resolve().parents[1]/'scripts';sys.path.insert(0,str(scripts))
    try:
        import isolated_protocol as rpc
        import isolated_public_worker as public
    finally:sys.path.pop(0)
    return scripts,rpc,public


def test_real_process_death_after_side_effect_never_replays_under_new_id(tmp_path):
    scripts,rpc,_=modules();marker=tmp_path/'effect'
    script='''from pathlib import Path
import os
from isolated_protocol import Journal
def effect():
 Path(MARKER).write_text('possibly billed')
 os._exit(23)
Journal(JOURNAL).execute('sent',{'op':'model.complete','payload':{}},effect)
'''.replace('MARKER',repr(str(marker))).replace('JOURNAL',repr(str(tmp_path/'journal')))
    process=subprocess.run([sys.executable,'-c',script],env={**os.environ,'PYTHONPATH':str(scripts)},timeout=10)
    assert process.returncode==23 and marker.read_text()=='possibly billed'
    for request_id in ['sent','new']:
        with pytest.raises(rpc.ProtocolError,match='OUTCOME_UNKNOWN'):
            rpc.Journal(tmp_path/'journal').execute(request_id,{'op':'model.complete','payload':{}},
                lambda:marker.write_text('duplicated') or {})
    assert marker.read_text()=='possibly billed'


def test_process_death_keeps_financial_reservation_and_does_not_reset_on_restart(tmp_path):
    scripts,rpc,_=modules()
    src=Path(__file__).resolve().parents[1]/'src'
    code='''import os
from isolated_protocol import Journal
from contractedge.model.money import MoneyBudget
money=MoneyBudget(LEDGER,limit_cny=150,model='test',input_cny_per_million='2',output_cny_per_million='8')
def effect():
 money.reserve('test',100,10)
 os._exit(23)
Journal(JOURNAL).execute('sent',{'op':'model.complete','payload':{}},effect)
'''.replace('LEDGER',repr(str(tmp_path/'money.json'))).replace('JOURNAL',repr(str(tmp_path/'journal')))
    process=subprocess.run([sys.executable,'-c',code],env={**os.environ,'PYTHONPATH':str(scripts)+os.pathsep+str(src)},timeout=10)
    assert process.returncode==23
    from contractedge.model.money import MoneyBudget
    money=MoneyBudget(tmp_path/'money.json',limit_cny=150,model='test',input_cny_per_million='2',output_cny_per_million='8')
    assert money.summary()['committed_micro_cny']==280
    with pytest.raises(rpc.ProtocolError,match='OUTCOME_UNKNOWN'):
        rpc.Journal(tmp_path/'journal').execute('new',{'op':'model.complete','payload':{}},
            lambda:money.reserve('test',100,10))
    ledger=json.loads((tmp_path/'money.json').read_text())
    assert len(ledger['calls'])==1 and ledger['calls'][0]['status']=='RESERVED'


def interrupted_switch(tmp_path,monkeypatch,*,corrupt=False):
    producer,transfer=session(tmp_path)
    producer.prepare({'case_id':'CASE','timeout_seconds':30})
    source=Path(producer.record['source_root']);before=compute_snapshot(source).snapshot_id
    stage='e'*32;(transfer/stage).mkdir();(transfer/stage/'main.go').write_text('unchecked trial\n')
    _,_,public=modules();old_write=public.write_json
    # session() loads an isolated module instance; patch the actual method globals.
    globals_=producer._sync.__func__.__globals__;old=globals_['write_json']
    def fail_after_swap(path,value):
        if Path(path).name=='source-switch.json' and value.get('status')=='DONE':
            raise OSError('simulated power loss before commit receipt')
        old(path,value)
    monkeypatch.setitem(globals_,'write_json',fail_after_swap)
    with pytest.raises(OSError):
        producer.observe(dict(stage_id=stage,snapshot_id=compute_snapshot(transfer/stage).snapshot_id,timeout_seconds=10))
    monkeypatch.setitem(globals_,'write_json',old)
    assert (producer.root/'source-switch.json').is_file()
    record=json.loads((producer.root/'source-switch.json').read_text())
    assert record['status']=='PENDING' and compute_snapshot(source).snapshot_id!=before
    if corrupt:(Path(record['old_source'])/'main.go').write_text('corrupt previous')
    return producer,public,before


def test_interrupted_source_swap_rolls_back_verified_checkpoint_without_export_or_budget_reset(tmp_path,monkeypatch):
    producer,public,before=interrupted_switch(tmp_path,monkeypatch)
    recovered=public.recover_pending_source(producer.root)
    assert recovered['status']=='ROLLED_BACK' and recovered['restored_snapshot']==before
    assert recovered['automatic_method_resume'] is False and recovered['candidate_exported'] is False
    assert recovered['budget_reset'] is False
    assert compute_snapshot(producer.record['source_root']).snapshot_id==before
    assert (Path(producer.record['source_root'])/'main.go').read_text()=='broken\n'
    assert not list(producer.root.glob('*.patch'))
    assert public.recover_pending_source(producer.root)['status']=='NO_PENDING_SOURCE_SWITCH'


def test_corrupt_source_checkpoint_is_not_promoted_or_silently_rolled_back(tmp_path,monkeypatch):
    producer,public,_=interrupted_switch(tmp_path,monkeypatch,corrupt=True)
    current=compute_snapshot(producer.record['source_root']).snapshot_id
    with pytest.raises(ValueError):public.recover_pending_source(producer.root)
    assert compute_snapshot(producer.record['source_root']).snapshot_id==current
    assert json.loads((producer.root/'source-switch.json').read_text())['status']=='PENDING'


def test_cancelling_session_prevents_queued_request_from_dispatching_after_active_call(tmp_path,monkeypatch):
    import threading
    from contextlib import contextmanager
    _,rpc,_=modules();journal=rpc.Journal(tmp_path/'journal')
    active,release,queued=threading.Event(),threading.Event(),threading.Event()
    real_lock=journal._locked
    @contextmanager
    def observed_lock():
        if threading.current_thread().name=='queued':queued.set()
        with real_lock():yield
    monkeypatch.setattr(journal,'_locked',observed_lock)
    effects,errors=[],[]
    def service(payload):
        effects.append(payload['number'])
        if payload['number']==1:active.set();assert release.wait(3)
        return {'done':payload['number']}
    server=rpc.JsonRpcServer(tmp_path/'s','auth-8d7c',journal,{'model.complete':service})
    def dispatch(number):
        try:server.dispatch(dict(token='auth-8d7c',request_id=str(number),op='model.complete',payload={'number':number}))
        except rpc.ProtocolError as error:errors.append(str(error))
    first=threading.Thread(target=dispatch,args=(1,));second=threading.Thread(target=dispatch,args=(2,),name='queued')
    first.start();assert active.wait(2);second.start();assert queued.wait(2)
    try:server.cancel_pending()
    finally:release.set();first.join(3);second.join(3)
    assert effects==[1] and errors==['RUN_CLOSED']
    assert json.loads((tmp_path/'journal/2.json').read_text())['status']=='NOT_DISPATCHED'


def test_run_claim_survives_process_death_and_requires_distinct_registered_attempt_and_output(tmp_path):
    scripts,_,_=modules()
    code='''import os
from run_isolated_method import claim_attempt
claim_attempt(ROOT,attempt_id='registered-1',output=OUTPUT,state=STATE,identity={'case_id':'CASE'})
os._exit(23)
'''.replace('ROOT',repr(str(tmp_path/'claims'))).replace('OUTPUT',repr(str(tmp_path/'out/CASE'))).replace('STATE',repr(str(tmp_path/'state')))
    process=subprocess.run([sys.executable,'-c',code],env={**os.environ,'PYTHONPATH':str(scripts)},timeout=10)
    assert process.returncode==23
    spec=importlib.util.spec_from_file_location('claim_coordinator',scripts/'run_isolated_method.py')
    entry=importlib.util.module_from_spec(spec);sys.path.insert(0,str(scripts))
    try:spec.loader.exec_module(entry)
    finally:sys.path.pop(0)
    for attempt,output in [('registered-1','out/CASE'),('registered-2','out/CASE'),('registered-1','other/CASE')]:
        with pytest.raises(ValueError,match='already claimed'):
            entry.claim_attempt(tmp_path/'claims',attempt_id=attempt,output=tmp_path/output,
                                state=tmp_path/'new-state',identity={'case_id':'CASE'})
    accepted=entry.claim_attempt(tmp_path/'claims',attempt_id='registered-2',output=tmp_path/'new/CASE',
                                 state=tmp_path/'new-state',identity={'case_id':'CASE'})
    assert accepted['automatic_resume'] is False and accepted['budget_reset'] is False
    records=[json.loads(p.read_text()) for p in (tmp_path/'claims').glob('*.json')]
    assert any(r['attempt_id']=='registered-1' and r['state_root']==str(tmp_path/'state') for r in records)
