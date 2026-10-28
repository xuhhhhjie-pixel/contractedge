"""Verify existing live request boundaries and exact-head CI; no dispatch."""
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

SYSTEM = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
WORK = SYSTEM / '.superpowers/sdd/2026-10-28-behavior-plan-closure'
PANEL = Path('/home/xhj/contractedge-fixed46-official-v068-20261028/panel.json')
FREEZE = SYSTEM / 'docs/development/quote-binding-v068-20261028.snapshot.json'
sys.path[:0] = [str(SYSTEM / 'src'), str(SYSTEM / 'scripts')]
from assess_formal_readiness import reference, sha, digest, read
from fixed_panel import verify_panel, verified_records


def write(name, value):
    path = OUT / name
    with path.open('x') as f: json.dump(value, f, indent=2); f.write('\n')
    return path


def main():
    panel = verify_panel(PANEL)
    frozen = read(FREEZE)
    identity = dict(source_sha256=frozen['source_sha256'], panel_sha256=sha(PANEL),
        candidate_config_sha256=panel['variants']['candidate']['config_sha256'], data_files_sha256=digest(panel['data_files']))
    row = next(r for r in verified_records(PANEL, panel) if r['case_id'] == 'R3-KPT-4673' and r['variant'] == 'candidate')
    assert row['status'] == 'PASS'
    receipt_path = Path(row['evidence_files']['receipt']['path'])
    receipt = read(receipt_path)
    calls_path = Path(receipt['state_root']) / 'parent-model-calls.json'
    calls = read(calls_path)
    assert len(calls) == 3
    stages = []
    sid = None
    for index, call in enumerate(calls):
        body = json.loads(call['messages'][1]['content'])
        current = body.get('run_context', {}).get('snapshot_id') or body.get('accepted_plan', {}).get('snapshot_id')
        sid = current if sid is None else sid
        assert current == sid
        sources = body.get('evidence_catalog', body.get('read_only_source_support', []))
        sources = [r for r in sources if r.get('kind') == 'SOURCE_SPAN']
        assert sources
        for source in sources:
            assert source['snapshot_id'] == sid
            assert hashlib.sha256(source['excerpt'].encode()).hexdigest() == source['content_digest']
        code = '\n'.join(source['excerpt'] for source in sources)
        required = (['runMutators', 'runValidators', 'runPipeline'] if index == 0 else
            ['func SetResourceIDs', 'func DeleteResourceIDs', 'func MergeWithInput'])
        assert all(term in code for term in required)
        assert call['response_metadata']['model_identity_verified'] is True
        stages.append(dict(prompt_version=call['prompt_version'], snapshot_id=sid,
            current_source_records=[dict(id=r['id'], path=r['locator']['path'], content_digest=r['content_digest']) for r in sources],
            confirmed_source_terms=required, request_digest=call['request_digest'],
            scope='Diagnosis gets production entry bodies; planning and execution get prerequisite helper bodies. Not every helper is required in diagnosis.'))
    replay_path = OUT / 'V068_PUBLIC_FAILURE_REPLAY.json'
    replay = read(replay_path)
    assert replay['status'] == 'PASS' and replay['source_sha256'] == identity['source_sha256']
    assert replay['model_http_calls_added'] == 0
    delivery = write('V068_CURRENT_REQUEST_DELIVERY.json', dict(status='PASS', identity=identity, stages=stages,
        receipt=reference(receipt_path), parent_calls=reference(calls_path), trace=row['evidence_files']['trace'],
        saved_failure_current_replanning=reference(replay_path), new_model_calls=0,
        scope='Actual final diagnosis/planning/execution requests for current KPT plus current-code saved-failure rediagnosis replay. Finite relevant source delivery, not universal diagnosis correctness.', formal_ready=False))
    ci = read(WORK / 'ci-v068-push.json')
    assert ci['event'] == 'push' and ci['status'] == 'completed' and ci['conclusion'] == 'success'
    assert len(ci['jobs']) == 5 and all(j['conclusion'] == 'success' for j in ci['jobs'])
    archive = subprocess.check_output(['git', '-C', str(SYSTEM.parent), 'archive', ci['headSha'], 'system/src', '.github/workflows/development.yml'])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        files = {m.name.removeprefix('system/src/'): hashlib.sha256(tar.extractfile(m).read()).hexdigest()
            for m in tar.getmembers() if m.isfile() and m.name.startswith('system/src/') and Path(m.name).suffix in {'.py', '.json'}}
        workflow = tar.extractfile('.github/workflows/development.yml').read()
    assert files == frozen['files']
    assert hashlib.sha256(workflow).hexdigest() == frozen['repository_files']['.github/workflows/development.yml']
    ci.update(run_id=40674568912, url='https://github.com/xuhhhhjie-pixel/contractedge/actions/runs/40674568912',
        source_sha256=identity['source_sha256'], exact_push_head_source_and_workflow_verified=True)
    ci_path = write('V068_GITHUB_CI.json', ci)
    full_path = OUT / 'V068_FULL_REGRESSION.json'
    full = read(full_path)
    assert full['status'] == 'PASS' and full['source_sha256'] == identity['source_sha256']
    assert full['log_sha256'] == sha(OUT / 'full-v068.log')
    proofs = {'exact_source_full_suite': [full_path, OUT / 'full-v068.log'],
        'current_cross_stage_delivery': [delivery, replay_path, OUT / 'V068_KPT_PLAN_CORRECTION.json'],
        'supported_environment_ci': [ci_path]}
    write('V068_G2_EVIDENCE.json', dict(schema_version='ce.formal_gate_evidence.v1', gate='G2', identity=identity,
        executed_at=datetime.now(timezone.utc).isoformat(), checker=reference(__file__),
        inputs=[reference(p) for p in [FREEZE, PANEL, full_path, delivery, replay_path, ci_path]],
        checks=[dict(id=k, status='PASS', evidence=[reference(p) for p in paths]) for k, paths in proofs.items()],
        scope='Exact current source full suite, actual relevant request boundaries plus saved failure replay, and actual push-head CI. No overall repair-rate claim.'))
    print(json.dumps(dict(status='ENGINEERING_EVIDENCE_BOUND', gates=['G2'], formal_ready=False)))


if __name__ == '__main__':
    main()
