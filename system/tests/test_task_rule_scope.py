"""Task rules bind public requirement anchors and accepted current source."""
import hashlib

import pytest

from contractedge.changeset.state_mapping import validate_state_mapping
from contractedge.changeset.topomen import translate_for_topomen
from contractedge.execution.topomen import ModelPlanExecutor, PlanRejected
from contractedge.model.provider import FixtureProvider
from contractedge.requirement.normalize import normalize_requirement
from contractedge.source.snapshot import compute_snapshot
from tests.test_contracts import valid_plan


STATES = ('A requested value with absent external state must differ from a not-requested value; '
          'an enabled external state remains present.')
METRICS = 'Write one metrics record while preserving the log format.'
OPTIONS = 'The timeout for HTTP OPTIONS discovery must be configurable and preserve the caller context.'


def state_diagnosis(raw, quotes):
    requirement = normalize_requirement({'requirement_text': raw})
    atoms = []
    for index, quote in enumerate(quotes, 1):
        clause = next(row for row in requirement['clauses'] if quote in row['text'])
        atoms.append({'id': f'A{index}', 'outcome_id': 'O1', 'statement': quote,
                      'source_quote': quote, 'source_refs': [clause['source_ref']], 'support': 'EXPLICIT'})
    return {'requirement_spec': requirement, 'obligation_atoms': atoms,
            'expected_relations': [], 'evidence_refs': [{'id': 'EV1'}]}


def test_generated_relation_cannot_create_state_mapping_requirement():
    diagnosis = state_diagnosis(METRICS, [METRICS])
    assert validate_state_mapping({}, diagnosis) == []
    diagnosis['expected_relations'] = [{'condition': 'requested but absent external state',
                                      'expected_contract': 'present and absent remain distinct'}]
    diagnosis['obligation_atoms'][0]['statement'] = 'Distinguish requested absent and present states'
    assert validate_state_mapping({}, diagnosis) == []


def test_optional_state_explanations_do_not_add_another_atom_coverage_gate():
    diagnosis = state_diagnosis(STATES + '\n' + METRICS,
        ['requested value with absent external state', 'not-requested value',
         'enabled external state remains present', METRICS])
    rows = [{'case': case, 'observed_state': value, 'obligation_refs': [atom], 'evidence_refs': ['EV1']}
            for case, value, atom in [('requested absent', 'false', 'A1'),
                                     ('not requested', 'nil', 'A2'), ('enabled present', 'true', 'A3')]]
    assert validate_state_mapping({'state_mapping': rows}, diagnosis) == []
    rows[-1]['obligation_refs'] = ['A2']
    assert validate_state_mapping({'state_mapping': rows}, diagnosis) == []


def test_quote_in_same_clause_does_not_import_unrelated_obligation():
    raw = STATES[:-1] + '; preserve the log format.'
    diagnosis = state_diagnosis(raw,
        ['requested value with absent external state', 'not-requested value',
         'enabled external state remains present', 'preserve the log format'])
    assert validate_state_mapping({}, diagnosis) == []


def test_unanchored_model_quote_does_not_claim_state_coverage():
    diagnosis = state_diagnosis(STATES + '\n' + METRICS, [METRICS])
    diagnosis['obligation_atoms'][0]['source_quote'] = 'requested value with absent external state'
    # Original-anchor validity belongs to diagnosis validation. An optional
    # table must not become a second inference pass over quoted words.
    assert validate_state_mapping({}, diagnosis) == []


def test_presence_words_without_an_explicit_distinction_do_not_require_table():
    raw = 'If a metrics file is missing, preserve the existing log format.'
    assert validate_state_mapping({}, state_diagnosis(raw, [raw])) == []


GO_SOURCE = '''package example
import (
    "context"
    "net/http"
    "time"
)
func discover(ctx context.Context, timeout time.Duration) {
    req, _ := http.NewRequestWithContext(ctx, http.MethodOptions, "/", nil)
    _ = req
}
func metrics(ctx context.Context) {
    _ = ctx
}
'''
OPTIONS_CALL = '    req, _ := http.NewRequestWithContext(ctx, http.MethodOptions, "/", nil)'
METRICS_LINE = '    _ = ctx'
FIXED = '    ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)\n    defer cancel()'


def execute_edit(tmp_path, *, requirement=METRICS, locator=(11, 13), old=METRICS_LINE,
                 new=FIXED, source=GO_SOURCE, noise=False, plan_requirement=None):
    path = tmp_path / 'main.go'
    path.write_text(source)
    if noise:
        (tmp_path / 'helper.go').write_text('// optional timeout diagnostics in unrelated helper\n')
    snapshot = compute_snapshot(tmp_path)
    semantic = valid_plan()
    semantic['snapshot_id'] = snapshot.snapshot_id
    semantic['changes'][0]['target'].update(path='main.go', snapshot_id=snapshot.snapshot_id,
        content_digest=snapshot.file_digests['main.go'],
        locator={'start_line': locator[0], 'end_line': locator[1]})
    plan = translate_for_topomen(semantic, 'contractedge.topomen_plan.v1')
    if plan_requirement:
        plan['goal'] = plan_requirement
    if noise:
        excerpt = (tmp_path / 'helper.go').read_text()
        plan['coordination_context'] = {'source_support': [{
            'id': 'HELPER1', 'kind': 'SOURCE_SPAN', 'snapshot_id': snapshot.snapshot_id,
            'locator': {'path': 'helper.go', 'start_line': 1, 'end_line': 1},
            'excerpt': excerpt, 'content_digest': hashlib.sha256(excerpt.encode()).hexdigest()}]}
    provider = FixtureProvider({'edits': [{'change_id': 'C1', 'path': 'main.go', 'operation': 'MODIFY',
                                         'old_text': old, 'new_text': new}]})
    executor = ModelPlanExecutor(provider, lambda text: len(text.split()),
        {'model': 'fixture', 'max_context_tokens': 16000, 'execution_max_output_tokens': 2048})
    result = executor.execute(plan, tmp_path, {'allowed_paths': ['main.go']}, requirement=requirement)
    return result, path.read_text()


def test_unrelated_readonly_support_does_not_activate_options_rule(tmp_path):
    result, actual = execute_edit(tmp_path, noise=True)
    assert result['status'] == 'APPLIED' and FIXED in actual


def test_original_options_requirement_rejects_fixed_deadline_at_real_branch(tmp_path):
    with pytest.raises(PlanRejected, match='TIMEOUT_CONTRACT_VIOLATION'):
        execute_edit(tmp_path, requirement=OPTIONS, locator=(7, 10), old=OPTIONS_CALL,
                     new=FIXED + '\n' + OPTIONS_CALL)
    assert (tmp_path / 'main.go').read_text() == GO_SOURCE


def test_original_options_requirement_accepts_configured_caller_deadline(tmp_path):
    configured = '    ctx, cancel := context.WithTimeout(ctx, timeout)\n    defer cancel()\n' + OPTIONS_CALL
    result, actual = execute_edit(tmp_path, requirement=OPTIONS, locator=(7, 10), old=OPTIONS_CALL,
                                  new=configured)
    assert result['status'] == 'APPLIED' and configured in actual


def test_options_task_does_not_constrain_an_unrelated_accepted_function(tmp_path):
    result, _ = execute_edit(tmp_path, requirement=OPTIONS, plan_requirement=OPTIONS)
    assert result['status'] == 'APPLIED'


def test_model_plan_text_cannot_override_original_requirement(tmp_path):
    result, _ = execute_edit(tmp_path, requirement=METRICS, plan_requirement=OPTIONS,
        locator=(7, 10), old=OPTIONS_CALL, new=FIXED + '\n' + OPTIONS_CALL)
    assert result['status'] == 'APPLIED'


def test_comments_in_an_accepted_target_are_not_options_calls(tmp_path):
    source = GO_SOURCE.replace('    _ = ctx', '    // http.MethodOptions\n    _ = ctx')
    result, _ = execute_edit(tmp_path, requirement=OPTIONS, plan_requirement=OPTIONS,
                             locator=(11, 14), source=source)
    assert result['status'] == 'APPLIED'


def test_timeout_words_in_another_requirement_clause_do_not_expand_rule(tmp_path):
    requirement = 'Make worker timeout configurable. Preserve HTTP OPTIONS behavior.'
    result, _ = execute_edit(tmp_path, requirement=requirement, plan_requirement=requirement,
        locator=(7, 10), old=OPTIONS_CALL, new=FIXED + '\n' + OPTIONS_CALL)
    assert result['status'] == 'APPLIED'


def test_edit_outside_the_accepted_options_span_does_not_inherit_rule(tmp_path):
    result, _ = execute_edit(tmp_path, requirement=OPTIONS, plan_requirement=OPTIONS, locator=(7, 10))
    assert result['status'] == 'APPLIED'


def test_deadline_examples_in_new_comments_are_not_executable_changes(tmp_path):
    change = ('    // context.WithTimeout(context.Background(), 3*time.Second) is unrelated\n'
              '    ctx, cancel := context.WithTimeout(ctx, timeout)\n    defer cancel()\n' + OPTIONS_CALL)
    result, _ = execute_edit(tmp_path, requirement=OPTIONS, plan_requirement=OPTIONS,
                             locator=(7, 10), old=OPTIONS_CALL, new=change)
    assert result['status'] == 'APPLIED'


def test_options_import_alias_binds_to_actual_standard_library(tmp_path):
    source = GO_SOURCE.replace('"net/http"', 'web "net/http"').replace('http.', 'web.')
    old = OPTIONS_CALL.replace('http.', 'web.')
    with pytest.raises(PlanRejected, match='TIMEOUT_CONTRACT_VIOLATION'):
        execute_edit(tmp_path, requirement=OPTIONS, locator=(7, 10), source=source,
                     old=old, new=FIXED + '\n' + old)


def test_generic_stage_prompts_do_not_prescribe_an_options_repair(tmp_path):
    from contractedge.diagnosis.prompt import SYSTEM_PROMPT
    from contractedge.changeset.prompt import CHANGESET_SYSTEM_PROMPT
    from contractedge.execution.topomen import build_execution_request
    assert 'OPTIONS' not in SYSTEM_PROMPT and 'MethodOptions' not in SYSTEM_PROMPT
    assert 'OPTIONS' not in CHANGESET_SYSTEM_PROMPT and 'MethodOptions' not in CHANGESET_SYSTEM_PROMPT
    (tmp_path / 'main.go').write_text(GO_SOURCE)
    snapshot = compute_snapshot(tmp_path)
    plan = valid_plan()
    plan['snapshot_id'] = snapshot.snapshot_id
    plan['changes'][0]['target'].update(path='main.go', snapshot_id=snapshot.snapshot_id,
        content_digest=snapshot.file_digests['main.go'], locator={'start_line': 11, 'end_line': 13})
    request = build_execution_request(translate_for_topomen(plan, 'contractedge.topomen_plan.v1'),
        tmp_path, {'allowed_paths': ['main.go']}, lambda text: len(text.split()),
        {'model': 'fixture', 'max_context_tokens': 16000}, requirement=METRICS)
    assert 'OPTIONS' not in request.messages[0]['content']
