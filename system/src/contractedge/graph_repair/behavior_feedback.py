"""Turn check evidence into per-scenario outcomes, next actions and a short note for the executor.

Only evidence whose identity matches the current tree and the current obligation versions
changes an outcome. A passing check updates only the obligations and scenario it is linked to.
REPAIR is proposed only for a grounded expectation with a valid failing test; environment
failures become FIX_ENV, build failures READ, the rest DEFER. Explanations from the model never
change an outcome. At submission, a failure that was never shown blocks once; nothing else blocks.
"""
from __future__ import annotations

import json

from contractedge.graph_repair import behavior

MAX_NOTE = 1500


def _short(text: str, n: int = 140) -> str:
    text = behavior._norm(text)
    return text if len(text) <= n else text[:n - 1] + '…'


def _label(check: dict) -> str:
    spec = check['runner_spec']
    return f"go test {spec['package']} -run {spec['run']}"


def _signature(evidence: dict) -> str:
    return json.dumps([evidence['check_id'], evidence['identity']['tree_before'], evidence['result'],
                       evidence['failing']])


def _set(ob: dict, scenario: str, status: str, evidence: dict, calibrated) -> dict:
    row = {'status': status, 'tree': evidence['identity']['tree_before'], 'version': ob['version'],
           'evidence': evidence['id'], 'check': evidence['check_id'], 'calibrated': calibrated}
    ob['outcomes'][scenario] = row
    return row


def reduce(snapshot: dict, evidence: dict, tree: str, baseline_tree: str | None, show: bool = True) -> dict:
    """Apply one evidence record; return {'changes', 'next_actions', 'text'}.

    With show=False (checks run while submitting) nothing is marked as shown, so an unseen failure
    can still produce the one-time submission reminder."""
    check = snapshot['checks'][evidence['check_id']]
    ident = evidence['identity']
    changes, actions = [], []
    result = evidence['result']
    if ident['tree_before'] != tree or ident['tree_before'] != ident['tree_after']:
        actions.append({'type': 'CHECK', 'obligation_refs': list(evidence['obligation_versions']),
                        'evidence_refs': [evidence['id']], 'target': check['id'],
                        'reason': 'the result belongs to another version of the source; re-check'})
        return _bundle(snapshot, evidence, check, changes, actions, show)
    at_baseline = baseline_tree is not None and tree == baseline_tree
    if at_baseline and result in ('PASS', 'SEMANTIC_FAIL'):
        check['baseline_result'] = result
    for oid, version in evidence['obligation_versions'].items():
        ob = snapshot['obligations'].get(oid)
        if ob is None or ob['version'] != version or ob['grounding'] != 'GROUNDED':
            continue                                    # the obligation changed meaning or support
        scenario = evidence['scenario']
        if ob['kind'] == 'target':
            calibrated = {'SEMANTIC_FAIL': True, 'PASS': False}.get(check['baseline_result'])
        else:
            calibrated = {'PASS': True, 'SEMANTIC_FAIL': False}.get(check['baseline_result'])
        if result == 'PASS':
            before = behavior.outcome(ob, scenario, tree)
            _set(ob, scenario, 'PASS_AT_SNAPSHOT', evidence, calibrated)
            changes.append({'id': oid, 'scenario': scenario, 'from': before, 'to': 'PASS_AT_SNAPSHOT'})
            if ob['kind'] == 'target' and calibrated is False:
                actions.append({'type': 'READ', 'obligation_refs': [oid], 'evidence_refs': [evidence['id']],
                                'target': check['id'], 'reason': 'this test already passes on the original version, '
                                'so it cannot show the target behaviour; it is supporting evidence only'})
        elif result == 'SEMANTIC_FAIL':
            before = behavior.outcome(ob, scenario, tree)
            _set(ob, scenario, 'FAIL_AT_SNAPSHOT', evidence, calibrated)
            changes.append({'id': oid, 'scenario': scenario, 'from': before, 'to': 'FAIL_AT_SNAPSHOT'})
            if ob['kind'] == 'preserve' and calibrated is False:
                actions.append({'type': 'RESOLVE_CONFLICT', 'obligation_refs': [oid], 'evidence_refs': [evidence['id']],
                                'target': check['id'], 'reason': 'the test already fails on the original version, so '
                                'it does not show a preserved behaviour; compare it with the requirement source'})
            else:
                actions.append({'type': 'REPAIR', 'obligation_refs': [oid], 'evidence_refs': [evidence['id']],
                                'target': check['id'], 'priority': 0 if ob['kind'] == 'preserve' else 1,
                                'reason': ('a behaviour that must be preserved now fails' if ob['kind'] == 'preserve'
                                           else 'the required behaviour is not produced yet')})
    if result == 'INFRA_ERROR':
        actions.append({'type': 'FIX_ENV', 'obligation_refs': list(evidence['obligation_versions']),
                        'evidence_refs': [evidence['id']], 'target': check['id'],
                        'reason': 'the runtime failed (' + _short(evidence['detail'], 80) + '); this says nothing '
                        'about the behaviour, do not change production code for it'})
    elif result == 'BUILD_FAIL':
        actions.append({'type': 'READ', 'obligation_refs': list(evidence['obligation_versions']),
                        'evidence_refs': [evidence['id']], 'target': check['id'],
                        'reason': 'the package does not build at this version, so the test did not run'})
    elif result in ('TIMEOUT', 'UNCLASSIFIED', 'INVALID_CHECK'):
        actions.append({'type': 'DEFER', 'obligation_refs': list(evidence['obligation_versions']),
                        'evidence_refs': [evidence['id']], 'target': check['id'],
                        'reason': f"{result.lower().replace('_', ' ')}: {_short(evidence['detail'], 80)}"})
    return _bundle(snapshot, evidence, check, changes, actions, show)


def _bundle(snapshot, evidence, check, changes, actions, show=True) -> dict:
    signature = _signature(evidence)
    fresh = show and signature not in snapshot['reported']
    if fresh:
        snapshot['reported'].append(signature)
    snapshot['next_actions'] = [a for a in snapshot['next_actions']
                                if not set(a['obligation_refs']) & set(evidence['obligation_versions'])] + actions
    text = render(snapshot, evidence, check, actions) if fresh else ''
    return {'changes': changes, 'next_actions': actions, 'text': text, 'evidence': evidence['id']}


def render(snapshot: dict, evidence: dict, check: dict, actions: list[dict]) -> str:
    lines = [f"[ce-behavior] Check {check['id']} `{_label(check)}` on the current version: {evidence['result']}"
             f" ({_short(evidence['detail'], 80)})."]
    for oid in evidence['obligation_versions']:
        ob = snapshot['obligations'].get(oid)
        if ob:
            lines.append(f"- {oid} [{ob['kind']}] when {_short(ob['condition'], 100)}: expected "
                         f"{_short(ob['expected'], 120)}")
    if evidence['failing']:
        lines.append('  failing: ' + ', '.join(evidence['failing'][:6]))
    if evidence['excerpt']:
        lines += ['  ' + _short(l, 200) for l in evidence['excerpt'].splitlines()[:6]]
    order = {'REPAIR': 0, 'RESOLVE_CONFLICT': 1, 'FIX_ENV': 2, 'READ': 3, 'CHECK': 4, 'DEFER': 5}
    for action in sorted(actions, key=lambda a: (order[a['type']], a.get('priority', 9))):
        lines.append(f"Next: {action['type']} {','.join(action['obligation_refs']) or action['target']} - "
                     f"{action['reason']}.")
    lines.append('(Requirements come from the public task text and tests; this covers only the test above.)')
    text = '\n'.join(lines)
    return text if len(text) <= MAX_NOTE else text[:MAX_NOTE - 1] + '…'


def stale(snapshot: dict, previous: str | None, tree: str) -> list[str]:
    """Obligations whose last outcome was bound to `previous` and is now UNKNOWN after an edit."""
    out = []
    for ob in behavior.active(snapshot):
        if any(row.get('tree') == previous and row.get('version') == ob['version'] for row in ob['outcomes'].values()) \
                and previous != tree:
            out.append(ob['id'])
    return out


def summary(snapshot: dict, tree: str) -> dict:
    """What is known about the submitted version; unchecked is reported as unknown, never as passing."""
    passed, failed, unknown = [], [], []
    for ob in behavior.active(snapshot):
        scenarios = sorted({snapshot['checks'][c]['scenario'] for c in ob['check_ids'] if c in snapshot['checks']})
        if not scenarios:
            unknown.append({'id': ob['id'], 'scenario': None, 'why': 'no check'})
        for scenario in scenarios:
            status = behavior.outcome(ob, scenario, tree)
            row = ob['outcomes'].get(scenario, {})
            item = {'id': ob['id'], 'kind': ob['kind'], 'scenario': scenario}
            if status == 'PASS_AT_SNAPSHOT':
                passed.append({**item, 'calibrated': row.get('calibrated'), 'evidence': row.get('evidence')})
            elif status == 'FAIL_AT_SNAPSHOT':
                failed.append({**item, 'evidence': row.get('evidence')})
            else:
                unknown.append({**item, 'why': 'not checked on this version'})
    by = lambda g: [o['id'] for o in snapshot['obligations'].values() if o['grounding'] == g]
    return {'schema': 'contractedge.behavior_summary.v1', 'submitted_identity': tree, 'observed_pass': passed,
            'failed': failed, 'unknown': unknown, 'unresolved': by('UNRESOLVED'), 'retracted': by('RETRACTED'),
            'counters': dict(snapshot['counters'])}


def submit_reminder(snapshot: dict, tree: str) -> str | None:
    """A valid failure on this version that the executor has not been shown blocks submission once."""
    lines = []
    for ev in snapshot['evidence']:
        if ev['identity']['tree_before'] != tree or ev['result'] != 'SEMANTIC_FAIL':
            continue
        signature = _signature(ev)
        if signature in snapshot['reported'] or signature in snapshot['reminded']:
            continue
        snapshot['reminded'].append(signature)
        snapshot['reported'].append(signature)
        check = snapshot['checks'][ev['check_id']]
        live = [oid for oid, v in ev['obligation_versions'].items()
                if snapshot['obligations'].get(oid, {}).get('version') == v
                and snapshot['obligations'][oid]['grounding'] == 'GROUNDED']
        if live:
            lines.append(f"- `{_label(check)}` fails for {', '.join(live)}: {', '.join(ev['failing'][:4])}"
                         + (f"\n  {_short(ev['excerpt'].splitlines()[0], 200)}" if ev['excerpt'] else ''))
    if not lines:
        return None
    return ('[ce-behavior] Before submitting: checks run on this version found failures you have not seen yet.\n'
            + '\n'.join(lines) + '\nFix them, or submit again to submit as is (this reminder is shown once).')


def rank(snapshot: dict, tree: str) -> tuple:
    """Ordering for candidate selection: fewer confirmed preserve failures first, then more target passes.
    Unchecked behaviour adds nothing; it is not counted as passing. Read from the evidence log because
    outcomes keep only the latest version, while earlier checkpoints are ranked too."""
    latest = {}
    for ev in snapshot['evidence']:
        ident = ev['identity']
        if ident['tree_before'] != tree or ident['tree_after'] != tree or ev['result'] not in ('PASS', 'SEMANTIC_FAIL'):
            continue
        for oid, version in ev['obligation_versions'].items():
            ob = snapshot['obligations'].get(oid)
            if ob and ob['grounding'] == 'GROUNDED' and ob['version'] == version:
                latest[(oid, ev['scenario'])] = (ob['kind'], ev['result'])
    regressions = sum(1 for kind, result in latest.values() if kind == 'preserve' and result == 'SEMANTIC_FAIL')
    passes = sum(1 for kind, result in latest.values() if kind == 'target' and result == 'PASS')
    return (-regressions, passes)
