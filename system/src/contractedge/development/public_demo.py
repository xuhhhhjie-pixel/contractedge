"""Inspectable, offline public repair examples using the production repair loop.

Only this module's three published examples are loaded. No benchmark installation
or evaluator is needed. Checkpoints support deliberate READY handoff only.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time

from contractedge.contracts.validation import canonical_digest, parse_method_input
from contractedge.dataset.archive import safe_extract
from contractedge.dataset.benchmark import file_sha, method_input_from_task
from contractedge.execution.topomen_agent import source_identity
from contractedge.model.budget import BudgetedProvider
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.repair import repair_case, validate_ready_trace, write_json
from contractedge.orchestration.runner import run_front_half
from contractedge.source.snapshot import compute_snapshot


SYSTEM_ROOT = Path(__file__).resolve().parents[3]
CASES = {
    'config-binding': 'Bind enabled selected_name to the production output; retain disabled defaults and stable mode.',
    'reload-state': 'Refresh a warmed state after configuration reload; retain disabled defaults and stable mode.',
    'two-consumers': 'Bind both production consumers; an intentionally incomplete first edit demonstrates feedback and retry.',
}


def count_tokens(text):
    """Conservative byte counter for the zero-cost fixture, not a model tokenizer."""
    return len(text.encode('utf-8'))


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def freeze_example(case, output):
    """Build the existing MethodInput public-task protocol from tiny source files."""
    example = SYSTEM_ROOT / 'examples/public-repair' / case
    frozen = output / 'frozen-public'
    task = frozen / 'public/tasks' / case
    task.mkdir(parents=True)
    shutil.copyfile(example / 'requirement.md', task / 'requirement.md')
    shutil.copyfile(example / 'runtime-inputs.json', task / 'runtime-inputs.json')
    with (task / 'source.tar.gz').open('wb') as raw:
        # Stable member order and metadata; gzip header is deterministic too.
        import gzip
        with gzip.GzipFile(fileobj=raw, mode='wb', mtime=0, filename='') as zipped:
            with tarfile.open(fileobj=zipped, mode='w') as archive:
                for path in sorted((example / 'source').rglob('*')):
                    if path.is_file():
                        data = path.read_bytes()
                        member = tarfile.TarInfo('repo/' + path.relative_to(example / 'source').as_posix())
                        member.size, member.mode = len(data), 0o644
                        archive.addfile(member, io.BytesIO(data))
    (task / 'task.yaml').write_text('schema_version: contractedge.method_task.v1\nrequirement_file: requirement.md\nsource_archive: source.tar.gz\nexecution_config: environment.json\n', encoding='utf-8')
    write_json(task / 'environment.json', {
        'editable_paths': ['app.py'], 'network': 'none',
        'observation_boundary': 'Python child process runs published selected and preservation behavior controls.',
        'scenarios': [{'name': 'selected-and-preservation', 'argv': ['python', 'published-observer']}],
    })
    names = sorted(p.name for p in task.iterdir())
    manifest = {'case_id': case, 'source_sha256': file_sha(task / 'source.tar.gz'),
                'files': names, 'file_sha256': {n: file_sha(task / n) for n in names}}
    write_json(task / 'manifest.json', manifest)
    row = {'case_id': case, 'primary_class': 'R1', 'task_path': 'tasks/' + case,
           'source_sha256': manifest['source_sha256'], 'task_manifest_sha256': file_sha(task / 'manifest.json')}
    (frozen / 'public/manifest.jsonl').write_text(json.dumps(row) + '\n', encoding='utf-8')
    write_json(frozen / 'RELEASE_ENTRY.json', {'status': 'FROZEN_LOCAL_SUPPORTED_PLATFORM_RELEASE',
        'version': 'public-development-examples-v1', 'selected_cases': 1, 'by_class': {'R1': 1},
        'public_manifest_sha256': file_sha(frozen / 'public/manifest.jsonl')})
    return frozen / 'public'


OBSERVER = '''import importlib.util,json,pathlib,sys,yaml
source=pathlib.Path(sys.argv[1]); inputs=json.loads(pathlib.Path(sys.argv[2]).read_text())
spec=importlib.util.spec_from_file_location('public_example_candidate',source/'app.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
base=yaml.safe_load((source/'config.yaml').read_text())
results=[]; modes=[]
for scenario in inputs['scenarios']:
    config={**base,**scenario['config']}; state=None
    if scenario.get('warm_config') is not None:
        warm=module.render({**base,**scenario['warm_config']},None);state=warm.get('state')
    actual=module.render(config,state);modes.append(actual.get('mode'))
    selected={key:actual.get(key) for key in scenario['expected']}
    results.append({'id':scenario['id'],'status':'PASS' if selected==scenario['expected'] else 'FAIL',
                    'actual':selected,'expected':scenario['expected']})
stable=next(r for r in results if r['id']=='stable')
stable.update(status='PASS' if all(m=='stable' for m in modes) else 'FAIL',
              actual={'modes':modes},expected={'modes':['stable']*len(modes)})
print(json.dumps({'checks':results},sort_keys=True))
'''


class PublicExampleRunner:
    """Real archive, Git patch and subprocess boundaries, without Docker."""
    timeout_seconds = 30

    def __init__(self, public, case):
        self.public, self.case, self.source = Path(public), case, None

    def prepare(self, case_id, workspace, log):
        workspace.mkdir(parents=True)
        task = self.public / 'tasks' / case_id
        self.source = Path(safe_extract(task / 'source.tar.gz', workspace / 'source').source_root)
        subprocess.run(['git', 'init', '-q', str(self.source)], check=True)
        subprocess.run(['git', '-C', str(self.source), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.source), '-c', 'user.name=Public Demo',
                        '-c', 'user.email=public-demo@localhost', 'commit', '-qm', 'Frozen broken example'], check=True)
        inputs = workspace / 'inputs'
        inputs.mkdir()
        shutil.copyfile(task / 'runtime-inputs.json', inputs / 'runtime-inputs.json')
        record = {'case_id': case_id, 'source_root': str(self.source), 'inputs_root': str(inputs),
                  'source_sha256': file_sha(task / 'source.tar.gz'), 'private_materials_loaded': False}
        write_json(workspace / 'workspace.json', record)
        write_json(Path(log) / 'prepare.json', record)
        return record

    def observe(self, workspace, output):
        output.mkdir(parents=True)
        record = _read(workspace / 'workspace.json')
        source = Path(record['source_root'])
        argv = [sys.executable, '-B', '-c', OBSERVER, str(source), str(workspace / 'inputs/runtime-inputs.json')]
        result = subprocess.run(argv, capture_output=True, text=True, timeout=self.timeout_seconds)
        (output / 'stdout.txt').write_text(result.stdout, encoding='utf-8')
        (output / 'stderr.txt').write_text(result.stderr, encoding='utf-8')
        write_json(output / 'process.json', {'argv': argv, 'returncode': result.returncode,
            'boundary': 'returncode=0 means observations collected; checks carry behavioral PASS/FAIL'})
        snapshot = compute_snapshot(source).snapshot_id
        observations = []
        if result.returncode == 0:
            body = json.loads(result.stdout)
            for check in body['checks']:
                excerpt = json.dumps(check, sort_keys=True)
                observations.append({'id': 'OBS-' + check['id'], 'snapshot_id': snapshot,
                    'kind': 'PUBLIC_OBSERVATION', 'evidence_kind': 'PUBLIC_BEHAVIOR',
                    'locator': {'path': str((output / 'stdout.txt').relative_to(workspace.parent)), 'field': check['id']},
                    'content_digest': hashlib.sha256(excerpt.encode()).hexdigest(), 'excerpt': excerpt,
                    'origin': 'published Python subprocess behavior check'})
        observation = {'status': 'EXECUTION_PASS' if result.returncode == 0 else 'EXECUTION_FAILED',
            'snapshot_id': snapshot, 'observations': observations,
            'input_digest': file_sha(workspace / 'inputs/runtime-inputs.json'),
            'environment_digest': canonical_digest({'python': sys.version, 'observer': OBSERVER})}
        write_json(output / 'observation.json', observation)
        return observation

    def export(self, workspace, patch, log):
        source = _read(workspace / 'workspace.json')['source_root']
        diff = subprocess.check_output(['git', '-C', source, 'diff', '--binary', 'HEAD', '--'])
        patch.write_bytes(diff)
        result = {'patch_sha256': hashlib.sha256(diff).hexdigest(), 'patch_bytes': len(diff), 'patch_path': str(patch)}
        write_json(Path(log) / 'export.json', result)
        return result


class SyntheticExampleProvider:
    """Deterministic public-only responses, still validated by every real stage."""
    def __init__(self, case, runner):
        self.case, self.runner, self.executions = case, runner, 0

    def complete_json(self, request):
        body = json.loads(request.messages[1]['content'])
        version = request.prompt_version
        if version.startswith('diagnosis.'):
            source = next(e for e in body['evidence_catalog'] if e['kind'] == 'SOURCE_SPAN' and e['locator']['path'] == 'app.py')
            observation = next(e for e in body['evidence_catalog'] if e['kind'] == 'PUBLIC_OBSERVATION')
            outcome = body['requirement_spec']['outcomes'][0]
            payload = {'requirement_horizon': [outcome['id']], 'observed_boundary': 'published selected and preservation subprocess controls',
                'inferred_causal_path': ['published configuration does not reach the production output'], 'unverified_outcomes': [],
                'units': [
                    {'id': 'U1', 'role': 'configuration input', 'kind': 'configuration', 'semantic_role_class': 'CONFIGURATION_INPUT', 'existence': 'PRESENT', 'locator_refs': [], 'requirement_refs': [outcome['id']]},
                    {'id': 'U2', 'role': 'render production output', 'kind': 'source', 'semantic_role_class': 'PRODUCTION_CONSUMER', 'existence': 'PRESENT', 'locator_refs': [source['id']], 'requirement_refs': [outcome['id']]}],
                'obligation_atoms': [{'id': 'A1', 'outcome_id': outcome['id'], 'statement': outcome['statement'], 'source_refs': outcome['source_refs'], 'support': 'EXPLICIT'}],
                'expected_relations': [{'id': 'E1', 'source_unit_ids': ['U1'], 'target_unit_ids': ['U2'], 'relation_form': 'DIRECT_EDGE', 'shared_constraint_reason': None,
                    'mechanism': 'BINDING', 'condition': 'enabled', 'expected_contract': outcome['statement'], 'requirement_refs': [outcome['id']], 'obligation_refs': ['A1'], 'support_refs': [source['id']]}],
                'violations': [{'id': 'V1', 'relation_id': 'E1', 'broken_behavior': 'selected output differs from published configuration',
                    'causal_explanation': 'production render retains a constant or stale state', 'evidence_refs': [observation['id'], source['id']], 'support_basis': 'OBSERVED_AND_STATIC', 'causal_status': 'SUPPORTED'}],
                'outcome_coverage': [{'outcome_id': outcome['id'], 'repair_supported': ['V1'], 'preserve_required': [], 'preserve_current_satisfaction': [], 'unresolved': []}],
                'evidence_refs': [observation['id'], source['id']], 'evidence_gaps': [], 'status': 'READY'}
        elif version == 'topomen-source-execution.v1':
            self.executions += 1
            if self.case == 'config-binding':
                old, new = 'name = "default"', 'name = config["selected_name"]'
            elif self.case == 'reload-state':
                old, new = '# configuration reload leaves cached name stale', 'state["name"] = config["selected_name"]'
            elif self.executions == 1:
                old, new = 'primary = "default"', 'primary = config["selected_name"]'
            else:
                old, new = 'secondary = "default"', 'secondary = config["selected_name"]'
            payload = {'edits': [{'change_id': 'C1', 'path': 'app.py', 'operation': 'MODIFY', 'old_text': old, 'new_text': new}]}
        elif version == 'public-postcondition-review.v1':
            records = body['public_observations']
            checks = []
            for check in body['checks']:
                # IDs are carried through the real review/retained-feedback path.
                check_id = check['id']
                suffix = check_id.split(':')[-1]
                wanted = 'selected' if suffix == 'P1' else 'disabled' if suffix == 'P2' else 'stable'
                record = next(r for r in records if json.loads(r['excerpt'])['id'] == wanted)
                status = json.loads(record['excerpt'])['status']
                checks.append({'id': check_id, 'status': status, 'rationale': 'Published subprocess check result.',
                    'evidence': [{'observation_id': record['id'], 'quote': record['excerpt']}]})
            payload = {'checks': checks, 'failure_kind': 'EXECUTION',
                       'rationale': 'Concrete implementation must satisfy all selected and preservation controls.'}
        else:
            packet, context = body['diagnosis_packet'], body['run_context']
            source = next(e for e in packet['evidence_refs'] if e['kind'] == 'SOURCE_SPAN' and e['locator']['path'] == 'app.py')
            oid = packet['requirement_spec']['outcomes'][0]['id']
            payload = {'schema_version': 'ce.semantic_changeset.v1_1', 'run_id': context['run_id'], 'snapshot_id': context['snapshot_id'],
                'input_digest': context['input_digest'], 'diagnosis_digest': canonical_digest(packet), 'goal': CASES[self.case],
                'planned_violation_ids': ['V1'], 'changes': [{'id': 'C1', 'operation': 'MODIFY',
                    'target': {'kind': 'EXISTING', 'path': 'app.py', 'locator': source['locator'], 'unit_refs': ['U2'],
                        'snapshot_id': context['snapshot_id'], 'content_digest': file_sha(self.runner.source / 'app.py')},
                    'semantic_action': CASES[self.case], 'intent': 'Restore configuration-to-production behavior.',
                    'violation_refs': ['V1'], 'postcondition_refs': ['P1', 'P2', 'P3'], 'depends_on': []}],
                'postconditions': [{'id': pid, 'statement': statement, 'requirement_refs': [oid], 'obligation_refs': ['A1'], 'relation_refs': ['E1'],
                    'planned_check': 'Run the published Python behavior controls.', 'check_scope': 'published runtime output'}
                    for pid, statement in [('P1', 'Selected enabled outputs match selected_name, including reload and both consumers when declared.'),
                                           ('P2', 'Disabled output retains default_name.'), ('P3', 'Mode remains stable for every public scenario.')]],
                'preserve_constraints': [], 'open_obligations': [],
                'outcome_coverage': [{'outcome_id': oid, 'repair_supported': ['V1'], 'preserve_required': [], 'unresolved': []}], 'status': 'READY'}
        return ModelResponse(json.dumps(payload), {'provider': 'deterministic-public-example', 'zero_cost': True})


def default_config(case):
    topo = SYSTEM_ROOT / 'vendor/topomen'
    config = {'model': 'synthetic-public-example', 'provider': 'synthetic',
        'max_context_tokens': 240000, 'max_output_tokens': 4096, 'execution_max_output_tokens': 4096,
        'review_max_output_tokens': 4096, 'max_total_tokens': 1500000, 'max_elapsed_seconds': 180,
        'max_model_calls': 16, 'max_rounds': 2, 'max_format_retries': 0,
        'execution_backend': 'topomen_agent' if case == 'config-binding' else 'direct',
        'schema_in_prompt': True}
    if config['execution_backend'] == 'topomen_agent':
        config.update(topomen_source_root=str(topo), topomen_source_sha256=source_identity(topo)['source_sha256'])
    return config


def _freeze_inventory(root):
    return {p.relative_to(root).as_posix(): file_sha(p) for p in sorted(root.rglob('*')) if p.is_file()}


def _method_source_identity():
    paths = [p for p in (SYSTEM_ROOT / 'src').rglob('*') if p.is_file() and p.suffix in {'.py', '.json'}]
    paths.append(SYSTEM_ROOT / 'scripts/run_public_demo.py')
    files = [{'path': p.relative_to(SYSTEM_ROOT).as_posix(), 'sha256': file_sha(p)} for p in sorted(paths)]
    return {'digest': canonical_digest(files), 'files': files}


def _runtime_identity():
    from importlib.metadata import version
    return {'python': sys.version, 'executable': sys.executable,
            'packages': {name: version(name) for name in ['PyYAML', 'jsonschema', 'pydantic', 'rich', 'python-dotenv', 'jinja2']}}


def _provider(config, case, runner):
    if config.get('provider') == 'synthetic':
        return SyntheticExampleProvider(case, runner), count_tokens
    from contractedge.cli import _build_provider
    return _build_provider(config)


def _safe_config(config):
    # Credentials are environment references only; never serialize literal keys.
    forbidden = {'api_key', 'token', 'password', 'authorization', 'headers', 'extra_headers'}
    def inspect(value):
        if isinstance(value, dict):
            if forbidden.intersection(k.lower() for k in value):
                raise ValueError('Provider config must use environment credential references, never literal credentials or headers')
            for child in value.values():
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)
    inspect(config)
    return config


def run_demo(case, output, *, save_ready=False, resume_ready=None, provider_config=None):
    started = time.monotonic()
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    previous_calls, trace, checkpoint = [], None, None
    if resume_ready is not None:
        saved = Path(resume_ready).resolve(strict=True)
        checkpoint = _read(saved / 'ready.checkpoint.json')
        if checkpoint['status'] != 'READY' or checkpoint['resume_boundary'] != 'deliberate-READY-only':
            raise ValueError('Only a deliberate validated READY checkpoint can continue; arbitrary crash resume is unsupported')
        if case is not None and case != checkpoint['case']:
            raise ValueError('Continuation case differs from saved READY case')
        case = checkpoint['case']
        if provider_config is not None:
            raise ValueError('READY continuation inherits frozen provider and budgets; provider overrides are prohibited')
        if _freeze_inventory(saved / 'frozen-public') != checkpoint['frozen_public_files']:
            raise ValueError('Frozen READY source/config/runtime-input bytes changed')
        for name in ['ready.trace.json', 'model_calls.json', 'run.config.json', 'run.freeze.json']:
            if file_sha(saved / name) != checkpoint['files'][name]:
                raise ValueError('READY checkpoint identity mismatch: ' + name)
        previous_calls = _read(saved / 'model_calls.json')
        if any(not call.get('response_content') or call.get('error') for call in previous_calls):
            raise ValueError('Unknown/incomplete dispatch cannot be resumed or automatically resent')
        config = _read(saved / 'run.config.json')
        trace = _read(saved / 'ready.trace.json')
        freeze = _read(saved / 'run.freeze.json')
        if freeze['method_source']['digest'] != _method_source_identity()['digest'] or freeze['runtime'] != _runtime_identity():
            raise ValueError('READY method source/runtime identity differs; use the original frozen environment')
        if trace['model_calls'] != previous_calls:
            raise ValueError('READY trace must retain the original exact dispatch ledger')
        money = checkpoint.get('money_ledger')
        if money and (not Path(money['path']).is_file() or file_sha(Path(money['path'])) != money['sha256']):
            raise ValueError('READY money ledger is missing or changed; never create a fresh allowance on continuation')
        shutil.copytree(saved / 'frozen-public', output / 'frozen-public')
        public = output / 'frozen-public/public'
        write_json(output / 'initial.model_calls.json', previous_calls)
        write_json(output / 'continuation.json', {'checkpoint': str(saved), 'checkpoint_sha256': file_sha(saved / 'ready.checkpoint.json'),
            'inherited_budget': checkpoint['budget'], 'prior_ledger_sha256': file_sha(saved / 'model_calls.json'),
            'automatic_resend': False, 'arbitrary_crash_resume': 'UNSUPPORTED'})
    else:
        if case not in CASES:
            raise ValueError('Select a public example with --case')
        public = freeze_example(case, output)
        config = default_config(case)
        if provider_config is not None:
            supplied = _safe_config(_read(provider_config))
            if supplied.get('provider') == 'openai_compatible':
                required = {'max_total_tokens', 'max_elapsed_seconds', 'max_model_calls', 'max_rounds', 'money_budget'}
                if not required.issubset(supplied):
                    raise ValueError('API provider config requires explicit token/time/call/round/money budgets')
            config.update(supplied)
        if config.get('money_budget') is not None:
            config['money_budget']['path'] = str(Path(config['money_budget']['path']).resolve())
    _safe_config(config)
    write_json(output / 'run.config.json', config)
    method, projection = method_input_from_task(public, case)
    write_json(output / 'run.freeze.json', {'schema_version': 'ce.public_demo_freeze.v1',
        'method_source': _method_source_identity(), 'runtime': _runtime_identity(),
        'config_digest': canonical_digest(config), 'method_input_digest': method['input_digest'],
        'initial_snapshot_id': method['initial_snapshot_id'], 'source_archive_sha256': method['archive_sha256'],
        'frozen_public_files': _freeze_inventory(output / 'frozen-public')})
    runner = PublicExampleRunner(public, case)
    provider, counter = _provider(config, case, runner)
    if save_ready:
        workspace = output / 'ready-workspace'
        runner.prepare(case, workspace, output / 'prepare')
        observation = runner.observe(workspace, output / 'broken-public')
        from contractedge.contracts.execution_capabilities import resolve_execution_capabilities
        capabilities = resolve_execution_capabilities(projection['public_execution_capabilities'], backend=config['execution_backend'])
        front_config = {**config, 'public_execution_capabilities': capabilities,
            'model': {'model': config['model'], 'provider': config['provider'], 'schema_in_prompt': config.get('schema_in_prompt', True)}}
        budget = BudgetedProvider(provider, counter, max_tokens=config['max_total_tokens'], max_seconds=config['max_elapsed_seconds'],
            max_calls=config['max_model_calls'], on_update=lambda calls: write_json(output / 'model_calls.json', calls))
        budget.started = started
        trace = run_front_half(method, runner.source.parent, budget, front_config, counter,
                               public_observations=observation['observations']).to_wire()
        # Front semantic traces contain abbreviated records. Preserve the exact
        # budget dispatch records and inherit their reservations on continuation.
        trace['model_calls'] = budget.calls
        write_json(output / 'ready.trace.json', trace)
        write_json(output / 'model_calls.json', budget.calls)
        if trace['status'] not in {'READY', 'READY_PARTIAL'}:
            write_json(output / 'summary.json', {'case': case, 'status': trace['status'], 'errors': trace.get('errors', [])})
            return _read(output / 'summary.json')
        validate_ready_trace(trace, parse_method_input(method), runner.source,
                             capabilities=capabilities)
        # Include all READY preparation/planning elapsed time in inherited time.
        if budget.calls:
            budget.calls[-1]['elapsed_seconds'] = budget.summary()['elapsed_seconds']
        write_json(output / 'ready.trace.json', trace)
        write_json(output / 'model_calls.json', budget.calls)
        checkpoint = {'schema_version': 'ce.public_demo_ready.v1', 'case': case, 'status': 'READY',
            'front_status': trace['status'],
            'resume_boundary': 'deliberate-READY-only', 'arbitrary_crash_resume': 'UNSUPPORTED; unknown dispatches are never resent',
            'budget': budget.summary(), 'frozen_public_files': _freeze_inventory(output / 'frozen-public'),
            'files': {name: file_sha(output / name) for name in ['ready.trace.json', 'model_calls.json', 'run.config.json', 'run.freeze.json']}}
        if config.get('money_budget') is not None:
            money_path = Path(config['money_budget']['path'])
            checkpoint['money_ledger'] = {'path': str(money_path), 'sha256': file_sha(money_path)}
        write_json(output / 'ready.checkpoint.json', checkpoint)
        summary = {'case': case, 'status': 'READY', 'output': str(output), 'model_calls': len(budget.calls), 'zero_cost': config['provider'] == 'synthetic'}
    else:
        report = repair_case(public, output / 'frozen-public', case, output / 'repair', provider, counter, config,
                             initial_trace=trace, public_runner=runner)
        calls = _read(output / 'repair/model_calls.json')
        write_json(output / 'combined.model_calls.json', previous_calls + calls)
        summary = {'case': case, 'status': report['status'], 'output': str(output), 'rounds': len(report['rounds']),
            'model_calls': report['budget']['model_calls'], 'resumed_model_calls': report['budget']['resumed_model_calls'],
            'patch': str(output / 'repair/candidate.patch'), 'zero_cost': config['provider'] == 'synthetic', 'errors': report['errors'],
            'scope': 'Published developer examples; no benchmark or hidden evaluation result.'}
    write_json(output / 'summary.json', summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list', action='store_true', help='List the three public examples without model calls.')
    parser.add_argument('--case', choices=CASES)
    parser.add_argument('--output', type=Path, help='New directory; existing output is never overwritten.')
    parser.add_argument('--save-ready', action='store_true', help='Stop after a validated READY plan and persist its exact ledger.')
    parser.add_argument('--resume-ready', type=Path, help='Continue a deliberate READY checkpoint in a new process/output; budgets are inherited.')
    parser.add_argument('--provider-config', type=Path, help='Optional explicit provider JSON using the existing CLI factory; may incur API cost.')
    args = parser.parse_args(argv)
    if args.list:
        print(json.dumps(CASES, indent=2))
        return 0
    if args.output is None or (not args.case and not args.resume_ready):
        parser.error('--output and either --case or --resume-ready are required')
    if args.save_ready and args.resume_ready:
        parser.error('--save-ready and --resume-ready are mutually exclusive')
    try:
        summary = run_demo(args.case, args.output, save_ready=args.save_ready,
                           resume_ready=args.resume_ready, provider_config=args.provider_config)
    except (ValueError, OSError) as exc:
        parser.exit(2, f'public demo: {exc}\n')
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary['status'] in {'READY', 'PUBLIC_REVIEW_COMPLETE'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
