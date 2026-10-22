from contractedge.retrieval.gap_delivery import locate_gap_roles
from contractedge.source.snapshot import compute_snapshot


def test_exact_symbol_precedes_long_similarly_named_metric_constructor(tmp_path):
    (tmp_path/'state.go').write_text('package state\ntype Worker struct { groups map[string]*Group }\n'
        'func NewWorkerMetrics() { '+ 'worker groups lifecycle state '*40+' }\n')
    units,_=locate_gap_roles(compute_snapshot(tmp_path),[dict(id='G',summary='Worker groups lifecycle state',
        locator_hint=dict(path='state.go',symbol='Worker'))],{})
    assert next(u for u in units if 'G:anchor' in u.get('gap_role_keys',[]))['symbol']=='Worker'


def test_exact_go_method_case_is_not_replaced_by_lowercase_method(tmp_path):
    (tmp_path/'state.go').write_text('package state\nfunc (w *Worker) Run() { initialize() }\n'
        'func (w *Worker) run() { '+ 'groups lifecycle replay state '*40+' }\n')
    units,_=locate_gap_roles(compute_snapshot(tmp_path),[dict(id='G',summary='groups lifecycle replay state',
        locator_hint=dict(path='state.go',symbol='Run'))],{})
    assert next(u for u in units if 'G:anchor' in u.get('gap_role_keys',[]))['symbol']=='Run'


def test_qualified_call_query_requires_actual_call_text_before_term_overlap(tmp_path):
    (tmp_path/'state.go').write_text('package state\nfunc (w *Worker) Run() { w.alerts.Subscribe("public") }\n'
        'type AlertGroup struct { '+ 'alerts subscribe aggregation state '*40+' }\n')
    units,_=locate_gap_roles(compute_snapshot(tmp_path),[dict(id='G',summary='alerts subscribe aggregation state',
        locator_hint=dict(path='state.go',search_term='alerts.Subscribe'))],{})
    assert next(u for u in units if 'G:anchor' in u.get('gap_role_keys',[]))['symbol']=='Run'


def test_requested_receiver_factory_can_have_multiple_results_without_broadening_file_budget(tmp_path):
    (tmp_path/'go.mod').write_text('module example.org/app\n')
    (tmp_path/'main.go').write_text('''package main
import "example.org/app/store"
import "example.org/app/other"
func run() { alerts, err := store.NewAlerts(); _=alerts; _=err; coordinator:=other.New(); coordinator.Subscribe() }
''')
    (tmp_path/'store').mkdir();(tmp_path/'other').mkdir()
    (tmp_path/'store/mem.go').write_text('package store\nfunc (a *Alerts) Subscribe() { replayActive() }\n')
    (tmp_path/'other/coord.go').write_text('package other\nfunc (c *Coordinator) Subscribe() { reloadCallback() }\n')
    units,audit=locate_gap_roles(compute_snapshot(tmp_path),[dict(id='G',summary='alerts replay semantics',
        locator_hint=dict(path='main.go',search_term='alerts.Subscribe'))],dict(max_new_files=2))
    assert any(u['path']=='store/mem.go' and u.get('symbol')=='Subscribe' for u in units)
    assert audit['inspected_files']<=2
    assert not any(u.get('verified_causal_edge') for u in units)


def test_qualified_receiver_symbol_does_not_select_another_receivers_method(tmp_path):
    (tmp_path/'state.go').write_text('package state\nfunc (w *Worker) Run() { initialize() }\n'
        'func (m *Metrics) Run() { '+ 'worker lifecycle state '*40+' }\n')
    units,_=locate_gap_roles(compute_snapshot(tmp_path),[dict(id='G',summary='worker lifecycle state',
        locator_hint=dict(path='state.go',symbol='Worker.Run'))],{})
    anchor=next(u for u in units if 'G:anchor' in u.get('gap_role_keys',[]))
    assert '(w *Worker)' in anchor['text']


def test_same_declaration_answers_two_gap_roles_without_consuming_two_units(tmp_path):
    (tmp_path/'state.go').write_text('package state\nfunc (w *Worker) Run() { initialize() }\n')
    units,_=locate_gap_roles(compute_snapshot(tmp_path),[
        dict(id='G1',summary='startup',locator_hint=dict(path='state.go',symbol='Run')),
        dict(id='G2',summary='initialize',locator_hint=dict(path='state.go',search_term='initialize'))],{})
    assert len(units)==1
    assert {'G1:anchor','G2:anchor'}<=set(units[0]['gap_role_keys'])
