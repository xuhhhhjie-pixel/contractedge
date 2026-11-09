"""Graph store: the single hard anchor check, staleness, closure decisions."""
import pytest

from conftest import run_tool
from contractedge.graph_repair.graph import closure, store
from contractedge.graph_repair.graph.schema import GraphError, empty


def build(root):
    graph = empty()
    store.add_node(graph, role='CONFIG_DECLARATION', path='chart/values.yaml', span=[1, 4], anchor='ingress', root=root)
    store.add_node(graph, role='PRODUCTION_CONSUMER', path='chart/templates/ingress.yaml', span=[1, 8], root=root)
    store.add_edge(graph, edge_type='PROPAGATION', source='N1', target='N2', status='VIOLATED', note='annotations')
    return graph


def test_anchor_must_exist_and_fit(workspace):
    graph = empty()
    with pytest.raises(GraphError) as exc:
        store.add_node(graph, role='CONFIG_DECLARATION', path='nope.yaml', span=[1, 1], root=workspace.root)
    assert exc.value.code == 'NO_SUCH_FILE'
    with pytest.raises(GraphError) as exc:
        store.add_node(graph, role='CONFIG_DECLARATION', path='chart/values.yaml', span=[5, 99], root=workspace.root)
    assert exc.value.code == 'BAD_SPAN'
    with pytest.raises(GraphError):
        store.add_node(graph, role='CONFIG_DECLARATION', path='../etc/passwd', span=[1, 1], root=workspace.root)
    node = store.add_node(graph, role='CONFIG_DECLARATION', path='/testbed/chart/values.yaml', span=[1, 2],
                          root=workspace.root)
    assert node['path'] == 'chart/values.yaml' and len(node['text_sha256']) == 64


def test_validate_reports_stale_but_stays_valid(workspace):
    graph = build(workspace.root)
    assert store.validate(graph, workspace.root)['stale_nodes'] == []
    (workspace.root / 'chart/values.yaml').write_text('ingress:\n  enabled: false\n  annotations: {}\n  host: x\n')
    report = store.validate(graph, workspace.root)
    assert report['stale_nodes'] == ['N1'] and report['valid']


def test_no_fix_requires_reason(workspace):
    graph = build(workspace.root)
    with pytest.raises(GraphError):
        store.decide(graph, of='E1', candidate='N2', decision='NO_FIX')
    item = store.decide(graph, of='E1', candidate='N2', decision='NO_FIX', reason='already covered')
    assert item['decision'] == 'NO_FIX'


def test_closure_finds_sibling_consumers(workspace):
    graph = empty()
    store.add_node(graph, role='CONFIG_DECLARATION', path='chart/values.yaml', span=[4, 4], anchor='ingress.host',
                   root=workspace.root)
    store.add_node(graph, role='PRODUCTION_CONSUMER', path='chart/templates/ingress.yaml', span=[8, 8], root=workspace.root)
    store.add_edge(graph, edge_type='PROPAGATION', source='N1', target='N2', status='VIOLATED')
    added = closure.expand(graph, 'E1', workspace.root)
    paths = {a['path'] for a in added}
    assert 'chart/templates/admin-ingress.yaml' in paths
    assert 'chart/templates/deploy.yaml' not in paths
    assert all(a['decision'] == 'UNDECIDED' for a in added)


def test_cli_roundtrip_persists_outside_workspace(workspace):
    run_tool(workspace, 'ce-graph', 'add-node', '--role', 'CONFIG_DECLARATION', '--path', 'chart/values.yaml',
             '--span', '1-4', '--anchor', 'ingress', check=True)
    run_tool(workspace, 'ce-graph', 'add-node', '--role', 'PRODUCTION_CONSUMER', '--path',
             'chart/templates/ingress.yaml', '--span', '5-5', check=True)
    run_tool(workspace, 'ce-graph', 'add-edge', '--type', 'PROPAGATION', '--from', 'N1', '--to', 'N2', '--status',
             'VIOLATED', check=True)
    shown = run_tool(workspace, 'ce-graph', 'show', check=True).stdout
    assert '"E1"' in shown and (workspace.state / 'graph.json').exists()
    status = run_tool(workspace, 'git', 'status', '--porcelain', check=True).stdout
    assert status == ''
