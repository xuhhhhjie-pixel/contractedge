"""Once-only public-text correction pair; no candidate execution or independent score.

Prepare pins the same rejected draft and canonical public input for both arms.
Execute requires exact engineering qualification; responses are parsed, never run.
This component diagnostic is not an isolated repair attempt or fixed46 result.
"""
import argparse
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys

SYSTEM = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent
ROOT = Path('/home/xhj/contractedge-graph-correction-pair-v057-20261026')
SAVED = Path('/home/xhj/contractedge-diagnostic-source056-v2-20261026/methods/R3-KPT-4673/R3-KPT-4673')
FREEZE = SYSTEM / 'docs/development/grounded-graph-correction-v057-20261026-v1.snapshot.json'
CONFIG = SYSTEM / 'configs/fixed46-candidate-v057-budget150.json'
sys.path[:0] = [str(SYSTEM / 'src'), str(SYSTEM / 'scripts')]
from contractedge.contracts.models import ContractError, RunContext
from contractedge.contracts.validation import parse_method_input
from contractedge.diagnosis.evidence import build_evidence_catalog
from contractedge.diagnosis.parse import parse_diagnosis_response
from contractedge.diagnosis.validate import validate_diagnosis
from contractedge.orchestration.runner import _append_diagnosis_retry_turn, _bind_diagnosis_envelope
from contractedge.retrieval.candidates import CandidateSet
from verify_development_snapshot import verify_snapshot


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())


def public_context():
    calls = read(SAVED / 'model_calls.json')
    request = json.loads(calls[0]['messages'][1]['content'])
    front = read(SAVED / 'rounds/0/front.trace.json')
    catalog = {record['id']: record for record in request['evidence_catalog']}
    assert len(catalog) == len(request['evidence_catalog'])
    method = replace(parse_method_input(read(SAVED / 'method_input.json')),
                     public_evidence=tuple(catalog[ref['id']] for ref in request['method_input']['public_evidence']))
    context = RunContext(**front['run_context'])
    candidates = CandidateSet(**front['candidate_set'])
    assert build_evidence_catalog(method, candidates, context.snapshot_id) == request['evidence_catalog']
    assert context.input_digest == method.input_digest == request['run_context']['input_digest']
    assert context.snapshot_id == request['run_context']['snapshot_id']
    return calls, method, context, front['requirement_spec'], candidates


def assess(content, method, context, requirement, candidates):
    try:
        body, _ = _bind_diagnosis_envelope(parse_diagnosis_response(content), method, context, requirement, candidates)
        packet = validate_diagnosis(body, method, context, candidates)
        return dict(status='CONTRACT_PASS', diagnosis_status=packet.raw['status'],
                    supported_violations=sum(v['causal_status'] == 'SUPPORTED' for v in packet.raw['violations']),
                    unverified_outcomes=packet.raw.get('unverified_outcomes', []))
    except (ValueError, ContractError) as error:
        return dict(status='CONTRACT_FAIL', error=str(error))


def prepare():
    calls, *context = public_context()
    first, second = (assess(call['response_content'], *context) for call in calls)
    assert first['status'] == second['status'] == 'CONTRACT_FAIL'
    assert 'PRODUCTION_PATH_INCOMPLETE' in first['error']
    assert 'PRODUCTION_CONSUMER_UNEVIDENCED' in second['error']
    control = deepcopy(calls[1]['request'])
    treatment = deepcopy(control)
    treatment['messages'] = list(_append_diagnosis_retry_turn(calls[0]['messages'],
        calls[0]['response_content'], calls[1]['messages'][-1]['content']))
    assert control['messages'][:-1] == treatment['messages'][:-1]
    assert {k: v for k, v in control.items() if k != 'messages'} == {k: v for k, v in treatment.items() if k != 'messages'}
    assert treatment['messages'][-1]['content'].startswith(control['messages'][-1]['content'])
    assert 0 < len(treatment['messages'][-1]['content'][len(control['messages'][-1]['content']):].encode()) <= 4096
    ROOT.mkdir(exist_ok=False)
    write_new(ROOT / 'control.request.json', control)
    write_new(ROOT / 'treatment.request.json', treatment)
    files = [SAVED / 'model_calls.json', SAVED / 'method_input.json', SAVED / 'rounds/0/front.trace.json',
             ROOT / 'control.request.json', ROOT / 'treatment.request.json', CONFIG, Path(__file__)]
    write_new(ROOT / 'registration.json', dict(schema_version='ce.public_text_correction_pair.v1',
        role='COMPONENT_DIAGNOSTIC_ONLY', order=['control', 'treatment'], once_each=True, max_calls=2,
        hypothesis='Grounded inventory helps correct the same invalid configuration graph without inventing source consumers.',
        success='Treatment passes unchanged diagnosis contracts with supported work; inspect both outputs. A single pair cannot establish repair efficacy or superiority.',
        frozen_inputs={str(p): sha(p) for p in files}, original_errors=[first, second],
        formal_ready=False, counts_in_46_slot_panel=False, executable_outputs_allowed=False,
        constraint='No candidate/hidden grader accesses; same public text and provider settings, no retry. Persistent150CNY ledger.'))
    print(json.dumps(dict(status='PREPARED_NO_CALLS', root=str(ROOT))))


def execute():
    registration = read(ROOT / 'registration.json')
    assert registration['role'] == 'COMPONENT_DIAGNOSTIC_ONLY' and registration['max_calls'] == 2
    for name, digest in registration['frozen_inputs'].items():
        assert sha(name) == digest, name
    frozen = read(FREEZE)
    assert verify_snapshot(SYSTEM, frozen, require_current_bindings=True)['status'] == 'PASS'
    full = read(OUT / 'FULL_REGRESSION_COMPLETION_v057_v1.json')
    qualification = read(OUT / 'qualification-v057-v1-state.json')
    assert full['status'] == 'PASS' and full['exit_code'] == 0
    assert full['source_sha256'] == frozen['source_sha256'] and full['freeze_sha256'] == sha(FREEZE)
    assert full['log_sha256'] == sha(OUT / 'full-v057-v1.log')
    assert qualification['status'] == 'ZERO_CALL_QUALIFICATION_PASS_REQUIRES_NEW_DIAGNOSTIC_REGISTRATION'
    assert qualification['full_process_receipt_sha256'] == sha(OUT / 'FULL_REGRESSION_COMPLETION_v057_v1.json')
    calls, *context = public_context()
    write_new(ROOT / 'started.json', dict(registration_sha256=sha(ROOT / 'registration.json'),
        freeze_sha256=sha(FREEZE), qualification_sha256=sha(OUT / 'qualification-v057-v1-state.json')))
    # Credentials stay only in this trusted text-only parent process.
    from contractedge.cli import _build_provider
    from run_isolated_method import ModelBroker
    config = read(CONFIG)
    os.environ[config['api_key_env']] = Path('/home/xhj/.config/contractedge/chatanywhere-experiment-20261019.key').read_text().strip()
    provider, counter = _build_provider(config)
    before = provider.money_budget.summary()
    broker = ModelBroker(provider, counter, {**config, 'max_model_calls': 2}, ROOT / 'model_calls.json')
    rows = []
    for arm in registration['order']:
        write_new(ROOT / (arm + '.claimed.json'), dict(registration_sha256=sha(ROOT / 'registration.json')))
        request = read(ROOT / (arm + '.request.json'))
        try:
            response = broker.complete(dict(request=request, timeout_seconds=config['timeout_seconds']))
            write_new(ROOT / (arm + '.response.json'), response)
            row = (dict(status='PROVIDER_POLICY_REJECTED') if response.get('received_error') else
                   assess(response['content'], *context))
        except Exception as error:
            row = dict(status='TRANSPORT_OR_ADMISSION_ERROR_NO_RETRY', error_type=type(error).__name__)
        rows.append(dict(arm=arm, **row))
        write_new(ROOT / (arm + '.result.json'), rows[-1])
    assert verify_snapshot(SYSTEM, frozen, require_current_bindings=True)['status'] == 'PASS'
    result = dict(status='COMPONENT_PAIR_CLOSED', rows=rows, calls=len(broker.budget.calls),
        before_budget=before, after_budget=provider.money_budget.summary(),
        formal_ready=False, counts_in_46_slot_panel=False, patch_generated=False,
        limitation='Two corrections of one saved public diagnosis, not original end-to-end repair or generalized effect evidence.')
    write_new(ROOT / 'result.json', result)
    print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'execute'])
    args = parser.parse_args()
    prepare() if args.action == 'prepare' else execute()
