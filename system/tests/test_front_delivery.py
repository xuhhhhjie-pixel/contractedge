import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from contractedge.contracts.validation import canonical_digest
from contractedge.orchestration.evidence_delivery import select_observations_delivery
from contractedge.orchestration.front_delivery import build_front_delivery


class FrontDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ce-front-delivery-test-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.method = {"input_digest": "public-method"}
        excerpt = '{"value":"actual"}'
        self.observations = [{"id": "O1", "snapshot_id": "S1", "excerpt": excerpt,
            "content_digest": hashlib.sha256(excerpt.encode()).hexdigest(),
            "locator": {"path": "raw/public.json"}}]
        self.delivery = select_observations_delivery(self.observations, "", len, snapshot_id="S1")
        self.values = {
            "front.method_input.json": self.method,
            "front.config.json": {"model": "public-synthetic"},
            "front.public_observations.json": self.observations,
            "front.execution_feedback.json": None,
            "front.replay.json": {"model_call_range": [1, 3]},
        }
        for name, value in self.values.items():
            self.write(name, value)
        self.calls = [{"old": True},
            {"request": {"model": "public-synthetic", "messages": []},
             "transport_request": {"body_sha256": "actual-transport-digest"},
             "prompt_version": "diagnosis.public", "prompt_tokens_reserved": 11,
             "output_tokens_reserved": 13},
            {"messages": [{"role": "user", "content": "legacy request parameters unavailable"}]}]
        self.provider = SimpleNamespace(calls=self.calls)

    def write(self, name, value):
        (self.directory / name).write_text(json.dumps(value) + "\n", encoding="utf-8")

    def build(self, receipt=None):
        return build_front_delivery(self.directory, self.method, self.provider, 1,
            self.delivery if receipt is None else receipt)

    def test_actual_files_ledger_and_receipt_are_bound_without_legacy_reconstruction(self):
        result = self.build()
        self.assertEqual(result["schema_version"], "ce.front_delivery.v1")
        self.assertEqual(result["model_call_range"], [1, 3])
        self.assertEqual(result["requests"][0]["call_index"], 1)
        self.assertEqual(result["requests"][0]["request_digest"], canonical_digest(self.calls[1]["request"]))
        self.assertEqual(result["requests"][0]["transport_body_sha256"], "actual-transport-digest")
        self.assertIsNone(result["requests"][1]["request_digest"])
        self.assertEqual(result["evidence_delivery_receipt_digest"], self.delivery.receipt["receipt_digest"])
        self.assertEqual(result["effective_front_input_sha256"], canonical_digest({
            "method_input": self.method, "config": self.values["front.config.json"],
            "observations": self.observations, "feedback": None}))
        for name in self.values:
            self.assertEqual(result["artifacts"][name],
                hashlib.sha256((self.directory / name).read_bytes()).hexdigest())
        self.assertFalse((self.directory / "front.delivery.json").exists())

    def test_authoritative_top_level_request_digest_is_kept(self):
        self.calls[1]["request_digest"] = "recorded-final-request"
        self.assertEqual(self.build()["requests"][0]["request_digest"], "recorded-final-request")

    def test_changed_receipt_is_rejected(self):
        receipt = dict(self.delivery.receipt)
        receipt["status"] = "INCOMPLETE"
        with self.assertRaisesRegex(ValueError, "receipt digest"):
            self.build(receipt)

    def test_changed_persisted_observations_cannot_reuse_old_receipt(self):
        self.write("front.public_observations.json", [])
        with self.assertRaisesRegex(ValueError, "records digest"):
            self.build()

    def test_selected_references_must_match_actual_delivered_record_identity(self):
        receipt = dict(self.delivery.receipt)
        receipt["selected"] = [{"ref": {"id": "OTHER", "snapshot_id": "S1",
            "content_digest": self.observations[0]["content_digest"]}}]
        receipt["receipt_digest"] = canonical_digest({k: v for k, v in receipt.items()
                                                     if k != "receipt_digest"})
        with self.assertRaisesRegex(ValueError, "selected references"):
            self.build(receipt)

    def test_explicit_delivery_snapshot_must_match_actual_records(self):
        receipt = dict(self.delivery.receipt)
        receipt["snapshot_id"] = "S0"
        receipt["receipt_digest"] = canonical_digest({k: v for k, v in receipt.items()
                                                     if k != "receipt_digest"})
        with self.assertRaisesRegex(ValueError, "snapshot"):
            self.build(receipt)

    def test_no_receipt_keeps_legacy_input_honest_and_digest_null(self):
        result = build_front_delivery(self.directory, self.method, self.provider, 1)
        self.assertIsNone(result["evidence_delivery_receipt_digest"])
        self.assertIsNone(result["requests"][1]["request_digest"])

    def test_method_input_identity_must_match_persisted_json(self):
        with self.assertRaisesRegex(ValueError, "MethodInput"):
            build_front_delivery(self.directory, {"input_digest": "different"}, self.provider, 1)


if __name__ == "__main__":
    unittest.main()
