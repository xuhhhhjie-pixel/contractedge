"""Bounded multi-token public locators retain exact identifier semantics."""
import pytest
from contractedge.source.snapshot import compute_snapshot
from contractedge.source.units import extract_units, extract_gap_extension_units


def fixture(root, text=None):
    (root/'docs.md').write_text('Queue lifecycle background.\n')
    (root/'worker').mkdir()
    source=text or 'package worker\nfunc ProcessItem() { consume(obj.NextPrivateKeySecretName) }\n'
    (root/'worker/controller.go').write_text(source)
    (root/'other.go').write_text(source)
    snap=compute_snapshot(root)
    seeds=extract_units(snap,{'seed_paths':['docs.md']})
    return snap,seeds


def test_directory_plus_multiple_terms_locates_the_named_implementation_only(tmp_path):
    snap,seeds=fixture(tmp_path)
    gap=dict(id='G1',locator_hint=dict(path='worker',search_term='NextPrivateKeySecretName CertificateRequest CSR compare'))
    units,audit=extract_gap_extension_units(snap,['docs.md'],seeds,[gap])
    assert [(u['path'],u.get('symbol')) for u in units]==[('worker/controller.go','ProcessItem')]
    assert units[0]['evidence_gap_refs']==['G1']
    assert units[0]['scope_extension']=='EVIDENCE_GAP_DIRECTORY_PATH'
    assert any(h.get('parent_query')==gap['locator_hint']['search_term'] for h in audit['queries'])


def test_decomposed_tokens_keep_case_boundary_and_same_gap_scope(tmp_path):
    snap,seeds=fixture(tmp_path,'''package worker
func WrongCase() { consume(obj.nextPrivateKeySecretName) }
func Substring() { consume(obj.PreviousNextPrivateKeySecretName) }
func Correct() { consume(obj.NextPrivateKeySecretName) }
''')
    units,_=extract_gap_extension_units(snap,['docs.md'],seeds,[dict(id='G',locator_hint=dict(
        path='worker',search_term='NextPrivateKeySecretName handling compare'))],max_units=1)
    assert [(u['path'],u.get('symbol')) for u in units]==[('worker/controller.go','Correct')]


@pytest.mark.parametrize('query',['compare key state','CSR terminal failure','newValue processing','missing resources'])
def test_generic_prose_does_not_become_a_new_identifier_locator(tmp_path,query):
    snap,seeds=fixture(tmp_path)
    units,_=extract_gap_extension_units(snap,['docs.md'],seeds,[dict(id='G',locator_hint=dict(search_term=query))])
    assert not units


@pytest.mark.parametrize('limit',['files','bytes','units'])
def test_token_decomposition_does_not_increase_read_or_candidate_limits(tmp_path,limit):
    snap,seeds=fixture(tmp_path)
    seed_paths=['docs.md']
    if limit=='files':
        (tmp_path/'seed.go').write_text('package seed\nfunc Anchor() {}\n')
        snap=compute_snapshot(tmp_path);seed_paths=['seed.go']
        seeds=extract_units(snap,{'seed_paths':seed_paths})
    kwargs={'max_files':1} if limit=='files' else {'max_search_bytes':1} if limit=='bytes' else {'max_units':0}
    # The existing seed is visited first for a pathless query.
    units,_=extract_gap_extension_units(snap,seed_paths,seeds,[dict(id='G',locator_hint=dict(
        search_term='NextPrivateKeySecretName CSR comparison'))],**kwargs)
    assert not units


def test_pool_cap_keeps_the_declaration_covering_both_explicit_identifiers(tmp_path):
    snap,seeds=fixture(tmp_path,'''package worker
func Constructor() { configure(CertificateRequest) }
func ProcessItem() { compare(CertificateRequest, obj.NextPrivateKeySecretName) }
''')
    units,_=extract_gap_extension_units(snap,['docs.md'],seeds,[dict(id='G',locator_hint=dict(
        path='worker',search_term='NextPrivateKeySecretName CertificateRequest CSR compare'))],max_units=1)
    assert [u.get('symbol') for u in units]==['ProcessItem']


@pytest.mark.parametrize('suffix,text',[
    ('.go','package p\nfunc Constructor() { use(IncomingRequestToken) }\nfunc ProcessRecord() { compare(IncomingRequestToken, CurrentKeySecretName) }\n'),
    ('.py','def constructor():\n    use(IncomingRequestToken)\ndef ProcessRecord():\n    compare(IncomingRequestToken, CurrentKeySecretName)\n'),
    ('.ts','function constructor() { use(IncomingRequestToken); }\nfunction ProcessRecord() { compare(IncomingRequestToken, CurrentKeySecretName); }\n'),
])
def test_pre_cap_query_coverage_works_across_supported_source_languages(tmp_path,suffix,text):
    (tmp_path/'docs.md').write_text('Background.\n')
    (tmp_path/'worker').mkdir();(tmp_path/('worker/consumer'+suffix)).write_text(text)
    snapshot=compute_snapshot(tmp_path);seeds=extract_units(snapshot,{'seed_paths':['docs.md']})
    units,_=extract_gap_extension_units(snapshot,['docs.md'],seeds,[dict(id='G',locator_hint=dict(
        path='worker',search_term='CurrentKeySecretName IncomingRequestToken comparison'))],max_units=1)
    assert [u.get('symbol') for u in units]==['ProcessRecord']
