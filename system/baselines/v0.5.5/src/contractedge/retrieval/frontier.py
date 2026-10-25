"""Small, explicitly lexical branch inventory from source already in context."""
from collections import defaultdict
from contractedge.retrieval.candidates import literal_identifiers

_COMMON_CALLS = {'len','append','make','new','delete','copy','Errorf','Error','String','Printf','Sprintf','panic','recover'}


def production_frontier(candidates, requirement):
    identifiers = literal_identifiers(requirement)
    eligible = [u for u in candidates if u.get('kind') == 'FUNCTION_OR_TYPE'
                and not any(x in u.get('path','').lower() for x in ('_test.', '/test/', '/tests/'))
                and any(t in u.get('text','').casefold() for t in identifiers)]
    peers = defaultdict(set)
    for i,a in enumerate(eligible):
        for b in eligible[i+1:]:
            # Same-file ownership is deliberate: a lexical name match in a
            # different package is not enough to infer a shared consumer.
            if a['path'] != b['path']: continue
            shared = (set(a.get('references',[])) & set(b.get('references',[]))) - _COMMON_CALLS
            if len(shared) >= 2:
                peers[a['id']].update(shared);peers[b['id']].update(shared)
    rows=[]
    for u in eligible:
        if u['id'] not in peers: continue
        rows.append({'source_evidence_id':'SRC-'+u['id'],'path':u['path'],
            'locator':dict(u['locator']),'snapshot_id':u['snapshot_id'],'content_digest':u['content_digest'],
            'symbol':u.get('symbol'),'shared_calls':sorted(peers[u['id']]),'text':u['text'],
            'association':'LEXICAL_SIBLING_CANDIDATE; relevance and causal role require explicit review',
            'truncated':bool(u.get('truncated'))})
    return rows[:12]
