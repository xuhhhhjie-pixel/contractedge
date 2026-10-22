"""Use the frozen public launcher in disposable runtime copies of candidate code."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import re
from collections import Counter
import yaml

from contractedge.dataset.benchmark import file_sha
from contractedge.source.snapshot import compute_snapshot


def run_tool(argv, output: Path, timeout: float) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    timed_out = False
    with (output / 'tool.stdout').open('wb') as stdout, (output / 'tool.stderr').open('wb') as stderr:
        process = subprocess.Popen(argv, stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            code = process.returncode
    return {'argv': argv, 'returncode': code, 'timed_out': timed_out,
            'stdout_sha256': file_sha(output / 'tool.stdout'),
            'stderr_sha256': file_sha(output / 'tool.stderr')}


def _link_or_copy(source, destination):
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def has_candidate_runtime_failure(report):
    """Consume only current failures produced by this public runtime wrapper."""
    return bool(report.get('status')=='EXECUTION_FAILED' and report.get('snapshot_id') and any(
        f.get('snapshot_id')==report['snapshot_id'] and f.get('operation')=='PRODUCTION_PROCESS_START'
        and f.get('status')=='FAILED' and f.get('source_locations')
        for f in report.get('candidate_runtime_failures',[])))


def public_failure_diagnostics(report):
    """Keep candidate execution failures distinct from adapter failures."""
    runtime=report.get('candidate_runtime_failures',[]) if has_candidate_runtime_failure(report) else []
    return [*report.get('observation_adapter_audit',[]),
        *[{'kind':'CANDIDATE_RUNTIME_FAILURE',**f} for f in runtime],
        *([{'kind':'METHOD_PROBE_ERROR','status':'PROBE_ERROR'}]
            if report.get('method_probe_status')=='PROBE_ERROR' and not runtime else [])]


def cleanup_owned_containers(roots):
    """Remove only containers mounting this invocation's disposable directories."""
    owned = [Path(p).resolve() for p in roots]
    removed = []
    try:
        running = subprocess.run(['docker', 'ps', '-q'], capture_output=True, text=True, timeout=10)
        if running.returncode:
            return {'status': 'DOCKER_UNAVAILABLE', 'removed': []}
        for cid in running.stdout.split():
            mounts = subprocess.run(['docker', 'inspect', '--format', '{{json .Mounts}}', cid],
                                    capture_output=True, text=True, timeout=10)
            if mounts.returncode:
                continue
            values = json.loads(mounts.stdout)
            if not any(m.get('Type') == 'bind' and any(Path(m['Source']).resolve().is_relative_to(root)
                       for root in owned) for m in values):
                continue
            result = subprocess.run(['docker', 'rm', '-f', cid], capture_output=True, timeout=15)
            if result.returncode:
                return {'status': 'OWNED_CLEANUP_FAILED', 'removed': removed}
            removed.append(cid)
        return {'status': 'OWNED_CONTAINERS_CLOSED', 'removed': removed}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {'status': 'CLEANUP_UNCONFIRMED', 'removed': removed}


class BenchmarkPublicRunner:
    def __init__(self, public_root: str | Path, tools_root: str | Path, *,
                 lock_root: str | Path, timeout_seconds: float = 1200, query: str = '',
                 method_probe_profiles=()):
        self.public_root = Path(public_root).resolve(strict=True)
        self.tools_root = Path(tools_root).resolve(strict=True)
        self.lock_root = Path(lock_root).resolve()
        self.timeout_seconds = timeout_seconds
        self.query = query
        self.method_probe_profiles = tuple(method_probe_profiles)
        self._method_probe_specs = {}
        self._observation_adapters = {}
        for name in ['prepare_case.py', 'collect_patch.py', 'launch_public_case.py']:
            if not (self.tools_root / name).is_file():
                raise ValueError('Missing frozen public tool: ' + name)

    def prepare(self, case_id: str, workspace: Path, log: Path) -> dict:
        command = [sys.executable, str(self.tools_root / 'prepare_case.py'),
                   '--public-root', str(self.public_root), '--case-id', case_id,
                   '--workspace', str(workspace), '--git']
        result = run_tool(command, log, self.timeout_seconds)
        if result['returncode'] or result['timed_out']:
            raise ValueError('Public workspace preparation failed; inspect ' + str(log))
        record = json.loads((workspace / 'workspace.json').read_text())
        source = Path(record['source_root']).resolve(strict=True)
        source.relative_to(workspace.resolve())
        if record['case_id'] != case_id or record.get('private_materials_loaded') is not False:
            raise ValueError('Invalid public preparation result')
        from contractedge.execution.probe_specs import select_probes
        self._method_probe_specs[str(workspace.resolve())] = select_probes(self.query, source, self.method_probe_profiles)
        from contractedge.execution.observations import select_observation_adapters
        self._observation_adapters[str(workspace.resolve())] = select_observation_adapters(self.public_root, case_id)
        return record

    def export(self, workspace: Path, patch: Path, log: Path) -> dict:
        command = [sys.executable, str(self.tools_root / 'collect_patch.py'),
                   '--workspace', str(workspace), '--output', str(patch)]
        result = run_tool(command, log, self.timeout_seconds)
        if result['returncode'] or not patch.is_file():
            raise ValueError('Candidate patch export failed; inspect ' + str(log))
        return {'patch_path': str(patch), 'patch_sha256': file_sha(patch),
                'patch_bytes': patch.stat().st_size, 'tool': result}

    def observe(self, workspace: Path, output: Path) -> dict:
        observe_started = time.monotonic()
        workspace_record = json.loads((workspace / 'workspace.json').read_text())
        from contractedge.dataset.benchmark import public_task, checked_public_file
        comparison_identity={'input_digest':compute_snapshot(Path(workspace_record['inputs_root'])).snapshot_id,
                             'environment_digest':None}
        # Raw collection can be used independently (including fixture tests).
        # Without a pinned public manifest it supplies no comparable environment
        # identity, and the promotion guard must remain UNCOMPARABLE.
        if (self.public_root/'manifest.jsonl').is_file():
            _,task_directory,_,task_manifest=public_task(self.public_root,workspace_record['case_id'])
            checked_public_file(task_directory,task_manifest,'environment.json')
            comparison_identity['environment_digest']=hashlib.sha256(json.dumps({
                'environment_sha256':file_sha(task_directory/'environment.json'),
                'public_tools':{name:file_sha(self.tools_root/name) for name in ('launch_public_case.py','run_public_case.py')},
                'method_probes':[{k:s[k] for k in ('id','sha256')} for s in self._method_probe_specs.get(str(workspace.resolve()),[])]},
                sort_keys=True).encode()).hexdigest()
        source = Path(workspace_record['source_root']).resolve(strict=True)
        before = compute_snapshot(source).snapshot_id
        output.mkdir(parents=True, exist_ok=False)
        # Public build commands may generate files. Run them on a copy so S1
        # always identifies the candidate edits, not compiler/cache side effects.
        with tempfile.TemporaryDirectory(prefix='ce-public-runtime-', dir=workspace.parent,
                                         ignore_cleanup_errors=True) as temporary:
            runtime = Path(temporary)
            runtime_source = runtime / 'source'
            shutil.copytree(source, runtime_source, symlinks=True,
                            ignore=shutil.ignore_patterns('.git', '.cache', '.contractedge-deps'))
            shutil.copytree(Path(workspace_record['inputs_root']), runtime / 'inputs',
                            copy_function=_link_or_copy, symlinks=False)
            clone_record = {**workspace_record, 'source_root': str(runtime_source), 'inputs_root': str(runtime / 'inputs')}
            (runtime / 'workspace.json').write_text(json.dumps(clone_record) + '\n')
            command = [sys.executable, str(self.tools_root / 'launch_public_case.py'),
                       '--public-root', str(self.public_root), '--workspace', str(runtime),
                       '--output', str(output / 'raw'), '--lock-root', str(self.lock_root), '--execute']
            try:
                result = run_tool(command, output / 'launcher', self.timeout_seconds)
            finally:
                cleanup = cleanup_owned_containers([runtime, output / 'raw'])
        after = compute_snapshot(source).snapshot_id
        if after != before:
            raise ValueError('Public runtime unexpectedly changed the editable candidate tree')
        # Helm can emit hundreds of KiB of CRDs before the target resources.
        # Retain a complete resource index rather than treating a prefix as an
        # exhaustive observation. No expected values or private assertions enter it.
        derived_evidence = {}
        method_probe_report = None
        runtime_failures = []
        if self.method_probe_profiles and result['returncode'] == 0 and not result['timed_out']:
            from contractedge.execution.probes import run_method_probes, behavior_records, candidate_runtime_failures
            specs = self._method_probe_specs.get(str(workspace.resolve()))
            if specs is None:
                raise ValueError('Method probe selection requires the prepared Broken workspace')
            if specs:
                try:
                    method_probe_report = run_method_probes(workspace, output / 'method-probes', self.tools_root,
                        specs, timeout_seconds=max(0, self.timeout_seconds - (time.monotonic() - observe_started)))
                    derived_evidence.update(behavior_records(method_probe_report, output / 'method-probes'))
                    runtime_failures = candidate_runtime_failures(method_probe_report, output / 'method-probes', source)
                    if method_probe_report['status'] != 'COMPLETE':
                        failures = []
                        for probe in method_probe_report.get('probes', []):
                            stderr = output / 'method-probes' / probe['probe_id'] / 'stderr'
                            failures.append({'probe_id':probe['probe_id'],'returncode':probe['returncode'],
                                'timed_out':probe['timed_out'],'stderr_excerpt':stderr.read_text(errors='replace')[:4000] if stderr.exists() else ''})
                        (output / 'method-probe-error.json').write_text(json.dumps({'status':'PROBE_ERROR','failures':failures})+'\n')
                except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
                    method_probe_report = {'status':'PROBE_ERROR','error':str(exc),'observations':[]}
                    (output / 'method-probe-error.json').write_text(json.dumps(method_probe_report)+'\n')

        for path in sorted((output / 'raw').rglob('*.stdout')) if (output / 'raw').exists() else []:
            if result['returncode'] != 0 or result['timed_out']:
                continue  # A partial render cannot establish an exhaustive inventory.
            if path.stat().st_size > 8 * 1024 * 1024:
                continue
            text = path.read_text(errors='replace')
            if '# Source:' not in text:
                continue
            try:
                documents = []
                def collect(value, depth=0):
                    if not isinstance(value, dict) or not isinstance(value.get('kind'), str) or depth > 16:
                        raise ValueError('Incomplete public resource enumeration')
                    kind = value['kind']
                    if kind == 'List' or kind.endswith('List'):
                        if not isinstance(value.get('items'), list):
                            raise ValueError('Incomplete public List enumeration')
                        for item in value['items']:
                            collect(item, depth + 1)
                    else:
                        if not isinstance(value.get('metadata', {}), dict) or not isinstance(value.get('spec', {}), dict):
                            raise ValueError('Malformed public resource')
                        documents.append(value)
                for value in yaml.safe_load_all(text):
                    if value is not None:
                        collect(value)
            except (yaml.YAMLError, ValueError, RecursionError):
                continue
            counts = Counter(v['kind'] for v in documents)
            resources, pods = [], []
            for value in documents:
                metadata = value.get('metadata') or {}
                resource = {'kind': value['kind'], 'name': metadata.get('name'), 'namespace': metadata.get('namespace')}
                resources.append(resource)
                spec = value.get('spec') or {}
                pod = spec if value['kind'] == 'Pod' else (spec.get('template') or {}).get('spec')
                if isinstance(pod, dict):
                    fields = ['nodeSelector', 'tolerations', 'affinity', 'serviceAccountName', 'automountServiceAccountToken', 'containers']
                    pods.append({**resource, 'pod_spec_fields': {k: pod[k] for k in fields if k in pod},
                                 'absent_pod_spec_fields': [k for k in fields if k not in pod]})
            summary = {'source_file': path.relative_to(output).as_posix(), 'source_sha256': file_sha(path),
                       'observation_type': 'PARSED_PUBLIC_RENDER', 'all_documents_parsed': True,
                       'document_count': len(documents),
                       'resource_counts': {k: counts[k] for k in sorted(set(counts) | {'Job', 'Deployment', 'Pod'})},
                       'names_by_kind': {kind: [r['name'] for r in resources if r['kind'] == kind]
                                         for kind in sorted(counts)},
                       'resources': resources,
                       'pod_specs': pods, 'pod_specs_complete': True,
                       'interpretation': 'Raw resource identities and selected pod fields, no task correctness verdict.'}
            if len(json.dumps(summary).encode()) > 256 * 1024:
                summary['pod_specs'] = []
                summary['pod_specs_complete'] = False
            derived_evidence[path.with_suffix('.render-index.json')] = {
                'evidence_kind': 'PUBLIC_STRUCTURE', 'scenario': path.stem}
            path.with_suffix('.render-index.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
            # Compact, complete inventories keep controls observable within the
            # same context bound. In particular, an empty kind is affirmative
            # enumeration evidence only after the entire render parsed above.
            overview = {k: summary[k] for k in ('source_file', 'source_sha256',
                'all_documents_parsed', 'document_count', 'resource_counts')}
            overview['observation_type'] = 'PARSED_PUBLIC_RENDER_OVERVIEW'
            compact = {'.render-overview.json': overview}
            for kind in sorted(set(counts) | {'Job', 'Deployment', 'Pod'}):
                # Kubernetes kind identifiers cannot introduce output paths.
                if not re.fullmatch(r'[A-Za-z][A-Za-z0-9]*', kind):
                    continue
                compact[f'.render-kind-{kind}.json'] = {
                    'source_file': summary['source_file'], 'source_sha256': summary['source_sha256'],
                    'observation_type': 'PARSED_PUBLIC_RESOURCE_INVENTORY',
                    'kind': kind, 'complete_inventory': True,
                    'resources': [r for r in resources if r['kind'] == kind]}
            for suffix, record in compact.items():
                body = json.dumps(record, ensure_ascii=False, indent=2) + '\n'
                if len(body.encode()) > 256 * 1024:
                    continue  # Never substitute a truncated absence proof.
                destination = path.with_suffix(suffix)
                destination.write_text(body)
                derived_evidence[destination] = {
                    'evidence_kind': 'PUBLIC_STRUCTURE', 'scenario': path.stem}
            # Keep each pod-bearing resource independently quotable. A large
            # inventory prefix must not crowd target fields out of review context.
            for index, pod in enumerate(pods):
                record = {'source_file': summary['source_file'], 'source_sha256': summary['source_sha256'],
                          'observation_type': 'PARSED_PUBLIC_POD_SPEC', 'resource': pod,
                          'interpretation': 'Selected fields parsed from this public render; no correctness verdict.'}
                derived_evidence[path.with_suffix(f'.pod-spec-{index:04d}.json')] = {
                    'evidence_kind': 'PUBLIC_STRUCTURE', 'scenario': path.stem,
                    'resource_identity': {k: pod.get(k) for k in ('kind', 'name', 'namespace')}}
                path.with_suffix(f'.pod-spec-{index:04d}.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
        from contractedge.execution.observations import derive_observations, select_observation_adapters
        adapters = self._observation_adapters.get(str(workspace.resolve()))
        if adapters is None:
            adapters = select_observation_adapters(self.public_root, workspace_record['case_id'])
            self._observation_adapters[str(workspace.resolve())] = adapters
        structured, adapter_audit = derive_observations(output/'raw', output/'structured', adapters)
        derived_evidence.update(structured)
        candidates = []
        raw_statuses = []
        for path in sorted(output.rglob('*')):
            if not path.is_file() or path.is_symlink():
                continue
            if 'method-probes' in path.relative_to(output).parts and path not in derived_evidence:
                continue  # Keep raw program/log/report artifacts without duplicating them in model context.
            if path.stat().st_size <= 1024 * 1024 and path.suffix == '.json':
                try:
                    value = json.loads(path.read_text())
                    if isinstance(value, dict):
                        raw_statuses.append(value.get('status'))
                except (ValueError, UnicodeError):
                    pass
            with path.open('rb') as stream:
                raw = stream.read(8 * 1024 * 1024)
            try:
                excerpt = raw.decode('utf-8')
            except UnicodeDecodeError:
                continue
            if not excerpt.strip():
                continue
            offset = 0
            digest = file_sha(path)
            # Typed JSON objects stay whole; splitting them destroys field comparison.
            width = len(excerpt) if path in derived_evidence else 6000
            for start in range(0, len(excerpt), width):
                fragment = excerpt[start:start + width]
                data = fragment.encode()
                candidates.append({'snapshot_id': before, 'kind': 'PUBLIC_OBSERVATION',
                    **derived_evidence.get(path, {'evidence_kind': 'EXECUTION_ONLY'}),
                    'locator': {'path': path.relative_to(output).as_posix(), 'file_sha256': digest,
                        'start_byte': offset, 'copied_bytes': len(data),
                        'truncated': len(data) < path.stat().st_size,
                        'whole_file_read': path.stat().st_size == len(raw)},
                    'content_digest': hashlib.sha256(data).hexdigest(), 'excerpt': fragment,
                    'origin': derived_evidence.get(path, {}).get('origin',
                        'Frozen public launcher; exact raw/derived public output, no hidden oracle')})
                offset += len(data)
        terms = {w for w in re.findall(r'[a-z][a-z0-9_-]{3,}', self.query.casefold())
                 if w not in {'when', 'must', 'with', 'from', 'both', 'should', 'their', 'that', 'into'}}
        candidates.sort(key=lambda o: (-sum(term in o['excerpt'].casefold() for term in terms),
            -int('render-index.json' in o['locator']['path']), o['locator']['path'], o['locator']['start_byte']))
        headers = [o for o in candidates if o['locator']['start_byte'] == 0
                   and (o['locator']['path'].endswith('.render-overview.json')
                        or '.render-kind-' in o['locator']['path'])]
        observations, used, selected_spans = [], 0, set()
        for record in headers + candidates:
            key = (record['locator']['path'], record['locator']['start_byte'])
            if key in selected_spans:
                continue
            if used + len(record['excerpt'].encode()) > 512 * 1024:
                continue
            used += len(record['excerpt'].encode())
            selected_spans.add(key)
            observations.append({'id': 'OBS' + str(len(observations) + 1), **record})
        if result['timed_out']:
            status = 'BUDGET_EXCEEDED'
        elif cleanup['status'] != 'OWNED_CONTAINERS_CLOSED':
            status = 'INFRA_ERROR'
        elif any(s in {'INFRA_ERROR', 'INVALID_OBSERVATION', 'PREPARATION_FAILED'} for s in raw_statuses):
            status = 'INFRA_ERROR'
        elif runtime_failures:
            status = 'EXECUTION_FAILED'
        elif result['returncode'] == 0 and observations:
            status = 'EXECUTION_PASS'
        elif 'EXECUTION_FAILED' in raw_statuses:
            status = 'EXECUTION_FAILED'
        else:
            status = 'INVALID_OBSERVATION'
        report = {'schema_version': 'ce.public_observation.v1', 'status': status,
                  **comparison_identity,
                  'case_id': workspace_record['case_id'], 'snapshot_id': before,
                  'observations': observations, 'launcher': result,
                  'raw_execution_statuses': raw_statuses,
                  'method_probe_status': method_probe_report.get('status') if method_probe_report else 'NOT_RUN',
                  'candidate_runtime_failures': runtime_failures,
                  'observation_adapter_audit': adapter_audit,
                  'runtime_cleanup': cleanup,
                  'source_unchanged_by_public_build': before == after,
                  'hidden_semantic_evaluation': 'NOT_RUN'}
        (output / 'observation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        return report
