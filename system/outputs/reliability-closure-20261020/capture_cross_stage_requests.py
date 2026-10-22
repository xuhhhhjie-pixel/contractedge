"""Execute real request builders against public S0/S1; never call a model."""
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

from contractedge.cli import _token_counter
from contractedge.orchestration.runner import run_front_half
from contractedge.orchestration.repair import select_observations
from contractedge.source.snapshot import compute_snapshot
from contractedge.contracts.models import RunContext
from contractedge.changeset.prompt import build_changeset_request
from contractedge.model.provider import ModelResponse
from contractedge.execution.topomen import build_execution_request
from contractedge.changeset.topomen import translate_for_topomen


class Captured(BaseException):
    pass


class Offline:
    def complete_json(self, request):
        self.request = request
        raise Captured()


class ReplaySavedDiagnosis(Offline):
    """Only on unchanged S0; replay saved response to reach the real planner."""
    def __init__(self, content):
        self.content = content

    def complete_json(self, request):
        if request.prompt_version.startswith('diagnosis'):
            return ModelResponse(self.content, {'provider':'offline_saved_response', 'finish_reason':'stop'})
        return super().complete_json(request)


def main():
    root = Path.cwd(); out = root/'outputs/reliability-closure-20261020'
    case = out/'runs/delivery-0.6.10-A-RW-ALERTMANAGER-4705-0-candidate/cases/RW-ALERTMANAGER-4705'
    load = lambda name: json.loads((case/name).read_text())
    cfg = json.loads((root/'configs/deepseek-reliability-closure-v4-budget80.json').read_text())
    old = load('rounds/0/front.trace.json')
    cfg.update(run_id=old['run_id'], model=old['run_context']['model_config'],
        public_execution_capabilities=old['run_context']['retrieval_config']['public_execution_capabilities'])
    counter = _token_counter(cfg['tokenizer']); records = []
    method = load('method_input.json')
    source = next((out/'public-source-alertmanager').iterdir())
    with tempfile.TemporaryDirectory(prefix='ce-cross-stage-') as temp:
        current = Path(temp)/source.name
        shutil.copytree(source, current)
        for stage in ['initial', 'rediagnosis']:
            feedback = None
            if stage == 'rediagnosis':
                subprocess.run(['patch', '-p1', '--batch', '--input', str(case/'candidate.patch')],
                    cwd=current, check=True, capture_output=True)
                feedback = load('rounds/0/feedback.json')
                assert compute_snapshot(current).snapshot_id == feedback['to_snapshot']
            report = load('broken-public/observation.json' if stage == 'initial' else 'rounds/0/public/observation.json')
            observations = tuple(select_observations(report['observations'], method['requirement_text'],
                counter, cfg.get('max_public_evidence_tokens', 4000)))
            provider = Offline()
            try:
                trace = run_front_half(method, Path(temp), provider, cfg, counter,
                    execution_feedback=feedback, public_observations=observations)
            except Captured:
                pass
            else:
                raise AssertionError((stage, trace.status, trace.errors))
            request = provider.request
            payload = json.loads(request.messages[1]['content'])
            support = [r for r in payload['evidence_catalog'] if r.get('locator', {}).get('path')=='dispatch/dispatch.go']
            text = '\n'.join(r.get('excerpt','') for r in support)
            assert 'func (d *Dispatcher) Run(' in text, stage
            assert 'func (d *Dispatcher) run(' in text, stage
            sid = compute_snapshot(current).snapshot_id
            assert all(r['snapshot_id']==sid and hashlib.sha256(r['excerpt'].encode()).hexdigest()==r['content_digest'] for r in support)
            context = RunContext(old['run_id'],method['case_id'],int(stage=='rediagnosis'),sid,
                feedback['from_snapshot'] if feedback else None,method['input_digest'],
                retrieval_config={'necessary_source_support':support})
            plan_request = build_changeset_request(SimpleNamespace(raw={}),context)
            assert json.loads(plan_request.messages[1]['content'])['read_only_source_support']==support
            records.append(dict(stage=stage,snapshot=sid,input_bound=sum(counter(m['content']) for m in request.messages),
                helpers=[dict(id=r['id'],locator=r['locator'],content_digest=r['content_digest']) for r in support],
                delivery=request.metadata.get('gap_delivery'),planning_builder_preserves_support=True,
                planning_scope='Builder transport check, not regenerated model plan'))
            if stage == 'initial':
                saved_calls = load('model_calls.json')
                replay = ReplaySavedDiagnosis(saved_calls[1]['response_content'])
                try:
                    trace = run_front_half(method, Path(temp), replay, cfg, counter,
                        public_observations=observations)
                except Captured:
                    pass
                else:
                    raise AssertionError(('Actual planner not reached', trace.status, trace.errors))
                plan_body = json.loads(replay.request.messages[1]['content'])
                assert plan_body['source_prerequisites']
                assert 'precondition_review' in replay.request.output_schema['required']
                records[-1]['actual_front_half_planner'] = dict(
                    source_prerequisites=plan_body['source_prerequisites'],
                    input_bound=sum(counter(m['content']) for m in replay.request.messages),
                    scope='Saved unchanged-S0 diagnosis replay; planner request captured before response')
                transport_plan = load('rounds/0/plan.json')
                transport_plan['coordination_context']['source_support'] = plan_body['read_only_source_support']
                transport_plan['precondition_review'] = [dict(source_evidence_ref=r['source_evidence_ref'],
                    condition='Transport fixture only; not a regenerated model plan', disposition='REQUIRED',
                    verification_refs=['P1'], open_obligation_refs=[], rationale='Offline request transport check')
                    for r in plan_body['source_prerequisites']]
                executor_config = {**cfg, 'model':'deepseek-v4.1-flash'}
                execution = build_execution_request(transport_plan,current,load('projection.json')['editable_policy'],
                    counter,executor_config,requirement=method['requirement_text'])
                ex_body = json.loads(execution.messages[1]['content'])
                actual_ids = {r['id'] for r in ex_body['read_only_source_support']}
                assert {r['source_evidence_ref'] for r in plan_body['source_prerequisites']} <= actual_ids
                records[-1]['executor_retains_prerequisite_ids'] = True
    target=out/'cross-stage-candidate-request-replay.json'
    target.write_text(json.dumps(dict(api_calls=0,records=records),ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(api_calls=0,records=[{k:v for k,v in r.items() if k!='helpers'} for r in records])))


if __name__ == '__main__':main()
