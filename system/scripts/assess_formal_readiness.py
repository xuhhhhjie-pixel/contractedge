"""Parent-only, zero-model aggregation of the existing G0--G6 admission gates.

Reports are trusted checker outputs, not self-authenticating proofs. Their
checker, inputs and per-check evidence bytes must be retained and hash-bound.
Panel scores are revalidated by the existing terminal/scoring protocol rather
than accepted from a cached PASS. This command never dispatches a worker,
changes runtime configuration, starts formal150 or authorizes expenditure.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixed_panel import verify_panel, verified_records, b_gate, digest
from assess_reliability_delivery_v2 import assess as assess_effect
from verify_development_snapshot import verify_snapshot


# These are the original delivery plan's gates, not additional model tasks.
REQUIRED_CHECKS = {
    'G0': ('membership150', 'public_evaluation_mapping', 'archive_grader_partition_integrity',
           'release_layout_mapping', 'resource_preflight150'),
    'G1': ('obligation_lineage', 'operation_fact_consistency', 'helper_prerequisites',
           'bounded_gap_delivery', 'candidate_preservation', 'feedback_unknown_reasons'),
    'G2': ('exact_source_full_suite', 'current_cross_stage_delivery', 'supported_environment_ci'),
    'G3': ('method_filesystem_isolation', 'per_attempt_source_config_data_patch_score_identity'),
    'G4': ('original_successes_preserved', 'registered_repetition', 'registered_seeded_recovery'),
    'G5': ('complete_original_panel', 'declared_paired_effect_gates', 'exposure_accounting'),
    'G6': ('clean_clone_real_roundtrip', 'durable_crash_recovery', 'resource_and_drift_checks',
           'declared_serial_execution'),
}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''): h.update(part)
    return h.hexdigest()


def reference(path):
    return {'path': str(Path(path).absolute()), 'sha256': sha(path)}


def checked_reference(ref, base):
    if (not isinstance(ref, dict) or set(ref) != {'path', 'sha256'}
            or not isinstance(ref['path'], str) or not ref['path']
            or not isinstance(ref['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', ref['sha256'])):
        raise ValueError('Evidence requires an exact path/SHA256 reference')
    path = Path(ref['path'])
    if not path.is_absolute(): path = Path(base) / path
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError('Symlink evidence is not an immutable regular file')
    if not path.is_file() or sha(path) != ref['sha256']:
        raise ValueError('Missing or modified evidence: ' + str(path))
    return path.resolve()


def read(path):
    def unique(pairs):
        value = {}
        for k, v in pairs:
            if k in value: raise ValueError('Duplicate JSON key: ' + k)
            value[k] = v
        return value
    return json.loads(Path(path).read_text(), object_pairs_hook=unique,
                      parse_constant=lambda v: (_ for _ in ()).throw(ValueError('Nonfinite JSON: ' + v)))


def check_gate_reports(refs, identity, base):
    results = {}
    for gate, required in REQUIRED_CHECKS.items():
        errors = []; bound = None
        try:
            path = checked_reference(refs.get(gate), base); report = read(path)
            bound = reference(path)
            if (report.get('schema_version') != 'ce.formal_gate_evidence.v1'
                    or report.get('gate') != gate or report.get('identity') != identity):
                raise ValueError('Gate report schema or frozen source/config/data/panel differs')
            stamp = datetime.fromisoformat(report['executed_at'].replace('Z', '+00:00'))
            if stamp.utcoffset() is None or stamp > datetime.now(timezone.utc):
                raise ValueError('Checker execution time requires a past timezone-aware timestamp')
            checked_reference(report['checker'], path.parent)
            inputs = report.get('inputs')
            if not isinstance(inputs, list) or not inputs: raise ValueError('Missing checker inputs')
            for ref in inputs: checked_reference(ref, path.parent)
            checks = report.get('checks')
            if not isinstance(checks, list) or not checks: raise ValueError('Missing executed checks')
            names = [c['id'] for c in checks]
            if len(names) != len(set(names)): raise ValueError('Duplicate gate check')
            if not set(required) <= set(names): raise ValueError('Required checks are missing')
            for check in checks:
                if check.get('status') != 'PASS':
                    errors.append(str(check['id']) + ': ' + str(check.get('status', 'NOT_RUN')))
                evidence = check.get('evidence')
                if not isinstance(evidence, list) or not evidence:
                    raise ValueError('Each check requires retained evidence, not a PASS boolean')
                for ref in evidence: checked_reference(ref, path.parent)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            errors.append(str(exc))
        results[gate] = {'status': 'FAIL' if errors else 'PASS', 'errors': errors,
                         'report': bound, 'required_checks': list(required)}
    return results


def check_panel(path):
    panel = verify_panel(path)
    rows = verified_records(path, panel)
    # The existing verifier derives public_decision from the actual trace.
    # Independent PASS never erases a known public contradiction.
    contradictions = [r['slot_id'] for r in rows if r['variant'] == 'candidate'
                      and r['status'] == 'PASS' and r['public_decision'] == 'FAIL']
    if contradictions: raise ValueError('Independent PASS contradicts an explicit public FAIL: ' + ','.join(contradictions))
    registration = read(panel['original_registration']['path'])
    effect = assess_effect(registration, rows)
    complete = len(rows) == len(panel['slots'])
    gate = b_gate(panel, rows)
    return {'closed_slots': len(rows), 'expected_slots': len(panel['slots']), 'complete': complete,
            'formal_effect_pass': complete and effect['delivery_effect_gates_passed'] and gate['open'],
            'effect': effect, 'all_terminal_gate': gate,
            'public_unknown_retained': sum(r['public_decision'] == 'UNKNOWN' for r in rows),
            'record_identity_policy': 'Existing verify_panel/verified_records and independent finalize; no model or new score dispatch'}


def assess(manifest_path):
    manifest_path = Path(manifest_path).absolute()
    result = {'schema_version': 'ce.formal_readiness.v1', 'status': 'NOT_READY', 'formal_ready': False,
              'checked_at': datetime.now(timezone.utc).isoformat(), 'checker': reference(__file__),
              'new_model_http_calls': 0, 'formal150_started': False,
              'gates': {}, 'errors': [],
              'scope': 'Evidence admission for one frozen candidate; not a guarantee of all-case repair success or formal experiment funding'}
    try:
        manifest = read(manifest_path)
        if manifest.get('schema_version') != 'ce.formal_admission_manifest.v1':
            raise ValueError('Unknown admission manifest schema')
        result['manifest'] = reference(manifest_path)
        base = manifest_path.parent
        root = Path(manifest['system_root'])
        if not root.is_absolute(): root = base / root
        root = root.resolve(strict=True)
        snapshot_path = checked_reference(manifest['snapshot'], base)
        snapshot = read(snapshot_path)
        verification = verify_snapshot(root, snapshot, require_current_bindings=True)
        result['snapshot_verification'] = verification
        if verification['status'] != 'PASS': raise ValueError('Frozen delivery source/config/tests/tools verification failed')
        panel_path = checked_reference(manifest['panel'], base)
        panel = verify_panel(panel_path)
        if (Path(panel['variants']['candidate']['root']).resolve() != root
                or panel['variants']['candidate']['source_sha256'] != snapshot['source_sha256']):
            raise ValueError('Delivery snapshot and actual candidate panel source differ')
        records_path = checked_reference(manifest['records'], base)
        if records_path != (Path(panel['output_root']) / 'records.json').resolve():
            raise ValueError('Admission must use original authoritative records, never a best-result copy')
        identity = dict(source_sha256=snapshot['source_sha256'], panel_sha256=sha(panel_path),
                        candidate_config_sha256=panel['variants']['candidate']['config_sha256'],
                        data_files_sha256=digest(panel['data_files']))
        result['identity'] = identity
        result['panel'] = check_panel(panel_path)
        # Refuse a concurrent cohort update rather than silently mixing reads.
        checked_reference(manifest['records'], base)
        result['gates'] = check_gate_reports(manifest.get('gates', {}), identity, base)
        if not result['panel']['formal_effect_pass']:
            for gate in ['G4', 'G5']:
                result['gates'][gate]['status'] = 'FAIL'
                result['gates'][gate]['errors'].append('Original same-version repetition/recovery/effect acceptance is incomplete or failed')
        result['formal_ready'] = all(v['status'] == 'PASS' for v in result['gates'].values())
        result['status'] = 'READY' if result['formal_ready'] else 'NOT_READY'
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        result['errors'].append(str(exc))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists(): parser.error('Use a new output path; admission evidence must not be overwritten')
    result = assess(args.manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream: json.dump(result, stream, indent=2, ensure_ascii=False); stream.write('\n')
    print(json.dumps({k: result[k] for k in ['status', 'formal_ready', 'new_model_http_calls']}))
    return 0 if result['formal_ready'] else 1


if __name__ == '__main__': raise SystemExit(main())
