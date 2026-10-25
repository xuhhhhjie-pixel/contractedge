"""Feedback rediagnosis shares the total context, keeping initial/output caps."""
import json
import pytest
from contractedge.orchestration.runner import run_front_half
from contractedge.source.snapshot import compute_snapshot
from tests import test_feedback_context as feedback_fixtures


class Captured(BaseException):
    pass


class Capture:
    def __init__(self):self.requests=[]
    def complete_json(self,request):
        self.requests.append(request)
        raise Captured()


@pytest.fixture
def context():
    value=feedback_fixtures.FeedbackContextTests();value.setUp()
    try:yield value
    finally:value.doCleanups()


def invoke(context,provider,*,feedback=True,**options):
    return run_front_half(context.method,context.source.parent,provider,
        {**context.config,'run_id':context.trace['run_id'],
         'initial_diagnosis_context_tokens':1,**options},len,
        execution_feedback=context.feedback if feedback else None,
        public_observations=context.observations)


def test_valid_feedback_can_deliver_fresh_source_beyond_initial_cap_within_total(context):
    provider=Capture();before=compute_snapshot(context.source).snapshot_id
    with pytest.raises(Captured):invoke(context,provider)
    request=provider.requests[0];body=json.loads(request.messages[1]['content'])
    assert body['candidate_set']['selected_ids']
    assert any(e['kind']=='SOURCE_SPAN' and e['snapshot_id']==before for e in body['evidence_catalog'])
    assert sum(len(m['content']) for m in request.messages)+request.max_output_tokens<=context.config['max_context_tokens']
    assert 'pending_obligations' in request.messages[-1]['content']
    assert compute_snapshot(context.source).snapshot_id==before


def test_initial_diagnosis_keeps_its_separate_limit(context):
    provider=Capture();trace=invoke(context,provider,feedback=False)
    assert not provider.requests and trace.status=='BUDGET_EXHAUSTED'


@pytest.mark.parametrize('limits',[{'max_context_tokens':1024},
    {'model_context_tokens':1024},{'max_total_tokens':1}])
def test_feedback_does_not_bypass_total_output_or_shared_token_limits(context,limits):
    provider=Capture();trace=invoke(context,provider,**limits)
    assert not provider.requests and trace.status=='BUDGET_EXHAUSTED'


def test_stale_feedback_cannot_unlock_followup_context(context):
    context.feedback={**context.feedback,'to_snapshot':'f'*64}
    provider=Capture();trace=invoke(context,provider)
    assert not provider.requests and trace.status!='READY'
    assert any('FEEDBACK_SNAPSHOT_MISMATCH' in str(e) for e in trace.errors)
