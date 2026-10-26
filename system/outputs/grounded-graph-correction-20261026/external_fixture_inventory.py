"""Fail-fast identity check for explicitly external public full-suite fixtures."""
import hashlib,json
from pathlib import Path
from contractedge.source.snapshot import compute_snapshot

def verify_external_fixtures(root, manifest):
    root=Path(root);manifest=Path(manifest);expected=json.loads(manifest.read_text());actual={}
    for record in expected['entries']:
        path=root/record['path']
        if not path.exists() or str(path.resolve(strict=True))!=record['canonical_path']:
            raise ValueError('Missing or redirected external public fixture: '+record['path'])
        digest=(compute_snapshot(path).snapshot_id if record['kind']=='directory'
                else hashlib.sha256(path.read_bytes()).hexdigest())
        if digest!=record['digest']:
            raise ValueError('Changed external public fixture: '+record['path'])
        actual[record['path']]=digest
    return dict(status='PASS',manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),digests=actual)
