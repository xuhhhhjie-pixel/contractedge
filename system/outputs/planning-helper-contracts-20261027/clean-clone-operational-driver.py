"""One zero-HTTP operational replay of a sealed public development candidate.

Run with the clean checkout's fresh interpreter. Scoring stays parent-only;
only its aggregate is read. This is not another fixed-panel repair attempt.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone

CLONE = Path('/home/xhj/contractedge-cleancheck-v060-ordering-20261027')
SYSTEM = CLONE / 'system'
ROOT = CLONE / 'operational-real-rw130'
BENCH = Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')
PANEL = Path('/home/xhj/contractedge-fixed46-source060-20261027/panel.json')
sys.path.insert(0, str(SYSTEM / 'scripts'))
from contractedge.execution.public import BenchmarkPublicRunner
from contractedge.source.snapshot import compute_snapshot
import isolated_public_worker as public
from score_isolated_method import finished_score, score_outcome


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    with Path(path).open('x') as stream: json.dump(value, stream, indent=2); stream.write('\n')


def ledger_identity():
    d = json.loads(Path('/home/xhj/contractedge-reports/deepseek-batch-readiness-20261019/money-ledger.json').read_text())
    return {'calls': len(d['calls']), 'limit_micro_cny': d['settings']['limit_micro_cny']}


def make_runner():
    return BenchmarkPublicRunner(BENCH / 'public', BENCH / 'tools', lock_root=ROOT / 'locks', timeout_seconds=1200)


def child():
    ROOT.mkdir(exist_ok=False)
    producer = ROOT / 'producer'; producer.mkdir()
    transfer = ROOT / 'transfer'; transfer.mkdir()
    session = public.PublicSession(producer, transfer, 'RW-CAND-130', make_runner(), max_timeout=1200)
    prepared = session.prepare({'case_id': 'RW-CAND-130', 'timeout_seconds': 1200})
    source = Path(prepared['record']['source_root'])
    stage = transfer / ('a' * 32)
    shutil.copytree(source, stage, symlinks=True)
    original = json.loads((Path(json.loads(PANEL.read_text())['output_root']) / 'records.json').read_text())
    row = next(r for r in original if r['case_id'] == 'RW-CAND-130' and r['variant'] == 'candidate')
    trace = json.loads(Path(row['evidence_files']['trace']['path']).read_text())
    patch = Path(trace['candidate']['patch_path'])
    assert sha(patch) == trace['candidate']['patch_sha256']
    result = subprocess.run(['git', '-C', str(stage), 'apply', '--check', str(patch)], capture_output=True)
    assert result.returncode == 0, result.stderr
    subprocess.run(['git', '-C', str(stage), 'apply', str(patch)], check=True)
    target = compute_snapshot(stage).snapshot_id
    assert target == trace['final_snapshot_id']
    write(ROOT / 'sealed-input.json', {'original_slot': row['slot_id'], 'original_trace_sha256': sha(row['evidence_files']['trace']['path']),
        'sealed_patch_path': str(patch), 'sealed_patch_sha256': sha(patch), 'original_snapshot': prepared['snapshot_id'],
        'candidate_snapshot': target, 'source_sha256': json.loads(PANEL.read_text())['variants']['candidate']['source_sha256']})
    old_write = public.write_json
    def die_after_source_swap(path, value):
        if Path(path).name == 'source-switch.json' and value.get('status') == 'DONE': os._exit(73)
        old_write(path, value)
    public.write_json = die_after_source_swap
    session._sync({'stage_id': 'a' * 32, 'snapshot_id': target, 'timeout_seconds': 1200})
    raise AssertionError('The real crash boundary was not reached')


def parent():
    assert not ROOT.exists(), 'Operational run already exists; never overwrite or resend'
    before = ledger_identity()
    # No credentials are passed to either operational process.
    child_env = {k: v for k, v in os.environ.items() if not any(s in k.upper() for s in ['API_KEY', 'TOKEN', 'SECRET'])}
    child_env['PYTHONDONTWRITEBYTECODE'] = '1'
    process = subprocess.run([sys.executable, __file__, '--crash-child'], env=child_env)
    assert process.returncode == 73
    inputs = json.loads((ROOT / 'sealed-input.json').read_text())
    recovered = public.recover_pending_source(ROOT / 'producer')
    assert recovered['status'] == 'ROLLED_BACK' and recovered['restored_snapshot'] == inputs['original_snapshot']
    assert recovered['candidate_exported'] is False and recovered['budget_reset'] is False
    again = public.recover_pending_source(ROOT / 'producer')
    assert again['status'] == 'NO_PENDING_SOURCE_SWITCH'
    record = json.loads((ROOT / 'producer/workspace/workspace.json').read_text())
    session = public.PublicSession(ROOT / 'producer', ROOT / 'transfer', 'RW-CAND-130', make_runner(), max_timeout=1200)
    session.record = record
    session.inputs_snapshot = compute_snapshot(record['inputs_root']).snapshot_id
    session.source_links = public.trusted_source_links(record['source_root'])
    request = {'stage_id': 'a' * 32, 'snapshot_id': inputs['candidate_snapshot'], 'timeout_seconds': 1200}
    # Explicit operator replay of known sealed bytes after audited rollback;
    # not automatic replay of a model/RPC request with an unknown response.
    observed = session.observe(request)
    report = observed['report']
    assert report['snapshot_id'] == inputs['candidate_snapshot']
    assert report['status'] == 'EXECUTION_PASS', report['status']
    exported = session.export(request)
    patch = Path(exported['patch'])
    assert sha(patch) == inputs['sealed_patch_sha256']
    score_root = ROOT / 'independent'; score_root.mkdir()
    identity = {'case_id': 'RW-CAND-130', 'patch_sha256': sha(patch), 'patch_path': str(patch),
        'public_manifest_sha256': sha(BENCH / 'public/manifest.jsonl'),
        'evaluation_manifest_sha256': sha(BENCH / 'evaluation/manifest.jsonl'),
        'score_tool_sha256': sha(BENCH / 'tools/score_case.py'),
        'candidate_snapshot': inputs['candidate_snapshot'], 'operational_only': True}
    command = [sys.executable, str(BENCH / 'tools/score_case.py'), '--evaluation-root', str(BENCH / 'evaluation'),
        '--case-id', 'RW-CAND-130', '--patch', str(patch), '--output', str(score_root / 'native')]
    with (score_root / 'grader.log').open('wb') as log:
        code = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=1200).returncode
    native = score_root / 'native/score.json'
    write(score_root / 'checkpoint.json', {'status': 'TOOL_FINISHED', 'identity': identity, 'returncode': code,
        'timed_out': False, 'score_sha256': sha(native), 'grader_log_sha256': sha(score_root / 'grader.log')})
    # Completed score survives loss of the parent aggregation step. Finalize
    # twice using the existing checker; the scoring process is never rerun.
    checkpoint_hash = sha(score_root / 'checkpoint.json')
    saved, value, _ = finished_score(score_root, identity)
    status = score_outcome(saved, value)
    assert status == 'PASS'
    stamp = native.stat().st_mtime_ns
    saved2, value2, _ = finished_score(score_root, identity)
    assert score_outcome(saved2, value2) == status and native.stat().st_mtime_ns == stamp
    assert sha(score_root / 'checkpoint.json') == checkpoint_hash
    result = {'schema_version': 'ce.clean_clone_operational_replay.v1', 'status': 'PASS',
        'executed_at': datetime.now(timezone.utc).isoformat(), 'clone_commit': subprocess.check_output(['git', '-C', str(CLONE), 'rev-parse', 'HEAD'], text=True).strip(),
        'installed_module': __import__('contractedge').__file__, 'source_sha256': inputs['source_sha256'],
        'case_id': 'RW-CAND-130', 'sealed_input': inputs,
        'public_status': report['status'], 'public_observation': observed['output'], 'export': exported['result'],
        'independent_status': status, 'source_swap_real_process_exit': process.returncode,
        'source_recovery': recovered, 'recovery_second_call': again,
        'completed_score_finalize_twice': True, 'score_dispatches': 1, 'score_checkpoint_sha256': checkpoint_hash,
        'new_model_http_calls': 0, 'counts_in_46_slot_panel': False, 'formal_ready': False,
        'ledger_before': before, 'ledger_after': ledger_identity(),
        'scope': 'Same-server clean GitHub clone and fresh venv, real public benchmark candidate reconstruction/execution/export/independent scoring plus source crash and score finalization recovery. Uses a sealed existing candidate; not a fresh model repair, another-host certification, or a fixed46 result.'}
    write(ROOT / 'operational-readiness.json', result)
    print(json.dumps({k: result[k] for k in ['status', 'public_status', 'independent_status', 'new_model_http_calls', 'counts_in_46_slot_panel']}))


if __name__ == '__main__':
    child() if '--crash-child' in sys.argv else parent()
