"""Equivalent trusted excerpts must not consume two mandatory context slots."""
from copy import deepcopy
import hashlib

import pytest

from contractedge.execution.helper_context import source_lifecycle_units
from contractedge.retrieval.candidates import build_candidate_set, extend_once
from contractedge.source.parsers import parse_source_units
from contractedge.source.snapshot import compute_snapshot


def setup_source(root):
    (root/'worker').mkdir()
    (root/'go.mod').write_text('module example.org/fixture\n')
    (root/'worker/worker.go').write_text('package worker\n'
        'type Worker struct {}\n'
        'func New() *Worker { return &Worker{} }\n'
        '// Run initializes state before waiting.\n'
        'func (w *Worker) Run() { w.initialize() }\n'
        'func (w *Worker) initialize() { freshState() }\n')
    (root/'main.go').write_text('package main\nimport w "example.org/fixture/worker"\n'
        'func main() {\nservice := w.New()\ngo service.Run()\n}\n')
    snapshot=compute_snapshot(root)
    callers=parse_source_units('main.go',(root/'main.go').read_text(),snapshot.snapshot_id)
    helpers,_=source_lifecycle_units(snapshot,callers)
    helper=next(u for u in helpers if u['symbol']=='Run')
    direct=next(u for u in parse_source_units('worker/worker.go',
        (root/'worker/worker.go').read_text(),snapshot.snapshot_id) if u.get('symbol')=='Run')
    return snapshot,helper,direct


@pytest.mark.parametrize('reverse', [False, True])
def test_commented_helper_and_exact_endpoint_share_one_required_slot(tmp_path, reverse):
    _,helper,direct=setup_source(tmp_path)
    direct.update(gap_role_keys=['G:anchor'],required_for_gap_delivery=True)
    other={**direct,'id':'OTHER','path':'consumer.go','text':'consumer contract',
           'content_digest':hashlib.sha256(b'consumer contract').hexdigest(),
           'gap_role_keys':['H:anchor']}
    units=[direct,helper] if reverse else [helper,direct]
    result=build_candidate_set('Run consumer',None,[*units,other],[],top_n=2,
        max_context_tokens=12000,token_counter=len)
    assert set(result.selected_ids)=={helper['id'],'OTHER'}
    selected=next(u for u in result.candidates if u['id']==helper['id'])
    assert selected['text']==helper['text'] and selected['locator']==helper['locator']
    assert set(selected['gap_role_keys'])==set(helper['gap_role_keys'])|{'G:anchor'}
    assert selected['precondition_review_required']
    assert direct['id'] in selected['superseded_unit_ids']


def test_actual_same_body_different_optional_column_metadata_is_not_duplicated(tmp_path):
    _,_,direct=setup_source(tmp_path)
    alias=deepcopy(direct);alias['id']='COLUMN-ALIAS';alias['locator'].pop('column_start',None)
    result=build_candidate_set('Run',None,[direct,alias],[],top_n=12,
        max_context_tokens=12000,token_counter=len)
    assert result.selected_count==1


@pytest.mark.parametrize('mutation', ['other-path','old-snapshot','different-body','bad-declaration-digest'])
def test_unproven_aliases_remain_separate(tmp_path,mutation):
    _,helper,direct=setup_source(tmp_path)
    if mutation=='other-path':direct['path']='other/worker.go'
    elif mutation=='old-snapshot':direct['snapshot_id']='OLD'
    elif mutation=='different-body':
        direct['text']=direct['text'].replace('initialize','corrupt')
        direct['content_digest']=hashlib.sha256(direct['text'].encode()).hexdigest()
    else:helper['declaration_content_digest']='f'*64
    result=build_candidate_set('Run',None,[helper,direct],[],top_n=12,
        max_context_tokens=12000,token_counter=len)
    assert result.selected_count==2


def test_extension_keeps_old_lifecycle_identity_and_new_gap_role(tmp_path):
    _,helper,direct=setup_source(tmp_path)
    first=build_candidate_set('Run',None,[helper],[],top_n=2,
        max_context_tokens=12000,token_counter=len)
    direct.update(gap_role_keys=['G:anchor'],required_for_gap_delivery=True)
    result=extend_once(first,[{'id':'G','locator_hint':{'symbol':'Run'}}],[direct],
        max_context_tokens=12000,token_counter=len)
    assert result.selected_ids==(helper['id'],)
    assert result.required_endpoint_ids==(helper['id'],)
    assert 'G:anchor' in result.candidates[0]['gap_role_keys']


def test_reference_edges_follow_a_verified_alias_to_the_retained_identity(tmp_path):
    _,helper,direct=setup_source(tmp_path)
    caller={**direct,'id':'CALLER','path':'consumer.go','text':'uses Run',
            'content_digest':hashlib.sha256(b'uses Run').hexdigest()}
    edge={'source':'CALLER','target':direct['id'],'edge_kind':'INFERRED'}
    result=build_candidate_set('Run',None,[caller,direct,helper],[edge],top_n=2,
        max_context_tokens=12000,token_counter=len)
    assert len(result.edges)==1
    assert result.edges[0]['target']==helper['id']
