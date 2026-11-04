"""Read-only context for adding a setting to an existing production path."""
from pathlib import Path

import pytest

from contractedge.source.parsers import parse_source_units
from contractedge.source.snapshot import compute_snapshot
from contractedge.source.units import extract_gap_extension_units


def fixture(tmp_path, *, caller=None, constructor=None):
    files = {
        'go.mod': 'module example.test/service\n\ngo 1.24\n',
        'core/engine.go': 'package core\ntype Engine struct{}\nfunc (e *Engine) Apply() string { return "old" }\n',
        'core/pair.go': 'package core\ntype Pair struct{}\ntype Pairs []*Pair\n' + (constructor or
            'func (p *Pair) Combine() string { e := &Engine{}; return e.Apply() }\n'),
        'core/fuse.go': 'package core\nfunc Fuse() string { pairs := Pairs{}; return MergePairs(pairs) }\nfunc MergePairs(pairs Pairs) string { return "old" }\n',
        'app/update.go': caller or 'package app\nimport engine "example.test/service/core"\nfunc Update() string { return engine.Fuse() }\n',
    }
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    snapshot = compute_snapshot(tmp_path)
    seeds = parse_source_units('core/engine.go', files['core/engine.go'], snapshot.snapshot_id)
    gaps = [dict(id='G1', summary='The option is not connected to the production processor.',
                 locator_hint=dict(path='core/engine.go', search_term='RetainEmptyValues'))]
    return snapshot, seeds, gaps


def discover(snapshot, seeds, gaps, **limits):
    return extract_gap_extension_units(snapshot, ['core/engine.go'], seeds, gaps, **limits)


def test_missing_new_field_still_locates_existing_construction_and_package_callers(tmp_path):
    snapshot, seeds, gaps = fixture(tmp_path)
    units, audit = discover(snapshot, seeds, gaps)
    bodies = {(u['path'], u.get('symbol')) for u in units}
    assert {('core/pair.go', 'Combine'), ('core/fuse.go', 'Fuse'),
            ('core/fuse.go', 'MergePairs'), ('app/update.go', 'Update')} <= bodies
    assert not any(u.get('required_for_gap_delivery') for u in units)
    assert audit['inspected_files'] <= 2000


def test_gap_construction_ignores_declaration_inside_comment_or_string(tmp_path):
    snapshot, seeds, gaps = fixture(tmp_path, constructor='/*\nfunc Decoy() { _ = &Engine{} }\n*/\n'
        + 'var quoted = `\nfunc Bogus() { _ = &Engine{} }\n`\n')
    units, audit = discover(snapshot, seeds, gaps)
    assert not any(row['basis'] == 'EXACT_SAME_PACKAGE_TYPE_CONSTRUCTION'
                   for row in audit['construction_context'])


@pytest.mark.parametrize('caller', [
    'package app\nimport engine "other.test/core"\nfunc Update() string { return engine.Fuse() }\n',
    'package app\nimport engine "example.test/service/core"\nfunc Update(engine unknown) string { return engine.Fuse() }\n',
    'package app\nfunc Update(engine unknown) string { return engine.Fuse() }\n',
    'package app\nimport engine "example.test/service/core"\nfunc Update() string { return "engine.Fuse()" }\n',
    'package app\nimport engine "example.test/service/core"\nfunc Update() string { engine, err := other(); return engine.Fuse() }\n',
    'package app\nimport engine "example.test/service/core"\nfunc Update() string { var ( engine OtherType ); return engine.Fuse() }\n',
    'package app\nimport engine "example.test/service/core"\nfunc Update() string { return obj . engine.Fuse() }\n',
])
def test_unrelated_or_unbound_receiver_cannot_become_local_package_caller(tmp_path, caller):
    snapshot, seeds, gaps = fixture(tmp_path, caller=caller)
    units, _ = discover(snapshot, seeds, gaps)
    assert not any(u['path'] == 'app/update.go' for u in units)


@pytest.mark.parametrize('constructor', [
    'func (p *Pair) Combine() string { /* _ = &Engine{} */ return "old" }\n',
    'func (p *Pair) Combine() string { _ = "&Engine{}"; return "old" }\n',
    'func (p *Pair) Combine() string { _ = &other.Engine{}; return "old" }\n',
])
def test_only_real_same_package_construction_can_expand_missing_field_gap(tmp_path, constructor):
    snapshot, seeds, gaps = fixture(tmp_path, constructor=constructor)
    units, _ = discover(snapshot, seeds, gaps)
    assert not any(u['path'] == 'core/pair.go' for u in units)


def test_context_discovery_respects_existing_file_and_byte_limits(tmp_path):
    snapshot, seeds, gaps = fixture(tmp_path)
    units, audit = discover(snapshot, seeds, gaps, max_files=1, max_search_bytes=128)
    assert audit['inspected_files'] <= 1
    assert audit['inspected_bytes'] <= 128
    assert not any(u['path'] == 'app/update.go' for u in units)


@pytest.mark.parametrize('submodule,expected', [('api', True), ('core', False)])
def test_only_replacement_of_queried_import_subtree_blocks_local_ownership(tmp_path, submodule, expected):
    snapshot, seeds, gaps = fixture(tmp_path)
    with (tmp_path / 'go.mod').open('a') as stream:
        stream.write(f'replace example.test/service/{submodule} => ./replacement\n')
    snapshot = compute_snapshot(tmp_path)
    seeds = parse_source_units('core/engine.go', (tmp_path / 'core/engine.go').read_text(), snapshot.snapshot_id)
    units, _ = discover(snapshot, seeds, gaps)
    assert any(u['path'] == 'app/update.go' for u in units) is expected


def test_unrelated_free_entry_does_not_win_by_filename_or_declaration_order(tmp_path):
    snapshot, seeds, gaps = fixture(tmp_path)
    with (tmp_path / 'core/fuse.go').open('a') as stream:
        stream.write('func Unrelated() string { return "decoy" }\n')
    (tmp_path / 'app/aaa.go').write_text('package app\nimport engine "example.test/service/core"\nfunc Earlier() string { return engine.Unrelated() }\n')
    snapshot = compute_snapshot(tmp_path)
    seeds = parse_source_units('core/engine.go', (tmp_path / 'core/engine.go').read_text(), snapshot.snapshot_id)
    units, _ = discover(snapshot, seeds, gaps)
    assert any(u.get('symbol') == 'Update' for u in units)
    assert not any(u.get('symbol') in {'Earlier', 'Unrelated'} for u in units)


def refresh(tmp_path):
    snapshot = compute_snapshot(tmp_path)
    seeds = parse_source_units('core/engine.go', (tmp_path / 'core/engine.go').read_text(), snapshot.snapshot_id)
    return snapshot, seeds


def test_same_package_name_in_another_directory_does_not_supply_construction(tmp_path):
    snapshot, seeds, gaps = fixture(tmp_path, constructor='func (p *Pair) Combine() string { return "old" }\n')
    (tmp_path / 'elsewhere').mkdir()
    (tmp_path / 'elsewhere/other.go').write_text('package core\nfunc Build() { _ = &Engine{} }\n')
    snapshot, seeds = refresh(tmp_path)
    _, audit = discover(snapshot, seeds, gaps)
    assert not audit['construction_context']


@pytest.mark.parametrize('change', ['content', 'seed-snapshot', 'seed-digest', 'build-tag', 'mixed-package'])
def test_unverified_or_ambiguous_owner_cannot_expand_context(tmp_path, change):
    snapshot, seeds, gaps = fixture(tmp_path)
    if change == 'content':
        (tmp_path / 'core/engine.go').write_text('package core\ntype Different struct{}\n')
    elif change.startswith('seed-'):
        for seed in seeds:
            seed['snapshot_id' if change == 'seed-snapshot' else 'content_digest'] = 'wrong'
    else:
        (tmp_path / 'core/ambiguous.go').write_text('//go:build optional\n\npackage core\n' if change == 'build-tag'
                                                   else 'package other\n')
        snapshot, seeds = refresh(tmp_path)
    _, audit = discover(snapshot, seeds, gaps)
    assert not audit['construction_context']


def test_reverse_free_call_expansion_stops_after_two_layers(tmp_path):
    snapshot, seeds, gaps = fixture(tmp_path, caller='package app\nimport engine "example.test/service/core"\n'
        + 'func Near() string { return engine.Second() }\nfunc TooFar() string { return engine.Third() }\n')
    with (tmp_path / 'core/fuse.go').open('a') as stream:
        # Declaration order would propagate all three in a single mutable pass.
        stream.write('func First() string { return Fuse() }\nfunc Second() string { return First() }\n'
                     + 'func Third() string { return Second() }\n')
    snapshot, seeds = refresh(tmp_path)
    _, audit = discover(snapshot, seeds, gaps)
    paths = {row['unit_id'] for row in audit['construction_context']}
    parsed = parse_source_units('app/update.go', (tmp_path / 'app/update.go').read_text(), snapshot.snapshot_id)
    assert next(u['id'] for u in parsed if u['symbol'] == 'Near') in paths
    assert next(u['id'] for u in parsed if u['symbol'] == 'TooFar') not in paths


def test_parameter_named_like_related_type_is_not_a_type_relationship(tmp_path):
    snapshot, seeds, gaps = fixture(tmp_path, caller='package app\nimport engine "example.test/service/core"\n'
        + 'func Update() string { return engine.Unrelated(1) }\n')
    with (tmp_path / 'core/fuse.go').open('a') as stream:
        stream.write('func Unrelated(Pairs int) string { return "decoy" }\n')
    snapshot, seeds = refresh(tmp_path)
    _, audit = discover(snapshot, seeds, gaps)
    assert not any(row['path'].startswith('app/') for row in audit['construction_context'])


def test_local_alias_cannot_create_package_type_relationship(tmp_path):
    snapshot, seeds, gaps = fixture(tmp_path, caller='package app\nimport engine "example.test/service/core"\n'
        + 'func Update() string { return engine.Unrelated() }\n')
    with (tmp_path / 'core/fuse.go').open('a') as stream:
        stream.write('func LocalOnly() {\n type Decoy []*Pair\n}\n'
                     + 'func Unrelated() string { _ = Decoy{}; return "decoy" }\n')
    snapshot, seeds = refresh(tmp_path)
    _, audit = discover(snapshot, seeds, gaps)
    assert not any(row['path'].startswith('app/') for row in audit['construction_context'])


@pytest.mark.parametrize('explicit,expected', [(False, False), (True, True)])
def test_import_path_tail_cannot_guess_different_verified_package_name(tmp_path, explicit, expected):
    alias = 'core ' if explicit else ''
    snapshot, seeds, gaps = fixture(tmp_path, caller='package app\nimport ' + alias
        + '"example.test/service/core"\nfunc Update() string { return core.Fuse() }\n')
    for path in (tmp_path / 'core').glob('*.go'):
        path.write_text(path.read_text().replace('package core', 'package actual'))
    snapshot, seeds = refresh(tmp_path)
    _, audit = discover(snapshot, seeds, gaps)
    assert any(row['path'].startswith('app/') for row in audit['construction_context']) is expected


def test_multiple_callers_reuse_bounded_context_cap(tmp_path):
    snapshot, seeds, gaps = fixture(tmp_path, caller='package app\nimport engine "example.test/service/core"\n'
        + ''.join(f'func Caller{i}() string {{ return engine.Fuse() }}\n' for i in range(8)))
    _, audit = discover(snapshot, seeds, gaps)
    assert len(audit['construction_context']) <= 6
    assert sum(row['basis'] == 'EXACT_LOCAL_MODULE_IMPORT_CALL' for row in audit['construction_context']) == 2


def test_discovered_chain_survives_selection_and_actual_prepared_request(tmp_path):
    import json
    from types import SimpleNamespace
    from contractedge.contracts.validation import parse_method_input
    from contractedge.diagnosis.evidence import source_context_new, source_context_for_candidates
    from contractedge.diagnosis.prompt import build_diagnosis_request
    from contractedge.retrieval.candidates import build_candidate_set, extend_once
    from contractedge.model.provider import OpenAICompatibleProvider
    from contractedge.model.admission import quote_request
    from tests.test_contracts import valid_method_input, valid_diagnosis
    snapshot, seeds, gaps = fixture(tmp_path)
    units, _ = discover(snapshot, seeds, gaps)
    method = parse_method_input(valid_method_input())
    initial = build_candidate_set('', None, seeds, [], top_n=12, max_context_tokens=100000, token_counter=len)
    active = source_context_for_candidates(source_context_new(dict(run_id='run', case_id=method.case_id,
        input_digest=method.input_digest, front_id='front')), method, initial, snapshot.snapshot_id)
    proposed = extend_once(initial, gaps, units, source_context=active, max_context_tokens=100000, token_counter=len)
    draft = source_context_for_candidates(active, method, proposed, snapshot.snapshot_id)
    context = SimpleNamespace(run_id='run', case_id=method.case_id, snapshot_id=snapshot.snapshot_id,
        input_digest=method.input_digest, model_config={'model':'fixture'}, budget={'max_output_tokens':1024},
        retrieval_config={'source_context':draft, 'necessary_source_support':[]})
    request = build_diagnosis_request(method, valid_diagnosis()['requirement_spec'], proposed, context)
    provider = OpenAICompatibleProvider('https://example.invalid', 'NEVER_DISPATCH_TEST')
    quoted = quote_request(provider, request, len)
    body = json.loads(json.loads(quoted.prepared.body)['messages'][1]['content'])
    records = {row['id']: row for row in body['evidence_catalog']}
    wanted = [u for u in units if u.get('symbol') in {'Combine', 'Fuse', 'MergePairs', 'Update'}]
    assert len(wanted) == 4
    for unit in wanted:
        assert records['SRC-' + unit['id']]['excerpt'] == unit['text']
        assert records['SRC-' + unit['id']]['content_digest'] == unit['content_digest']
