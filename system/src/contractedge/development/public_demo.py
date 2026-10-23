"""Inspectable, offline public repair examples using the production repair loop.

Only this module's three published examples are loaded. No benchmark installation
or evaluator is needed. Checkpoints support deliberate READY handoff only.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time
import uuid

from contractedge.contracts.validation import canonical_digest, parse_method_input
from contractedge.contracts.artifacts import digest, relative_path
from contractedge.dataset.archive import safe_extract
from contractedge.dataset.benchmark import file_sha, method_input_from_task
from contractedge.execution.topomen_agent import source_identity
from contractedge.model.budget import BudgetedProvider
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.repair import repair_case, validate_ready_trace, write_json
from contractedge.orchestration.runner import run_front_half
from contractedge.orchestration.recorder import RunRecorder, ProviderJournal
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


def freeze_example(case, output, *, runtime='host'):
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
                for name in ['app.py', 'config.yaml']:
                    path = example / 'source' / name
                    if path.is_symlink() or not path.resolve().is_relative_to(example.resolve()):
                        raise ValueError('Public example source must be an owned regular file')
                    data = path.read_bytes()
                    member = tarfile.TarInfo('repo/' + name)
                    member.size, member.mode = len(data), 0o644
                    archive.addfile(member, io.BytesIO(data))
    (task / 'task.yaml').write_text('schema_version: contractedge.method_task.v1\nrequirement_file: requirement.md\nsource_archive: source.tar.gz\nexecution_config: environment.json\n', encoding='utf-8')
    environment = {
        'editable_paths': ['app.py'], 'network': 'none' if runtime == 'isolated' else 'host-process-not-enforced',
        'observation_boundary': ('Isolated candidate process output with independent frozen public assertions; effective isolation is verified per actual invocation.'
            if runtime == 'isolated' else 'Host Python child process runs published controls; no OS network/filesystem sandbox. Only synthetic-provider execution is admitted.'),
        'scenarios': [{'name': 'selected-and-preservation', 'argv': ['python', 'published-observer']}],
    }
    if runtime == 'isolated':
        from contractedge.execution.public_observer import public_check_capabilities
        environment['registered_public_checks'] = public_check_capabilities()
    write_json(task / 'environment.json', environment)
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
        argv = [sys.executable, '-I', '-B', '-c', OBSERVER, str(source), str(workspace / 'inputs/runtime-inputs.json')]
        environment = {key: os.environ[key] for key in ['PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'TMPDIR'] if key in os.environ}
        environment['LANG'] = 'C.UTF-8'
        # This removes inherited API credentials and Python/user startup paths.
        # It is not an operating-system filesystem or network sandbox.
        result = subprocess.run(argv, capture_output=True, text=True, timeout=self.timeout_seconds,
                                cwd=source, env=environment)
        (output / 'stdout.txt').write_text(result.stdout, encoding='utf-8')
        (output / 'stderr.txt').write_text(result.stderr, encoding='utf-8')
        write_json(output / 'process.json', {'argv': argv, 'returncode': result.returncode,
            'environment_keys': sorted(environment), 'python_isolated': True,
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


class IsolatedPublicExampleRunner(PublicExampleRunner):
    """Production public-runner protocol backed by independent container checks."""
    runtime_kind = 'isolated-public-process'
    requires_public_profile = True

    def __init__(self, public, case, runtime_config):
        super().__init__(public, case)
        from contractedge.execution.public_observer import IsolatedPublicObserver
        if set(runtime_config) - {'image', 'docker_host', 'docker_api_version'}:
            raise ValueError('Unsupported public runtime configuration')
        self.observer = IsolatedPublicObserver(**runtime_config, timeout_seconds=self.timeout_seconds)
        self.first_observation = True

    def observe(self, workspace, output):
        record = _read(workspace / 'workspace.json')
        # repair.reserve_public_time narrows this timeout using the shared
        # deadline; do not retain the constructor's original 30-second allowance.
        self.observer.timeout_seconds = self.timeout_seconds
        report = self.observer.observe(Path(record['source_root']), workspace / 'inputs/runtime-inputs.json', output)
        if report.get('status') == 'EXECUTION_PASS':
            from contractedge.execution.public_observer import isolated_observation_complete
            if not isolated_observation_complete(report):
                report = {**report, 'status': 'INVALID_OBSERVATION',
                    'errors': list(report.get('errors', [])) + ['Required public profile or runtime receipt is missing, incomplete or stale']}
        if self.first_observation:
            self.first_observation = False
            checks = {row['id']: row['status'] for row in report.get('required_public_profile', {}).get('checks', [])}
            if (report.get('status') != 'EXECUTION_PASS'
                    or report.get('required_public_profile', {}).get('status') != 'COMPLETE'
                    or checks != {'selected': 'FAIL', 'disabled': 'PASS', 'stable': 'PASS'}):
                report['status'] = 'INVALID_OBSERVATION'
                report.setdefault('errors', []).append('The frozen Broken must fail the registered target while preserving the published controls before any model dispatch.')
                write_json(Path(output) / 'observation.json', report)
        return report


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
        if (not version.startswith('diagnosis.') and version not in {'topomen-source-execution.v1', 'public-postcondition-review.v1'}
                and isinstance(self.runner, IsolatedPublicExampleRunner)):
            # This synthetic provider explicitly declares its finite predicate
            # bindings. Real model responses must supply their own bindings.
            for condition in payload['postconditions']:
                suffix = {'P1': 'selected', 'P2': 'disabled', 'P3': 'stable'}[condition['id']]
                condition.update(verification_kind='PUBLIC_BEHAVIOR',
                    behavior_check_refs=['contractedge-public-process-v1:' + suffix],
                    check_coverage='ALL_OF',
                    coverage_rationale='This postcondition is restricted to its published finite process-output predicate; it makes no hidden or internal-state claim.')
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
    if not isinstance(config, dict):
        raise ValueError('Provider config must be a JSON object')
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
    if config.get('base_url') is not None:
        from urllib.parse import urlsplit
        if not isinstance(config['base_url'], str):
            raise ValueError('Provider base_url must be a URL string')
        url = urlsplit(config['base_url'])
        if url.scheme not in {'http', 'https'} or not url.netloc or url.username is not None or url.password is not None or url.query or url.fragment:
            raise ValueError('Provider base_url must be HTTP(S) without userinfo, query or fragment')
    if config.get('api_key_env') is not None and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', str(config['api_key_env'])):
        raise ValueError('api_key_env must be an environment variable name')
    if config.get('provider') == 'openai_compatible':
        money = config.get('money_budget')
        fields = {'path', 'limit_cny', 'model', 'input_cny_per_million', 'output_cny_per_million'}
        if not isinstance(money, dict) or not fields.issubset(money) or not isinstance(money['path'], str) or not money['path']:
            raise ValueError('API provider requires a nonempty money_budget object with path/limit/model/input and output rates')
        if money['model'] != config.get('model'):
            raise ValueError('Money budget model must match the configured provider model')
    return config


def _claim_ready(saved, output):
    """A deliberate checkpoint is consumed at most once, even after a crash."""
    path = saved / 'continuation.claim.json'
    claim = {'schema_version': 'ce.public_demo_continuation_claim.v1', 'output': str(output),
             'checkpoint_sha256': file_sha(saved / 'ready.checkpoint.json'),
             'prior_ledger_sha256': file_sha(saved / 'model_calls.json'),
             'claimed_unix_seconds': time.time(), 'automatic_resend': False}
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ValueError('READY checkpoint is already claimed; inspect the first continuation ledger and never automatically resend unknown calls: ' + str(path)) from exc
    # An interrupted/empty claim also blocks reuse: no unknown attempt is erased.
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(json.dumps(claim, indent=2) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(saved, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return claim


def _journal_path(root, name):
    """Checkpoint references can read only owned immutable journal namespaces."""
    relative_path(name)
    immutable = (name == 'journal/run.identity.json'
        or re.fullmatch(r'journal/ledger/segment-[0-9]{6,}/revision-[0-9]{6,}(?:\.seal)?\.json', name)
        or re.fullmatch(r'journal/stages/[A-Za-z0-9][A-Za-z0-9_.-]{0,127}/[A-Za-z0-9][A-Za-z0-9_.-]{0,127}/revision-[0-9]{6,}\.json', name)
        or re.fullmatch(r'journal/values/[0-9a-f]{64}\.json', name)
        or re.fullmatch(r'journal/artifacts/[0-9a-f]{64}/.+', name))
    if not immutable:
        raise ValueError('READY stage journal path is outside the immutable namespaces')
    path = Path(root)
    for part in Path(name).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError('READY stage journal paths cannot traverse symlinks')
    if not path.is_file():
        raise ValueError('READY stage journal artifact is missing: ' + name)
    return path


def _journal_inventory(root):
    journal = Path(root) / 'journal'
    if journal.is_symlink() or not journal.is_dir():
        raise ValueError('READY stage journal must be an owned directory')
    files = {}
    for path in sorted(journal.rglob('*')):
        if path.is_symlink():
            raise ValueError('READY stage journal paths cannot be symlinks')
        if path.is_file() and not path.name.endswith('.inflight.json'):
            name = path.relative_to(root).as_posix()
            files[name] = file_sha(_journal_path(root, name))
    return files


def _validate_ready_journal(saved, checkpoint, trace, calls):
    binding = checkpoint.get('stage_journal')
    if binding is None:
        return None
    if (not isinstance(binding, dict) or binding.get('schema_version') != 'ce.ready_stage_journal.v1'
            or binding.get('run_id') != trace['run_id'] or checkpoint.get('run_id') != trace['run_id']
            or binding.get('input_digest') != trace['input_digest']
            or binding.get('snapshot_id') != trace['snapshot_id']):
        raise ValueError('READY stage journal identity differs from the saved trace')
    files = binding.get('files')
    if not isinstance(files, dict) or 'journal/run.identity.json' not in files:
        raise ValueError('READY stage journal inventory is missing')
    for name, checksum in files.items():
        digest(checksum)
        if file_sha(_journal_path(saved, name)) != checksum:
            raise ValueError('READY stage journal artifact changed: ' + name)
    if _journal_inventory(saved) != files:
        raise ValueError('READY stage journal exact immutable inventory differs')
    recorder = RunRecorder(saved, run_id=trace['run_id'], input_digest=trace['input_digest'])
    references = binding.get('stage_receipts')
    if not isinstance(references, dict) or set(references) != {'diagnosis','planning','front','ready'}:
        raise ValueError('READY original stage receipt references are incomplete')
    def verify_artifact(reference):
        if not isinstance(reference, dict) or reference.get('relative_path') not in files:
            raise ValueError('READY stage receipt points outside its bound journal')
        _journal_path(saved, reference['relative_path'])
        return recorder.verify_artifact(reference)
    for name in files:
        if not name.startswith('journal/stages/'):
            continue
        wire = _read(_journal_path(saved, name))
        receipt = recorder._load_stage(name, stage=wire['stage'], attempt_id=wire['attempt_id'], revision=wire['revision'])
        for field in ('input_refs','output_refs','projection_refs'):
            for reference in getattr(receipt, field):verify_artifact(asdict(reference))
        for reference in receipt.call_refs:recorder.verify_call(reference)
    for stage, reference in references.items():
        ref = verify_artifact(reference)
        if (ref.kind != 'STAGE_RECEIPT' or not ref.relative_path.startswith('journal/stages/' + stage + '/')
                or ref.snapshot_id != trace['snapshot_id']):
            raise ValueError('READY stage receipt kind/snapshot/stage differs')
    ready_wire = _read(_journal_path(saved, references['ready']['relative_path']))
    ready_outputs = {reference['kind']:reference for reference in ready_wire['output_refs']}
    for kind, name in [('READY_TRACE_ACTUAL_LEDGER','ready.trace.json'),('ACTUAL_CALLS_VIEW','model_calls.json'),
                       ('READY_CONFIG','run.config.json'),('READY_FREEZE','run.freeze.json')]:
        if kind not in ready_outputs or ready_outputs[kind]['sha256'] != file_sha(saved / name):
            raise ValueError('READY stage journal source/configuration/observe_broken binding differs: ' + name)
    seal_wire = binding.get('ledger_seal')
    if not isinstance(seal_wire, dict):
        raise ValueError('READY stage journal ledger seal is missing')
    try:
        seal = recorder._load_seal(recorder._revision_name(seal_wire['segment_index'], seal_wire['revision'], metadata=True))
    except (KeyError, TypeError) as error:
        raise ValueError('READY stage journal ledger seal is invalid') from error
    if canonical_digest(asdict(seal)) != canonical_digest(seal_wire) or _read(saved / seal.artifact_ref.relative_path) != calls:
        raise ValueError('READY final settled journal differs from the actual saved calls')
    return binding


def _import_ready_journal(saved, output, binding, trace):
    if (output / 'journal').exists() or (output / 'journal').is_symlink():
        raise ValueError('READY journal import cannot overwrite an existing destination')
    for name, checksum in binding['files'].items():
        content = _journal_path(saved, name).read_bytes()
        if hashlib.sha256(content).hexdigest() != checksum:
            raise ValueError('READY stage journal changed during import')
        # Every validated name starts in journal/. Copying cannot overwrite
        # config, freeze, claims, model-call views or a money ledger.
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if any(path.is_symlink() for path in (target, *target.parents)):
            raise ValueError('READY journal import destination cannot contain symlinks')
        with target.open('xb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    recorder = RunRecorder(output, run_id=trace['run_id'], input_digest=trace['input_digest'])
    seal = recorder._load_seal(recorder._revision_name(binding['ledger_seal']['segment_index'],
                                                     binding['ledger_seal']['revision'], metadata=True))
    recorder.record_stage('ready_import', attempt_id='saved', snapshot_id=trace['snapshot_id'],
        status='VERIFIED_STAGE_IMPORT', input_refs=tuple(binding['stage_receipts'].values()),
        output_refs=(recorder.capture_artifact('continuation.json','READY_CONTINUATION'),),
        call_refs=seal.call_refs, issues=({'scope':'Original stage facts imported byte-for-byte; no new dispatch or cost.'},))


def run_demo(case, output, *, save_ready=False, resume_ready=None, provider_config=None,
             runtime=None, runtime_image=None):
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
        original_journal = _validate_ready_journal(saved, checkpoint, trace, previous_calls)
        shutil.copytree(saved / 'frozen-public', output / 'frozen-public')
        public = output / 'frozen-public/public'
        write_json(output / 'initial.model_calls.json', previous_calls)
        write_json(output / 'continuation.json', {'checkpoint': str(saved), 'checkpoint_sha256': file_sha(saved / 'ready.checkpoint.json'),
            'inherited_budget': checkpoint['budget'], 'prior_ledger_sha256': file_sha(saved / 'model_calls.json'),
            'initial_stage_provenance': {'status':'VERIFIED_STAGE_IMPORT' if original_journal is not None else 'LEGACY_IMPORT',
                'scope':'Original immutable stage receipts retained.' if original_journal is not None else
                        'No original stage receipts available; compatibility import only, no stages synthesized.'},
            'automatic_resend': False, 'arbitrary_crash_resume': 'UNSUPPORTED'})
        if original_journal is not None:
            _import_ready_journal(saved, output, original_journal, trace)
    else:
        if case not in CASES:
            raise ValueError('Select a public example with --case')
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
    if resume_ready is not None:
        if runtime is not None or runtime_image is not None:
            raise ValueError('READY continuation inherits the frozen execution runtime; overrides are prohibited')
    else:
        kind = runtime or config.get('public_runtime', 'host')
        if kind not in {'host', 'isolated'}:
            raise ValueError('Unsupported public runtime')
        config['public_runtime'] = kind
        if kind == 'isolated':
            settings = dict(config.get('public_runtime_config') or {})
            settings['image'] = runtime_image or settings.get('image') or os.environ.get('CONTRACTEDGE_RUNTIME_IMAGE')
            settings['docker_host'] = settings.get('docker_host') or os.environ.get('DOCKER_HOST', 'unix:///var/run/docker.sock')
            # API negotiation belongs to the current daemon, not a baked-in API version.
            config['public_runtime_config'] = settings
            if not settings.get('image'):
                raise ValueError('Isolated runtime requires an explicit cached digest-pinned image')
        elif runtime_image is not None:
            raise ValueError('--runtime-image requires --runtime isolated')
        public = freeze_example(case, output, runtime=kind)
    _safe_config(config)
    if config.get('public_runtime', 'host') == 'isolated' and config.get('observe_broken', True) is not True:
        raise ValueError('Isolated public execution requires the actual Broken observation before model dispatch')
    if config.get('provider') != 'synthetic' and not save_ready and config.get('public_runtime', 'host') != 'isolated':
        raise ValueError('Non-synthetic provider execution requires a sandbox observer; only --save-ready is supported')
    write_json(output / 'run.config.json', config)
    method, projection = method_input_from_task(public, case)
    write_json(output / 'run.freeze.json', {'schema_version': 'ce.public_demo_freeze.v1',
        'method_source': _method_source_identity(), 'runtime': _runtime_identity(),
        'config_digest': canonical_digest(config), 'method_input_digest': method['input_digest'],
        'initial_snapshot_id': method['initial_snapshot_id'], 'source_archive_sha256': method['archive_sha256'],
        'frozen_public_files': _freeze_inventory(output / 'frozen-public')})
    runner = (IsolatedPublicExampleRunner(public, case, config['public_runtime_config'])
              if config.get('public_runtime', 'host') == 'isolated' else PublicExampleRunner(public, case))
    if resume_ready is not None:
        claim = _claim_ready(saved, output)
        write_json(output / 'continuation.claim.json', claim)
    provider, counter = _provider(config, case, runner)
    if save_ready:
        workspace = output / 'ready-workspace'
        remaining = float(config['max_elapsed_seconds']) - (time.monotonic() - started)
        if remaining <= 0:
            raise ValueError('Global wall-clock budget exhausted before Broken preparation')
        runner.timeout_seconds = min(float(config.get('public_timeout_seconds', 600)), remaining)
        runner.prepare(case, workspace, output / 'prepare')
        remaining = float(config['max_elapsed_seconds']) - (time.monotonic() - started)
        if remaining <= 0:
            raise ValueError('Global wall-clock budget exhausted before Broken observation')
        runner.timeout_seconds = min(float(config.get('public_timeout_seconds', 600)), remaining)
        observation = runner.observe(workspace, output / 'broken-public')
        if observation.get('status') in {'INFRA_ERROR', 'INVALID_OBSERVATION', 'BUDGET_EXCEEDED'}:
            raise ValueError('Broken observation could not be completed; model dispatch is prohibited')
        from contractedge.contracts.execution_capabilities import resolve_execution_capabilities
        capabilities = resolve_execution_capabilities(projection['public_execution_capabilities'], backend=config['execution_backend'])
        front_config = {**config, 'public_execution_capabilities': capabilities,
            'run_id': str(config.get('run_id') or uuid.uuid4()),
            'model': {'model': config['model'], 'provider': config['provider'], 'schema_in_prompt': config.get('schema_in_prompt', True)}}
        journal = ProviderJournal(RunRecorder(output, run_id=front_config['run_id'], input_digest=method['input_digest']))
        budget = BudgetedProvider(provider, counter, max_tokens=config['max_total_tokens'], max_seconds=config['max_elapsed_seconds'],
            max_calls=config['max_model_calls'],
            on_update=lambda calls: journal.callback(calls, lambda actual: write_json(output / 'model_calls.json', actual)))
        journal.attach(budget)
        budget.started = started
        trace = run_front_half(method, runner.source.parent, budget, front_config, counter,
                               public_observations=observation['observations'], journal=journal, attempt_id='ready').to_wire()
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
        journal.update(budget.calls)
        final_seal = journal.recorder.seal_ledger()
        journal.record('ready', attempt_id='save', snapshot_id=trace['snapshot_id'], status='READY',
            input_refs=tuple(journal.latest[stage] for stage in ('diagnosis','planning','front')),
            output_refs=tuple(journal.capture(name, kind, snapshot_id=trace['snapshot_id'])
                for name,kind in [('ready.trace.json','READY_TRACE_ACTUAL_LEDGER'),('model_calls.json','ACTUAL_CALLS_VIEW'),
                                  ('run.config.json','READY_CONFIG'),('run.freeze.json','READY_FREEZE')]))
        checkpoint = {'schema_version': 'ce.public_demo_ready.v1', 'case': case, 'status': 'READY',
            'run_id':trace['run_id'],
            'front_status': trace['status'],
            'resume_boundary': 'deliberate-READY-only', 'arbitrary_crash_resume': 'UNSUPPORTED; unknown dispatches are never resent',
            'budget': budget.summary(), 'frozen_public_files': _freeze_inventory(output / 'frozen-public'),
            'files': {name: file_sha(output / name) for name in ['ready.trace.json', 'model_calls.json', 'run.config.json', 'run.freeze.json']}}
        checkpoint['stage_journal'] = {'schema_version':'ce.ready_stage_journal.v1',
            'run_id':trace['run_id'],'input_digest':trace['input_digest'],'snapshot_id':trace['snapshot_id'],
            'files':_journal_inventory(output),'ledger_seal':asdict(final_seal),
            'stage_receipts':{stage:asdict(journal.latest[stage]) for stage in ('diagnosis','planning','front','ready')},
            'runner_trace_scope':'RunTrace is a compatibility projection. Final ready.trace.json/model_calls.json bind the later actual ledger settlement.'}
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
    parser.add_argument('--provider-config', type=Path, help='Optional provider JSON; live execution requires --runtime isolated and a persistent money budget.')
    parser.add_argument('--runtime', choices=['host', 'isolated'], help='Host remains synthetic-only; isolated verifies actual candidate and observer boundaries.')
    parser.add_argument('--runtime-image', help='Cached digest-pinned image for isolated execution; no automatic pull.')
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
                           resume_ready=args.resume_ready, provider_config=args.provider_config,
                           runtime=args.runtime, runtime_image=args.runtime_image)
    except (ValueError, OSError) as exc:
        parser.exit(2, f'public demo: {exc}\n')
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary['status'] in {'READY', 'PUBLIC_REVIEW_COMPLETE'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
