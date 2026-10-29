"""Controller-side development admission from pinned release partition metadata.

This metadata is not an oracle and is never added to model prompts.
"""
import hashlib
import json
from pathlib import Path

from contractedge.dataset.benchmark import read_members, file_sha


def development_case_ids(public_root, config):
    flag = config.get('development_only', False)
    if type(flag) is not bool:
        raise ValueError('development_only must be a boolean')
    if not flag:
        return None
    root = Path(public_root).resolve(strict=True)
    expected = config.get('expected_partition_manifest_sha256')
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError('development_only requires a frozen partition hash')
    partition = root.parent / 'partition/member_groups.jsonl'
    try:
        data = partition.read_bytes()
    except OSError as exc:
        raise ValueError('Frozen partition metadata is unavailable') from exc
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('Frozen partition identity mismatch')
    manifest_digest = file_sha(root / 'manifest.jsonl')
    if config.get('expected_public_manifest_sha256', manifest_digest) != manifest_digest:
        raise ValueError('Frozen public manifest identity mismatch')
    public = {r['case_id']: r for r in read_members(root)}
    rows = [json.loads(line) for line in data.decode().splitlines() if line.strip()]
    if len(rows) != len(public) or {r['case_id'] for r in rows} != set(public):
        raise ValueError('Frozen partition membership mismatch')
    if any(r.get('primary_class') != public[r['case_id']]['primary_class']
           or r.get('candidate_split') not in {'development', 'validation', 'blind_test'} for r in rows):
        raise ValueError('Frozen partition labels are invalid')
    return frozenset(r['case_id'] for r in rows if r['candidate_split'] == 'development')


def check_case_admission(public_root, case_id, config):
    allowed = development_case_ids(public_root, config)
    if allowed is not None and case_id not in allowed:
        raise ValueError('development_only rejects this non-development case before model execution: ' + case_id)
