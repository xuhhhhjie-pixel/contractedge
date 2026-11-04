"""Run the fixed 4 x 6 public developer panel through production repair_case.

All responses are independent, finite synthetic fixtures. This is engineering
evidence with no paid HTTP, benchmark estimate or claim of Full superiority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

from contractedge.contracts.validation import canonical_digest
from contractedge.dataset.benchmark import file_sha
from contractedge.development import public_demo
from contractedge.development.method_panel_fixtures import (CASES, MODES, PROBE_ID,
    PanelRunner, case_definition, freeze_panel_case, synthetic_edits, check_capabilities)
from contractedge.experiments.modes import INTENTS
from contractedge.execution.consumers.direct import ModifyOnlyConsumer
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.repair import repair_case, write_json


class PanelProvider:
    """Author each mode's response only when its actual stage is requested."""
    def __init__(self, case_id, mode, runner):
        self.case = case_definition(case_id)
        self.mode, self.runner = mode, runner
        self.requests = []
        self.executions = 0

    def complete_json(self, request):
        started = time.perf_counter()
        self.requests.append(request)
        version = request.prompt_version
        body = json.loads(request.messages[1]['content'])
        if version.startswith('diagnosis.'):
            if self.mode not in {'full', 'without_changeset'}:
                raise AssertionError('Removed relation stage was dispatched')
            source = next(e for e in body['evidence_catalog']
                if e['kind'] == 'SOURCE_SPAN' and e['locator']['path'] == 'app.py')
            observation = next(e for e in body['evidence_catalog'] if e['kind'] == 'PUBLIC_OBSERVATION'
                and json.loads(e['excerpt'])['status'] == 'FAIL')
            outcome = body['requirement_spec']['outcomes'][0]
            # The body is authored separately on this mode's actual request;
            # no Full trace or plan is generated for a relation-only run.
            payload = {'requirement_horizon': [outcome['id']],
                'observed_boundary': 'fixed finite public requirement profile',
                'inferred_causal_path': ['public configuration reaches stale or constant production output'],
                'unverified_outcomes': [], 'units': [
                    {'id': 'U1', 'role': 'configuration input', 'kind': 'configuration',
                        'semantic_role_class': 'CONFIGURATION_INPUT', 'existence': 'PRESENT',
                        'locator_refs': [], 'requirement_refs': [outcome['id']]},
                    {'id': 'U2', 'role': 'production output consumers', 'kind': 'source',
                        'semantic_role_class': 'PRODUCTION_CONSUMER', 'existence': 'PRESENT',
                        'locator_refs': [source['id']], 'requirement_refs': [outcome['id']]}],
                'obligation_atoms': [{'id': 'A1', 'outcome_id': outcome['id'],
                    'statement': outcome['statement'], 'source_refs': outcome['source_refs'], 'support': 'EXPLICIT'}],
                'expected_relations': [{'id': 'E1', 'source_unit_ids': ['U1'], 'target_unit_ids': ['U2'],
                    'relation_form': 'DIRECT_EDGE', 'shared_constraint_reason': None, 'mechanism': 'BINDING',
                    'condition': self.case['requirement'], 'expected_contract': outcome['statement'],
                    'requirement_refs': [outcome['id']], 'obligation_refs': ['A1'], 'support_refs': [source['id']]}],
                'violations': [{'id': 'V1', 'relation_id': 'E1',
                    'broken_behavior': 'public target profile fails on current production output',
                    'causal_explanation': 'the source returns a constant or retains the old state instead of consuming the selected value',
                    'evidence_refs': [observation['id'], source['id']],
                    'support_basis': 'OBSERVED_AND_STATIC', 'causal_status': 'SUPPORTED'}],
                'outcome_coverage': [{'outcome_id': outcome['id'], 'repair_supported': ['V1'],
                    'preserve_required': [], 'preserve_current_satisfaction': [], 'unresolved': []}],
                'evidence_refs': [observation['id'], source['id']], 'evidence_gaps': [], 'status': 'READY'}
            if self.case['fixture_id'].startswith('two-consumers/'):
                import copy
                primary_correct = self.case['fixture_id'].endswith('preserve-one')
                payload['units'][1]['role'] = 'secondary production consumer' if primary_correct else 'primary production consumer'
                other = copy.deepcopy(payload['units'][1]); other.update(id='U3',
                    role='primary production consumer' if primary_correct else 'secondary production consumer')
                payload['units'].append(other)
                first = payload['expected_relations'][0]
                first['expected_contract'] = ('Enabled secondary' if primary_correct else 'Enabled primary') + ' consumes selected_name; disabled retains default_name.'
                other_relation = copy.deepcopy(first); other_relation.update(id='E2', target_unit_ids=['U3'],
                    expected_contract=('Enabled primary' if primary_correct else 'Enabled secondary') + ' consumes selected_name; disabled retains default_name.')
                payload['expected_relations'].append(other_relation)
                if not primary_correct:
                    other_violation = copy.deepcopy(payload['violations'][0]); other_violation.update(id='V2', relation_id='E2')
                    payload['violations'].append(other_violation)
                    payload['outcome_coverage'][0]['repair_supported'] = ['V1', 'V2']
        elif version == 'direct-plan.v1':
            if self.mode != 'without_relation':
                raise AssertionError('DirectPlan generated for another mode')
            payload = {'schema_version': 'ce.direct_plan.v1', 'snapshot_id': body['snapshot_id'],
                'input_digest': body['input_digest'], 'goal': self.case['requirement'],
                'steps': [{'id': 'C1', 'path': 'app.py', 'operation': 'MODIFY',
                    'intent': self.case['requirement'], 'depends_on': []}]}
        elif version.startswith('changeset.'):
            if self.mode != 'full':
                raise AssertionError('Removed ChangeSet stage was dispatched')
            packet, context = body['diagnosis_packet'], body['run_context']
            source = next(e for e in packet['evidence_refs']
                if e['kind'] == 'SOURCE_SPAN' and e['locator']['path'] == 'app.py')
            oid = packet['requirement_spec']['outcomes'][0]['id']
            conditions = [{'id': 'P' + str(index + 1), 'statement': check['statement'],
                'requirement_refs': [oid], 'obligation_refs': ['A1'], 'relation_refs': [r['id'] for r in packet['expected_relations']],
                'planned_check': 'Run the fixed published requirement profile.',
                'check_scope': 'Finite public candidate process outputs.',
                'verification_kind': 'PUBLIC_BEHAVIOR',
                'behavior_check_refs': [PROBE_ID + ':' + check['id']],
                'verification_scope': {'scenario': 'public-requirement-panel'},
                'check_coverage': 'ALL_OF',
                'coverage_rationale': 'This authored condition is restricted to its exact finite public predicate.'}
                for index, check in enumerate(self.case['checks'])]
            payload = {'schema_version': 'ce.semantic_changeset.v1_1', 'run_id': context['run_id'],
                'snapshot_id': context['snapshot_id'], 'input_digest': context['input_digest'],
                'diagnosis_digest': canonical_digest(packet), 'goal': self.case['requirement'],
                'planned_violation_ids': [v['id'] for v in packet['violations']], 'changes': [{'id': 'C1', 'operation': 'MODIFY',
                    'target': {'kind': 'EXISTING', 'path': 'app.py', 'locator': source['locator'],
                        'unit_refs': [u['id'] for u in packet['units'] if u['semantic_role_class'] == 'PRODUCTION_CONSUMER'], 'snapshot_id': context['snapshot_id'],
                        'content_digest': file_sha(self.runner.source / 'app.py')},
                    'semantic_action': self.case['requirement'], 'intent': self.case['requirement'],
                    'violation_refs': [v['id'] for v in packet['violations']], 'postcondition_refs': [p['id'] for p in conditions], 'depends_on': []}],
                'postconditions': conditions, 'preserve_constraints': [], 'open_obligations': [],
                'outcome_coverage': [{'outcome_id': oid, 'repair_supported': [v['id'] for v in packet['violations']],
                    'preserve_required': [], 'unresolved': []}], 'status': 'READY'}
        elif version == ModifyOnlyConsumer.PROMPT:
            self.executions += 1
            edits = synthetic_edits(self.case['fixture_id'], self.mode, self.executions)
            payload = {'replacements': [{'key': row['change_id'], 'file': row['path'],
                'before': row['old_text'], 'after': row['new_text']} for row in edits]}
        elif version in {'public-postcondition-review.v1','public-postcondition-review.v2'}:
            # Review is generated from this run's raw public records only.
            records = body['public_observations']
            checks = []
            for condition in body['checks']:
                refs = set(condition.get('behavior_check_refs', []))
                found = [r for r in records if r.get('behavior_check', {}).get('id') in refs]
                statuses = [r['behavior_check']['status'] for r in found]
                checks.append({'id': condition['id'], 'status': 'FAIL' if 'FAIL' in statuses else
                    'PASS' if found and len(found) == len(refs) else 'UNKNOWN',
                    'rationale': 'Only this run\'s finite public outputs are reviewed.',
                    'evidence': [{'observation_id': row['id'], 'quote': row['excerpt']} for row in found]})
            payload = {'checks': checks, 'failure_kind': 'UNKNOWN', 'rationale': 'Finite public requirement profile.'}
        else:
            raise AssertionError('Unexpected prompt stage: ' + version)
        return ModelResponse(json.dumps(payload), {'provider': 'd2-independent-mode-fixture',
            'method_mode': self.mode, 'zero_cost': True, 'http_requests': 0,
            'fixture_provider_elapsed_seconds': time.perf_counter() - started})


def panel_config(mode):
    return {'model': 'synthetic-d2-independent-mode', 'provider': 'synthetic', 'method_mode': mode,
        'execution_backend': 'direct', 'repair_consumer': 'modify_only',
        'max_context_tokens': 240000, 'max_output_tokens': 4096, 'execution_max_output_tokens': 4096,
        'review_max_output_tokens': 4096, 'max_total_tokens': 500000, 'max_elapsed_seconds': 180,
        'max_model_calls': 6, 'max_rounds': 1, 'max_format_retries': 0,
        'schema_in_prompt': True, 'candidate_guard_enabled': True}


def run_cell(case_id, mode, output, *, runtime_config=None):
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    case = case_definition(case_id)
    public = freeze_panel_case(case_id, output, runtime='isolated' if runtime_config is not None else 'host')
    if runtime_config is None:
        runner = PanelRunner(public, case_id)
    else:
        from contractedge.development.isolated_panel import IsolatedPanelRunner
        runner = IsolatedPanelRunner(public, case_id, runtime_config)
    provider = PanelProvider(case_id, mode, runner)
    config = panel_config(mode)
    if runtime_config is not None:
        config.update(public_runtime='isolated', public_runtime_config=dict(runtime_config))
    started = time.monotonic()
    report = repair_case(public, output / 'unused-tools', case['public_case_id'], output / 'repair',
        provider, public_demo.count_tokens, config, public_runner=runner)
    elapsed = time.monotonic() - started
    def read(path):
        return json.loads(path.read_text()) if path.is_file() else None
    broken = read(output / 'repair/initial-public-observation.json')
    actual = read(output / 'repair/rounds/0/public-observation.json')
    calls = read(output / 'repair/model_calls.json')
    audit = read(output / 'repair/method-mode.audit.json')
    consumer = read(output / 'repair/rounds/0/consumer-input.json')
    def states(obs):
        return {row['id']: row['status'] for row in ((obs or {}).get('requirement_profile') or {}).get('checks', [])}
    broken_checks, candidate_checks = states(broken), states(actual)
    row = {'fixture_id': case_id, 'method_mode': mode, 'status': report['status'],
        'execution_backend': config['execution_backend'], 'consumer': ModifyOnlyConsumer.ID,
        'intent': consumer['intent_kind'] if consumer else None, 'expected_intent': INTENTS[mode],
        'broken_checks': broken_checks, 'expected_broken_checks': case['broken_profile'],
        'broken_target_fail_proven': broken_checks == case['broken_profile']
            and any(broken_checks.get(k) == 'FAIL' for k in case['repair_item_ids']),
        'candidate_checks': candidate_checks,
        'target_checks': {key: candidate_checks.get(key, 'UNKNOWN') for key in case['repair_item_ids']},
        'preservation_checks': {key: candidate_checks.get(key, 'UNKNOWN') for key in case['preservation_item_ids']},
        'candidate_export': report.get('candidate'),
        'stage_counts': audit['counts'], 'removed_stage_calls_zero': audit['removed_stage_calls_zero'],
        'actual_prompt_versions': [c['prompt_version'] for c in calls],
        'elapsed_seconds': elapsed,
        'stage_elapsed_seconds': [{'prompt_version': c['prompt_version'], 'cumulative_seconds': c.get('elapsed_seconds'),
            'provider_seconds': c.get('response_metadata', {}).get('fixture_provider_elapsed_seconds'),
            'input_tokens_reserved': c['prompt_tokens_reserved'], 'output_tokens_reserved': c['output_tokens_reserved'],
            'transport_overhead_tokens_reserved': c.get('transport_schema_tokens_reserved', 0),
            'response_bytes': len(c.get('response_content', '').encode())} for c in calls],
        'tokens': {'reserved': report['budget']['reserved_tokens'],
            'source': 'Conservative UTF-8 byte fixture counter; reservation, not model usage'},
        'overhead': {'case_elapsed_seconds': elapsed,
            'provider_seconds': sum(c.get('response_metadata', {}).get('fixture_provider_elapsed_seconds', 0) for c in calls),
            'source': 'Case wall clock and actual synthetic complete_json wall clock; difference includes retrieval, admission, recording, processes, observation, and export',
            'format_corrections': report['execution_format_corrections_used'],
            'preparation_invocations': 1, 'repeated_preparation_invocations': 0},
        'money': {'amount_cny': 0, 'source': 'Trusted synthetic fixture; no provider/HTTP/token usage bill', 'http_requests': 0},
        'raw_requirement_digest': hashlib.sha256(case['requirement'].encode()).hexdigest(),
        'method_input_digest': report['input_digest'],
        'source_archive_sha256': file_sha(public / 'tasks' / case['public_case_id'] / 'source.tar.gz'),
        'initial_snapshot_id': report['initial_snapshot_id'],
        'final_snapshot_id': report['final_snapshot_id'],
        'config_digest': canonical_digest(config), 'public_profile_digest': canonical_digest(case['checks']),
        'shared_resource_config_digest': canonical_digest({key:value for key,value in config.items() if key != 'method_mode'}),
        'formal_ready': False, 'scope': 'EXPOSED_PUBLIC_DEVELOPMENT_FIXTURE_ISOLATED_SYNTHETIC' if runtime_config is not None else 'EXPOSED_PUBLIC_DEVELOPMENT_FIXTURE_HOST_SYNTHETIC_ONLY'}
    if runtime_config is not None:
        from contractedge.execution.panel_observer import isolated_panel_observation_complete
        row['runtime'] = 'isolated'
        row['broken_isolation_complete'] = isolated_panel_observation_complete(broken)
        row['candidate_isolation_complete'] = isolated_panel_observation_complete(actual)
        row['runtime_identity'] = {'image': runtime_config['image'],
            'candidate_invocation_digest': (actual or {}).get('required_public_profile', {}).get('candidate_invocation_digest'),
            'observer_invocation_digest': (actual or {}).get('required_public_profile', {}).get('observer_invocation_digest')}
    write_json(output / 'summary.json', row)
    return row


def run_feedback_study(output):
    """Separate synthetic two-step control; never part of the 24-cell score."""
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=False)
    rows = []
    class IncompleteProvider(PanelProvider):
        def complete_json(self, request):
            if request.prompt_version != ModifyOnlyConsumer.PROMPT:
                return super().complete_json(request)
            self.requests.append(request); self.executions += 1
            variable = 'primary' if self.executions == 1 else 'secondary'
            return ModelResponse(json.dumps({'replacements': [{'key': 'C1', 'file': 'app.py',
                'before': variable + ' = "default"', 'after': variable + ' = config["selected_name"]'}]}),
                {'provider': 'independent-mode-feedback-control', 'zero_cost': True, 'http_requests': 0})
    for mode in MODES:
        root = output / mode; root.mkdir()
        public = freeze_panel_case('two-consumers/base', root); runner = PanelRunner(public, 'two-consumers/base')
        provider = IncompleteProvider('two-consumers/base', mode, runner)
        config = {**panel_config(mode), 'max_rounds': 2}
        report = repair_case(public, root / 'unused-tools', runner.case, root / 'repair', provider,
            public_demo.count_tokens, config, public_runner=runner)
        states = []
        for index in (0, 1):
            path = root / 'repair/rounds' / str(index) / 'public-observation.json'
            obs = json.loads(path.read_text()) if path.is_file() else {}
            states.append({r['id']:r['status'] for r in obs.get('requirement_profile', {}).get('checks', [])})
        row = {'method_mode': mode, 'status': report['status'], 'round_count': len(report['rounds']),
            'round_profiles': states, 'removed_stage_calls_zero': json.loads((root / 'repair/method-mode.audit.json').read_text())['removed_stage_calls_zero'],
            'scope': 'SEPARATE_SYNTHETIC_FEEDBACK_CONTROL_NOT_MAIN_PANEL_OR_METHOD_EFFECT',
            'semantic_attribution': 'UNKNOWN', 'http_requests': 0, 'formal_ready': False}
        rows.append(row)
    write_json(output / 'feedback-study.json', {'rows': rows, 'main_panel': False,
        'interpretation': 'An independently scripted incomplete first literal edit is followed by a separately scripted second edit. This demonstrates mode-safe feedback plumbing and observable repair progress, not feedback effectiveness under a model.'})
    return rows


def run_panel(output, *, case_id=None, method_mode=None, runtime_config=None):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for case in ([case_id] if case_id else CASES):
        for mode in ([method_mode] if method_mode else MODES):
            row = run_cell(case, mode, output / case.replace('/', '--') / mode, runtime_config=runtime_config)
            rows.append(row)
            print(json.dumps({k: row[k] for k in ('fixture_id', 'method_mode', 'status', 'stage_counts', 'broken_target_fail_proven')}, sort_keys=True), flush=True)
    manifest = {'schema_version': 'ce.method_panel.v1', 'cells': rows, 'cell_count': len(rows),
        'main_comparison': 'ONE_ROUND_NO_FEEDBACK_REPAIR', 'formal_ready': False,
        'boundary': 'ACTUAL_RUNTIME_SESSION_ISOLATED_SYNTHETIC' if runtime_config is not None else 'HOST_SYNTHETIC_ONLY_NO_OS_SANDBOX', 'http_requests': 0,
        'source_identity': public_demo._method_source_identity(),
        'shared_inputs_checks_consumer_and_budgets': all(len({row[key] for row in rows if row['fixture_id'] == case}) == 1
            for case in {r['fixture_id'] for r in rows} for key in ('raw_requirement_digest', 'method_input_digest',
                'source_archive_sha256', 'initial_snapshot_id', 'public_profile_digest', 'consumer', 'shared_resource_config_digest')),
        'interpretation': 'Fixed exposed public engineering development panel, not benchmark repair rate or 24 paid API runs.'}
    expected_pairs = {(case, mode) for case in ([case_id] if case_id else CASES)
        for mode in ([method_mode] if method_mode else MODES)}
    actual_pairs = [(r['fixture_id'], r['method_mode']) for r in rows]
    failed_cells = []
    for index, row in enumerate(rows):
        pair = actual_pairs[index]
        failures = []
        if pair not in expected_pairs or actual_pairs.count(pair) != 1:
            failures.append('Cell identity is unexpected or duplicated')
        expected = case_definition(row['fixture_id'])
        if row['target_checks'] != {key: 'PASS' for key in expected['repair_item_ids']}:
            failures.append('Fixed synthetic candidate target checks are missing, FAIL or UNKNOWN')
        if row['preservation_checks'] != {key: 'PASS' for key in expected['preservation_item_ids']}:
            failures.append('Fixed synthetic candidate preservation checks are missing, FAIL or UNKNOWN')
        if row['status'] != 'PUBLIC_REVIEW_COMPLETE':
            failures.append('Fixed synthetic repair did not reach its complete public-review terminal status')
        if not row['broken_target_fail_proven'] or not row['removed_stage_calls_zero']:
            failures.append('Broken target or removed-stage call control failed')
        if row['intent'] != row['expected_intent'] or not row['candidate_export']:
            failures.append('Expected mode intent or consistent candidate export is absent')
        if runtime_config is not None and not (row['broken_isolation_complete'] and row['candidate_isolation_complete']):
            failures.append('Broken or candidate actual isolation is incomplete')
        if failures:
            failed_cells.append({'index': index, 'fixture_id': row['fixture_id'],
                'method_mode': row['method_mode'], 'failures': failures})
    complete = len(rows) == len(expected_pairs) and set(actual_pairs) == expected_pairs
    accepted = complete and not failed_cells and manifest['shared_inputs_checks_consumer_and_budgets']
    manifest['acceptance'] = {'status': 'PASS' if accepted else 'FAIL',
        'scope': 'Fixed exposed synthetic fixture outcomes; not model effectiveness',
        'expected_cell_count': len(expected_pairs), 'unique_cells_complete': complete,
        'missing_cells': [list(pair) for pair in sorted(expected_pairs - set(actual_pairs))],
        'failed_cells': failed_cells}
    write_json(output / 'manifest.json', manifest)
    return 0 if accepted else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--case', choices=tuple(CASES))
    parser.add_argument('--mode', choices=MODES)
    args = parser.parse_args(argv)
    return run_panel(args.output, case_id=args.case, method_mode=args.mode)


if __name__ == '__main__':
    raise SystemExit(main())
