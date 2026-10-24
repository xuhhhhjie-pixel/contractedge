"""Copy/publish real trees while hostile paths and links stay unread."""
import importlib.util
import os
from pathlib import Path

import pytest


def module():
    path = Path(__file__).resolve().parents[1] / 'scripts/isolated_paths.py'
    assert path.is_file(), 'isolated snapshot/publish boundary is not implemented'
    spec = importlib.util.spec_from_file_location('isolated_paths_test', path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def source_tree(tmp_path):
    root = tmp_path/'case'; (root/'workspace/source').mkdir(parents=True)
    (root/'workspace/source/main.go').write_text('public source\n')
    return root


@pytest.mark.parametrize('relative', ['../private', '/etc', 'workspace/../../private', '.', ''])
def test_snapshot_path_cannot_escape_case(tmp_path, relative):
    safe = module(); root = source_tree(tmp_path)
    with safe.SafeRoot(root) as boundary:
        with pytest.raises(safe.UnsafePath):
            boundary.clone_source(relative, tmp_path/'copy')
    assert not (tmp_path/'copy').exists()


def test_snapshot_skips_untrusted_git_and_copies_executable_as_independent_bytes(tmp_path):
    safe = module(); root = source_tree(tmp_path)
    source = root/'workspace/source'
    (source/'.git').symlink_to(tmp_path/'private-git')
    os.chmod(source/'main.go', 0o755)
    with safe.SafeRoot(root) as boundary:
        manifest = boundary.clone_source('workspace/source', tmp_path/'copy')
    assert (tmp_path/'copy/main.go').read_text() == 'public source\n'
    assert not (tmp_path/'copy/.git').exists()
    assert manifest['files'] == {'main.go': 'f9a12765569513628027f2683aef1eb729db157758d76adf7f04c48ebfb9ebf6'}
    assert (tmp_path/'copy/main.go').stat().st_mode & 0o111
    (tmp_path/'copy/main.go').write_text('modified')
    assert (source/'main.go').read_text() == 'public source\n'


@pytest.mark.parametrize('kind', ['file-symlink', 'directory-symlink', 'fifo', 'hardlink'])
def test_unsafe_source_is_rejected_without_partial_candidate(tmp_path, kind):
    safe = module(); root = source_tree(tmp_path)
    private = tmp_path/'private'; private.mkdir(); (private/'secret').write_text('private canary')
    attack = root/'workspace/source/attack'
    if kind == 'file-symlink': attack.symlink_to(private/'secret')
    elif kind == 'directory-symlink': attack.symlink_to(private, target_is_directory=True)
    elif kind == 'fifo': os.mkfifo(attack)
    else: os.link(private/'secret', attack)
    with safe.SafeRoot(root) as boundary:
        with pytest.raises(safe.UnsafePath):
            boundary.clone_source('workspace/source', tmp_path/'copy')
    assert not (tmp_path/'copy').exists()
    assert (private/'secret').read_text() == 'private canary'


def test_symlink_ancestor_is_not_followed(tmp_path):
    safe = module(); root = source_tree(tmp_path)
    (root/'redirect').symlink_to(tmp_path)
    with safe.SafeRoot(root) as boundary:
        with pytest.raises(safe.UnsafePath): boundary.publish_bytes(b'bad', 'redirect/private')
        with pytest.raises(safe.UnsafePath): boundary.clone_source('redirect/case', tmp_path/'copy')
    assert not (tmp_path/'private').exists()


def test_pinned_root_does_not_follow_replaced_root_symlink(tmp_path):
    safe = module(); root = source_tree(tmp_path)
    private = tmp_path/'private'; private.mkdir()
    with safe.SafeRoot(root) as boundary:
        root.rename(tmp_path/'old-case'); root.symlink_to(private, target_is_directory=True)
        boundary.publish_bytes(b'public', 'receipt.json')
    assert (tmp_path/'old-case/receipt.json').read_bytes() == b'public'
    assert not (private/'receipt.json').exists()


def test_publish_never_overwrites_existing_file_or_directory(tmp_path):
    safe = module(); root = source_tree(tmp_path)
    stage = tmp_path/'stage'; stage.mkdir(); (stage/'check.json').write_text('{"status":"PASS"}')
    with safe.SafeRoot(root) as boundary:
        boundary.publish_bytes(b'original', 'receipt')
        with pytest.raises(safe.UnsafePath): boundary.publish_bytes(b'changed', 'receipt')
        boundary.publish_dir(stage, 'observations')
        with pytest.raises(safe.UnsafePath): boundary.publish_dir(stage, 'observations')
    assert (root/'receipt').read_bytes() == b'original'
    assert (root/'observations/check.json').read_text() == '{"status":"PASS"}'
    (root/'observations/check.json').write_text('candidate changed')
    assert (stage/'check.json').read_text() == '{"status":"PASS"}'


@pytest.mark.parametrize('limits', [{'max_files': 0}, {'max_bytes': 3}, {'max_file_bytes': 3}])
def test_snapshot_limits_reject_without_publishing_partial_tree(tmp_path, limits):
    safe = module(); root = source_tree(tmp_path)
    with safe.SafeRoot(root) as boundary:
        with pytest.raises(safe.UnsafePath): boundary.clone_source('workspace/source', tmp_path/'copy', limits)
    assert not (tmp_path/'copy').exists()


def test_published_directory_rejects_symlink_outputs_and_keeps_private_target(tmp_path):
    safe = module(); root = source_tree(tmp_path)
    stage = tmp_path/'stage'; stage.mkdir()
    target = tmp_path/'secret'; target.write_text('private')
    (stage/'report.json').symlink_to(target)
    with safe.SafeRoot(root) as boundary:
        with pytest.raises(safe.UnsafePath): boundary.publish_dir(stage, 'observations')
    assert not (root/'observations').exists() and target.read_text() == 'private'


def test_directory_replaced_between_stat_and_open_never_reads_private_tree(tmp_path, monkeypatch):
    safe = module(); root = source_tree(tmp_path)
    private = tmp_path/'private'; private.mkdir(); (private/'secret').write_text('private')
    nested = root/'workspace/source/nested'; nested.mkdir(); (nested/'public').write_text('public')
    original_open = os.open
    def racing_open(path, flags, *args, **kwargs):
        if path == 'nested' and kwargs.get('dir_fd') is not None:
            nested.rename(root/'workspace/source/original-nested')
            nested.symlink_to(private, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)
    with safe.SafeRoot(root) as boundary:
        monkeypatch.setattr(safe.os, 'open', racing_open)
        with pytest.raises(safe.UnsafePath): boundary.clone_source('workspace/source', tmp_path/'copy')
    assert not (tmp_path/'copy').exists() and (private/'secret').read_text() == 'private'


def test_file_modified_during_copy_cannot_become_complete_candidate(tmp_path, monkeypatch):
    safe = module(); root = source_tree(tmp_path)
    target = root/'workspace/source/main.go'
    inode = target.stat().st_ino
    original_read = os.read
    changed = False
    def racing_read(fd, size):
        nonlocal changed
        result = original_read(fd, size)
        if not changed and os.fstat(fd).st_ino == inode:
            changed = True
            target.write_text('changed in flight\n')
        return result
    with safe.SafeRoot(root) as boundary:
        monkeypatch.setattr(safe.os, 'read', racing_read)
        with pytest.raises(safe.UnsafePath): boundary.clone_source('workspace/source', tmp_path/'copy')
    assert changed and not (tmp_path/'copy').exists()
