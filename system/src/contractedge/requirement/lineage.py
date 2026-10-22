"""Append-only obligation history. Source anchors are evidence, not equivalence.

The ledger proves identity/structure only. Split residuals and original parent
checks remain live; a model's claim of semantic equivalence never closes them.
"""
from copy import deepcopy
import hashlib
import json


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _assert_graph(nodes):
    active, done = set(), set()
    def visit(key):
        if key not in nodes or key in active:
            raise ValueError('LINEAGE_INVALID_GRAPH: missing node or cycle')
        if key in done:
            return
        active.add(key)
        for child in nodes[key].get('children', []):
            visit(child)
        active.remove(key); done.add(key)
    for key in nodes:
        visit(key)


def validate_lineage(events: list[dict], prior: dict, current: dict) -> dict:
    """Validate events against runner-validated atoms, never mutate prior input."""
    identity = {k: current[k] for k in ('input_digest', 'requirement_digest')}
    if prior and any(prior.get(k) != v for k, v in identity.items()):
        raise ValueError('LINEAGE_IDENTITY_MISMATCH')
    ledger = deepcopy(prior) if prior else dict(schema_version='ce.obligation_lineage.v1',
                                               **identity, nodes={}, events=[])
    nodes = ledger['nodes']; _assert_graph(nodes)
    atoms = {a['id']: a for a in current['atoms']}
    if len(atoms) != len(current['atoms']):
        raise ValueError('LINEAGE_DUPLICATE_ATOM')
    bindings, covered = {}, set()

    def attach(key, atom):
        source = {k: deepcopy(atom.get(k)) for k in ('source_refs','source_quote','scope_quote')}
        if key not in nodes:
            nodes[key] = dict(id=key, definition=deepcopy(atom), aliases=[], sources=[],
                              parents=[], children=[], residual=None, kind='OBLIGATION')
        n = nodes[key]
        if atom['outcome_id'] != n['definition']['outcome_id']:
            raise ValueError('LINEAGE_OUTCOME_MISMATCH')
        if source not in n['sources']:
            n['sources'].append(source)
        if atom['statement'] != n['definition']['statement'] and atom['statement'] not in n['aliases']:
            n['aliases'].append(atom['statement'])
        bindings[atom['id']] = key

    for event in events:
        kind = event.get('kind'); parents = event.get('parent_ids', []); refs = event.get('atom_refs', [])
        if (kind not in {'CONTINUE','REANCHOR','SPLIT','MERGE'} or not event.get('rationale','').strip()
                or not parents or len(parents)!=len(set(parents)) or any(p not in nodes for p in parents)
                or not refs or len(refs)!=len(set(refs)) or any(a not in atoms or a in covered for a in refs)):
            raise ValueError('LINEAGE_INVALID_EVENT')
        if kind in {'CONTINUE','REANCHOR'}:
            if len(parents)!=1 or len(refs)!=1:
                raise ValueError('LINEAGE_CONTINUATION_ARITY')
            attach(parents[0], atoms[refs[0]])
            if event.get('interpretation_disputed'):
                node=nodes[parents[0]]
                node['interpretation_status']='DISPUTED'
                node['dispute']=dict(rationale=event['rationale'],snapshot_id=current['snapshot_id'],
                    source_refs=deepcopy(atoms[refs[0]].get('source_refs',[])),
                    source_quote=atoms[refs[0]].get('source_quote'),
                    interpretation='Prior derived interpretation challenged; original requirement and history remain open, no semantic equivalence or satisfaction established')
        elif kind=='SPLIT':
            if len(parents)!=1 or len(refs)<2:
                raise ValueError('LINEAGE_SPLIT_ARITY')
            for ref in refs:
                key='OB-'+_digest([identity, parents, atoms[ref]['stable_obligation_key'],ref])[:24]
                attach(key, atoms[ref]); nodes[key]['parents']=parents[:]
                if key not in nodes[parents[0]]['children']:
                    nodes[parents[0]]['children'].append(key)
            nodes[parents[0]]['residual'] = dict(status='UNKNOWN',
                reason='Original definition/checks remain live; child predicates do not prove exhaustive coverage')
        else:
            if len(parents)<2 or len(refs)!=1:
                raise ValueError('LINEAGE_MERGE_ARITY')
            key='OB-VIEW-'+_digest([identity, sorted(parents), atoms[refs[0]]['stable_obligation_key']])[:24]
            attach(key, atoms[refs[0]])
            nodes[key].update(kind='CONJUNCTION_VIEW', children=parents[:])
        if event.get('interpretation_disputed') and kind not in {'CONTINUE','REANCHOR'}:
            raise ValueError('LINEAGE_DISPUTE_REQUIRES_EXISTING_IDENTITY')
        covered.update(refs)
        record={**deepcopy(event), 'snapshot_id':current['snapshot_id'],
                'resolved_ids':{r:bindings[r] for r in refs}}
        record['id']='LE-'+_digest([len(ledger['events']),record])[:24]
        ledger['events'].append(record)
    for ref, atom in atoms.items():
        if ref in covered:
            continue
        key=atom.get('stable_obligation_key')
        if not key:
            raise ValueError('LINEAGE_SOURCE_IDENTITY_REQUIRED')
        new=key not in nodes; attach(key, atom)
        ledger['events'].append(dict(kind='REGISTER' if new else 'CONTINUE',
            id='LE-'+_digest([len(ledger['events']),key,current['snapshot_id'],ref])[:24],
            parent_ids=[] if new else [key], atom_refs=[ref], resolved_ids={ref:key},
            snapshot_id=current['snapshot_id'], rationale='Exact runner-bound source identity; original semantics retained'))
    _assert_graph(nodes)
    ledger.update(bindings=bindings, snapshot_id=current['snapshot_id'])
    return ledger


def project_obligation_coverage(ledger: dict, checks: list[dict], context: dict) -> dict:
    """Fresh validated checks only; views are conjunctions, splits not equivalence."""
    if context.get('input_digest') != ledger['input_digest']:
        raise ValueError('LINEAGE_CHECK_IDENTITY_MISMATCH')
    result={}
    for key,node in ledger['nodes'].items():
        applicable=[c for c in checks if c.get('obligation_id')==key and c.get('evidence_validated')
            and c.get('snapshot_id')==context['snapshot_id'] and c.get('input_digest')==context['input_digest']]
        # Conservative conjunction: every current bound claim must be adequately supported.
        status=('FAIL' if any(c.get('status')=='FAIL' and c.get('check_coverage') in {'ALL_OF','NECESSARY_ONLY'} for c in applicable)
            else 'PASS' if applicable and all(c.get('status')=='PASS' and c.get('check_coverage')=='ALL_OF'
                and (not node.get('aliases') or node['definition']['statement'] in c.get('covered_statements',[]))
                for c in applicable)
            else 'UNKNOWN')
        result[key]=dict(status=status, snapshot_id=context['snapshot_id'],
            residual=deepcopy(node.get('residual')), reason='Fresh direct checks' if status!='UNKNOWN' else 'Missing sufficient current evidence')
        if node.get('interpretation_status')=='DISPUTED':
            result[key].update(status='UNKNOWN',reason='Disputed derived interpretation; reassess the original public requirement without enforcing the old interpretation')
    def view(key):
        node=ledger['nodes'][key]
        if node.get('interpretation_status')=='DISPUTED':return 'UNKNOWN'
        if node['kind']=='CONJUNCTION_VIEW':
            states=[view(child) for child in node['children']]
            result[key]['status']='FAIL' if 'FAIL' in states else 'PASS' if states and all(s=='PASS' for s in states) else 'UNKNOWN'
        return result[key]['status']
    for key in result: view(key)
    return result


def disputed_obligation(item, ledger):
    return any(ledger.get('nodes',{}).get(key,{}).get('interpretation_status')=='DISPUTED'
               for key in item.get('stable_obligation_keys',[]))


def bind_diagnosis_lineage(payload, requirement):
    if not requirement.get('lineage_required'):
        return
    atoms=payload['obligation_atoms']
    ledger=validate_lineage(payload.get('lineage_events', []), requirement.get('prior_obligation_lineage', {}),
        dict(input_digest=payload['input_digest'], requirement_digest=_digest(requirement['raw_text']),
             snapshot_id=payload['snapshot_id'], atoms=atoms))
    payload['obligation_lineage']=ledger
    for atom in atoms:
        atom['stable_obligation_key']=ledger['bindings'][atom['id']]
