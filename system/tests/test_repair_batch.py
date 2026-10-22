import json
from pathlib import Path
import tempfile
import unittest

from contractedge.orchestration.batch import run_batch
from contractedge.contracts.validation import canonical_digest
from tests.test_benchmark_current import make_public_fixture


class RepairBatchTests(unittest.TestCase):
    def test_insufficient_work_disk_keeps_members_unattempted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);public,_=make_public_fixture(root/'data')
            result=run_batch(public,root/'batch',{'min_free_work_disk_bytes':10**30},lambda *a:self.fail('must not dispatch'))
            self.assertEqual(result['status_counts'],{'NOT_RUN':1})
            self.assertEqual(result['stop_reason'],'INSUFFICIENT_WORK_DISK')

    def test_changed_external_public_tools_reject_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);public,_=make_public_fixture(root/'data')
            tools=root/'tools';tools.mkdir();script=tools/'prepare_case.py';script.write_text('# original')
            run_batch(public,root/'batch',{},lambda *a:self.fail('not selected'),max_new_cases=0,tools_root=tools)
            script.write_text('# changed')
            with self.assertRaisesRegex(ValueError,'identity'):
                run_batch(public,root/'batch',{},lambda *a:self.fail('must not dispatch'),tools_root=tools)

    def test_formal_profile_rejects_wrong_membership_before_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);public,_=make_public_fixture(root/'data')
            with self.assertRaisesRegex(ValueError,'membership'):
                run_batch(public,root/'batch',{'expected_case_count':150},lambda *a:self.fail('must not dispatch'))

    def test_global_money_rejection_stops_before_dispatching_remaining_members(self):
        from contractedge.model.money import MoneyBudget, MoneyBudgetExceeded
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);public,_=make_public_fixture(root/'data')
            manifest=public/'manifest.jsonl';row=json.loads(manifest.read_text())
            manifest.write_text(manifest.read_text()+json.dumps({**row,'case_id':'CASE-2'})+'\n')
            settings=dict(path=str(root/'money.json'),limit_cny='0.01',model='test',input_cny_per_million='2',output_cny_per_million='8')
            budget=MoneyBudget(**settings)
            def execute(case_id,directory):
                directory.mkdir(parents=True)
                with self.assertRaises(MoneyBudgetExceeded):budget.reserve('test',100000,10000)
                result=dict(case_id=case_id,status='BUDGET_EXHAUSTED',candidate=None)
                (directory/'repair.trace.json').write_text(json.dumps(result));return result
            result=run_batch(public,root/'batch',{'money_budget':settings},execute)
            self.assertEqual(result['status_counts'],{'BUDGET_EXHAUSTED':1,'NOT_RUN':1})
            self.assertEqual(result['stop_reason'],'EXPERIMENT_MONEY_CAP')

    def test_crash_after_trace_commit_recovers_candidate_without_reexecution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);public,_=make_public_fixture(root/'data')
            def crash(case_id,directory):
                directory.mkdir(parents=True)
                candidate={'patch_sha256':'a'*64,'patch_bytes':10,'patch_path':str(directory/'candidate.patch')}
                (directory/'repair.trace.json').write_text(json.dumps(dict(case_id=case_id,status='PUBLIC_EXECUTION_COMPLETE',candidate=candidate)))
                raise KeyboardInterrupt()
            with self.assertRaises(KeyboardInterrupt):
                run_batch(public,root/'batch',{},crash)
            result=run_batch(public,root/'batch',{},lambda *a:self.fail('must not replay'))
            self.assertEqual(result['records'][0]['candidate']['patch_sha256'],'a'*64)

    def test_completed_case_is_not_rebilled_on_resume_and_changed_config_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); public, _ = make_public_fixture(root/'data')
            calls = []
            def execute(case_id, directory):
                calls.append(case_id); directory.mkdir(parents=True)
                report = {'case_id':case_id,'status':'INVALID_OUTPUT','candidate':None,'errors':['bad JSON']}
                (directory/'repair.trace.json').write_text(json.dumps(report))
                return report
            out = root/'batch'; config = {'model':'test'}
            report = run_batch(public, out, config, execute)
            self.assertEqual(report['status_counts'], {'INVALID_OUTPUT':1})
            run_batch(public, out, config, execute)
            self.assertEqual(calls,['CASE-1'])
            trace = out/'cases/CASE-1/repair.trace.json'
            trace.write_text(trace.read_text()+'\n')
            with self.assertRaisesRegex(ValueError,'trace.*changed'):
                run_batch(public, out, config, execute)
            with self.assertRaisesRegex(ValueError,'identity'):
                run_batch(public, out, {'model':'changed'}, execute)
            self.assertEqual(calls,['CASE-1'])

    def test_crashed_case_is_retained_without_automatic_paid_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); public,_=make_public_fixture(root/'data')
            def interrupted(case_id, directory):
                directory.mkdir(parents=True)
                raise RuntimeError('unexpected crash')
            report=run_batch(public, root/'batch', {}, interrupted)
            self.assertEqual(report['records'][0]['status'],'INTERRUPTED')
            def must_not_run(*args): self.fail('paid work must not repeat after crash')
            report=run_batch(public, root/'batch', {}, must_not_run)
            self.assertEqual(report['case_count'],1)
            self.assertEqual(report['records'][0]['status'],'INTERRUPTED')

    def test_terminal_workspace_can_be_rebuilt_from_retained_patch_and_frozen_inputs(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);public,_=make_public_fixture(root/'data')
            def execute(case_id,directory):
                (directory/'workspace/source').mkdir(parents=True)
                (directory/'workspace/source/generated.py').write_text('candidate code')
                patch_file=directory/'candidate.patch';patch_file.write_text('diff')
                candidate=dict(patch_path=str(patch_file),patch_bytes=4,patch_sha256=hashlib.sha256(b'diff').hexdigest())
                result=dict(case_id=case_id,status='PUBLIC_EXECUTION_COMPLETE',candidate=candidate)
                (directory/'repair.trace.json').write_text(json.dumps(result));return result
            result=run_batch(public,root/'batch',{'keep_workspaces':False},execute)
            directory=root/'batch/cases/CASE-1'
            self.assertFalse((directory/'workspace').exists())
            self.assertTrue((directory/'candidate.patch').is_file())
            self.assertTrue((directory/'workspace-retention.json').is_file())
            self.assertEqual(result['records'][0]['status'],'PUBLIC_EXECUTION_COMPLETE')
