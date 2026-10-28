"""Bind one closed known HTTP402 continuation; never run or retry a model."""
import argparse,json,sys
from pathlib import Path
sys.dont_write_bytecode=True
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'src'),str(Path(__file__).resolve().parent)]
from fixed_panel import read,sha,digest,verify_panel,ENVIRONMENT_KEYS,predecessor_rows
from run_fixed_panel import arguments,exclusive
from run_isolated_method import execution_identity


def ref(p):return {'path':str(Path(p).resolve()),'sha256':sha(p)}

def build(old_path,continuation,run_root):
    old_path=Path(old_path).resolve();root=Path(continuation).resolve();old=verify_panel(old_path)
    if old.get('predecessor'):raise ValueError('Original predecessor already amended')
    if read(Path(old['output_root'])/'acceptance-state.json')['status']!='A_OTHER_SLOTS_CLOSED_ACK_INFRASTRUCTURE_PENDING':
        raise ValueError('Original independent A controller is not durably closed')
    recovered=root/'selection-replay';result=read(recovered/'result.json')
    if result.get('status')!='CLOSED_DETERMINISTIC_RECOVERY':raise ValueError('Controlled repair has no final result')
    found=list((recovered/'method').glob('.isolated-*/receipt.json'))
    if len(found)!=1:raise ValueError('Continuation receipt is ambiguous')
    receipt=read(found[0]);supp=receipt['identity']['controlled_continuation'];case=Path(receipt['identity']['output_path']);failed_state=next((root/'repair').glob('.isolated-*'));failed_case=root/'repair'/receipt['case_id']
    original=read(root/'registration.json');original_audit=Path(old['variants']['candidate']['root'])/'outputs/alert-repeat-closure-20261028/V068_ACK_PROVIDER_STOP_AUDIT.json'
    audit=read(original_audit);slot=next(s for s in old['slots'] if s['slot_id']==audit['stopped_slot'])
    od=Path(old['output_root'])/'methods'/slot['slot_id'];orcs=list(od.glob('.isolated-*/receipt.json'))
    if len(orcs)!=1:raise ValueError('Original receipt is ambiguous')
    op=orcs[0];oc=od/slot['case_id'];scripts=Path(__file__).resolve().parent;system=scripts.parent
    panel={**old,'output_root':str(Path(run_root).resolve()),'harness_files':{str(p.resolve()):sha(p) for p in sorted(scripts.glob('*.py'))}}
    panel['harness_files'].update({str(p.resolve()):sha(p) for p in sorted((system/'src').rglob('*')) if p.is_file() and p.suffix in {'.py','.json'}})
    # Keep the original logical panel_id and all46 slot identities; only the
    # common parent authority and output directory receive this amendment.
    virtual=Path(run_root)/'panel.json';i=execution_identity(arguments(panel,panel['slots'][0],virtual,Path('NO_CREDENTIAL_READ')))
    panel['execution_environment']={key:digest(i[key]) for key in ENVIRONMENT_KEYS}
    a=dict(schema_version='ce.fixed_panel_provider_stop_amendment.v1',interpretation_policy='ce.explicit_http402_ready_prefix.v1',
        panel=ref(old_path),records=ref(Path(old['output_root'])/'records.json'),policy_registration=ref(root/'financial-policy-registration.json'),
        worker_files_before=read(op)['identity']['harness_files'],worker_files_after=i['harness_files'],
        original_receipt=ref(op),original_trace=ref(oc/'repair.trace.json'),original_calls=ref(op.parent/'parent-model-calls.json'),
        combined_calls=ref(root/'combined-model-calls.json'),ready=ref(root/'ready.trace.json'),queue_state=ref(Path(old['output_root'])/'queue'/slot['slot_id']/'state.json'),
        continuation_receipt=ref(found[0]),continuation_trace=ref(case/'repair.trace.json'),new_calls=ref(found[0].parent/'parent-model-calls.json'),
        imported_ready=ref(case/'initial-front.trace.json'),continuation_score=ref(recovered/'independent/result.json') if result.get('patch') else None,
        failed_selection_receipt=ref(failed_state/'receipt.json'),failed_selection_trace=ref(failed_case/'repair.trace.json'),received_calls=ref(failed_state/'parent-model-calls.json'),resource_calls=ref(recovered/'resource-calls.json'),projection=ref(recovered/'projection.json'),effective_config=ref(found[0].parent/'effective-config.json'),failed_trial_patch=ref(failed_case/'rounds/0/candidate.patch'),failed_trial_patch_info=ref(failed_case/'rounds/0/patch.json'),
        supplementary_code=[ref(Path('/home/xhj/contractedge-alert-closure-20261028/system/.superpowers/sdd/2026-10-28-behavior-plan-closure')/name) for name in ['recover_ack_selection_isolated.py','ack402_projection_child.py']])
    panel['predecessor']=a
    # Revalidate every inherited raw record and every continuation outcome;
    # never select or require a positive result when constructing the panel.
    rows=predecessor_rows(panel)
    return panel,rows

def main():
    ap=argparse.ArgumentParser()
    for k in ['old-panel','continuation-root','run-root']:ap.add_argument('--'+k,type=Path,required=True)
    ap.add_argument('--write',action='store_true');args=ap.parse_args()
    panel,rows=build(args.old_panel,args.continuation_root,args.run_root)
    if args.write:
        qualification=Path(__file__).resolve().parents[2]/'qualification.json'
        if read(qualification).get('status')!='PASS':raise ValueError('Parent qualification is required before publication')
        panel['parent_qualification']=ref(qualification)
        panel['harness_files'][str(qualification)]=sha(qualification)
        if args.run_root.exists():raise ValueError('Amended output already exists; do not overwrite')
        args.run_root.mkdir();exclusive(args.run_root/'panel.json',panel);verify_panel(args.run_root/'panel.json');exclusive(args.run_root/'records.json',rows)
        for row in rows:(args.run_root/'queue'/row['slot_id']).mkdir(parents=True,exist_ok=True)
        exclusive(args.run_root/'amendment.json',{'status':'BOUND_SAME_SOURCE_ADMINISTRATIVE_CONTINUATION','slots':len(panel['slots']),'inherited_rows':len(rows),'source_sha256':panel['variants']['candidate']['source_sha256'],'panel_sha256':sha(args.run_root/'panel.json'),'method_calls_added':0,'original_failures_preserved':True,'original_uninterrupted_wall_claim':False,'formal_ready':False})
    print(json.dumps({'status':'PASS_FINANCIAL_CONTINUATION_BINDING','rows':len(rows),'effect_outcomes':[(r['case_id'],r['variant'],r['status']) for r in rows],'new_model_calls':0,'formal_ready':False}))

if __name__=='__main__':main()
