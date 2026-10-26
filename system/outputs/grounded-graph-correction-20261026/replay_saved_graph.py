"""Replay public rejected diagnosis/correction boundary only; no provider or scorer."""
import hashlib
import json
from pathlib import Path

from contractedge.orchestration.runner import _append_diagnosis_retry_turn


def main():
    saved = Path('/home/xhj/contractedge-diagnostic-source056-v2-20261026/methods/R3-KPT-4673/R3-KPT-4673/model_calls.json')
    output = Path(__file__).parent
    before = saved.read_bytes()
    calls = json.loads(before)
    old_messages = calls[1]['messages']
    rejected = calls[0]['response_content']
    feedback = old_messages[-1]['content']
    new_messages = _append_diagnosis_retry_turn(calls[0]['messages'], rejected, feedback)
    assert new_messages[:-1] == tuple(old_messages[:-1])
    inventory = json.loads(new_messages[-1]['content'].split('Rejected graph inventory (not verified causality): ', 1)[1])
    consumers = {row['unit_id']: row for row in inventory['source_anchored_consumers']}
    assert {'U2', 'U3'} <= consumers.keys()
    assert consumers['U2']['source_refs'] == ['SRC-CU-883e2cb2727d395a']
    assert consumers['U3']['source_refs'] == ['SRC-CU-60d3496476d58093']
    assert any(row['source_unit_ids'] == ['U1'] and row['target_unit_ids'] == ['U5'] for row in inventory['relations'])
    assert any(row['source_unit_ids'] == ['U1'] and row['target_unit_ids'] == ['U6'] for row in inventory['relations'])
    appended_bytes = len(new_messages[-1]['content'][len(feedback):].encode())
    assert appended_bytes <= 4096
    assert saved.read_bytes() == before
    (output / 'saved-correction-request.json').write_text(json.dumps(new_messages, ensure_ascii=False, indent=2) + '\n')
    result = dict(status='PUBLIC_SAVED_CORRECTION_REPLAY_PASS',
        saved_calls_sha256=hashlib.sha256(before).hexdigest(),
        rejected_draft_sha256=hashlib.sha256(rejected.encode()).hexdigest(),
        first_request_unchanged=True, rejected_draft_unchanged=True,
        canonical_source_inventory_only=True, source_consumers_visible=['U2', 'U3'],
        bypass_endpoints_visible=True, appended_bytes=appended_bytes,
        inventory_omitted=inventory['omitted'], original_failure_unchanged=True,
        new_model_calls=0, new_grader_calls=0, formal_ready=False,
        limitation='Counterfactual input delivery only; no new diagnosis, causal support or patch success inferred.')
    (output / 'SAVED_GRAPH_REPLAY.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
