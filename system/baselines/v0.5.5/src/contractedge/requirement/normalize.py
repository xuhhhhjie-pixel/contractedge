from __future__ import annotations

import re
import hashlib
import json
from typing import Any, Mapping


_CONDITION = re.compile(r"^\s*((?:when|if|unless|while|after|before|once)\b[^,.;\n]*)(?:[,;:]|\s+)", re.IGNORECASE)


def normalize_requirement(payload: Mapping[str, Any], public_observable: str | None = None) -> dict[str, Any]:
    """Create a lossless coarse RequirementSpec; no behavior is inferred or split."""
    raw = payload.get("requirement_text")
    if not isinstance(raw, str):
        raw = payload.get("text")
    if not isinstance(raw, str):
        raw = ""
    stripped = raw.strip()
    result: dict[str, Any] = {
        "schema_version": "ce.requirement_spec.v1_1",
        "raw_text": raw,
        "intent_summary": stripped,
        "outcomes": [],
        "constraints": [],
        "trigger": None,
        "unknowns": [],
        "status": "UNDER_SPECIFIED" if not stripped else "READY",
    }
    if not stripped:
        result["unknowns"].append("The public requirement text is empty or missing.")
        return result
    condition = None
    clauses = []
    for match_clause in re.finditer(r'[^\n]+', raw):
        line = match_clause.group()
        if re.match(r'^\s*#{1,6}\s', line):
            continue
        for part in re.finditer(r'.+?(?:[.!?](?=\s|$)|$)', line):
            text = part.group().strip()
            if not text:
                continue
            start = match_clause.start()+part.start()+len(part.group())-len(part.group().lstrip())
            end = start+len(text)
            clauses.append({'id':'Q'+str(len(clauses)+1),'text':text,'source_ref':f'requirement_text:{start}:{end}'})
    result['clauses'] = clauses
    match = _CONDITION.match(clauses[0]['text'] if clauses else stripped)
    if match:
        condition = match.group(1).strip()
        result["trigger"] = condition
    result["outcomes"].append({
        "id": "O1", "statement": raw, "condition": condition,
        "source_refs": list(dict.fromkeys([f"requirement_text:0:{len(raw)}", *[c["source_ref"] for c in clauses]])), "support": "EXPLICIT",
    })
    if public_observable and not isinstance(public_observable, str):
        result["unknowns"].append("Public observable text was not a string and was ignored.")
    return result


def bind_obligation_sources(payload, requirement):
    """Runner-owned identity from exact raw requirement anchors, not prose IDs.

    Distinct predicates in one clause need distinct quotes. Changed anchors do
    not silently match old identities; prior constraints remain retained.
    """
    raw = requirement.get('raw_text', '')
    clauses = {c['source_ref']:c for c in requirement.get('clauses', [])}
    required = requirement.get('traceability_required', False)
    seen = set()
    for atom in payload.get('obligation_atoms', []):
        quote = atom.get('source_quote')
        if not quote and not required:
            atom.pop('stable_obligation_key',None)
            continue
        if not isinstance(quote,str) or not quote.strip():
            raise ValueError('OBLIGATION_QUOTE_REQUIRED: quote the precise public predicate')
        refs = atom.get('source_refs', [])
        if required and (not refs or any(r not in clauses for r in refs)):
            raise ValueError('OBLIGATION_CLAUSE_REQUIRED: select exact clause refs, not the entire requirement')
        excerpts = []
        for ref in refs:
            try:
                prefix,a,b=ref.split(':');a,b=int(a),int(b)
            except (ValueError,AttributeError):
                raise ValueError('OBLIGATION_SOURCE_INVALID')
            if prefix!='requirement_text' or not 0<=a<b<=len(raw):
                raise ValueError('OBLIGATION_SOURCE_INVALID')
            excerpts.append(raw[a:b])
        if not any(quote in excerpt for excerpt in excerpts):
            raise ValueError('OBLIGATION_QUOTE_MISMATCH: source_quote must occur in its cited source interval')
        scope_quote=atom.get('scope_quote','')
        if scope_quote and (not isinstance(scope_quote,str) or not any(scope_quote in excerpt for excerpt in excerpts)):
            raise ValueError('OBLIGATION_SCOPE_QUOTE_MISMATCH: quote the affected object from cited original text')
        body=[hashlib.sha256(raw.encode()).hexdigest(),atom['outcome_id'],sorted(refs),quote,scope_quote]
        key='OB-'+hashlib.sha256(json.dumps(body,ensure_ascii=False).encode()).hexdigest()[:24]
        # Shared source anchors are conservative groups, never semantic equivalence.
        # Retained statements/bindings are conjunctively preserved by feedback.
        seen.add(key);atom['stable_obligation_key']=key
    if required:
        rows=payload.get('clause_treatments', [])
        if {r.get('source_ref') for r in rows}!=set(clauses) or len(rows)!=len(clauses):
            raise ValueError('REQUIREMENT_CLAUSE_UNACCOUNTED: classify each public clause exactly once')
        atoms={a['id']:a for a in payload.get('obligation_atoms', [])}
        if any(r.get('disposition')=='UNCERTAIN' for r in rows) and payload.get('status')=='READY':
            raise ValueError('CLAUSE_UNRESOLVED: READY cannot hide an uncertain requirement clause')
        for row in rows:
            refs=row.get('obligation_refs', [])
            if row.get('disposition')=='OBLIGATION' and not refs:
                raise ValueError('CLAUSE_OBLIGATION_MISSING')
            if any(a not in atoms or row['source_ref'] not in atoms[a]['source_refs'] for a in refs):
                raise ValueError('CLAUSE_OBLIGATION_MISMATCH')
            if row.get('disposition')!='OBLIGATION' and refs:
                raise ValueError('CLAUSE_DISPOSITION_MISMATCH')
    return payload


def bind_plan_obligations(plan, diagnosis):
    atoms={a['id']:a for a in diagnosis.get('obligation_atoms', [])}
    for kind in ('postconditions','preserve_constraints','open_obligations'):
        for item in plan.get(kind, []):
            item.pop('stable_obligation_keys',None)
            keys=sorted({atoms[a]['stable_obligation_key'] for a in item.get('obligation_refs', [])
                         if a in atoms and atoms[a].get('stable_obligation_key')})
            if keys:
                item['stable_obligation_keys']=keys
    return plan
