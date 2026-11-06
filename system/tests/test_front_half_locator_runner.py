"""Runner with source context v2: locator replaces initial selection and extensions (batch 3c).

Spec section 10: empty initial selection starts the locator; lookup and final
diagnosis are separate replies; ChangeSet evidence requests return to the same
locator; no independent extension allowance; delivered bodies reach planning.
"""
import hashlib
import json
from dataclasses import replace

import pytest
from jsonschema import Draft202012Validator

from contractedge.model.provider import ModelResponse
from contractedge.orchestration.runner import run_front_half
from tests.test_bounded_retrieval_continuation import CONFIG
from tests.test_orchestration import PipelineFixtureProvider
from tests.test_planning_evidence_extension import front_fixture

from contractedge.diagnosis.evidence import SUPERSEDED_BY_SOURCE_CONTEXT_V2

V2_CONFIG = {**{k: v for k, v in CONFIG.items() if k not in SUPERSEDED_BY_SOURCE_CONTEXT_V2},
             'source_context_contract': 2}
TEMPLATE = 'templates/serviceaccount.yaml'


def read_template():
    return {'action': 'read', 'arguments': {'path': TEMPLATE, 'start_line': 1, 'end_line': 4}}


def finish_last_read(request):
    result = json.loads(request.messages[-1]['content'])['tool_result']
    return {'action': 'finish', 'deliver': [result['handle']]}


class V2Provider(PipelineFixtureProvider):
    """Locator turns are scripted; diagnosis bodies come from the v1 fixture, wrapped for v2."""

    def __init__(self, template, *, locator_script=None, lookups=0, changeset_gap=False):
        super().__init__()
        self._snapshot_digests = {TEMPLATE: hashlib.sha256(template.read_bytes()).hexdigest()}
        self.locator_script = list(locator_script or [read_template(), finish_last_read])
        self.lookups = lookups
        self.changeset_gap = changeset_gap
        self.requests = []

    def complete_json(self, request):
        self.requests.append(request)
        if request.prompt_version == 'locator.v1':
            step = self.locator_script.pop(0) if self.locator_script else {'action': 'finish'}
            value = step(request) if callable(step) else step
            return ModelResponse(json.dumps(value), {'provider': 'v2-fixture'})
        if request.prompt_version.startswith('diagnosis.v2.lookup.'):
            if self.lookups:
                self.lookups -= 1
                self.locator_script[:0] = [read_template(), finish_last_read]
                return ModelResponse(json.dumps({'response_kind': 'LOOKUP', 'lookup': {'evidence_gaps': [
                    {'summary': 'Which template renders metadata.name?', 'locator_hint': {'path': TEMPLATE}}]}}),
                    {'provider': 'v2-fixture'})
            body = json.loads(super().complete_json(replace(request, prompt_version='diagnosis.v1.1.24')).content)
            schema = request.metadata['diagnosis_body_schema']
            statements = schema['properties']['requirement_horizon']['items'].get('enum')
            if statements:
                body['requirement_horizon'] = statements[:1]
            body['outcome_coverage'] = {'O1': {'disposition': 'REPAIR_SUPPORTED',
                                               'preservation_status': 'NOT_APPLICABLE',
                                               'preservation_evidence_refs': []}}
            errors = sorted(Draft202012Validator(schema).iter_errors(body), key=str)
            assert not errors, errors[0].message
            return ModelResponse(json.dumps({'response_kind': 'DIAGNOSIS', 'diagnosis': body}),
                                 {'provider': 'v2-fixture'})
        response = super().complete_json(request)
        if request.prompt_version.startswith('changeset.') and self.changeset_gap:
            self.changeset_gap = False
            self.locator_script[:0] = [read_template(), finish_last_read]
            body = json.loads(response.content)
            body.update(status='NEEDS_EVIDENCE', changes=[], evidence_gaps=[
                dict(id='G1', summary='Need the template that renders metadata.name', locator_hint={'path': TEMPLATE})])
            return ModelResponse(json.dumps(body), {'provider': 'v2-fixture'})
        return response


def run(tmp_path, provider, **config):
    method, _ = front_fixture(tmp_path)
    return run_front_half(method, tmp_path, provider, {**V2_CONFIG, **config}, lambda t: len(t.split()))


def stages(trace):
    return [call.get('stage') for call in trace.model_calls]


def test_locator_starts_from_requirement_and_delivers_exact_bodies_to_diagnosis(tmp_path):
    method, template = front_fixture(tmp_path)
    provider = V2Provider(template)
    trace = run_front_half(method, tmp_path, provider, V2_CONFIG, lambda t: len(t.split()))
    assert trace.status == 'READY', trace.errors
    assert stages(trace)[:3] == ['LOCATOR', 'LOCATOR', 'DIAGNOSIS']
    session = trace.retrieval_rounds[0]['locator_session']
    assert session['status'] == 'FINISHED' and len(session['delivered']) == 1
    diagnosis = json.loads(provider.requests[2].messages[1]['content'])
    source = [r for r in diagnosis['evidence_catalog'] if r['kind'] == 'SOURCE_SPAN']
    assert source[0]['excerpt'] == template.read_bytes().decode() and source[0]['id'].startswith('SRC-NAV-')
    context = trace.run_context['retrieval_config']['source_context']
    assert context['version'] == 2 and len(context['producer_refs']['locator']) == 1
    assert not any(refs for producer, refs in context['producer_refs'].items() if producer != 'locator')
    planning = json.loads(provider.requests[-1].messages[1]['content'])
    assert json.dumps(template.read_bytes().decode())[1:-1] in json.dumps(planning)


def test_lookup_reply_runs_the_locator_then_reassesses_without_extension_allowance(tmp_path):
    method, template = front_fixture(tmp_path)
    provider = V2Provider(template, lookups=3)
    trace = run_front_half(method, tmp_path, provider, V2_CONFIG,
                           lambda t: len(t.split()))
    # Each lookup re-reads the same template: no new material, so the loop stops honestly.
    assert trace.status == 'EVIDENCE_GAP' and 'Locator found no new material' in trace.errors[0]
    assert [r['trigger'] for r in trace.retrieval_rounds] == ['INITIAL', 'DIAGNOSIS_LOOKUP']


def test_lookup_with_new_material_continues_past_the_old_two_extension_cap(tmp_path):
    method, template = front_fixture(tmp_path)
    for name in ('a.yaml', 'b.yaml', 'c.yaml'):
        (tmp_path / 'templates' / name).write_text(f'kind: ConfigMap\nmetadata:\n  name: {name}\n')
    from contractedge.source.snapshot import compute_snapshot
    from contractedge.contracts.validation import canonical_digest
    method['initial_snapshot_id'] = compute_snapshot(tmp_path).snapshot_id
    method['public_evidence'][0]['snapshot_id'] = method['initial_snapshot_id']
    method.pop('input_digest'); method['input_digest'] = canonical_digest(method)
    provider = V2Provider(template, lookups=0)

    def script():
        steps = [read_template(), finish_last_read]
        for name in ('a.yaml', 'b.yaml', 'c.yaml'):
            steps += [{'action': 'read', 'arguments': {'path': 'templates/' + name, 'start_line': 1, 'end_line': 3}},
                      finish_last_read]
        return steps
    provider.locator_script = script()
    original = provider.complete_json
    lookups = iter([True, True, True])

    def complete(request):
        if request.prompt_version.startswith('diagnosis.v2.lookup.') and next(lookups, False):
            provider.requests.append(request)
            return ModelResponse(json.dumps({'response_kind': 'LOOKUP', 'lookup': {'evidence_gaps': [
                {'summary': 'Next template'}]}}), {})
        return original(request)
    provider.complete_json = complete
    # The task-wide call budget (not an extension count) is what bounds this loop.
    tight = run_front_half(method, tmp_path, provider, V2_CONFIG,
                           lambda t: len(t.split()))
    assert tight.status == 'BUDGET_EXHAUSTED' and 'model calls budget' in tight.errors[0]
    provider.locator_script = script()
    lookups = iter([True, True, True])
    trace = run_front_half(method, tmp_path, provider, {**V2_CONFIG, 'max_model_calls': 20}, lambda t: len(t.split()))
    assert trace.status == 'READY', trace.errors
    assert [r['trigger'] for r in trace.retrieval_rounds] == ['INITIAL'] + ['DIAGNOSIS_LOOKUP'] * 3
    records = trace.run_context['retrieval_config']['source_context']['records']
    assert len([r for r in records if r['kind'] == 'SOURCE_SPAN']) == 4


def test_changeset_evidence_request_returns_to_the_same_locator(tmp_path):
    method, template = front_fixture(tmp_path)
    provider = V2Provider(template, changeset_gap=True)
    trace = run_front_half(method, tmp_path, provider, V2_CONFIG, lambda t: len(t.split()))
    triggers = [r['trigger'] for r in trace.retrieval_rounds]
    assert triggers == ['INITIAL', 'CHANGESET_NEEDS_EVIDENCE']
    planning_prompts = [r for r in provider.requests if r.prompt_version.startswith('changeset.')]
    assert 'shares the single Diagnosis extension allowance' not in planning_prompts[0].messages[0]['content']
    assert 'read-only locator' in planning_prompts[0].messages[0]['content']


def test_mixed_reply_is_corrected_not_accepted(tmp_path):
    method, template = front_fixture(tmp_path)
    provider = V2Provider(template)
    original = provider.complete_json
    sent = {'mixed': False}

    def complete(request):
        if request.prompt_version.startswith('diagnosis.v2.lookup.') and not sent['mixed']:
            sent['mixed'] = True
            provider.requests.append(request)
            return ModelResponse(json.dumps({'response_kind': 'LOOKUP', 'lookup': {'evidence_gaps': [
                {'summary': 'x'}]}, 'diagnosis': {'status': 'READY'}}), {})
        return original(request)
    provider.complete_json = complete
    trace = run_front_half(method, tmp_path, provider, V2_CONFIG, lambda t: len(t.split()))
    assert trace.status == 'READY', trace.errors
    assert 'DIAGNOSIS_FORMAT_RETRY' in stages(trace)
    rejected = next(c for c in trace.model_calls if c.get('validation_error'))
    assert rejected['stage'] == 'DIAGNOSIS' and 'INTERACTION_AMBIGUOUS' in rejected['validation_error']
    retry = next(r for r in provider.requests if r.metadata.get('format_retry_index') == 1)
    assert 'INTERACTION_AMBIGUOUS' in retry.messages[-1]['content']


def test_locator_receives_task_but_no_hidden_or_diagnosis_material(tmp_path):
    method, template = front_fixture(tmp_path)
    provider = V2Provider(template)
    run_front_half(method, tmp_path, provider, V2_CONFIG, lambda t: len(t.split()))
    opening = json.loads(provider.requests[0].messages[1]['content'])
    assert opening['task']['requirement_text'] == method['requirement_text']
    assert set(opening) == {'task', 'questions', 'already_delivered', 'action_schema'}
    assert 'evidence_catalog' not in provider.requests[0].messages[1]['content']
