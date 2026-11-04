"""Public-handler hints must retain source kind and qualified-name boundaries."""
import pytest

from contractedge.retrieval.gap_delivery import locate_gap_roles
from contractedge.source.parsers import parse_source_units
from contractedge.source.snapshot import compute_snapshot


def locate(tmp_path, source, summary, *, query='reloadConfig'):
    (tmp_path / 'api').mkdir()
    (tmp_path / 'go.mod').write_text('module example.org/service\n')
    (tmp_path / 'main.go').write_text('package main\nfunc reloadConfig() {}\n')
    (tmp_path / 'api/handler.go').write_text(source)
    snapshot = compute_snapshot(tmp_path)
    seeds = parse_source_units('api/handler.go', source, snapshot.snapshot_id)
    units, audit = locate_gap_roles(snapshot, [dict(id='G1', summary=summary,
        locator_hint={'path': 'main.go', 'search_term': query})],
        dict(max_new_files=4, seed_declarations=seeds))
    named = [unit for unit in units if any(role.startswith('NAMED_PUBLIC_ENDPOINT:')
                                         for role in unit.get('gap_role_keys', []))]
    return named, audit


def test_type_is_not_promoted_to_public_handler(tmp_path):
    named, _ = locate(tmp_path, 'package api\ntype Options struct { Timeout int }\n',
                      'Find options used by the public API')
    assert named == []


@pytest.mark.parametrize('summary,query', [
    ('Find processor.Options construction', 'reloadConfig'),
    ('Find the configuration carrier', 'processor.Options'),
    ('Find processor . Options construction', 'reloadConfig'),
])
def test_qualified_reference_does_not_become_unrelated_api_function(tmp_path, summary, query):
    named, _ = locate(tmp_path, 'package api\nfunc Options() {}\n', summary, query=query)
    assert named == []


@pytest.mark.parametrize('wrapper', ['/*\n%s\n*/', 'var archived = `\n%s\n`'])
def test_archived_function_text_is_not_a_live_handler(tmp_path, wrapper):
    named, _ = locate(tmp_path, 'package api\n' + wrapper % 'func getGroupsHandler() {}' + '\n',
                      'Find the groups public API provider')
    assert named == []


@pytest.mark.parametrize('declaration', [
    'func getGroupsHandler() { respond() }',
    'func (a *API) getGroupsHandler() { respond() }',
])
def test_unqualified_handler_concept_still_locates_real_function(tmp_path, declaration):
    named, audit = locate(tmp_path, 'package api\n' + declaration + '\n',
                         'Find reload path affecting the groups API provider')
    assert [unit['symbol'] for unit in named] == ['getGroupsHandler']
    assert audit['inspected_files'] <= 4


def test_unqualified_concept_survives_other_qualified_references(tmp_path):
    named, _ = locate(tmp_path, 'package api\nfunc getGroupsHandler() {}\n',
                      'Find groups API fed by storage.Reader')
    assert [unit['symbol'] for unit in named] == ['getGroupsHandler']
