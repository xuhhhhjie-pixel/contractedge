"""Real producer coordinates and prepared-byte persistence boundaries."""
import copy
import hashlib

import pytest

from contractedge.source.parsers import parse_source_units
from contractedge.model.budget import BudgetedProvider
from tests.test_request_admission import PreparedPort, request


@pytest.mark.parametrize('separator,suffix',[('\n',''),('\n','\n'),('\r\n','\r\n')])
def test_complete_yaml_root_has_inclusive_original_end_line(separator,suffix):
    text=separator.join(['apiVersion: v1','kind: ConfigMap','metadata:',
        '  name: demo','data:','  mode: strict'])+suffix
    root=next(u for u in parse_source_units('config.yaml',text,'S')
        if u['node_path']=='document[0]')
    assert root['text']==text
    assert root['content_digest']==hashlib.sha256(text.encode()).hexdigest()
    assert root['locator']['original_end_line']==root['locator']['end_line']==6
    assert root['truncated'] is False


def test_yaml_child_exclusive_mark_does_not_include_next_indented_key():
    text='spec:\n  labels:\n    app: demo\n  data:\n    mode: strict\nfooter:\n  value: done\n'
    units={u['node_path']:u for u in parse_source_units('config.yaml',text,'S')}
    assert units['document[0].spec.labels']['text']=='    app: demo\n'
    assert units['document[0].spec.data']['text']=='    mode: strict\n'
    assert units['document[0].spec']['text']=='  labels:\n    app: demo\n  data:\n    mode: strict\n'
    assert all(not u['truncated'] for u in units.values())


def test_yaml_document_end_does_not_include_next_document_marker():
    text='---\na: one\n---\nb: two\n'
    roots=[u for u in parse_source_units('config.yaml',text,'S')
        if u['node_path'] in {'document[0]','document[1]'}]
    assert [u['text'] for u in roots]==['a: one\n','b: two\n']
    assert all(not u['truncated'] for u in roots)


@pytest.mark.parametrize('limits',[{'max_lines':3},{'max_chars':15}])
def test_real_yaml_clipping_remains_declared_truncated(limits):
    text='apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: demo\ndata:\n  mode: strict\n'
    root=next(u for u in parse_source_units('config.yaml',text,'S',**limits)
        if u['node_path']=='document[0]')
    assert root['truncated'] is True
    assert root['text']!=text
    assert text.startswith(root['text'])


@pytest.mark.parametrize('unknown',[False,True])
def test_durable_transport_records_actual_prepared_body_before_dispatch(unknown):
    provider=PreparedPort()
    if unknown:provider.error=TimeoutError('unknown response')
    snapshots=[]
    budget=BudgetedProvider(provider,len,max_tokens=1000,max_seconds=60,
        on_update=lambda calls:snapshots.append(copy.deepcopy(calls)))
    value=request();quote=budget.quote_request(value)
    if unknown:
        with pytest.raises(TimeoutError):budget.complete_quoted(value,quote)
    else:budget.complete_quoted(value,quote)
    assert (provider.prepares,provider.dispatches)==(1,1)
    assert provider.seen is quote.prepared
    assert snapshots[0][0]['dispatch_state']=='not_sent'
    for calls in snapshots:
        record=calls[0]['transport_request']
        assert record['body_utf8'].encode()==quote.prepared.body
        assert record['body_bytes']==len(quote.prepared.body)
        assert record['body_sha256']==hashlib.sha256(quote.prepared.body).hexdigest()


def test_actual_prepared_bytes_own_transport_fields_and_keep_adapter_settings():
    provider=PreparedPort()
    budget=BudgetedProvider(provider,len,max_tokens=1000,max_seconds=60)
    value=request();quote=budget.quote_request(value)
    quote.prepared.audit_record=lambda:{'body_utf8':'incorrect audit copy','body_bytes':0,
        'body_sha256':'0'*64,'effective_settings':{'transport_mode':'fixture'}}
    budget.complete_quoted(value,quote)
    record=budget.calls[0]['transport_request']
    assert record['body_utf8'].encode()==provider.seen.body
    assert record['body_bytes']==len(provider.seen.body)
    assert record['body_sha256']==hashlib.sha256(provider.seen.body).hexdigest()
    assert record['effective_settings']=={'transport_mode':'fixture'}
