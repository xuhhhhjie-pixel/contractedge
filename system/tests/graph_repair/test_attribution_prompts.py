"""Failure attribution rules and the prompt builder."""
from pathlib import Path

import pytest

from contractedge.graph_repair import attribution
from contractedge.graph_repair.config import VARIANTS
from contractedge.graph_repair.prompts.build import END, START, agent_config

GRAPH = {'nodes': [{'id': 'N1', 'role': 'CONFIG_DECLARATION', 'path': 'chart/values.yaml', 'span': [1, 4]},
                   {'id': 'N2', 'role': 'PRODUCTION_CONSUMER', 'path': 'chart/templates/ingress.yaml', 'span': [10, 20]}],
         'edges': [{'id': 'E1', 'type': 'PROPAGATION', 'from': 'N1', 'to': 'N2', 'status': 'VIOLATED'}]}


def test_render_error_maps_to_edge():
    out = 'Error: template: mychart/templates/ingress.yaml:14:3: executing "x" at <.Values.a>: nil pointer'
    rows = attribution.attribute(out, GRAPH)
    assert rows[0].node == 'N2' and rows[0].confidence > 0.8 and 'E1 PROPAGATION' in rows[0].edges[0]


def test_go_error_outside_graph():
    rows = attribution.attribute('pkg/x/y.go:33:2: undefined: Foo', GRAPH)
    assert rows[0].node is None and 'not in the contract graph' in rows[0].reason


def test_probe_failure_maps_to_its_edge():
    probes = [{'id': 'P1', 'edge': 'E1', 'kind': 'violate'}]
    rows = attribution.attribute('{"P1": {"kind": "violate", "exit_code": 1}}', GRAPH, probes)
    assert rows[-1].location == 'P1' and 'still violated' in rows[-1].reason


def test_no_locations_no_note():
    assert attribution.render(attribution.attribute('make: *** [all] Error 2', GRAPH)) == ''


LIVE = Path(__file__).with_name('livesweagent.fixture.yaml')


@pytest.fixture(scope='module')
def live_yaml():
    return LIVE.read_text()


def test_live_prompt_is_unchanged(live_yaml):
    import yaml
    built = agent_config(live_yaml, VARIANTS['live-equivalent'])
    assert built['agent'] == yaml.safe_load(live_yaml)['agent']
    assert agent_config(live_yaml, VARIANTS['submit-only'])['agent'] == built['agent']


def test_graph_prompt_replaces_only_the_workflow(live_yaml):
    import yaml
    original = yaml.safe_load(live_yaml)['agent']
    built = agent_config(live_yaml, VARIANTS['full'])['agent']
    for key in original:
        if key != 'instance_template':
            assert built[key] == original[key]
    a, b = original['instance_template'], built['instance_template']
    assert a[:a.index(START)] == b[:b.index(START)] and a[a.index(END):] == b[b.index(END):]
    for word in ('ce-links', 'ce-closure', 'ce-probe calibrate', 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT', '/ce-state'):
        assert word in b


def test_ablated_prompts_drop_disabled_tools(live_yaml):
    assert 'ce-probe' not in agent_config(live_yaml, VARIANTS['no-probes'])['agent']['instance_template']
    assert 'ce-closure' not in agent_config(live_yaml, VARIANTS['no-closure'])['agent']['instance_template']
    text = agent_config(live_yaml, VARIANTS['no-stop-rule'])['agent']['instance_template']
    assert 'automatically (`ce-submit' not in text and 'self-check' in text
