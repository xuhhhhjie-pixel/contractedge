import hashlib
import json

from contractedge.execution import helper_context
from contractedge.source.parsers import parse_source_units
from contractedge.source.snapshot import compute_snapshot


def fixture(root):
    text = '''def current_parent(parent):
    if parent is None:
        raise LookupError("Parent context unavailable")
    return parent

def assign_scope(child, scope=None):
    if scope is None:
        scope = current_parent(None).get("scope")
    child["scope"] = scope

def attach(child, parent):
    assign_scope(child, parent.get("scope"))
'''
    (root/'consumer.py').write_text(text)
    snapshot = compute_snapshot(root)
    units = parse_source_units('consumer.py', text, snapshot.snapshot_id)
    seed = next(u for u in units if u['symbol'] == 'attach')
    excerpt = json.dumps(dict(resource={'kind':'Child','metadata':{'name':'item'}},
                              exception={'type':'LookupError','message':'Parent context unavailable'}))
    observation = dict(id='OBS-error', snapshot_id=snapshot.snapshot_id,
        kind='PUBLIC_OBSERVATION', locator={'path':'public/observation.json'},
        origin='Registered public operation fixture',
        excerpt=excerpt, content_digest=hashlib.sha256(excerpt.encode()).hexdigest(),
        adapter_id='public-object-adoption-operation-v2', structured_complete=True,
        producer_manifest_sha256='a'*64, source_file_sha256='b'*64,
        source_pointer='/observations/0', scenario='public-operation')
    return snapshot, seed, observation


def test_exact_public_exception_delivers_producer_and_two_local_callers(tmp_path):
    snapshot, seed, observation = fixture(tmp_path)
    units, audit = helper_context.source_exception_units(snapshot, [seed], [observation])
    assert {u['symbol'] for u in units} == {'current_parent', 'assign_scope', 'attach'}

    assert any('if parent is None:' in u['text'] and 'raise LookupError' in u['text'] for u in units)
    assert all(u['snapshot_id'] == snapshot.snapshot_id and u['required_for_gap_delivery'] for u in units)
    assert all(hashlib.sha256(u['text'].encode()).hexdigest() == u['content_digest'] for u in units)
    assert any(u.get('precondition_review_required') for u in units)
    assert audit and len(units) <= 6


def test_old_operation_or_unregistered_output_cannot_drive_source_pins(tmp_path):
    snapshot, seed, observation = fixture(tmp_path)
    stale = {**observation, 'snapshot_id':'old'}
    unknown = {**observation, 'structured_complete':False}
    assert helper_context.source_exception_units(snapshot, [seed], [stale])[0] == []
    assert helper_context.source_exception_units(snapshot, [seed], [unknown])[0] == []


def test_fresh_snapshot_reloads_changed_producer_and_rejects_stale_seed(tmp_path):
    old, seed, observation = fixture(tmp_path)
    p = tmp_path/'consumer.py'
    p.write_text(p.read_text().replace('if parent is None:', 'if parent is None or parent == {}:'))
    current = compute_snapshot(tmp_path)
    fresh_seed = next(u for u in parse_source_units('consumer.py', p.read_text(), current.snapshot_id)
                      if u['symbol'] == 'attach')
    units, _ = helper_context.source_exception_units(current, [fresh_seed],
                                                    [{**observation,'snapshot_id':current.snapshot_id}])
    assert any('parent == {}' in u['text'] for u in units)
    assert all(u['snapshot_id'] == current.snapshot_id for u in units)
    assert helper_context.source_exception_units(current, [seed],
                                                [{**observation,'snapshot_id':current.snapshot_id}])[0] == []
    p.write_text(p.read_text()+'\n# changed after snapshot\n')
    assert helper_context.source_exception_units(current, [fresh_seed],
                                                [{**observation,'snapshot_id':current.snapshot_id}])[0] == []


def test_comment_or_wrong_exception_type_is_not_a_producer(tmp_path):
    snapshot, seed, observation = fixture(tmp_path)
    p = tmp_path/'consumer.py'
    p.write_text(p.read_text().replace('raise LookupError("Parent context unavailable")',
                                     '# raise LookupError("Parent context unavailable")\n        raise TypeError("Other issue")'))
    current = compute_snapshot(tmp_path)
    fresh_seed = next(u for u in parse_source_units('consumer.py', p.read_text(), current.snapshot_id)
                      if u['symbol'] == 'attach')
    assert helper_context.source_exception_units(current, [fresh_seed],
                                                [{**observation,'snapshot_id':current.snapshot_id}])[0] == []


def test_same_named_method_cannot_replace_exact_decorated_top_level_producer(tmp_path):
    _, _, observation = fixture(tmp_path)
    path = tmp_path/'consumer.py'
    text = path.read_text().replace('def current_parent(parent):',
        'def decorate(fn):\n    return fn\n\n@decorate\ndef current_parent(parent):')
    path.write_text(text+'\nclass Other:\n    def current_parent(self):\n        return "unrelated method"\n')
    snapshot = compute_snapshot(tmp_path)
    seed = next(u for u in parse_source_units('consumer.py', path.read_text(), snapshot.snapshot_id)
                if u['symbol'] == 'attach')
    units, _ = helper_context.source_exception_units(snapshot, [seed],
        [{**observation,'snapshot_id':snapshot.snapshot_id}])
    producer = next(u for u in units if u['symbol'] == 'current_parent')
    assert 'raise LookupError("Parent context unavailable")' in producer['text']
    assert producer['text'].startswith('@decorate\n')
    assert 'unrelated method' not in producer['text']


def test_record_limit_is_audited_without_unbounded_chain(tmp_path):
    snapshot, seed, observation = fixture(tmp_path)
    units, audit = helper_context.source_exception_units(snapshot, [seed], [observation], max_records=1)
    assert len(units) == 1 and units[0]['symbol'] == 'current_parent'
    assert any(r['result'] == 'EXCEPTION_SOURCE_ROLE_LIMIT' for r in audit)


def test_selected_operation_chain_precedes_many_unrelated_callers(tmp_path):
    _, _, observation = fixture(tmp_path)
    path = tmp_path/'consumer.py'
    prefix = ''.join(f'def other_{i}(parent):\n    return current_parent(parent)\n\n' for i in range(8))
    path.write_text(prefix + path.read_text())
    snapshot = compute_snapshot(tmp_path)
    seed = next(u for u in parse_source_units('consumer.py', path.read_text(), snapshot.snapshot_id)
                if u['symbol'] == 'attach')
    units, _ = helper_context.source_exception_units(snapshot, [seed],
        [{**observation,'snapshot_id':snapshot.snapshot_id}], max_records=3)
    assert {u['symbol'] for u in units} == {'current_parent', 'assign_scope', 'attach'}
    units, _ = helper_context.source_exception_units(snapshot, [seed],
        [{**observation,'snapshot_id':snapshot.snapshot_id}], max_records=6)
    assert {u['symbol'] for u in units} == {'current_parent', 'assign_scope', 'attach'}


def test_actual_initial_request_pins_exception_chain_under_unchanged_top12(tmp_path):
    import pytest
    from contractedge.contracts.validation import canonical_digest
    from contractedge.orchestration.runner import run_front_half
    from tests.test_contracts import valid_method_input

    _, _, observation = fixture(tmp_path)
    path = tmp_path/'consumer.py'
    with path.open('a') as stream:
        for index in range(24):
            stream.write(f'\ndef decoy_{index}():\n    return "enabled render selected ServiceAccount"\n')
    snapshot = compute_snapshot(tmp_path)
    observation['snapshot_id'] = snapshot.snapshot_id
    method = valid_method_input()
    method.update(initial_snapshot_id=snapshot.snapshot_id,
                  scope_hints=[dict(kind='path',value='consumer.py',raw_text='consumer.py',origin_ref='public')])
    method['input_digest'] = canonical_digest({k:v for k,v in method.items() if k != 'input_digest'})

    class Captured(BaseException):
        pass

    class Provider:
        request = None
        def complete_json(self, request):
            self.request = request
            raise Captured()

    provider = Provider()
    with pytest.raises(Captured):
        run_front_half(method, tmp_path, provider,
            dict(model={'model':'fixture'},top_n=12,max_context_tokens=120000,
                 max_total_tokens=2000000,max_output_tokens=16384,gap_role_delivery_enabled=True),
            len, public_observations=(observation,))
    public = json.loads(provider.request.messages[1]['content'])
    sources = [r for r in public['evidence_catalog'] if r['kind'] == 'SOURCE_SPAN']
    assert any('raise LookupError("Parent context unavailable")' in r['excerpt'] for r in sources)
    assert any('def attach(' in r['excerpt'] for r in sources)
    assert len(public['candidate_set']['selected_ids']) <= 12
    assert all(r['snapshot_id'] == snapshot.snapshot_id for r in sources)
