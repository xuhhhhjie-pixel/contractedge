"""Parent-only frozen candidate scoring; completed checkpoints finalize without reruns.

Never mount this program or its outputs in a method process. ``run`` persists
the grader outcome; ``finalize`` only validates and aggregates that exact result.
An interrupted dispatch without a completed checkpoint requires operator audit.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from run_isolated_method import write_json


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def binding(receipt_path,benchmark,output):
    receipt_path=Path(receipt_path).resolve(strict=True);receipt=read(receipt_path)
    benchmark=Path(benchmark).resolve(strict=True);output=Path(output).resolve()
    case=Path(receipt['identity']['output_path']).resolve(strict=True)
    state=Path(receipt['state_root']).resolve(strict=True)
    if (receipt.get('status')!='METHOD_COMPLETE_PENDING_INDEPENDENT_SCORING'
            or receipt.get('method_returncode')!=0
            or receipt.get('cleanup',{}).get('status')!='OWNED_CONTAINERS_CLOSED'
            or receipt.get('terminal_validation',{}).get('classification')!='METHOD_RESULT'
            or output.is_relative_to(case) or output.is_relative_to(state)
            or output.is_relative_to(benchmark) or case.is_relative_to(output) or state.is_relative_to(output)):
        raise ValueError('Independent scoring requires a closed, verified method and separate output')
    journals=sorted((state/'method-journal').glob('*.json'))
    records=[read(p) for p in journals]
    if not records or any(r.get('status')!='DONE' for r in records):
        raise ValueError('Unresolved method request; independent scoring is not admitted')
    trace_path=case/'repair.trace.json';trace=read(trace_path);candidate=trace.get('candidate')
    if trace.get('case_id')!=receipt['case_id'] or not candidate:
        raise ValueError('No final candidate for this verified method result')
    patch=Path(candidate['patch_path']).resolve(strict=True)
    if (not patch.is_relative_to(case) or sha(patch)!=candidate.get('patch_sha256')
            or patch.stat().st_size!=candidate.get('patch_bytes')
            or not receipt['terminal_validation'].get('candidate_verified')
            or not any(r.get('op')=='public.export' and r.get('result')==candidate for r in records)):
        raise ValueError('Final patch is not bound to the trusted export')
    entry=benchmark/'RELEASE_ENTRY.json';manifest=benchmark/'evaluation/manifest.jsonl'
    if sha(manifest)!=read(entry)['evaluation_manifest_sha256']:
        raise ValueError('Frozen independent evaluator identity mismatch')
    for name,digest in receipt['identity'].get('public_release_files',{}).items():
        if sha(benchmark/name)!=digest:raise ValueError('Public release changed after method execution')
    tools=benchmark/'tools';tool=tools/'score_case.py'
    tool_files={p.relative_to(tools).as_posix():sha(p) for p in sorted(tools.rglob('*'))
                if p.is_file() and p.suffix in {'.py','.json'}}
    expected=receipt['identity'].get('public_tool_files')
    if expected is not None and tool_files!=expected:raise ValueError('Frozen scoring tools changed')
    return {'case_id':receipt['case_id'],'receipt_sha256':sha(receipt_path),
        'trace_sha256':sha(trace_path),'patch_sha256':sha(patch),'patch_path':str(patch),
        'final_snapshot_id':trace.get('final_snapshot_id'),'evaluation_manifest_sha256':sha(manifest),
        'public_release_sha256':sha(entry),'public_tool_files':tool_files,
        'scoring_program_sha256':sha(__file__),'interpreter_sha256':sha(sys.executable)}


def run(receipt,benchmark,output,*,timeout=7200,phase='all'):
    if phase not in {'all','run','finalize'}:raise ValueError('Invalid scoring phase')
    output=Path(output).resolve();benchmark=Path(benchmark).resolve(strict=True)
    identity=binding(receipt,benchmark,output)
    checkpoint=output/'checkpoint.json'
    if phase!='finalize':
        try:output.mkdir(parents=True,exist_ok=False)
        except FileExistsError:raise ValueError('Scoring output already exists; finalize completed checkpoint or audit uncertainty') from None
        write_json(checkpoint,{'status':'SCORING_STARTED','identity':identity})
        command=[sys.executable,str(benchmark/'tools/score_case.py'),'--evaluation-root',str(benchmark/'evaluation'),
                 '--case-id',identity['case_id'],'--patch',identity['patch_path'],'--output',str(output/'native')]
        timed_out=False
        with (output/'grader.log').open('wb') as log:
            try:completed=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
            except subprocess.TimeoutExpired:timed_out=True;code=None
            else:code=completed.returncode
        native=output/'native/score.json'
        write_json(checkpoint,{'status':'TOOL_FINISHED','identity':identity,'returncode':code,
            'timed_out':timed_out,'score_sha256':sha(native) if native.is_file() else None,
            'grader_log_sha256':sha(output/'grader.log')})
        if phase=='run':return read(checkpoint)
    saved=read(checkpoint)
    if saved.get('status')!='TOOL_FINISHED' or saved.get('identity')!=identity:
        raise ValueError('Scoring outcome unknown or binding changed; never auto-rerun')
    native=output/'native/score.json'
    digest=sha(native) if native.is_file() else None
    if digest!=saved.get('score_sha256') or sha(output/'grader.log')!=saved.get('grader_log_sha256'):
        raise ValueError('Independent scoring output changed after completion')
    value=read(native) if digest else {}
    if value and (value.get('case_id')!=identity['case_id'] or value.get('patch_sha256')!=identity['patch_sha256']):
        raise ValueError('Independent score case/patch mismatch')
    status=value.get('status','SCORER_ERROR')
    if saved['timed_out']:status='SCORER_TIMEOUT'
    elif saved['returncode']!=0:status='SCORER_ERROR'
    result={'schema_version':'ce.isolated_independent_score.v1','case_id':identity['case_id'],
        'status':status,'native_score_status':value.get('status'),
        'identity':identity,'checkpoint_sha256':sha(checkpoint),'score_sha256':digest,
        'candidate_selection_frozen':True,'model_calls_added':0,'score_feedback_to_method':False,
        'counts_in_46_slot_panel':False,'formal_ready':False}
    target=output/'result.json'
    if target.exists():
        if read(target)!=result:raise ValueError('Existing final score differs; no overwrite')
    else:write_json(target,result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['receipt','benchmark-root','output']:parser.add_argument('--'+name,required=True)
    parser.add_argument('--phase',choices=['all','run','finalize'],default='all')
    parser.add_argument('--timeout',type=int,default=7200)
    args=parser.parse_args()
    result=run(args.receipt,args.benchmark_root,args.output,timeout=args.timeout,phase=args.phase)
    print(json.dumps({k:result[k] for k in ['case_id','status'] if k in result}))


if __name__=='__main__':main()
