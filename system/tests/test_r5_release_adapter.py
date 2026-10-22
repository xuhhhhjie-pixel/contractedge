import json
import hashlib
import tempfile
import tarfile
import io
import unittest
from pathlib import Path

import yaml

from contractedge.dataset.export import prepare_release
from contractedge.dataset.releases import load_private_gt_paths, load_release_members


class R5ReleaseAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "release"
        public = self.root / "public"
        private = self.root / "private"
        (public / "inputs/RW-R5-1").mkdir(parents=True)
        (private / "cases/RW-R5-1").mkdir(parents=True)
        (public / "inputs/RW-R5-1/input.yaml").write_text(
            "requirement: The process consumes updated configuration.\n"
            "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: demo\n", encoding="utf-8")
        with tarfile.open(public / "inputs/RW-R5-1/broken.tar.gz", "w:gz") as archive:
            info = tarfile.TarInfo("repo/main.go")
            data = b"package main\n"
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        gt = {"case_id": "RW-R5-1", "requirement": {"statement": "The process consumes updated configuration."},
              "coordination": {"expected_relations": [], "violated_relations": []}}
        (private / "cases/RW-R5-1/case.yaml").write_text(yaml.safe_dump(gt), encoding="utf-8")
        (public / "manifest.jsonl").write_text(json.dumps({"case_id": "RW-R5-1", "input": "inputs/RW-R5-1/input.yaml",
            "broken_source": "inputs/RW-R5-1/broken.tar.gz",
            "input_view": "broken_source_and_declared_input_only"}) + "\n", encoding="utf-8")
        private_row = {"case_id": "RW-R5-1", "private_gt": "private/cases/RW-R5-1/case.yaml", "formal_membership": True}
        (private / "manifest.jsonl").write_text(json.dumps(private_row) + "\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def _review(self, requirement_text, *, rationale="reviewed"):
        source_rel = "inputs/RW-R5-1/input.yaml"
        source_bytes = (self.root / "public" / source_rel).read_bytes()
        return {"RW-R5-1": {"decision": "APPROVED", "reviewer_id": "review-fixture",
            "rationale": rationale, "requirement_text": requirement_text,
            "public_sources": [{"kind": "PUBLIC_DECLARATION", "uri": f"public://{source_rel}",
                "sha256": hashlib.sha256(source_bytes).hexdigest(),
                "evidence_refs": ["document:requirement"]}]}}

    def test_r5_requires_explicit_source_root_and_requirement_review(self):
        members = load_release_members(self.root / "public/manifest.jsonl", "R5-v28", self.root / "public")
        private_paths = load_private_gt_paths(self.root / "private/manifest.jsonl", self.root)
        blocked = prepare_release(members, self.root / "out-1", private_gt_paths=private_paths,
                                 source_roots={}, materialize=False)
        self.assertEqual(blocked["held_count"], 1)
        self.assertIn("SOURCE_ROOT_MAPPING_MISSING", blocked["decisions"][0]["reason"])

        review = self._review("The process consumes updated configuration.", rationale="synthetic fixture review")
        report = prepare_release(members, self.root / "out-2", private_gt_paths=private_paths,
                                 source_roots={"RW-R5-1": "repo"}, r5_requirement_reviews=review,
                                 materialize=True)
        self.assertEqual(report["prepared_count"], 1)
        projected = json.loads((self.root / "out-2/public/cases/RW-R5-1/method_input.json").read_text())
        self.assertEqual(len(projected["declared_artifacts"]), 1)
        self.assertIn("kind: ConfigMap", projected["declared_artifacts"][0]["content"])
        self.assertNotIn("requirement_sources", projected)
        private_row = json.loads((self.root / "out-2/evaluator/manifest.jsonl").read_text())
        self.assertEqual(private_row["requirement_sources"][0]["uri"], "public://inputs/RW-R5-1/input.yaml")
        self.assertNotIn("captured_content", private_row["requirement_sources"][0])
        artifact_path = self.root / "out-2/public/cases/RW-R5-1/declared-artifacts/input.yaml"
        self.assertEqual(artifact_path.read_text(), projected["declared_artifacts"][0]["content"])

    def test_private_requirement_cannot_be_promoted_by_human_review(self):
        public_input = self.root / "public/inputs/RW-R5-1/input.yaml"
        public_input.write_text("apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: demo\n", encoding="utf-8")
        members = load_release_members(self.root / "public/manifest.jsonl", "R5-v28", self.root / "public")
        private_paths = load_private_gt_paths(self.root / "private/manifest.jsonl", self.root)
        review = self._review("The process consumes updated configuration.")

        report = prepare_release(members, self.root / "out-private-text", private_gt_paths=private_paths,
                                 source_roots={"RW-R5-1": "repo"}, r5_requirement_reviews=review,
                                 materialize=False)

        self.assertEqual(report["held_count"], 1)
        self.assertIn("PUBLIC_REQUIREMENT_MISSING", report["decisions"][0]["reason"])

    def test_approved_r5_requirement_without_public_source_provenance_is_held(self):
        members = load_release_members(self.root / "public/manifest.jsonl", "R5-v28", self.root / "public")
        private_paths = load_private_gt_paths(self.root / "private/manifest.jsonl", self.root)
        review = {"RW-R5-1": {"decision": "APPROVED", "reviewer_id": "review-fixture",
            "rationale": "The task is faithful to the public issue.",
            "requirement_text": "The process consumes updated configuration."}}

        report = prepare_release(members, self.root / "out-no-source-proof", private_gt_paths=private_paths,
                                 source_roots={"RW-R5-1": "repo"}, r5_requirement_reviews=review,
                                 materialize=False)

        self.assertEqual(report["prepared_count"], 0)
        self.assertEqual(report["held_count"], 1)
        self.assertIn("PUBLIC_REQUIREMENT_PROVENANCE_REQUIRED", report["decisions"][0]["reason"])

    def test_r5_declaration_source_digest_must_match_public_package(self):
        members = load_release_members(self.root / "public/manifest.jsonl", "R5-v28", self.root / "public")
        private_paths = load_private_gt_paths(self.root / "private/manifest.jsonl", self.root)
        review = self._review("The process consumes updated configuration.")
        review["RW-R5-1"]["public_sources"][0]["sha256"] = "0" * 64

        report = prepare_release(members, self.root / "out-bad-source-digest", private_gt_paths=private_paths,
                                 source_roots={"RW-R5-1": "repo"}, r5_requirement_reviews=review,
                                 materialize=False)

        self.assertEqual(report["held_count"], 1)
        self.assertIn("PUBLIC_REQUIREMENT_SOURCE_DIGEST_MISMATCH", report["decisions"][0]["reason"])

    def test_public_issue_provenance_rejects_pull_request_urls(self):
        members = load_release_members(self.root / "public/manifest.jsonl", "R5-v28", self.root / "public")
        private_paths = load_private_gt_paths(self.root / "private/manifest.jsonl", self.root)
        review = self._review("The process consumes updated configuration.")
        issue_body = "Users require the process to consume the declared configuration."
        review["RW-R5-1"]["public_sources"] = [{"kind": "PUBLIC_ISSUE",
            "uri": "https://github.com/example/project/pull/1", "captured_content": issue_body,
            "sha256": hashlib.sha256(issue_body.encode()).hexdigest(), "evidence_refs": ["body:expected"]}]

        report = prepare_release(members, self.root / "out-pr-as-issue", private_gt_paths=private_paths,
                                 source_roots={"RW-R5-1": "repo"}, r5_requirement_reviews=review,
                                 materialize=False)

        self.assertEqual(report["held_count"], 1)
        self.assertIn("PUBLIC_REQUIREMENT_PROVENANCE_INVALID", report["decisions"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
