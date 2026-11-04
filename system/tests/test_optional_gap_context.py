"""A bounded nonmatching hint must not displace actual required evidence."""
import hashlib
from contractedge.retrieval.candidates import CandidateSet, trim_context_bundle
from contractedge.retrieval.gap_delivery import locate_gap_roles
from contractedge.source.snapshot import compute_snapshot


def unit(name, *, optional=False, anchor=False):
    text = 'func ' + name + '() {}'
    return dict(id=name,path=name+'.go',text=text,
        content_digest=hashlib.sha256(text.encode()).hexdigest(),snapshot_id='S',
        locator=dict(start_line=1,end_line=1),
        gap_role_keys=['G:'+('anchor' if anchor else 'context')],optional_gap_context=optional)


def test_optional_nonmatching_context_is_removed_before_lower_ranked_required_endpoint():
    candidates=(unit('context',optional=True),unit('writer',anchor=True),unit('reader',anchor=True))
    value=CandidateSet(candidates,tuple(c['id'] for c in candidates),(),{},(),300,200,12,
                       required_endpoint_ids=('writer','reader'))
    fitted=trim_context_bundle(value,request_tokens=300,limit=200)
    assert set(fitted.selected_ids)=={'writer','reader'}
    assert fitted.required_endpoint_ids==value.required_endpoint_ids


def test_literal_gap_miss_is_optional_context_and_never_an_exact_anchor(tmp_path):
    (tmp_path/'update.go').write_text('package p\nfunc UpdateResource() { buildResourcePayload() }\n')
    gaps=[dict(id='G',summary='Update resource request handling',locator_hint=dict(path='update.go',search_term='ReplaceResource'))]
    units,audit=locate_gap_roles(compute_snapshot(tmp_path),gaps,{})
    contexts=[u for u in units if u.get('evidence_gap_refs')==['G']]
    assert contexts
    assert all(u.get('optional_gap_context') for u in contexts)
    assert all(not role.endswith(':anchor') for u in contexts for role in u['gap_role_keys'])
    assert not any(r.get('status')=='FOUND' and r.get('gap_id')=='G' for r in audit['records'])


def test_exact_anchor_is_required_even_when_also_seen_as_optional_context():
    a=unit('shared',optional=True,anchor=True);b=unit('context',optional=True)
    value=CandidateSet((a,b),('shared','context'),(),{},(),300,200,12)
    fitted=trim_context_bundle(value,request_tokens=300,limit=200)
    assert fitted.selected_ids==('shared',)


import pytest
from contractedge.retrieval.candidates import build_candidate_set, requires_gap_delivery


@pytest.mark.parametrize('reverse',[False,True])
@pytest.mark.parametrize('contribution',['exact_context','legacy_context','no_role'])
def test_ranking_merge_preserves_any_required_role_in_both_orders(reverse,contribution):
    optional=unit('shared',optional=True)
    other={**optional,'id':'alias','gap_role_keys':[] if contribution=='no_role' else ['G2:context']}
    if contribution=='exact_context':other['optional_gap_context']=False
    else:other.pop('optional_gap_context')
    inputs=[optional,other]
    if reverse:inputs.reverse()
    selected=build_candidate_set('shared',None,inputs,[],top_n=12,max_context_tokens=10000,token_counter=len)
    merged=selected.candidates[0]
    assert requires_gap_delivery(merged)==(contribution!='no_role')


@pytest.mark.parametrize('reverse',[False,True])
def test_locator_merges_secondary_exact_context_with_prior_fallback_without_demotion(tmp_path,reverse):
    (tmp_path/'update.go').write_text('package p\nfunc HeadResource() { ExactThing() }\nfunc TailResource() { ExactThing() }\n')
    gaps=[dict(id='fallback',summary='resource path',locator_hint=dict(path='update.go',search_term='UnknownResource')),
          dict(id='exact',summary='resource path',locator_hint=dict(path='update.go',search_term='ExactThing'))]
    if reverse:gaps.reverse()
    units,_=locate_gap_roles(compute_snapshot(tmp_path),gaps,{})
    tail=next(u for u in units if u.get('symbol')=='TailResource')
    assert 'exact:context' in tail['gap_role_keys']
    assert requires_gap_delivery(tail)
