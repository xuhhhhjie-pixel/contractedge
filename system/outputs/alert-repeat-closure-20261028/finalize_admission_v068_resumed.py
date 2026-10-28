"""Close existing G3/G4/G5 and invoke the existing final admission checker.

Never dispatch models/graders or declare readiness for an incomplete panel.
Original effect thresholds and development exposure labels are retained.
"""
from datetime import datetime, timezone
import copy
import argparse
import json
from pathlib import Path
import sys

SYSTEM = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent
OUT = BASE / 'administrative-admission'
PARENT = Path('/home/xhj/contractedge-parent402-v068-20261028/system')
PANEL = Path('/home/xhj/contractedge-fixed46-official-v068-resumed-20261028/panel.json')
FREEZE = SYSTEM / 'docs/development/quote-binding-v068-20261028.snapshot.json'
sys.path[:0] = [str(PARENT / 'scripts'), str(SYSTEM / 'src')]
from assess_formal_readiness import assess, reference, sha, digest, read, check_gate_reports
from assess_reliability_delivery_v2 import assess as assess_effect
from fixed_panel import verify_panel, verified_records, b_gate
from verify_development_snapshot import verify_snapshot


def write(name, value):
    path = OUT / name
    with path.open('x') as f: json.dump(value, f, indent=2); f.write('\n')
    return path


def prepare_static(panel):
    identity = dict(source_sha256=panel['variants']['candidate']['source_sha256'], panel_sha256=sha(PANEL),
        candidate_config_sha256=panel['variants']['candidate']['config_sha256'], data_files_sha256=digest(panel['data_files']))
    old_refs = {k: reference(BASE / ('V068_' + k + '_EVIDENCE.json')) for k in ['G0','G1','G2','G6']}
    previous = read(BASE / 'V068_G0_EVIDENCE.json')['identity']
    assert previous['panel_sha256'] == panel['predecessor']['panel']['sha256']
    assert all(previous[k] == identity[k] for k in identity if k != 'panel_sha256')
    old_checks = check_gate_reports(old_refs, previous, BASE)
    assert all(old_checks[k]['status'] == 'PASS' for k in old_refs)
    qualification = Path(panel['parent_qualification']['path'])
    assert sha(qualification) == panel['parent_qualification']['sha256']
    qualified = read(qualification)
    assert qualified['status'] == 'PASS' and qualified['method_source_sha256'] == identity['source_sha256']
    assert all(sha(PARENT / k) == v for k,v in qualified['parent_files'].items())
    OUT.mkdir(exist_ok=True)
    for gate, ref in old_refs.items():
        path = OUT / ('V068_' + gate + '_EVIDENCE.json')
        if path.exists():
            assert read(path)['identity'] == identity
            continue
        value = copy.deepcopy(read(ref['path']))
        value.update(identity=identity, checker=reference(__file__), executed_at=datetime.now(timezone.utc).isoformat(),
            scope='Reuse verified unchanged method source/config/data evidence; explicitly bind same-source administrative parent amendment. No old method-version effectiveness is inherited.')
        value['inputs'] += [ref, reference(PANEL), reference(qualification)]
        if gate in {'G2','G6'}:
            value['checks'].append(dict(id='qualified_administrative_parent',status='PASS',
                evidence=[reference(qualification),qualified['test_summary']['receipt'],qualified['test_summary']['log'],qualified['actual_namespace_public_smoke'],qualified['actual_isolated_recovery']]))
        write('V068_' + gate + '_EVIDENCE.json', value)
    rebound = check_gate_reports({k:reference(OUT / ('V068_' + k + '_EVIDENCE.json')) for k in old_refs},identity,OUT)
    assert all(rebound[k]['status']=='PASS' for k in old_refs)
    return identity


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--prepare-static',action='store_true');args=parser.parse_args()
    panel = verify_panel(PANEL)
    if args.prepare_static:
        identity=prepare_static(panel)
        print(json.dumps(dict(status='PASS_REBOUND_UNCHANGED_STATIC_GATES',identity=identity,gates=['G0','G1','G2','G6'],new_model_calls=0,formal_ready=False)))
        return 0
    rows = verified_records(PANEL, panel)
    assert verify_snapshot(SYSTEM, read(FREEZE), require_current_bindings=True)['status'] == 'PASS'
    state = read(Path(panel['output_root']) / 'acceptance-state.json')
    # A real negative outcome and an unfinished cohort both remain visible.
    # This early return has no write side effect and can be checked at any time.
    if len(rows) != 46 or state['status'] != 'ORIGINAL46_CLOSED_PENDING_FINAL_EFFECT_AND_DELIVERY_ASSESSMENT':
        print(json.dumps(dict(status='NOT_READY_PANEL_UNFINISHED_OR_STOPPED',
            controller_status=state['status'], closed_slots=len(rows), expected_slots=46,
            new_model_calls=0, score_dispatches=0, formal_ready=False)))
        return 3
    assert len(panel['slots']) == 46
    records_path = Path(panel['output_root']) / 'records.json'
    identity = dict(source_sha256=panel['variants']['candidate']['source_sha256'], panel_sha256=sha(PANEL),
        candidate_config_sha256=panel['variants']['candidate']['config_sha256'], data_files_sha256=digest(panel['data_files']))
    registration_path = Path(panel['original_registration']['path'])
    registration = read(registration_path)
    effect = assess_effect(registration, rows)
    prerequisite = b_gate(panel, rows)
    stamp = datetime.now(timezone.utc).isoformat()

    def gate(name, checks, inputs, scope):
        return write('V068_' + name + '_FINAL_EVIDENCE.json', dict(schema_version='ce.formal_gate_evidence.v1',
            gate=name, identity=identity, executed_at=stamp, checker=reference(__file__),
            inputs=[reference(p) for p in inputs],
            checks=[dict(id=k, status='PASS' if passed else 'FAIL', evidence=[reference(p) for p in proof])
                for k, passed, proof in checks], scope=scope))

    prepare_static(panel)
    ci_path = BASE / 'V068_GITHUB_CI.json'
    ci = read(ci_path)
    assert ci['source_sha256'] == identity['source_sha256'] and ci['conclusion'] == 'success'
    assert any('Verify method namespaces' in step['name'] and step['conclusion'] == 'success'
        for job in ci['jobs'] for step in job['steps'])
    isolation_path = BASE / 'V068_ACTUAL_ISOLATION.json'
    isolation = read(isolation_path)
    assert isolation['status'] == 'PASS' and isolation['source_sha256'] == identity['source_sha256']
    assert isolation['evidence']['actual_runtime_sessions'] == 14
    terminals = write('V068_VERIFIED_TERMINALS.json', dict(status='ALL46_TERMINALS_VERIFIED', identity=identity,
        recorded_slots=len(rows), original_records=reference(records_path),
        verifier=reference(SYSTEM / 'scripts/fixed_panel.py'), records=rows, formal_ready=False))
    g3 = gate('G3', [
        ('method_filesystem_isolation', True, [ci_path, isolation_path, SYSTEM / 'tests/test_isolated_method.py']),
        ('per_attempt_source_config_data_patch_score_identity', True, [terminals, SYSTEM / 'scripts/fixed_panel.py'])],
        [ci_path, isolation_path, terminals, records_path],
        'Actual namespace/isolation evidence and all46 source/config/data/patch/score records reverified by the existing protocol. No unknown dispatch is retried. One explicit rejected HTTP402 is bound through declared administrative READY/received-response recovery; both infrastructure receipts and all prior resources remain, no uninterrupted-wall claim.')
    effect_path = write('V068_REGISTERED_EFFECT_ASSESSMENT.json', dict(identity=identity,
        records=reference(records_path), registration=reference(registration_path),
        evaluator=reference(SYSTEM / 'scripts/assess_reliability_delivery_v2.py'),
        effect=effect, prerequisite_gate=prerequisite, formal_ready=False))
    g4 = gate('G4', [
        ('original_successes_preserved', effect['historical_successes_preserved'], [effect_path, terminals]),
        ('registered_repetition', effect['critical_cases_pass_both_attempts'], [effect_path, terminals]),
        ('registered_seeded_recovery', effect['two_repository_recovery_pass_both_attempts'], [effect_path, terminals])],
        [effect_path, records_path, registration_path],
        'Original success, repeat and two-repository/two-attempt saved-failure rules. No qualification singles or historical best results imported.')
    benchmark = Path(panel['benchmark_root'])
    partition = {r['case_id']: r for r in [json.loads(s) for s in (benchmark / 'partition/member_groups.jsonl').read_text().splitlines() if s.strip()]}
    public = {r['case_id']: r for r in [json.loads(s) for s in (benchmark / 'public/manifest.jsonl').read_text().splitlines() if s.strip()]}
    exposures = []
    for group in ['A', 'B']:
        for entry in registration[group]:
            case = entry['case_id']
            split = partition[case].get('candidate_split', partition[case].get('split'))
            assert split == 'development'
            for key in ['source_sha256', 'task_manifest_sha256']:
                if key in entry: assert entry[key] == public[case][key]
            exposures.append(dict(case_id=case, group=group, actual_split=split,
                registered_exposure=entry['exposure'], source_sha256=public[case]['source_sha256']))
    exposure_path = write('V068_EXPOSURE_ACCOUNTING.json', dict(identity=identity, registration=reference(registration_path),
        current_partition=reference(benchmark / 'partition/member_groups.jsonl'), cases=exposures,
        scope='All cases are development. A exposed; B not used to change this frozen method. Historical unexposed status is not proven. No validation/blind or statistical-significance claim.', formal_ready=False))
    g5 = gate('G5', [
        ('complete_original_panel', len(rows) == 46 and not effect['missing'], [terminals, effect_path]),
        ('declared_paired_effect_gates', effect['delivery_effect_gates_passed'] and prerequisite['open'], [effect_path]),
        ('exposure_accounting', True, [exposure_path, registration_path])],
        [terminals, effect_path, exposure_path, records_path],
        'Original thresholds: no regression, all required behavior gates, strictly greater primary independent PASS, B not below baseline. No best-attempt selection.')
    gates = {k: reference(OUT / ('V068_' + k + '_EVIDENCE.json')) for k in ['G0', 'G1', 'G2', 'G6']}
    gates.update({k: reference(path) for k, path in [('G3', g3), ('G4', g4), ('G5', g5)]})
    manifest = write('V068_FINAL_ADMISSION_MANIFEST.json', dict(schema_version='ce.formal_admission_manifest.v1',
        system_root=str(SYSTEM), snapshot=reference(FREEZE), panel=reference(PANEL),
        records=reference(records_path), gates=gates,
        scope='Exact frozen candidate; no formal150 dispatch or new expenditure authorized by this aggregation.'))
    result = assess(manifest)
    write('V068_FINAL_ADMISSION.json', result)
    print(json.dumps({k: result[k] for k in ['status', 'formal_ready', 'new_model_http_calls']}))
    return 0 if result['formal_ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
