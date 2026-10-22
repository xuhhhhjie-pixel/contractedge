import json
from pathlib import Path
import tempfile
import unittest
from contractedge.evaluation.repair_batch import evaluate_repair_batch

class RepairCohortEvaluationTests(unittest.TestCase):
    def test_missing_candidates_stay_in_denominator_without_starting_a_scorer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);evaluation=root/'evaluation';evaluation.mkdir()
            (evaluation/'manifest.jsonl').write_text(json.dumps({'case_id':'A'})+'\n'+json.dumps({'case_id':'B'})+'\n')
            batch=root/'batch';batch.mkdir()
            (batch/'batch.json').write_text(json.dumps({'case_count':2,'records':[{'case_id':'A','status':'EVIDENCE_GAP'},{'case_id':'B','status':'NOT_RUN'}]}))
            report=evaluate_repair_batch(evaluation,batch,root/'tools',root/'score')
            self.assertEqual(report['case_count'],2)
            self.assertEqual(report['status_counts'],{'NO_CANDIDATE':1,'NOT_RUN':1})
            self.assertEqual(report['pass_count'],0)

    def test_registry_mismatch_is_rejected_before_any_scoring(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);evaluation=root/'evaluation';evaluation.mkdir()
            (evaluation/'manifest.jsonl').write_text(json.dumps({'case_id':'A'})+'\n')
            batch=root/'batch';batch.mkdir()
            (batch/'batch.json').write_text(json.dumps({'case_count':1,'records':[{'case_id':'B','status':'NOT_RUN'}]}))
            with self.assertRaisesRegex(ValueError,'membership'):
                evaluate_repair_batch(evaluation,batch,root/'tools',root/'score')

    def test_timed_out_scorer_cannot_contribute_a_pass_even_if_score_file_exists(self):
        from unittest.mock import patch
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);evaluation=root/'evaluation';evaluation.mkdir()
            (evaluation/'manifest.jsonl').write_text(json.dumps({'case_id':'A'})+'\n')
            tools=root/'tools';tools.mkdir();batch=root/'batch';batch.mkdir()
            patch_file=batch/'candidate.patch';patch_file.write_text('patch')
            digest=hashlib.sha256(patch_file.read_bytes()).hexdigest()
            trace=batch/'repair.trace.json';trace.write_text('{}')
            record=dict(case_id='A',status='PUBLIC_EXECUTION_COMPLETE',trace=trace.name,
                trace_sha256=hashlib.sha256(trace.read_bytes()).hexdigest(),candidate=dict(patch_path=str(patch_file),patch_bytes=5,patch_sha256=digest))
            (batch/'batch.json').write_text(json.dumps({'case_count':1,'records':[record]}))
            def fail_after_score(argv,*args):
                out=Path(argv[argv.index('--output')+1]);out.mkdir(parents=True)
                (out/'score.json').write_text(json.dumps(dict(case_id='A',status='PASS',patch_sha256=digest)))
                return dict(returncode=2,timed_out=True)
            with patch('contractedge.evaluation.repair_batch.run_tool',side_effect=fail_after_score):
                report=evaluate_repair_batch(evaluation,batch,tools,root/'score')
            self.assertEqual(report['pass_count'],0)
            self.assertEqual(report['records'][0]['status'],'SCORER_TIMEOUT')

    def test_active_batch_cannot_be_privately_scored_before_candidate_selection_finishes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);evaluation=root/'evaluation';evaluation.mkdir();batch=root/'batch';batch.mkdir()
            (evaluation/'manifest.jsonl').write_text(json.dumps({'case_id':'A'})+'\n')
            (batch/'batch.json').write_text(json.dumps({'case_count':1,'records':[{'case_id':'A','status':'RUNNING'}]}))
            with self.assertRaisesRegex(ValueError,'active'):
                evaluate_repair_batch(evaluation,batch,root/'tools',root/'score')
