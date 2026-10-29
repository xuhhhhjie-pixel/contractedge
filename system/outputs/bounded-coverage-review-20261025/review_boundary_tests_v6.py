"""Regression fixtures for confirmed review findings; no live source edits."""
import pytest
from contractedge.source.snapshot import compute_snapshot
from contractedge.source.units import extract_units, extract_gap_extension_units
from contractedge.source.parsers import parse_source_units
from contractedge.retrieval.gap_delivery import locate_gap_roles


@pytest.mark.parametrize('limit', [1, 40])
def test_same_file_precise_query_keeps_only_exact_token_and_case(tmp_path, limit):
    (tmp_path / 'docs.md').write_text('Queue processing background.\n')
    (tmp_path / 'controller.go').write_text('''package controller
func WrongCase() { consume(obj.nextQueueTokenName) }
func Substring() { consume(obj.PrevNextQueueTokenName) }
func Exact() { consume(obj.NextQueueTokenName) }
''')
    snap = compute_snapshot(tmp_path)
    seeds = extract_units(snap, {'seed_paths': ['docs.md']})
    units, _ = extract_gap_extension_units(snap, ['docs.md'], seeds,
        [dict(id='G1', locator_hint={'search_term': 'NextQueueTokenName'})], max_units=limit)
    assert [u.get('symbol') for u in units] == ['Exact']


@pytest.mark.parametrize('bound', ['files', 'bytes'])
def test_stale_field_contract_spends_budget_before_next_package(tmp_path, bound):
    (tmp_path / 'go.mod').write_text('module example.org/app\n')
    (tmp_path / 'worker').mkdir(); (tmp_path / 'one').mkdir(); (tmp_path / 'two').mkdir()
    source = '''package worker
import (
 p1 "example.org/app/one"
 p2 "example.org/app/two"
)
type Worker struct {
 a p1.Queue
 b p2.Queue
}
func (w *Worker) Run() {
 w.a.Publish()
 w.b.Publish()
}
'''
    (tmp_path / 'worker/work.go').write_text(source)
    contract = 'package api\ntype Queue interface {\n Publish()\n}\n'
    first = tmp_path / 'one/api.go'; second = tmp_path / 'two/api.go'
    first.write_text(contract); second.write_text(contract)
    snap = compute_snapshot(tmp_path)
    first.write_text(contract + '// stale\n')
    gap = dict(id='G1', locator_hint={'path': 'worker/work.go', 'symbol': 'Worker.Run',
                                    'search_term': 'a.Publish b.Publish'})
    limits = dict(seed_declarations=parse_source_units('worker/work.go', source, snap.snapshot_id))
    if bound == 'files':
        limits['max_new_files'] = 2
    else:
        limits['max_bytes'] = len(source.encode()) + first.stat().st_size
    units, audit = locate_gap_roles(snap, [gap], limits)
    assert not any(u['path'] == 'two/api.go' for u in units)
    assert audit['inspected_files'] == 2
    assert audit['inspected_bytes'] == len(source.encode()) + first.stat().st_size
