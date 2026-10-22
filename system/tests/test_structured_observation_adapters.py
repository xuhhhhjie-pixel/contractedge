import json
from pathlib import Path
import tempfile
import unittest

try:
    from contractedge.execution import observations
except ImportError:
    observations = None

ROOT = Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019/public')


class StructuredObservationTests(unittest.TestCase):
    def profile(self, case='R3-KOPF-726'):
        self.assertIsNotNone(observations, 'Registered public observation adapter is not implemented')
        return observations.select_observation_adapters(ROOT, case)

    def derive(self, value, *, case='R3-KOPF-726', limited=False, successful=True):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        root = Path(temp.name); raw = root/'raw'; raw.mkdir()
        profile = self.profile(case); scenario = profile[0]['scenario']
        (raw/'public_execution.json').write_text(json.dumps({'scenarios':[{'scenario':scenario,
            'exit_code':0 if successful else 1,'timed_out':False,'output_limited':limited}]}))
        (raw/(scenario+'.stdout')).write_text(json.dumps(value))
        return observations.derive_observations(raw, root/'derived', profile)

    def test_real_kopf_objects_are_complete_resource_scoped_records(self):
        value = {'observations':[{'name':'cluster-child','exception':None,'resource':{
            'kind':'Namespace','metadata':{'name':'child','labels':{'large':'x'*8500}}}}]}
        records, audit = self.derive(value)
        self.assertEqual(audit[0]['status'],'COMPLETE')
        self.assertEqual(len(records),1)
        path, meta = next(iter(records.items())); item=json.loads(path.read_text())
        self.assertEqual(item,value['observations'][0])
        self.assertEqual(meta['evidence_kind'],'PUBLIC_BEHAVIOR')
        self.assertEqual(meta['resource_identity']['name'],'child')
        self.assertEqual(meta['source_pointer'],'/observations/0')
        self.assertTrue(meta['structured_complete'])

    def test_executed_local_adoption_exception_supports_failure_without_claiming_cluster_behavior(self):
        from contractedge.execution.support import support_error
        profile=self.profile()[0]
        self.assertEqual(profile['id'],'public-object-adoption-operation-v2')
        self.assertIn('no Kubernetes API',profile['boundary'])
        records,_=self.derive({'observations':[{'name':'cluster-child','resource':{
            'kind':'Namespace','metadata':{'name':'child'}},'exception':{'type':'LookupError','message':'owner unresolved'}}]})
        path,meta=next(iter(records.items()))
        observation=dict(meta,id='OBS',kind='PUBLIC_OBSERVATION',snapshot_id='S1',excerpt=path.read_text())
        claim={'status':'FAIL','evidence':[{'observation_id':'OBS','quote':'owner unresolved'}]}
        self.assertIsNone(support_error({'verification_kind':'PUBLIC_BEHAVIOR',
            'verification_scope':{'scenario':profile['scenario'],'resource':{'kind':'Namespace','name':'child'}}},claim,[observation]))

    def test_unknown_producer_never_gets_semantic_authority(self):
        self.profile()
        self.assertEqual(observations.select_observation_adapters(ROOT,'RW-CAND-130'),[])
        records,audit=self.derive({'observations':[{'name':'fake','evidence_kind':'PUBLIC_BEHAVIOR','status':'PASS'}]})
        self.assertFalse(records)
        self.assertEqual(audit[0]['status'],'SCHEMA_MISMATCH')

    def test_public_invocation_context_preserves_explicit_owner_and_scope(self):
        profile=self.profile()[0]
        evidence=profile['producer_context']
        self.assertIn('kopf.adopt(resource, owner=parent)',evidence['excerpt'])
        self.assertIn("('cluster-child',owner(None)",evidence['excerpt'])
        self.assertEqual(evidence['interpretation'],'Public invocation context only; no expected result or edit permission')

    def test_output_limit_and_failed_scenario_reject_observation(self):
        value={'observations':[{'name':'cluster-child','exception':None,'resource':{'kind':'Namespace','metadata':{'name':'x'}}}]}
        for kwargs in [{'limited':True},{'successful':False}]:
            with self.subTest(kwargs=kwargs):
                records,audit=self.derive(value,**kwargs)
                self.assertFalse(records)
                self.assertEqual(audit[0]['status'],'EXECUTION_INCOMPLETE')

    def test_provider_observations_are_behavior_not_deployment_claims(self):
        original=Path('outputs/deepseek-optimized-new-development-v1-20261020/cases/R4-PROVIDER-HELM-408/rounds/0/public/raw/public-production-oci-http-transport.stdout')
        value=json.loads(original.read_text());records,audit=self.derive(value,case='R4-PROVIDER-HELM-408')
        self.assertEqual(audit[0]['status'],'COMPLETE')
        self.assertEqual(len(records),len(value['observations']))
        for p,m in records.items():
            self.assertEqual(m['evidence_kind'],'PUBLIC_BEHAVIOR')
            self.assertTrue(m['structured_complete']);json.loads(p.read_text())
            self.assertIn('local',m['origin'].lower())

    def test_malformed_json_is_a_parse_error_not_behavior_fail(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);root=Path(temp.name);raw=root/'raw';raw.mkdir()
        profile=self.profile();scenario=profile[0]['scenario']
        (raw/'public_execution.json').write_text(json.dumps({'scenarios':[{'scenario':scenario,'exit_code':0}]}))
        (raw/(scenario+'.stdout')).write_text('{broken')
        records,audit=observations.derive_observations(raw,root/'derived',profile)
        self.assertFalse(records);self.assertEqual(audit[0]['status'],'PARSE_ERROR')
