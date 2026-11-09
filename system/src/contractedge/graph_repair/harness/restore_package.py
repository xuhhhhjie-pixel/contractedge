"""Re-create a released case's release package from the verified member cache (for re-scoring).

Every file is checked against the case's frozen SUBSET_IDENTITY.json (bytes and SHA-256).
Usage: python restore_package.py CASE_ID   (run in the experiment root)
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path, PurePosixPath

E = Path(__file__).resolve().parent


def main(cid):
    active = E / 'cases' / cid / 'active-case'
    package_root = active / 'package'
    assert not package_root.exists(), 'package already present'
    selection = json.loads((active / 'CASE_SELECTION.json').read_text())
    subset = json.loads((active / 'SUBSET_IDENTITY.json').read_text())
    cat = subset['category']
    assets = json.loads((E / 'assets/BENCHMARK_ASSETS.json').read_text())['classes'][cat]
    catalog = {v['path']: v for v in json.loads((E / 'inputs' / f'{cat}.ARCHIVE_INDEX.json').read_text())['members']}

    def canonical(name):
        seen = set()
        while catalog[name]['type'] == '1':
            assert name not in seen
            seen.add(name)
            p = PurePosixPath(catalog[name]['linkname'])
            name = PurePosixPath(*p.parts[1:]).as_posix()
        return name

    cache = E / 'assets/member-cache' / cat
    package = package_root / assets['archive_root']
    for name, item in subset['files'].items():
        source = cache / hashlib.sha256(canonical(name).encode()).hexdigest()
        assert source.stat().st_size == item['bytes'], name
        assert hashlib.file_digest(source.open('rb'), 'sha256').hexdigest() == item['sha256'], name
        dest = package / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.link(source, dest)
    (active / 'evidence' / 'PACKAGE_RESTORED_FOR_RESCORING.json').write_text(json.dumps(
        {'case_id': cid, 'files': len(subset['files']), 'verified_against': 'SUBSET_IDENTITY.json',
         'selection_case': selection['case_id']}, indent=2))
    print(json.dumps({'case_id': cid, 'restored_files': len(subset['files'])}))


if __name__ == '__main__':
    main(sys.argv[1])
