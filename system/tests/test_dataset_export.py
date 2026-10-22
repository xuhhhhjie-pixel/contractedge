import json
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

import yaml

from contractedge.dataset.releases import ReleaseError, load_release_members, project_method_input


class DatasetProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pkg = self.root / "release"
        (self.pkg / "inputs").mkdir(parents=True)
        with tarfile.open(self.pkg / "inputs/broken.tar.gz", "w:gz") as archive:
            info = tarfile.TarInfo("repo/source.txt")
            data = b"synthetic source"
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        self.public = {
            "case_id": "RW-DEMO-1",
            "requirement": {"statement": "Keep source text", "provenance": [{"kind": "pr", "source": "private-url"}]},
            "artifact_scope": ["src/main.go"],
            "behavioral_observable": "output contains expected value",
            "broken": {"revision": "abc123", "source_archive": "inputs/broken.tar.gz"},
            "evidence": [{"kind": "public", "path": "observation.json"}],
            "private_gt": {"violations": ["hidden-answer"]},
        }
        (self.pkg / "inputs/demo.yaml").write_text(yaml.safe_dump(self.public), encoding="utf-8")
        member = {"case_id": "RW-DEMO-1", "input": "inputs/demo.yaml", "broken_source": "inputs/broken.tar.gz"}
        (self.pkg / "manifest.jsonl").write_text(json.dumps(member) + "\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_manifest_is_the_only_membership_source(self):
        (self.pkg / "inputs/stray.yaml").write_text("case_id: stray\n", encoding="utf-8")
        members = load_release_members(self.pkg / "manifest.jsonl", "R5-v1")
        self.assertEqual([m.case_id for m in members], ["RW-DEMO-1"])

    def test_projection_uses_allowlist_and_keeps_requirement_text(self):
        member = load_release_members(self.pkg / "manifest.jsonl", "R5-v1")[0]
        result = project_method_input(member, self.pkg)
        self.assertEqual(result["requirement_text"], "Keep source text")
        encoded = json.dumps(result)
        self.assertNotIn("private-url", encoded)
        self.assertNotIn("hidden-answer", encoded)
        self.assertEqual(result["public_evidence"][0]["kind"], "PUBLIC_OBSERVATION")
        self.assertEqual(result["archive_path"], "inputs/broken.tar.gz")

    def test_requirement_variants_are_normalized_without_fabricating_fields(self):
        member = load_release_members(self.pkg / "manifest.jsonl", "R5-v1")[0]
        value = yaml.safe_load((self.pkg / "inputs/demo.yaml").read_text())
        value["requirement"] = "literal requirement"
        value.pop("behavioral_observable")
        (self.pkg / "inputs/demo.yaml").write_text(yaml.safe_dump(value), encoding="utf-8")
        result = project_method_input(member, self.pkg)
        self.assertEqual(result["requirement_text"], "literal requirement")
        self.assertIsNone(result["public_observable_text"])

    def test_requirement_projection_preserves_exact_whitespace(self):
        member = load_release_members(self.pkg / "manifest.jsonl", "R1-test")[0]
        value = yaml.safe_load((self.pkg / "inputs/demo.yaml").read_text())
        value["requirement"] = {"text": "  Keep source text as written.\n"}
        (self.pkg / "inputs/demo.yaml").write_text(yaml.safe_dump(value), encoding="utf-8")

        projected = project_method_input(member, self.pkg)

        self.assertEqual(projected["requirement_text"], "  Keep source text as written.\n")

    def test_conflicting_requirement_fields_are_rejected(self):
        member = load_release_members(self.pkg / "manifest.jsonl", "R5-v1")[0]
        value = yaml.safe_load((self.pkg / "inputs/demo.yaml").read_text())
        value["requirement"]["text"] = "first requirement"
        value["requirement"]["statement"] = "different requirement"
        (self.pkg / "inputs/demo.yaml").write_text(yaml.safe_dump(value), encoding="utf-8")
        with self.assertRaisesRegex(Exception, "REQUIREMENT_CONFLICT"):
            project_method_input(member, self.pkg)

    def test_source_span_excerpt_without_source_digest_is_not_relabelled_as_observation(self):
        member = load_release_members(self.pkg / "manifest.jsonl", "R5-v1")[0]
        value = yaml.safe_load((self.pkg / "inputs/demo.yaml").read_text())
        value["evidence"] = [{"kind": "source_span", "path": "src/main.go",
                              "excerpt": "func main() { deploy() }"}]
        (self.pkg / "inputs/demo.yaml").write_text(yaml.safe_dump(value), encoding="utf-8")

        with self.assertRaisesRegex(ReleaseError, "PUBLIC_SOURCE_EVIDENCE_UNVERIFIED"):
            project_method_input(member, self.pkg)


if __name__ == "__main__":
    unittest.main()
