"""Capture next plan from exact public saved inputs/responses, without network."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.runner import run_front_half
from contractedge.source.snapshot import compute_snapshot
from contractedge.tokenizers import utf8_byte_token_bound

SAVED = Path('/home/xhj/contractedge-e2e-source057-20261027/methods/R3-KPT-4673/R3-KPT-4673')
WORKSPACE = Path('/home/xhj/contractedge-generation-replay-v058-20261027/source')
OUT = Path(__file__).parent


def main():
    calls = json.loads((SAVED / 'model_calls.json').read_text())
    stage = json.loads((SAVED / 'journal/stages/diagnosis/round-0/revision-000000.json').read_text())
    inputs = {}
    for ref in stage['input_refs']:
        if ref['kind'] in {'METHOD_INPUT', 'FRONT_CONFIG', 'EXECUTION_FEEDBACK', 'PUBLIC_OBSERVATIONS'}:
            path = (SAVED / ref['relative_path']).resolve()
            assert path.is_relative_to(SAVED)
            raw = path.read_bytes(); assert hashlib.sha256(raw).hexdigest() == ref['sha256']
            inputs[ref['kind']] = json.loads(raw)
    source = WORKSPACE / inputs['METHOD_INPUT']['source_root']
    snapshot = compute_snapshot(source).snapshot_id
    assert snapshot == inputs['METHOD_INPUT']['initial_snapshot_id']
    # Saved journal JSON sorts dictionary keys; restore only the original public
    # check declaration iteration order from the actual serialized plan schema.
    check_order = calls[2]['request']['output_schema']['properties']['postconditions']['items']['properties']['behavior_check_refs']['items']['enum']
    for probe in inputs['FRONT_CONFIG']['public_execution_capabilities']['method_behavior_probes']:
        checks = probe['checks']; names = [name.split(':', 1)[1] for name in check_order if name.startswith(probe['id'] + ':')]
        assert set(names) == set(checks)
        probe['checks'] = {name: checks[name] for name in names}
    captured = {}
    for scope in ['prompt_version', 'front_half']:
        class Provider:
            count = 0
            def complete_json(self, request):
                index = self.count; self.count += 1
                if index == 2:
                    captured[scope] = asdict(request)
                    raise RuntimeError('OFFLINE_CAPTURE_ONLY_NO_DISPATCH')
                assert index < 2 and request.prompt_version == calls[index]['prompt_version']
                return ModelResponse(calls[index]['response_content'], calls[index]['response_metadata'])
        trace = run_front_half(inputs['METHOD_INPUT'], WORKSPACE, Provider(),
            {**inputs['FRONT_CONFIG'], 'planning_generation_recovery_scope': scope}, utf8_byte_token_bound,
            execution_feedback=inputs['EXECUTION_FEEDBACK'],
            public_observations=tuple(inputs['PUBLIC_OBSERVATIONS']))
        assert scope in captured, (trace.status, trace.errors)
        assert compute_snapshot(source).snapshot_id == snapshot
    old, new = captured['prompt_version'], captured['front_half']
    (OUT / 'captured-requests.json').write_text(json.dumps(captured, ensure_ascii=False, indent=2)+'\n')
    assert list(old['messages']) == list(new['messages']) == calls[2]['request']['messages']
    assert old['output_schema'] == new['output_schema'] == calls[2]['request']['output_schema']
    assert old['thinking'] != 'disabled' and new['thinking'] == 'disabled'
    assert {k: v for k, v in old.items() if k not in {'thinking', 'metadata'}} == {k: v for k, v in new.items() if k not in {'thinking', 'metadata'}}
    assert {k:v for k,v in new['metadata'].items() if k != 'planning_generation_policy'} == old['metadata']
    from contractedge.cli import _build_provider
    from contractedge.model.admission import quote_request
    from contractedge.model.provider import ModelRequest
    config = json.loads(Path('configs/fixed46-candidate-v057-budget150.json').read_text())
    provider, counter = _build_provider(config)
    wires = {}
    for label, request in [('old', old), ('new', new)]:
        quote = quote_request(provider, ModelRequest(**{**request, 'messages': tuple(request['messages'])}), counter)
        wires[label] = json.loads(quote.prepared.body)
        assert quote.total_tokens <= config['max_context_tokens']
    changed_wire_fields = sorted(key for key in wires['old'].keys() | wires['new'].keys() if wires['old'].get(key) != wires['new'].get(key))
    # The existing adapter removes reasoning_effort when thinking is disabled.
    assert changed_wire_fields == ['reasoning_effort', 'thinking']
    assert wires['old']['thinking'] == {'type': 'enabled'} and wires['new']['thinking'] == {'type': 'disabled'}
    assert wires['old']['reasoning_effort'] == config['reasoning_effort'] and 'reasoning_effort' not in wires['new']
    result = dict(status='SAVED_FULL_FRONT_MODE_REPLAY_PASS', source_snapshot=snapshot,
        messages_exact_original=True, schema_exact_original=True, output_budget_unchanged=True,
        new_model_calls=0, new_grader_calls=0, source_unchanged=True,
        original_output_and_failure_unchanged=True, old_mode=old['thinking'], new_mode=new['thinking'],
        public_declaration_order_restored=True, adapter_wire_changed_fields=changed_wire_fields,
        provenance=new['metadata']['planning_generation_policy'], formal_ready=False,
        limitation='Proves actual serialized transport policy, not that a new plan or patch succeeds.')
    (OUT / 'SAVED_GENERATION_REPLAY.json').write_text(json.dumps(result, indent=2)+'\n')
    (OUT / 'new-planning-request.json').write_text(json.dumps(new, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
