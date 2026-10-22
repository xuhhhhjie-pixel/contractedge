"""Gap-directed source locations and final structured-message delivery receipts.

Roles here are query endpoints, not automatically proven semantic/causal roles.
"""
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from contractedge.retrieval.candidates import _terms
from contractedge.source.parsers import parse_source_units, _unit


def locate_gap_roles(snapshot, gaps: list[dict], limits: dict):
    root=Path(snapshot.source_root).resolve(); inventory=snapshot.file_digests
    max_files=limits.get('max_new_files',4);max_bytes=limits.get('max_bytes',4*1024*1024)
    seeds=limits.get('seed_units',[]); output=[];used=0;scanned={};audit=[]
    shared_queries=set()
    for g in gaps:
        shared_queries |= _terms(g.get('locator_hint',{}).get('search_term',''))
    for gap in gaps:
        hint=gap.get('locator_hint',{});path=hint.get('path','')
        if path not in inventory or PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts:continue
        if not (hint.get('symbol') or hint.get('search_term')):continue
        if path not in scanned:
            if len(scanned)>=max_files:continue
            target=root/path
            if target.is_symlink() or target.stat().st_size>1024*1024 or used+target.stat().st_size>max_bytes:continue
            text=target.read_text(errors='replace');used+=len(text.encode());scanned[path]=text
        text=scanned[path];query=hint.get('symbol') or hint.get('search_term','')
        terms=_terms(query)|(_terms(gap.get('summary',''))&shared_queries)
        all_terms=terms|_terms(gap.get('summary',''))
        # The exact gap already justified this bounded file read. Preserve its
        # enclosing declaration, including callbacks near the end, instead of
        # reintroducing a prefix-only span as the supposedly complete endpoint.
        units=parse_source_units(path,text,snapshot.snapshot_id,max_lines=768,max_chars=32000)
        def match_tier(unit):
            symbol=str(unit.get('symbol',''))
            if hint.get('symbol'):
                if symbol==query:return 2
                if '.' in query:
                    owner,method=query.rsplit('.',1)
                    if symbol==method and re.search(r'\bfunc\s*\(\s*\w+\s+\*?'+re.escape(owner)+r'\s*\)\s*'+re.escape(method)+r'\s*\(',unit['text']):return 2
            return int(bool(re.search(r'(?<!\w)'+re.escape(query)+r'(?!\w)',unit['text'])))
        scored=[]
        for u in units:
            symbol=str(u.get('symbol',''));body=u['text']
            score=(30*len(terms&_terms(symbol))+5*len(terms&_terms(body))
                +len(all_terms&_terms(body)))
            if score and (terms&_terms(body) or terms&_terms(symbol)):
                scored.append((score,u))
        # Full declarations can be long. Locate finite public-query windows in
        # their tails rather than claiming a clipped prefix is a complete body.
        lines=text.splitlines(keepends=True);offsets=[0]
        for line in lines:offsets.append(offsets[-1]+len(line))
        for i,line in enumerate(lines):
            if not terms&_terms(line):continue
            if any(u['locator']['start_line']<=i+1<=u['locator']['end_line'] for _,u in scored):continue
            start=max(0,i-24);end=min(len(lines),start+80)
            u=_unit(snapshot.snapshot_id,path,text,offsets[start],offsets[end],'BOUNDED_TEXT_FALLBACK',
                    unit_kind='SOURCE_TEXT',max_chars=18000,extra={'query_window':True})
            scored.append((5*len(terms&_terms(u['text']))+len(all_terms&_terms(u['text'])),u))
        scored.sort(key=lambda pair:(-match_tier(pair[1]),
            -int(bool(pair[1].get('symbol')) and not pair[1].get('truncated')),
            -pair[0],pair[1]['locator']['start_line']))
        if not any(match_tier(u) for _,u in scored):
            literal=re.search(r'(?<!\w)'+re.escape(query)+r'(?!\w)',text)
            audit.append(dict(gap_id=gap['id'],path=path,query=query,
                status='EXACT_QUERY_OUTSIDE_RETURNED_SPANS' if literal else 'EXACT_QUERY_NOT_FOUND_IN_FILE',
                scope='THIS_BOUNDED_FILE_ONLY; not repository or external API absence'))
        for index,(_,unit) in enumerate(scored[:2]):
            if match_tier(unit)<match_tier(scored[0][1]):continue
            first=scored[0][1]
            if (index and first.get('symbol') and not unit.get('symbol')
                    and unit['locator']['start_line']<=first['locator']['end_line']
                    and first['locator']['start_line']<=unit['locator']['end_line']):continue
            if index and scored[index][0]<.7*scored[0][0]:continue
            item={**unit,'gap_role_keys':[gap['id']+(':anchor' if index==0 else ':context')],
                  'evidence_gap_refs':[gap['id']],'anchor_priority':4,
                  'scope_extension':'EVIDENCE_GAP_EXACT_PATH','hop_distance':0,
                  'association_status':'EXACT_PUBLIC_GAP_QUERY; lexical location only, not causality or edit grant'}
            prior=next((u for u in output if u['id']==item['id']),None)
            if prior is None:output.append(item)
            else:
                prior['gap_role_keys']=sorted(set(prior['gap_role_keys'])|set(item['gap_role_keys']))
                prior['evidence_gap_refs']=sorted(set(prior.get('evidence_gap_refs',[]))|set(item['evidence_gap_refs']))
            audit.append(dict(gap_id=gap['id'],unit_id=item['id'],path=path,
                status='FOUND' if match_tier(unit) else 'LEXICAL_CONTEXT',
                match_basis={2:'EXACT_SYMBOL',1:'EXACT_LITERAL_REFERENCE',0:'LEXICAL_FALLBACK'}[match_tier(unit)]))
    # A reader such as x.Groups() may live behind x := localpkg.NewReader().
    # Follow only an explicit local import + constructor + requested method.
    # This is a lexical locator, not a dispatch/alias or causal proof.
    module_path=root/'go.mod'
    module_match=(re.search(r'(?m)^module\s+(\S+)',module_path.read_text()[:64000])
                  if module_path.is_file() and not module_path.is_symlink() else None)
    if module_match:
        local_module=module_match[1];packages={}
        # Locator queries, not broad prose such as "serves", determine which
        # receiver method justifies spending a scarce file slot.
        # A locator may name only the reload callback while its explicit gap
        # also names the observed collection. Retain those named endpoint
        # tokens; generic constructors/verbs still cannot spend a file slot.
        generic={'new','get','set','find','show','read','write','call','path','source','state','method',
                 'function','public','production','api','provider','config','configuration','valid',
                 'supplied','evidence','code','serves','serve','error','string','main','close','open'}
        query_terms=shared_queries | (set().union(*(_terms(g.get('summary','')) for g in gaps))-generic)
        requested_receivers={}
        for gap in gaps:
            query=gap.get('locator_hint',{}).get('search_term','')
            for receiver,method in re.findall(r'\b(\w+)\.(\w+)\b',query):
                requested_receivers.setdefault(receiver,set()).add(method)
        for text in list(scanned.values()):
            imports={}
            for match in re.finditer(r'(?m)^\s*(?:import\s+)?(?:(\w+)\s+)?"([^"\n]+)"',text):
                imported=match[2]
                if imported.startswith(local_module+'/'):
                    imports[match[1] or imported.rsplit('/',1)[-1]]=imported[len(local_module)+1:]
            for match in re.finditer(r'\b(\w+(?:\s*,\s*\w+)*)\s*(?::=|=)\s*(\w+)\.(\w+)\s*\(',text):
                variables,alias,constructor=match.groups()
                if alias not in imports:continue
                variables=[v.strip() for v in variables.split(',')]
                methods={m[1] for variable in variables for m in re.finditer(r'\b'+re.escape(variable)+r'\.(\w+)\s*\(',text)
                         if _terms(m[1])&query_terms}
                requested=set().union(*(requested_receivers.get(v,set()) for v in variables))
                methods|=requested
                if methods:
                    entry=packages.setdefault(imports[alias],dict(methods=set(),constructors=set(),async_methods=set(),query_priority=0))
                    entry['methods'].update(methods);entry['constructors'].add(constructor)
                    entry['query_priority']=max(entry['query_priority'],int(bool(requested)))
                    entry['async_methods'].update(m[1] for variable in variables for m in re.finditer(
                        r'\bgo\s+'+re.escape(variable)+r'\.(\w+)\s*\(',text))
        for directory,entry in sorted(packages.items(),key=lambda p:(-p[1]['query_priority'],-len(p[1]['methods']),p[0])):
            names=entry['methods']|entry['constructors']
            paths=[p for p in inventory if str(PurePosixPath(p).parent)==directory
                   and p.endswith('.go') and not p.endswith('_test.go') and p not in scanned]
            paths.sort(key=lambda p:(-len(_terms(PurePosixPath(p).stem)&_terms(' '.join(names))),p))
            for path in paths:
                if len(scanned)>=max_files:break
                target=root/path
                if target.is_symlink() or target.stat().st_size>1024*1024 or used+target.stat().st_size>max_bytes:continue
                text=target.read_text(errors='replace');used+=len(text.encode());scanned[path]=text
                matches=[u for u in parse_source_units(path,text,snapshot.snapshot_id,max_lines=768,max_chars=32000)
                         if u.get('symbol') in entry['methods']|entry['async_methods']]
                matches.sort(key=lambda u:(u.get('symbol') not in entry['methods'],u['locator']['start_line']))
                # Leave one of the six role slots for a named public endpoint.
                for unit in matches[:max(0,5-len(output))]:
                    item={**unit,'gap_role_keys':['CONNECTOR:'+unit['id']], 'anchor_priority':4,
                          'required_for_gap_delivery':True,
                          'hop_distance':2,'association_status':'LOCAL_IMPORT_CONSTRUCTOR_METHOD_HINT; no verified dispatch, alias, causal edge or edit grant'}
                    output.append(item);audit.append(dict(unit_id=item['id'],path=path,status='FOUND',basis=item['association_status']))
                if matches:break
    # An already scoped public handler can otherwise lose all slots to reload
    # machinery or test helpers. This is finite token matching in known source,
    # not endpoint discovery across the repository or a verified read edge.
    named_terms=set().union(*(_terms(g.get('summary','')) for g in gaps))|shared_queries
    generic_names={'get','set','new','api','handler','handle','request','response','read','write','public','method','function','state'}
    endpoints=[]
    for unit in [*limits.get('seed_declarations',[]),*seeds]:
        path=unit.get('path','');parts=PurePosixPath(path).parts;symbol=unit.get('symbol','')
        if (not symbol or not path.endswith('.go') or path.endswith('_test.go')
                or any(p in {'test','tests','vendor','generated','restapi','client'} for p in parts)
                or 'api' not in parts):continue
        score=len((_terms(symbol)-generic_names)&named_terms)
        if score:endpoints.append((score,unit))
    endpoints.sort(key=lambda pair:(-pair[0],pair[1]['path'],pair[1]['locator']['start_line']))
    for _,unit in endpoints:
        if len(output)>=6:break
        path=unit['path']
        if any(u['path']==path and u.get('symbol')==unit.get('symbol') for u in output):continue
        if path not in scanned:
            if len(scanned)>=max_files:
                audit.append(dict(path=path,symbol=unit['symbol'],status='NAMED_ENDPOINT_FILE_BUDGET'));continue
            target=root/path
            if (path not in inventory or target.is_symlink() or target.stat().st_size>1024*1024
                    or used+target.stat().st_size>max_bytes):continue
            text=target.read_text(errors='replace');used+=len(text.encode());scanned[path]=text
        expanded=[u for u in parse_source_units(path,scanned[path],snapshot.snapshot_id,max_lines=768,max_chars=32000)
                  if u.get('symbol')==unit['symbol']]
        if not expanded:continue
        item={**expanded[0],'gap_role_keys':['NAMED_PUBLIC_ENDPOINT:'+expanded[0]['id']],
              'required_for_gap_delivery':True,
              'anchor_priority':4,'hop_distance':2,
              'association_status':'EXISTING_SCOPE_NAMED_PUBLIC_ENDPOINT; lexical hint, not verified dispatch or causality'}
        output.append(item);audit.append(dict(unit_id=item['id'],path=path,status='FOUND',basis=item['association_status']))
        break
    # Two finite lexical-reference hops restricted to existing seed evidence.
    # A name match is explicitly INFERRED and never relabelled as a call edge.
    frontier=list(output);seen={u['id'] for u in output}
    # Restore only existing, directly referenced long seed declarations. The
    # initial 80-line prefix may omit a callback or state replacement in its tail.
    restored_seeds=[]
    for path in dict.fromkeys(u['path'] for u in seeds if u.get('truncated')):
        if len(scanned)>=max_files or path not in inventory:break
        target=root/path
        if target.is_symlink() or target.stat().st_size>1024*1024 or used+target.stat().st_size>max_bytes:continue
        text=target.read_text(errors='replace');used+=len(text.encode());scanned[path]=text
        restored_seeds.extend(parse_source_units(path,text,snapshot.snapshot_id,max_lines=768,max_chars=32000))
    seeds=[*restored_seeds,*seeds]
    for depth in range(1,3):
        symbols={s for u in frontier for s in u.get('declared_symbols',[])}
        references={s for u in frontier for s in u.get('references',[])}
        ambiguous={s for s in {'New','Error','String','main','Close','Open','Run'} if not _terms(s)&shared_queries}
        symbols-=ambiguous;references-=ambiguous
        neighbors=[u for u in seeds if u['id'] not in seen and
            (symbols & set(u.get('references',[])) or references & set(u.get('declared_symbols',[])))]
        neighbors.sort(key=lambda u:(int(u.get('truncated',False)),
            -len(_terms(' '.join(g.get('summary','') for g in gaps))&_terms(u.get('text',''))),u['path']))
        frontier=[]
        for unit in neighbors[:max(0,6-len(output))]:
            if any(u['path']==unit['path'] and u.get('symbol')==unit.get('symbol')
                   and not u.get('truncated') for u in output):continue
            item={**unit,'gap_role_keys':['CONNECTOR:'+unit['id']], 'anchor_priority':4,
                'hop_distance':depth,'association_status':'LEXICAL_REFERENCE_HINT; no verified dispatch or causal edge'}
            output.append(item);frontier.append(item);seen.add(item['id'])
    return output,dict(inspected_files=len(scanned),inspected_bytes=used,records=audit,
        role_basis='Task-specific requested endpoints plus bounded lexical connectors',
        truncated=len(scanned)>=max_files or used>=max_bytes)


def audit_gap_delivery(gaps: list[dict], selected: list[dict], serialized_request: str):
    value=json.loads(serialized_request); catalog={}
    def collect(item):
        if isinstance(item,dict):
            if isinstance(item.get('role'),str) and item['role'] in {'user','system','assistant'} and isinstance(item.get('content'),str):
                try:collect(json.loads(item['content']))
                except ValueError:pass
            for record in item.get('evidence_catalog',[]):catalog[record['id']]=record
            for v in item.values():collect(v)
        elif isinstance(item,list):
            for v in item:collect(v)
    collect(value);rows=[]
    for unit in selected:
        if not unit.get('gap_role_keys'):continue
        r=catalog.get('SRC-'+unit['id'],{})
        delivered=(r.get('excerpt')==unit['text'] and r.get('content_digest')==unit['content_digest']
            and hashlib.sha256(unit['text'].encode()).hexdigest()==unit['content_digest'])
        rows.append(dict(unit_id=unit['id'],roles=unit['gap_role_keys'],selected=True,delivered=delivered))
    required={i for gap in gaps for i in gap.get('required_unit_ids',[])}
    delivered_ids={r['unit_id'] for r in rows if r['delivered']}
    return dict(status='DELIVERED' if all(r['delivered'] for r in rows) and required<=delivered_ids else 'INCOMPLETE',records=rows,
        missing_required_ids=sorted(required-delivered_ids),
        interpretation='Exact endpoint excerpts delivered; does not prove causal relevance or resolve gaps')
