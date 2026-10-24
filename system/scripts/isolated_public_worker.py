"""Trusted public producer. No model key, evaluator or arbitrary shell RPC.

The runner and its S0 probe cache live for the entire case. Candidate source
bytes replace only the source tree; inputs, workspace metadata and Git baseline
are never accepted from the method process.
"""
import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import uuid

from isolated_paths import SafeRoot, UnsafePath, trusted_source_links, trusted_publication_limits
from isolated_protocol import JsonRpcServer, Journal
from run_isolated_method import write_json


class PublicSession:
    def __init__(self, root, transfer_root, case_id, runner, *, max_timeout):
        self.root, self.transfer = Path(root), Path(transfer_root)
        self.workspace = self.root/'workspace'
        self.case_id, self.runner, self.max_timeout = case_id, runner, max_timeout
        self.record = None
        self.inputs_snapshot = None
        self.source_links = {}

    def _timeout(self, payload):
        value = payload.get('timeout_seconds')
        if type(value) not in {int, float} or not math.isfinite(value) or not 0 < value <= self.max_timeout:
            raise ValueError('Invalid public timeout')
        self.runner.timeout_seconds = value

    def prepare(self, payload):
        from contractedge.source.snapshot import compute_snapshot
        if (set(payload) != {'case_id', 'timeout_seconds'} or payload['case_id'] != self.case_id
                or self.record is not None):
            raise ValueError('Only the registered initial workspace may be prepared')
        self._timeout(payload)
        record = self.runner.prepare(self.case_id, self.workspace, self.root/'prepare')
        if record.get('private_materials_loaded') is not False or record.get('case_id') != self.case_id:
            raise ValueError('Public producer identity mismatch')
        for key in ['source_root', 'inputs_root']:
            Path(record[key]).resolve(strict=True).relative_to(self.workspace.resolve())
        self.inputs_snapshot = compute_snapshot(record['inputs_root']).snapshot_id
        self.record = record
        self.source_links = trusted_source_links(record['source_root'])
        return {'record': record, 'workspace': str(self.workspace), 'prepare_log': str(self.root/'prepare'),
                'source_links': self.source_links, 'publication_limits': trusted_publication_limits(self.workspace),
                'snapshot_id': compute_snapshot(record['source_root']).snapshot_id}

    def _verify_inputs(self):
        from contractedge.source.snapshot import compute_snapshot
        if compute_snapshot(self.record['inputs_root']).snapshot_id != self.inputs_snapshot:
            raise ValueError('Trusted public inputs changed during execution')

    def _sync(self, payload):
        from contractedge.source.snapshot import compute_snapshot
        if self.record is None or set(payload) != {'stage_id', 'snapshot_id', 'timeout_seconds'}:
            raise ValueError('Unregistered public source request')
        self._timeout(payload)
        stage = payload['stage_id']
        if not isinstance(stage, str) or not re.fullmatch('[a-f0-9]{32}', stage):
            raise ValueError('Invalid registered source stage')
        self._verify_inputs()
        source = Path(self.record['source_root'])
        next_source = source.with_name('.next-' + uuid.uuid4().hex)
        old_source = source.with_name('.previous-' + uuid.uuid4().hex)
        try:
            with SafeRoot(self.transfer) as boundary:
                manifest = boundary.clone_source(stage, next_source, allowed_symlinks=self.source_links)
            if manifest['snapshot_id'] != payload['snapshot_id']:
                raise UnsafePath('Candidate snapshot mismatch')
            previous = compute_snapshot(source).snapshot_id
            transition = dict(status='PENDING', previous_snapshot=previous,
                              requested_snapshot=payload['snapshot_id'], source=str(source),
                              next_source=str(next_source), old_source=str(old_source),
                              inputs_snapshot=self.inputs_snapshot, source_links=self.source_links,
                              case_id=self.case_id, baseline_commit=self.record['baseline_commit'])
            write_json(self.root/'source-switch.json', transition)
            try:
                os.rename(source, old_source)
                # Only the producer's Git is carried forward. Child .git was
                # excluded by clone_source; commit/index mutations cannot cheat
                # the public collect_patch baseline.
                os.rename(old_source/'.git', next_source/'.git')
                os.rename(next_source, source)
            except BaseException:
                if not source.exists() and old_source.exists():
                    if not (old_source/'.git').exists() and (next_source/'.git').exists():
                        os.rename(next_source/'.git', old_source/'.git')
                    os.rename(old_source, source)
                raise
            if compute_snapshot(source).snapshot_id != payload['snapshot_id']:
                raise UnsafePath('Published source snapshot mismatch')
            write_json(self.root/'source-switch.json', dict(transition, status='DONE'))
            shutil.rmtree(old_source)
        finally:
            if next_source.exists():
                shutil.rmtree(next_source)

    def observe(self, payload):
        self._sync(payload)
        output = self.root/('observation-' + uuid.uuid4().hex)
        report = self.runner.observe(self.workspace, output)
        self._verify_inputs()
        return {'report': report, 'output': str(output)}

    def export(self, payload):
        self._sync(payload)
        name = uuid.uuid4().hex
        patch, log = self.root/(name + '.patch'), self.root/(name + '-export')
        result = self.runner.export(self.workspace, patch, log)
        self._verify_inputs()
        return {'result': result, 'patch': str(patch), 'log': str(log)}


def recover_pending_source(root):
    """Operator-only rollback. Never resumes a method, model call or scoring.

The original request journal remains PENDING/unknown. This restores a verified
source checkpoint for inspection; it does not declare the interrupted trial a
complete patch or allow a new request ID to evade the durable call journal.
"""
    from contractedge.source.snapshot import compute_snapshot
    root = Path(root).resolve(strict=True)
    with SafeRoot(root) as boundary:
        transition = json.loads(boundary.read_bytes('source-switch.json', max_bytes=1024*1024))
        if transition.get('status') != 'PENDING':
            return {'status': 'NO_PENDING_SOURCE_SWITCH', 'automatic_method_resume': False}
        record = json.loads(boundary.read_bytes('workspace/workspace.json', max_bytes=1024*1024))
    workspace = root/'workspace'
    source, old_source, next_source = (Path(transition[k]) for k in ['source', 'old_source', 'next_source'])
    if (str(source) != record['source_root'] or not source.is_relative_to(workspace) or source == workspace
            or '..' in source.parts or transition.get('case_id') != record.get('case_id')
            or transition.get('baseline_commit') != record.get('baseline_commit')
            or not re.fullmatch('[a-f0-9]{40,64}', record.get('baseline_commit', ''))):
        raise ValueError('Checkpoint source/baseline identity mismatch')
    for path, prefix in [(old_source, '.previous-'), (next_source, '.next-')]:
        if path.parent != source.parent or not re.fullmatch(re.escape(prefix)+'[a-f0-9]{32}', path.name):
            raise ValueError('Checkpoint path escapes registered source lifecycle')
    for path in [source, old_source, next_source]:
        if path.is_symlink():
            raise ValueError('Checkpoint directory cannot be a link')
    inputs = Path(record['inputs_root'])
    if (not inputs.is_relative_to(workspace) or '..' in inputs.parts or inputs.is_symlink()
            or compute_snapshot(inputs).snapshot_id != transition['inputs_snapshot']):
        raise ValueError('Checkpoint input identity mismatch')
    checkpoint = old_source if old_source.is_dir() else source
    if (not checkpoint.is_dir() or compute_snapshot(checkpoint).snapshot_id != transition['previous_snapshot']
            or trusted_source_links(checkpoint) != transition['source_links']):
        raise ValueError('Checkpoint is incomplete or corrupt; no recovery performed')
    git_owner = next((path for path in [checkpoint, source, next_source]
                      if (path/'.git').is_dir() and not (path/'.git').is_symlink()), None)
    if git_owner is None:
        raise ValueError('Trusted baseline unavailable')
    verified = subprocess.run(['git', '-C', str(git_owner), 'rev-parse', '--verify',
                               record['baseline_commit']+'^{commit}'], capture_output=True, text=True, timeout=10)
    if verified.returncode or verified.stdout.strip() != record['baseline_commit']:
        raise ValueError('Trusted baseline failed verification')
    if checkpoint != source:
        if not (checkpoint/'.git').exists():
            os.rename(git_owner/'.git', checkpoint/'.git')
        if source.exists():
            # Keep rejected/partial trial bytes for audit; do not export them.
            quarantine = source.with_name('.interrupted-' + uuid.uuid4().hex)
            os.rename(source, quarantine)
            transition['quarantined_source'] = str(quarantine)
        os.rename(checkpoint, source)
    if compute_snapshot(source).snapshot_id != transition['previous_snapshot']:
        raise ValueError('Restored source failed snapshot verification')
    result = dict(status='ROLLED_BACK', restored_snapshot=transition['previous_snapshot'],
                  automatic_method_resume=False, candidate_exported=False, budget_reset=False)
    write_json(root/'source-switch.json', {**transition, **result})
    return result


def main():
    if '--recover-root' in sys.argv[1:]:
        parser = argparse.ArgumentParser(description='Inspect/rollback a pending trusted source switch; no model resume')
        parser.add_argument('--recover-root', required=True)
        print(json.dumps(recover_pending_source(parser.parse_args().recover_root)))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['root', 'transfer-root', 'public-root', 'tools-root', 'config', 'case']:
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    from contractedge.dataset.benchmark import method_input_from_task
    from contractedge.execution.public import BenchmarkPublicRunner
    config = json.loads(Path(args.config).read_text())
    method, _ = method_input_from_task(Path(args.public_root), args.case)
    runner = BenchmarkPublicRunner(args.public_root, args.tools_root,
        lock_root=config['lock_root'], timeout_seconds=config['public_timeout_seconds'],
        query=method['requirement_text'], method_probe_profiles=config.get('method_probe_profiles', ()))
    session = PublicSession(args.root, args.transfer_root, args.case, runner,
                            max_timeout=config['public_timeout_seconds'])
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    with JsonRpcServer(os.environ['CONTRACTEDGE_RPC_SOCKET'], os.environ['CONTRACTEDGE_RPC_TOKEN'],
                       Journal(Path(args.root)/'journal'),
                       {'public.prepare': session.prepare, 'public.observe': session.observe,
                        'public.export': session.export}):
        stop.wait()


if __name__ == '__main__':
    main()
