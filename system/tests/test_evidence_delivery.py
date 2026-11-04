import hashlib
import json
import unittest

from contractedge.contracts.delivery import Delivery
from contractedge.contracts.validation import canonical_digest
from contractedge.orchestration.evidence_delivery import select_observations_delivery, annotate_delivery_citations


def observation(identifier, excerpt="public output", snapshot="S1", **fields):
    result = {"id": identifier, "snapshot_id": snapshot,
              "content_digest": hashlib.sha256(excerpt.encode()).hexdigest(),
              "excerpt": excerpt,
              "locator": {"path": "raw/" + identifier + ".json", "start_byte": 0,
                          "whole_file_read": True, "truncated": False}}
    result.update(fields)
    return result


def failed_check(identifier="FAILED", snapshot="S1"):
    body = json.dumps({"probe_id": "public", "id": identifier, "status": "FAIL",
                       "statement": "The public condition holds.", "observed": {"value": False}})
    return observation(identifier, body, snapshot, evidence_kind="PUBLIC_BEHAVIOR",
                       behavior_check={"id": "public:" + identifier, "status": "FAIL",
                                       "probe_sha256": "f" * 64})


class EvidenceDeliveryTests(unittest.TestCase):
    def test_delivery_receipt_separates_delivery_from_model_citation(self):
        record = observation("ONE")
        delivery = select_observations_delivery([record], "condition", len, 100)
        cited = annotate_delivery_citations(delivery, {
            "checks": [{"evidence": [{"observation_id": "ONE", "quote": "public"}]}]})
        self.assertEqual(cited.receipt["model_citation_status"], "CITED")
        self.assertEqual(cited.receipt["citation_status"]["ONE"], "CITED")
        self.assertEqual(cited.receipt["selected"][0]["ref"]["id"], "ONE")

    def test_original_resource_priority_and_complete_records_are_preserved(self):
        def pod(identifier, kind, name, fields):
            record = observation(identifier, json.dumps({"resource": {"kind": kind,
                "name": name, "pod_spec_fields": fields}}))
            record["locator"]["path"] = "raw/trigger.pod-spec-" + identifier + ".json"
            return record
        deployment = pod("D", "Deployment", "gateway", {"value": "node selector " * 20})
        create = pod("J1", "Job", "gateway-create", {})
        patch = pod("J2", "Job", "gateway-patch", {})
        delivery = select_observations_delivery([deployment, create, patch], "gateway jobs",
            len, len(deployment["excerpt"]) + len(create["excerpt"]))
        self.assertIsInstance(delivery, Delivery)
        self.assertEqual([r["id"] for r in delivery.records], ["J1", "J2"])
        self.assertEqual(delivery.records[0], create)
        self.assertEqual(delivery.receipt["status"], "COMPLETE")
        self.assertEqual(delivery.receipt["omitted"][0]["reason"], "TOKEN_BUDGET")

    def test_current_trusted_failed_check_omitted_by_budget_is_incomplete(self):
        record = failed_check()
        delivery = select_observations_delivery([record], "condition", len,
            len(record["excerpt"]) - 1, snapshot_id="S1")
        self.assertEqual(delivery.records, ())
        self.assertEqual(delivery.receipt["status"], "INCOMPLETE")
        self.assertEqual(delivery.receipt["mandatory_coverage"]["missing"][0]["id"], record["id"])
        self.assertEqual(delivery.receipt["omitted"][0]["reason"], "TOKEN_BUDGET")

    def test_operation_failure_is_automatically_required(self):
        record = observation("OP", json.dumps({"exception": {"type": "RuntimeError",
            "message": "operation failed"}}), structured_complete=True,
            producer_manifest_sha256="p" * 64, source_file_sha256="s" * 64,
            adapter_id="public-object-adoption-v1")
        delivery = select_observations_delivery([record], "operation", len, 0,
            snapshot_id="S1")
        self.assertEqual(delivery.receipt["status"], "INCOMPLETE")
        self.assertEqual(delivery.receipt["mandatory_refs"][0]["id"], "OP")

    def test_untyped_or_unregistered_failure_does_not_make_every_record_mandatory(self):
        ordinary = observation("ORDINARY", "ordinary output" * 10)
        unregistered = observation("UNREGISTERED", '{"status":"FAIL"}',
            behavior_check={"status": "FAIL"})
        delivery = select_observations_delivery([ordinary, unregistered], "", len, 0,
            snapshot_id="S1")
        self.assertEqual(delivery.receipt["status"], "COMPLETE")
        self.assertEqual(delivery.receipt["mandatory_refs"], [])

    def test_explicit_current_snapshot_never_relabels_or_delivers_stale_evidence(self):
        stale, fresh = failed_check("OLD", "S0"), observation("NEW", snapshot="S1")
        delivery = select_observations_delivery([stale, fresh], "", len, snapshot_id="S1")
        self.assertEqual(delivery.records, (fresh,))
        self.assertEqual(delivery.receipt["omitted"][0]["reason"], "STALE_SNAPSHOT")
        self.assertEqual(delivery.receipt["omitted"][0]["ref"]["snapshot_id"], "S0")
        self.assertEqual(stale["snapshot_id"], "S0")
        self.assertEqual(delivery.receipt["mandatory_refs"], [])

    def test_explicit_stale_required_ref_is_incomplete(self):
        stale = observation("OLD", snapshot="S0")
        delivery = select_observations_delivery([stale], "", len, snapshot_id="S1",
                                               mandatory_refs=("OLD",))
        self.assertEqual(delivery.receipt["status"], "INCOMPLETE")
        self.assertEqual(delivery.receipt["mandatory_coverage"]["missing"][0]["snapshot_id"], "S0")

    def test_legacy_none_keeps_cross_snapshot_selection_unchanged(self):
        older = observation("OLD", snapshot="S0")
        delivery = select_observations_delivery([older], "", len)
        self.assertEqual(delivery.records, (older,))
        self.assertIsNone(delivery.receipt["snapshot_id"])

    def test_missing_snapshot_is_omitted_when_current_snapshot_is_required(self):
        record = observation("NO_SNAPSHOT")
        record.pop("snapshot_id")
        delivery = select_observations_delivery([record], "", len, snapshot_id="S1")
        self.assertEqual(delivery.records, ())
        self.assertEqual(delivery.receipt["omitted"][0]["reason"], "MISSING_SNAPSHOT")
        self.assertIsNone(delivery.receipt["input_refs"][0]["snapshot_id"])

    def test_colliding_id_with_different_snapshot_or_content_is_rejected(self):
        for other in (observation("SAME", "different"), observation("SAME", snapshot="S0")):
            with self.subTest(other=other):
                delivery = select_observations_delivery([observation("SAME"), other], "", len)
                self.assertEqual(delivery.records, ())
                self.assertEqual(delivery.receipt["status"], "REJECTED")
                self.assertEqual(delivery.receipt["errors"][0]["code"], "OBSERVATION_ID_COLLISION")

    def test_identical_duplicate_has_explicit_receipt_without_duplicate_delivery(self):
        record = observation("SAME")
        delivery = select_observations_delivery([record, dict(record)], "", len)
        self.assertEqual(delivery.records, (record,))
        self.assertEqual(delivery.receipt["omitted"][0]["reason"], "DUPLICATE_IDENTICAL")

    def test_unknown_or_mismatched_mandatory_reference_is_rejected(self):
        bad_refs = [("ABSENT",), ({"id": "ONE", "snapshot_id": "S0",
                                  "content_digest": "f" * 64},), ({"id": "ONE"},)]
        for refs in bad_refs:
            with self.subTest(refs=refs):
                delivery = select_observations_delivery([observation("ONE")], "", len,
                    mandatory_refs=refs)
                self.assertEqual(delivery.records, ())
                self.assertEqual(delivery.receipt["status"], "REJECTED")
                self.assertEqual(delivery.receipt["errors"][0]["code"], "MANDATORY_REF_INVALID")

    def test_mismatched_record_digest_is_rejected(self):
        record = observation("BAD")
        record["content_digest"] = "f" * 64
        delivery = select_observations_delivery([record], "", len)
        self.assertEqual(delivery.receipt["status"], "REJECTED")
        self.assertEqual(delivery.receipt["errors"][0]["code"], "OBSERVATION_DIGEST_MISMATCH")

    def test_exact_mandatory_ref_and_receipt_digests_bind_delivered_records(self):
        record = observation("ONE")
        reference = {k: record[k] for k in ("id", "snapshot_id", "content_digest")}
        delivery = select_observations_delivery([record], "", len, snapshot_id="S1",
                                               mandatory_refs=(reference,))
        receipt = dict(delivery.receipt)
        self.assertEqual(receipt["mandatory_coverage"]["delivered"], [reference])
        self.assertEqual(receipt["records_digest"], canonical_digest(list(delivery.records)))
        digest = receipt.pop("receipt_digest")
        self.assertEqual(digest, canonical_digest(receipt))
        self.assertEqual(receipt["budget"]["used_tokens"], len(record["excerpt"]))

    def test_unhashable_mandatory_reference_is_rejected_instead_of_raising(self):
        supplied = {"id": [], "snapshot_id": "S1", "content_digest": "f" * 64}
        delivery = select_observations_delivery([observation("ONE")], "", len,
                                                mandatory_refs=(supplied,))
        self.assertEqual(delivery.receipt["status"], "REJECTED")
        self.assertEqual(delivery.receipt["errors"][0]["code"], "MANDATORY_REF_INVALID")

    def test_declared_failed_observation_with_unverifiable_body_is_rejected(self):
        variants = [
            observation("BAD_BODY", '{"status":"PASS"}', evidence_kind="PUBLIC_BEHAVIOR",
                behavior_check={"id": "probe:BAD_BODY", "status": "FAIL", "probe_sha256": "f" * 64}),
            observation("NO_PROBE", '{"status":"FAIL"}', evidence_kind="PUBLIC_BEHAVIOR",
                behavior_check={"id": "probe:NO_PROBE", "status": "FAIL"}),
        ]
        truncated = failed_check("TRUNCATED")
        truncated["locator"]["truncated"] = True
        variants.append(truncated)
        for record in variants:
            with self.subTest(record=record["id"]):
                delivery = select_observations_delivery([record], "", len, snapshot_id="S1")
                self.assertEqual(delivery.records, ())
                self.assertEqual(delivery.receipt["status"], "REJECTED")
                self.assertIn("UNVERIFIABLE_FAILED_OBSERVATION",
                              [e["code"] for e in delivery.receipt["errors"]])

    def test_registered_failed_check_requires_explicit_complete_locator(self):
        for missing in ("truncated", "whole_file_read"):
            record = failed_check()
            record["locator"].pop(missing)
            with self.subTest(missing=missing):
                delivery = select_observations_delivery([record], "", len, snapshot_id="S1")
                self.assertEqual(delivery.receipt["status"], "REJECTED")
                self.assertIn("UNVERIFIABLE_FAILED_OBSERVATION",
                              [e["code"] for e in delivery.receipt["errors"]])

    def test_invalid_token_budgets_are_rejected(self):
        for budget in (-1, 1.5, True, "10"):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                select_observations_delivery([observation("ONE")], "", len, budget)

    def test_counter_cannot_create_negative_or_fractional_token_capacity(self):
        for count in (-1, 0.5, True):
            with self.subTest(count=count), self.assertRaises(ValueError):
                select_observations_delivery([observation("ONE")], "", lambda _: count)


if __name__ == "__main__":
    unittest.main()
