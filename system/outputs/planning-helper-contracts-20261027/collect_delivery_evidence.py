"""Normalize completed delivery evidence; never manufacture missing gate PASS."""
from pathlib import Path
from datetime import datetime, timezone
import importlib.util
import json
import re
import shutil
import subprocess
import tarfile
import io
import hashlib
import sys

SYSTEM = Path('/home/xhj/contractedge-planning-branch-continuity-20261026/system')
OUT = SYSTEM / 'outputs/planning-helper-contracts-20261027'
WORK = SYSTEM / '.superpowers/sdd/2026-10-27-planning-helper-contracts'
CLONE = Path('/home/xhj/contractedge-cleancheck-v060-ordering-20261027')
PANEL = Path('/home/xhj/contractedge-fixed46-source060-20261027/panel.json')
sys.path.insert(0, str(SYSTEM / 'scripts'))
from assess_formal_readiness import reference, sha, digest, read
from fixed_panel import verify_panel, verified_records, source_files

panel = verify_panel(PANEL); rows = verified_records(PANEL, panel)
source = panel['variants']['candidate']['source_sha256']
assert source_files(CLONE / 'system') == panel['variants']['candidate']['source_files']
identity = dict(source_sha256=source, panel_sha256=sha(PANEL),
    candidate_config_sha256=panel['variants']['candidate']['config_sha256'], data_files_sha256=digest(panel['data_files']))
stamp = datetime.now(timezone.utc).isoformat()

# Preserve both the original failed command and the narrowly corrected rerun.
for source_path, name in [
    (WORK / 'formal-admission-final.log', 'formal-admission-26-tests.log'),
    (WORK / 'formal-admission-related-v2.log', 'formal-admission-related-64-tests.log'),
    (WORK / 'formal-admission-negative-terminal.log', 'formal-admission-valid-negative-test.log'),
    (WORK / 'clean_clone_roundtrip.py', 'clean-clone-operational-driver.py'),
    (WORK / 'clean-clone-real-roundtrip-v2.log', 'clean-clone-operational.log'),
    (CLONE / 'operational-real-rw130/operational-readiness.json', 'CLEAN_CLONE_REAL_ROUNDTRIP.json'),
    (CLONE / 'operational-recovery-extra.log', 'clean-clone-extra-initial-env-failure.log'),
    (CLONE / 'operational-consumer-recovery-correct-env.log', 'clean-clone-consumer-recovery-correct-env.log')]:
    dest = OUT / name
    if dest.exists(): assert dest.read_bytes() == source_path.read_bytes(), 'Retained evidence cannot be overwritten'
    else: shutil.copy2(source_path, dest)

qualification_path = OUT / 'ORDERING_SOURCE_QUALIFICATION.json'; q = read(qualification_path)
assert q['source_sha256'] == source and q['full_exit_code'] == q['actual8_exit_code'] == 0
assert q['full_tests'] == 2177 and q['subtests'] == 91
assert sha(OUT / 'full-ordering-source-final.log') == q['full_log_sha256']
alignment_path = OUT / 'ORDERING_CERT_EVIDENCE_ALIGNMENT.json'; a = read(alignment_path)
assert a['source_sha256'] == source and len(a['actual_request_delivery']) == 3
assert all(all(r['critical_public_source'].values()) for r in a['actual_request_delivery'])
ci_path = OUT / 'CI_REMOTE_SUCCESS_e50be76.json'; ci = read(ci_path)
assert ci['status'] == 'completed' and ci['conclusion'] == 'success'
assert all(j['conclusion'] == 'success' for j in ci['jobs'])
# Check the real remote CI commit's source, not merely the version label.
archive = subprocess.check_output(['git', '-C', str(SYSTEM.parent), 'archive', ci['headSha'], 'system/src'])
with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
    files = {m.name.removeprefix('system/src/'): hashlib.sha256(tar.extractfile(m).read()).hexdigest()
             for m in tar.getmembers() if m.isfile() and Path(m.name).suffix in {'.py', '.json'}}
assert files == panel['variants']['candidate']['source_files']

operational_path = OUT / 'CLEAN_CLONE_REAL_ROUNDTRIP.json'; op = read(operational_path)
assert op['status'] == op['independent_status'] == 'PASS' and op['public_status'] == 'EXECUTION_PASS'
assert op['source_sha256'] == source and op['new_model_http_calls'] == 0
assert op['source_swap_real_process_exit'] == 73 and op['source_recovery']['status'] == 'ROLLED_BACK'
assert op['source_recovery']['restored_snapshot'] == op['sealed_input']['original_snapshot']
assert not op['source_recovery']['candidate_exported'] and not op['source_recovery']['budget_reset']
assert op['completed_score_finalize_twice'] and op['score_dispatches'] == 1
assert op['ledger_before'] == op['ledger_after']
initial_log = (OUT / 'clean-clone-extra-initial-env-failure.log').read_text()
corrected_log = (OUT / 'clean-clone-consumer-recovery-correct-env.log').read_text()
assert '15 failed, 38 passed' in initial_log
assert "ModuleNotFoundError: No module named 'scripts'" in initial_log
assert re.search(r'15 passed in ', corrected_log) and 'FAILED ' not in corrected_log
assert '26 passed in ' in (OUT / 'formal-admission-26-tests.log').read_text()
assert '64 passed in ' in (OUT / 'formal-admission-related-64-tests.log').read_text()
assert '1 passed' in (OUT / 'formal-admission-valid-negative-test.log').read_text()


def gate(name, specs, inputs, scope):
    value = dict(schema_version='ce.formal_gate_evidence.v1', gate=name, identity=identity,
        executed_at=stamp, checker=reference(__file__), inputs=[reference(p) for p in inputs],
        checks=[dict(id=key, status='PASS', evidence=[reference(p) for p in proof]) for key, proof in specs], scope=scope)
    path = OUT / (name + '_DELIVERY_EVIDENCE_v1.json')
    with path.open('x') as stream: json.dump(value, stream, indent=2); stream.write('\n')
    return reference(path)


g2 = gate('G2', [
    ('exact_source_full_suite', [qualification_path, OUT / 'full-ordering-source-final.log']),
    ('current_cross_stage_delivery', [alignment_path, OUT / 'ORDER_SOURCE_REPLAY.json', OUT / 'ORDERING_KPT_NONREGRESSION_REPLAY.json']),
    ('supported_environment_ci', [ci_path])], [qualification_path, alignment_path, ci_path],
    'Existing frozen source qualification and finite cross-stage delivery/CI; not universal semantic correctness. Additive parent admission checker has separately retained tests and does not change method/harness identity.')
g6 = gate('G6', [
    ('clean_clone_real_roundtrip', [operational_path, OUT / 'CLEAN_GITHUB_CLONE_VERIFICATION.json']),
    ('durable_crash_recovery', [operational_path, OUT / 'clean-clone-extra-initial-env-failure.log', OUT / 'clean-clone-consumer-recovery-correct-env.log']),
    ('resource_and_drift_checks', [qualification_path, OUT / 'clean-clone-extra-initial-env-failure.log']),
    ('declared_serial_execution', [SYSTEM / 'scripts/run_fixed_panel.py', PANEL])], [operational_path, qualification_path],
    'Same-server clean GitHub checkout/fresh venv; real saved-candidate public execution/export/score and source/score recovery. 38 unaffected recovery/resource tests passed in initial command;15 dependent consumer tests passed after README env setup. Serial support only. No fresh model repair or other-host certification.')
manifest = dict(schema_version='ce.formal_admission_manifest.v1', system_root=str(SYSTEM),
    snapshot=reference(SYSTEM / 'docs/development/ordering-source-v060-20261027-delivery-v2.snapshot.json'),
    panel=reference(PANEL), records=reference(Path(panel['output_root']) / 'records.json'), gates={'G2': g2, 'G6': g6},
    scope='Checkpoint: missing G0/G1/G3 final evidence and G4/G5 original effect acceptance remain explicitly closed. No formal dispatch.')
with (OUT / 'FINAL_ADMISSION_MANIFEST_v1.json').open('x') as stream: json.dump(manifest, stream, indent=2); stream.write('\n')
print(json.dumps({'validated_panel_records': len(rows), 'completed_evidence_gates': ['G2', 'G6'], 'formal_ready': False}))
