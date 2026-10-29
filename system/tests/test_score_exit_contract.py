"""Frozen grader exit1 is a negative result, not an infrastructure failure."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from tests.test_isolated_scoring import fixture, load


@pytest.mark.parametrize('status', ['SEMANTIC_FAIL', 'BUILD_FAIL', 'PATCH_APPLY_FAIL', 'FAIL'])
def test_native_negative_with_contract_exit_one_is_retained(tmp_path, status):
    receipt, benchmark, output, _ = fixture(tmp_path, exit_code=1)
    tool = benchmark/'tools/score_case.py'
    tool.write_text(tool.read_text().replace("'status':'PASS'", "'status':"+repr(status)))
    result = load().run(receipt, benchmark, output, phase='all')
    assert result['status'] == status and result['native_score_status'] == status


@pytest.mark.parametrize('status,code', [('PASS', 1), ('SEMANTIC_FAIL', 2), ('SEMANTIC_FAIL', 0)])
def test_inconsistent_exit_and_native_status_remain_infrastructure_errors(tmp_path, status, code):
    receipt, benchmark, output, _ = fixture(tmp_path, exit_code=code)
    tool = benchmark/'tools/score_case.py'
    tool.write_text(tool.read_text().replace("'status':'PASS'", "'status':"+repr(status)))
    assert load().run(receipt, benchmark, output, phase='all')['status'] == 'SCORER_ERROR'


def test_recorded_v1_error_is_interpreted_without_overwriting_or_rerunning(tmp_path, monkeypatch):
    receipt, benchmark, output, _ = fixture(tmp_path, exit_code=1)
    tool = benchmark/'tools/score_case.py'
    tool.write_text(tool.read_text().replace("'status':'PASS'", "'status':'SEMANTIC_FAIL'"))
    program = Path(__file__).with_name('fixtures')/'isolated_score_v1.py'
    spec = importlib.util.spec_from_file_location('ce_legacy_score_fixture', program)
    legacy = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
    spec.loader.exec_module(legacy)
    old = legacy.run(receipt, benchmark, output, phase='all')
    assert old['status'] == 'SCORER_ERROR' and old['native_score_status'] == 'SEMANTIC_FAIL'
    before = {str(p):p.read_bytes() for p in output.rglob('*') if p.is_file()}
    scoring = load()
    assert hasattr(scoring, 'read_recorded_score'), 'Read-only legacy interpretation is not implemented'
    monkeypatch.setattr(scoring.subprocess, 'run', lambda *a, **k: pytest.fail('Grader must not rerun'))
    result = scoring.read_recorded_score(receipt, benchmark, output,
        recorded_program={'path':str(program), 'sha256':scoring.sha(program)})
    assert result['raw'] == old and result['interpreted_status'] == 'SEMANTIC_FAIL'
    assert before == {str(p):p.read_bytes() for p in output.rglob('*') if p.is_file()}
    (output/'native/score.json').write_text(json.dumps({'status':'PASS'}))
    with pytest.raises(ValueError):
        scoring.read_recorded_score(receipt, benchmark, output,
            recorded_program={'path':str(program), 'sha256':scoring.sha(program)})
