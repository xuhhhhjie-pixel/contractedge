"""Exact qualified source058 KPT, then gated Cert control, each once only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

SYSTEM = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent
ROOT = Path('/home/xhj/contractedge-e2e-source058-20261027')
FREEZE = SYSTEM / 'docs/development/planning-generation-continuity-v058-20261027-v1.snapshot.json'
CONFIG = SYSTEM / 'configs/fixed46-candidate-v058-budget150.json'
PYTHON = '/home/xhj/contractedge-latest-integration-20261024/.superpowers/sdd/2026-10-24-latest-main-integration/venv-v044/bin/python'
DEPS = '/home/xhj/contractedge-latest-integration-20261024/.superpowers/sdd/2026-10-24-latest-main-integration/empty-dependencies'
BENCH = '/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019'
sys.path[:0] = [str(SYSTEM / 'scripts')]
from run_isolated_method import execution_identity, check_registration, write_json
from run_fixed_panel import retire_workspace
from score_isolated_method import run as score
from verify_development_snapshot import verify_snapshot


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def exclusive(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        json.dump(value, stream, indent=2); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())


def main():
    assert not ROOT.exists()
    frozen = read(FREEZE)
    assert verify_snapshot(SYSTEM, frozen, require_current_bindings=True)['status'] == 'PASS'
    full = read(OUT / 'FULL_REGRESSION_COMPLETION_v058_v1.json')
    qualification = read(OUT / 'qualification-v058-v1-state.json')
    replay = read(OUT / 'SAVED_GENERATION_REPLAY.json')
    assert full['status'] == 'PASS' and full['exit_code'] == 0 and (full['tests'], full['subtests']) == (2100, 91)
    assert full['source_sha256'] == frozen['source_sha256'] and full['freeze_sha256'] == sha(FREEZE)
    assert full['log_sha256'] == sha(OUT / 'full-v058-v1.log')
    assert qualification['status'] == 'ZERO_CALL_QUALIFICATION_PASS_REQUIRES_NEW_DIAGNOSTIC_REGISTRATION'
    assert qualification['full_process_receipt_sha256'] == sha(OUT / 'FULL_REGRESSION_COMPLETION_v058_v1.json')
    assert replay['status'] == 'SAVED_FULL_FRONT_MODE_REPLAY_PASS' and replay['adapter_wire_changed_fields'] == ['reasoning_effort', 'thinking']
    exclusive(ROOT / 'experiment.json', dict(source_sha256=frozen['source_sha256'], freeze_sha256=sha(FREEZE),
        qualification_sha256=sha(OUT / 'qualification-v058-v1-state.json'), driver_sha256=sha(__file__),
        order=['R3-KPT-4673', 'RW-CERTMANAGER-9116'], once_each=True, max_model_calls_per_case=12,
        max_semantic_rounds=2, cumulative_budget_cny=150, counts_in_46_slot_panel=False, formal_ready=False,
        hypothesis='Preserve successful starvation recovery mode into later planning without spending another recovery; test resulting full repair and preselected repository control.',
        second_case_gate='KPT independentPASS/publicnotFAIL, exact source and complete clean receipt; otherwise NOT_RUN_PREREQUISITE_FAILED.'))
    state = dict(status='REGISTERED_KPT_THEN_GATED_CERT', rows=[], formal_ready=False)
    write_json(ROOT / 'state.json', state)
    try:
        for case in ['R3-KPT-4673', 'RW-CERTMANAGER-9116']:
            assert verify_snapshot(SYSTEM, frozen, require_current_bindings=True)['status'] == 'PASS'
            attempt = 'e2e-source058-' + case.lower() + '-20261027'
            output = ROOT / 'methods' / case / case; registration = ROOT / (case + '.registration.json')
            args = argparse.Namespace(method_root=str(SYSTEM), benchmark_root=BENCH, config=str(CONFIG),
                output=str(output), case=case, python=PYTHON, dependency_root=DEPS, seed=None,
                runtime_admission=None, panel_registration=None, panel_slot=None, attempt_id=attempt)
            identity = execution_identity(args)
            exclusive(registration, dict(identity, role='ISOLATED_DEVELOPMENT_VALIDATION', allow_paid=True,
                                          counts_in_46_slot_panel=False))
            check_registration(registration, identity)
            output.parent.mkdir(parents=True, exist_ok=False)
            state.update(status='RUNNING_ONCE', case_id=case); write_json(ROOT / 'state.json', state)
            command = [PYTHON, str(SYSTEM / 'scripts/run_isolated_method.py'), '--execute', '--method-root', str(SYSTEM),
                '--benchmark-root', BENCH, '--config', str(CONFIG), '--output', str(output), '--case', case,
                '--python', PYTHON, '--dependency-root', DEPS, '--registration', str(registration),
                '--attempt-id', attempt, '--api-key-file', '/home/xhj/.config/contractedge/chatanywhere-experiment-20261019.key']
            with (output.parent / 'method.log').open('x') as log:
                code = subprocess.call(command, cwd=SYSTEM, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), stdout=log, stderr=subprocess.STDOUT)
            if code:
                raise RuntimeError('Method admission/infrastructure stopped; never restart this attempt')
            receipts = list(output.parent.glob('.isolated-*/receipt.json')); assert len(receipts) == 1
            receipt = read(receipts[0]); assert receipt['status'] == 'METHOD_COMPLETE_PENDING_INDEPENDENT_SCORING'
            assert receipt['method_source_unchanged'] and receipt['cleanup']['status'] == 'OWNED_CONTAINERS_CLOSED'
            trace = read(output / 'repair.trace.json')
            state.update(status='PARENT_SCORING', case_id=case); write_json(ROOT / 'state.json', state)
            result = score(receipts[0], BENCH, ROOT / 'independent' / case) if trace.get('candidate') else dict(status='NO_CANDIDATE')
            row = dict(case_id=case, method_status=trace['status'], independent_status=result['status'],
                public_outcome=(trace.get('last_feedback') or {}).get('semantic_outcome', 'UNKNOWN'),
                patch_bytes=(trace.get('candidate') or {}).get('patch_bytes', 0), output=str(output),
                model_calls=trace.get('budget', {}).get('model_calls'),
                evidence_files=dict(receipt=dict(path=str(receipts[0]), sha256=sha(receipts[0])),
                                    trace=dict(path=str(output / 'repair.trace.json'), sha256=sha(output / 'repair.trace.json'))))
            state['rows'].append(row); write_json(ROOT / 'state.json', state)
            assert verify_snapshot(SYSTEM, frozen, require_current_bindings=True)['status'] == 'PASS'
            if result['status'] not in {'PASS', 'SEMANTIC_FAIL', 'EXECUTION_FAIL', 'BUILD_FAIL', 'PATCH_APPLY_FAIL', 'METHOD_FAIL', 'FAIL', 'NO_CANDIDATE'}:
                raise RuntimeError('Independent outcome unresolved; no next case')
            if trace.get('candidate'):
                (ROOT / 'queue' / attempt).mkdir(parents=True)
                retire_workspace(dict(transient_workspace_policy='RETIRE_CLOSED_SCORED_PUBLIC_WORKSPACE', output_root=str(ROOT)),
                    dict(status=result['status'], slot_id=attempt, evidence_files=row['evidence_files']))
            if result['status'] != 'PASS' or row['public_outcome'] == 'FAIL':
                state.update(status='CLOSED_EFFECT_PREREQUISITE_FAILED', remaining='NOT_RUN_PREREQUISITE_FAILED')
                write_json(ROOT / 'state.json', state); return
        state.update(status='CLOSED_TWO_CASES_REQUIRES_ACTUAL_MECHANISM_AUDIT'); write_json(ROOT / 'state.json', state)
    except BaseException as error:
        state.update(status='STOP_NO_AUTOMATIC_RETRY', error_type=type(error).__name__)
        write_json(ROOT / 'state.json', state); raise


if __name__ == '__main__':
    main()
