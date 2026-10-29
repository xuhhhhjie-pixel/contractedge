"""Read-only structural verification of the independent 150-case package.

This check does not execute candidates or substitute for retained behavioral
evidence. --checksums additionally verifies a frozen SHA256SUMS file.
"""
from collections import Counter,defaultdict
from pathlib import Path
import argparse
import hashlib
import json
import platform
import yaml

EXPECTED={'R1':30,'R2':30,'R3':30,'R4':30,'R5':30}

def verify(root,checksums=False,digest_cache=None):
    cache={} if digest_cache is None else digest_cache
    def sha(p):
        info=p.stat();key=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
        if key not in cache:
            h=hashlib.sha256()
            with p.open('rb') as stream:
                for block in iter(lambda:stream.read(8*1024**2),b''):h.update(block)
            after=p.stat()
            if key!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):
                raise ValueError('File changed while hashing: '+str(p))
            cache[key]=h.hexdigest()
        return cache[key]
    def confined(base,name):
        p=(base/name).resolve(strict=True)
        if not p.is_relative_to(root):raise ValueError('Reference escapes release: '+name)
        return p
    def rows(p):return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
    public=rows(root/'public/manifest.jsonl');evaluation=rows(root/'evaluation/manifest.jsonl')
    assert len(public)==len(evaluation)==150
    pub={r['case_id']:r for r in public};ev={r['case_id']:r for r in evaluation}
    calibration_by_case={r['case_id']:r for r in rows(root/'evidence/calibration/index.jsonl')}
    assert len(pub)==len(ev)==150 and set(pub)==set(ev)
    assert Counter(r['primary_class'] for r in public)==Counter(r['primary_class'] for r in evaluation)==EXPECTED
    verified=[];launch_gaps=[];gt_class_conflicts=[]
    for cid,r in ev.items():
        pr=pub[cid];assert pr['primary_class']==r['primary_class']
        task=confined(root/'public',pr['task_path']);mp=task/'manifest.json'
        assert sha(mp)==pr['task_manifest_sha256']
        m=json.loads(mp.read_text());assert m['case_id']==cid and m['source_sha256']==pr['source_sha256']
        for name in m['files']:assert sha(confined(task,name))==m['file_sha256'][name],(cid,name)
        assert sha(task/'source.tar.gz')==pr['source_sha256']
        t=yaml.safe_load((task/'task.yaml').read_text())
        req=t.get('requirement')
        if not req and t.get('requirement_file'):req=confined(task,t['requirement_file']).read_text()
        assert isinstance(req,str) and req.strip(), ('Missing requirement',cid)
        if t.get('public_self_check_runner'):
            tool=confined(root,t['public_self_check_runner']);pin=json.loads((tool.parent/'manifest.json').read_text())
            assert pin['case_id']==cid and pin['entry']==tool.name
            for name,digest in pin['files_sha256'].items():assert sha(confined(tool.parent,name))==digest
            for name,digest in pin.get('shared_tools_sha256',{}).items():assert sha(confined(root/'tools',name))==digest
        elif not (task/'environment.json').is_file():launch_gaps.append(cid)
        else:
            env=json.loads((task/'environment.json').read_text())
            for mount in env.get('public_mounts',[]):
                assert mount['archive'] in m['files'] and m['file_sha256'][mount['archive']]==mount['sha256']
        adapter=confined(root/'evaluation',r['adapter']);assert sha(adapter)==r['adapter_sha256']
        if r.get('calibration_report'):
            expected_report=r.get('calibration_report_sha256') or calibration_by_case[cid]['report']['sha256']
            assert sha(confined(root/'evaluation',r['calibration_report']))==expected_report
        ad=json.loads(adapter.read_text())
        source=ad.get('broken_sha256',ad.get('source_sha256',r.get('public_source_sha256')))
        assert source==pr['source_sha256'], ('Public/evaluator source mismatch',cid)
        if r.get('runner_entry'):
            entry_path=confined(root/'evaluation',r['runner_entry']);assert sha(entry_path)==r['runner_entry_sha256']
            entry=json.loads(entry_path.read_text());assert entry['case_id']==cid
            for name,digest in entry['immutable_files_sha256'].items():assert sha(confined(entry_path.parent,name))==digest
        else:
            for path,digest in [(ad['broken_archive'],ad['broken_sha256']),(ad['evaluator_archive'],ad['evaluator_sha256'])]:
                assert sha(confined(adapter.parent,path))==digest
            for mount in ad.get('public_mounts',[]):assert sha(confined(adapter.parent,mount['archive']))==mount['sha256']
        gt=confined(root/'evaluation',r['gt']);assert sha(gt)==r['gt_sha256']
        annotations=yaml.safe_load(gt.read_text())
        if annotations.get('primary_class') and annotations['primary_class']!=r['primary_class']:
            gt_class_conflicts.append({'case_id':cid,'manifest':r['primary_class'],'gt':annotations['primary_class']})
        directory=root/r['primary_class']/'cases'/cid
        assert directory.is_dir() and (directory/'method_input').resolve()==task
        verified.append(cid)
    partition=rows(root/'partition/member_groups.jsonl');reserves=rows(root/'partition/r1_reserve_groups.jsonl')
    assert len(partition)==150 and {r['case_id'] for r in partition}==set(ev)
    assert len(reserves)==27 and not set(ev)&{r['case_id'] for r in reserves}
    repos=defaultdict(set);groups=defaultdict(set)
    for r in partition+reserves:repos[r['repository']].add(r['candidate_split']);groups[r['group_id']].add(r['candidate_split'])
    assert all(len(v)==1 for v in repos.values()) and all(len(v)==1 for v in groups.values())
    calibration=rows(root/'evidence/calibration/index.jsonl')
    assert len(calibration)==150 and {r['case_id'] for r in calibration}==set(ev)
    for record in calibration:
        assert record['adapter_sha256']==ev[record['case_id']]['adapter_sha256']
        for item in record.get('records',[]):assert sha(confined(root,item['path']))==item['sha256']
        if record.get('report'):
            item=record['report'];assert sha(confined(root,item['path']))==item['sha256']
    runtime=json.loads((root/'runtime/manifest.json').read_text())
    for image in runtime['images']:assert sha(confined(root/'runtime',image['archive']))==image['archive_sha256']
    lines=0
    if checksums:
        for line in (root/'SHA256SUMS').read_text().splitlines():
            digest,name=line.split('  ',1);assert sha(confined(root,name))==digest,name;lines+=1
    assert not gt_class_conflicts,gt_class_conflicts
    report={'status':'150_STRUCTURE_AND_IDENTITIES_VERIFIED' if not launch_gaps else '150_PUBLIC_LAUNCH_ENTRIES_PENDING',
        'cases':len(verified),'by_class':EXPECTED,'private_gt_bound':150,'public_requirements_present':150,
        'missing_public_launch_entries':launch_gaps,'gt_class_conflicts':gt_class_conflicts,
        'repository_split_conflicts':[],'family_split_conflicts':[],
        'runtime_images_hash_verified':len(runtime['images']),'frozen_checksum_files_verified':lines,
        'public_manifest_sha256':sha(root/'public/manifest.jsonl'),'evaluation_manifest_sha256':sha(root/'evaluation/manifest.jsonl'),
        'unique_inodes_hashed':len(cache),'candidate_runs_added':0,'historical_replays_added':0,
        'scope':'Whole selected150 metadata, actual file identities, public entry presence and pinned dependencies. Does not claim all-case fresh-machine execution or model baseline performance.'}
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--checksums',action='store_true');p.add_argument('--report',type=Path);a=p.parse_args()
    report=verify(a.root.resolve(strict=True),a.checksums)
    if a.report:a.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))
    return 0 if not report['missing_public_launch_entries'] else 2

if __name__=='__main__':raise SystemExit(main())
