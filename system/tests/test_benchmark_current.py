import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from contractedge.dataset.benchmark import file_sha, method_input_from_task, prepare_benchmark, release_identity
from contractedge.contracts.validation import parse_method_input
from contractedge.source.snapshot import compute_snapshot
from contractedge.dataset.archive import safe_extract


def make_public_fixture(root: Path):
    public = root / 'public'
    task = public / 'tasks/CASE-1'
    task.mkdir(parents=True)
    with tarfile.open(task / 'source.tar.gz', 'w:gz') as archive:
        data = b'apiVersion: v1\nkind: ServiceAccount\nmetadata:\n  name: default\n'
        info = tarfile.TarInfo('repo/templates/serviceaccount.yaml')
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    (task / 'task.yaml').write_text('schema_version: contractedge.method_task.v1\nrequirement_file: requirement.md\nsource_archive: source.tar.gz\nexecution_config: environment.json\nrepair_pr: PRIVATE_METADATA_NOT_A_METHOD_HINT\n')
    (task / 'requirement.md').write_text('When enabled, render the selected ServiceAccount.')
    (task / 'environment.json').write_text(json.dumps({'editable_paths': ['templates/serviceaccount.yaml'],
        'scenarios': [{'name': 'trigger', 'argv': ['template', '/work']}]}))
    names = ['task.yaml', 'source.tar.gz', 'requirement.md', 'environment.json']
    manifest = {'case_id': 'CASE-1', 'source_sha256': file_sha(task / 'source.tar.gz'),
                'files': names, 'file_sha256': {name: file_sha(task / name) for name in names}}
    (task / 'manifest.json').write_text(json.dumps(manifest))
    row = {'case_id': 'CASE-1', 'primary_class': 'R1', 'task_path': 'tasks/CASE-1',
           'source_sha256': manifest['source_sha256'], 'task_manifest_sha256': file_sha(task / 'manifest.json')}
    (public / 'manifest.jsonl').write_text(json.dumps(row) + '\n')
    (root / 'RELEASE_ENTRY.json').write_text(json.dumps({'status': 'FROZEN_LOCAL_SUPPORTED_PLATFORM_RELEASE',
        'version': 'test-frozen-benchmark', 'selected_cases': 1, 'by_class': {'R1': 1},
        'public_manifest_sha256': file_sha(public / 'manifest.jsonl')}))
    return public, task


class CurrentBenchmarkTests(unittest.TestCase):
    def test_current_task_projects_file_requirement_and_scope_without_private_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            public, task = make_public_fixture(root)
            value, audit = method_input_from_task(public, 'CASE-1', release_id='test-frozen-benchmark')
            parsed = parse_method_input(value)
            self.assertEqual(parsed.source_root, 'repo')
            self.assertEqual(parsed.original_broken_revision, 'archive-sha256:' + file_sha(task / 'source.tar.gz'))
            self.assertEqual(parsed.scope_hints[0]['value'], 'templates/serviceaccount.yaml')
            self.assertNotIn('PRIVATE_METADATA', json.dumps(value))
            self.assertNotIn('primary_class', value)
            self.assertFalse(audit['private_materials_loaded'])
            snapshot = safe_extract(task / 'source.tar.gz', root / 'source')
            self.assertEqual(snapshot.snapshot_id, parsed.initial_snapshot_id)

    def test_corrupted_requirement_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            public, task = make_public_fixture(Path(tmp))
            (task / 'requirement.md').write_text('tampered')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                method_input_from_task(public, 'CASE-1')

    def test_projection_never_requires_or_copies_an_evaluator_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            benchmark = root / 'benchmark'
            make_public_fixture(benchmark)
            report = prepare_benchmark(benchmark, root / 'projected')
            self.assertEqual(report['status'], 'PASS')
            self.assertEqual(report['prepared'], 1)
            self.assertFalse((root / 'projected/evaluation').exists())
            member = json.loads((root / 'projected/public/manifest.jsonl').read_text())
            value = json.loads((root / 'projected/public' / member['method_input']).read_text())
            parse_method_input(value)
            self.assertEqual(file_sha(root / 'projected/public' / value['archive_path']), value['archive_sha256'])

    def test_frozen_membership_changes_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            public, _ = make_public_fixture(root)
            (public / 'manifest.jsonl').write_text((public / 'manifest.jsonl').read_text() + '\n')
            with self.assertRaisesRegex(ValueError, 'membership identity'):
                release_identity(root)

    def test_git_metadata_does_not_change_source_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'source.py').write_text('answer = 1\n')
            before = compute_snapshot(root)
            (root / '.git/objects').mkdir(parents=True)
            (root / '.git/objects/example').write_text('git metadata')
            self.assertEqual(compute_snapshot(root).snapshot_id, before.snapshot_id)

    def test_single_flat_source_file_is_not_misidentified_as_a_directory(self):
        from contractedge.dataset.archive import inspect_archive
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'flat.tar.gz'
            with tarfile.open(path, 'w:gz') as archive:
                data = b'answer = 1\n'
                info = tarfile.TarInfo('source.py'); info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
            self.assertEqual(inspect_archive(path).source_root, '.')


if __name__ == '__main__':
    unittest.main()
