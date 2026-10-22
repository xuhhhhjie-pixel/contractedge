import unittest

from contractedge.changeset.adapter import adapt_model_changeset, bind_changeset_envelope
from contractedge.contracts.models import Snapshot
from contractedge.contracts.validation import canonical_digest, parse_method_input, validate_diagnosis
from tests.test_contracts import valid_diagnosis, valid_method_input


class ModelChangeSetAdapterTests(unittest.TestCase):
    def setUp(self):
        self.diagnosis = validate_diagnosis(valid_diagnosis(), parse_method_input(valid_method_input()), None)

    def test_binds_run_identity_and_diagnosis_digest_and_audits_model_copy_errors(self):
        payload = {"schema_version": "wrong", "run_id": "wrong-run", "snapshot_id": "stale",
                   "input_digest": "bad", "diagnosis_digest": "bad"}

        result, audit = bind_changeset_envelope(payload, self.diagnosis, "run-1", "snap-0", "a" * 64)

        self.assertEqual(result["run_id"], "run-1")
        self.assertEqual(result["snapshot_id"], "snap-0")
        self.assertEqual(result["input_digest"], "a" * 64)
        self.assertEqual(result["diagnosis_digest"], canonical_digest(self.diagnosis.raw))
        self.assertEqual(audit["model_mismatched_fields"],
                         ["diagnosis_digest", "input_digest", "run_id", "schema_version", "snapshot_id"])

    def test_resolves_existing_target_from_semantic_unit_and_snapshot(self):
        payload = {"changes": [{"id": "C1", "target": {"target_kind": "EXISTING", "unit_refs": ["U2"]}}]}
        snapshot = Snapshot("snap-0", ".", {"templates/serviceaccount.yaml": "b" * 64})

        result = adapt_model_changeset(payload, self.diagnosis, snapshot)

        self.assertEqual(result["changes"][0]["target"], {
            "kind": "EXISTING", "path": "templates/serviceaccount.yaml",
            "locator": {"start_line": 1, "end_line": 8}, "unit_refs": ["U2"],
            "snapshot_id": "snap-0", "content_digest": "b" * 64,
        })

    def test_translates_new_target_selector_without_inventing_existing_fields(self):
        payload = {"changes": [{"id": "C1", "target": {"target_kind": "NEW",
            "parent_path": "templates", "proposed_path": "templates/serviceaccount.yaml",
            "expected_role": "chart template", "anchor_unit_refs": ["U1"],
            "missing_unit_refs": ["U2"], "justification_refs": ["EV2"]}}]}

        result = adapt_model_changeset(payload, self.diagnosis, None)

        self.assertEqual(result["changes"][0]["target"]["kind"], "NEW")
        self.assertNotIn("target_kind", result["changes"][0]["target"])
        self.assertNotIn("snapshot_id", result["changes"][0]["target"])

    def test_existing_target_must_resolve_to_one_snapshot_file(self):
        payload = {"changes": [{"id": "C1", "target": {"target_kind": "EXISTING", "unit_refs": ["U2"]}}]}
        snapshot = Snapshot("snap-0", ".", {})

        with self.assertRaisesRegex(ValueError, "NOT_IN_SNAPSHOT"):
            adapt_model_changeset(payload, self.diagnosis, snapshot)

    def test_existing_target_source_path_must_match_selected_units(self):
        payload = {"changes": [{"id": "C1", "target": {"target_kind": "EXISTING",
            "source_path": "values.yaml", "unit_refs": ["U2"]}}]}
        snapshot = Snapshot("snap-0", ".", {"templates/serviceaccount.yaml": "b" * 64,
                                              "values.yaml": "c" * 64})

        with self.assertRaisesRegex(ValueError, "MODEL_EXISTING_TARGET_PATH_MISMATCH"):
            adapt_model_changeset(payload, self.diagnosis, snapshot)

    def test_explicit_source_path_disambiguates_multifile_semantic_role_without_new_permission(self):
        from copy import deepcopy
        from dataclasses import replace
        raw=deepcopy(self.diagnosis.raw)
        source=next(e for e in raw['evidence_refs'] if e['kind']=='SOURCE_SPAN')
        extra={**source,'id':'OTHER','locator':{**source['locator'],'path':'helpers.go','start_line':100,'end_line':120}}
        raw['evidence_refs'].append(extra)
        target=next(u for u in raw['units'] if u['id']=='U2');target['locator_refs'].append('OTHER')
        packet=replace(self.diagnosis,raw=raw)
        snapshot=Snapshot('snap-0','.',{'templates/serviceaccount.yaml':'b'*64,'helpers.go':'c'*64})
        plan={'changes':[{'id':'C','target':{'target_kind':'EXISTING','source_path':'templates/serviceaccount.yaml','unit_refs':['U2']}}]}
        selected=adapt_model_changeset(plan,packet,snapshot)['changes'][0]['target']
        self.assertEqual(selected['path'],'templates/serviceaccount.yaml')
        self.assertEqual(selected['locator']['end_line'],8)


if __name__ == "__main__":
    unittest.main()
