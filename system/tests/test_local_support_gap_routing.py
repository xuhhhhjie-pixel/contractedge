"""Local evidence survives a blocking same-outcome gap without authorizing edits."""
import hashlib
import json

import pytest

from contractedge.contracts.validation import canonical_digest
from contractedge.diagnosis.prompt import SYSTEM_PROMPT, _case_output_schema
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.runner import _format_validation_feedback, run_front_half
from contractedge.source.snapshot import compute_snapshot
from tests.test_orchestration import PipelineFixtureProvider
from tests.test_planning_evidence_extension import front_fixture


@pytest.mark.parametrize('resolved', [False, True])
def test_local_supported_gap_reaches_bounded_rediagnosis_without_losing_its_evidence(tmp_path, resolved):
    method, template = front_fixture(tmp_path)
    renderer = tmp_path / 'renderer.go'
    renderer.write_text('package renderer\nfunc renderAccount() { render("templates/serviceaccount.yaml") }\n')
    method['initial_snapshot_id'] = compute_snapshot(tmp_path).snapshot_id
    method['public_evidence'][0]['snapshot_id'] = method['initial_snapshot_id']
    method.pop('input_digest'); method['input_digest'] = canonical_digest(method)

    class Provider(PipelineFixtureProvider):
        diagnosis_calls = 0
        plan_calls = 0
        reassessment_seen = False

        def complete_json(self, request):
            response = super().complete_json(request)
            if not request.prompt_version.startswith('diagnosis.'):
                self.plan_calls += 1
                assert resolved, 'Unresolved local support must not authorize planning'
                return response
            self.diagnosis_calls += 1
            context = json.loads(request.messages[1]['content'])
            catalog = {record['id']: record for record in context['evidence_catalog']}
            source = next(record for record in catalog.values()
                          if record['kind'] == 'SOURCE_SPAN'
                          and record['locator']['path'] == 'templates/serviceaccount.yaml')
            observed = next(record for record in catalog.values()
                            if record['kind'] == 'PUBLIC_OBSERVATION')
            body = json.loads(response.content)
            body['units'][1]['locator_refs'] = [source['id']]
            body['expected_relations'][0]['support_refs'] = [source['id']]
            body['violations'][0]['evidence_refs'] = [observed['id'], source['id']]
            body['evidence_refs'] = [observed['id'], source['id']]
            body['units'].append(dict(id='U3', role='rendered account boundary', kind='runtime output',
                semantic_role_class='OBSERVED_STATE', existence='UNKNOWN', locator_refs=[],
                requirement_refs=['O1']))
            body['expected_relations'].append(dict(id='E2', source_unit_ids=['U2'], target_unit_ids=['U3'],
                relation_form='DIRECT_EDGE', shared_constraint_reason=None, mechanism='RENDERING', condition='enabled',
                expected_contract='The production renderer delivers the template account to the observed boundary.',
                requirement_refs=['O1'], obligation_refs=['A1'], support_refs=[]))
            body.update(status='EVIDENCE_GAP', unverified_outcomes=['O1'], evidence_gaps=[dict(
                id='G1', summary='Locate the production renderer that consumes the template.',
                locator_hint={'path': 'renderer.go', 'symbol': 'renderAccount'})])
            # Use the actual model encoding: this disposition preserves local
            # support even while status and gaps keep the whole case blocked.
            body['outcome_coverage'] = {'O1': dict(disposition='REPAIR_SUPPORTED',
                preservation_status='NOT_APPLICABLE', preservation_evidence_refs=[])}
            if self.diagnosis_calls == 2:
                continuity = context['diagnosis_continuity']
                assert continuity['extension_gaps'][0]['id'] == 'G1'
                claim = next(row for row in continuity['claims_for_reassessment']
                             if row['violation']['id'] == 'V1')
                assert claim['violation']['causal_status'] == 'SUPPORTED'
                assert source['id'] in claim['current_evidence_refs']
                assert catalog[source['id']]['excerpt'] == source['excerpt']
                implementation = next(record for record in catalog.values()
                    if record['kind'] == 'SOURCE_SPAN' and record['locator']['path'] == 'renderer.go'
                    and 'func renderAccount()' in record['excerpt'])
                self.reassessment_seen = True
                if resolved:
                    body['units'][-1].update(existence='PRESENT', locator_refs=[observed['id']])
                    body['expected_relations'][-1]['support_refs'] = [source['id'], implementation['id']]
                    body['evidence_refs'].append(implementation['id'])
                    body.update(status='READY', evidence_gaps=[])
                    # Future repaired behavior remains unverified. The current
                    # source supports planning, not a behavioral PASS verdict.
            return ModelResponse(json.dumps(body), {'provider': 'local-support-gap-fixture'})

    provider = Provider()
    provider._snapshot_digests = {'templates/serviceaccount.yaml': hashlib.sha256(template.read_bytes()).hexdigest()}
    trace = run_front_half(method, tmp_path, provider,
        dict(model={'model': 'fixture'}, top_n=12, max_context_tokens=40000,
             max_output_tokens=1024, max_total_tokens=160000, max_format_retries=0),
        lambda text: len(text.split()))
    assert trace.status == ('READY' if resolved else 'EVIDENCE_GAP'), trace.errors
    assert provider.diagnosis_calls == 2 and provider.reassessment_seen
    assert provider.plan_calls == int(resolved)
    assert sum(bool(row.get('extension_used')) for row in trace.retrieval_rounds) == 1
    assert trace.diagnosis['violations'][0]['id'] == 'V1'
    assert trace.diagnosis['violations'][0]['causal_status'] == 'SUPPORTED'
    assert trace.diagnosis['outcome_coverage'][0]['repair_supported'] == ['V1']
    assert trace.diagnosis['unverified_outcomes'] == ['O1']
    if not resolved:
        assert trace.diagnosis['evidence_gaps'][0]['id'] == 'G1'
        assert trace.changeset is None


@pytest.mark.parametrize('code', ['OUTCOME_CATEGORY_CONFLICT at outcome_coverage[0]',
    'OUTCOME_SUPPORTED_VIOLATION_UNCOVERED', 'STATUS_INCONSISTENT', 'PRODUCTION_PATH_INCOMPLETE'])
def test_correction_keeps_local_support_and_same_outcome_uncertainty(code):
    feedback = _format_validation_feedback(ValueError(code), {})
    assert 'EVIDENCE_GAP' in feedback
    assert 'unverified_outcomes' in feedback
    assert 'remove that outcome from unverified_outcomes' not in feedback
    assert 'EVIDENCE_GAP means no supported' not in feedback
    assert 'EVIDENCE_GAP requires that no supported' not in feedback


def test_prompt_uses_existing_statuses_for_local_support_and_blocking_gaps():
    assert 'REPAIR_SUPPORTED means a local causal violation' in SYSTEM_PROMPT
    assert 'including when a local SUPPORTED violation is already established' in SYSTEM_PROMPT
    assert 'READY permits planning; it is not behavioral PASS' in SYSTEM_PROMPT
    assert 'PARTIAL_READY requires a distinct unresolved outcome' in SYSTEM_PROMPT
    assert 'no supported actionable violation exists' not in SYSTEM_PROMPT
    schema = _case_output_schema({'outcomes': [{'id': 'O1', 'statement': 'Render account.'}]}, [])
    assert 'PARTIAL_READY' not in schema['properties']['status']['enum']
