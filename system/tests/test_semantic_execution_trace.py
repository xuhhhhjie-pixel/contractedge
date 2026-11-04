import tempfile,unittest
from pathlib import Path
from tests.test_repair_execution import bound_plan
from contractedge.execution.topomen import apply_edits
from contractedge.model.budget import BudgetExceeded


class SemanticExecutionTraceTests(unittest.TestCase):
    def test_plan_intent_conditions_and_actual_edit_spans_remain_auditable(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'templates').mkdir();(root/'templates/serviceaccount.yaml').write_text('name: default\n')
            p=bound_plan(root)
            result=apply_edits(p,root,{'allowed_paths':['templates/*']},{'edits':[{
                'change_id':'C1','path':'templates/serviceaccount.yaml','operation':'MODIFY',
                'old_text':'name: default','new_text':'name: selected'}]})
            trace=result.get('semantic_edit_trace',[])
            self.assertEqual(len(trace),1)
            self.assertEqual(trace[0]['change_id'],'C1')
            self.assertEqual(trace[0]['semantic_action'],p['operations'][0]['semantic_action'])
            self.assertEqual(trace[0]['postcondition_refs'],p['operations'][0]['postcondition_refs'])
            self.assertEqual(trace[0]['literal_edits'][0]['old_text'],'name: default')
            self.assertEqual(trace[0]['literal_edits'][0]['new_text'],'name: selected')
            self.assertIn('NOT_PROVEN',trace[0]['semantic_status'])

    def test_review_mapping_keeps_complete_edits_or_rejects_explicit_limit(self):
        from contractedge.execution.topomen import execution_review_mapping
        result={'from_snapshot':'before','to_snapshot':'after',
            'semantic_edit_trace':[{'change_id':'C','semantic_action':'Keep trigger',
            'literal_edits':[{'old_text':'old','new_text':'x'*10000}]}]}
        with self.assertRaisesRegex(BudgetExceeded, 'context'):
            execution_review_mapping(result,max_characters=30)
        mapping=execution_review_mapping(result)
        self.assertEqual(mapping[0]['literal_edits'][0]['old_text'],'old')
        self.assertFalse(mapping[0]['literal_edits'][0]['truncated'])
        self.assertEqual(mapping[0]['literal_edits'][0]['new_text'],'x'*10000)
        self.assertEqual(result['semantic_edit_trace'][0]['literal_edits'][0]['new_text'],'x'*10000)
