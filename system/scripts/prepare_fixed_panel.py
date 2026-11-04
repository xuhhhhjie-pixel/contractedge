"""Prepare exact fixed46 authority without a credential or model dispatch."""
import argparse
from pathlib import Path
import sys

sys.dont_write_bytecode = True
from fixed_panel import read, sha, digest, source_files, build_schedule, verify_panel, ENVIRONMENT_KEYS
from run_fixed_panel import arguments, exclusive
from run_isolated_method import execution_identity


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['original','baseline-reference','candidate-reference','baseline-root','candidate-root',
                 'baseline-config','candidate-config','benchmark-root','seed-root','python','dependency-root',
                 'run-root','output']:
        parser.add_argument('--'+name,required=True,type=Path)
    parser.add_argument('--panel-id',required=True)
    parser.add_argument('--runtime-admission',type=Path)
    parser.add_argument('--compatibility-report',type=Path)
    parser.add_argument('--predecessor-panel',type=Path,
        help='Closed original panel; retain all slots and normalize only the known parent scoring exit-code bug')
    parser.add_argument('--terminal-receipt',type=Path,
        help='Original immutable pre-edit EVIDENCE_GAP receipt, only with predecessor-panel and terminal-score')
    parser.add_argument('--terminal-score',type=Path,
        help='Completed independent result of that same sealed candidate, no method rerun')
    args=parser.parse_args()
    if bool(args.terminal_receipt) != bool(args.terminal_score) or (args.terminal_receipt and not args.predecessor_panel):
        raise ValueError('Terminal correction requires predecessor-panel, receipt and completed score together')
    if args.output.exists():raise ValueError('Panel already exists; never overwrite its identity')
    original=read(args.original);variants={}
    for name,root,config,reference,key in [
        ('baseline',args.baseline_root,args.baseline_config,args.baseline_reference,'baseline_source_files'),
        ('candidate',args.candidate_root,args.candidate_config,args.candidate_reference,'files')]:
        files=source_files(root)
        if files!=read(reference)[key]:raise ValueError('Frozen '+name+' differs from its original source reference')
        variants[name]={'root':str(root.resolve()),'source_files':files,'source_sha256':digest(files),
            'config':str(config.resolve()),'config_sha256':sha(config),
            'source_reference':{'path':str(reference.resolve()),'sha256':sha(reference)}}
    scripts=Path(__file__).resolve().parent;system=scripts.parent;benchmark=args.benchmark_root.resolve()
    harness={str(p.resolve()):sha(p) for p in sorted(scripts.glob('*.py'))}
    harness.update({str(p.resolve()):sha(p) for p in sorted((system/'src').rglob('*'))
                    if p.is_file() and p.suffix in {'.py','.json'}})
    names=['public/manifest.jsonl','partition/member_groups.jsonl','RELEASE_ENTRY.json']
    if (benchmark/'evaluation/manifest.jsonl').is_file():names.append('evaluation/manifest.jsonl')
    configs=[read(args.baseline_config),read(args.candidate_config)]
    modes=[c.get('seed_planning_snapshot','ORIGINAL_BROKEN') for c in configs]
    if len(set(modes))!=1 or modes[0] not in {'ORIGINAL_BROKEN','CURRENT_CANDIDATE'}:
        raise ValueError('Both frozen variants need the same explicit seeded continuation protocol')
    panel={'schema_version':'ce.isolated_fixed_panel.v1','panel_id':args.panel_id,
        'original_registration':{'path':str(args.original.resolve()),'sha256':sha(args.original)},
        'variants':variants,'benchmark_root':str(benchmark),'data_files':{n:sha(benchmark/n) for n in names},
        'seeds':{case:{'path':str((args.seed_root/name).resolve()),'sha256':sha(args.seed_root/name)}
                 for case,name in original['recovery_seed_paths'].items()},
        'harness_files':harness,'output_root':str(args.run_root.resolve()),
        'runtime':{'python':str(args.python.absolute()),'dependency_root':str(args.dependency_root.resolve()),
                   'runtime_admission':str(args.runtime_admission.resolve()) if args.runtime_admission else None},
        'model_call_cap':12,'fresh_edit_cap':2,'formal_ready':False,
        'transient_workspace_policy':'RETIRE_CLOSED_SCORED_PUBLIC_WORKSPACE',
        'continuation_protocol':{'id':'ce.seeded_continuation.v6' if modes[0]=='CURRENT_CANDIDATE' else 'ce.seeded_continuation.v5',
            'planning_snapshot':modes[0],'applies_to':['baseline','candidate'],'historical_results_reused':False},
        'slots':build_schedule(original,args.panel_id)}
    if args.compatibility_report:
        panel['compatibility_report']={'path':str(args.compatibility_report.resolve()),'sha256':sha(args.compatibility_report)}
    identity=execution_identity(arguments(panel,panel['slots'][0],args.output,Path('NO_CREDENTIAL_READ')))
    panel['execution_environment']={key:digest(identity[key]) for key in ENVIRONMENT_KEYS}
    if args.predecessor_panel:
        previous=verify_panel(args.predecessor_panel)
        records_path=Path(previous['output_root'])/'records.json'; rows=read(records_path)
        if not isinstance(rows,list) or not rows:raise ValueError('A closed predecessor prefix is required')
        receipt=read(rows[0]['evidence_files']['receipt']['path'])
        programs=[name for name in previous['harness_files'] if Path(name).name=='score_isolated_method.py']
        if len(programs)!=1:raise ValueError('Original scorer identity is ambiguous')
        panel['predecessor']={'schema_version':'ce.fixed_panel_score_parent_amendment.v1',
            'panel':{'path':str(args.predecessor_panel.resolve()),'sha256':sha(args.predecessor_panel)},
            'records':{'path':str(records_path),'sha256':sha(records_path)},
            'scoring_program':{'path':programs[0],'sha256':previous['harness_files'][programs[0]]},
            'worker_files_before':receipt['identity']['harness_files'],
            'worker_files_after':identity['harness_files'],'interpretation_policy':'ce.frozen_score_exit.v1'}
        if args.terminal_receipt:
            terminal = read(args.terminal_receipt)
            programs = [p for p in previous['harness_files'] if Path(p).name == 'run_isolated_method.py']
            if len(programs) != 1:raise ValueError('Recorded terminal program is ambiguous')
            slot_id = terminal['identity']['attempt_id']
            queue_state = Path(previous['output_root'])/'queue'/slot_id/'state.json'
            panel['predecessor'].update(schema_version='ce.fixed_panel_method_terminal_amendment.v1',
                interpretation_policy='ce.pre_edit_evidence_gap_terminal.v1',
                worker_files_before=terminal['identity']['harness_files'],
                terminal_program={'path':programs[0],'sha256':previous['harness_files'][programs[0]]},
                terminal_receipt={'path':str(args.terminal_receipt.resolve()),'sha256':sha(args.terminal_receipt)},
                terminal_score={'path':str(args.terminal_score.resolve()),'sha256':sha(args.terminal_score)},
                queue_state={'path':str(queue_state),'sha256':sha(queue_state)})
    # Validate before publication so failed preparation cannot create authority.
    import tempfile,json
    with tempfile.TemporaryDirectory(prefix='ce-panel-prepare-') as directory:
        path=Path(directory)/'panel.json';path.write_text(json.dumps(panel))
        verify_panel(path)
    exclusive(args.output,panel)
    print(json.dumps({'panel':str(args.output),'slots':len(panel['slots']),'model_http_calls':0,
                      'source_sha256':{k:v['source_sha256'] for k,v in variants.items()},'formal_ready':False}))


if __name__=='__main__':main()
