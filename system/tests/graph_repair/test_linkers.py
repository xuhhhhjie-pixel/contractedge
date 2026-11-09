"""Each linker on a small positive fixture and a negative one."""
from conftest import make_repo
from contractedge.graph_repair import linkers


def pairs(result):
    return {(c['linker'], c['from']['path'], c['to']['path']) for c in result['candidates']}


def test_helm_values_key_to_templates(workspace):
    result = linkers.run(workspace.root, 'ingress.host', ['helm'])
    got = pairs(result)
    assert ('helm', 'chart/values.yaml', 'chart/templates/ingress.yaml') in got
    assert ('helm', 'chart/values.yaml', 'chart/templates/admin-ingress.yaml') in got
    assert not any(t == 'chart/templates/deploy.yaml' for _, _, t in got)


def test_helm_seed_path_line(workspace):
    result = linkers.run(workspace.root, 'chart/templates/deploy.yaml:3', ['helm'])
    assert ('helm', 'chart/values.yaml', 'chart/templates/deploy.yaml') in pairs(result)


def test_helm_negative(workspace):
    assert linkers.run(workspace.root, 'nothing.here', ['helm'])['count'] == 0


def test_go_tags_and_crd(tmp_path):
    root = tmp_path / 'r'
    make_repo(root, {
        'api/v1/types.go': 'package v1\ntype Spec struct {\n\tReplicas int `json:"replicas,omitempty"`\n\tName string `json:"name"`\n}\n',
        'controllers/reconcile.go': 'package c\nfunc f(o *Obj) int { return o.Spec.Replicas }\n',
        'config/crd/bases/x.yaml': 'kind: CustomResourceDefinition\nspec:\n  versions:\n  - schema:\n      openAPIV3Schema:\n'
                                   '        properties:\n          spec:\n            properties:\n              replicas:\n'
                                   '                type: integer\n',
        'config/samples/x.yaml': 'spec:\n  replicas: 3\n',
    })
    tags = pairs(linkers.run(root, 'Replicas', ['go_tags']))
    assert ('go_tags', 'api/v1/types.go', 'controllers/reconcile.go') in tags
    assert ('go_tags', 'config/samples/x.yaml', 'api/v1/types.go') in tags
    crd = pairs(linkers.run(root, 'replicas', ['crd']))
    assert ('crd', 'config/crd/bases/x.yaml', 'api/v1/types.go') in crd
    assert ('crd', 'api/v1/types.go', 'controllers/reconcile.go') in crd
    assert linkers.run(root, 'Name', ['crd'])['count'] == 0


def test_env_and_flags(tmp_path):
    root = tmp_path / 'r'
    make_repo(root, {
        'main.go': 'package main\nimport ("flag";"os")\nvar addr = flag.String("metrics-addr", ":8080", "")\n'
                   'func f() string { return os.Getenv("WATCH_NAMESPACE") }\n',
        'deploy/manager.yaml': 'spec:\n  containers:\n  - args:\n    - --metrics-addr=:9090\n    env:\n    - name: WATCH_NAMESPACE\n',
    })
    got = pairs(linkers.run(root, 'WATCH_NAMESPACE', ['env_flags']))
    assert ('env_flags', 'deploy/manager.yaml', 'main.go') in got
    assert ('env_flags', 'deploy/manager.yaml', 'main.go') in pairs(linkers.run(root, 'metrics-addr', ['env_flags']))
    assert all(t != 'main.go' for _, _, t in pairs(linkers.run(root, 'OTHER_VAR', ['env_flags'])))


def test_kustomize_patch_targets(tmp_path):
    root = tmp_path / 'r'
    make_repo(root, {
        'base/kustomization.yaml': 'resources:\n- deployment.yaml\n',
        'base/deployment.yaml': 'apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\n',
        'overlay/kustomization.yaml': 'resources:\n- ../base\npatches:\n- path: patch.yaml\n',
        'overlay/patch.yaml': 'kind: Deployment\nmetadata:\n  name: web\n',
    })
    got = pairs(linkers.run(root, 'overlay/kustomization.yaml', ['kustomize']))
    assert ('kustomize', 'overlay/kustomization.yaml', 'base/kustomization.yaml') in got
    assert ('kustomize', 'overlay/kustomization.yaml', 'overlay/patch.yaml') in got
    assert ('kustomize', 'base/kustomization.yaml', 'base/deployment.yaml') in pairs(
        linkers.run(root, 'base/kustomization.yaml', ['kustomize']))


def test_deps_call_sites(tmp_path):
    root = tmp_path / 'r'
    make_repo(root, {'a.go': 'package a\nimport (\n\tregistry "oras.land/oras-go/v2/registry/remote"\n)\n'
                             'func f() { registry.NewRepository("x") }\n'})
    result = linkers.run(root, 'registry', ['deps'])
    assert result['candidates'][0]['to']['path'] == 'dependency:oras.land/oras-go/v2/registry/remote'
    assert linkers.run(root, 'unrelated', ['deps'])['count'] == 0


def test_broken_linker_is_reported_not_fatal(workspace, monkeypatch):
    def boom(*a):
        raise RuntimeError('x')
    monkeypatch.setitem(linkers.LINKERS, 'kustomize', boom)
    result = linkers.run(workspace.root, 'ingress.host')
    assert 'kustomize' in result['linker_errors'] and result['count'] > 0


def test_prefilter_limits_scanned_files_on_large_repos(tmp_path):
    files = {f'gen/zz_{i}.go': f'package gen\nvar X{i} = {i}\n' for i in range(400)}
    files.update({'chart/values.yaml': 'ingress:\n  host: a\n',
                  'chart/templates/ing.yaml': 'host: {{ .Values.ingress.host }}\n'})
    root = tmp_path / 'big'
    make_repo(root, files)
    repo = linkers.Repo(root)
    repo.prefilter(linkers.seed_tokens(repo, 'ingress.host', None, None))
    assert repo.focus and not any(p.startswith('gen/') for p in repo.focus)
    result = linkers.run(root, 'ingress.host', ['helm'])
    assert ('helm', 'chart/values.yaml', 'chart/templates/ing.yaml') in pairs(result)


def test_time_budget_returns_partial_results(workspace):
    result = linkers.run(workspace.root, 'ingress.host', budget=0)
    assert result['linker_errors'] and all('LinkerTimeout' in v for v in result['linker_errors'].values())
