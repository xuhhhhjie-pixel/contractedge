"""Build the read-only ce-tools directory mounted into the method container.

Layout: lib/contractedge/ (source copy), bin/ce-* (entry scripts run by the mounted venv312
interpreter in isolated mode), VARIANTS.json (frozen variant table), MANIFEST.json (hashes).
Usage: python package_tools.py SYSTEM_SRC_DIR TARGET_DIR
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

PYTHON = '/tmp/contractedge-deepseek-preflight-20261019/venv312/bin/python'
COMMANDS = ['ce-search', 'ce-read', 'ce-follow', 'ce-links', 'ce-graph', 'ce-closure', 'ce-probe', 'ce-ckpt',
            'ce-rollback', 'ce-submit']
ENTRY = f'''#!{PYTHON} -I
import sys
sys.path.insert(0, '/ce-tools/lib')
from contractedge.graph_repair.cli.main import main
sys.exit(main())
'''


def main(src: str, target: str) -> None:
    src, target = Path(src), Path(target)
    assert not target.exists(), 'Refuse to overwrite an existing tool package'
    shutil.copytree(src / 'contractedge', target / 'lib' / 'contractedge',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'harness'))
    (target / 'bin').mkdir()
    for name in COMMANDS:
        path = target / 'bin' / name
        path.write_text(ENTRY)
        path.chmod(0o755)
    sys.path.insert(0, str(target / 'lib'))
    from contractedge.graph_repair.config import VARIANTS
    (target / 'VARIANTS.json').write_text(json.dumps({k: v.to_json() for k, v in VARIANTS.items()}, indent=1, sort_keys=True))
    files = {p.relative_to(target).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(target.rglob('*')) if p.is_file()}
    (target / 'MANIFEST.json').write_text(json.dumps({'schema': 'contractedge.ce_tools.v1', 'commands': COMMANDS,
                                                      'interpreter': PYTHON, 'files': files}, indent=1))
    for path in sorted(target.rglob('*'), reverse=True):
        path.chmod(0o555 if path.is_dir() or path.parent.name == 'bin' else 0o444)
    target.chmod(0o555)
    print(json.dumps({'tools': str(target), 'files': len(files)}))


if __name__ == '__main__':
    main(*sys.argv[1:3])
