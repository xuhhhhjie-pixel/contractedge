"""Bounded public source support for local calls, lifecycle and exceptions."""
import hashlib
import re
from pathlib import Path, PurePosixPath

from contractedge.source.parsers import parse_source_units


def local_go_helper_support(snapshot, targets, operations, *, max_bytes=24000,
                            max_scan_files=128, max_scan_bytes=4*1024*1024):
    """Read-only implementation context, never a causal claim or edit grant."""
    root = Path(snapshot.source_root)
    if 'go.mod' not in snapshot.file_digests:
        return [], []
    module = re.search(r'(?m)^module\s+(\S+)', (root/'go.mod').read_text())
    if not module:
        return [], []
    module = module[1]; queries = set(); audit = []; result = []; async_queries = {}
    for target in targets:
        if not target['path'].endswith('.go') or not target['content']:
            continue
        text = target['content']
        imports = []
        for block in re.findall(r'(?ms)^import\s*\((.*?)\)', text):
            imports.extend(re.findall(r'(?m)^\s*(?:(\w+)\s+)?"([^"\n]+)"', block))
        imports.extend(re.findall(r'(?m)^import\s+(?:(\w+)\s+)?"([^"\n]+)"', text))
        aliases = {(alias or path.rsplit('/', 1)[-1]):path for alias,path in imports
                   if alias not in {'_', '.'} and path.startswith(module+'/')}
        for operation in operations:
            selected = operation['target']
            if selected.get('path') != target['path']:
                continue
            loc = selected.get('locator', {})
            start, end = loc.get('start_line'), loc.get('end_line')
            if type(start) is not int or type(end) is not int:
                continue
            body = '\n'.join(text.splitlines()[max(0,start-1):end])
            for alias,symbol in re.findall(r'\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*\(', body):
                if alias in aliases:
                    queries.add((aliases[alias][len(module)+1:], symbol))
            # Follow only an explicit, unambiguous local construction used by
            # an actual asynchronous receiver call. This is a source locator,
            # never a proof of dispatch, readiness, aliasing or causality.
            code=re.sub(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|`[^`]*`',
                        lambda m:re.sub(r'[^\n]',' ',m[0]),body)
            assignments={}
            for match in re.finditer(r'\b(\w+)\s*(?::=|=)\s*(\w+)\.(\w+)\s*\(',code):
                variable,alias,constructor=match.groups()
                if alias in aliases:assignments.setdefault(variable,set()).add((aliases[alias][len(module)+1:],constructor))
            for variable,assignments_for_variable in assignments.items():
                methods={m[1] for m in re.finditer(r'\bgo\s+'+re.escape(variable)+r'\.(\w+)\s*\(',code)}
                if not methods:continue
                if len(assignments_for_variable)!=1:
                    audit.append(dict(path=target['path'],receiver=variable,result='AMBIGUOUS_LOCAL_CONSTRUCTION'));continue
                writes=re.findall(r'(?<![\w.])'+re.escape(variable)+r'\s*(?::=|=(?!=))',code)
                constructions=re.findall(r'(?<![\w.])'+re.escape(variable)+r'\s*(?::=|=)\s*\w+\.\w+\s*\(',code)
                if len(writes)!=len(constructions):
                    audit.append(dict(path=target['path'],receiver=variable,result='ASYNC_RECEIVER_REASSIGNMENT'));continue
                package,constructor=next(iter(assignments_for_variable))
                entry=async_queries.setdefault(package,{})
                found=entry.setdefault(constructor,dict(async_methods=set(),methods=set()))
                found['async_methods'].update(methods)
                found['methods'].update(m[1] for m in re.finditer(r'\b'+re.escape(variable)+r'\.(\w+)\s*\(',code))
    target_paths = {t['path'] for t in targets}
    files, scanned_bytes, included_bytes = 0, 0, 0
    for package in sorted({p for p,_ in queries},key=lambda p:(p not in async_queries,p)):
        symbols = {s for p,s in queries if p == package}
        async_entry=async_queries.get(package,{})
        paths = [p for p in sorted(snapshot.file_digests) if str(PurePosixPath(p).parent) == package
                 and p.endswith('.go') and not p.endswith('_test.go') and p not in target_paths]
        candidates=[]
        for path in paths:
            if files >= max_scan_files or scanned_bytes >= max_scan_bytes or len(result) >= 8:break
            candidate = root/path
            if candidate.is_symlink():continue
            size = candidate.stat().st_size
            if scanned_bytes + size > max_scan_bytes:
                audit.append({'path':path,'result':'HELPER_SCAN_BYTE_LIMIT'}); continue
            files += 1; scanned_bytes += size
            raw = candidate.read_bytes()
            if hashlib.sha256(raw).hexdigest() != snapshot.file_digests[path]:
                audit.append(dict(path=path, result='SOURCE_CHANGED_AFTER_SNAPSHOT')); continue
            text = raw.decode(errors='replace')
            lines = text.splitlines(keepends=True)
            for unit in parse_source_units(path,text,snapshot.snapshot_id,
                    max_lines=768 if async_entry else 80,max_chars=32000 if async_entry else 16000):
                candidates.append((path,lines,unit))
        receiver_types={}
        for _,_,unit in candidates:
            name=unit.get('symbol')
            if name not in async_entry or unit.get('truncated'):continue
            signature=re.search(r'\bfunc\s+'+re.escape(name)+r'\s*\([\s\S]*?\)\s*\*?(\w+)\s*\{',unit['text'])
            if signature:receiver_types[name]=signature[1]
        for constructor in async_entry:
            if constructor not in receiver_types:
                audit.append(dict(package=package,symbol=constructor,result='ASYNC_RETURN_TYPE_UNRESOLVED'))
        inner_methods=set()
        def priority(unit):
            receiver=re.match(r'\s*func\s*\(\s*\w+\s+\*?(\w+)\s*\)\s*(\w+)\s*\(',unit['text'])
            if receiver:
                if (receiver[1],receiver[2]) in inner_methods:return 1
                for constructor,entry in async_entry.items():
                    if receiver_types.get(constructor)!=receiver[1]:continue
                    if receiver[2] in entry['async_methods']:return 0
                    if receiver[2] in entry['methods']:return 3
            if unit.get('symbol') in receiver_types:return 2
            return 4
        # One additional same-receiver hop inside the async entry, restricted
        # to declarations already scanned. No call graph or transitive scan.
        async_roots=[unit for _,_,unit in candidates if priority(unit)==0]
        for unit in async_roots:
            receiver=re.match(r'\s*func\s*\(\s*(\w+)\s+\*?(\w+)\s*\)\s*(\w+)\s*\(',unit['text'])
            if not receiver:continue
            body=re.sub(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|`[^`]*`',
                        lambda m:re.sub(r'[^\n]',' ',m[0]),unit['text'])
            called=list(dict.fromkeys(re.findall(r'\b'+re.escape(receiver[1])+r'\.(\w+)\s*\(',body)))
            inner_methods.update((receiver[2],name) for name in called[:2] if name!=receiver[3])
        candidates.sort(key=lambda row:(priority(row[2]),row[0],row[2]['locator']['start_line']))
        for path,lines,unit in candidates:
            rank=priority(unit)
            if (rank==4 and unit.get('symbol') not in symbols) or unit.get('truncated'):
                continue
            # Full expansion is reserved for the explicit lifecycle path.
            if rank==4 and unit['locator']['end_line']-unit['locator']['start_line']>=80:continue
            loc = unit['locator']; start = loc['start_line']-1
            # Include adjacent API comments: they often declare preconditions.
            while start > max(0,loc['start_line']-13) and lines[start-1].lstrip().startswith('//'):
                start -= 1
            excerpt = ''.join(lines[start:loc['end_line']])
            size = len(excerpt.encode())
            if included_bytes + size > max_bytes or len(result) >= 8:
                audit.append({'path':path,'symbol':unit['symbol'],'result':'READ_ONLY_SUPPORT_BUDGET'}); continue
            included_bytes += size
            digest = hashlib.sha256(excerpt.encode()).hexdigest()
            record = {'id':'HELPER-'+digest[:16], 'kind':'SOURCE_SPAN', 'snapshot_id':snapshot.snapshot_id,
                'locator':{'path':path,'start_line':start+1,'end_line':loc['end_line']},
                'content_digest':digest, 'excerpt':excerpt,
                'origin':('Explicit local construction and asynchronous receiver call; read-only lexical hint, not readiness or verified dispatch/causality/edit permission'
                          if rank<4 else 'Direct qualified call in accepted target; local Go import lexical lookup; read-only, not a verified causal edge or edit permission')}
            result.append(record)
            audit.append({'id':record['id'],'path':path,'symbol':unit['symbol'],'result':'INCLUDED_READ_ONLY_HELPER',
                          'basis':'LOCAL_CONSTRUCTOR_ASYNC_RECEIVER' if rank<4 else 'DIRECT_LOCAL_IMPORT',
                          'lifecycle_role':('ASYNC_ENTRY' if rank==0 else 'ASYNC_SUPPORT') if rank<4 else None})
    return result, audit


def source_lifecycle_units(snapshot, selected, *, max_files=4, max_records=6):
    """Locate pre-plan lifecycle evidence from fresh selected production callers.

    Reuses the executor's bounded lexical lookup, without requiring an accepted
    plan. These are read-only source candidates, never readiness/causal proofs.
    """
    root = Path(snapshot.source_root)
    targets, operations, audit = {}, [], []
    used = 0
    for seed in selected:
        path = seed.get('path', '')
        if not path.endswith('.go') or path.endswith('_test.go'):
            continue
        if seed.get('snapshot_id') != snapshot.snapshot_id:
            audit.append(dict(path=path, result='STALE_SOURCE_SEED')); continue
        text = seed.get('text', '')
        code = re.sub(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|`[^`]*`', '', text)
        if not re.search(r'\bgo\s+\w+\.\w+\s*\(', code):
            continue
        p = root/path
        if path not in snapshot.file_digests or p.is_symlink() or not p.is_file():
            audit.append(dict(path=path, result='SOURCE_SEED_UNAVAILABLE')); continue
        if path not in targets:
            if len(targets) >= max_files or used+p.stat().st_size > 4*1024*1024:
                audit.append(dict(path=path, result='SOURCE_SEED_BUDGET')); continue
            raw = p.read_bytes(); used += len(raw)
            if hashlib.sha256(raw).hexdigest() != snapshot.file_digests[path]:
                audit.append(dict(path=path, result='SOURCE_CHANGED_AFTER_SNAPSHOT')); continue
            targets[path] = dict(path=path, content=raw.decode(errors='replace'))
        loc = seed['locator']
        exact = ''.join(targets[path]['content'].splitlines(keepends=True)[loc['start_line']-1:loc['end_line']])
        if text != exact or hashlib.sha256(text.encode()).hexdigest() != seed.get('content_digest'):
            audit.append(dict(path=path, result='SOURCE_SEED_BODY_MISMATCH')); continue
        operations.append(dict(target=dict(path=path, locator=loc)))
    if not operations:
        return [], audit
    records, lookup = local_go_helper_support(snapshot, list(targets.values()), operations,
        max_scan_files=max_files, max_scan_bytes=4*1024*1024-used)
    audit.extend(lookup)
    lifecycle_ids = {r['id'] for r in lookup if r.get('basis') == 'LOCAL_CONSTRUCTOR_ASYNC_RECEIVER'}
    entry_ids = {r['id'] for r in lookup if r.get('lifecycle_role') == 'ASYNC_ENTRY'}
    units = []
    for record in records:
        if record['id'] not in lifecycle_ids:
            continue
        if len(units) >= max_records:
            audit.append(dict(path=record['locator']['path'], result='LIFECYCLE_ROLE_BUDGET')); continue
        match = re.search(r'\bfunc\s+(?:\([^)]*\)\s*)?(\w+)\s*\(', record['excerpt'])
        units.append(dict(id='CU-'+record['id'], path=record['locator']['path'],
            locator={k:v for k,v in record['locator'].items() if k!='path'},
            kind='SOURCE_FUNCTION', parser_kind='go', symbol=match[1] if match else '',
            declared_symbols=[match[1]] if match else [], references=[], text=record['excerpt'],
            snapshot_id=snapshot.snapshot_id, content_digest=record['content_digest'], truncated=False,
            anchor_priority=4, gap_role_keys=['PUBLIC_LIFECYCLE:'+record['id']],
            required_for_gap_delivery=True, precondition_review_required=record['id'] in entry_ids,
            association_status=record['origin']))
    if not units:
        audit.append(dict(result='LIFECYCLE_EVIDENCE_UNRESOLVED', paths=sorted(targets)))
    return units, audit


def source_exception_units(snapshot, selected, observations, *, max_files=4,
                           max_records=6, max_bytes=4*1024*1024):
    """Locate literal Python exception producers and at most two local callers.

    Only current, registered operation failures can initiate this read-only
    lookup. A literal raise and a lexical call remain hints, never causal proof.
    Dynamic messages, methods, imports and ambiguous bindings stay unresolved.
    """
    import ast
    from contractedge.diagnosis.operation_consistency import operation_facts

    failures = {(op['exception'].get('type'), op['exception'].get('message'))
        for op in operation_facts(observations)
        if op['snapshot_id'] == snapshot.snapshot_id and op['status'] == 'FAILED'
        and isinstance(op.get('exception'), dict)
        and isinstance(op['exception'].get('type'), str)
        and isinstance(op['exception'].get('message'), str)
        and 8 <= len(op['exception']['message']) <= 600}
    if not failures or max_files <= 0 or max_records <= 0:
        return [], []
    root = Path(snapshot.source_root)
    result, audit, paths = [], [], set()
    used = 0

    def own_nodes(function):
        pending = list(function.body)
        while pending:
            node = pending.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            yield node
            pending.extend(ast.iter_child_nodes(node))

    for seed in selected:
        path = seed.get('path', '')
        if not path.endswith('.py') or path in paths:
            continue
        if (Path(path).name.startswith('test_') or any(p in {'tests', 'test', '.contractedge-deps'}
                                                     for p in PurePosixPath(path).parts)):
            continue
        if seed.get('snapshot_id') != snapshot.snapshot_id:
            audit.append(dict(path=path, result='STALE_SOURCE_SEED')); continue
        target = root/path
        if path not in snapshot.file_digests or target.is_symlink() or not target.is_file():
            audit.append(dict(path=path, result='SOURCE_SEED_UNAVAILABLE')); continue
        if len(paths) >= max_files or used + target.stat().st_size > max_bytes:
            audit.append(dict(path=path, result='EXCEPTION_SCAN_LIMIT')); continue
        raw = target.read_bytes(); used += len(raw)
        if hashlib.sha256(raw).hexdigest() != snapshot.file_digests[path]:
            audit.append(dict(path=path, result='SOURCE_CHANGED_AFTER_SNAPSHOT')); continue
        text = raw.decode(errors='replace'); loc = seed.get('locator', {})
        start, end = loc.get('start_line'), loc.get('end_line')
        if (type(start) is not int or type(end) is not int
                or ''.join(text.splitlines(keepends=True)[start-1:end]) != seed.get('text')
                or hashlib.sha256(seed['text'].encode()).hexdigest() != seed.get('content_digest')):
            audit.append(dict(path=path, result='SOURCE_SEED_BODY_MISMATCH')); continue
        paths.add(path)
        try:
            tree = ast.parse(text)
        except SyntaxError:
            audit.append(dict(path=path, result='PYTHON_SOURCE_UNPARSEABLE')); continue
        declarations = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        counts = {n.name: sum(other.name == n.name for other in declarations) for n in declarations}
        functions = {n.name:n for n in declarations if counts[n.name] == 1}
        nodes = {name:list(own_nodes(fn)) for name, fn in functions.items()}
        producers = []
        for name, body in nodes.items():
            for node in body:
                if not isinstance(node, ast.Raise) or not isinstance(node.exc, ast.Call):
                    continue
                constructor = node.exc.func
                exception_type = constructor.id if isinstance(constructor, ast.Name) else None
                if any((exception_type, arg.value) in failures for arg in node.exc.args
                       if isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                    producers.append(name); break
        roles = {name:0 for name in sorted(producers)}
        for depth in (1, 2):
            previous = {name for name, level in roles.items() if level == depth-1}
            for name, body in nodes.items():
                if name not in roles and any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                                            and n.func.id in previous for n in body):
                    roles[name] = depth
        # Prefer the selected consumer's connection to the producer, then
        # callers of that consumer. Other utilities sharing the exception
        # helper must not displace the actual operation under the same cap.
        focused = {u.get('symbol') for u in selected if u.get('path') == path
                   and u.get('snapshot_id') == snapshot.snapshot_id} & roles.keys()
        for _ in range(2):
            focused |= {n.func.id for name in tuple(focused) for n in nodes[name]
                        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                        and n.func.id in roles}
        important = focused - set(producers)
        upstream = {name for name, body in nodes.items() if name in roles and any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in important for n in body)}
        parsed = {(u.get('symbol'),u['locator']['start_line'],u['locator']['end_line']):u
                  for u in parse_source_units(path, text, snapshot.snapshot_id,
                                              max_lines=768, max_chars=32000)}
        for name, depth in sorted(roles.items(), key=lambda p:(
                0 if p[0] in producers else 1 if p[0] in focused else 2 if p[0] in upstream else 3,
                p[1],functions[p[0]].lineno)):
            if important and name not in set(producers) | focused | upstream:
                continue
            declaration = functions[name]
            start = min([declaration.lineno] + [d.lineno for d in declaration.decorator_list])
            unit = parsed.get((name,start,declaration.end_lineno))
            if unit is None or unit.get('truncated'):
                audit.append(dict(path=path, symbol=name, result='EXCEPTION_SOURCE_BODY_UNAVAILABLE')); continue
            if len(result) >= max_records:
                audit.append(dict(path=path, symbol=name, result='EXCEPTION_SOURCE_ROLE_LIMIT')); continue
            result.append({**unit, 'anchor_priority':4, 'required_for_gap_delivery':True,
                'gap_role_keys':['PUBLIC_EXCEPTION:'+unit['id']],
                'precondition_review_required':depth == 0,
                'association_status':'CURRENT_PUBLIC_EXCEPTION_LITERAL_AND_LOCAL_CALL_HINT; not causal proof or edit permission'})
            audit.append(dict(path=path, symbol=name, unit_id=unit['id'], local_call_hops=depth,
                              result='INCLUDED_EXCEPTION_SOURCE'))
    return result, audit


def local_helper_support(snapshot, targets, operations, *, max_bytes=24000):
    """Shared finite budget for Go and directly imported Python helper bodies."""
    import ast
    result,audit=local_go_helper_support(snapshot,targets,operations,max_bytes=max_bytes)
    remaining=max_bytes-sum(len(r['excerpt'].encode()) for r in result)
    root=Path(snapshot.source_root);queries=set();scanned=used=0
    for target in targets:
        if not target['path'].endswith('.py') or not target['content']:continue
        try:tree=ast.parse(target['content'])
        except SyntaxError:continue
        calls=set()
        for operation in operations:
            if operation['target'].get('path')!=target['path']:continue
            loc=operation['target'].get('locator',{});start,end=loc.get('start_line'),loc.get('end_line')
            if type(start) is not int or type(end) is not int:continue
            for node in ast.walk(tree):
                if isinstance(node,ast.Call) and start<=node.lineno<=end and isinstance(node.func,ast.Name):calls.add(node.func.id)
        for node in tree.body:
            if not isinstance(node,ast.ImportFrom) or node.module is None:continue
            if node.level:
                parts=list(PurePosixPath(target['path']).parent.parts)
                if node.level>len(parts):continue
                base=parts[:len(parts)-node.level+1]
                module='/'.join([*base,*node.module.split('.')])
            else:module=node.module.replace('.','/')
            for name in node.names:
                if (name.asname or name.name) in calls and name.name!='*':queries.add((module+'.py',name.name))
    for path,symbol in sorted(queries):
        if len(result)>=8 or scanned>=128:break
        if path not in snapshot.file_digests or path in {t['path'] for t in targets}:continue
        candidate=root/path
        if candidate.is_symlink():continue
        size=candidate.stat().st_size
        if used+size>4*1024*1024:audit.append({'path':path,'result':'HELPER_SCAN_BYTE_LIMIT'});continue
        scanned+=1;used+=size;text=candidate.read_text(errors='replace')
        for unit in parse_source_units(path,text,snapshot.snapshot_id):
            if unit.get('symbol')!=symbol or unit.get('truncated'):continue
            loc=unit['locator'];excerpt=''.join(text.splitlines(keepends=True)[loc['start_line']-1:loc['end_line']])
            if len(excerpt.encode())>remaining:continue
            digest=hashlib.sha256(excerpt.encode()).hexdigest();remaining-=len(excerpt.encode())
            record={'id':'HELPER-'+digest[:16],'kind':'SOURCE_SPAN','snapshot_id':snapshot.snapshot_id,
                'locator':{'path':path,**loc},'content_digest':digest,'excerpt':excerpt,
                'origin':'Direct imported local Python call in accepted target; read-only, not verified causality or edit permission'}
            result.append(record);audit.append({'id':record['id'],'path':path,'symbol':symbol,'result':'INCLUDED_READ_ONLY_HELPER'})
    return result,audit
