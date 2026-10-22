"""Bounded lexical reminders about result writeback in accepted Go targets.

These are fresh source excerpts, not runtime observations or causal findings.
They focus the existing executor on writeback as well as input selection.
"""
import hashlib
import re

from contractedge.source.parsers import parse_source_units


def writeback_context(targets, operations, snapshot_id, *, max_bytes=12000):
    sites=[];used=0
    for target in targets:
        path,text=target['path'],target.get('content')
        if not path.endswith('.go') or not text:continue
        ranges=[op['target'].get('locator',{}) for op in operations if op['target'].get('path')==path]
        # Preserve character positions and lines while excluding quoted examples.
        masked=re.sub(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|`[^`]*`|\'(?:\\.|[^\'\\])*\'',
                      lambda m:re.sub(r'[^\n]',' ',m[0]),text)
        lines=text.splitlines(keepends=True);code=masked.splitlines(keepends=True)
        for unit in parse_source_units(path,text,snapshot_id,max_lines=10000,max_chars=1024*1024):
            if unit.get('kind')!='FUNCTION_OR_TYPE' or unit.get('truncated'):continue
            start,end=unit['locator']['start_line'],unit['locator']['end_line']
            if not any(type(r.get('start_line')) is int and type(r.get('end_line')) is int
                       and r['start_line']<=end and r['end_line']>=start for r in ranges):continue
            for number in range(start-1,min(end,len(code))):
                # Plain replacement from a result field; do not infer aliases,
                # collection types or helper correctness from this syntax.
                match=re.fullmatch(r'\s*(\w+(?:\.\w+)*)\s*=\s*(\w+(?:\.\w+)+)\s*;?\s*',code[number])
                if not match:continue
                lo,hi=max(start-1,number-10),min(end,number+4)
                excerpt=''.join(lines[lo:hi]);size=len(excerpt.encode())
                if len(sites)>=8 or used+size>max_bytes:continue
                used+=size
                sites.append(dict(snapshot_id=snapshot_id,symbol=unit.get('symbol'),
                    locator=dict(path=path,start_line=lo+1,end_line=hi),
                    assignment=f'{match[1]} = {match[2]}',assignment_line=number+1,
                    excerpt=excerpt,content_digest=hashlib.sha256(excerpt.encode()).hexdigest()))
    return dict(schema_version='ce.collection_writeback_context.v1',sites=sites,
        boundary='Lexical source hints only; not a dataflow proof, behavior verdict, or additional edit permission',
        review_question=('When a function receives a filtered subset, trace its result through EVERY writeback branch. '
            'Check whether whole-state replacement discards excluded inputs that retention obligations require downstream. '
            'Moving input filtering alone does not establish preservation. Read actual merge-helper preconditions, '
            'initialization and cleanup together before changing a writeback guard; comments are not proof. '
            'Use the public failing predicates to verify the resulting behavior.'))
