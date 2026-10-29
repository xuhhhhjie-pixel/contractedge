"""Compare the recorded initial scope to current scoring on identical public source."""
import hashlib,json
from pathlib import Path
from contractedge.source.scopes import resolve_scope
from contractedge.source.snapshot import compute_snapshot
BASE=Path('/home/xhj/contractedge-diagnostic-source049-v2-20261026/methods/RW-CERTMANAGER-9116/RW-CERTMANAGER-9116')
OUT=Path(__file__).parent
read=lambda p:json.loads(Path(p).read_text())
f=read(BASE/'rounds/0/front.trace.json');method=read(BASE/'rounds/0/front.method_input.json')
source=BASE/'workspace/source'/method['source_root'];snapshot=compute_snapshot(source)
assert snapshot.snapshot_id==f['snapshot_id']
hints=[dict(kind=r['kind'],value=r['query'],origin_ref=r['origin_ref']) for r in f['scope_resolution']['search_audit']]
new=resolve_scope(source,snapshot,hints)
path='pkg/controller/certificates/issuing/issuing_controller.go'
assert path not in f['scope_resolution']['seed_paths']
assert path in new.seed_paths
old_a=f['scope_resolution']['search_audit'][0];new_a=new.search_audit[0]
assert new_a['indexed_files_total']<=2000 and new_a['indexed_bytes_total']<=64*1024*1024
result=dict(status='INITIAL_SCOPE_CONTENT_SCORE_CEILING_REMOVED',snapshot_id=snapshot.snapshot_id,
 old_production_seeded=False,new_production_seeded=True,new_production_rank=new.seed_paths.index(path),
 old_path_only_score=sum(t in path.casefold() for t in old_a['query_terms']),new_combined_score=new_a['matched_scores'][path],
 old_indexed_files=old_a['indexed_files_total'],new_indexed_files=new_a['indexed_files_total'],
 new_indexed_bytes=new_a['indexed_bytes_total'],max_selected_scope_paths=64,
 new_model_calls=0,new_grader_calls=0,formal_ready=False,
 interpretation='Scope and initial file availability only; complete mechanism and repair quality require further verification')
(OUT/'CERT_INITIAL_SCOPE_CHECKPOINT.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
