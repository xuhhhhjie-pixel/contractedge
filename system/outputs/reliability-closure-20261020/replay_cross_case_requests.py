"""Offline initial-request regression on fixed, already exposed public cases."""
import json
import tempfile
from pathlib import Path

from contractedge.cli import _token_counter
from contractedge.dataset.benchmark import method_input_from_task
from contractedge.execution.public import BenchmarkPublicRunner
from contractedge.orchestration.repair import select_observations
from contractedge.orchestration.runner import run_front_half


class Captured(BaseException):
    pass


class NoNetwork:
    def complete_json(self, request):
        self.request = request
        raise Captured()


def main():
    root=Path.cwd();out=root/'outputs/reliability-closure-20261020'
    benchmark=Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')
    cases=['RW-CAND-130','R3-KPT-4673','R3-KOPF-726','R4-PROVIDER-HELM-408','R4-ACK-CLOUDWATCH-2985']
    config=json.loads((root/'configs/deepseek-reliability-closure-v4-budget80.json').read_text())
    counter=_token_counter(config['tokenizer']);rows=[]
    for case in cases:
        saved=out/f'runs/delivery-0.6.8-A-{case}-0-candidate/cases/{case}'
        method,projection=method_input_from_task(benchmark/'public',case)
        trace=json.loads((saved/'rounds/0/front.trace.json').read_text())
        cfg={**config,'model':trace['run_context']['model_config'],'run_id':trace['run_id'],
             'public_execution_capabilities':trace['run_context']['retrieval_config']['public_execution_capabilities']}
        report=json.loads((saved/'broken-public/observation.json').read_text())
        observations=tuple(select_observations(report['observations'],method['requirement_text'],counter,
            config['max_public_evidence_tokens']))
        provider=NoNetwork()
        with tempfile.TemporaryDirectory(prefix='ce-public-replay-') as temp:
            workspace=Path(temp)/'workspace';log=Path(temp)/'prepare'
            runner=BenchmarkPublicRunner(benchmark/'public',benchmark/'tools',lock_root=root/'outputs/runtime-locks')
            record=runner.prepare(case,workspace,log)
            source=Path(record['source_root'])
            try:
                front=run_front_half(method,source if method['source_root']=='.' else source.parent,
                    provider,cfg,counter,public_observations=observations)
            except Captured:
                request=provider.request
                payload=json.loads(request.messages[1]['content'])
                rows.append(dict(case_id=case,status='REQUEST_CAPTURED',
                    input_bound=sum(counter(m['content']) for m in request.messages),
                    input_limit=cfg['initial_diagnosis_context_tokens'],
                    lifecycle_units=[r['locator']['path'] for r in payload['evidence_catalog']
                        if r['id'].startswith('SRC-CU-HELPER-')],
                    gap_delivery=request.metadata.get('gap_delivery',{}).get('status')))
            else:
                raise AssertionError((case,front.status,front.errors))
    (out/'cross-case-candidate-request-replay.json').write_text(json.dumps(dict(api_calls=0,
        scope='Existing exposed development members; request generation and budgets only, no repair result or holdout evaluation',
        records=rows),ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(rows,ensure_ascii=False))


if __name__=='__main__':main()
