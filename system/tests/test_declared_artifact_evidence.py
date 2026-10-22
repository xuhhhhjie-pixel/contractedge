import hashlib
import unittest
from dataclasses import replace

from contractedge.contracts.validation import parse_method_input, validate_diagnosis
from contractedge.diagnosis.evidence import build_evidence_catalog
from contractedge.source.units import extract_declared_artifact_units
from tests.test_contracts import valid_method_input, valid_diagnosis


class DeclaredArtifactEvidenceTests(unittest.TestCase):
    def test_immutable_input_spans_are_not_misrepresented_as_mutable_source(self):
        artifact = {'id': 'INPUT1', 'path': 'declared-artifacts/trigger.yaml',
            'media_type': 'application/yaml', 'content': 'enabled: true\nname: selected\n',
            'content_digest': hashlib.sha256(b'enabled: true\nname: selected\n').hexdigest()}
        method = replace(parse_method_input(valid_method_input()), declared_artifacts=(artifact,))
        units = extract_declared_artifact_units([artifact], 'snap-0', max_lines=1)
        records = build_evidence_catalog(method, {'candidates': units}, 'snap-0')
        self.assertTrue(records)
        self.assertTrue(all(r['kind'] == 'DECLARED_ARTIFACT' for r in records))
        for record in records:
            loc = record['locator']
            self.assertEqual(artifact['content'][loc['start_char']:loc['end_char']], record['excerpt'])
            self.assertEqual(loc['artifact_content_digest'], artifact['content_digest'])
            self.assertEqual(hashlib.sha256(record['excerpt'].encode()).hexdigest(), record['content_digest'])
        self.assertTrue(all(u['mutable'] is False for u in units))

        # The runner binds canonical evidence before validating the model's packet.
        # Every kind emitted by that binder must cross the actual contract boundary.
        packet = valid_diagnosis()
        packet['evidence_refs'].extend(records)
        parsed = validate_diagnosis(packet, method, None)
        self.assertEqual(parsed.evidence_refs[-1]['kind'], 'DECLARED_ARTIFACT')
