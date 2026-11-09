"""Probe calibration on the original version, probe budget, and the stop rule."""
import json

from conftest import run_tool
from contractedge.graph_repair import state


def graph_with_edge(w):
    run_tool(w, 'ce-graph', 'add-node', '--role', 'CONFIG_DECLARATION', '--path', 'chart/values.yaml', '--span', '4-4',
             '--anchor', 'ingress.host', check=True)
    run_tool(w, 'ce-graph', 'add-node', '--role', 'PRODUCTION_CONSUMER', '--path', 'app.txt', '--span', '1-1', check=True)
    run_tool(w, 'ce-graph', 'add-edge', '--type', 'PROPAGATION', '--from', 'N1', '--to', 'N2', '--status', 'VIOLATED',
             check=True)
    run_tool(w, 'ce-graph', 'preserve', '--edge', 'E1', '--scenario', 'readme untouched', check=True)


def check(w):
    out = run_tool(w, 'ce-submit', '--check', '--json').stdout
    return json.loads([l for l in out.splitlines() if l.startswith('CE_SUBMIT_CHECK_JSON=')][0].split('=', 1)[1])


def test_calibration_keeps_good_probes_and_restores_edits(workspace):
    w = workspace
    graph_with_edge(w)
    (w.root / 'app.txt').write_text('state=fixed\n')        # edit before calibrating
    run_tool(w, 'ce-probe', 'add', '--edge', 'E1', '--kind', 'violate', '--cmd', 'grep -q fixed app.txt', '--local', check=True)
    run_tool(w, 'ce-probe', 'add', '--edge', 'E1', '--kind', 'preserve', '--cmd', 'test -f README.md', check=True)
    run_tool(w, 'ce-probe', 'add', '--edge', 'E1', '--kind', 'violate', '--cmd', 'true', '--local', check=True)
    out = json.loads(run_tool(w, 'ce-probe', 'calibrate', check=True).stdout)
    assert out['calibrated'] == ['P1', 'P2'] and [d['id'] for d in out['discarded']] == ['P3']
    assert (w.root / 'app.txt').read_text() == 'state=fixed\n' and out['work_tree_restored']
    assert not (w.state / 'calibration-in-progress.json').exists()
    ran = json.loads(run_tool(w, 'ce-probe', 'run', check=True).stdout)
    assert ran['summary'] == {'violate_passing': 1, 'violate_total': 1, 'preserve_failing': 0}


def test_stop_rule_items(workspace):
    w = workspace
    graph_with_edge(w)
    run_tool(w, 'ce-probe', 'add', '--edge', 'E1', '--kind', 'violate', '--cmd', 'grep -q fixed app.txt', '--local', check=True)
    run_tool(w, 'ce-probe', 'calibrate', check=True)
    (w.root / 'chart/templates/admin-ingress.yaml').write_text('kind: Ingress\n')   # leave a closure candidate open
    run_tool(w, 'ce-closure', 'E1', check=True)
    (w.root / 'tests/app_test.txt').write_text('changed\n')
    (w.root / 'notes.txt').write_text('scratch\n')
    kinds = {(u['kind'], u['id']) for u in check(w)['unmet']}
    assert ('public_check', 'PUBLIC') in kinds and ('violate_probe', 'P1') in kinds
    assert ('policy', 'tests/app_test.txt') in kinds and ('policy', 'notes.txt') in kinds
    assert any(k == 'closure' for k, _ in kinds)
    # Fix everything.
    (w.root / 'app.txt').write_text('state=fixed\n')
    (w.root / 'notes.txt').unlink()
    run_tool(w, 'git', 'checkout', '--', 'tests/app_test.txt', check=True)
    graph = json.loads((w.state / 'graph.json').read_text())
    for item in graph['closure']:
        run_tool(w, 'ce-graph', 'decide', '--of', item['of'], '--candidate', item['candidate'], '--decision', 'NO_FIX',
                 '--reason', 'not a consumer of the annotations', check=True)
    value = check(w)
    assert value['passed'], value['unmet']
    assert value['checks']['public']['exit_code'] == 0


def test_closure_off_and_probes_off_variants_skip_items(workspace):
    w = workspace
    w.configure('no-probes')
    graph_with_edge(w)
    (w.root / 'app.txt').write_text('state=fixed\n')
    assert check(w)['passed']


def test_submit_only_variant_checks_public_and_policy(workspace):
    w = workspace
    w.configure('submit-only')
    assert {u['kind'] for u in check(w)['unmet']} == {'policy', 'public_check'}   # no changes, public failing
    (w.root / 'app.txt').write_text('state=fixed\n')
    assert check(w)['passed']


def test_probe_budget_disables_probes(workspace):
    w = workspace
    graph_with_edge(w)
    run_tool(w, 'ce-probe', 'add', '--edge', 'E1', '--kind', 'violate', '--cmd', 'grep -q fixed app.txt', '--local', check=True)
    state.append_jsonl(w.state / 'probe-events.jsonl', {'probe': 'P0', 'probe_seconds': 400, 'phase': 'run'})
    done = run_tool(w, 'ce-probe', 'calibrate')
    assert done.returncode == 3 and 'probes_disabled' in done.stdout
    (w.root / 'app.txt').write_text('state=fixed\n')
    value = check(w)
    assert value['passed'] and value['probes_disabled']


def test_policy_globs_follow_the_published_validator():
    """fnmatchcase semantics, as scorer/patches.py: '*' crosses directories."""
    from contractedge.graph_repair.submit import matches
    assert matches('pkg/skaffold/render/kustomize.go', ['pkg/*.go', 'cmd/*.go'])
    assert matches('chart/templates/a.yaml', ['chart/**'])
    assert matches('a/b/c_test.go', ['**/*test*'])
    assert not matches('c_test.go', ['**/*test*'])           # validator needs a '/' for '**/'
    assert not matches('cmd/main.go', ['pkg/*', '*.md'])
    assert matches('docs/README.md', ['*.md'])
