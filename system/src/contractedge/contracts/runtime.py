"""Trusted, frozen control-plane inputs for isolated public runtime sessions."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Mapping


class RuntimeRejected(ValueError):
    """A frozen input cannot authorize this runtime operation."""


def canonical_digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True)
class FrozenInvocation:
    argv: tuple[str, ...]
    digest: str
    trusted_shell: bool = False

    @classmethod
    def freeze(cls, argv, *, trusted_shell=False):
        if not isinstance(argv, (tuple, list)) or not argv or any(
                not isinstance(value, str) or not value or '\x00' in value for value in argv):
            raise RuntimeRejected('Invocation must be a nonempty argv sequence, never a shell string')
        value = cls(tuple(argv), canonical_digest({'argv': list(argv), 'trusted_shell': trusted_shell}), trusted_shell)
        value.validate()
        return value

    def validate(self):
        if not isinstance(self.argv, tuple) or not self.argv or any(
                not isinstance(value, str) or not value or '\x00' in value for value in self.argv):
            raise RuntimeRejected('Frozen invocation argv is invalid')
        if type(self.trusted_shell) is not bool:
            raise RuntimeRejected('Shell registration must be explicit')
        if Path(self.argv[0]).name in {'sh', 'bash', 'dash', 'zsh', 'ksh'} and not self.trusted_shell:
            raise RuntimeRejected('A shell invocation requires a trusted frozen script registration')
        if self.digest != canonical_digest({'argv': list(self.argv), 'trusted_shell': self.trusted_shell}):
            raise RuntimeRejected('Frozen invocation digest mismatch')


def no_symlink_path(path):
    path = Path(path).absolute()
    if '..' in path.parts:
        raise RuntimeRejected('Runtime paths cannot contain parent traversal')
    for item in (path, *path.parents):
        if item.is_symlink():
            raise RuntimeRejected('Runtime paths cannot traverse symlinks')
    return path.resolve()


@dataclass(frozen=True)
class RuntimeSpec:
    image: str
    docker_host: str
    workspace_root: str
    session_root: str
    allowed_invocation_digests: tuple[str, ...]
    docker_binary: str = '/usr/bin/docker'
    docker_api_version: str | None = None
    python_executable: str = '/usr/bin/python3'
    uid: int = 10001
    gid: int = 10001
    cpus: float = 2.0
    memory_bytes: int = 2 * 1024 * 1024 * 1024
    pids_limit: int = 64
    tmpfs_bytes: int = 64 * 1024 * 1024
    stdout_limit: int = 64 * 1024
    stderr_limit: int = 64 * 1024
    output_limit: int = 128 * 1024
    cleanup_seconds: float = 5.0
    source_max_bytes: int = 64 * 1024 * 1024
    inputs_max_bytes: int = 16 * 1024 * 1024

    def to_wire(self):
        value = asdict(self)
        for name in ('workspace_root', 'session_root', 'docker_binary', 'python_executable'):
            value[name] = str(value[name])
        return value

    @classmethod
    def from_mapping(cls, value: Mapping):
        try:
            fields = dict(value)
            fields['allowed_invocation_digests'] = tuple(fields.get('allowed_invocation_digests', ()))
            result = cls(**fields)
            result.validate()
            return result
        except (TypeError, ValueError) as exc:
            raise RuntimeRejected('Runtime spec contains invalid or extra fields') from exc

    def validate(self):
        if not re.fullmatch(r'(?:[a-zA-Z0-9][a-zA-Z0-9./:_-]*@)?sha256:[0-9a-f]{64}', self.image):
            raise RuntimeRejected('Runtime image must be frozen to a sha256 digest')
        if (not self.docker_host.startswith('unix:///') or '\x00' in self.docker_host
                or any(part == '..' for part in Path(self.docker_host[7:]).parts)):
            raise RuntimeRejected('Only an explicit local Unix Docker socket is supported')
        if self.docker_api_version is not None and not re.fullmatch(r'1\.[0-9]+', self.docker_api_version):
            raise RuntimeRejected('Docker API version must be explicit and valid or negotiated')
        for name in ('uid', 'gid', 'memory_bytes', 'pids_limit', 'tmpfs_bytes',
                     'source_max_bytes', 'inputs_max_bytes'):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise RuntimeRejected('Positive nonroot IDs and resource limits are required: ' + name)
        if not isinstance(self.cpus, (int, float)) or isinstance(self.cpus, bool) or not math.isfinite(self.cpus) or self.cpus <= 0:
            raise RuntimeRejected('CPU limit must be finite and positive')
        if not isinstance(self.cleanup_seconds, (int, float)) or not 0 < self.cleanup_seconds <= 15:
            raise RuntimeRejected('Cleanup grace must be independently bounded to at most 15 seconds')
        for name in ('stdout_limit', 'stderr_limit', 'output_limit'):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise RuntimeRejected('Output byte limits must be nonnegative integers')
        if not self.allowed_invocation_digests or any(
                not re.fullmatch(r'[0-9a-f]{64}', value) for value in self.allowed_invocation_digests):
            raise RuntimeRejected('A trusted frozen invocation allowlist is required')
        workspace = no_symlink_path(self.workspace_root)
        forbidden = {Path('/'), Path('/root'), Path('/home'), Path('/tmp'), Path('/var'), Path.home()}
        if workspace in forbidden or not workspace.is_dir():
            raise RuntimeRejected('Workspace must be an existing task-owned directory, not a system or user root')
        session = no_symlink_path(self.session_root)
        if session == workspace or not session.is_relative_to(workspace):
            raise RuntimeRejected('Session artifacts must be a new descendant of the task workspace')
        binary = Path(self.docker_binary)
        if not binary.is_absolute() or not binary.is_file():
            raise RuntimeRejected('Docker CLI must be an explicit existing executable')
        executable = Path(self.python_executable)
        if not executable.is_absolute() or '..' in executable.parts or '\x00' in self.python_executable:
            raise RuntimeRejected('Trusted container Python path must be absolute')
