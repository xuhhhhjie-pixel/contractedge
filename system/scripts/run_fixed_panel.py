"""Durable parent-only fixed46 queue; methods and graders remain separate.

Resumption only verifies an already closed method or finalizes a completed
grader. An uncertain method admission is never resent under the same slot.
"""
import argparse
from decimal import Decimal, ROUND_CEILING
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
from types import SimpleNamespace

sys.dont_write_bytecode = True
from fixed_panel import (read, sha, digest, verify_panel, validate_slot_registration,
                         verified_records, verify_record, b_gate, TERMINAL_OUTCOMES)
from run_isolated_method import execution_identity, write_json


class Transport:
    def identity(self, args): return execution_identity(args)

    def dispatch(self, command, log):
        with Path(log).open('wb') as stream:
            process = subprocess.Popen(command, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
                                       stdout=stream, stderr=subprocess.STDOUT)
            try:
                return process.wait()
            except BaseException:
                # The isolated coordinator owns cleanup and durable receipts.
                # Give it SIGTERM before any forced termination of the child.
                if process.poll() is None:
                    process.terminate()
                    try: process.wait(timeout=45)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait(timeout=10)
                raise

    def score(self, receipt, benchmark, output, *, phase):
        from score_isolated_method import run
        return run(receipt, benchmark, output, phase=phase)


def exclusive(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, sort_keys=True, allow_nan=False); stream.write('\n')
        stream.flush(); os.fsync(stream.fileno())
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(parent)
    finally: os.close(parent)


def arguments(panel, slot, panel_path, key):
    variant = panel['variants'][slot['variant']]; runtime = panel['runtime']
    root = Path(panel['output_root']).resolve()
    return SimpleNamespace(execute=True, inspect=False, dry_public=False,
        method_root=variant['root'], benchmark_root=panel['benchmark_root'], config=variant['config'],
        output=str(root/'methods'/slot['slot_id']/slot['case_id']), case=slot['case_id'],
        python=runtime['python'], dependency_root=runtime['dependency_root'],
        runtime_admission=runtime.get('runtime_admission'),
        seed=panel['seeds'][slot['case_id']]['path'] if slot['group'] == 'recovery' else None,
        attempt_id=slot['slot_id'], registration=str(root/'registrations'/(slot['slot_id']+'.json')),
        api_key_file=str(key), panel_registration=str(Path(panel_path).resolve()), panel_slot=slot['slot_id'])


def command(args):
    result = [args.python, str(Path(__file__).with_name('run_isolated_method.py')), '--execute']
    for name in ['method_root', 'benchmark_root', 'config', 'output', 'case', 'python', 'dependency_root',
                 'registration', 'api_key_file', 'runtime_admission', 'seed', 'attempt_id', 'panel_registration', 'panel_slot']:
        value = getattr(args, name)
        if value is not None: result += ['--' + name.replace('_', '-'), str(value)]
    return result


def find_receipt(args, identity):
    found = []
    for path in Path(args.output).parent.glob('.isolated-*/receipt.json'):
        if read(path).get('identity') == identity: found.append(path)
    if len(found) != 1:
        raise ValueError('Method dispatch outcome uncertain; no closed unique receipt, never auto-resend')
    receipt = read(found[0])
    if (receipt.get('status') != 'METHOD_COMPLETE_PENDING_INDEPENDENT_SCORING'
            or receipt.get('cleanup', {}).get('status') != 'OWNED_CONTAINERS_CLOSED'
            or receipt.get('terminal_validation', {}).get('classification') != 'METHOD_RESULT'):
        raise ValueError('Method dispatch outcome uncertain or infrastructure stop; operator audit required')
    return found[0], receipt


def retire_workspace(panel, record):
    """Retire only reconstructible source/cache after closed verified scoring.

    Receipts, journal, patches, observations and independent outputs remain.
    Restoring the public archive plus the sealed patch reconstructs the source.
    """
    if panel.get('transient_workspace_policy') != 'RETIRE_CLOSED_SCORED_PUBLIC_WORKSPACE': return
    if record['status'] not in TERMINAL_OUTCOMES: return
    receipt_path = Path(record['evidence_files']['receipt']['path'])
    receipt = read(receipt_path)
    if Path(receipt['state_root']).resolve() != receipt_path.parent.resolve():
        raise ValueError('Public workspace owner does not match the closed receipt')
    producer = receipt_path.parent/'producer'; workspace = producer/'workspace'
    if producer.is_symlink() or workspace.is_symlink():
        raise ValueError('Public workspace is a foreign symlink; refusing retirement')
    retired = workspace.exists()
    if retired:
        if not workspace.resolve().is_relative_to(receipt_path.parent.resolve()):
            raise ValueError('Public workspace escapes the closed session')
        shutil.rmtree(workspace)
    path = Path(panel['output_root'])/'queue'/record['slot_id']/'workspace-retirement.json'
    if not path.exists():
        write_json(path, {'status': 'RETIRED' if retired else 'ABSENT', 'path': str(workspace),
            'receipt_sha256': sha(receipt_path), 'trace_sha256': record['evidence_files']['trace']['sha256'],
            'retained': ['method journal', 'candidate and rejected patches', 'public observations', 'independent score'],
            'reconstruction': 'Frozen public archive plus sealed candidate patch; no model call'})


def run_slot(panel_path, slot_id, key, *, transport=None):
    panel = verify_panel(panel_path); rows = verified_records(panel_path, panel)
    slot = next((s for s in panel['slots'] if s['slot_id'] == slot_id), None)
    if slot is None: raise ValueError('Unknown registered slot')
    prior = next((r for r in rows if r['slot_id'] == slot_id), None)
    if prior is not None:
        retire_workspace(panel, prior)
        return prior
    transport = transport or Transport(); args = arguments(panel, slot, panel_path, key)
    identity = transport.identity(args)
    authority = {**identity, 'role': 'ISOLATED_FIXED_PANEL_SLOT', 'allow_paid': True,
        'counts_in_46_slot_panel': True, 'panel_registration_sha256': sha(panel_path), 'panel_slot_id': slot_id}
    validate_slot_registration(authority, identity, panel_path)
    root = Path(panel['output_root']); directory = root/'queue'/slot_id
    directory.mkdir(parents=True, exist_ok=True); state_path = directory/'state.json'
    if state_path.exists():
        state = read(state_path)
        if state.get('identity_sha256') != digest(identity) or state.get('panel_sha256') != sha(panel_path):
            raise ValueError('Method environment or dispatch identity changed')
        if read(args.registration) != authority: raise ValueError('Registered dispatch authority changed')
        receipt_path, receipt = find_receipt(args, identity)
    else:
        if Path(args.registration).exists() or Path(args.output).exists():
            raise ValueError('Earlier admission uncertain; never overwrite or create another attempt')
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        exclusive(args.registration, authority)
        state = {'status': 'DISPATCH_INTENT', 'identity_sha256': digest(identity),
                 'panel_sha256': sha(panel_path), 'slot_id': slot_id}
        exclusive(state_path, state)
        code = transport.dispatch(command(args), directory/'method.log')
        if code != 0:
            write_json(state_path, {**state, 'status': 'METHOD_RETURNED_ERROR', 'returncode': code})
            raise ValueError('Method infrastructure/admission failed; no automatic retry')
        receipt_path, receipt = find_receipt(args, identity)
    write_json(state_path, {**state, 'status': 'METHOD_DONE', 'receipt': str(receipt_path)})
    trace_path = Path(args.output)/'repair.trace.json'; trace = read(trace_path)
    refs = {'receipt': {'path': str(receipt_path), 'sha256': sha(receipt_path)},
            'trace': {'path': str(trace_path), 'sha256': sha(trace_path)}}
    if trace.get('candidate'):
        score_root = root/'independent'/slot_id
        result = transport.score(receipt_path, panel['benchmark_root'], score_root,
                                 phase='finalize' if (score_root/'checkpoint.json').exists() else 'all')
        independent = result['status']; refs['score'] = {'path': str(score_root/'result.json'), 'sha256': sha(score_root/'result.json')}
    else: independent = 'NO_CANDIDATE'
    public = {'PUBLIC_REVIEW_PASS': 'PASS', 'PUBLIC_REVIEW_FAIL': 'FAIL'}.get(
        (trace.get('last_feedback') or {}).get('semantic_outcome'), 'UNKNOWN')
    record = {**slot, 'independent_status': independent, 'public_decision': public,
        # Independent repair-rate statistics and public acceptance are separate.
        # B admission checks public_decision explicitly; never rewrite a score.
        'status': independent,
        'method_status': trace.get('status'), 'terminal_verified': True, 'counts_in_46_slot_panel': True,
        'identity_sha256': digest(identity), 'evidence_files': refs, 'formal_ready': False}
    verify_record(panel_path, panel, record)
    write_json(root/'records.json', rows + [record])
    write_json(state_path, {**state, 'status': 'TERMINAL_RECORDED', 'receipt': str(receipt_path)})
    retire_workspace(panel, record)
    return record


def pair_budget(panel, slots):
    """Admission estimate only; the existing locked money ledger is authoritative."""
    budgets = [read(panel['variants'][s['variant']]['config']) for s in slots]
    money = budgets[0]['money_budget']; ledger = read(money['path'])
    if any(c['money_budget'] != money for c in budgets): raise ValueError('Both variants must share one money ledger')
    spent = sum(c.get('charged_micro_cny', c['reserved_micro_cny']) for c in ledger['calls'])
    bound = Decimal(0)
    for config in budgets:
        total = int(config['max_total_tokens']); output = min(total, int(config['max_model_calls']) * max(
            int(config[k]) for k in ['max_output_tokens', 'execution_max_output_tokens', 'review_max_output_tokens']))
        bound += (Decimal(total-output)*Decimal(money['input_cny_per_million'])
                  + Decimal(output)*Decimal(money['output_cny_per_million']))
    limit = min(ledger['settings']['limit_micro_cny'], int(Decimal(str(money['limit_cny']))*1000000))
    required = int(bound.to_integral_value(rounding=ROUND_CEILING))
    return {'admitted': limit-spent >= required, 'required_micro_cny': required, 'remaining_micro_cny': limit-spent}


def run_panel(panel_path, group, key):
    panel = verify_panel(panel_path); root = Path(panel['output_root']); root.mkdir(parents=True, exist_ok=True)
    with (root/'controller.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        selected = [s for s in panel['slots'] if group == 'all' or s['group'] == group]
        rows = verified_records(panel_path, panel)
        done = {r['slot_id'] for r in rows}
        for index, slot in enumerate(selected):
            if slot['slot_id'] in done: continue
            # Admit the remaining pair before dispatching its first member.
            pair = [s for s in selected if (s['group'], s['case_id'], s['repeat']) ==
                    (slot['group'], slot['case_id'], slot['repeat']) and s['slot_id'] not in done]
            budget = pair_budget(panel, pair)
            if not budget['admitted']:
                write_json(root/'state.json', {'status': 'BUDGET_PAIR_ADMISSION_STOP', 'next_slot': slot, 'budget': budget})
                return 2
            write_json(root/'state.json', {'status': 'RUNNING', 'active_slot': slot, 'panel_sha256': sha(panel_path)})
            row = run_slot(panel_path, slot['slot_id'], key)
            rows = verified_records(panel_path, panel); done = {r['slot_id'] for r in rows}
            print(json.dumps({k: row[k] for k in ['slot_id', 'group', 'case_id', 'variant', 'status', 'public_decision']}, sort_keys=True), flush=True)
            if row['status'] not in TERMINAL_OUTCOMES:
                write_json(root/'state.json', {'status': 'SCORING_INFRASTRUCTURE_STOP', 'slot': slot}); return 2
        write_json(root/'state.json', {'status': 'GROUP_COMPLETE', 'group': group, 'records': len(rows),
            'B_gate': b_gate(panel, rows), 'formal_ready': False})
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--panel', required=True, type=Path)
    parser.add_argument('--group', choices=['A', 'repeat', 'recovery', 'B', 'all'], default='A')
    parser.add_argument('--dry-run', action='store_true'); parser.add_argument('--api-key-file', type=Path)
    args = parser.parse_args(); panel = verify_panel(args.panel)
    if args.dry_run:
        print(json.dumps({'slots': panel['slots'], 'count': len(panel['slots']), 'dispatches': 0,
                          'formal_ready': False}, sort_keys=True)); return 0
    if args.api_key_file is None: parser.error('Execution needs a parent-only credential file')
    def interrupted(*_): raise KeyboardInterrupt('Owned panel controller interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    try:
        return run_panel(args.panel, args.group, args.api_key_file)
    except BaseException as error:
        # A stale RUNNING state is not evidence of a live job after exit.
        state_path = Path(panel['output_root'])/'state.json'
        previous = read(state_path) if state_path.exists() else {}
        state_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(state_path, {**previous, 'status': 'AUDIT_REQUIRED',
                               'error_type': type(error).__name__, 'automatic_method_retry': False})
        raise


if __name__ == '__main__': raise SystemExit(main())
