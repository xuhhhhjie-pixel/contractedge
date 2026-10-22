"""Saved public trajectory: current source delivery and joint error feedback, no API."""
import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from contractedge.cli import _token_counter
from contractedge.contracts.models import RunContext
from contractedge.contracts.validation import parse_method_input
from contractedge.dataset.archive import safe_extract
from contractedge.orchestration import runner
from contractedge.orchestration.repair import select_observations
from contractedge.retrieval.candidates import CandidateSet
from contractedge.source.snapshot import compute_snapshot


class Captured(BaseException):
    pass


def read(path):
    return json.loads(path.read_text())


def main():
    root = Path.cwd()
    out = root/'outputs/reliability-closure-20261020'
    dest = out/'kopf-closure-replay-v0620'
    origin = out/'runs/delivery-0.6.19-A-R3-KOPF-726-0-candidate/cases/R3-KOPF-726'
    method = read(origin/'method_input.json')
    config = read(root/'configs/deepseek-reliability-closure-v6-budget150.json')
    counter = _token_counter(config['tokenizer'])
    front0 = read(origin/'rounds/0/front.trace.json')
    front1 = read(origin/'rounds/1/front.trace.json')
    feedback = read(origin/'rounds/0/feedback.json')
    archive = Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019/public')/method['archive_path']
    if not (dest/'S0').exists():
        safe_extract(archive, dest/'S0')
    source0 = dest/'S0'/method['source_root']
    assert compute_snapshot(source0).snapshot_id == method['initial_snapshot_id']
    if not (dest/'S1').exists():
        shutil.copytree(dest/'S0', dest/'S1')
        subprocess.run(['patch','--batch','--no-backup-if-mismatch','-p1','-i',
                        str(origin/'rounds/0/candidate.patch')],
                       cwd=dest/'S1'/method['source_root'], check=True, capture_output=True)
    source1 = dest/'S1'/method['source_root']
    assert compute_snapshot(source1).snapshot_id == feedback['to_snapshot']
    observations = []
    for name in ('broken-public/observation.json', 'rounds/0/public/observation.json'):
        report = read(origin/name)
        observations.append(tuple(select_observations(report['observations'], method['requirement_text'],
                                                      counter, config['max_public_evidence_tokens'])))
    receipts = []
    for index, (source, front) in enumerate(((source0,front0),(source1,front1))):
        cfg = {**config, 'run_id':front['run_id'], 'model':front['run_context']['model_config'],
               'public_execution_capabilities':front['run_context']['retrieval_config']['public_execution_capabilities']}
        captured = {}
        build = runner.build_diagnosis_request
        def capture_build(*args, **kwargs):
            captured.update(method=args[0],candidates=args[2],context=args[3])
            return build(*args, **kwargs)
        class Provider:
            def complete_json(self, request):
                captured['request'] = request
                raise Captured()
        with patch.object(runner,'build_diagnosis_request',capture_build):
            try:
                trace = runner.run_front_half(method, source.parent, Provider(), cfg, counter,
                    public_observations=observations[index], execution_feedback=feedback if index else None)
            except Captured:
                pass
            else:
                raise AssertionError((trace.status,trace.errors))
        request = captured['request']
        body = json.loads(request.messages[1]['content'])
        sources = [r for r in body['evidence_catalog'] if r['kind']=='SOURCE_SPAN']
        for name in ('_guess_owner','adjust_namespace','adopt'):
            assert any('def '+name+'(' in r['excerpt'] for r in sources), name
        assert len(body['candidate_set']['selected_ids']) <= 12
        sid = compute_snapshot(source).snapshot_id
        assert all(r['snapshot_id'] == sid for r in sources)
        assert all(r['excerpt'] in (source/r['locator']['path']).read_text() for r in sources)
        input_bound = sum(counter(m['content']) for m in request.messages)
        assert input_bound <= cfg['max_context_tokens']
        assert input_bound+request.max_output_tokens <= cfg['model_context_tokens']
        support = runner.required_planning_source_support(captured['method'], captured['candidates'], sid)
        prerequisite_ids = ['SRC-'+u['id'] for u in captured['candidates'].candidates
                            if u.get('precondition_review_required')]
        assert prerequisite_ids
        from contractedge.changeset.prompt import build_changeset_request
        context = replace(captured['context'],retrieval_config={**captured['context'].retrieval_config,
            'necessary_source_support':support,
            'source_prerequisites':[dict(source_evidence_ref=x,reason='Review exception producer guard')
                                    for x in prerequisite_ids]})
        # Saved diagnosis used only to exercise the transport; not a new model judgment or Ready approval.
        plan_request = build_changeset_request(SimpleNamespace(raw=front0['diagnosis']),context)
        planning = json.loads(plan_request.messages[1]['content'])
        assert planning['read_only_source_support'] == support
        assert planning['source_prerequisites']
        execution_input = None
        if not index:
            from contractedge.changeset.topomen import translate_for_topomen
            from contractedge.execution.topomen import build_execution_request
            plan = translate_for_topomen(front0['changeset'],'contractedge.topomen_plan.v1',
                diagnosis=front0['diagnosis'],source_support=support,
                public_capabilities=cfg['public_execution_capabilities'])
            execution = build_execution_request(plan,source,read(origin/'projection.json')['editable_policy'],
                counter,cfg,requirement=method['requirement_text'])
            executor = json.loads(execution.messages[1]['content'])
            for record in support:
                assert any(r['id']==record['id'] for r in executor['read_only_source_support']) or any(
                    r['path']==record['locator']['path'] and record['excerpt'] in r['content']
                    for r in executor['current_target_files'])
            execution_input = sum(counter(m['content']) for m in execution.messages)
        receipts.append(dict(stage='REDIAGNOSIS_S1' if index else 'INITIAL_DIAGNOSIS_S0',snapshot=sid,
            input_bound=input_bound,planning_input=sum(counter(m['content']) for m in plan_request.messages),
            execution_transport_fixture_input=execution_input,required_support=len(support),
            prerequisite_ids=prerequisite_ids,selected_count=len(body['candidate_set']['selected_ids']),
            exact_current_source_match=True,source_unchanged=compute_snapshot(source).snapshot_id==sid))
        (dest/f'request-S{index}.json').write_text(json.dumps(dict(messages=request.messages,
            metadata=request.metadata),ensure_ascii=False,indent=2)+'\n')
    # Preserve the exact rejected body. No hand repair or extra model correction.
    calls = read(origin/'model_calls.json')
    raw = json.loads(calls[4]['response_content'])
    old_method = replace(parse_method_input(method),public_evidence=observations[1])
    try:
        payload,_ = runner._bind_diagnosis_envelope(raw,old_method,RunContext(**front1['run_context']),
            front1['requirement_spec'],CandidateSet(**front1['candidate_set']))
        runner.validate_diagnosis(payload,old_method,RunContext(**front1['run_context']),
                                  CandidateSet(**front1['candidate_set']))
    except ValueError as error:
        correction = runner._format_validation_feedback(error,raw)
    else:
        raise AssertionError('Saved invalid response unexpectedly accepted')
    assert 'outcome_refs_note_removed' in correction
    assert 'OPERATION_UNRELATED_UNSUPPORTED' in correction and 'scope_evidence_refs' in correction
    (dest/'joint-correction.txt').write_text(correction+'\n')
    summary = dict(api_calls=0,actual_request_receipts=receipts,
        saved_invalid_body_still_rejected=True,both_independent_errors_in_same_correction=True,
        scope='Actual initial/rediagnosis request capture; saved-plan downstream transport fixture, not a new repair or Ready approval')
    (dest/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False))


if __name__ == '__main__':
    main()
