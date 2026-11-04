"""Localize a wrong source identity without accepting a caller quote as helper evidence."""
from copy import deepcopy
import hashlib
from types import SimpleNamespace
import pytest
from contractedge.changeset.validate import _prerequisite_input_issues


def inputs():
    def span(identity,path,text):
        return dict(id=identity,kind='SOURCE_SPAN',snapshot_id='CURRENT',excerpt=text,
            content_digest=hashlib.sha256(text.encode()).hexdigest(),
            locator=dict(path=path,start_line=10,end_line=15))
    helper=span('HELPER-A','runtime/utils.go','// SetID establishes identity.\nfunc SetID(input Nodes) error {\n return annotate(input)\n}\n')
    caller=span('CALLER-B','runtime/run.go','if hasSelectors {\n return SetID(input)\n}\n')
    plan={'precondition_review':[dict(source_evidence_ref='HELPER-A',
        establishment=dict(mode='PLANNED_CHANGE',source_quote='if hasSelectors {'))]}
    return plan,SimpleNamespace(snapshot_id='CURRENT'),[{'source_evidence_ref':'HELPER-A'}],[helper,caller]


def test_wrong_caller_quote_identifies_expected_helper_and_other_delivered_source():
    plan,diag,prereqs,records=inputs();before=deepcopy(plan)
    issues=_prerequisite_input_issues(plan,diag,prereqs,records)
    assert len(issues)==1 and issues[0].code=='PRECONDITION_ESTABLISHMENT_INVALID'
    message=issues[0].message
    assert 'runtime/utils.go' in message and 'func SetID(input Nodes) error {' in message
    assert 'CALLER-B' in message and 'runtime/run.go' in message
    assert 'not proof' in message and plan==before


@pytest.mark.parametrize('fault',['old_snapshot','wrong_digest','wrong_kind'])
def test_untrusted_helper_has_no_source_quote_suggestion(fault):
    plan,diag,prereqs,records=inputs()
    if fault=='old_snapshot':records[0]['snapshot_id']='OLD'
    elif fault=='wrong_digest':records[0]['content_digest']='wrong'
    else:records[0]['kind']='PUBLIC_OBSERVATION'
    issues=_prerequisite_input_issues(plan,diag,prereqs,records)
    assert any(i.code=='PRECONDITION_ESTABLISHMENT_INVALID' for i in issues)
    assert not any('func SetID(input Nodes) error {' in i.message for i in issues)


def test_exact_helper_quote_still_passes_without_automatic_plan_rewriting():
    plan,diag,prereqs,records=inputs()
    plan['precondition_review'][0]['establishment']['source_quote']='func SetID(input Nodes) error {'
    assert not _prerequisite_input_issues(plan,diag,prereqs,records)
