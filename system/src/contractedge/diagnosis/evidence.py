from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from contractedge.contracts.models import MethodInput
from contractedge.retrieval.candidates import CandidateSet


def build_evidence_catalog(method_input: MethodInput, candidates: CandidateSet | Mapping[str, Any],
                           snapshot_id: str) -> list[dict[str, Any]]:
    selected = list(candidates.candidates) if hasattr(candidates, "candidates") else list(candidates.get("candidates", ()))
    records = []
    artifacts = {a['id']: a for a in method_input.declared_artifacts}
    for item in selected:
        record = {"id": f"SRC-{item['id']}", "snapshot_id": snapshot_id,
                  "kind": "SOURCE_SPAN", "locator": {"path": str(item['path']), **dict(item['locator'])},
                  "content_digest": str(item['content_digest']), "excerpt": str(item['text']),
                  "origin": f"selected candidate {item['id']}"}
        if item.get('origin_kind') == 'DECLARED_ARTIFACT':
            artifact = artifacts[item['artifact_id']]
            start = sum(len(line) for line in artifact['content'].splitlines(keepends=True)[:item['locator']['start_line'] - 1])
            end = start + len(item['text'])
            if artifact['content'][start:end] != item['text']:
                raise ValueError('Declared artifact unit does not match its exact source span')
            record.update(kind='DECLARED_ARTIFACT', locator={
                'path': f"cases/{method_input.case_id}/{artifact['path']}",
                'artifact_id': artifact['id'], 'artifact_content_digest': artifact['content_digest'],
                'start_char': start, 'end_char': end}, origin='Immutable public input span')
        records.append(record)
    if hasattr(candidates, "supplemental_evidence"):
        records.extend(dict(item) for item in candidates.supplemental_evidence)
    records.extend(dict(item) for item in method_input.public_evidence)
    return records


def bind_evidence_ids(evidence_refs: Any, catalog: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(evidence_refs, list):
        raise ValueError("evidence_refs must be an array")
    by_id = {str(item["id"]): dict(item) for item in catalog}
    bound = []
    seen = set()
    for index, reference in enumerate(evidence_refs):
        if isinstance(reference, Mapping):
            # Fixture and legacy responses already carry the canonical records.
            record_id = reference.get("id")
            if record_id not in by_id or dict(reference) != by_id[record_id]:
                raise ValueError(f"evidence_refs[{index}] is not an exact catalog record")
            record = dict(reference)
        elif isinstance(reference, str):
            if reference not in by_id:
                raise ValueError(f"evidence_refs[{index}] cites unknown catalog ID {reference!r}")
            record = by_id[reference]
        else:
            raise ValueError(f"evidence_refs[{index}] must be a catalog ID")
        if record["id"] in seen:
            raise ValueError(f"evidence_refs[{index}] duplicates catalog ID {record['id']!r}")
        seen.add(record["id"])
        bound.append(record)
    return bound


def field_contract_contexts(units, query, *, max_records=4, max_bytes=8000):
    """Exact local Go JSON-field comments; lexical context, not API truth.

    Highlight relevant source already selected. Never introduce a repository path,
    executable observation, absence claim or edit permission from a comment.
    """
    terms = {word.casefold() for word in re.findall(r'[A-Za-z_][A-Za-z0-9_]*', query)}
    output, seen = [], set()
    for unit in units:
        text = unit.get('text', '')
        if not unit.get('path','').endswith('.go') or not re.match(r'\s*type\s+\w+\s+struct\s*\{', text):
            continue
        lines = text.splitlines(keepends=True)
        for index, line in enumerate(lines):
            match = re.match(r'^\s*([A-Za-z_]\w*)\s+[^`{}]+`[^`]*json:"([^",]+)[^`]*`', line)
            if not match or not ({match[1].casefold(), match[2].casefold()} & terms):
                continue
            start = index
            while start > 0 and lines[start-1].lstrip().startswith('//'):
                start -= 1
            if start == index:
                continue
            excerpt = ''.join(lines[start:index+1])
            first = unit['locator']['start_line'] + start
            identity = (unit['snapshot_id'], unit['path'], first, excerpt)
            if identity in seen:
                continue
            record = dict(source_evidence_ref='SRC-'+unit['id'], candidate_id=unit['id'],
                snapshot_id=unit['snapshot_id'], source_content_digest=unit['content_digest'],
                field_name=match[1], json_key=match[2],
                locator=dict(path=unit['path'], start_line=first, end_line=unit['locator']['start_line']+index),
                excerpt=excerpt, excerpt_digest=hashlib.sha256(excerpt.encode()).hexdigest(),
                interpretation='Exact selected-source comment/field window; applicability requires review. Not runtime evidence, causal proof or edit permission.')
            if len(output)>=max_records or len(json.dumps(output+[record],ensure_ascii=False).encode())>max_bytes:
                continue
            output.append(record);seen.add(identity)
    return output


def reader_reference_contexts(units, *, max_records=4, max_bytes=8000):
    """Highlight a simple local callback read and later variable assignments.

    Uses only exact selected text. This is a lexical locator, never a proof of
    callback invocation, alias identity, readiness, causality or edit authority.
    Complex closures and known shadowing are deliberately not resolved.
    """
    from contractedge.retrieval.gap_delivery import _go_code
    output, seen = [], set()
    pattern = r'(?m)^[ \t]*(\w+)\s*:=\s*func\s*\(([^\n]*)\)[^\n{]*\{\s*return\s+(\w+)\.(\w+)\([^\n{}]*\)\s*\}'
    for unit in units:
        text=unit.get('text','')
        if (not unit.get('path','').endswith('.go') or unit.get('truncated')
                or not unit.get('id') or not unit.get('snapshot_id')
                or hashlib.sha256(text.encode()).hexdigest()!=unit.get('content_digest')
                or not re.match(r'\s*func\s+\w+\s*\(',text)):
            continue
        code=_go_code(text); lines=text.splitlines(keepends=True)
        def window(start,end):
            first=code.count('\n',0,start); last=code.count('\n',0,max(start,end-1))
            excerpt=''.join(lines[first:last+1]); base=unit['locator']['start_line']
            return dict(locator=dict(path=unit['path'],start_line=base+first,end_line=base+last),
                        excerpt=excerpt,excerpt_digest=hashlib.sha256(excerpt.encode()).hexdigest())
        for match in re.finditer(pattern,code):
            callback, parameters, variable, method=match.groups()
            if (re.search(r'\b'+re.escape(variable)+r'\b',parameters)
                    or any(variable in re.findall(r'\w+', lhs) for lhs in
                           re.findall(r'\b(\w+(?:\s*,\s*\w+)*)\s*:=', code))
                    # Grouped declarations can introduce a different binding.
                    # Skip conservatively; this locator does not resolve scope.
                    or any(re.search(r'\b'+re.escape(variable)+r'\b', group)
                           for group in re.findall(r'\bvar\s*\(([^)]*)', code))
                    or re.search(r'\bfunc\s*\([^)]*\b'+re.escape(variable)+r'\b',code)
                    or len(re.findall(r'(?m)^[ \t]*var\s+'+re.escape(variable)+r'\s+',code))!=1
                    or not re.search(r'(?m)^[ \t]*var\s+'+re.escape(variable)+r'\s+[^\n]+',code[:match.start()])):
                continue
            writes=list(re.finditer(r'(?m)^[ \t]*'+re.escape(variable)+r'\s*=(?!=)[^\n]+',code[match.end():]))
            if not writes:continue
            identity=(unit['snapshot_id'],unit['path'],unit['locator']['start_line'],match.start())
            if identity in seen:continue
            row=dict(source_evidence_ref='SRC-'+unit['id'],candidate_id=unit['id'],snapshot_id=unit['snapshot_id'],
                source_content_digest=unit['content_digest'],variable=variable,callback=callback,receiver_method=method,
                reader=window(match.start(),match.end()),
                writes=[window(match.end()+m.start(),match.end()+m.end()) for m in writes[:4]],
                interpretation='Exact selected-source read/reassignment windows; review shared-reference publication. '
                    'Not verified invocation, same-scope alias proof, runtime readiness, causality or edit permission.')
            if len(output)>=max_records or len(json.dumps(output+[row],ensure_ascii=False).encode())>max_bytes:continue
            output.append(row);seen.add(identity)
    return output
