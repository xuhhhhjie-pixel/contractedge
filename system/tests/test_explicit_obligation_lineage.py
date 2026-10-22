from copy import deepcopy
import importlib.util
import pytest


def module():
    assert importlib.util.find_spec("contractedge.requirement.lineage"), "Explicit lineage implementation is missing"
    from contractedge.requirement import lineage
    return lineage


def atom(name="A1", key="OB-original", statement="Retain configuration", quote="retain configuration"):
    return dict(id=name, stable_obligation_key=key, outcome_id="O1", statement=statement,
                source_refs=["requirement_text:0:80"], source_quote=quote)


def current(atoms=None, snapshot="S0", digest="public-input"):
    return dict(input_digest=digest, requirement_digest="public-requirement", snapshot_id=snapshot,
                atoms=atoms or [atom()])


def root():
    return module().validate_lineage([], {}, current())


def event(kind, parents, refs):
    return dict(kind=kind, parent_ids=parents, atom_refs=refs, rationale="Public requirement decomposition")


def test_reanchor_keeps_identity_definition_and_prior_ledger_immutable():
    prior = root()
    saved = deepcopy(prior)
    changed = atom(key="OB-new-anchor", statement="Keep the configuration", quote="configuration")
    ledger = module().validate_lineage(
        [event("REANCHOR", ["OB-original"], ["A1"])], prior, current([changed], "S1"))
    assert ledger["bindings"]["A1"] == "OB-original"
    node = ledger["nodes"]["OB-original"]
    assert node["definition"]["statement"] == "Retain configuration"
    assert "Keep the configuration" in node["aliases"]
    assert len(node["sources"]) == 2
    assert prior == saved


def test_split_preserves_parent_residual_and_does_not_inherit_child_pass():
    prior = root()
    atoms = [atom("A1", "OB-exists", "Configuration exists"), atom("A2", "OB-content", "Contents remain")]
    ledger = module().validate_lineage([event("SPLIT", ["OB-original"], ["A1", "A2"])],
                                        prior, current(atoms, "S1"))
    assert len(ledger["nodes"]["OB-original"]["children"]) == 2
    assert ledger["nodes"]["OB-original"]["residual"]
    checks = [dict(obligation_id=ledger["bindings"][a["id"]], status="PASS",
                   snapshot_id="S1", input_digest="public-input", evidence_validated=True,
                   check_coverage="ALL_OF") for a in atoms]
    projection = module().project_obligation_coverage(ledger, checks, current(snapshot="S1"))
    assert projection["OB-original"]["status"] == "UNKNOWN"
    assert all(projection[c["obligation_id"]]["status"] == "PASS" for c in checks)


def test_merge_is_a_view_and_keeps_failed_parent():
    prior = module().validate_lineage([], {}, current([atom(), atom("A2", "OB-workload", "Retain workload")]))
    ledger = module().validate_lineage([event("MERGE", ["OB-original", "OB-workload"], ["A1"])],
                                       prior, current([atom(key="OB-group", statement="Retain both")], "S1"))
    assert {"OB-original", "OB-workload"} <= ledger["nodes"].keys()
    checks = [dict(obligation_id=key, status=status, snapshot_id="S1", input_digest="public-input",
                   evidence_validated=True, check_coverage="ALL_OF")
              for key, status in [("OB-original", "PASS"), ("OB-workload", "FAIL")]]
    projection = module().project_obligation_coverage(ledger, checks, current(snapshot="S1"))
    assert projection[ledger["bindings"]["A1"]]["status"] == "FAIL"


@pytest.mark.parametrize("change", ["input", "parent", "reuse", "cycle"])
def test_invalid_history_cannot_erase_or_cross_bind_requirements(change):
    prior = root()
    events = [event("CONTINUE", ["OB-original"], ["A1"])]
    ctx = current(snapshot="S1")
    if change == "input":
        ctx["input_digest"] = "other-input"
    elif change == "parent":
        events[0]["parent_ids"] = ["OB-nonexistent"]
    elif change == "reuse":
        events.append(deepcopy(events[0]))
    else:
        prior["nodes"]["OB-original"]["children"] = ["OB-original"]
    with pytest.raises(ValueError, match="LINEAGE"):
        module().validate_lineage(events, prior, ctx)


def test_stale_or_necessary_only_pass_cannot_close_current_obligation():
    ledger = root()
    check = dict(obligation_id="OB-original", status="PASS", snapshot_id="S0",
                 input_digest="public-input", evidence_validated=True, check_coverage="ALL_OF")
    assert module().project_obligation_coverage(ledger, [check], current(snapshot="S1"))["OB-original"]["status"] == "UNKNOWN"
    check.update(snapshot_id="S1", check_coverage="NECESSARY_ONLY")
    assert module().project_obligation_coverage(ledger, [check], current(snapshot="S1"))["OB-original"]["status"] == "UNKNOWN"


def test_unchanged_anchor_never_replaces_previous_semantics():
    ledger = module().validate_lineage([], root(), current([atom(statement="Only retain its name")], "S1"))
    node = ledger["nodes"]["OB-original"]
    assert node["definition"]["statement"] == "Retain configuration"
    assert "Only retain its name" in node["aliases"]


def test_lineage_is_carried_through_plan_topomen_and_feedback_schema():
    from contractedge.requirement.normalize import bind_plan_obligations
    from contractedge.changeset.topomen import translate_for_topomen
    from contractedge.contracts.validation import _load_schema
    diagnosis = dict(obligation_atoms=[atom()], obligation_lineage=root())
    plan = dict(postconditions=[dict(id='P',obligation_refs=['A1'])])
    bind_plan_obligations(plan, diagnosis)
    assert plan['obligation_lineage'] == diagnosis['obligation_lineage']
    assert translate_for_topomen(plan,'contractedge.topomen_plan.v1')['obligation_lineage']==root()
    for contract in ['ce.diagnosis_packet.v1_1','ce.semantic_changeset.v1_1','ce.execution_feedback.v1']:
        assert 'obligation_lineage' in _load_schema(contract)['properties']


def test_narrow_alias_pass_cannot_close_original_and_original_is_sent_to_review():
    from contractedge.requirement.normalize import bind_plan_obligations
    changed=atom(statement='Only retain its name')
    ledger=module().validate_lineage([],root(),current([changed],'S1'))
    check=dict(obligation_id='OB-original',status='PASS',snapshot_id='S1',input_digest='public-input',
               evidence_validated=True,check_coverage='ALL_OF',covered_statements=['Only retain its name'])
    assert module().project_obligation_coverage(ledger,[check],current(snapshot='S1'))['OB-original']['status']=='UNKNOWN'
    plan={'postconditions':[dict(id='P',statement='Only retain its name',obligation_refs=['A1'])]}
    bind_plan_obligations(plan,dict(obligation_atoms=[changed],obligation_lineage=ledger))
    assert 'Retain configuration' in plan['postconditions'][0]['statement']


def test_new_requirement_context_flags_survive_real_strict_schema():
    from contractedge.contracts.validation import _check_schema
    from contractedge.requirement.normalize import normalize_requirement
    requirement=normalize_requirement({'requirement_text':'Keep configuration.'})
    requirement.update(lineage_required=True,operation_consistency_required=True,prior_obligation_lineage={})
    _check_schema(requirement,'ce.requirement_spec.v1_1')


def test_disputed_derivation_retains_history_without_forcing_old_interpretation():
    from contractedge.requirement.normalize import bind_plan_obligations
    old=root();changed=atom(statement='Preserve configuration only in its stated scope')
    change=event('REANCHOR',['OB-original'],['A1']);change['interpretation_disputed']=True
    ledger=module().validate_lineage([change],old,current([changed],'S1'))
    assert ledger['nodes']['OB-original']['interpretation_status']=='DISPUTED'
    assert ledger['nodes']['OB-original']['definition']==old['nodes']['OB-original']['definition']
    plan=dict(postconditions=[dict(id='P',statement=changed['statement'],obligation_refs=['A1'])])
    bind_plan_obligations(plan,dict(obligation_atoms=[changed],obligation_lineage=ledger,
        requirement_spec={'outcomes':[{'id':'O1','statement':'The complete public requirement.'}]}))
    assert plan['postconditions'][0]['statement']==changed['statement']
    assert plan['open_obligations'][0]['statement']=='The complete public requirement.'
    check=dict(obligation_id='OB-original',status='PASS',snapshot_id='S1',input_digest='public-input',
               evidence_validated=True,check_coverage='ALL_OF')
    assert module().project_obligation_coverage(ledger,[check],current(snapshot='S1'))['OB-original']['status']=='UNKNOWN'


def test_dispute_does_not_lock_previous_interpretation_as_candidate_preservation():
    from contractedge.orchestration.candidate_guard import preservation_check_ids
    from contractedge.orchestration.feedback import review_plan_with_retained
    ledger=root();ledger['nodes']['OB-original']['interpretation_status']='DISPUTED'
    retained=dict(retention_id='R',id='P',statement='An earlier incorrect interpretation',
                  stable_obligation_keys=['OB-original'],behavior_check_refs=['old-check'])
    plan=dict(obligation_lineage=ledger,postconditions=[],preserve_constraints=[],open_obligations=[])
    prior={'retained_constraints':[retained]}
    assert preservation_check_ids(plan,prior)==[]
    envelope=review_plan_with_retained(plan,prior)
    assert envelope['preserve_constraints']==[]
    assert envelope['open_obligations'], 'Disputed history remains explicitly unresolved'


def test_bound_original_statements_serialize_through_strict_changeset_contract():
    from contractedge.requirement.normalize import bind_plan_obligations
    from contractedge.contracts.validation import _check_schema
    from tests.test_contracts import valid_plan
    changed=atom(statement='Only retain its name')
    ledger=module().validate_lineage([],root(),current([changed],'S1'))
    plan=valid_plan()
    plan['preserve_constraints']=[dict(id='S1',outcome_id='O1',obligation_refs=['A1'],
        statement='Keep the configuration',current_satisfaction='OBSERVED',evidence_refs=['EV1'])]
    bind_plan_obligations(plan,dict(obligation_atoms=[changed],obligation_lineage=ledger))
    assert plan['postconditions'][0]['semantic_statements']
    assert plan['preserve_constraints'][0]['semantic_statements']
    _check_schema(plan,'ce.semantic_changeset.v1_1')
