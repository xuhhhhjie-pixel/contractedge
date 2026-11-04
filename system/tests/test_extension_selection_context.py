"""Selection-stage regressions from public Cloudwatch and independent fixtures."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from contractedge.diagnosis.evidence import source_context_new,source_context_offer,source_context_for_candidates
from contractedge.retrieval.candidates import build_candidate_set,extend_once
from contractedge.retrieval.gap_delivery import audit_gap_delivery
from contractedge.source.parsers import parse_source_units,_unit

SNAPSHOT='selection-context-fixture'
GAP={'id':'G','locator_hint':{'path':'pkg/manager.go','symbol':'Update'}}


def record(unit):
    return dict(id='SRC-'+unit['id'],kind='SOURCE_SPAN',snapshot_id=SNAPSHOT,
        locator={'path':unit['path'],**unit['locator']},excerpt=unit['text'],
        content_digest=unit['content_digest'],origin='selected candidate '+unit['id'])


def textunit(path,text,**extra):
    return _unit(SNAPSHOT,path,text,0,len(text),'FIXTURE',unit_kind='SOURCE_TEXT',
        max_chars=100000,extra=extra)


def one_remaining():
    root=parse_source_units('pkg/manager.go',
        'package pkg\nfunc (rm *Manager) Update() {\n rm.sdkFind()\n}\n',SNAPSHOT)[0]
    fillers=[textunit(f'old/{i}.txt',f'prior {i}') for i in range(11)]
    base=build_candidate_set('update tags TagResource',None,[root,*fillers],[],
        top_n=12,max_context_tokens=100000,token_counter=len)
    active=source_context_new(dict(run_id='r',case_id='c',input_digest='i',front_id='f'))
    active=source_context_offer(active,[record(root)],producer='initial',candidate_ids=[root['id']])
    active=source_context_offer(active,[record(u) for u in fillers],producer='extension',
        candidate_ids=[u['id'] for u in fillers])
    return base,active


def extend(base,active,proposals):
    return extend_once(base,[GAP],proposals,max_context_tokens=100000,
        token_counter=len,source_context=active)


def test_existing_receiver_context_guides_new_slot_without_reranking_base():
    base,active=one_remaining()
    callee=parse_source_units('pkg/sdk.go',
        'package pkg\nfunc (rm *Manager) sdkFind() {\n read()\n}\n',SNAPSHOT)[0]
    doc=textunit('docs/tags.md','update tags TagResource update function behavior and tags')
    grown=extend(base,active,[callee,doc])
    assert grown.candidates[:len(base.candidates)]==base.candidates
    assert [u['id'] for u in grown.candidates[len(base.candidates):]]==[callee['id']]


def test_optional_bounded_window_keeps_exact_text_coordinates_and_truncation():
    base,active=one_remaining()
    source='func sdkFind() {\n read()\n tail()\n}\n'
    prefix=parse_source_units('pkg/prefix.go',source,SNAPSHOT,max_lines=2)[0]
    assert prefix['truncated']
    grown=extend(base,active,[prefix])
    assert grown.candidates[-1]==prefix
    assert grown.candidates[:len(base.candidates)]==base.candidates
    assert source[prefix['locator']['start_char']:prefix['locator']['end_char']]==prefix['text']
    draft=source_context_for_candidates(active,SimpleNamespace(case_id='c',declared_artifacts=(),public_evidence=()),grown,SNAPSHOT)
    assert next(r for r in draft['records'] if r['id']=='SRC-'+prefix['id'])['excerpt']==prefix['text']


@pytest.mark.parametrize('limits',[{'max_lines':2},{'max_chars':18}])
def test_required_truncated_endpoint_is_still_incomplete(limits):
    base,active=one_remaining()
    prefix=parse_source_units('pkg/prefix.go','func Required() {\n read()\n tail()\n}\n',SNAPSHOT,**limits)[0]
    prefix={**prefix,'required_for_gap_delivery':True,'gap_role_keys':['G:anchor'],'evidence_gap_refs':['G']}
    grown=extend(base,active,[prefix])
    assert prefix['id'] not in grown.selected_ids
    assert prefix['id'] in grown.required_endpoint_ids
    receipt=audit_gap_delivery([{'required_unit_ids':list(grown.required_endpoint_ids)}],
        list(grown.candidates),json.dumps(list(grown.candidates)),drop_reasons=grown.drop_reasons)
    assert receipt['status']=='INCOMPLETE'


@pytest.mark.parametrize('unicode_body',[False,True])
def test_byte_rejection_does_not_consume_last_record_slot(unicode_body):
    base,active=one_remaining()
    large=textunit('docs/large.md','TagResource '+('é' if unicode_body else 'x')*24001,anchor_priority=4)
    small=textunit('pkg/small.txt','update tags')
    grown=extend(base,active,[large,small])
    assert grown.candidates[:len(base.candidates)]==base.candidates
    assert [u['id'] for u in grown.candidates[len(base.candidates):]]==[small['id']]
    assert grown.drop_reasons[large['id']]=='CUMULATIVE_SOURCE_ADMISSION'
    assert small['id'] not in grown.drop_reasons


@pytest.mark.parametrize('alias',[False,True])
def test_adopted_optional_prefix_cannot_be_promoted_to_complete_required_endpoint(alias):
    prefix=parse_source_units('pkg/prefix.go','func Required() {\n read()\n tail()\n}\n',SNAPSHOT,max_lines=2)[0]
    base=build_candidate_set('Required',None,[prefix],[],top_n=12,max_context_tokens=100000,token_counter=len)
    active=source_context_offer(source_context_new(dict(run_id='r',case_id='c',input_digest='i',front_id='f')),
        [record(prefix)],producer='initial',candidate_ids=[prefix['id']])
    proposal={**prefix,'id':prefix['id']+'-alias' if alias else prefix['id'],
        'required_for_gap_delivery':True,'gap_role_keys':['G:anchor'],'evidence_gap_refs':['G']}
    with pytest.raises(ValueError,match='Incomplete adopted source'):
        extend(base,active,[proposal])
    assert active['records']==[record(prefix)]
