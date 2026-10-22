import json
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

import yaml

from contractedge.dataset.releases import load_release_members
from contractedge.dataset.archive import safe_extract
from contractedge.dataset.audit import audit_prepared_release
from contractedge.dataset.export import PreparationBlocked, prepare_release


class DatasetMaterializationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "source"
        self.root.mkdir()
        (self.root / "inputs").mkdir()
        with tarfile.open(self.root / "inputs/broken.tar.gz", "w:gz") as archive:
            info = tarfile.TarInfo("repo/README.md")
            data = b"broken source fixture"
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        value = {"case_id": "RW-DATA-1", "requirement": {"text": "render selected value", "provenance": [{"source": "private"}]},
                 "broken": {"source_archive": "inputs/broken.tar.gz", "revision": "abc123"},
                 "artifact_scope": ["templates/deploy.yaml"], "observable": {"statement": "rendered value is absent"},
                 "evidence": [{"kind": "public_observation", "path": "evidence/raw.json", "excerpt": "public excerpt"}]}
        (self.root / "inputs/method.yaml").write_text(yaml.safe_dump(value), encoding="utf-8")
        row = {"candidate_id": "RW-DATA-1", "input_path": "inputs/method.yaml", "gt_path": "private/gt.yaml"}
        (self.root / "manifest.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
        (self.root / "private").mkdir()
        (self.root / "private/gt.yaml").write_text("case_id: RW-DATA-1\nviolated_relations: []\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_materializes_separate_public_and_private_views_from_manifest(self):
        member = load_release_members(self.root / "manifest.jsonl", "R1-test")[0]
        output = Path(self.tmp.name) / "prepared"
        report = prepare_release([member], output, materialize=True, available_bytes=10_000_000)
        public = output / "public/cases/RW-DATA-1"
        private = output / "evaluator/cases/RW-DATA-1"
        self.assertEqual(report["prepared_count"], 1)
        self.assertTrue((public / "broken-source.tar.gz").is_file())
        self.assertTrue((private / "ground_truth.yaml").is_file())
        wire = json.loads((public / "method_input.json").read_text())
        self.assertEqual(wire["source_root"], "repo")
        evidence_path = output / "public" / wire["public_evidence"][0]["locator"]["path"]
        self.assertTrue(evidence_path.is_file())
        import hashlib
        self.assertEqual(hashlib.sha256(evidence_path.read_bytes()).hexdigest(), wire["public_evidence"][0]["content_digest"])
        snapshot = safe_extract(public / "broken-source.tar.gz", Path(self.tmp.name) / "recheck")
        self.assertEqual(wire["initial_snapshot_id"], snapshot.snapshot_id)
        self.assertTrue((Path(self.tmp.name) / "recheck").exists())
        encoded_public = "".join(path.read_text(errors="ignore") for path in public.iterdir() if path.is_file() and path.suffix == ".json")
        self.assertNotIn("private", encoded_public)
        self.assertFalse((public / "ground_truth.yaml").exists())
        audit = audit_prepared_release(output, expected_members=1)
        self.assertEqual(audit["status"], "PASS", audit["errors"])

    def test_insufficient_space_refuses_partial_output(self):
        member = load_release_members(self.root / "manifest.jsonl", "R1-test")[0]
        output = Path(self.tmp.name) / "prepared"
        with self.assertRaises(PreparationBlocked):
            prepare_release([member], output, materialize=True, available_bytes=1)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
