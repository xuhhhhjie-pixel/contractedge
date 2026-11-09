"""Frozen v1 interfaces: graph JSON, command exit codes, ce-submit --check structure."""
import json

import pytest

from conftest import run_tool
from contractedge.graph_repair.config import SWITCHES, VARIANTS, Variant
from contractedge.graph_repair.graph.schema import SCHEMA, empty, format_problems


def test_empty_graph_is_valid_v1():
    graph = empty()
    assert graph['schema'] == SCHEMA == 'contractedge.contract_graph.v1'
    assert format_problems(graph) == []


@pytest.mark.parametrize('mutate,needle', [
    (lambda g: g.update(schema='x'), 'schema'),
    (lambda g: g['nodes'].append({'id': 'N1', 'role': 'NOPE', 'path': 'a', 'span': [1, 1]}), 'role'),
    (lambda g: g['edges'].append({'id': 'E1', 'type': 'PROPAGATION', 'from': 'N9', 'to': 'N8', 'status': 'VIOLATED'}), 'not a node'),
])
def test_format_problems_are_reported(mutate, needle):
    graph = empty()
    mutate(graph)
    assert any(needle in p for p in format_problems(graph))


def test_variant_table_matches_ablation_design():
    assert VARIANTS['full'].any_mechanism and all(getattr(VARIANTS['full'], s) for s in SWITCHES)
    assert not VARIANTS['live-equivalent'].any_mechanism and VARIANTS['live-equivalent'].prompt == 'live'
    assert not VARIANTS['no-probes'].probes and VARIANTS['no-probes'].closure
    assert not VARIANTS['no-closure'].closure and VARIANTS['no-closure'].probes
    assert not VARIANTS['no-stop-rule'].stop_rule and VARIANTS['no-stop-rule'].graph
    f = VARIANTS['submit-only']
    assert f.prompt == 'live' and f.stop_rule and f.checkpoint and not (f.graph or f.linkers or f.closure or f.probes)
    for v in VARIANTS.values():
        assert Variant.from_json(v.to_json()) == v
    with pytest.raises(ValueError):
        Variant.from_json({'name': 'x', 'bogus': 1})


def test_submit_check_structure(workspace):
    done = run_tool(workspace, 'ce-submit', '--check', '--json')
    line = [l for l in done.stdout.splitlines() if l.startswith('CE_SUBMIT_CHECK_JSON=')][0]
    value = json.loads(line.split('=', 1)[1])
    assert set(value) == {'schema', 'tree', 'passed', 'unmet', 'checks', 'notes', 'probes_disabled'}
    assert value['schema'] == 'contractedge.submit_check.v1'
    assert done.returncode == 1 and value['passed'] is False
    for item in value['unmet']:
        assert set(item) == {'id', 'kind', 'detail'}
        assert item['kind'] in {'public_check', 'violate_probe', 'preserve_probe', 'closure', 'policy'}


def test_disabled_command_exits_3(workspace):
    workspace.configure('no-probes')
    done = run_tool(workspace, 'ce-probe', 'list')
    assert done.returncode == 3 and 'not enabled' in done.stdout


def test_tool_errors_exit_2_and_are_logged(workspace):
    done = run_tool(workspace, 'ce-graph', 'add-node', '--role', 'CONFIG_DECLARATION', '--path', 'missing.yaml',
                    '--span', '1-2')
    assert done.returncode == 2 and 'NO_SUCH_FILE' in done.stdout
    events = [json.loads(l) for l in (workspace.state / 'events.jsonl').read_text().splitlines()]
    assert events[-1]['tool'] == 'ce-graph' and events[-1]['exit_code'] == 2
