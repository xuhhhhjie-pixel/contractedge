import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from contractedge.changeset.validate import validate_semantic_changeset
from contractedge.contracts.validation import canonical_digest, parse_method_input, validate_diagnosis
from contractedge.execution.public import BenchmarkPublicRunner
from contractedge.execution.support import support_error
from contractedge.orchestration.feedback import review_plan_with_retained
from tests.test_contracts import valid_diagnosis, valid_method_input, valid_plan


def test_same_identity_retained_pending_and_new_plan_union_behavior_requirements():
    base = {'statement': 'preserve configuration', 'requirement_refs': ['O1'],
            'verification_kind': 'PUBLIC_BEHAVIOR'}
    prior = {'to_snapshot': 's0', 'retained_constraints': [{**base, 'source_snapshot': 's0',
             'behavior_check_refs': ['p:owner', 'p:final']}],
             'pending_obligations': [{**base, 'id': 'old', 'behavior_check_refs': ['p:owner']}]}
    plan = {'postconditions': [{**base, 'id': 'new', 'behavior_check_refs': ['p:control']}],
            'preserve_constraints': [], 'open_obligations': []}
    claim = review_plan_with_retained(plan, prior)['postconditions'][0]
    assert set(claim['behavior_check_refs']) == {'p:owner', 'p:final', 'p:control'}
    observation = {'id': 'obs', 'evidence_kind': 'PUBLIC_BEHAVIOR',
                   'behavior_check': {'id': 'p:owner', 'status': 'PASS'}}
    assert support_error(claim, {'status': 'PASS', 'evidence': [{'observation_id': 'obs'}]},
                         [observation]) == 'METHOD_BEHAVIOR_CHECKS_INCOMPLETE'


def test_modify_branch_requires_a_selected_unit_span_not_combined_envelope():
    raw = valid_diagnosis()
    raw['units'].append({**raw['units'][1], 'id': 'U3', 'locator_refs': ['EV3']})
    raw['evidence_refs'][1]['locator'].update(start_line=1, end_line=8)
    raw['evidence_refs'].append({**raw['evidence_refs'][1], 'id': 'EV3',
        'locator': {'path': 'templates/serviceaccount.yaml', 'start_line': 40, 'end_line': 48}})
    packet = validate_diagnosis(raw, parse_method_input(valid_method_input()), None)
    plan = valid_plan(); plan['diagnosis_digest'] = canonical_digest(packet.raw)
    target = plan['changes'][0]['target']
    target.update(unit_refs=['U2', 'U3'], locator={'start_line': 1, 'end_line': 48})
    branch = {'source_evidence_id': 'SRC-middle', 'path': target['path'],
              'locator': {'start_line': 15, 'end_line': 30}}
    plan['production_path_review'] = [{'source_evidence_id': branch['source_evidence_id'],
        'disposition': 'MODIFY', 'rationale': 'Supposed middle branch coverage',
        'change_refs': ['C1'], 'open_obligation_refs': []}]
    result = validate_semantic_changeset(plan, packet, None, frontier=[branch])
    assert 'PRODUCTION_BRANCH_CHANGE_INVALID' in [e.code for e in result.errors]
    branch['locator'] = {'start_line': 2, 'end_line': 6}
    assert validate_semantic_changeset(plan, packet, None, frontier=[branch]).valid


def test_prepared_probe_selection_survives_candidate_file_rename():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary); tools = root/'tools'; tools.mkdir(); public = root/'public'; public.mkdir()
        for name in ('prepare_case.py', 'collect_patch.py', 'launch_public_case.py'):
            (tools/name).write_text('# test launcher')
        workspace = root/'workspace'; workspace.mkdir(); source = workspace/'source'; source.mkdir()
        inputs = workspace/'inputs'; inputs.mkdir()
        (source/'pkg/lib/kptops').mkdir(parents=True); (source/'pkg/fn').mkdir(parents=True)
        (source/'pkg/lib/kptops/render.go').write_text('package kptops\n')
        entry = source/'pkg/fn/eval.go'; entry.write_text('package fn\n')
        (workspace/'workspace.json').write_text(json.dumps({'case_id':'CASE', 'source_root':str(source),
            'inputs_root':str(inputs), 'private_materials_loaded':False}))
        def process(argv, log, timeout):
            log.mkdir(); (log/'tool.stdout').write_text(''); (log/'tool.stderr').write_text('')
            return {'returncode':0, 'timed_out':False}
        runner = BenchmarkPublicRunner(public, tools, lock_root=root/'locks',
            query='configPath functionConfig owning later render', method_probe_profiles=['kpt-configpath-v1'])
        with patch('contractedge.execution.public.run_tool', side_effect=process):
            runner.prepare('CASE', workspace, root/'prepare')
        entry.rename(entry.with_name('renamed.go'))
        with patch('contractedge.execution.public.run_tool', side_effect=process), patch(
             'contractedge.execution.public.cleanup_owned_containers', return_value={'status':'OWNED_CONTAINERS_CLOSED'}), patch(
             'contractedge.execution.probes.run_method_probes', return_value={'status':'PROBE_ERROR', 'probes':[]}) as probes:
            result = runner.observe(workspace, root/'observation')
        assert probes.call_count == 1
        assert probes.call_args.args[3][0]['id'] == 'kpt-configpath-v1'
        assert result['method_probe_status'] == 'PROBE_ERROR'
