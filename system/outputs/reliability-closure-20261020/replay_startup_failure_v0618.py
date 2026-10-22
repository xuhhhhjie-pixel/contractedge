"""Actual saved startup crash -> source-bound feedback -> native retry requests; zero HTTP."""
import json
import shutil
import tempfile
from pathlib import Path

from contractedge.cli import _token_counter
from contractedge.execution.probes import candidate_runtime_failures
from contractedge.execution.public import public_failure_diagnostics
from contractedge.execution.topomen_agent import TopoMenAgentExecutor
from contractedge.execution.topomen import execution_review_mapping
from contractedge.orchestration.feedback import make_feedback,feedback_prompt_view
from contractedge.orchestration.repair import rebind_execution_plan,select_observations
from contractedge.source.snapshot import compute_snapshot


class Captured(BaseException):pass
class NoHTTP:
    def complete_json(self,request):self.request=request;raise Captured()


def main():
    root=Path.cwd();out=root/'outputs/reliability-closure-20261020';dest=out/'startup-failure-continuity-v0618';dest.mkdir(exist_ok=False)
    read=lambda p:json.loads(p.read_text())
    origin=out/'runs/delivery-0.6.16-A-RW-ALERTMANAGER-4705-0-candidate/cases/RW-ALERTMANAGER-4705'
    component=out/'execution-schema-single-factor-v0617'
    workspace=read(component/'workspace/workspace.json');source=Path(workspace['source_root'])
    report=read(component/'public/method-probes/report.json');obs=read(component/'public/observation.json')
    facts=candidate_runtime_failures(report,component/'public/method-probes',source)
    assert facts and all(f['snapshot_id']==obs['snapshot_id'] for f in facts)
    observed={**obs,'status':'EXECUTION_FAILED','candidate_runtime_failures':facts}
    semantic=read(origin/'rounds/0/public-review-plan.json');execution=read(component/'execution.json');patch=read(component/'patch.json')
    method=read(origin/'method_input.json');oldplan=read(origin/'rounds/0/plan.json')
    feedback=make_feedback(case_id=method['case_id'],run_id=oldplan['run_id'],input_digest=method['input_digest'],round_id=0,
        from_snapshot=execution['from_snapshot'],to_snapshot=execution['to_snapshot'],patch_digest=patch['patch_sha256'],
        execution_status=observed['status'],observations=observed['observations'],plan=semantic,
        review={'decision':'UNKNOWN','checks':[]},observation_diagnostics=public_failure_diagnostics(observed))
    assert feedback['route']=='EXECUTE' and feedback['semantic_outcome']=='UNKNOWN'
    assert feedback['next_action']=='EXECUTE'
    (dest/'feedback.json').write_text(json.dumps(feedback,ensure_ascii=False,indent=2)+'\n')
    cfg=read(root/'configs/deepseek-reliability-closure-v6-budget150.json');counter=_token_counter(cfg['tokenizer']);records=[]
    original_call=read(origin/'model_calls.json')[2]
    declared=json.loads(original_call['messages'][1]['content'])['declared_artifacts']
    with tempfile.TemporaryDirectory(prefix='ce-startup-replay-') as td:
        before_source=next((out/'public-source-alertmanager').iterdir())
        restored=Path(td)/'restored';shutil.copytree(before_source,restored)
        for mode,current in [('current_failed_candidate',source),('restored_incumbent',restored)]:
            before=compute_snapshot(current).snapshot_id
            plan=rebind_execution_plan(oldplan,current,feedback if mode=='current_failed_candidate' else {},
                **({'prior_execution':execution} if mode=='current_failed_candidate' else {}))
            options=dict(requirement=method['requirement_text'],declared_artifacts=declared)
            if mode=='current_failed_candidate':
                options.update(feedback=feedback,observations=select_observations(observed['observations'],method['requirement_text'],counter,cfg['max_public_evidence_tokens']))
            else:
                options.update(previous_attempt=dict(source_snapshot=before,validation_error='Candidate startup failed; revise from restored current source.',
                    rejected_trial=dict(snapshot_id=execution['to_snapshot'],feedback=feedback_prompt_view(feedback),
                        actual_edits=execution_review_mapping(execution),patch_digest=patch['patch_sha256'])))
            provider=NoHTTP();executor=TopoMenAgentExecutor(provider,counter,cfg)
            try:executor.execute(plan,current,read(origin/'projection.json')['editable_policy'],**options)
            except Captured:pass
            else:raise AssertionError('Native execution request was not captured')
            request=provider.request;body=json.loads(request.messages[1]['content']);serialized=request.messages[1]['content']
            assert 'invalid memory address' in serialized
            assert 'main.go:542' in serialized
            assert body['accepted_plan']['snapshot_id']==before
            assert body['current_target_files'][0]['content']==(current/'cmd/alertmanager/main.go').read_text()
            assert body['declared_artifacts']==declared
            assert body['output_schema']==request.output_schema
            assert compute_snapshot(current).snapshot_id==before
            if mode=='restored_incumbent':
                history=body['previous_execution_attempt']['rejected_trial']['feedback']
                assert history['to_snapshot']==execution['to_snapshot']!=before
                assert history['failure_tasks']['shared_execution_errors'][0]['snapshot_id']==execution['to_snapshot']
            bound=sum(counter(m['content']) for m in request.messages)
            assert bound+request.max_output_tokens<=cfg['max_context_tokens']
            (dest/(mode+'-request.json')).write_text(json.dumps(dict(messages=list(request.messages),output_schema=request.output_schema),ensure_ascii=False,indent=2)+'\n')
            records.append(dict(stage=mode,snapshot_id=before,input_bound=bound,output_bound=request.max_output_tokens,
                declared_inputs=len(declared),startup_error_delivered=True,source_unchanged=True))
    summary=dict(api_calls=0,source='Actual0.6.17 saved component public startup crash',
        old_feedback_route='STOP',new_feedback_route=feedback['route'],semantic_outcome=feedback['semantic_outcome'],
        facts=facts,records=records,scope='Routing and request transport replay; not model recovery or E2E success')
    (dest/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n');print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':main()
