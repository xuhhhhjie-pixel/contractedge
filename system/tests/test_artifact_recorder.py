import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path
import tempfile
import unittest

from contractedge.contracts.artifacts import ArtifactRef, CallRef
from contractedge.contracts.validation import canonical_digest
from contractedge.orchestration.recorder import RunRecorder, preserve_error


INPUT = "a" * 64


def call():
    request = {"model": "synthetic", "messages": [{"role": "user", "content": "public fixture"}]}
    return {"request": request, "request_digest": canonical_digest(request),
            "prompt_version": "public.fixture", "prompt_tokens_reserved": 10,
            "output_tokens_reserved": 20,
            "transport_request": {"body_sha256": "b" * 64}}


class ArtifactRecorderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ce-recorder-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.recorder = RunRecorder(self.root, run_id="RUN", input_digest=INPUT)

    def artifact(self, name="public/input.json", text='{"value":"public"}'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return self.recorder.artifact_ref(name, "PUBLIC_INPUT", snapshot_id="S0")

    def test_artifact_reference_hashes_actual_bytes_and_is_json_safe(self):
        reference = self.artifact()
        self.assertIsInstance(reference, ArtifactRef)
        self.assertEqual(reference.sha256, hashlib.sha256((self.root / reference.relative_path).read_bytes()).hexdigest())
        self.assertEqual(json.loads(json.dumps(asdict(reference)))["input_digest"], INPUT)

    def test_absolute_parent_ambiguous_and_windows_paths_are_rejected(self):
        for name in ("", "../other", "/absolute", "a/../b", "./a", "a//b",
                     "C:/outside", "a\\b", "a/"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.recorder.artifact_ref(name, "PUBLIC_INPUT")

    def test_symlink_artifacts_and_symlink_directories_are_rejected(self):
        outside = self.root / "outside.json"
        outside.write_text("{}")
        (self.root / "link.json").symlink_to(outside)
        with self.assertRaises(ValueError):
            self.recorder.artifact_ref("link.json", "PUBLIC_INPUT")
        (self.root / "link-dir").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.recorder.artifact_ref("link-dir/outside.json", "PUBLIC_INPUT")

    def test_existing_recorder_identity_cannot_be_rebound(self):
        with self.assertRaises(ValueError):
            RunRecorder(self.root, run_id="OTHER", input_digest=INPUT)
        with self.assertRaises(ValueError):
            RunRecorder(self.root, run_id="RUN", input_digest="c" * 64)

    def test_inflight_references_keep_run_index_request_identity(self):
        calls = [call()]
        before = self.recorder.update_ledger(calls, segment_index=2, first_call_index=7)
        calls[0]["response_content"] = '{"received":true}'
        after = self.recorder.update_ledger(calls, segment_index=2, first_call_index=7)
        self.assertEqual(before, after)
        self.assertEqual(before[0].absolute_call_index, 7)
        self.assertIsNone(before[0].ledger_segment_digest)
        self.assertEqual(before[0].request_digest, calls[0]["request_digest"])
        self.assertEqual(before[0].transport_digest, "b" * 64)

    def test_identical_requests_are_distinct_actual_calls(self):
        refs = self.recorder.update_ledger([call(), call()], first_call_index=3)
        self.assertEqual([r.absolute_call_index for r in refs], [3, 4])

    def test_sealed_actual_ledger_and_later_settlement_are_immutable_revisions(self):
        calls = [call()]
        self.recorder.update_ledger(calls)
        first = self.recorder.seal_ledger()
        old_bytes = (self.root / first.artifact_ref.relative_path).read_bytes()
        self.assertEqual(json.loads(old_bytes), calls)
        calls[0]["error"] = "ResponseUnknown: no reply"
        self.recorder.update_ledger(calls)
        second = self.recorder.seal_ledger()
        self.assertEqual(second.revision, first.revision + 1)
        self.assertEqual((self.root / first.artifact_ref.relative_path).read_bytes(), old_bytes)
        self.assertNotEqual(first.artifact_ref.sha256, second.artifact_ref.sha256)
        receipt = self.recorder.record_stage("DIAGNOSIS", attempt_id="attempt-0",
            snapshot_id="S0", status="UNKNOWN", call_refs=first.call_refs)
        self.assertEqual(receipt.call_refs, first.call_refs)

    def test_repeated_seal_without_changes_reuses_exact_immutable_revision(self):
        self.recorder.update_ledger([call()])
        first = self.recorder.seal_ledger()
        self.assertEqual(first, self.recorder.seal_ledger())

    def test_legacy_missing_request_stays_unbound(self):
        legacy = {"messages": [{"role": "user", "content": "legacy"}], "error": "unknown"}
        refs = self.recorder.update_ledger([legacy])
        self.assertIsNone(refs[0].request_digest)
        self.assertIsNone(refs[0].transport_digest)
        sealed = self.recorder.seal_ledger()
        self.assertEqual(json.loads((self.root / sealed.artifact_ref.relative_path).read_bytes()), [legacy])
        self.assertIsNone(sealed.call_refs[0].request_digest)

    def test_request_mutation_ledger_truncation_or_index_rebinding_is_rejected(self):
        original = [call()]
        self.recorder.update_ledger(original, first_call_index=5)
        changed = json.loads(json.dumps(original))
        changed[0]["request"]["messages"][0]["content"] = "different"
        changed[0]["request_digest"] = canonical_digest(changed[0]["request"])
        for calls, first in ((changed, 5), ([], 5), (original, 6)):
            with self.subTest(calls=calls, first=first), self.assertRaises(ValueError):
                self.recorder.update_ledger(calls, first_call_index=first)

    def test_caller_mutation_after_update_cannot_change_stored_ledger(self):
        calls = [call()]
        expected = json.loads(json.dumps(calls))
        self.recorder.update_ledger(calls)
        calls[0]["request"]["model"] = "changed-without-callback"
        sealed = self.recorder.seal_ledger()
        self.assertEqual(json.loads((self.root / sealed.artifact_ref.relative_path).read_bytes()), expected)

    def test_reopened_recorder_retains_segment_and_revision_identity(self):
        calls = [call()]
        self.recorder.update_ledger(calls, segment_index=3, first_call_index=9)
        first = self.recorder.seal_ledger(segment_index=3)
        reopened = RunRecorder(self.root, run_id="RUN", input_digest=INPUT)
        self.assertEqual(first, reopened.seal_ledger(segment_index=3))
        calls[0]["response_content"] = "received"
        reopened.update_ledger(calls, segment_index=3, first_call_index=9)
        self.assertEqual(reopened.seal_ledger(segment_index=3).revision, first.revision + 1)

    def test_stage_receipt_checks_and_records_all_projection_artifacts(self):
        reference = self.artifact()
        self.recorder.update_ledger([call()])
        sealed = self.recorder.seal_ledger()
        receipt = self.recorder.record_stage("DIAGNOSIS", attempt_id="first", snapshot_id="S0",
            status="READY", input_refs=(reference,), output_refs=(reference,),
            projection_refs=(reference,), call_refs=sealed.call_refs)
        wire = json.loads((self.root / receipt.relative_path).read_bytes())
        digest = wire.pop("receipt_digest")
        self.assertEqual(digest, canonical_digest(wire))
        self.assertEqual(wire["input_refs"][0]["sha256"], reference.sha256)
        self.assertEqual(wire["projection_refs"][0]["relative_path"], reference.relative_path)
        self.assertEqual(wire["call_refs"][0]["ledger_segment_digest"], sealed.artifact_ref.sha256)

    def test_changed_artifact_or_wrong_input_identity_cannot_enter_stage_receipt(self):
        reference = self.artifact()
        (self.root / reference.relative_path).write_text("changed")
        with self.assertRaises(ValueError):
            self.recorder.record_stage("DIAGNOSIS", attempt_id="bad", snapshot_id="S0",
                                       status="READY", input_refs=(reference,))
        with self.assertRaises(ValueError):
            self.recorder.record_stage("DIAGNOSIS", attempt_id="bad-input", snapshot_id="S0",
                status="READY", input_refs=(replace(reference, input_digest="d" * 64),))

    def test_forged_or_tampered_sealed_call_reference_is_rejected(self):
        self.recorder.update_ledger([call()])
        sealed = self.recorder.seal_ledger()
        forged = replace(sealed.call_refs[0], ledger_segment_digest="c" * 64)
        with self.assertRaises(ValueError):
            self.recorder.record_stage("DIAGNOSIS", attempt_id="forged", snapshot_id="S0",
                                       status="READY", call_refs=(forged,))
        (self.root / sealed.artifact_ref.relative_path).write_text("[]")
        with self.assertRaises(ValueError):
            self.recorder.record_stage("DIAGNOSIS", attempt_id="tampered", snapshot_id="S0",
                                       status="READY", call_refs=sealed.call_refs)

    def test_stage_updates_append_revisions_and_do_not_rewrite_old_receipt(self):
        first = self.recorder.record_stage("EXECUTE", attempt_id="one", snapshot_id="S0",
                                           status="STARTED")
        original = (self.root / first.relative_path).read_bytes()
        again = self.recorder.record_stage("EXECUTE", attempt_id="one", snapshot_id="S0",
                                           status="STARTED")
        second = self.recorder.record_stage("EXECUTE", attempt_id="one", snapshot_id="S1",
                                            status="FAILED")
        self.assertEqual(first, again)
        self.assertEqual(second.revision, first.revision + 1)
        self.assertEqual((self.root / first.relative_path).read_bytes(), original)

    def test_recording_error_never_replaces_existing_primary_error(self):
        primary = RuntimeError("primary failure")
        def broken():
            raise OSError("recording failure")
        self.assertIsNone(preserve_error(primary, broken))
        self.assertIn("recording failure", primary.recording_errors[0])
        with self.assertRaisesRegex(OSError, "recording failure"):
            preserve_error(None, broken)

    def test_actual_budget_provider_callback_binds_one_dispatch_and_later_response(self):
        from contractedge.model.budget import BudgetedProvider
        from contractedge.model.provider import ModelRequest, ModelResponse
        updates = []
        class Synthetic:
            def complete_json(self, request):
                return ModelResponse('{"received":true}', {"fixture": True})
        def record(calls):
            updates.append(self.recorder.update_ledger(calls))
        budget = BudgetedProvider(Synthetic(), len, max_tokens=10000,
            max_seconds=30, max_calls=2, on_update=record)
        request = ModelRequest(model="synthetic", prompt_version="public.fixture",
            messages=({"role": "user", "content": "public input"},), max_output_tokens=20,
            temperature=0.0)
        budget.complete_json(request)
        self.assertEqual(len(budget.calls), 1)
        self.assertEqual(updates[0], updates[1])
        sealed = self.recorder.seal_ledger()
        self.assertEqual(json.loads((self.root / sealed.artifact_ref.relative_path).read_bytes()),
                         json.loads(json.dumps(budget.calls)))
        self.assertEqual(sealed.call_refs[0].request_digest, budget.calls[0]["request_digest"])

    def test_same_actual_index_cannot_be_aliased_across_segments(self):
        self.recorder.update_ledger([call()], segment_index=0, first_call_index=0)
        with self.assertRaises(ValueError):
            self.recorder.update_ledger([call()], segment_index=1, first_call_index=0)

    def test_pending_tampering_cannot_rebind_already_sealed_dispatch_identity(self):
        self.recorder.update_ledger([call()])
        first = self.recorder.seal_ledger()
        path = self.root / "journal/ledger/segment-000000.inflight.json"
        state = json.loads(path.read_bytes())
        state["calls"][0]["request"]["model"] = "different"
        state["calls"][0]["request_digest"] = canonical_digest(state["calls"][0]["request"])
        path.write_text(json.dumps(state))
        with self.assertRaises(ValueError):
            self.recorder.seal_ledger()
        self.assertEqual(len(list((self.root / "journal/ledger/segment-000000").glob("*.seal.json"))), 1)
        self.assertEqual(first.revision, 0)

    def test_copied_stage_receipt_cannot_be_adopted_as_another_attempt_history(self):
        original = self.recorder.record_stage("EXECUTE", attempt_id="original",
            snapshot_id="S0", status="STARTED")
        target = self.root / "journal/stages/EXECUTE/different/revision-000000.json"
        target.parent.mkdir(parents=True)
        target.write_bytes((self.root / original.relative_path).read_bytes())
        with self.assertRaises(ValueError):
            self.recorder.record_stage("EXECUTE", attempt_id="different",
                snapshot_id="S0", status="STARTED")


if __name__ == "__main__":
    unittest.main()
