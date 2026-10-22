"""Offline saved-trajectory replay: public method artifacts only, no provider."""
import hashlib
import json
from pathlib import Path

from contractedge.source.parsers import parse_source_units
from contractedge.source.snapshot import compute_snapshot


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)
    elif isinstance(value, str) and value[:1] in ('{', '['):
        try:
            decoded = json.loads(value)
        except ValueError:
            return
        yield from walk(decoded)


def main():
    out = Path(__file__).resolve().parent
    case = out/'runs/delivery-0.6.10-A-RW-ALERTMANAGER-4705-0-candidate/cases/RW-ALERTMANAGER-4705'
    read = lambda name: json.loads((case/name).read_text())
    source = next((out/'public-source-alertmanager').iterdir())
    snapshot = compute_snapshot(source)
    units = parse_source_units('dispatch/dispatch.go', (source/'dispatch/dispatch.go').read_text(),
                              snapshot.snapshot_id, max_lines=768, max_chars=32000)
    helpers = [u for u in units if u.get('symbol') == 'NewDispatcher' or
               (u.get('symbol') in {'Run', 'run', 'Groups', 'Stop'} and '(d *Dispatcher)' in u['text'])]
    calls = read('model_calls.json')
    rows = []
    for index, call in enumerate(calls):
        records = [d for d in walk(call['messages']) if isinstance(d.get('excerpt'), str)
                   and d.get('content_digest') and d.get('snapshot_id')]
        bad = [r.get('id') for r in records
               if hashlib.sha256(r['excerpt'].encode()).hexdigest() != r['content_digest']]
        assert not bad, bad
        def source_exact(record, unit):
            loc = record.get('locator', {})
            if loc.get('path') != unit['path'] or record['snapshot_id'] != snapshot.snapshot_id:
                return False
            if not (isinstance(loc.get('start_line'), int) and isinstance(loc.get('end_line'), int)):
                return False
            original = (source/unit['path']).read_text().splitlines(keepends=True)
            exact = ''.join(original[loc['start_line']-1:loc['end_line']])
            return record['excerpt'] == exact and unit['text'] in exact
        found = {u['symbol']: any(source_exact(r, u) for r in records) for u in helpers}
        rows.append(dict(index=index, stage=call['prompt_version'], full_fresh_helper_body=found,
                         exact_excerpt_digest_errors=bad))
    assert rows[3]['full_fresh_helper_body']['Run']
    assert not any(rows[i]['full_fresh_helper_body']['Run'] for i in [0, 1, 2, 4, 5, 6])
    front = read('rounds/0/front.trace.json')
    feedback = read('rounds/0/feedback.json')
    plan = read('rounds/0/plan.json')
    checks = [dict(id=o['behavior_check']['id'], status=o['behavior_check']['status'],
                   observed=json.loads(o['excerpt']).get('observed'))
              for o in front['diagnosis']['evidence_refs'] if o.get('behavior_check')]
    result = dict(api_calls=0, snapshot=snapshot.snapshot_id,
        method_input_digest=read('method_input.json')['input_digest'],
        requirement=read('method_input.json')['requirement_text'],
        facts=[dict(id='F1', source='method_input.json', fact='Groups continuity after valid reload',
                    consumers=['diagnosis', 'changeset', 'execution', 'review']),
               dict(id='F2', source='rounds/0/front.trace.json:diagnosis.evidence_refs',
                    fact='During-reload FAIL, after-reload PASS', checks=checks),
               dict(id='F3', source='public-source-alertmanager/dispatch/dispatch.go:145',
                    fact='Run initializes empty aggregation maps before processing subscribed alerts',
                    consumers=['diagnosis evaluating replacement readiness', 'changeset', 'execution']),
               dict(id='F4', source='rounds/0/plan.json:operations',
                    fact='Accepted action starts replacement then switches/stops; readiness not established',
                    operations=plan['operations']),
               dict(id='F5', source='rounds/0/feedback.json', fact='No target improvement; re-diagnosis selected',
                    route=feedback['route'], rationale=feedback['rationale'])],
        requests=rows,
        causal_assessment='Static caller ordering supports a candidate cause, but does not establish that starting replacement makes grouping state ready. Missing callee implementation leaves the accepted fix incomplete. Delivery repair is not proof of eventual repair success.',
        first_supported_boundary='INITIAL_SOURCE_SELECTION_TO_DIAGNOSIS: readiness inference lacks full initialization/consumption evidence; later execution-only enrichment is too late for that plan.',
        independent_defects=['FEEDBACK_ROUTE_AND_LEGACY_INSTRUCTION_CONFLICT',
                             'PARTIAL_READY_STATUS_WITHOUT_UNRESOLVED_OUTCOME'],
        terminal=read('rounds/1/front.trace.json')['errors'],
        limits='Replay of saved messages and public source; next-snapshot callee bodies have not been reconstructed here. No independent verifier internals read; no claim of unique root cause or repair acceptance.')
    (out/'cross-stage-failure-alignment.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(dict(api_calls=0, request_count=len(rows), full_body_initial=rows[0]['full_fresh_helper_body'],
                         full_body_execution=rows[3]['full_fresh_helper_body'], terminal=result['terminal'])))


if __name__ == '__main__':
    main()
