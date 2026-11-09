"""Fixtures: a throwaway Git workspace, a state dir outside it, and ce-* commands on PATH."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / 'src'
COMMANDS = ['ce-search', 'ce-read', 'ce-follow', 'ce-links', 'ce-graph', 'ce-closure', 'ce-probe', 'ce-ckpt',
            'ce-rollback', 'ce-submit']


def git(cwd, *args):
    return subprocess.run(['git', '-c', 'safe.directory=*', *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def make_repo(root: Path, files: dict) -> str:
    root.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(root, 'init', '-q')
    git(root, 'add', '-A')
    git(root, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'base')
    return git(root, 'rev-parse', 'HEAD')


CHART = {
    'chart/values.yaml': 'ingress:\n  enabled: true\n  annotations: {}\n  host: example.com\nimage:\n  tag: v1\n',
    'chart/templates/ingress.yaml': 'apiVersion: networking.k8s.io/v1\nkind: Ingress\nmetadata:\n  name: app\n'
                                    '  annotations: {}\nspec:\n  rules:\n  - host: {{ .Values.ingress.host }}\n',
    'chart/templates/admin-ingress.yaml': 'kind: Ingress\nmetadata:\n  name: admin\nspec:\n  host: {{ .Values.ingress.host }}\n',
    'chart/templates/deploy.yaml': 'kind: Deployment\nspec:\n  image: app:{{ .Values.image.tag }}\n',
    'app.txt': 'state=broken\n',
    'tests/app_test.txt': 'test\n',
    'README.md': 'demo\n',
}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    ws, st, inputs = tmp_path / 'testbed', tmp_path / 'ce-state', tmp_path / 'ce-inputs'
    base = make_repo(ws, CHART)
    st.mkdir()
    inputs.mkdir()
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    for name in COMMANDS:
        path = bindir / name
        path.write_text(f'#!{sys.executable}\nimport sys\nsys.path.insert(0, {str(SRC)!r})\n'
                        'from contractedge.graph_repair.cli.main import main\nsys.exit(main())\n')
        path.chmod(0o755)
    # Fake public check: passes once app.txt says fixed.
    public = bindir / 'ce-public'
    public.write_text('#!/bin/sh\ncd "$CE_WORKSPACE" && grep -q fixed app.txt && echo PUBLIC_OK || { echo PUBLIC_FAIL app.txt:1; exit 1; }\n')
    public.chmod(0o755)
    run = bindir / 'ce-run'
    run.write_text('#!/bin/sh\ncd "$CE_WORKSPACE" && exec "$@"\n')
    run.chmod(0o755)
    env = {'CE_WORKSPACE': str(ws), 'CE_STATE': str(st), 'CE_INPUTS': str(inputs),
           'PATH': f'{bindir}{os.pathsep}{os.environ["PATH"]}'}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    from contractedge.graph_repair import state
    monkeypatch.setattr(state, 'WORKSPACE', ws)
    monkeypatch.setattr(state, 'STATE', st)
    monkeypatch.setattr(state, 'INPUTS', inputs)

    def configure(variant='full', policy=None):
        from contractedge.graph_repair.config import variant as load
        value = {'variant': load(variant).to_json(), 'baseline_commit': base, 'tool_timeout_seconds': 60,
                 'policy': policy or {'patchable_paths': ['chart/**', 'app.txt'], 'forbidden_paths': ['tests/**']}}
        (inputs / 'run.json').write_text(json.dumps(value))
        return value
    configure()
    return type('W', (), {'root': ws, 'state': st, 'inputs': inputs, 'base': base, 'bin': bindir, 'env': env,
                          'configure': staticmethod(configure)})


def run_tool(w, *argv, check=False):
    done = subprocess.run(list(argv), cwd=w.root, capture_output=True, text=True, env={**os.environ, **w.env})
    if check:
        assert done.returncode == 0, done.stdout + done.stderr
    return done
