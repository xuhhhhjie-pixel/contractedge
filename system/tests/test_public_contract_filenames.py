"""Support public execution contracts declared by filename in frozen manifests."""
import json,hashlib
import pytest
from contractedge.dataset.benchmark import execution_contract,public_execution_capabilities


def manifest(root,files):
    digests={}
    for name,content in files.items():
        data=json.dumps(content).encode();(root/name).write_bytes(data);digests[name]=hashlib.sha256(data).hexdigest()
    return {'files':list(files),'file_sha256':digests}


def test_manifest_pinned_execution_contract_filename_is_used(tmp_path):
    value={'runtime_image':'trusted@sha256:'+'a'*64,'editable_paths':['src/**'],'scenarios':[{'name':'public-observe','argv':['observe']}],'observation_limit':'local public only'}
    m=manifest(tmp_path,{'execution_contract.json':value})
    assert execution_contract(tmp_path,{},m)==value
    capabilities=public_execution_capabilities(tmp_path,{},m)
    assert capabilities['source']['path']=='execution_contract.json'
    assert capabilities['source']['sha256']==m['file_sha256']['execution_contract.json']


def test_explicit_public_contract_keeps_precedence(tmp_path):
    m=manifest(tmp_path,{'environment.json':{'tag':'legacy'},'execution_contract.json':{'tag':'fallback'},'custom.json':{'tag':'explicit'}})
    assert execution_contract(tmp_path,{'execution_contract':'custom.json'},m)=={'tag':'explicit'}
    assert execution_contract(tmp_path,{},m)=={'tag':'legacy'}


def test_fallback_is_not_read_outside_manifest_or_after_hash_corruption(tmp_path):
    (tmp_path/'execution_contract.json').write_text('{"runtime_image":"unregistered"}')
    assert execution_contract(tmp_path,{}, {'files':[], 'file_sha256':{}})=={}
    m=manifest(tmp_path,{'execution_contract.json':{'tag':'public'}})
    (tmp_path/'execution_contract.json').write_text('{"tag":"changed"}')
    with pytest.raises(ValueError,match='hash mismatch'):execution_contract(tmp_path,{},m)
