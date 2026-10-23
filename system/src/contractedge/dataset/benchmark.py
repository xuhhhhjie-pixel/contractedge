"""Public-only adapter for the frozen task_path/source.tar.gz benchmark format.

Legacy exports remain readable. New experiments enumerate the frozen public
manifest; neither GT nor historical repairs participate in this projection.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
from typing import Any, Mapping

import yaml

from contractedge.contracts.validation import canonical_digest, parse_method_input
from contractedge.dataset.archive import ArchiveInspection, inspect_archive


CURRENT_RELEASE_ID = 'ContractEdge-benchmark150-v1-20261019'


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def confined(root: Path, relative: str, *, directory: bool = False) -> Path:
    if not isinstance(relative, str) or not relative or '\\' in relative or '\x00' in relative:
        raise ValueError('Invalid benchmark path')
    rel = PurePosixPath(relative)
    if rel.is_absolute() or rel.as_posix() != relative or any(p in {'.', '..'} for p in rel.parts):
        raise ValueError('Benchmark reference must be normalized and relative')
    path = root / relative
    cursor = root
    for part in rel.parts:
        cursor /= part
        if cursor.is_symlink():
            raise ValueError('Benchmark reference traverses a symlink')
    resolved = path.resolve(strict=True)
    resolved.relative_to(root.resolve(strict=True))
    if not (resolved.is_dir() if directory else resolved.is_file()):
        raise ValueError('Benchmark reference has the wrong file type')
    return resolved


def read_members(public_root: str | Path) -> list[dict[str, Any]]:
    root = Path(public_root).resolve(strict=True)
    rows = [json.loads(line) for line in (root / 'manifest.jsonl').read_text().splitlines() if line.strip()]
    ids = [row['case_id'] for row in rows]
    if not rows or len(ids) != len(set(ids)):
        raise ValueError('Empty or duplicate public benchmark members')
    for row in rows:
        if not isinstance(row['case_id'], str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', row['case_id']):
            raise ValueError('Unsafe benchmark case ID')
        if row.get('primary_class') not in {'R1', 'R2', 'R3', 'R4', 'R5'}:
            raise ValueError('Missing authoritative primary_class')
    return rows


def release_identity(benchmark_root: str | Path) -> dict[str, Any]:
    root = Path(benchmark_root).resolve(strict=True)
    entry = json.loads((root / 'RELEASE_ENTRY.json').read_text())
    if entry.get('status') != 'FROZEN_LOCAL_SUPPORTED_PLATFORM_RELEASE':
        raise ValueError('Benchmark has not been frozen')
    if file_sha(root / 'public/manifest.jsonl') != entry['public_manifest_sha256']:
        raise ValueError('Frozen public membership identity mismatch')
    members = read_members(root / 'public')
    if len(members) != entry['selected_cases'] or dict(Counter(r['primary_class'] for r in members)) != entry['by_class']:
        raise ValueError('Frozen benchmark membership/class counts mismatch')
    return {'release_id': entry['version'], 'release_entry_sha256': file_sha(root / 'RELEASE_ENTRY.json'),
            'public_manifest_sha256': entry['public_manifest_sha256'], 'selected_cases': len(members),
            'by_class': entry['by_class']}


def public_task(public_root: str | Path, case_id: str) -> tuple[dict, Path, dict, dict]:
    root = Path(public_root).resolve(strict=True)
    selected = [r for r in read_members(root) if r['case_id'] == case_id]
    if len(selected) != 1:
        raise ValueError('Case is outside the selected public benchmark')
    row = selected[0]
    directory = confined(root, row['task_path'], directory=True)
    manifest_path = confined(directory, 'manifest.json')
    if file_sha(manifest_path) != row['task_manifest_sha256']:
        raise ValueError('Task manifest identity mismatch')
    manifest = json.loads(manifest_path.read_text())
    if manifest['case_id'] != case_id or manifest['source_sha256'] != row['source_sha256']:
        raise ValueError('Task/source identity conflict')
    for name in ['task.yaml']:
        if name not in manifest['files'] or file_sha(confined(directory, name)) != manifest['file_sha256'][name]:
            raise ValueError('Public task bytes do not match the frozen task manifest')
    task = yaml.safe_load((directory / 'task.yaml').read_text())
    if not isinstance(task, dict):
        raise ValueError('Public task must be an object')
    return row, directory, task, manifest


def checked_public_file(directory: Path, manifest: Mapping, relative: str) -> Path:
    if relative not in manifest['files'] or relative not in manifest['file_sha256']:
        raise ValueError('Public reference is outside task whitelist: ' + relative)
    path = confined(directory, relative)
    if file_sha(path) != manifest['file_sha256'][relative]:
        raise ValueError('Public input hash mismatch: ' + relative)
    return path


def execution_contract(directory: Path, task: Mapping, manifest: Mapping) -> dict:
    for name in [task.get('execution_config'), task.get('execution_contract'), 'environment.json']:
        if isinstance(name, str) and name in manifest['files']:
            value = json.loads(checked_public_file(directory, manifest, name).read_text())
            if isinstance(value, dict):
                return value
    return {}


def editable_policy(task: Mapping, environment: Mapping) -> dict:
    allowed = task.get('allowed_paths') or task.get('editable_scope') or environment.get('editable_paths')
    # A scope absent from public metadata is not permission to edit all files.
    if not allowed:
        allowed = environment.get('allowed_paths') or environment.get('patchable_paths') or []
    forbidden = list(task.get('forbidden_paths') or environment.get('forbidden_paths')
                     or environment.get('forbidden_candidate_paths') or [])
    return {'allowed_paths': list(allowed), 'forbidden_paths': forbidden,
            'policy_status': 'DECLARED' if allowed else 'PLAN_TARGETS_ONLY_PUBLIC_SCOPE_UNSPECIFIED'}


def public_execution_capabilities(directory: Path, task: Mapping, manifest: Mapping) -> dict:
    """A lossless, public-contract-derived capability view, never a new permission.

    In particular a command named build.sh is not classified as a behavioral
    verifier. Keep its declared observation boundary and actual argv visible.
    """
    environment = execution_contract(directory, task, manifest)
    names = [task.get('execution_config'), task.get('execution_contract'), 'environment.json']
    contract_name = next((n for n in names if isinstance(n, str) and n in manifest['files']), 'task.yaml')
    result = {'schema_version': 'ce.public_execution_capabilities.v1',
        'source': {'path': contract_name, 'sha256': manifest.get('file_sha256', {}).get(contract_name),
                   'task_observation_limit_source': 'task.yaml:observation_limit'},
        'task_observation_limit': task.get('observation_limit'),
        'environment_observation_limit': environment.get('observation_limit'),
        'observation_boundary': environment.get('observation_boundary'),
        'public_scope': environment.get('public_scope'),
        'native_input_boundary': environment.get('native_input_boundary'),
        'candidate_command': environment.get('candidate_command'),
        'build_entrypoint': environment.get('build_entrypoint', task.get('build_entrypoint')),
        'public_runner': task.get('public_self_check_runner'),
        'expected_artifact': task.get('expected_artifact'),
        'scenarios': [{'name': s.get('name'), 'argv': list(s.get('argv', []))}
                      for s in environment.get('scenarios', []) if isinstance(s, Mapping)],
        'editable_policy': editable_policy(task, environment), 'network': environment.get('network'),
        'interpretation': 'Declared public execution/observation only. Build success is not behavioral success; unavailable behavior stays UNKNOWN. No additional execution or edit permission.'}

    if 'registered_public_checks' in environment:
        result['registered_public_checks'] = environment['registered_public_checks']
    return result


def method_input_from_task(public_root: str | Path, case_id: str, *,
                           release_id: str | None = None,
                           inspection: ArchiveInspection | None = None) -> tuple[dict, dict]:
    root = Path(public_root).resolve(strict=True)
    if release_id is None:
        if not (root.parent / 'RELEASE_ENTRY.json').is_file():
            raise ValueError('Current task format requires a frozen release entry or an explicit trusted release_id')
        release_id = release_identity(root.parent)['release_id']
    row, directory, task, manifest = public_task(root, case_id)
    requirement = task.get('requirement')
    if requirement is None and isinstance(task.get('requirement_file'), str):
        requirement = checked_public_file(directory, manifest, task['requirement_file']).read_text()
    if not isinstance(requirement, str) or not requirement.strip():
        raise ValueError('Missing public requirement')
    source = checked_public_file(directory, manifest, task.get('source_archive', 'source.tar.gz'))
    if file_sha(source) != row['source_sha256']:
        raise ValueError('Broken archive identity mismatch')
    inspection = inspection or inspect_archive(source)
    if not inspection.file_digests:
        raise ValueError('Broken source archive has no regular source files')
    environment = execution_contract(directory, task, manifest)
    policy = editable_policy(task, environment)
    hints = []
    for path in policy['allowed_paths']:
        if not any(char in path for char in '*?['):
            hints.append({'kind': 'path', 'value': path, 'raw_text': path,
                          'origin_ref': 'public.execution.editable_paths'})
    for scenario in environment.get('scenarios', []):
        for argument in scenario.get('argv', []):
            if isinstance(argument, str) and argument.startswith('/work/') and not argument.startswith('/work/.'):
                path = argument[len('/work/'):]
                hints.append({'kind': 'path', 'value': path, 'raw_text': argument,
                              'origin_ref': 'public.execution.scenarios.argv'})
    if not hints:
        hints.append({'kind': 'concept', 'value': requirement, 'raw_text': requirement,
                      'origin_ref': 'public.requirement'})
    artifacts = []
    # Only explicitly published small textual inputs; never unpack dependency archives into a prompt.
    for name in sorted(manifest['files']):
        if name in {'task.yaml', 'manifest.json', task.get('requirement_file'), 'environment.json', 'execution_contract.json'}:
            continue
        if Path(name).suffix.lower() not in {'.yaml', '.yml', '.json', '.cfg', '.conf'}:
            continue
        path = confined(directory, name)
        if path.stat().st_size > 128 * 1024 or sum(len(a['content'].encode()) for a in artifacts) + path.stat().st_size > 512 * 1024:
            continue
        data = checked_public_file(directory, manifest, name).read_bytes()
        try:
            content = data.decode('utf-8')
        except UnicodeDecodeError:
            continue
        artifacts.append({'id': 'INPUT' + str(len(artifacts) + 1),
                          'path': 'declared-artifacts/' + name, 'content': content,
                          'content_digest': hashlib.sha256(data).hexdigest(),
                          'media_type': 'application/json' if name.endswith('.json') else 'text/plain'})
    observable = task.get('expected_artifact') or task.get('observation_limit')
    if not isinstance(observable, str):
        observable = None
    payload = {'schema_version': 'ce.method_input.v1_1', 'case_id': case_id, 'release_id': release_id,
               'original_broken_revision': 'archive-sha256:' + row['source_sha256'],
               'archive_path': source.relative_to(root).as_posix(), 'archive_sha256': row['source_sha256'],
               'source_root': inspection.source_root, 'initial_snapshot_id': inspection.snapshot_id,
               'requirement_text': requirement, 'public_observable_text': observable,
               'scope_hints': hints, 'public_evidence': [], 'declared_artifacts': artifacts}
    payload['input_digest'] = canonical_digest(payload)
    parse_method_input(payload)
    audit = {'case_id': case_id, 'primary_class': row['primary_class'],
             'source_file_count': len(inspection.file_digests), 'expanded_bytes': inspection.expanded_bytes,
             'snapshot_id': inspection.snapshot_id, 'source_root': inspection.source_root,
             'task_manifest_sha256': row['task_manifest_sha256'], 'source_sha256': row['source_sha256'],
             'input_digest': payload['input_digest'], 'editable_policy': policy,
             'public_route': task.get('public_self_check_runner') or task.get('execution_config') or 'environment.json',
             'public_execution_capabilities': public_execution_capabilities(directory, task, manifest),
             'private_materials_loaded': False}
    return payload, audit


def prepare_benchmark(benchmark_root: str | Path, output: str | Path) -> dict:
    """Materialize all selected MethodInputs, streaming archives without extracting trees."""
    benchmark = Path(benchmark_root).resolve(strict=True)
    identity = release_identity(benchmark)
    public_root = benchmark / 'public'
    destination = Path(output).resolve()
    if destination.is_relative_to(benchmark) or benchmark.is_relative_to(destination):
        raise ValueError('Prepared method view must be independent of frozen benchmark')
    destination.mkdir(parents=True, exist_ok=False)
    public_output = destination / 'public'
    public_output.mkdir()
    records, members, errors = [], [], []
    for row in read_members(public_root):
        try:
            value, audit = method_input_from_task(public_root, row['case_id'], release_id=identity['release_id'])
            case = public_output / 'cases' / row['case_id']
            case.mkdir(parents=True)
            archive = confined(public_root, value['archive_path'])
            target = case / 'source.tar.gz'
            try:
                os.link(archive, target)
            except OSError:
                shutil.copyfile(archive, target)
            value['archive_path'] = target.relative_to(public_output).as_posix()
            value.pop('input_digest')
            value['input_digest'] = canonical_digest(value)
            parse_method_input(value)
            for artifact in value['declared_artifacts']:
                path = case / artifact['path']
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(artifact['content'])
            method_path = case / 'method_input.json'
            method_path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
            audit['prepared_input_digest'] = value['input_digest']
            records.append(audit)
            members.append({'case_id': row['case_id'], 'method_input': method_path.relative_to(public_output).as_posix(),
                            'method_input_sha256': file_sha(method_path), 'archive_sha256': value['archive_sha256']})
        except (OSError, ValueError, KeyError) as exc:
            errors.append({'case_id': row['case_id'], 'error': type(exc).__name__ + ': ' + str(exc)})
        (destination / 'progress.json').write_text(json.dumps({'processed': len(records) + len(errors),
            'prepared': len(records), 'errors': errors}, ensure_ascii=False, indent=2) + '\n')
    (public_output / 'manifest.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in members))
    report = {'schema_version': 'ce.frozen_benchmark_projection.v1', **identity,
              'status': 'PASS' if not errors and len(members) == identity['selected_cases'] else 'INCOMPLETE',
              'prepared': len(members), 'errors': errors, 'records': records,
              'private_materials_loaded': False, 'source_trees_extracted': 0,
              'scope': 'Public task/source projection only. Prior experiments are historical reference.'}
    (destination / 'preparation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report
