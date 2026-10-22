"""Bind saved plan to the versioned public scenario; no model or evaluator inputs."""
import json
from pathlib import Path

from contractedge.execution.review import ModelPublicReviewer
from contractedge.orchestration.feedback import make_feedback,reassess_failed_target_predicates


def main():
    out=Path('outputs/reliability-closure-20261020')
    origin=out/'runs/delivery-0.6.15-A-RW-ALERTMANAGER-4705-0-candidate/cases/RW-ALERTMANAGER-4705'
    read=lambda p:json.loads(p.read_text())
    # Only scenario/check namespace changes; predicates/coverage/unknown remain.
    plan=json.loads((origin/'rounds/0/public-review-plan.json').read_text().replace('alertmanager-reload-v2','alertmanager-reload-v3'))
    before=read(out/'temporal-v3-calibration/broken/public/observation.json')
    after=read(out/'temporal-v3-calibration/saved_candidate/public/observation.json')
    trace=read(origin/'repair.trace.json');front=read(origin/'rounds/0/front.trace.json')
    class NoCalls:
        def complete_json(self,request):raise ValueError('Offline replay: unverifiable residual stays UNKNOWN')
    review=ModelPublicReviewer(NoCalls(),len,{'model':'offline-no-network'}).review(
        read(origin/'method_input.json')['requirement_text'],plan,after)
    feedback=make_feedback(case_id=trace['case_id'],run_id=trace['run_id'],input_digest=trace['input_digest'],
        round_id=0,from_snapshot=before['snapshot_id'],to_snapshot=after['snapshot_id'],
        patch_digest=trace['candidate']['patch_sha256'],execution_status=after['status'],
        observations=after['observations'],plan=plan,review=review,diagnosis=front['diagnosis'])
    final=reassess_failed_target_predicates(feedback,before,after,plan)
    assert any(c['id']=='P1' and c['status']=='FAIL' for c in final['public_check_results'])
    assert final['route']=='DIAGNOSIS'
    (out/'temporal-v3-feedback.json').write_text(json.dumps(final,ensure_ascii=False,indent=2)+'\n')
    result=dict(scope='OFFLINE_PUBLIC_BINDING_REPLAY_NOT_NEW_REPAIR_SCORE',api_calls=0,
        adaptation='Saved v2 plan check/scenario namespace rebound to v3 only; original plan untouched',
        old_method_route='STOP',old_method_semantic_outcome='UNKNOWN',
        new_route=final['route'],new_semantic_outcome=final['semantic_outcome'],
        checks=final['public_check_results'],rationale=final['rationale'],
        private_inputs_loaded=False)
    (out/'temporal-v3-feedback-replay.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
