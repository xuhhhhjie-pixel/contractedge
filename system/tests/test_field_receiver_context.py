"""Explicit constructor arguments locate concrete field methods within old caps."""
import pytest

from contractedge.execution.helper_context import local_go_helper_support, source_lifecycle_units
from contractedge.source.parsers import parse_source_units
from contractedge.source.snapshot import compute_snapshot


def setup(root, *, name="queue", alias="storage"):
    (root / 'go.mod').write_text('module example.org/ctx\n')
    (root / 'engine').mkdir(); (root / 'store').mkdir()
    (root / 'engine/engine.go').write_text('''package engine
type Source interface { Replay() <-chan int }
type Worker struct { source Source }
func Create(input Source) *Worker { return &Worker{source: input} }
func (w *Worker) Start() { w.loop() }
func (w *Worker) loop() { <-w.source.Replay() }
''')
    (root / 'store/store.go').write_text('''package store
type Store struct {}
type Other struct {}
func Make() (*Store, error) { return &Store{}, nil }
func (s *Store) Replay() <-chan int { bufferedSnapshot(); return nil }
func (s *Other) Replay() <-chan int { wrongImplementation(); return nil }
''')
    text = ('package main\nimport e "example.org/ctx/engine"\n'
            f'import {alias} "example.org/ctx/store"\nfunc run() {{\n'
            f' {name}, err := {alias}.Make()\n _ = err\n worker := e.Create({name})\n go worker.Start()\n}}\n')
    (root / 'main.go').write_text(text)
    return [dict(path='main.go', content=text)], [dict(target=dict(path='main.go', locator=dict(start_line=4,end_line=9)))]


@pytest.mark.parametrize('name,alias', [('queue','storage'), ('pending','cache')])
def test_tuple_factory_and_renamed_parameter_locate_exact_field_implementation(tmp_path,name,alias):
    targets,ops=setup(tmp_path,name=name,alias=alias)
    records,audit=local_go_helper_support(compute_snapshot(tmp_path),targets,ops,max_scan_files=2)
    joined='\n'.join(r['excerpt'] for r in records)
    assert 'bufferedSnapshot()' in joined
    assert 'wrongImplementation()' not in joined
    assert any(r.get('basis')=='LOCAL_CONSTRUCTOR_FIELD_RECEIVER' for r in audit)
    assert len(records)<=8


def test_field_dependency_enters_existing_preplan_source_slots(tmp_path):
    setup(tmp_path)
    snapshot=compute_snapshot(tmp_path)
    seeds=parse_source_units('main.go',(tmp_path/'main.go').read_text(),snapshot.snapshot_id)
    units,audit=source_lifecycle_units(snapshot,seeds,max_files=2,max_records=6)
    assert any('bufferedSnapshot()' in u['text'] for u in units)
    assert len(units)<=6
    assert not any(u.get('verified_causal_edge') for u in units)


@pytest.mark.parametrize('mutation',['reassigned','indirect_field','external_factory','comment_only'])
def test_ambiguous_or_unavailable_factory_cannot_supply_a_concrete_receiver(tmp_path,mutation):
    targets,ops=setup(tmp_path)
    if mutation=='reassigned':
        targets[0]['content']=targets[0]['content'].replace(' worker :=',' queue = otherQueue\n worker :=')
    elif mutation=='external_factory':
        targets[0]['content']=targets[0]['content'].replace('example.org/ctx/store','external.org/store')
    elif mutation=='comment_only':
        targets[0]['content']=targets[0]['content'].replace(' queue, err := storage.Make()',' // queue, err := storage.Make()')
    else:
        p=tmp_path/'engine/engine.go';p.write_text(p.read_text().replace('source: input','source: wrap(input)'))
    (tmp_path/'main.go').write_text(targets[0]['content'])
    records,_=local_go_helper_support(compute_snapshot(tmp_path),targets,ops)
    assert not any('bufferedSnapshot()' in r['excerpt'] for r in records)


@pytest.mark.parametrize('limit',[dict(max_scan_files=1),dict(max_scan_bytes=1),dict(max_bytes=20)])
def test_field_location_obeys_existing_shared_budgets(tmp_path,limit):
    targets,ops=setup(tmp_path)
    records,_=local_go_helper_support(compute_snapshot(tmp_path),targets,ops,**limit)
    assert not any('bufferedSnapshot()' in r['excerpt'] for r in records)
    assert sum(len(r['excerpt'].encode()) for r in records)<=limit.get('max_bytes',24000)


def test_changed_factory_source_is_not_used_from_old_snapshot(tmp_path):
    targets,ops=setup(tmp_path);snapshot=compute_snapshot(tmp_path)
    p=tmp_path/'store/store.go';p.write_text(p.read_text().replace('bufferedSnapshot','changedBody'))
    records,audit=local_go_helper_support(snapshot,targets,ops)
    assert not any('changedBody' in r['excerpt'] or 'bufferedSnapshot' in r['excerpt'] for r in records)
    assert any(r['result']=='SOURCE_CHANGED_AFTER_SNAPSHOT' for r in audit)


def test_unrelated_package_siblings_cannot_spend_the_dependency_file_slot(tmp_path):
    targets,ops=setup(tmp_path)
    (tmp_path/'engine/z_unrelated.go').write_text('package engine\nfunc Unrelated() {}\n')
    records,audit=local_go_helper_support(compute_snapshot(tmp_path),targets,ops,max_scan_files=2)
    assert any('bufferedSnapshot()' in r['excerpt'] for r in records)
    assert next(r for r in audit if r['result']=='HELPER_SCAN_TOTALS')['indexed_files']==2


@pytest.mark.parametrize('mutation',['owner_reassigned','field_overwritten','factory_after_use','shadow_parameter'])
def test_local_value_changes_cannot_be_misread_as_a_unique_field_binding(tmp_path,mutation):
    targets,ops=setup(tmp_path)
    if mutation in {'owner_reassigned','field_overwritten'}:
        p=tmp_path/'engine/engine.go'
        assignment='owner = anotherOwner' if mutation=='owner_reassigned' else 'owner.source = anotherSource'
        p.write_text(p.read_text().replace('return &Worker{source: input}',
            'owner := &Worker{source: input}; '+assignment+'; return owner'))
    elif mutation=='factory_after_use':
        text=targets[0]['content'].replace(' queue, err := storage.Make()\n _ = err\n worker := e.Create(queue)',
            ' var queue e.Source\n worker := e.Create(queue)\n queue, err = storage.Make()\n _ = err')
        targets[0]['content']=text
    else:
        text=targets[0]['content'].replace(' worker := e.Create(queue)\n go worker.Start()',
            ' launch := func(queue e.Source) {\n worker := e.Create(queue)\n go worker.Start()\n }\n _ = launch')
        targets[0]['content']=text
    (tmp_path/'main.go').write_text(targets[0]['content'])
    records,_=local_go_helper_support(compute_snapshot(tmp_path),targets,ops)
    assert not any('bufferedSnapshot()' in r['excerpt'] for r in records)


@pytest.mark.parametrize('extend', [False, True])
def test_field_context_crosses_diagnosis_plan_ready_and_execution_without_edit_grant(tmp_path,extend):
    from dataclasses import replace
    import hashlib,json
    from contractedge.changeset.topomen import translate_for_topomen
    from contractedge.contracts.validation import canonical_digest,parse_method_input
    from contractedge.execution.topomen import build_execution_request,PlanRejected
    from contractedge.model.provider import ModelResponse
    from contractedge.orchestration.runner import run_front_half
    from contractedge.orchestration.repair import validate_ready_trace
    from tests.test_planning_evidence_extension import front_fixture,PlanningGapProvider
    from tests.test_orchestration import PipelineFixtureProvider
    value,template=front_fixture(tmp_path);setup(tmp_path)
    value['scope_hints'].append(dict(kind='path',value='main.go',origin_ref='public.scope',raw_text='main.go'))
    value['initial_snapshot_id']=compute_snapshot(tmp_path).snapshot_id
    value['public_evidence'][0]['snapshot_id']=value['initial_snapshot_id']
    value.pop('input_digest');value['input_digest']=canonical_digest(value)
    class Provider(PlanningGapProvider if extend else PipelineFixtureProvider):
        def complete_json(self,request):
            body=json.loads(request.messages[1]['content'])
            if str(request.prompt_version).startswith('diagnosis.'):
                assert any('bufferedSnapshot()' in e.get('excerpt','') for e in body['evidence_catalog'])
                body['evidence_catalog'].sort(key=lambda e:e.get('locator',{}).get('path')!='templates/serviceaccount.yaml')
                request=replace(request,messages=(request.messages[0],{**request.messages[1],'content':json.dumps(body)}))
            response=super().complete_json(request)
            if str(request.prompt_version).startswith('changeset.'):
                assert any('bufferedSnapshot()' in r['excerpt'] for r in body['read_only_source_support'])
                plan=json.loads(response.content)
                plan['precondition_review']=[dict(source_evidence_ref=r['source_evidence_ref'],
                    condition='Worker is an unrelated read-only control in this rendering fixture.',
                    disposition='NOT_APPLICABLE',verification_refs=[],open_obligation_refs=[],
                    establishment=dict(mode='NOT_APPLICABLE',source_quote='',change_refs=[],mechanism='No worker state is changed by the rendering edit.'),
                    rationale='The fixture changes only ServiceAccount rendering, not worker state.')
                    for r in body['source_prerequisites']]
                return ModelResponse(json.dumps(plan),{})
            return response
    provider=Provider();provider._snapshot_digests={'templates/serviceaccount.yaml':hashlib.sha256(template.read_bytes()).hexdigest()}
    trace=run_front_half(value,tmp_path,provider,dict(model={'model':'fixture'},gap_role_delivery_enabled=True,
        max_context_tokens=50000,max_output_tokens=1024,max_total_tokens=200000),lambda t:len(t.split())).to_wire()
    assert trace['status']=='READY',trace['errors']
    assert trace['candidate_set']['extension_used'] == extend
    _,context=validate_ready_trace(trace,parse_method_input(value),tmp_path)
    support=context.retrieval_config['necessary_source_support']
    assert any('bufferedSnapshot()' in r['excerpt'] for r in support)
    plan=translate_for_topomen(trace['changeset'],'contractedge.topomen_plan.v1',diagnosis=trace['diagnosis'],source_support=support)
    config=dict(model='fixture',max_context_tokens=50000,execution_max_output_tokens=1024)
    request=build_execution_request(plan,tmp_path,{'allowed_paths':['templates/**']},lambda t:len(t.split()),config,requirement=value['requirement_text'])
    body=json.loads(request.messages[1]['content'])
    assert any('bufferedSnapshot()' in r['excerpt'] for r in body['read_only_source_support'])
    assert {r['path'] for r in body['current_target_files']}=={'templates/serviceaccount.yaml'}
    path=tmp_path/'store/store.go';path.write_text(path.read_text().replace('bufferedSnapshot','changedSource'))
    with pytest.raises(PlanRejected):
        build_execution_request(plan,tmp_path,{'allowed_paths':['templates/**']},lambda t:len(t.split()),config,requirement=value['requirement_text'])


def test_legacy_async_lookup_keeps_grouped_parameters_without_guessing_field_positions(tmp_path):
    targets,ops=setup(tmp_path)
    p=tmp_path/'engine/engine.go'
    p.write_text(p.read_text().replace('Create(input Source)', 'Create(input, fallback Source)'))
    targets[0]['content']=targets[0]['content'].replace('e.Create(queue)', 'e.Create(queue, queue)')
    (tmp_path/'main.go').write_text(targets[0]['content'])
    records,_=local_go_helper_support(compute_snapshot(tmp_path),targets,ops)
    assert any('w.loop()' in r['excerpt'] for r in records)
    assert not any('bufferedSnapshot()' in r['excerpt'] for r in records)
