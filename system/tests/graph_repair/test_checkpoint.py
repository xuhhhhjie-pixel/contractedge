"""Checkpoints never move HEAD, never touch the index, never reach the exported patch; restore works."""
import subprocess

from conftest import git, run_tool
from contractedge.graph_repair import checkpoint


def sh(w, script):
    script = script.replace('/testbed', str(w.root)).replace('/ce-state', str(w.state))
    return subprocess.run(['bash', '-c', script], capture_output=True, text=True)


def export(w):
    """The baseline exporter: git add -N . && git diff BASELINE."""
    git(w.root, 'add', '-N', '.')
    return subprocess.run(['git', '-c', 'safe.directory=*', 'diff', '--binary', w.base], cwd=w.root,
                          capture_output=True, text=True).stdout


def test_checkpoint_keeps_head_index_and_patch(workspace):
    w = workspace
    (w.root / 'app.txt').write_text('state=fixed\n')
    (w.root / 'new.txt').write_text('untracked\n')
    index_before = (w.root / '.git/index').read_bytes()
    out = sh(w, checkpoint.checkpoint_script(1)).stdout
    tree, commit = checkpoint.parse(out, 'TREE'), checkpoint.parse(out, 'COMMIT')
    assert tree and commit
    assert git(w.root, 'rev-parse', 'HEAD') == w.base
    assert (w.root / '.git/index').read_bytes() == index_before
    assert git(w.root, 'rev-parse', 'refs/ce/ckpt/1') == commit
    assert 'new.txt' in git(w.root, 'ls-tree', '-r', '--name-only', commit)
    patch = export(w)
    assert 'ce-ckpt' not in patch and 'refs/ce' not in patch
    assert set(l[6:] for l in patch.splitlines() if l.startswith('+++ b/')) == {'app.txt', 'new.txt'}


def test_unchanged_tree_creates_no_new_checkpoint(workspace):
    w = workspace
    out1 = sh(w, checkpoint.checkpoint_script(1)).stdout
    tree = checkpoint.parse(out1, 'TREE')
    out2 = sh(w, checkpoint.checkpoint_script(2, tree)).stdout
    assert checkpoint.parse(out2, 'TREE') == tree and checkpoint.parse(out2, 'COMMIT') is None


def test_restore_returns_exact_tree(workspace):
    w = workspace
    (w.root / 'app.txt').write_text('state=fixed\n')
    (w.root / 'keep.txt').write_text('k\n')
    commit = checkpoint.parse(sh(w, checkpoint.checkpoint_script(1)).stdout, 'COMMIT')
    (w.root / 'app.txt').write_text('state=worse\n')
    (w.root / 'scratch.py').write_text('x\n')
    (w.root / 'README.md').unlink()
    result = checkpoint.restore(commit)
    assert (w.root / 'app.txt').read_text() == 'state=fixed\n'
    assert (w.root / 'README.md').exists() and (w.root / 'keep.txt').exists()
    assert not (w.root / 'scratch.py').exists() and result['removed_files'] == ['scratch.py']
    assert git(w.root, 'rev-parse', 'HEAD') == w.base


def test_rollback_cli_best_prefers_public_pass(workspace):
    w = workspace
    (w.root / 'app.txt').write_text('state=fixed\n')
    c1 = sh(w, checkpoint.checkpoint_script(1)).stdout
    (w.root / 'app.txt').write_text('state=worse\n')
    c2 = sh(w, checkpoint.checkpoint_script(2, checkpoint.parse(c1, 'TREE'))).stdout
    rows = [{'n': 1, 'commit': checkpoint.parse(c1, 'COMMIT'), 'tree': checkpoint.parse(c1, 'TREE'), 'changed': True},
            {'n': 2, 'commit': checkpoint.parse(c2, 'COMMIT'), 'tree': checkpoint.parse(c2, 'TREE'), 'changed': True}]
    from contractedge.graph_repair import state
    state.write_json(w.state / 'checkpoints.json', rows)
    state.write_json(w.state / 'results.json', {rows[0]['tree']: {'public': {'exit_code': 0}},
                                                rows[1]['tree']: {'public': {'exit_code': 1}}})
    assert checkpoint.best()['n'] == 1
    run_tool(w, 'ce-rollback', 'best', check=True)
    assert (w.root / 'app.txt').read_text() == 'state=fixed\n'


def test_state_dir_never_appears_in_patch(workspace):
    w = workspace
    run_tool(w, 'ce-graph', 'show', check=True)
    run_tool(w, 'ce-links', 'ingress.host', check=True)
    (w.root / 'app.txt').write_text('state=fixed\n')
    files = set(l[6:] for l in export(w).splitlines() if l.startswith('+++ b/'))
    assert files == {'app.txt'}
