from contractedge.source.snapshot import compute_snapshot
from contractedge.source.units import extract_units


def test_long_relevant_public_entry_reaches_initial_pool_without_waiting_for_model_gap(tmp_path):
    text='package main\nfunc run() {\n'+' flags.String("option", "value")\n'*180+' reloadConfig(func() { state.Stop(); state = newGroups() })\n}\n'
    (tmp_path/'main.go').write_text(text)
    snap=compute_snapshot(tmp_path)
    units=extract_units(snap,{'seed_paths':['main.go']},query='Active groups remain visible after a valid reload',
        focused_paths=['main.go'],max_units=1,max_extension_files=0)
    assert len(units)==1 and units[0].get('symbol')=='run'
    assert not units[0]['truncated'] and 'state = newGroups()' in units[0]['text']
    assert units[0]['locator']['end_line']>180 and units[0].get('expanded_from_unit_id')


def test_unfocused_or_irrelevant_declaration_keeps_existing_bound(tmp_path):
    (tmp_path/'main.go').write_text('package main\nfunc run() {\n'+' work()\n'*180+'}\n')
    snap=compute_snapshot(tmp_path)
    for focus,query in [([], 'work'),(['main.go'],'unrelated requirement')]:
        units=extract_units(snap,{'seed_paths':['main.go']},query=query,focused_paths=focus,
            max_units=1,max_extension_files=0)
        assert units[0]['truncated'] and not units[0].get('expanded_from_unit_id')


def test_causal_correction_points_to_supplied_source_without_inventing_causal_support():
    import json
    from contractedge.orchestration.runner import _format_validation_feedback,_append_diagnosis_retry_turn
    correction=_format_validation_feedback(ValueError('CAUSAL_CLAIM_UNSUPPORTED at violations'),{})
    messages=[dict(role='user',content=json.dumps(dict(evidence_catalog=[dict(id='SRC-main',kind='SOURCE_SPAN',
        locator=dict(path='main.go',start_line=130,end_line=600))])))]
    retry=_append_diagnosis_retry_turn(messages,'{}',correction)
    assert 'SRC-main' in retry[-1]['content'] and 'EVIDENCE_GAP' in retry[-1]['content']
    assert 'availability alone is not causal support' in retry[-1]['content']
