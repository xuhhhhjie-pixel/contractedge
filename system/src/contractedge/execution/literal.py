"""Shared bounded literal source transaction, independent of semantic plan format."""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import tempfile
from typing import Mapping
from contractedge.contracts.permissions import target_permission_error
from contractedge.source.snapshot import compute_snapshot

class PlanRejected(ValueError):
    pass


def safe_target(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or '\\' in relative or '\x00' in relative:
        raise PlanRejected('Invalid target path')
    rel = PurePosixPath(relative)
    if (not relative or rel.is_absolute() or rel.as_posix() != relative
            or any(p in {'.', '..', '.git', '.contractedge-deps'} for p in rel.parts)):
        raise PlanRejected('Unsafe target path')
    cursor = root
    for part in rel.parts:
        cursor /= part
        if cursor.is_symlink():
            raise PlanRejected('Target traverses a symlink')
    cursor.resolve().relative_to(root.resolve())
    return cursor


@dataclass(frozen=True)
class LiteralTarget:
    key: str
    path: str
    operation: str
    content_digest: str | None = None


def validate_literal_targets(source, policy, snapshot_id, targets, supported_operations=('MODIFY', 'CREATE')):
    root=Path(source).resolve(strict=True)
    snapshot=compute_snapshot(root)
    if snapshot.snapshot_id != snapshot_id:
        raise PlanRejected('Literal input snapshot is stale')
    keys=[t.key for t in targets]
    if not targets or len(keys)!=len(set(keys)) or any(not isinstance(k,str) or not k for k in keys):
        raise PlanRejected('Literal targets must have nonempty unique IDs')
    for target in targets:
        path=safe_target(root,target.path)
        error=target_permission_error(target.path,policy)
        if error:
            raise PlanRejected('Target outside editable scope or forbidden: '+target.path)
        if target.operation not in supported_operations:
            raise PlanRejected('Execution consumer rejects unsupported operation: '+target.operation)
        if target.operation=='MODIFY':
            if not path.is_file() or snapshot.file_digests.get(target.path)!=target.content_digest:
                raise PlanRejected('Existing literal target identity mismatch: '+target.path)
        elif target.operation=='CREATE':
            if path.exists() or not path.parent.is_dir():
                raise PlanRejected('New target must have an existing parent and no path conflict')
        else:
            raise PlanRejected('Literal transaction does not support operation: '+target.operation)
    return snapshot


def apply_literal_edits(source, policy, response, *, snapshot_id, targets, operation_order=None,
                        prior_applied_operations=None, require_behavior_change=False,
                        supported_operations=('MODIFY','CREATE'), preflight=None, trial_session=None):
    root=Path(source).resolve(strict=True)
    validate_literal_targets(root,policy,snapshot_id,targets,supported_operations)
    by_id={target.key:target for target in targets}
    acceptance={'snapshot_id':snapshot_id,'operation_order':list(operation_order or by_id)}
    if not isinstance(response, Mapping):
        raise PlanRejected('Concrete response must be an object')
    edits = response.get('edits')
    if not isinstance(edits, list) or not edits or set(response) != {'edits'}:
        raise PlanRejected('Concrete response must contain a nonempty edits array only')
    if any(not isinstance(edit, Mapping) or set(edit) != {'change_id', 'path', 'operation', 'old_text', 'new_text'} for edit in edits):
        raise PlanRejected('Unsupported concrete edit fields')
    if any(not isinstance(edit['change_id'], str) for edit in edits):
        raise PlanRejected('Concrete change_id must be a string')
    submitted={edit['change_id'] for edit in edits}
    eligible=prior_applied_operations or {}
    eligible=eligible if isinstance(eligible,Mapping) else {}
    missing=set(by_id)-submitted
    retained={cid for cid in missing if cid in eligible and
              eligible[cid].get('path')==by_id[cid].path and
              eligible[cid].get('snapshot_id')==acceptance['snapshot_id'] and
              eligible[cid].get('content_digest')==by_id[cid].content_digest and
              eligible[cid].get('content_digest')==hashlib.sha256(safe_target(root,by_id[cid].path).read_bytes()).hexdigest()}
    if submitted-set(by_id) or missing-retained:
        raise PlanRejected('Concrete edits must cover exactly the accepted change IDs; '
            f'missing={sorted(set(by_id)-submitted)}, unexpected={sorted(submitted-set(by_id))}. '
            'No source was changed. Cover every accepted operation with its own change_id; '
            'do not silently omit an operation or relabel another operation as completed.')
    original, changed, per_change = {}, {}, Counter()
    for edit_index, edit in enumerate(sorted(edits, key=lambda e: acceptance['operation_order'].index(e['change_id']))):
        if set(edit) != {'change_id', 'path', 'operation', 'old_text', 'new_text'}:
            raise PlanRejected('Unsupported edit fields')
        op = by_id[edit['change_id']]
        relative = op.path
        if edit['path'] != relative or edit['operation'] != op.operation:
            raise PlanRejected('Edit path/operation differs from accepted semantic target')
        path = safe_target(root, relative)
        if not isinstance(edit['old_text'], str) or not isinstance(edit['new_text'], str):
            raise PlanRejected('Edits must contain literal UTF-8 text')
        if '\x00' in edit['new_text'] or len(edit['new_text'].encode()) > 4 * 1024 * 1024:
            raise PlanRejected('Concrete output is binary or exceeds patch budget')
        if relative not in original:
            original[relative] = path.read_bytes() if path.exists() else None
            changed[relative] = original[relative].decode('utf-8') if original[relative] is not None else ''
        if op.operation == 'CREATE':
            if per_change[op.key] or edit['old_text'] or not edit['new_text'] or original[relative] is not None:
                raise PlanRejected('CREATE must specify one complete new regular file')
            changed[relative] = edit['new_text']
        else:
            matches = changed[relative].count(edit['old_text']) if edit['old_text'] else 0
            if not edit['old_text'] or matches != 1:
                raise PlanRejected('MODIFY old_text must occur exactly once in the current file; '
                    f'change_id={edit["change_id"]}, path={relative}, edit_index={edit_index}, matches={matches}. '
                    'Copy a unique literal anchor from current_target_files; no source was changed.')
            changed[relative] = changed[relative].replace(edit['old_text'], edit['new_text'], 1)
        per_change[op.key] += 1
    if sum(len(value.encode()) for value in changed.values()) > 8 * 1024 * 1024:
        raise PlanRejected('Edited file batch exceeds the consumer byte limit')
    if all(original[path] == text.encode() for path, text in changed.items()):
        raise PlanRejected('Concrete edits are a no-op')
    if require_behavior_change:
        from contractedge.execution.code_change import semantics_unchanged
        if semantics_unchanged(original,changed):
            raise PlanRejected('The current supported behavior failure requires a semantic change; '
                'this retry changes only a matching Python AST or blank Go source lines. No source was changed. '
                'Address the actual failing operation/path from fresh feedback, preserving already passing conditions. '
                'Do not repeat a cosmetic edit or claim the accepted intent was completed.')
    # Recheck all source bytes after model generation and before transaction.
    validate_literal_targets(root, policy, snapshot_id, tuple(by_id.values()), supported_operations)
    if preflight is not None:
        preflight()
    if trial_session is not None:
        trial_session.record_edit_intent(changed)
    # Transaction scratch is owned by the trial, outside the hashed source.
    # A hard exit can retain it without inventing an external source change.
    staging_parent = Path(getattr(trial_session, 'directory', root.parent)).resolve(strict=True)
    if staging_parent.is_relative_to(root):
        raise PlanRejected('Edit staging must remain outside the source snapshot')
    with tempfile.TemporaryDirectory(prefix='.ce-transaction-', dir=staging_parent) as transaction:
        staging_root = Path(transaction)
        if staging_root.stat().st_dev != root.stat().st_dev:
            raise PlanRejected('Edit staging must share the source filesystem')
        written, staged, backups = [], {}, {}
        try:
            # Stage all bytes before replacing any source file. Existing inode links
            # allow rollback even if writing another file exhausts disk space.
            for relative, text in changed.items():
                path = safe_target(root, relative)
                descriptor, name = tempfile.mkstemp(prefix='.ce-edit-', dir=staging_root)
                staged[relative] = Path(name)
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(text.encode('utf-8'))
                staged[relative].chmod(path.stat().st_mode & 0o777 if path.exists() else 0o644)
                if path.exists():
                    descriptor, name = tempfile.mkstemp(prefix='.ce-before-', dir=staging_root)
                    os.close(descriptor)
                    Path(name).unlink()
                    os.link(path, name)
                    backups[relative] = Path(name)
            for relative in changed:
                path = safe_target(root, relative)
                os.replace(staged[relative], path)
                written.append(relative)
        except Exception:
            for relative in written:
                path = safe_target(root, relative)
                if original[relative] is None:
                    path.unlink()
                else:
                    os.replace(backups[relative], path)
            raise
        finally:
            for path in [*staged.values(), *backups.values()]:
                path.unlink(missing_ok=True)
    current = compute_snapshot(root)
    return {'snapshot':current,'changed':changed,'per_change':per_change,'retained':retained}
