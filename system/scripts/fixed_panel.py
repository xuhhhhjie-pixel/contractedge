"""Strict parent-side authority for the original paired46 development panel.

No model or grader is invoked here. The original registration, frozen sources,
public membership and per-slot output/attempt are checked before dispatch.
"""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

ENVIRONMENT_KEYS = ('python_environment', 'dependency_files', 'public_tool_files',
                    'public_release_files', 'harness_files', 'coordinator_code_files')
TERMINAL_OUTCOMES = frozenset({'PASS', 'SEMANTIC_FAIL', 'BUILD_FAIL', 'PATCH_APPLY_FAIL',
                             'NO_CANDIDATE', 'METHOD_FAIL', 'FAIL'})


def verify_environment(panel, identity):
    pinned = panel.get('execution_environment')
    if not isinstance(pinned, dict) or set(pinned) != set(ENVIRONMENT_KEYS):
        raise ValueError('Common execution environment must be frozen for every panel slot')
    if any(key not in identity or digest(identity[key]) != pinned[key] for key in ENVIRONMENT_KEYS):
        raise ValueError('Common execution environment changed after panel freeze')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def sha(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('Expected a regular frozen file: ' + str(path))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError('Duplicate panel JSON key')
            result[key] = value
        return result
    return json.loads(Path(path).read_text(), object_pairs_hook=unique)


def source_files(root):
    root = Path(root) / 'src'
    if not root.is_dir() or any(p.is_symlink() for p in root.rglob('*')):
        raise ValueError('Method source must be a regular exact file tree')
    return {p.relative_to(root).as_posix(): sha(p) for p in sorted(root.rglob('*'))
            if p.is_file() and p.suffix in {'.py', '.json'}}


def build_schedule(original, panel_id):
    if not isinstance(panel_id, str) or not re.fullmatch('[A-Za-z0-9_-]{1,40}', panel_id):
        raise ValueError('Invalid panel ID')
    a = [r['case_id'] for r in original['A']]; b = [r['case_id'] for r in original['B']]
    repeat = original['key_repeat_cases']; recovery = list(original['recovery_seed_paths'])
    if (len(a) != 7 or len(b) != 10 or len(set(a + b)) != 17
            or len(repeat) != 2 or len(set(repeat)) != 2 or not set(repeat) <= set(a)
            or len(recovery) != 2 or not set(recovery) <= set(a)
            or any(not isinstance(c, str) or not re.fullmatch('[A-Za-z0-9_-]+', c) for c in a + b)):
        raise ValueError('Original46 membership/counts are invalid')
    slots = []
    # Original per-pair order preserved. B admission is a separate mandatory gate.
    for group, cases, repeats in [('A', a, [0]), ('repeat', repeat, [1]),
                                  ('recovery', recovery, [0, 1]), ('B', b, [0])]:
        for case in cases:
            for attempt in repeats:
                variants = ['baseline', 'candidate']
                if int(hashlib.sha256((group + case + str(attempt)).encode()).hexdigest(), 16) % 2:
                    variants.reverse()
                for variant in variants:
                    key = [group, case, attempt, variant]
                    slots.append({'slot_id': panel_id + '-' + digest(key)[:24],
                                  'group': group, 'case_id': case, 'repeat': attempt, 'variant': variant})
    return slots


def verify_panel(path):
    panel = read(path)
    if (panel.get('schema_version') != 'ce.isolated_fixed_panel.v1'
            or panel.get('formal_ready') is not False or panel.get('model_call_cap') != 12
            or panel.get('fresh_edit_cap') != 2):
        raise ValueError('Invalid fixed panel protocol or limits')
    if panel.get('transient_workspace_policy') not in {None, 'RETIRE_CLOSED_SCORED_PUBLIC_WORKSPACE'}:
        raise ValueError('Unknown public workspace retirement policy')
    if (not isinstance(panel.get('execution_environment'), dict)
            or set(panel['execution_environment']) != set(ENVIRONMENT_KEYS)
            or any(not isinstance(v,str) or not re.fullmatch('[a-f0-9]{64}',v)
                   for v in panel['execution_environment'].values())):
        raise ValueError('Common execution environment identity is incomplete')
    original = panel['original_registration']
    if sha(original['path']) != original['sha256']:
        raise ValueError('Original panel registration changed')
    registration = read(original['path'])
    if panel['slots'] != build_schedule(registration, panel['panel_id']):
        raise ValueError('Missing/replaced/duplicate/reordered original slot')
    if set(panel['variants']) != {'baseline', 'candidate'}:
        raise ValueError('Both original variants are required')
    configurations = []
    output = Path(panel['output_root']).resolve()
    for variant_name, variant in panel['variants'].items():
        actual = source_files(variant['root'])
        if not actual or actual != variant['source_files'] or digest(actual) != variant['source_sha256']:
            raise ValueError('Frozen method source changed')
        reference = variant.get('source_reference')
        if reference is not None:
            key = 'baseline_source_files' if variant_name == 'baseline' else 'files'
            if sha(reference['path']) != reference['sha256'] or read(reference['path'])[key] != actual:
                raise ValueError('Original source reference changed')
        if sha(variant['config']) != variant['config_sha256']:
            raise ValueError('Frozen method configuration changed')
        config = read(variant['config']); configurations.append(config)
        if config.get('max_model_calls') != 12 or config.get('max_rounds') != 2:
            raise ValueError('Case budget exceeds original12/2')
        method = Path(variant['root']).resolve()
        if output.is_relative_to(method) or method.is_relative_to(output):
            raise ValueError('Panel outputs must be separate from frozen method')
    common = ('model', 'max_model_calls', 'max_rounds', 'max_context_tokens', 'max_output_tokens',
              'execution_max_output_tokens', 'review_max_output_tokens', 'max_total_tokens',
              'max_elapsed_seconds', 'seed_planning_snapshot')
    if any(configurations[0].get(k) != configurations[1].get(k) for k in common):
        raise ValueError('Variants must share declared model, resource and continuation limits')
    if configurations[0].get('seed_planning_snapshot') == 'CURRENT_CANDIDATE':
        proof = panel.get('compatibility_report')
        if not isinstance(proof, dict) or sha(proof['path']) != proof.get('sha256'):
            raise ValueError('Current-seed compatibility proof for both variants is required')
        report = read(proof['path']); protocol = panel.get('continuation_protocol') or {}
        expected = {(variant, case) for variant in ['baseline', 'candidate']
                    for case in registration['recovery_seed_paths']}
        captures = report.get('seeded_request_captures', [])
        if (report.get('status') != 'PASS_INTERFACE_COMPATIBILITY' or report.get('new_model_http_calls') != 0
                or report.get('source_sha256') != {k:v['source_sha256'] for k,v in panel['variants'].items()}
                or set(report.get('sandbox_verified_variants', [])) != {'baseline', 'candidate'}
                or len(captures) != 4 or {(r['variant'],r['case_id']) for r in captures} != expected
                or any(r.get('source_unchanged') is not True or r.get('current_snapshot_bound') is not True for r in captures)
                or protocol.get('id') != 'ce.seeded_continuation.v6'
                or protocol.get('applies_to') != ['baseline', 'candidate']
                or not report.get('proof_files')):
            raise ValueError('Current-seed compatibility report is incomplete or belongs to another source')
        for name, expected_hash in report['proof_files'].items():
            if sha(name) != expected_hash: raise ValueError('Compatibility proof file changed')
    if set(panel['seeds']) != set(registration['recovery_seed_paths']):
        raise ValueError('Original recovery seeds are missing or replaced')
    for case, seed in panel['seeds'].items():
        if sha(seed['path']) != seed['sha256']: raise ValueError('Public failure seed changed')
        original_hash = registration.get('recovery_seed_calibration', {}).get(case, {}).get('seed_sha256')
        if original_hash is not None and seed['sha256'] != original_hash:
            raise ValueError('Recovery patch differs from original public failure seed')
    if not panel['harness_files']:
        raise ValueError('Parent harness must be frozen')
    for name, expected in panel['harness_files'].items():
        if sha(name) != expected: raise ValueError('Parent harness changed')
    benchmark = Path(panel['benchmark_root']).resolve()
    if output.is_relative_to(benchmark) or benchmark.is_relative_to(output):
        raise ValueError('Panel outputs must be separate from benchmark')
    required = {'public/manifest.jsonl', 'partition/member_groups.jsonl', 'RELEASE_ENTRY.json'}
    if not required <= set(panel['data_files']): raise ValueError('Frozen public data/partition identity required')
    for name, expected in panel['data_files'].items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts or sha(benchmark / name) != expected:
            raise ValueError('Benchmark identity changed')
    members = [json.loads(line) for line in (benchmark/'partition/member_groups.jsonl').read_text().splitlines() if line]
    ids = [r['case_id'] for r in members]
    if len(ids) != len(set(ids)): raise ValueError('Duplicate partition member')
    development = {r['case_id'] for r in members if r.get('candidate_split', r.get('split')) == 'development'}
    public = {json.loads(line)['case_id'] for line in (benchmark/'public/manifest.jsonl').read_text().splitlines() if line}
    if not {s['case_id'] for s in panel['slots']} <= development & public:
        raise ValueError('Fixed panel must contain only public development members')
    if panel.get('predecessor') is not None:
        predecessor_rows(panel)
    return panel


def predecessor_rows(panel):
    """Carry the entire closed prefix across one bounded parent-only correction.

    The old panel, records and scoring bytes remain immutable. Only the known
    exit-status wrapper is interpreted; a method or grader is never rerun.
    """
    amendment = panel.get('predecessor')
    if amendment is None: return []
    if (amendment.get('schema_version') != 'ce.fixed_panel_score_parent_amendment.v1'
            or amendment.get('interpretation_policy') != 'ce.frozen_score_exit.v1'):
        raise ValueError('Unknown parent scoring amendment')
    for key in ['panel', 'records', 'scoring_program']:
        ref = amendment[key]
        if sha(ref['path']) != ref['sha256']: raise ValueError('Predecessor proof changed')
    old_path = amendment['panel']['path']; old = read(old_path)
    if old.get('predecessor') is not None:
        raise ValueError('Only the explicitly supported original scoring correction is admitted')
    old = verify_panel(old_path)
    unchanged = ['panel_id', 'slots', 'variants', 'original_registration', 'benchmark_root', 'data_files',
                 'seeds', 'runtime', 'model_call_cap', 'fresh_edit_cap', 'formal_ready',
                 'compatibility_report', 'continuation_protocol', 'transient_workspace_policy']
    if any(panel.get(k) != old.get(k) for k in unchanged):
        raise ValueError('Parent correction must retain every method, resource, case and original slot')
    before_root = Path(old['output_root']).resolve(); after_root = Path(panel['output_root']).resolve()
    if before_root.is_relative_to(after_root) or after_root.is_relative_to(before_root):
        raise ValueError('New parent records must be separate from immutable predecessor output')
    if any(panel['execution_environment'][k] != old['execution_environment'][k]
           for k in ENVIRONMENT_KEYS if k != 'harness_files'):
        raise ValueError('Parent scoring correction cannot change the common method environment')
    before = amendment['worker_files_before']; after = amendment['worker_files_after']
    if (set(before) != set(after)
            or digest(before) != old['execution_environment']['harness_files']
            or digest(after) != panel['execution_environment']['harness_files']
            or any(before[k] != after[k] and Path(k).name != 'fixed_panel.py' for k in before)):
        raise ValueError('Only the parent authority module may differ; repair workers remain unchanged')
    for files, owner in [(before, old), (after, panel)]:
        for name, expected in files.items():
            matches = [h for h in owner['harness_files'] if Path(h).name == Path(name).name]
            if len(matches) != 1 or owner['harness_files'][matches[0]] != expected:
                raise ValueError('Worker identity is not bound to the frozen parent bundle')
    program = amendment['scoring_program']
    if old['harness_files'].get(program['path']) != program['sha256']:
        raise ValueError('Recorded scorer was not part of the original frozen parent')
    original_records = before_root/'records.json'
    if sha(original_records) != amendment['records']['sha256']:
        raise ValueError('Predecessor must retain its entire original closed prefix')
    rows = read(amendment['records']['path'])
    if not isinstance(rows, list) or not rows or len({r['slot_id'] for r in rows}) != len(rows):
        raise ValueError('Malformed predecessor prefix')
    from score_isolated_method import read_recorded_score
    imported = []
    for row in rows:
        verify_record(old_path, old, row, _recorded_program=program)
        status = row['status']
        if 'score' in row['evidence_files']:
            result = read_recorded_score(row['evidence_files']['receipt']['path'], old['benchmark_root'],
                Path(row['evidence_files']['score']['path']).parent, recorded_program=program)
            status = result['interpreted_status']
        if status not in TERMINAL_OUTCOMES:
            raise ValueError('Unresolved predecessor infrastructure error cannot become a completed slot')
        imported.append({**row, 'status':status, 'independent_status':status,
            'recorded_status':row['status'], 'predecessor_panel_sha256':amendment['panel']['sha256'],
            'predecessor_record_sha256':digest(row), 'interpretation_policy':'ce.frozen_score_exit.v1'})
    return imported


def validate_slot_registration(record, identity, panel_path):
    panel = verify_panel(panel_path)
    verify_environment(panel, identity)
    if (record.get('role') != 'ISOLATED_FIXED_PANEL_SLOT' or record.get('allow_paid') is not True
            or record.get('counts_in_46_slot_panel') is not True
            or record.get('panel_registration_sha256') != sha(panel_path)
            or any(record.get(k) != v for k, v in identity.items())):
        raise ValueError('Invalid exact fixed-panel dispatch authority')
    slot = next((s for s in panel['slots'] if s['slot_id'] == record.get('panel_slot_id')), None)
    if slot is None: raise ValueError('Unknown fixed-panel slot')
    variant = panel['variants'][slot['variant']]
    expected = {'case_id': slot['case_id'], 'attempt_id': slot['slot_id'],
                'method_files': variant['source_files'], 'config_sha256': variant['config_sha256'],
                'output_path': str(Path(panel['output_root']).resolve()/'methods'/slot['slot_id']/slot['case_id'])}
    if slot['group'] == 'recovery': expected['seed_sha256'] = panel['seeds'][slot['case_id']]['sha256']
    elif 'seed_sha256' in identity: raise ValueError('Unregistered seed in original-Broken slot')
    if any(identity.get(k) != v for k, v in expected.items()):
        raise ValueError('Slot does not match original variant/case/attempt/output/seed')
    if slot['group'] != 'A':
        rows = verified_records(panel_path, panel)
        required_groups = {'A'} if slot['group'] in {'repeat', 'recovery'} else {'A', 'repeat', 'recovery'}
        prerequisite_panel = {**panel, 'slots': [s for s in panel['slots'] if s['group'] in required_groups]}
        subset = [r for r in rows if r['group'] in required_groups]
        if not b_gate(prerequisite_panel, subset)['open']:
            raise ValueError('Prior registered groups have not passed; dispatch remains closed')
    return slot


def verify_record(panel_path, panel, record, *, _recorded_program=None):
    """Recheck terminal proof, never trust a cached PASS boolean for admission."""
    if record.get('predecessor_panel_sha256') is not None:
        expected = next((r for r in predecessor_rows(panel) if r['slot_id'] == record.get('slot_id')), None)
        if expected is None or expected != record:
            raise ValueError('Inherited slot differs from its immutable original proof')
        return record
    slot = next((s for s in panel['slots'] if s['slot_id'] == record.get('slot_id')), None)
    if slot is None or any(record.get(k) != v for k, v in slot.items()):
        raise ValueError('Terminal record does not belong to the exact slot')
    root = Path(panel['output_root']).resolve()
    case = root/'methods'/slot['slot_id']/slot['case_id']
    refs = record['evidence_files']
    paths = {}
    for label, item in refs.items():
        path = Path(item['path']).resolve(strict=True)
        if not path.is_relative_to(root) or sha(path) != item['sha256']:
            raise ValueError('Terminal proof is outside panel or changed')
        paths[label] = path
    receipt = read(paths['receipt']); trace = read(paths['trace'])
    if (paths['trace'] != case/'repair.trace.json'
            or paths['receipt'].name != 'receipt.json' or paths['receipt'].parent.parent != case.parent
            or not paths['receipt'].parent.name.startswith('.isolated-')
            or receipt.get('status') != 'METHOD_COMPLETE_PENDING_INDEPENDENT_SCORING'
            or receipt.get('method_returncode') != 0
            or receipt.get('cleanup', {}).get('status') != 'OWNED_CONTAINERS_CLOSED'
            or receipt.get('terminal_validation', {}).get('classification') != 'METHOD_RESULT'
            or receipt.get('counts_in_46_slot_panel') is not True
            or receipt.get('panel_registration_sha256') != sha(panel_path)
            or Path(receipt.get('state_root', '')).resolve() != paths['receipt'].parent
            or receipt.get('panel_slot') != slot or trace.get('case_id') != slot['case_id']):
        raise ValueError('Terminal method proof is incomplete or belongs to qualification')
    variant = panel['variants'][slot['variant']]
    identity = receipt['identity']
    verify_environment(panel, identity)
    if (identity.get('case_id') != slot['case_id'] or identity.get('attempt_id') != slot['slot_id']
            or identity.get('method_files') != variant['source_files']
            or identity.get('config_sha256') != variant['config_sha256']
            or identity.get('output_path') != str(case)
            or identity.get('panel_slot_id') != slot['slot_id']
            or identity.get('panel_registration_sha256') != sha(panel_path)
            or (slot['group'] == 'recovery' and identity.get('seed_sha256') != panel['seeds'][slot['case_id']]['sha256'])
            or (slot['group'] != 'recovery' and 'seed_sha256' in identity)
            or record.get('identity_sha256') != digest(identity)):
        raise ValueError('Terminal method identity differs from registered source/config/output')
    if receipt['terminal_validation'].get('candidate_verified') is not bool(trace.get('candidate')):
        raise ValueError('Candidate export verification disagrees with terminal trace')
    if trace.get('candidate'):
        from score_isolated_method import run, read_recorded_score
        score_root = root/'independent'/slot['slot_id']
        if paths.get('score') != score_root/'result.json':
            raise ValueError('Missing independent result for final candidate')
        result = (run(paths['receipt'], panel['benchmark_root'], score_root, phase='finalize')
                  if _recorded_program is None else read_recorded_score(paths['receipt'], panel['benchmark_root'],
                      score_root, recorded_program=_recorded_program)['raw'])
        independent = result['status']
    else:
        if 'score' in paths: raise ValueError('No candidate must not have a fabricated score')
        independent = 'NO_CANDIDATE'
    public = (trace.get('last_feedback') or {}).get('semantic_outcome')
    decision = {'PUBLIC_REVIEW_PASS': 'PASS', 'PUBLIC_REVIEW_FAIL': 'FAIL'}.get(public, 'UNKNOWN')
    if (record.get('independent_status') != independent or record.get('status') != independent
            or record.get('public_decision') != decision or record.get('terminal_verified') is not True):
        raise ValueError('Cached terminal status differs from actual public/independent evidence')
    return record


def verified_records(panel_path, panel=None):
    panel = panel or verify_panel(panel_path)
    path = Path(panel['output_root'])/'records.json'
    inherited = predecessor_rows(panel)
    rows = read(path) if path.exists() else inherited
    if not isinstance(rows, list) or len({r['slot_id'] for r in rows}) != len(rows):
        raise ValueError('Duplicate/malformed terminal records')
    if not {r['slot_id'] for r in inherited} <= {r['slot_id'] for r in rows}:
        raise ValueError('A predecessor result was dropped')
    return [verify_record(panel_path, panel, row) for row in rows]


def b_gate(panel, records):
    """Policy over already verified records; dispatch uses verified_records first."""
    expected = {s['slot_id']: s for s in panel['slots']}
    table = {}; errors = []
    for row in records:
        key = row.get('slot_id')
        if key not in expected or key in table:
            errors.append('duplicate/unregistered record'); continue
        if any(row.get(k) != v for k, v in expected[key].items()):
            errors.append('record identity differs'); continue
        table[key] = row
    missing = []
    for slot in panel['slots']:
        if slot['group'] == 'B': continue
        row = table.get(slot['slot_id'])
        if (row is None or row.get('terminal_verified') is not True
                or row.get('status') not in TERMINAL_OUTCOMES):
            missing.append(slot['slot_id']); continue
        if slot['variant'] == 'candidate' and (row['status'] != 'PASS' or row.get('public_decision') == 'FAIL'):
            errors.append('candidate prerequisite failed: ' + slot['slot_id'])
    return {'open': not missing and not errors, 'missing': missing, 'errors': errors}
