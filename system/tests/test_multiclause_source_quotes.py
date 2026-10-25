"""A verbatim predicate may span consecutive, explicitly cited requirement clauses."""
import pytest
from contractedge.requirement.normalize import bind_obligation_sources,normalize_requirement


def payload(raw,refs,quote):
    req=normalize_requirement({'requirement_text':raw})
    return {'obligation_atoms':[{'id':'A1','outcome_id':'O1','statement':quote,'source_refs':refs,'source_quote':quote}]},req


def test_exact_quote_across_cited_sentences_is_valid_with_whitespace_gap():
    raw='Keep keys. Remove extras.'
    p,r=payload(raw,['requirement_text:0:10','requirement_text:11:25'],raw)
    # Exact positions deliberately computed from actual public clauses.
    p['obligation_atoms'][0]['source_refs']=[c['source_ref'] for c in r['clauses']]
    bind_obligation_sources(p,r)
    assert p['obligation_atoms'][0]['stable_obligation_key'].startswith('OB-')


def test_uncited_intervening_requirement_cannot_be_swallowed_by_a_long_quote():
    raw='Keep keys. Never delete state. Remove extras.'
    p,r=payload(raw,[],raw);clauses=r['clauses'];p['obligation_atoms'][0]['source_refs']=[clauses[0]['source_ref'],clauses[2]['source_ref']]
    with pytest.raises(ValueError,match='OBLIGATION_QUOTE_MISMATCH'):bind_obligation_sources(p,r)


def test_joined_or_rephrased_quote_still_rejected():
    raw='Keep keys. Remove extras.'
    p,r=payload(raw,['requirement_text:0:24'],'Keep keys.Remove extras.')
    with pytest.raises(ValueError,match='OBLIGATION_QUOTE_MISMATCH'):bind_obligation_sources(p,r)


def test_repeated_quote_must_have_an_occurrence_covered_by_the_actual_refs():
    raw='Keep keys. Keep keys. Remove extras.'
    p,r=payload(raw,[],'Keep keys. Remove extras.');p['obligation_atoms'][0]['source_refs']=[c['source_ref'] for c in r['clauses'][1:]]
    bind_obligation_sources(p,r)
    p['obligation_atoms'][0]['source_refs']=[r['clauses'][0]['source_ref']]
    with pytest.raises(ValueError,match='OBLIGATION_QUOTE_MISMATCH'):bind_obligation_sources(p,r)


def test_single_span_identity_remains_unchanged():
    import hashlib,json
    raw='Keep keys.';refs=['requirement_text:0:10'];p,r=payload(raw,refs,raw);bind_obligation_sources(p,r)
    body=[hashlib.sha256(raw.encode()).hexdigest(),'O1',refs,raw,'']
    assert p['obligation_atoms'][0]['stable_obligation_key']=='OB-'+hashlib.sha256(json.dumps(body,ensure_ascii=False).encode()).hexdigest()[:24]
