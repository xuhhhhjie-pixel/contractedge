#!/usr/bin/env python3
"""Zero-cost consumer interchange fixture through the production repair entry.

This runs published synthetic code in host child processes. It supplies no OS
sandbox and has no API-provider option; isolated runtime validation is separate.
"""
import argparse
import hashlib
import json
from pathlib import Path

from contractedge.development import public_demo
from contractedge.execution.consumers.direct import ModifyOnlyConsumer
from contractedge.model.provider import ModelResponse
from contractedge.orchestration.repair import repair_case


class ModifyFixtureProvider(public_demo.SyntheticExampleProvider):
    """Author the alternate response directly, without an intermediate Full edit."""
    def complete_json(self,request):
        if request.prompt_version!=ModifyOnlyConsumer.PROMPT:
            return super().complete_json(request)
        self.executions+=1
        if self.case=='config-binding':
            old,new='name = "default"','name = config["selected_name"]'
        elif self.case=='reload-state':
            old,new='# configuration reload leaves cached name stale','state["name"] = config["selected_name"]'
        elif self.executions==1:
            old,new='primary = "default"','primary = config["selected_name"]'
        else:
            old,new='secondary = "default"','secondary = config["selected_name"]'
        return ModelResponse(json.dumps({'replacements':[
            {'key':'C1','file':'app.py','before':old,'after':new}]}),
            {'provider':'independent-modify-only-fixture','zero_cost':True})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case',choices=sorted(public_demo.CASES),default='config-binding')
    parser.add_argument('--consumer',choices=['topomen','modify-only'],default='modify-only')
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=False)
    public=public_demo.freeze_example(args.case,output,runtime='host')
    runner=public_demo.PublicExampleRunner(public,args.case)
    config={**public_demo.default_config(args.case),'candidate_guard_enabled':True}
    if args.consumer=='modify-only':
        config['execution_backend']='direct'
        provider=ModifyFixtureProvider(args.case,runner)
        consumer=ModifyOnlyConsumer()
    else:
        provider=public_demo.SyntheticExampleProvider(args.case,runner)
        consumer=None
    identity=public_demo._method_source_identity()
    identity['consumer_entry_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    public_demo.write_json(output/'source.identity.json',identity)
    public_demo.write_json(output/'config.json',config)
    report=repair_case(public,output/'unused-tools',args.case,output/'repair',provider,
        public_demo.count_tokens,config,public_runner=runner,consumer=consumer)
    calls=json.loads((output/'repair/model_calls.json').read_text())
    patch=output/'repair/candidate.patch'
    summary={'status':report['status'],'consumer':args.consumer,'case':args.case,
        'rounds':len(report['rounds']),'zero_cost':True,'boundary':'HOST_SYNTHETIC_ONLY_NO_OS_SANDBOX',
        'source_sha256':identity['digest'],'consumer_entry_sha256':identity['consumer_entry_sha256'],
        'initial_snapshot_id':report['initial_snapshot_id'],'final_snapshot_id':report['final_snapshot_id'],
        'actual_prompt_versions':[call['prompt_version'] for call in calls],
        'patch_sha256':hashlib.sha256(patch.read_bytes()).hexdigest() if patch.exists() else None,
        'formal_ready':False}
    public_demo.write_json(output/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False,sort_keys=True))
    return 0 if report['status']=='PUBLIC_REVIEW_COMPLETE' else 1


if __name__=='__main__':
    raise SystemExit(main())
