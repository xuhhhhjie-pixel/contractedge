"""Bounded lexical lookup of directly called Go helpers in the public module."""
import hashlib
import re
from pathlib import Path, PurePosixPath

from contractedge.source.parsers import parse_source_units


def local_go_helper_support(snapshot, targets, operations, *, max_bytes=24000):
    """Read-only implementation context, never a causal claim or edit grant."""
    root = Path(snapshot.source_root)
    if 'go.mod' not in snapshot.file_digests:
        return [], []
    module = re.search(r'(?m)^module\s+(\S+)', (root/'go.mod').read_text())
    if not module:
        return [], []
    module = module[1]; queries = set(); audit = []; result = []
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
    target_paths = {t['path'] for t in targets}
    files, scanned_bytes, included_bytes = 0, 0, 0
    for package in sorted({p for p,_ in queries}):
        symbols = {s for p,s in queries if p == package}
        paths = [p for p in sorted(snapshot.file_digests) if str(PurePosixPath(p).parent) == package
                 and p.endswith('.go') and not p.endswith('_test.go') and p not in target_paths]
        for path in paths:
            if files >= 128 or scanned_bytes >= 4*1024*1024 or len(result) >= 8:
                return result, audit
            candidate = root/path
            size = candidate.stat().st_size
            if scanned_bytes + size > 4*1024*1024:
                audit.append({'path':path,'result':'HELPER_SCAN_BYTE_LIMIT'}); continue
            files += 1; scanned_bytes += size
            text = candidate.read_text(errors='replace')
            lines = text.splitlines(keepends=True)
            for unit in parse_source_units(path,text,snapshot.snapshot_id):
                if unit.get('symbol') not in symbols or unit.get('truncated'):
                    continue
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
                    'origin':'Direct qualified call in accepted target; local Go import lexical lookup; read-only, not a verified causal edge or edit permission'}
                result.append(record)
                audit.append({'id':record['id'],'path':path,'symbol':unit['symbol'],'result':'INCLUDED_READ_ONLY_HELPER'})
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
