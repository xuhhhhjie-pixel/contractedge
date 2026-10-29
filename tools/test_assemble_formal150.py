"""Packaging failure guards, independent of model and frozen method fixtures."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('assembly', Path(__file__).with_name('assemble_formal150.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class AssemblyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.packages = self.root / 'packages'
        self.meta = self.root / 'metadata'
        (self.root / 'benchmark').mkdir()
        self.meta.mkdir()
        self.index = {'classes': {}}
        self.order = []
        allrows = []
        for cl in m.CLASSES:
            p = self.packages / cl; p.mkdir(parents=True)
            rows = [{'case_id': cl + '-' + str(i)} for i in range(30)]
            allrows += rows
            (p / 'public').mkdir()
            (p / 'public/manifest.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
            (p / 'RELEASE_ENTRY.json').write_text('{}')
            (p / ('payload-' + cl)).write_text(cl)
            sums = ''.join(m.sha(p/n) + '  ' + n + '\n' for n in
                           ['RELEASE_ENTRY.json', 'public/manifest.jsonl', 'payload-' + cl])
            (p / 'SHA256SUMS').write_text(sums)
            self.index['classes'][cl] = dict(archive_root=cl,
                package_entry_sha256=m.sha(p/'RELEASE_ENTRY.json'),
                payload_checksums_sha256=m.sha(p/'SHA256SUMS'))
        raw = ''.join(json.dumps(r) + '\n' for r in allrows).encode()
        self.plan = dict(manifest_case_order={'public/manifest.jsonl': [r['case_id'] for r in allrows]},
                        manifest_sha256={'public/manifest.jsonl': m.hashlib.sha256(raw).hexdigest()},
                        metadata_sha256={})
        self.save()
        self.patches = [patch.object(m, 'ROOT', self.root), patch.object(m, 'META', self.meta)]
        for p in self.patches:
            p.start(); self.addCleanup(p.stop)

    def save(self):
        (self.root/'benchmark/RELEASE_INDEX.json').write_text(json.dumps(self.index))
        (self.meta/'layout.json').write_text(json.dumps(self.plan))

    def test_exact_mapping(self):
        payload, generated, pins, _ = m.prepare(self.packages)
        self.assertEqual(len(payload), 5)
        self.assertEqual(len(pins), 5)
        self.assertEqual(len(generated['public/manifest.jsonl'].splitlines()), 150)

    def test_modified_asset_rejected(self):
        (self.packages/'R1/payload-R1').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'Payload checksum mismatch'):
            m.prepare(self.packages)

    def test_missing_and_changed_checksum_list_rejected(self):
        (self.packages/'R1/SHA256SUMS').write_text('')
        with self.assertRaisesRegex(ValueError, 'checksum-list mismatch'):
            m.prepare(self.packages)

    def test_wrong_membership_and_order_rejected(self):
        self.plan['manifest_case_order']['public/manifest.jsonl'][0] = 'unregistered'
        self.save()
        with self.assertRaisesRegex(ValueError, 'exact150 membership'):
            m.prepare(self.packages)

    def test_path_escape_and_symlink_rejected(self):
        (self.root/'target').write_text('private')
        (self.root/'link').symlink_to(self.root/'target')
        for name in ['../target', '/target', 'x/../target', 'link', 'x\\target']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                m.confined(self.root, name)


if __name__ == '__main__':
    unittest.main()
