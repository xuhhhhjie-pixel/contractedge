"""Finish the zero-model tail of a run whose supervisor stopped at request settlement.

Cause: two concurrent slots of one method in one case shared CE_API_USER_ID, so the shared
coordination ledger tripped request_settlement's assertion after the model run had ended.
The model run is complete (SUPERVISOR_RESULT, wire ledger); this does exactly the remaining
supervisor steps - billing, outcome, evidence freeze - with no model request and no resampling.
Usage: python complete_slot.py CASE_ID TAG METHOD
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

E = Path(__file__).resolve().parent


def main(cid, tag, method):
    root = E / 'cases' / cid
    out = root / 'active-case' / f'attempts-{tag}' / method
    assert (out / 'SUPERVISOR_RESULT.json').exists() and not (out / 'EXECUTION_OUTCOME.json').exists()
    assert not (out / 'CANDIDATE_FROZEN.json').exists()
    code = E / 'controller'
    sys.path.insert(0, str(code))
    from billing import build as build_billing
    from run_outcome import build as build_outcome
    if (out / 'wire/usage.jsonl').exists():
        build_billing(out / 'wire/usage.jsonl', code / 'pricing.json', out)
    build_outcome(out, method)
    env = {**os.environ, 'CE_RUN_ROOT': str(root), 'CE_RUN_VERSION': tag, 'PATH': str(E / 'bin') + ':' + os.environ['PATH'],
           'PYTHONPATH': str(code) + ':' + str(E)}
    subprocess.run(['python3', str(code / 'freeze_method.py'), method], env=env, check=True)
    (out / 'COMPLETED_AFTER_SETTLEMENT_FAILURE.json').write_text(json.dumps({
        'reason': 'request_settlement assertion: concurrent slots shared CE_API_USER_ID', 'new_model_requests': 0,
        'steps_done': ['billing', 'outcome', 'freeze_method'], 'settlement_skipped': True}, indent=2))
    issue = E / 'issues' / f'generate-{cid}-{tag}-{method}.json'
    if issue.exists():
        issue.rename(issue.with_suffix('.resolved'))
    print(json.load(open(out / 'EXECUTION_OUTCOME.json'))['execution_status'])


if __name__ == '__main__':
    main(*sys.argv[1:4])
