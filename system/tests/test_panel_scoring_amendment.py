"""A parent scoring correction carries every closed slot without model retries."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from tests.test_fixed_panel import load as core
from tests.test_fixed_panel_driver import prepared, load as driver, MethodTransport


def prior_fixture(tmp_path, *, code=1):
    scripts = str(Path(__file__).resolve().parents[1]/'scripts')
    if scripts not in sys.path: sys.path.insert(0, scripts)
    panel, path, slot, transport = prepared(tmp_path)
    program = tmp_path/'legacy/score_isolated_method.py'; program.parent.mkdir()
    program.write_bytes((Path(__file__).with_name('fixtures')/'isolated_score_v1.py').read_bytes())
    panel['harness_files'][str(program)] = hashlib.sha256(program.read_bytes()).hexdigest()
    worker = program.with_name('worker.py'); worker.write_text('# frozen method worker fixture')
    workers = {'worker.py':hashlib.sha256(worker.read_bytes()).hexdigest()}
    panel['harness_files'][str(worker)] = workers['worker.py']
    panel['execution_environment']['harness_files'] = core().digest(workers)
    class BoundWorkers(MethodTransport):
        def identity(self, args):return {**super().identity(args), 'harness_files':workers}
    transport = BoundWorkers(panel, path, slot)
    tool = Path(panel['benchmark_root'])/'tools/score_case.py'
    tool.write_text(tool.read_text().replace("'status':'PASS'", "'status':'SEMANTIC_FAIL'")
                    + '\nraise SystemExit('+str(code)+')\n')
    tools = {'score_case.py':hashlib.sha256(tool.read_bytes()).hexdigest()}
    panel['execution_environment']['public_tool_files'] = core().digest(tools)
    path.write_text(json.dumps(panel))
    transport.dispatch(['python', '--execute', '--panel-slot', slot['slot_id']], tmp_path/'unused.log')
    root = Path(panel['output_root']); receipt = root/'methods'/slot['slot_id']/'.isolated-fixture/receipt.json'
    trace = receipt.parent.parent/slot['case_id']/'repair.trace.json'
    spec = importlib.util.spec_from_file_location('ce_prior_score_fixture', program)
    legacy = importlib.util.module_from_spec(spec); spec.loader.exec_module(legacy)
    output = root/'independent'/slot['slot_id']
    result = legacy.run(receipt, panel['benchmark_root'], output, phase='all')
    row = {**slot, 'independent_status':result['status'], 'status':result['status'], 'public_decision':'PASS',
           'method_status':'PUBLIC_REVIEW_COMPLETE', 'terminal_verified':True,
           'counts_in_46_slot_panel':True, 'formal_ready':False,
           'identity_sha256':core().digest(transport.identity(None)),
           'evidence_files':{name:{'path':str(p), 'sha256':core().sha(p)}
               for name,p in [('receipt',receipt), ('trace',trace), ('score',output/'result.json')]}}
    records = root/'records.json'; records.write_text(json.dumps([row]))
    seal = tmp_path/'sealed-prior-records.json'; seal.write_bytes(records.read_bytes())
    current = copy.deepcopy(panel); current['output_root'] = str(tmp_path/'runs-amended')
    current['predecessor'] = {'schema_version':'ce.fixed_panel_score_parent_amendment.v1',
        'panel':{'path':str(path), 'sha256':core().sha(path)},
        'records':{'path':str(seal), 'sha256':core().sha(seal)},
        'scoring_program':{'path':str(program), 'sha256':core().sha(program)},
        'worker_files_before':workers, 'worker_files_after':workers,
        'interpretation_policy':'ce.frozen_score_exit.v1'}
    current_path = tmp_path/'amended-panel.json'; current_path.write_text(json.dumps(current))
    return panel, path, row, current, current_path


def test_prior_negative_is_normalized_without_changing_its_raw_evidence(tmp_path):
    panel, path, old, current, amended = prior_fixture(tmp_path)
    module = core()
    module.verify_panel(amended)
    before = {str(p):p.read_bytes() for p in Path(panel['output_root']).rglob('*') if p.is_file()}
    rows = module.verified_records(amended)
    assert len(rows) == 1 and rows[0]['status'] == 'SEMANTIC_FAIL'
    assert rows[0]['recorded_status'] == 'SCORER_ERROR'
    assert rows[0]['predecessor_record_sha256'] == module.digest(old)
    assert before == {str(p):p.read_bytes() for p in Path(panel['output_root']).rglob('*') if p.is_file()}
    assert not module.b_gate(current, rows)['open']


@pytest.mark.parametrize('change', ['config', 'source', 'slot', 'runtime', 'environment', 'worker'])
def test_parent_correction_cannot_change_method_schedule_or_common_environment(tmp_path, change):
    panel, path, old, current, amended = prior_fixture(tmp_path)
    if change == 'config':
        p = tmp_path/'other-config.json';p.write_bytes(Path(current['variants']['candidate']['config']).read_bytes())
        current['variants']['candidate']['config'] = str(p)
    elif change == 'source':
        current['variants']['candidate']['source_reference'] = {'path':'other','sha256':'a'*64}
    elif change == 'slot': current['slots'].reverse()
    elif change == 'runtime': current['runtime']['python'] = '/different/python'
    elif change == 'environment': current['execution_environment']['python_environment'] = 'b'*64
    elif change == 'worker':
        current['predecessor']['worker_files_after'] = {'worker.py':'c'*64}
        current['execution_environment']['harness_files'] = core().digest({'worker.py':'c'*64})
    amended.write_text(json.dumps(current))
    with pytest.raises((ValueError, FileNotFoundError)):core().verify_panel(amended)


def test_prior_record_cannot_be_omitted_or_upgraded(tmp_path):
    panel, path, old, current, amended = prior_fixture(tmp_path)
    root = Path(current['output_root']); root.mkdir()
    records = root/'records.json'; records.write_text('[]')
    with pytest.raises(ValueError):core().verified_records(amended)
    records.unlink(); rows = core().verified_records(amended);rows[0]['status']='PASS'
    records.write_text(json.dumps(rows))
    with pytest.raises(ValueError):core().verified_records(amended)


def test_genuine_prior_infrastructure_error_cannot_be_imported_as_done(tmp_path):
    panel, path, old, current, amended = prior_fixture(tmp_path, code=2)
    with pytest.raises(ValueError):core().verify_panel(amended)


def test_original_completed_slot_is_never_dispatched_again(tmp_path):
    panel, path, old, current, amended = prior_fixture(tmp_path)
    class NoDispatch:
        def identity(self, args):pytest.fail('Completed slot must not be readmitted')
        def dispatch(self, *args):pytest.fail('Completed slot must not rerun')
    assert driver().run_slot(amended, old['slot_id'], tmp_path/'unused', transport=NoDispatch())['status']=='SEMANTIC_FAIL'
