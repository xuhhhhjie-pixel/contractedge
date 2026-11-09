"""Single entry for every ce-* command; the command is chosen by the program name."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from contractedge.graph_repair import checkpoint, state

INACTIVE = 3
NEEDS = {'ce-search': ('graph', 'linkers'), 'ce-read': ('graph', 'linkers'), 'ce-follow': ('graph', 'linkers'),
         'ce-links': ('linkers',), 'ce-graph': ('graph',), 'ce-closure': ('closure',), 'ce-probe': ('probes',),
         'ce-ckpt': ('checkpoint',), 'ce-rollback': ('checkpoint',), 'ce-submit': ('stop_rule', 'checkpoint')}


def emit(value) -> None:
    if isinstance(value, str):
        print(value)
    else:
        from contractedge.graph_repair.navigation import dumps
        print(dumps(value))


def cmd_search(argv):
    p = argparse.ArgumentParser(prog='ce-search', description='Locate declarations or text (names, positions, handles).')
    p.add_argument('--scope', action='append')
    p.add_argument('--symbol'); p.add_argument('--receiver'); p.add_argument('--text'); p.add_argument('--kind')
    p.add_argument('--case-sensitive', action='store_true'); p.add_argument('--cursor')
    p.add_argument('--page-size', type=int, default=25)
    a = p.parse_args(argv)
    from contractedge.graph_repair import navigation
    emit(navigation.search(scope=a.scope, symbol=a.symbol, receiver=a.receiver, text=a.text, kind=a.kind,
                           case_sensitive=a.case_sensitive, page_size=a.page_size, cursor=a.cursor))


def cmd_read(argv):
    p = argparse.ArgumentParser(prog='ce-read', description='Exact text of a declaration (handle) or a line range.')
    p.add_argument('handle', nargs='?'); p.add_argument('--path'); p.add_argument('--symbol'); p.add_argument('--receiver')
    p.add_argument('--start', type=int); p.add_argument('--end', type=int)
    a = p.parse_args(argv)
    from contractedge.graph_repair import navigation
    emit(navigation.read(a.handle, path=a.path, symbol=a.symbol, receiver=a.receiver, start_line=a.start, end_line=a.end))


def cmd_follow(argv):
    p = argparse.ArgumentParser(prog='ce-follow', description='Follow a Go declaration through an import or receiver.')
    p.add_argument('handle'); p.add_argument('--selector'); p.add_argument('--cursor')
    a = p.parse_args(argv)
    from contractedge.graph_repair import navigation
    emit(navigation.follow(a.handle, selector=a.selector, cursor=a.cursor))


def cmd_links(argv):
    p = argparse.ArgumentParser(prog='ce-links', description='Static cross-artifact candidate edges for a seed '
                                '(values key, Go field/tag, env var/flag, path or path:line).')
    p.add_argument('seed'); p.add_argument('--linker', action='append'); p.add_argument('--limit', type=int, default=60)
    a = p.parse_args(argv)
    from contractedge.graph_repair import linkers
    emit(linkers.run(state.WORKSPACE, a.seed, a.linker, a.limit))


def cmd_graph(argv):
    from contractedge.graph_repair.graph import store
    p = argparse.ArgumentParser(prog='ce-graph', description='Maintain the contract graph (/ce-state/graph.json).')
    sub = p.add_subparsers(dest='op', required=True)
    n = sub.add_parser('add-node'); n.add_argument('--id'); n.add_argument('--role', required=True)
    n.add_argument('--path', required=True); n.add_argument('--span', required=True, help='START-END lines')
    n.add_argument('--anchor', default=''); n.add_argument('--source', default='model')
    e = sub.add_parser('add-edge'); e.add_argument('--id'); e.add_argument('--type', required=True)
    e.add_argument('--from', dest='source', required=True); e.add_argument('--to', dest='target', required=True)
    e.add_argument('--status', default='UNKNOWN'); e.add_argument('--note', default='')
    s = sub.add_parser('set-edge'); s.add_argument('id'); s.add_argument('--status'); s.add_argument('--note')
    r = sub.add_parser('preserve'); r.add_argument('--edge', required=True); r.add_argument('--scenario', required=True)
    d = sub.add_parser('decide'); d.add_argument('--of', required=True); d.add_argument('--candidate', required=True)
    d.add_argument('--decision', required=True); d.add_argument('--reason', default='')
    u = sub.add_parser('unknown'); u.add_argument('--question', required=True); u.add_argument('--check', default='')
    sub.add_parser('show'); sub.add_parser('validate')
    i = sub.add_parser('import'); i.add_argument('file')
    t = sub.add_parser('auto', help='seed the graph from edited files (called by the executor)')
    t.add_argument('--files', nargs='+', required=True)
    a = p.parse_args(argv)
    with state.locked('graph'):
        graph = store.load()
        if a.op == 'auto':
            variant = state.switches()
            if not variant.get('linkers'):
                return INACTIVE
            from contractedge.graph_repair.graph import auto
            rows = auto.auto(graph, a.files, state.WORKSPACE, checkpoint.baseline_commit(), bool(variant.get('closure')))
            store.save(graph)
            if rows:
                undecided = [i for i in graph['closure'] if i['decision'] == 'UNDECIDED']
                tail = ('\n  Decide each listed consumer before submitting: ce-graph decide --of EDGE --candidate NODE '
                        '--decision NEEDS_FIX|NO_FIX --reason "..."' if undecided and variant.get('closure') else '')
                if variant.get('probes'):
                    from contractedge.graph_repair.probes import spec
                    covered = {p['edge'] for p in spec.load()}
                    open_edges = [e['id'] for e in graph['edges'] if e['id'] not in covered]
                    if open_edges:
                        tail += (f'\n  Optional cheap check for {open_edges[0]}: ce-probe add --edge {open_edges[0]} --kind violate '
                                 '--cmd "<render/static check that fails before the fix>", then ce-probe calibrate.')
                emit('[ce-auto] contract graph updated from your edit:\n' + '\n'.join(rows) + tail)
            return 0
        if a.op == 'add-node':
            start, _, end = a.span.partition('-')
            value = store.add_node(graph, role=a.role, path=a.path, span=[int(start), int(end or start)],
                                   anchor=a.anchor, source=a.source, node_id=a.id)
        elif a.op == 'add-edge':
            value = store.add_edge(graph, edge_type=a.type, source=a.source, target=a.target, status=a.status,
                                   note=a.note, edge_id=a.id)
        elif a.op == 'set-edge':
            value = store.set_edge(graph, a.id, status=a.status, note=a.note)
        elif a.op == 'preserve':
            value = store.add_preserve(graph, edge_id=a.edge, scenario=a.scenario)
        elif a.op == 'decide':
            value = store.decide(graph, of=a.of, candidate=a.candidate, decision=a.decision, reason=a.reason)
        elif a.op == 'unknown':
            value = store.add_unknown(graph, question=a.question, check=a.check)
        elif a.op == 'import':
            incoming = json.loads(Path(a.file).read_text())
            graph = {**graph, **{k: incoming[k] for k in ('nodes', 'edges', 'preserve', 'closure', 'unknown') if k in incoming}}
            for node in graph['nodes']:
                store.add_node(graph, role=node['role'], path=node['path'], span=node['span'],
                               anchor=node.get('anchor', ''), source=node.get('source', 'model'), node_id=node['id'])
            value = {'imported': True}
        elif a.op == 'validate':
            emit(store.validate(graph)); return
        else:
            emit(graph); return
        store.save(graph)
    emit(value)


def cmd_closure(argv):
    from contractedge.graph_repair.graph import closure, store
    p = argparse.ArgumentParser(prog='ce-closure', description='Same-kind consumers of an edge source not yet in the graph.')
    p.add_argument('edge')
    a = p.parse_args(argv)
    with state.locked('graph'):
        graph = store.load()
        added = closure.expand(graph, a.edge, state.WORKSPACE)
        store.save(graph)
    emit({'edge': a.edge, 'added_undecided': added,
          'next': 'decide each: ce-graph decide --of EDGE --candidate NODE --decision NEEDS_FIX|NO_FIX --reason "..."'})


def cmd_probe(argv):
    from contractedge.graph_repair.graph import store
    from contractedge.graph_repair.probes import calibrate, generate, run, spec
    variant = state.switches()
    p = argparse.ArgumentParser(prog='ce-probe', description='Contract probes: add, gen, calibrate, run, list.')
    sub = p.add_subparsers(dest='op', required=True)
    add = sub.add_parser('add'); add.add_argument('--edge', required=True); add.add_argument('--kind', required=True)
    add.add_argument('--cmd', required=True); add.add_argument('--local', action='store_true')
    sub.add_parser('gen'); sub.add_parser('calibrate'); sub.add_parser('list')
    r = sub.add_parser('run'); r.add_argument('--id')
    a = p.parse_args(argv)
    if a.op in ('calibrate', 'run'):
        stop = spec.check_budget(variant)
        if stop:
            emit({'probes_disabled': stop}); return INACTIVE
    with state.locked('probes'):
        if a.op == 'add':
            probes = spec.load()
            store.edge(store.load(), a.edge)
            value = spec.add(probes, edge=a.edge, kind=a.kind, cmd=a.cmd, local=a.local)
            spec.save(probes); emit(value)
        elif a.op == 'gen':
            emit(generate.report(store.load(), spec.load()))
        elif a.op == 'calibrate':
            emit(calibrate.calibrate(variant))
        elif a.op == 'run':
            emit(run.run(variant, only=a.id))
        else:
            emit(spec.load())
    return 0


def cmd_ckpt(argv):
    rows = checkpoint.checkpoints()
    data = checkpoint.results()
    best = checkpoint.best()
    emit({'checkpoints': [{**c, 'result': data.get(c['tree'], {})} for c in rows[-20:]],
          'best': best and best['n'], 'count': len(rows)})


def cmd_rollback(argv):
    from contractedge.graph_repair.probes import calibrate
    p = argparse.ArgumentParser(prog='ce-rollback', description='Restore the work tree to a checkpoint (HEAD unchanged).')
    p.add_argument('target', nargs='?', default='best', help='checkpoint number, "best", or "baseline"')
    p.add_argument('--resume-calibration', action='store_true')
    a = p.parse_args(argv)
    if a.resume_calibration:
        emit(calibrate.resume() or {'resumed': False}); return 0
    if a.target == 'baseline':
        rev = checkpoint.baseline_commit()
    else:
        rows = {c['n']: c for c in checkpoint.checkpoints()}
        row = checkpoint.best() if a.target == 'best' else rows.get(int(a.target))
        if not row:
            emit('CE: no such checkpoint'); return 2
        rev = row['commit']
    emit(checkpoint.restore(rev))
    return 0


def cmd_submit(argv):
    p = argparse.ArgumentParser(prog='ce-submit', description='Check the stop rule before submitting.')
    p.add_argument('--check', action='store_true', required=True); p.add_argument('--json', action='store_true')
    a = p.parse_args(argv)
    from contractedge.graph_repair import submit
    value = submit.check()
    if a.json:
        print('CE_SUBMIT_CHECK_JSON=' + json.dumps(value, sort_keys=True))
    else:
        emit(value)
    return 0 if value['passed'] else 1


COMMANDS = {'ce-search': cmd_search, 'ce-read': cmd_read, 'ce-follow': cmd_follow, 'ce-links': cmd_links,
            'ce-graph': cmd_graph, 'ce-closure': cmd_closure, 'ce-probe': cmd_probe, 'ce-ckpt': cmd_ckpt,
            'ce-rollback': cmd_rollback, 'ce-submit': cmd_submit}


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    name = Path(argv[0]).name
    if name not in COMMANDS:
        print(f'CE: unknown command {name}', file=sys.stderr)
        return 2
    variant = state.switches()
    if not state.STATE.is_dir() or not any(variant.get(k) for k in NEEDS[name]):
        print(f'CE: {name} is not enabled in this run')
        return INACTIVE
    began = time.time()
    code = 1
    try:
        code = COMMANDS[name](argv[1:]) or 0
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
    except Exception as exc:  # tools report, they do not crash the session
        reason = getattr(exc, 'reason', None) or getattr(exc, 'code', None) or type(exc).__name__
        print(f'CE_ERROR {reason}: {exc}')
        code = 2
    finally:
        try:
            state.append_jsonl(state.STATE / 'events.jsonl', {'tool': name, 'argv': argv[1:], 'started': began,
                                                              'ended': time.time(), 'exit_code': code})
        except OSError:
            pass
    return code
