"""Registered, public-producer-bound JSON observations; never an oracle.

Complete objects are parsed before prompt selection. Candidate stdout cannot
self-declare evidence capabilities. Producer identity is frozen from public
release metadata, separately from candidate source identity.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from contractedge.dataset.benchmark import public_task, checked_public_file

_PROFILES = {
    '617060121f039332a0af17a953de7ba0d171071da59610845ca73e035cc7b864': {
        'id': 'public-object-adoption-v1', 'scenario': 'public-observation',
        'bridge': 'behavior-bridge-v2.tar.gz', 'evidence_kind': 'PUBLIC_STRUCTURE',
        'boundary': 'Candidate production object adoption observed locally; no Kubernetes API or cluster convergence',
        'api_entrypoints': [{'package':'kopf', 'symbol':'adopt', 'language':'python'}]},
    '82fb0dfafe4c2b20a7cbb0eeaf53d10ea33e06184ff163e8b08efbf852e88010': {
        'id': 'public-oci-transport-v1', 'scenario': 'public-production-oci-http-transport',
        'bridge': 'behavior-bridge-v3.tar.gz', 'evidence_kind': 'PUBLIC_BEHAVIOR',
        'boundary': 'Candidate production Helm OCI pull against a local fixture; no live registry or deployment guarantee',
        'api_entrypoints': [{'package':'pkg/clients', 'symbol':'Pull', 'language':'go'}]},
}


def select_observation_adapters(public_root, case_id):
    root = Path(public_root)
    if not (root/'manifest.jsonl').is_file():
        return []
    row, directory, task, manifest = public_task(root, case_id)
    profile = _PROFILES.get(row['task_manifest_sha256'])
    if not profile:
        return []
    # Hash checks bind the observation producer, dependencies and declaration.
    for name in ('environment.json', profile['bridge']):
        checked_public_file(directory, manifest, name)
    environment = json.loads((directory/'environment.json').read_text())
    if not any(s.get('name') == profile['scenario'] for s in environment.get('scenarios', [])):
        raise ValueError('Registered public observation scenario is missing')
    return [{**deepcopy(profile), 'producer_manifest_sha256': row['task_manifest_sha256']}]


def _valid_item(profile, item):
    if not isinstance(item, dict):
        return False
    if profile['id'] == 'public-object-adoption-v1':
        resource = item.get('resource')
        return (isinstance(item.get('name'), str) and 'exception' in item
                and (isinstance(resource, dict) and isinstance(resource.get('kind'), str)
                     and isinstance(resource.get('metadata'), dict)
                     or resource is None and item.get('exception') is not None))
    return (isinstance(item.get('id'), str) and isinstance(item.get('requests'), list)
            and all(isinstance(item.get(k), str) for k in ('setup_error','client_error','pull_error'))
            and all(isinstance(r, dict) and isinstance(r.get('method'), str)
                    and isinstance(r.get('path'), str) and isinstance(r.get('tls'), bool)
                    for r in item['requests']))


def derive_observations(raw_root, derived_root, profiles, *, max_bytes=8*1024*1024,
                        max_object_bytes=256*1024, max_records=256):
    raw_root, derived_root = Path(raw_root), Path(derived_root)
    records, audit = {}, []
    try:
        execution = json.loads((raw_root/'public_execution.json').read_text())
    except (OSError, ValueError):
        execution = {}
    for profile in profiles:
        row = {'adapter_id': profile['id'], 'scenario': profile['scenario']}
        audit.append(row)
        scenarios = [s for s in execution.get('scenarios', []) if s.get('scenario') == profile['scenario']]
        if (len(scenarios) != 1 or scenarios[0].get('exit_code') != 0
                or scenarios[0].get('timed_out') or scenarios[0].get('output_limited')):
            row['status'] = 'EXECUTION_INCOMPLETE'; continue
        source = raw_root/(profile['scenario']+'.stdout')
        if not source.is_file() or source.is_symlink() or source.stat().st_size > max_bytes:
            row['status'] = 'OUTPUT_UNAVAILABLE_OR_LIMITED'; continue
        raw = source.read_bytes()
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            row['status'] = 'PARSE_ERROR'; continue
        items = value.get('observations') if isinstance(value, dict) else None
        if (not isinstance(items, list) or not items or len(items) > max_records
                or not all(_valid_item(profile, item) for item in items)):
            row['status'] = 'SCHEMA_MISMATCH'; continue
        texts = [json.dumps(item, ensure_ascii=False, indent=2)+'\n' for item in items]
        if any(len(t.encode()) > max_object_bytes for t in texts):
            row['status'] = 'OBJECT_TOO_LARGE'; continue
        derived_root.mkdir(parents=True, exist_ok=True)
        for index, (item, text) in enumerate(zip(items, texts)):
            path = derived_root/(profile['id']+f'-{index:03d}.json')
            path.write_text(text)
            metadata = (item.get('resource') or {}).get('metadata', {})
            identity = {k: metadata.get(k) for k in ('name', 'namespace')}
            identity['kind'] = (item.get('resource') or {}).get('kind')
            records[path] = {'evidence_kind': profile['evidence_kind'], 'scenario': profile['scenario'],
                'resource_identity': identity if profile['evidence_kind']=='PUBLIC_STRUCTURE' else {'name':item['id'],'kind':'LocalTransportObservation'},
                'structured_complete': True, 'source_pointer': f'/observations/{index}',
                'source_file': source.name, 'source_file_sha256': hashlib.sha256(raw).hexdigest(),
                'adapter_id': profile['id'], 'producer_manifest_sha256': profile['producer_manifest_sha256'],
                'origin': 'Frozen registered public producer; '+profile['boundary']+'; not independent grading or tamper-proof evidence'}
        row.update(status='COMPLETE', records=len(items), source_sha256=hashlib.sha256(raw).hexdigest())
    return records, audit
