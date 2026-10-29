"""Causal source must be pinned before reranking, not only after it survives."""
import hashlib
import json
from unittest.mock import patch

import pytest

from contractedge.model.provider import ModelResponse
from contractedge.orchestration.runner import run_front_half
from contractedge.retrieval.candidates import extend_once
from tests.test_planning_evidence_extension import PlanningGapProvider, front_fixture


@pytest.mark.parametrize('trigger', ['DIAGNOSIS', 'CHANGESET'])
def test_real_extension_boundary_reserves_supported_source_before_ranking(tmp_path, trigger):
    value, source = front_fixture(tmp_path)
    class Provider(PlanningGapProvider):
        def complete_json(self, request):
            response = super().complete_json(request)
            if trigger == 'DIAGNOSIS' and request.prompt_version.startswith('diagnosis.') and self.diagnosis_calls == 1:
                body = json.loads(response.content)
                body.update(status='NEEDS_EVIDENCE', evidence_gaps=[dict(id='G1',summary='Check optional implementation',
                    locator_hint={'path':'templates/optional-serviceaccount.yaml'})])
                return ModelResponse(json.dumps(body), {})
            return response
    provider = Provider()
    provider._snapshot_digests = {'templates/serviceaccount.yaml': hashlib.sha256(source.read_bytes()).hexdigest()}
    observed = []
    def inspect(candidates, *args, **kwargs):
        refs = {r[4:] for v in provider.diagnosis['violations'] if v['causal_status']=='SUPPORTED'
                for r in v['evidence_refs'] if r.startswith('SRC-')}
        observed.append(refs)
        assert refs and refs <= set(candidates.required_endpoint_ids)
        return extend_once(candidates, *args, **kwargs)
    with patch('contractedge.orchestration.runner.extend_once', side_effect=inspect):
        trace = run_front_half(value, tmp_path, provider,
            dict(model={'model':'fixture'},max_context_tokens=24000,max_output_tokens=1024,max_total_tokens=100000,
                 gap_role_delivery_enabled=True),
            lambda text: len(text.split()))
    assert observed
    assert trace.status in {'READY','NEEDS_EVIDENCE'}, trace.errors


def test_cited_entrypoint_and_new_callee_both_survive_rerank_and_alias_merge():
    from contractedge.orchestration.runner import retain_supported_source
    from contractedge.retrieval.candidates import build_candidate_set
    from tests.test_required_extension_selection import unit
    entry = unit('entry','publish initialized replacement after prerequisite')
    initial = build_candidate_set('publish',None,[entry,unit('unused','background')],[],
        top_n=2,max_context_tokens=1000,token_counter=len)
    prior = dict(snapshot_id='S',input_digest='I',violations=[dict(causal_status='SUPPORTED',evidence_refs=['SRC-entry'])])
    pinned = retain_supported_source(initial,prior,'S','I')
    alias = {**entry,'id':'new-entry','gap_role_keys':['G:anchor']}
    callee = unit('callee','rebuild state prerequisite',required=True,role='G:anchor')
    selected = extend_once(pinned,[dict(id='G',locator_hint={'symbol':'prerequisite'})],[alias,callee],
        max_context_tokens=1000,token_counter=len)
    assert set(selected.selected_ids)=={'new-entry','callee'}
    assert set(selected.required_endpoint_ids)=={'new-entry','callee'}
    assert not initial.required_endpoint_ids


@pytest.mark.parametrize('mutation',['snapshot','input','uncertain','hash','foreign_source'])
def test_old_foreign_or_unsupported_claims_cannot_pin_source(mutation):
    from contractedge.orchestration.runner import retain_supported_source
    from contractedge.retrieval.candidates import build_candidate_set
    from tests.test_required_extension_selection import unit
    entry=unit('entry','current entrypoint')
    prior=dict(snapshot_id='S',input_digest='I',violations=[dict(causal_status='SUPPORTED',evidence_refs=['SRC-entry'])])
    if mutation=='snapshot':prior['snapshot_id']='OLD'
    if mutation=='input':prior['input_digest']='OTHER'
    if mutation=='uncertain':prior['violations'][0]['causal_status']='UNCERTAIN'
    if mutation=='hash':entry['content_digest']='0'*64
    if mutation=='foreign_source':entry['snapshot_id']='OLD'
    initial=build_candidate_set('entry',None,[entry],[],top_n=2,max_context_tokens=1000,token_counter=len)
    pinned=retain_supported_source(initial,prior,'S','I')
    assert not pinned.required_endpoint_ids


def test_overfull_causal_and_gap_evidence_remains_explicit_without_extra_slots():
    from contractedge.orchestration.runner import retain_supported_source
    from contractedge.retrieval.candidates import build_candidate_set
    from tests.test_required_extension_selection import unit
    initial=build_candidate_set('entry',None,[unit('entry','current entrypoint')],[],
        top_n=1,max_context_tokens=1000,token_counter=len)
    prior=dict(snapshot_id='S',input_digest='I',violations=[dict(causal_status='SUPPORTED',evidence_refs=['SRC-entry'])])
    pinned=retain_supported_source(initial,prior,'S','I')
    selected=extend_once(pinned,[dict(id='G',locator_hint={'symbol':'callee'})],
        [unit('callee','callee',required=True,role='G:anchor')],max_context_tokens=1000,token_counter=len)
    assert len(selected.selected_ids)==1
    assert len(selected.required_endpoint_ids)==2
    assert set(selected.required_endpoint_ids)-set(selected.selected_ids)
