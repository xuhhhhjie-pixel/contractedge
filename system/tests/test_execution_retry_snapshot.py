import json
import tempfile
import unittest
from pathlib import Path
from contractedge.execution.topomen import build_execution_request, apply_edits, PlanRejected
from tests.test_repair_execution import bound_plan


class ExecutionRetrySnapshot(unittest.TestCase):
    def test_only_same_snapshot_attempt_enters_correction_prompt(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'templates').mkdir();(root/'templates/serviceaccount.yaml').write_text('name: current\n')
            plan=bound_plan(root);policy={'allowed_paths':['templates/**']}
            for snapshot, expected in [('historical-S0',False),(None,False),(plan['snapshot_id'],True)]:
                with self.subTest(snapshot=snapshot):
                    previous={'source_snapshot':snapshot,'model_response':'STALE_LITERAL_SENTINEL','validation_error':'bad anchor'}
                    request=build_execution_request(plan,root,policy,len,{'model':'fixture','max_context_tokens':30000},
                        requirement='selected',feedback={'route':'EXECUTE','rationale':'retain config'},previous_attempt=previous)
                    payload=json.loads(request.messages[-1]['content'])
                    self.assertEqual('STALE_LITERAL_SENTINEL' in request.messages[-1]['content'],expected)
                    self.assertEqual(payload['execution_feedback']['rationale'],'retain config')
                    self.assertEqual(payload['current_target_files'][0]['content'],'name: current\n')

    def test_anchor_error_identifies_target_and_does_not_edit(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'templates').mkdir();p=root/'templates/serviceaccount.yaml';p.write_text('name: current\n')
            plan=bound_plan(root)
            with self.assertRaises(PlanRejected) as raised:
                apply_edits(plan,root,{'allowed_paths':['templates/**']},{'edits':[{
                    'change_id':'C1','path':'templates/serviceaccount.yaml','operation':'MODIFY','old_text':'absent','new_text':'changed'}]})
            self.assertIn('matches=0',str(raised.exception));self.assertIn('change_id=C1',str(raised.exception))
            self.assertIn('path=templates/serviceaccount.yaml',str(raised.exception));self.assertIn('edit_index=0',str(raised.exception))
            self.assertEqual(p.read_text(),'name: current\n')
