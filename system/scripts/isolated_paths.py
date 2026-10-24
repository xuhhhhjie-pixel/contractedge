"""FD-pinned regular-file copies and non-overwriting publication on Linux.

Sources and destinations are independent bytes, never writable hardlinks.
Caller-supplied destinations of clone_source are trusted private staging paths;
all method-controlled paths must go through SafeRoot's directory descriptors.
"""
from contextlib import contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import uuid


class UnsafePath(ValueError):
    pass


DEFAULT_LIMITS = {'max_files': 100000, 'max_bytes': 2 * 1024**3,
                  'max_file_bytes': 64 * 1024**2}


def _parts(relative):
    value = os.fspath(relative)
    if not isinstance(value, str) or '\\' in value or '\x00' in value:
        raise UnsafePath('invalid relative path')
    result = value.split('/')
    if any(item in {'', '.', '..'} for item in result):
        raise UnsafePath('invalid relative path')
    return result


def _signature(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _rename_new(src_fd, source, dst_fd, destination):
    # renameat2 NOREPLACE is atomic, unlike exists()+rename(). Linux is an
    # explicit deployment constraint; there is no unsafe portable fallback.
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is None:
        raise UnsafePath('atomic non-overwriting rename unavailable')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(src_fd, os.fsencode(source), dst_fd, os.fsencode(destination), 1):
        raise UnsafePath('publication refused')
    os.fsync(dst_fd)


def _discard(parent, name):
    """FD-relative cleanup, including Python 3.10 without rmtree(dir_fd=)."""
    try:
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISDIR(info.st_mode):
        current = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            for item in os.listdir(current):
                _discard(current, item)
        finally:
            os.close(current)
        os.rmdir(name, dir_fd=parent)
    else:
        os.unlink(name, dir_fd=parent)


def _copy_tree(source_fd, destination_fd, state, prefix='', *, skip_git=True):
    before = os.fstat(source_fd)
    names = sorted(os.listdir(source_fd))
    for name in names:
        if skip_git and name in {'.git', '.contractedge-deps'}:
            continue
        relative = prefix + name
        info = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=source_fd)
            try:
                if _signature(info) != _signature(os.fstat(child)):
                    raise UnsafePath('source changed during snapshot')
                os.mkdir(name, mode=0o755, dir_fd=destination_fd)
                target = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=destination_fd)
                try:
                    _copy_tree(child, target, state, relative + '/', skip_git=skip_git)
                finally:
                    os.close(target)
            finally:
                os.close(child)
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            if (len(state['files']) >= state['limits']['max_files']
                    or info.st_size > state['limits']['max_file_bytes']
                    or state['bytes'] + info.st_size > state['limits']['max_bytes']):
                raise UnsafePath('snapshot resource limit exceeded')
            current = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=source_fd)
            try:
                opened = os.fstat(current)
                if _signature(opened) != _signature(info) or opened.st_nlink != 1:
                    raise UnsafePath('source changed during snapshot')
                target = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=destination_fd)
                digest, size = hashlib.sha256(), 0
                with os.fdopen(target, 'wb') as output:
                    while True:
                        data = os.read(current, 1024 * 1024)
                        if not data:
                            break
                        size += len(data)
                        if (size > info.st_size or size > state['limits']['max_file_bytes']
                                or state['bytes'] + size > state['limits']['max_bytes']):
                            raise UnsafePath('source changed or exceeded limit')
                        digest.update(data)
                        output.write(data)
                    if size != info.st_size or _signature(os.fstat(current)) != _signature(info):
                        raise UnsafePath('source changed during snapshot')
                    final = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
                    if _signature(final) != _signature(info) or final.st_nlink != 1:
                        raise UnsafePath('source changed during snapshot')
                    output.flush()
                    os.fchmod(output.fileno(), 0o755 if info.st_mode & 0o111 else 0o644)
                    os.fsync(output.fileno())
                state['files'][relative] = digest.hexdigest()
                state['bytes'] += size
            finally:
                os.close(current)
        else:
            raise UnsafePath('links and special files are not accepted')
    if _signature(os.fstat(source_fd)) != _signature(before) or sorted(os.listdir(source_fd)) != names:
        raise UnsafePath('source directory changed during snapshot')
    os.fsync(destination_fd)


class SafeRoot:
    def __init__(self, root):
        try:
            self.fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError:
            raise UnsafePath('unavailable case root') from None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        os.close(self.fd)

    @contextmanager
    def _parent(self, relative, *, create=False):
        parts = _parts(relative)
        current = os.dup(self.fd)
        try:
            for item in parts[:-1]:
                if create:
                    try:
                        os.mkdir(item, mode=0o700, dir_fd=current)
                    except FileExistsError:
                        pass
                child = os.open(item, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
                os.close(current)
                current = child
            yield current, parts[-1]
        except OSError:
            raise UnsafePath('path unavailable or unsafe') from None
        finally:
            os.close(current)

    def clone_source(self, relative, destination, limits=None):
        """Copy child source into a new trusted staging directory, excluding Git."""
        bounds = dict(DEFAULT_LIMITS)
        if limits is not None:
            if set(limits) - set(bounds) or any(type(v) is not int or v < 0 for v in limits.values()):
                raise UnsafePath('invalid snapshot limits')
            bounds.update(limits)
        destination = Path(destination)
        staging = destination.with_name('.snapshot-' + uuid.uuid4().hex)
        state = {'files': {}, 'bytes': 0, 'limits': bounds}
        try:
            with self._parent(relative) as (parent, name):
                source = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    staging.mkdir(mode=0o700)
                    target = os.open(staging, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try:
                        _copy_tree(source, target, state)
                    finally:
                        os.close(target)
                finally:
                    os.close(source)
            parent = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                _rename_new(parent, staging.name, parent, destination.name)
            finally:
                os.close(parent)
        except OSError:
            raise UnsafePath('snapshot refused') from None
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        canonical = json.dumps(state['files'], ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        return {'files': state['files'], 'bytes': state['bytes'], 'snapshot_id': hashlib.sha256(canonical).hexdigest()}

    def publish_bytes(self, data, relative):
        if not isinstance(data, bytes):
            raise UnsafePath('publication requires bytes')
        with self._parent(relative, create=True) as (parent, name):
            temporary = '.publish-' + uuid.uuid4().hex
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                _rename_new(parent, temporary, parent, name)
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass

    def publish_dir(self, stage, relative):
        """Publish independent public outputs; never follow producer links."""
        with self._parent(relative, create=True) as (parent, name):
            temporary = '.publish-' + uuid.uuid4().hex
            source = os.open(stage, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.mkdir(temporary, mode=0o700, dir_fd=parent)
                target = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    _copy_tree(source, target, {'files': {}, 'bytes': 0, 'limits': DEFAULT_LIMITS}, skip_git=False)
                finally:
                    os.close(target)
                _rename_new(parent, temporary, parent, name)
            except OSError:
                raise UnsafePath('directory publication refused') from None
            finally:
                os.close(source)
                _discard(parent, temporary)
