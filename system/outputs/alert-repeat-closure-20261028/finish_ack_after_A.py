"""One serial follower; never retries a method or bypasses a failed A gate."""
import fcntl,json,os,signal,subprocess,sys,time
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
S=Path('/home/xhj/contractedge-alert-closure-20261028/system');H=Path(__file__).resolve().parent
R=Path('/home/xhj/contractedge-fixed46-official-v068-20261028');C=Path('/home/xhj/contractedge-ack402-continuation-v068-20261028')
read=lambda p:json.loads(Path(p).read_text())
stamp=lambda:datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
state=C/'serial-follower-state.json'
child=None

def write(value):state.write_text(json.dumps(value,indent=2)+'\n')
def stop(*_):raise KeyboardInterrupt('Explicit stop; no continuation retry')
signal.signal(signal.SIGTERM,stop)
assert not state.exists(),'Preserve previous follower claim; no automatic retry'
write(dict(status='WAITING_EXISTING_A_CONTROLLER',owner_pid=os.getpid(),started_at=stamp(),model_calls_added=0,formal_ready=False))
try:
    while True:
        active=read(S/'CURRENT_STATUS.json')['current_development']
        if active.get('user_paused') is True:
            write(dict(status='STOPPED_USER_PAUSED',at=stamp(),new_calls=0,formal_ready=False));break
        with (R/'phase-controller.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:pass
            else:
                control=read(R/'acceptance-state.json')
                if control['status']!='A_OTHER_SLOTS_CLOSED_ACK_INFRASTRUCTURE_PENDING':
                    write(dict(status='STOPPED_PREDECESSOR_NOT_PASSED',controller_status=control['status'],at=stamp(),new_calls=0,formal_ready=False));break
                # Child obtains this same lock immediately after release. It
                # also refuses a known effect failure and has a one-shot claim.
                fcntl.flock(lock,fcntl.LOCK_UN)
                write(dict(status='DISPATCHING_ONCE_EXISTING_READY',at=stamp(),formal_ready=False))
                child=subprocess.Popen([sys.executable,str(H/'run_ack402_ready.py'),'--execute'],env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
                code=child.wait();write(dict(status='FOLLOWER_FINISHED' if code==0 else 'AUDIT_REQUIRED',returncode=code,at=stamp(),formal_ready=False));break
        time.sleep(2)
except BaseException as exc:
    if child and child.poll() is None:
        child.terminate()
        try:child.wait(timeout=45)
        except subprocess.TimeoutExpired:child.kill();child.wait(timeout=10)
    write(dict(status='STOPPED_OR_AUDIT_REQUIRED',error_type=type(exc).__name__,at=stamp(),automatic_retry=False,formal_ready=False));raise
