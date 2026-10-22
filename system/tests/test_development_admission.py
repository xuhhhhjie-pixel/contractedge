import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from contractedge.orchestration.batch import run_batch
from contractedge.orchestration.repair import repair_case


class DevelopmentAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.public = self.root / 'public'; self.public.mkdir()
        rows = [{'case_id': name, 'primary_class': 'R1'} for name in ['DEV', 'VAL', 'BLIND']]
        (self.public / 'manifest.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
        directory = self.root / 'partition'; directory.mkdir()
        self.partition = directory / 'member_groups.jsonl'
        self.partition.write_text(''.join(json.dumps({**r, 'candidate_split': split}) + '\n'
            for r, split in zip(rows, ['development', 'validation', 'blind_test'])))
        self.config = {'development_only': True, 'expected_partition_manifest_sha256': hashlib.sha256(self.partition.read_bytes()).hexdigest()}
        self.dispatched = []

    def execute(self, case, directory):
        self.dispatched.append(case); directory.mkdir(parents=True)
        result = {'case_id': case, 'status': 'EVIDENCE_GAP'}
        (directory / 'repair.trace.json').write_text(json.dumps(result))
        return result

    def test_default_development_batch_keeps_full_denominator_and_dispatches_only_development(self):
        result = run_batch(self.public, self.root / 'batch', self.config, self.execute)
        self.assertEqual(self.dispatched, ['DEV'])
        self.assertEqual(result['case_count'], 3)
        self.assertEqual(result['status_counts'], {'EVIDENCE_GAP': 1, 'NOT_RUN': 2})

    def test_explicit_validation_selection_is_rejected_before_any_dispatch(self):
        with self.assertRaisesRegex(ValueError, 'development'):
            run_batch(self.public, self.root / 'batch', self.config, self.execute, case_ids=['VAL'])
        self.assertEqual(self.dispatched, [])
        self.assertFalse((self.root / 'batch').exists())

    def test_single_repair_rejects_blind_before_preparation_or_model_use(self):
        with self.assertRaisesRegex(ValueError, 'development'):
            repair_case(self.public, self.root, 'BLIND', self.root / 'run', None, len, self.config)
        self.assertFalse((self.root / 'run').exists())

    def test_changed_partition_is_rejected_before_dispatch(self):
        self.partition.write_text(self.partition.read_text().replace('validation', 'development'))
        with self.assertRaisesRegex(ValueError, 'partition'):
            run_batch(self.public, self.root / 'batch', self.config, self.execute)
        self.assertEqual(self.dispatched, [])

    def test_development_without_a_frozen_partition_pin_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'partition'):
            run_batch(self.public, self.root / 'batch', {'development_only': True}, self.execute)
        self.assertEqual(self.dispatched, [])

    def test_formal_profile_retains_and_dispatches_all_members(self):
        result = run_batch(self.public, self.root / 'batch', {'development_only': False}, self.execute)
        self.assertEqual(self.dispatched, ['DEV', 'VAL', 'BLIND'])
        self.assertEqual(result['attempted'], 3)
