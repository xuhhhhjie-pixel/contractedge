from contractedge.diagnosis.prompt import _case_output_schema


def test_keyed_single_outcome_cannot_express_partial_ready():
    r={'outcomes':[{'id':'O1','statement':'Restore one outcome.'}]}
    s=_case_output_schema(r,[])
    assert 'PARTIAL_READY' not in s['properties']['status']['enum']
    assert {'READY','EVIDENCE_GAP','NO_SUPPORTED_VIOLATION'}<=set(s['properties']['status']['enum'])


def test_multiple_outcomes_retain_partial_ready_for_distinct_unresolved_outcome():
    r={'outcomes':[{'id':'O1','statement':'Restore first.'},{'id':'O2','statement':'Keep second.'}]}
    assert 'PARTIAL_READY' in _case_output_schema(r,[])['properties']['status']['enum']
