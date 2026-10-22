import json
import unittest
from contractedge.orchestration.repair import select_observations


class ObservationSelectionTests(unittest.TestCase):
    def test_broken_target_jobs_are_not_crowded_out_by_a_verbose_working_deployment(self):
        def record(identifier,kind,name,fields):
            body=json.dumps({'resource':{'kind':kind,'name':name,'pod_spec_fields':fields}})
            return {'id':identifier,'locator':{'path':f'raw/trigger.pod-spec-{identifier}.json','start_byte':0},'excerpt':body}
        deployment=record('D','Deployment','ct-cluster-gateway',
            {'nodeSelector':'global tls secret control','tolerations':'global tls secret control','affinity':'global tls secret control'})
        create=record('J1','Job','ct-cluster-gateway-tls-secret-create',{})
        patch=record('J2','Job','ct-cluster-gateway-tls-secret-patch',{})
        budget=sum(len(r['excerpt']) for r in [deployment,create])
        selected=select_observations([deployment,create,patch],
            'cluster-gateway TLS secret create and patch Jobs must consume global nodeSelector tolerations affinity control',len,budget)
        self.assertEqual({r['id'] for r in selected},{'J1','J2'})


def test_registered_complete_objects_are_selected_before_untyped_repeated_logs():
    from contractedge.orchestration.repair import select_observations
    raw={'id':'RAW','excerpt':'namespace owner child '*20,'evidence_kind':'EXECUTION_ONLY','locator':{'path':'raw/stdout','start_byte':0}}
    typed={'id':'OBJECT','excerpt':'{"resource":{"kind":"Namespace","metadata":{"name":"child"}}}',
        'structured_complete':True,'adapter_id':'public-object-adoption-v1','evidence_kind':'PUBLIC_STRUCTURE',
        'locator':{'path':'structured/object.json','start_byte':0}}
    selected=select_observations([raw,typed],'namespace owner child',len,max_tokens=450)
    assert selected[0]['id']=='OBJECT'
