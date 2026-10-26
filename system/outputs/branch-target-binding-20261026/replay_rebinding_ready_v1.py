"""Offline contract fixture: simulate a corrected binding, never a repair result."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from contractedge.changeset.topomen import translate_for_topomen
from contractedge.contracts.validation import parse_method_input
from contractedge.execution.topomen import build_execution_request
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.repair import validate_ready_trace
from contractedge.orchestration.runner import run_front_half
from contractedge.source.snapshot import compute_snapshot
from contractedge.tokenizers import utf8_byte_token_bound

BASE = Path('/home/xhj/contractedge-diagnostic-source050-v2-20261026/methods/R3-KPT-4673/R3-KPT-4673')
OUT = Path(__file__).parent / 'corrected-binding-fixture'
read = lambda p: json.loads(Path(p).read_text())
assert not OUT.exists()
calls = read(BASE / 'model_calls.json')
stage = read(BASE / 'journal/stages/diagnosis/round-0/revision-000000.json')
inputs = {}
for ref in stage['input_refs']:
    if ref['kind'] in {'METHOD_INPUT', 'FRONT_CONFIG', 'PUBLIC_OBSERVATIONS', 'EXECUTION_FEEDBACK'}:
        path = (BASE / ref['relative_path']).resolve()
        assert path.is_relative_to(BASE)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == ref['sha256']
        inputs[ref['kind']] = read(path)
workspace = BASE / 'workspace/source'
source = workspace / inputs['METHOD_INPUT']['source_root']
before = compute_snapshot(source).snapshot_id
assert before == inputs['METHOD_INPUT']['initial_snapshot_id']
requests = []


class Fixture:
    def complete_json(self, request):
        index = len(requests)
        requests.append(asdict(request))
        if index < 4:
            saved = calls[index]
            assert request.prompt_version == saved['prompt_version']
            return ModelResponse(saved['response_content'], {
                'provider': 'fixture', 'finish_reason': saved['response_metadata'].get('finish_reason'),
                'usage': saved['response_metadata'].get('usage', {})})
        if index == 4:
            assert str(request.prompt_version).startswith('diagnosis.')
            body = json.loads(request.messages[1]['content'])
            assert body['diagnosis_continuity']['extension_gaps'][0]['locator_hint']['symbol'] == 'runValidators'
            assert any(e['id'] == 'SRC-CU-60d3496476d58093' for e in body['evidence_catalog'])
            value = json.loads(calls[1]['response_content'])
            # This is an explicit simulated model correction, not an engine
            # mutation, an autonomous diagnosis or behavioral success evidence.
            unit = deepcopy(next(u for u in value['units'] if u['id'] == 'U5'))
            unit.update(id='U-VALIDATOR', role='Owning validator execution branch',
                        locator_refs=['SRC-CU-60d3496476d58093'])
            value['units'].append(unit)
        elif index == 5:
            assert str(request.prompt_version).startswith('changeset.')
            value = json.loads(calls[4]['response_content'])
            next(c for c in value['changes'] if c['id'] == 'C2')['target']['unit_refs'] = ['U-VALIDATOR']
        else:
            raise AssertionError('Unexpected extra model request')
        return ModelResponse(json.dumps(value), {'provider': 'fixture', 'finish_reason': 'fixture'})


trace = run_front_half(inputs['METHOD_INPUT'], workspace, Fixture(), inputs['FRONT_CONFIG'],
    utf8_byte_token_bound, execution_feedback=inputs['EXECUTION_FEEDBACK'],
    public_observations=tuple(inputs['PUBLIC_OBSERVATIONS'])).to_wire()
OUT.mkdir()
(OUT / 'front.trace.json').write_text(json.dumps(trace, indent=2) + '\n')
assert trace['status'] == 'READY_PARTIAL', (trace['status'], trace['errors'])
assert len(requests) == 6
assert trace['retrieval_rounds'][-1]['trigger'] == 'CHANGESET_TARGET_BINDING_GAP'
packet, context = validate_ready_trace(trace, parse_method_input(inputs['METHOD_INPUT']), workspace)
support = context.retrieval_config['necessary_source_support']
plan = translate_for_topomen(trace['changeset'], 'contractedge.topomen_plan.v1',
    diagnosis=trace['diagnosis'], source_support=support)
request = build_execution_request(plan, source, {'allowed_paths': ['pkg/lib/kptops/render_executor.go']},
    utf8_byte_token_bound, {**inputs['FRONT_CONFIG'], 'model': 'fixture'},
    requirement=inputs['METHOD_INPUT']['requirement_text'])
body = json.loads(request.messages[1]['content'])
assert len(body['current_target_files']) == 1
assert 'runValidators' in body['current_target_files'][0]['content']
assert trace['changeset']['open_obligations']
assert compute_snapshot(source).snapshot_id == before
(OUT / 'execution.request.json').write_text(json.dumps(asdict(request), indent=2) + '\n')
(OUT / 'summary.json').write_text(json.dumps(dict(
    status='CORRECTED_BINDING_CONTRACT_FIXTURE_READY_AND_EXECUTION_REQUEST_BUILT',
    source_snapshot=before, fixture_responses=6, real_model_calls=0, grader_calls=0,
    source_unchanged=True, automatic_semantic_units_created_by_engine=0,
    required_source_support=len(support), open_obligations_retained=True,
    formal_ready=False, scope='Explicit simulated correction validates contracts and continuity only; no autonomous repair result'), indent=2) + '\n')
print((OUT / 'summary.json').read_text())
