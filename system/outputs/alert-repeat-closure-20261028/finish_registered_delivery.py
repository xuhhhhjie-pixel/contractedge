"""After the owned fixed46 controller closes, collect existing delivery evidence.

No model/grader dispatch, source edit, formal150 start or automatic retry. A
failed/stopped/unknown controller outcome remains stopped and is never admitted.
"""
import fcntl,json,os,signal,subprocess,sys,time
from pathlib import Path
from datetime import datetime,timezone
SYSTEM=Path(__file__).resolve().parents[2]
BASE=Path(__file__).resolve().parent
ROOT=Path('/home/xhj/contractedge-fixed46-official-v068-resumed-20261028')
OUT=BASE/'administrative-admission'
STATE=OUT/'delivery-follower.json'
child=None

def read(p):return json.loads(Path(p).read_text())
def write(value):
 value.update(at=datetime.now(timezone.utc).isoformat(),owner_pid=os.getpid(),model_dispatches=0,grade_dispatches=0,formal150_started=False)
 tmp=STATE.with_suffix('.tmp');tmp.write_text(json.dumps(value,indent=2)+'\n');tmp.replace(STATE)
def stop(*_):raise KeyboardInterrupt('Explicit stop; do not restart or finalize automatically')

def main():
 global child
 if STATE.exists():raise ValueError('Prior follower claim exists; retain and audit, never auto-resend')
 signal.signal(signal.SIGTERM,stop)
 write(dict(status='WAITING_REGISTERED_ACCEPTANCE',formal_ready=False))
 try:
  while True:
   if read(SYSTEM/'CURRENT_STATUS.json')['current_development'].get('user_paused') is True:
    write(dict(status='STOPPED_USER_PAUSED',formal_ready=False));return
   with (ROOT/'controller.lock').open('a') as lock:
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:pass
    else:
     state=read(ROOT/'acceptance-state.json');rows=read(ROOT/'records.json')
     if state.get('status')!='ORIGINAL46_CLOSED_PENDING_FINAL_EFFECT_AND_DELIVERY_ASSESSMENT' or len(rows)!=46:
      write(dict(status='STOPPED_WITH_ACCEPTANCE_CONTROLLER',controller_status=state.get('status'),closed_slots=len(rows),formal_ready=False));return
     write(dict(status='COLLECTING_CLOSED46_EVIDENCE',closed_slots=len(rows),formal_ready=False))
     env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(SYSTEM/'src')}
     commands=[
      [sys.executable,str(BASE/'collect_acceptance_metrics.py'),'--panel',str(ROOT/'panel.json'),'--output',str(OUT/'FINAL_ACCEPTANCE_METRICS.json')],
      [sys.executable,str(BASE/'finalize_admission_v068_resumed.py')]]
     for number,command in enumerate(commands):
      with (OUT/('finalize-'+str(number)+'.log')).open('x') as log:
       child=subprocess.Popen(command,cwd=SYSTEM,env=env,stdout=log,stderr=subprocess.STDOUT);code=child.wait()
      if code:
       write(dict(status='FINALIZATION_NOT_PASSED',step=number,returncode=code,formal_ready=False));return
     result=read(OUT/'V068_FINAL_ADMISSION.json')
     write(dict(status='EVIDENCE_COLLECTION_CLOSED',admission_status=result['status'],formal_ready=result['formal_ready'],scope='Certificate collected only; root completion audit, final publication and deployment are separate. No formal150 dispatch.'));return
   time.sleep(20)
 except BaseException as e:
  if child and child.poll() is None:
   child.terminate()
   try:child.wait(timeout=45)
   except subprocess.TimeoutExpired:child.kill();child.wait(timeout=10)
  write(dict(status='INTERRUPTED_AUDIT_REQUIRED',error_type=type(e).__name__,formal_ready=False));raise

if __name__=='__main__':main()
