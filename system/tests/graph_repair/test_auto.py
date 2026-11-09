"""Automatic graph seeding after edits, and its effect on the stop rule."""
import json

from conftest import run_tool
from test_agent_integration import FIX, SUBMIT, act, agent_for, events, run


EDIT_TEMPLATE = act("sed -i 's/host: {{ .Values.ingress.host }}/host: {{ .Values.ingress.host | quote }}/' "
                    "/testbed/chart/templates/ingress.yaml")


def test_auto_links_edit_to_declaration_and_siblings(workspace):
    w = workspace
    (w.root / 'chart/templates/ingress.yaml').write_text(
        (w.root / 'chart/templates/ingress.yaml').read_text().replace('.Values.ingress.host }}', '.Values.ingress.host | quote }}'))
    out = run_tool(w, 'ce-graph', 'auto', '--files', 'chart/templates/ingress.yaml', check=True).stdout
    assert out.startswith('[ce-auto]') and 'chart/values.yaml' in out
    assert 'chart/templates/admin-ingress.yaml' in out and 'Decide each listed consumer' in out
    graph = json.loads((w.state / 'graph.json').read_text())
    assert graph['edges'] and graph['edges'][0]['status'] == 'UNKNOWN'
    assert [i['decision'] for i in graph['closure']] == ['UNDECIDED']
    # A second call keeps existing decisions.
    item = graph['closure'][0]
    run_tool(w, 'ce-graph', 'decide', '--of', item['of'], '--candidate', item['candidate'], '--decision', 'NO_FIX',
             '--reason', 'admin host is fixed elsewhere', check=True)
    run_tool(w, 'ce-graph', 'auto', '--files', 'chart/templates/ingress.yaml', check=True)
    assert json.loads((w.state / 'graph.json').read_text())['closure'][0]['decision'] == 'NO_FIX'


def test_auto_is_inactive_without_linkers(workspace):
    workspace.configure('submit-only')
    assert run_tool(workspace, 'ce-graph', 'auto', '--files', 'app.txt').returncode == 3


def test_agent_appends_auto_note_and_stop_rule_asks_for_decision(workspace):
    decide = act('cd /testbed && for c in $(python3 -c "import json;g=json.load(open(\'/ce-state/graph.json\'));'
                 'print(\' \'.join(i[\'of\']+\',\'+i[\'candidate\'] for i in g[\'closure\']))"); do '
                 'ce-graph decide --of ${c%,*} --candidate ${c#*,} --decision NO_FIX --reason "not affected"; done')
    agent = agent_for(workspace, 'full', [EDIT_TEMPLATE, FIX, SUBMIT, decide, SUBMIT])
    status, _ = run(agent)
    assert status == 'Submitted' and agent.ce_forced is None
    notes = [m['content'] for m in agent.messages[2:] if '[ce-auto] contract graph' in m['content']]
    assert notes and 'admin-ingress.yaml' in notes[0]
    blocked = [m['content'] for m in agent.messages if 'Submission not accepted yet' in m['content']]
    assert len(blocked) == 1 and '[closure]' in blocked[0]
    assert any(e['event'] == 'auto_graph' for e in events(workspace))


def test_no_closure_variant_reports_but_does_not_block(workspace):
    agent = agent_for(workspace, 'no-closure', [EDIT_TEMPLATE, FIX, SUBMIT])
    status, _ = run(agent)
    assert status == 'Submitted'
    assert any('[ce-auto]' in m['content'] for m in agent.messages)
    assert not any('Submission not accepted yet' in m['content'] for m in agent.messages)
