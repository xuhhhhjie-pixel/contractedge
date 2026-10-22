import contextlib
import io
import json
import tempfile
import tarfile
import io as bytes_io
import unittest
from pathlib import Path

from contractedge.cli import _build_provider, main, validate_public_input
from contractedge.model.provider import OllamaNativeProvider
from contractedge.contracts.validation import canonical_digest
from contractedge.dataset.archive import inspect_archive
from tests.test_contracts import valid_method_input


class CliTests(unittest.TestCase):
    def test_native_ollama_provider_requires_tokenizer_but_not_api_key(self):
        provider, counter = _build_provider({"provider": "ollama_native", "base_url": "http://127.0.0.1:11434",
            "model": "fixture", "tokenizer": "json:loads"})
        self.assertIsInstance(provider, OllamaNativeProvider)
        self.assertIs(counter, json.loads)

    def test_validate_input_binds_method_input_to_public_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive_path = root / "broken-source.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                content = b"package demo\nfunc Render() {}\n"
                info = tarfile.TarInfo("repo/main.go")
                info.size = len(content)
                archive.addfile(info, bytes_io.BytesIO(content))
            value = valid_method_input()
            value["archive_path"] = "broken-source.tar.gz"
            value["archive_sha256"] = __import__("hashlib").sha256(archive_path.read_bytes()).hexdigest()
            value["source_root"] = "repo"
            value["initial_snapshot_id"] = inspect_archive(archive_path, source_root="repo").snapshot_id
            value.pop("input_digest", None)
            value["input_digest"] = canonical_digest(value)
            input_path = root / "method_input.json"
            input_path.write_text(json.dumps(value), encoding="utf-8")
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                code = main(["validate-input", "--method-input", str(input_path), "--public-root", str(root)])

        report = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["case_id"], value["case_id"])

    def test_run_requires_an_explicit_tokenizer_and_provider_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_value = valid_method_input()
            method_path = root / "method_input.json"
            method_path.write_text(json.dumps(input_value), encoding="utf-8")
            (root / "manifest.jsonl").write_text(json.dumps({"case_id": input_value["case_id"],
                "method_input": "method_input.json"}) + "\n", encoding="utf-8")
            config_path = root / "config.json"
            config_path.write_text(json.dumps({"provider": "openai_compatible", "base_url": "http://localhost",
                "api_key_env": "TEST_KEY", "model": "fixture", "max_context_tokens": 5000}), encoding="utf-8")
            errors = io.StringIO()
            with contextlib.redirect_stderr(errors):
                code = main(["run", "--public-root", str(root), "--case-id", input_value["case_id"],
                             "--config", str(config_path), "--output", str(root / "trace.json")])
        self.assertEqual(code, 2)
        self.assertIn("tokenizer", errors.getvalue())

    def test_validate_input_rejects_method_input_outside_public_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            public = root / "public"
            public.mkdir()
            outside = root / "private-method-input.json"
            outside.write_text(json.dumps(valid_method_input()), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "inside the public release root"):
                validate_public_input(outside, public)

    def test_evaluate_cohort_command_keeps_manifest_member_when_trace_is_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            r12_root, r5_root, trace_root = root / "r12", root / "r5", root / "traces"
            r12_root.mkdir(); r5_root.mkdir(); trace_root.mkdir()
            (r12_root / "gt.yaml").write_text("case_id: CASE-1\ncoordination:\n  units: []\n  expected_relations: []\n  violated_relations: []\n", encoding="utf-8")
            r12_manifest = root / "r12.jsonl"
            r12_manifest.write_text(json.dumps({"case_id": "CASE-1", "release_id": "R1-v1",
                "ground_truth": "gt.yaml", "ground_truth_sha256": __import__("hashlib").sha256(
                    (r12_root / "gt.yaml").read_bytes()).hexdigest()}) + "\n", encoding="utf-8")
            r5_manifest = root / "r5.jsonl"
            r5_manifest.write_text("", encoding="utf-8")
            trace_manifest = root / "traces.jsonl"
            trace_manifest.write_text("", encoding="utf-8")
            output_path = root / "cohort.json"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(["evaluate-cohort", "--r1-r2-manifest", str(r12_manifest),
                    "--r1-r2-root", str(r12_root), "--r5-manifest", str(r5_manifest),
                    "--r5-root", str(r5_root), "--trace-manifest", str(trace_manifest),
                    "--trace-root", str(trace_root), "--output", str(output_path)])

            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertEqual(payload["case_count"], 1)
        self.assertEqual(payload["attempted_count"], 0)
        self.assertEqual(payload["cases"][0]["status"], "NOT_RUN")
        self.assertEqual(json.loads(stdout.getvalue())["case_count"], 1)


if __name__ == "__main__":
    unittest.main()
