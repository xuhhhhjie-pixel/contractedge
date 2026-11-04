"""The adopted source lifetime supersedes citation-dependent pinning."""
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
        assert refs and refs <= set(kwargs['source_context']['candidate_ids'])
        assert set(candidates.selected_ids) <= set(kwargs['source_context']['candidate_ids'])
        return extend_once(candidates, *args, **kwargs)
    with patch('contractedge.orchestration.runner.extend_once', side_effect=inspect):
        trace = run_front_half(value, tmp_path, provider,
            dict(model={'model':'fixture'},max_context_tokens=24000,max_output_tokens=1024,max_total_tokens=100000,
                 gap_role_delivery_enabled=True),
            lambda text: len(text.split()))
    assert observed
    assert trace.status in {'READY','NEEDS_EVIDENCE'}, trace.errors


def test_cited_and_uncited_entrypoints_survive_without_renaming_their_records():
    from types import SimpleNamespace
    from contractedge.diagnosis.evidence import source_context_new,source_context_for_candidates
    from contractedge.retrieval.candidates import build_candidate_set
    from tests.test_required_extension_selection import unit
    entry=unit('entry','publish initialized replacement after prerequisite')
    initial=build_candidate_set('publish',None,[entry,unit('unused','background')],[],
        top_n=2,max_context_tokens=1000,token_counter=len)
    active=source_context_for_candidates(source_context_new(dict(run_id='run',case_id='case',
        input_digest='I',front_id='front')),SimpleNamespace(declared_artifacts=(),public_evidence=()),initial,'S')
    alias={**entry,'id':'new-entry','gap_role_keys':['G:anchor']}
    callee=unit('callee','rebuild state prerequisite',required=True,role='G:anchor')
    selected=extend_once(initial,[dict(id='G',locator_hint={'symbol':'prerequisite'})],[alias,callee],
        max_context_tokens=1000,token_counter=len,source_context=active)
    assert set(selected.selected_ids)=={'entry','unused','callee'}
    assert 'new-entry' not in selected.selected_ids
    assert set(selected.required_endpoint_ids)=={'entry','callee'}
    assert not initial.required_endpoint_ids
