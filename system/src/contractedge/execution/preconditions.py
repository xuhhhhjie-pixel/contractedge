"""Finite syntax checks for publicly evidenced annotation-identity merge APIs.

No general control-flow or alias proof. Unsupported guards remain UNKNOWN;
even a syntactic match still needs behavior verification on the candidate.
"""
import itertools
import re
from contractedge.source.parsers import _balanced_end, parse_source_units


def _condition_at(text, offset):
    conditions=[]
    for m in re.finditer(r'\b(?:else(?:\s+if\s+[^\n{]+)?|for\s+[^\n{]*|switch\s+[^\n{]*|select\s*)\s*\{',text):
        end=_balanced_end(text,m.end()-1)
        if m.end()-1<offset and end and offset<end:
            conditions.append('UNSUPPORTED_CONTROL_FLOW@'+str(m.end()-1))
    for m in re.finditer(r'\bif\s+([^\n{]+)\{',text):
        opening=m.end()-1;end=_balanced_end(text,opening)
        if opening<offset and end and offset<end:
            conditions.append(m[1].strip())
    return conditions


def _dnf(conditions):
    result=[set()]
    if len(conditions)>16:return None
    for condition in conditions:
        alternatives=[]
        for term in condition.split('||'):
            atoms={re.sub(r'\s+','',v) for v in term.split('&&')}
            if not atoms or any(not re.fullmatch(r'(?:len\([\w.]+\)>0|[\w.\[\]]+!="")',a) for a in atoms):
                return None
            alternatives.append(atoms)
        if len(result)*len(alternatives)>64:return None
        result=[a|b for a,b in itertools.product(result,alternatives)]
    return result


def _covers(required, initialization):
    # Calls within the same lexical control block can compare their additional
    # local guards. This establishes no loop/alias/early-exit proof. Different
    # loops and opposite branches retain distinct unsupported identities.
    shared={c for c in required if c.startswith('UNSUPPORTED_CONTROL_FLOW@')} & set(initialization)
    required=[c for c in required if c not in shared]
    initialization=[c for c in initialization if c not in shared]
    req=_dnf(required);init=_dnf(initialization)
    if req is None or init is None:return 'UNKNOWN'
    return 'CHECKED_SYNTAX' if all(any(i<=r for i in init) for r in req) else 'CONFLICT'


def validate_helper_preconditions(plan: dict, changed_source: dict, helper_evidence: list[dict]) -> dict:
    merges=[];initializers={};cleaners={}
    for evidence in helper_evidence:
        text=evidence.get('excerpt','');name=re.search(r'\bfunc\s+(\w+)\s*\(',text)
        if not name:continue
        key=re.search(r'GetAnnotations\(\)\[(\w+)\]',text)
        if key and 'append(' in text:
            merges.append((name[1],key[1],evidence.get('id')))
        key=re.search(r'\bSetAnnotation\(\s*(\w+)',text)
        if key:initializers.setdefault(key[1],[]).append(name[1])
        key=re.search(r'\bClearAnnotation\(\s*(\w+)',text)
        if key:cleaners.setdefault(key[1],[]).append(name[1])
    checks=[]
    for path,text in changed_source.items():
        if not path.endswith('.go'):continue
        for function in parse_source_units(path,text,'PRECONDITION_ANALYSIS',max_lines=10000,max_chars=1024*1024):
            if function.get('kind')!='FUNCTION_OR_TYPE':continue
            body=function['text']
            for name,key,eid in merges:
                pattern=r'\b(\w+)\.'+re.escape(name)+r'\(\s*[^,()]+,\s*[^,()]+,\s*([\w.]+)\s*\)'
                for call in re.finditer(pattern,body):
                    alias,resource=call[1],call[2];required=_condition_at(body,call.start())
                    common=dict(path=path,symbol=function.get('symbol'),call=name,resource=resource,
                                helper_evidence_ref=eid,call_line=function['locator']['start_line']+body[:call.start()].count('\n'))
                    for label,names,before in [('RESOURCE_ID_INITIALIZATION',initializers.get(key,[]),True),
                                               ('RESOURCE_ID_CLEANUP',cleaners.get(key,[]),False)]:
                        candidates=[]
                        for helper in names:
                            regex=r'\b'+re.escape(alias)+r'\.'+re.escape(helper)+r'\(\s*'+re.escape(resource)+r'\s*\)'
                            for m in re.finditer(regex,body):
                                if (before and m.start()<call.start()) or (not before and m.start()>call.start()):
                                    candidates.append(_covers(required,_condition_at(body,m.start())))
                        status=('CHECKED_SYNTAX' if 'CHECKED_SYNTAX' in candidates else
                                'UNKNOWN' if 'UNKNOWN' in candidates else 'CONFLICT')
                        checks.append({**common,'precondition':label,'annotation_key':key,'status':status,
                            'reason':'Initializer/cleanup must cover every supported call guard; aliasing, loops and early exits are not proven'})
    return dict(schema_version='ce.helper_preconditions.v1',checks=checks,
        interpretation='Finite registered syntax relation only; behavior probes remain required, no general control-flow or alias proof')
