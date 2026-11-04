"""Gap-directed source locations and final structured-message delivery receipts.

Roles here are query endpoints, not automatically proven semantic/causal roles.
"""
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Mapping
from contractedge.retrieval.candidates import _terms, merge_gap_context_optional
from contractedge.source.parsers import parse_source_units, _unit, mask_go_code
from contractedge.contracts.evidence import static_search_content_digest


def _go_code(text, *, keep_strings=False):
    return mask_go_code(text, keep_strings=keep_strings)



def _go_declaration(text):
    """Return a masked simple Go function declaration; unsupported forms abstain."""
    code = _go_code(text)
    match = re.match(r'\s*func\s*(?:\(\s*(?:(\w+)\s+)?\*?(\w+)\s*\)\s*)?(\w+)\s*\(', code)
    return (code, match) if match else (code, None)


def _go_identifier_shadowed(name, signature, body, *, parameter=True):
    """Conservatively reject parameters, assignments and declaration blocks."""
    token = r'\b' + re.escape(name) + r'\b'
    if parameter and re.search(token, signature):
        return True
    lhs = r'(?<![\w.])(?:\w+\s*,\s*)*' + re.escape(name) + r'(?:\s*,\s*\w+)*'
    if re.search(lhs + r'\s*(?::=|=(?!=))', body):
        return True
    # Only declaration names shadow. A type or initializer reference after
    # `var result ...` must not be mistaken for a new binding of that name.
    names = r'([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)'
    for match in re.finditer(r'\b(?:var|const|type)\s+' + names, body):
        if name in re.findall(r'\w+', match[1]):
            return True
    for group in re.finditer(r'\b(?:var|const|type)\s*\(([^)]*)\)', body):
        for match in re.finditer(r'(?:^|[\n;])\s*' + names, group[1]):
            if name in re.findall(r'\w+', match[1]):
                return True
    return False


def _go_direct_calls(text):
    """Literal local calls only, conservatively rejecting uncertain bindings.

    This is not a Go type checker. Parameters, named returns, local rebinding
    and closures can hide a package function or receiver. In those cases keep
    ordinary retrieval eligibility but do not manufacture call priority.
    """
    code, declaration = _go_declaration(text)
    if declaration is None:
        return ()
    body_start = code.find('{', declaration.end())
    if body_start < 0:
        return ()
    signature, body = code[:body_start], code[body_start:]
    # Anonymous struct/interface signatures and nested function scopes need
    # real scope parsing. Abstain rather than bind their tokens by name.
    if re.search(r'\b(?:struct|interface)\b', signature) or re.search(r'\bfunc\s*\(', body):
        return ()

    def shadowed(name, *, parameter=True):
        return _go_identifier_shadowed(name, signature, body, parameter=parameter)

    calls = []
    for match in re.finditer(r'(?<![\w.])([A-Za-z_]\w*)\s*\(', body):
        name = match[1]
        if not body[:match.start()].rstrip().endswith('.') and not shadowed(name):
            calls.append((None, name))
    receiver, owner = declaration[1], declaration[2]
    if receiver and not shadowed(receiver, parameter=False):
        calls.extend((owner, match[1]) for match in re.finditer(
            r'(?<![\w.])' + re.escape(receiver) + r'\s*\.\s*(\w+)\s*\(', body)
            if not body[:match.start()].rstrip().endswith('.'))
    return tuple(dict.fromkeys(calls))


def _go_import_specs(text):
    code, literals = _go_code(text), _go_code(text, keep_strings=True)
    result = []
    # Locate import keywords in code with strings/comments masked first;
    # inspect literal paths only within those actual specification ranges.
    for match in re.finditer(r'(?m)^[ \t]*import\b', code):
        pos = match.end()
        start = re.match(r'\s*', literals[pos:]).end() + pos
        if start < len(code) and code[start] == '(':
            end = code.find(')', start + 1)
            if end < 0:continue
            block = literals[start + 1:end]
        else:
            block = literals[start:].split('\n', 1)[0]
        result.extend((alias or path.rsplit('/', 1)[-1], path)
            for alias, path in re.findall(r'(?m)^[ \t]*(?:(\w+|[._])\s+)?"([^"\n]+)"[ \t]*;?[ \t]*$', block))
    return result



def _go_import_declaration_spans(text):
    """Exact import syntax, independent of alias/package-name inference."""
    code, literals = _go_code(text), _go_code(text, keep_strings=True)
    result = []
    for match in re.finditer(r'(?m)(?:^[ \t]*|(?<=;)[ \t]*)import\b', code):
        start = match.end() + re.match(r'\s*', literals[match.end():]).end()
        if start < len(code) and code[start] == '(':
            closing = code.find(')', start + 1)
            if closing < 0:
                continue
            end = closing + 1
        else:
            spec = re.match(r'(?:(?:\w+|[._])\s+)?(?:"(?:\\.|[^"\\])*"|`[^`]*`)', literals[start:])
            if spec is None:
                continue
            end = start + spec.end()
        result.append((match.start(), end))
    return result


def _local_field_contracts(snapshot, gaps, output, read, limits, audit):
    """Two explicit lexical links: receiver field type -> local interface.

    No implementation selection, dispatch inference or automatic gap resolution.
    Shares the caller's verified read cache and file/byte/role limits.
    """
    root = Path(snapshot.source_root).resolve()
    inventory = snapshot.file_digests
    module = root / 'go.mod'
    if ('go.mod' not in inventory or module.is_symlink() or not module.is_file()
            or module.stat().st_size > 64000):
        return
    raw = module.read_bytes()
    if hashlib.sha256(raw).hexdigest() != inventory['go.mod']:
        audit.append(dict(status='FIELD_CONTRACT_MODULE_SNAPSHOT_MISMATCH'))
        return
    match = re.search(r'(?m)^module\s+(\S+)', raw.decode(errors='replace'))
    if not match:
        return
    local_module = match[1]
    seen = set()
    for endpoint in list(output):
        receiver = re.match(r'\s*func\s*\(\s*(\w+)\s+\*?(\w+)\s*\)', endpoint['text'])
        if not endpoint['path'].endswith('.go') or not receiver:
            continue
        calls = set(re.findall(r'\b' + re.escape(receiver[1]) + r'\.(\w+)\.(\w+)\s*\(',
                               _go_code(endpoint['text'])))
        for gap in gaps:
            hint = gap.get('locator_hint', {})
            if hint.get('path') != endpoint['path']:
                continue
            requested = set(re.findall(r'\b(\w+)\.(\w+)\b', hint.get('search_term', '')))
            for field, method in sorted(calls & requested):
                key = (endpoint['path'], receiver[2], field, method, gap['id'])
                if key in seen:
                    continue
                seen.add(key)
                record = dict(gap_id=gap['id'], endpoint_path=endpoint['path'],
                              receiver_type=receiver[2], field=field, method=method)
                try:
                    if len(output) >= 6:
                        raise ValueError('ROLE_BUDGET')
                    # Both the caller and the owner declaration must still belong
                    # to this snapshot. The scope index alone is never evidence.
                    read(endpoint['path'])
                    owners = [u for u in output if u.get('symbol') == receiver[2]
                              and PurePosixPath(u['path']).parent == PurePosixPath(endpoint['path']).parent
                              and 'SAME_PACKAGE_RECEIVER_DECLARATION' in u.get('association_status', '')]
                    if len(owners) != 1:
                        raise ValueError('OWNER_UNRESOLVED')
                    owner_text = read(owners[0]['path'])
                    types = [u for u in parse_source_units(owners[0]['path'], owner_text,
                             snapshot.snapshot_id, max_lines=160, max_chars=12000)
                             if u.get('symbol') == receiver[2] and not u.get('truncated')]
                    if len(types) != 1:
                        raise ValueError('OWNER_UNRESOLVED')
                    fields = re.findall(r'(?m)^\s*' + re.escape(field) + r'\s+\*?(\w+)\.(\w+)\s*$',
                                        _go_code(types[0]['text']))
                    if len(fields) != 1:
                        raise ValueError('FIELD_UNRESOLVED')
                    alias, type_name = fields[0]
                    imports = _go_import_specs(owner_text)
                    packages = [p[len(local_module) + 1:] for a, p in imports
                                if a == alias and a not in {'_', '.'} and p.startswith(local_module + '/')]
                    if len(packages) != 1:
                        raise ValueError('IMPORT_UNRESOLVED')
                    paths = sorted(p for p in inventory if str(PurePosixPath(p).parent) == packages[0]
                                   and p.endswith('.go') and not p.endswith('_test.go'))
                    matches = []
                    for path in paths:
                        matches.extend(u for u in parse_source_units(path, read(path), snapshot.snapshot_id,
                                       max_lines=160, max_chars=12000) if u.get('symbol') == type_name)
                    if len(matches) != 1 or matches[0].get('truncated'):
                        raise ValueError('TYPE_UNRESOLVED_OR_AMBIGUOUS')
                    unit = matches[0]
                    code = _go_code(unit['text'])
                    if (not re.match(r'\s*type\s+' + re.escape(type_name) + r'\s+interface\s*\{', code)
                            or not re.search(r'(?m)^\s*' + re.escape(method) + r'\s*\(', code)):
                        raise ValueError('METHOD_NOT_DECLARED')
                    item = {**unit, 'gap_role_keys': [gap['id'] + ':field_contract'],
                            'evidence_gap_refs': [gap['id']], 'required_for_gap_delivery': True,
                            'anchor_priority': 4, 'hop_distance': 2,
                            'scope_extension': 'EXPLICIT_LOCAL_FIELD_CONTRACT',
                            'association_status': 'EXPLICIT_FIELD_LOCAL_INTERFACE; lexical contract location, not implementation, readiness, causality or edit permission'}
                    prior = next((u for u in output if u['id'] == item['id']), None)
                    if prior is None:
                        output.append(item)
                    else:
                        for name in ('gap_role_keys', 'evidence_gap_refs'):
                            prior[name] = sorted(set(prior.get(name, [])) | set(item[name]))
                        prior['required_for_gap_delivery'] = True
                    audit.append(dict(record, status='FIELD_CONTRACT_FOUND', path=unit['path'], unit_id=unit['id']))
                except (ValueError, OSError) as exc:
                    audit.append(dict(record, status='FIELD_CONTRACT_' + (str(exc) if isinstance(exc, ValueError) else 'UNAVAILABLE')))
    return


def locate_gap_roles(snapshot, gaps: list[dict], limits: dict):
    root=Path(snapshot.source_root).resolve(); inventory=snapshot.file_digests
    max_files=limits.get('max_new_files',4);max_bytes=limits.get('max_bytes',4*1024*1024)
    seeds=limits.get('seed_units',[]); output=[];used=0;scanned={};audit=[]
    rejected = {}
    search_facts = {}
    scanned_sizes = {}

    def read(path):
        nonlocal used
        if path in rejected:
            raise ValueError(rejected[path])
        if path in scanned:
            return scanned[path]
        target = root / path
        if (path not in inventory or target.is_symlink() or not target.is_file()
                or not target.resolve().is_relative_to(root)):
            raise ValueError('UNAVAILABLE')
        if len(scanned) >= max_files:
            raise ValueError('FILE_BUDGET')
        size = target.stat().st_size
        if size > 1024 * 1024 or used + size > max_bytes:
            raise ValueError('BYTE_BUDGET')
        raw = target.read_bytes()
        # Charge every attempted unique source read before accepting its hash.
        # Invalid bytes reserve their slot but are never parsed or reused.
        scanned[path] = ''
        used += len(raw)
        error = ('BYTE_BUDGET' if used > max_bytes or len(raw) > 1024 * 1024 else
                 'SNAPSHOT_MISMATCH' if hashlib.sha256(raw).hexdigest() != inventory[path] else None)
        if error:
            rejected[path] = error
            raise ValueError(error)
        scanned_sizes[path] = len(raw)
        scanned[path] = raw.decode('utf-8', errors='replace')
        return scanned[path]

    def try_read(path, stage):
        try:
            return read(path)
        except (ValueError, OSError) as exc:
            audit.append(dict(path=path, status=stage + '_' +
                              (str(exc) if isinstance(exc, ValueError) else 'UNAVAILABLE')))
            return None

    shared_queries=set()
    for g in gaps:
        shared_queries |= _terms(g.get('locator_hint',{}).get('search_term',''))
    for gap in gaps:
        hint=gap.get('locator_hint',{});path=hint.get('path','')
        if path not in inventory or PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts:continue
        if not (hint.get('symbol') or hint.get('search_term')):continue
        text = try_read(path, 'EXACT_ANCHOR')
        if text is None:continue
        query=hint.get('symbol') or hint.get('search_term','')
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
            # A precise symbol locator remains an anchor even when its name
            # is a stopword (for example Go's Not). Lexical scoring must not
            # erase an explicit source identity before match_tier sees it.
            if symbol == query or match_tier(u) == 2 or (score and (terms&_terms(body) or terms&_terms(symbol))):
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
            if not literal:
                # read() accepted the full bytes only after the snapshot hash
                # check. This fact is about this exact query in this file,
                # never implementation, repository, or dependency absence.
                locator = dict(scope='snapshot_file_content', query_kind='EXACT_TEXT_IN_FILE',
                    query=query, path=path, source_file_sha256=inventory[path],
                    matching='case-sensitive escaped text with Unicode word boundaries',
                    decoding='utf-8 with replacement',
                    inspected_file_count=1, inspected_byte_count=scanned_sizes[path],
                    complete=True, truncated=False, match_count=0, matched_paths=[])
                excerpt = ('The exact text query ' + repr(query) + ' was not found in the complete '
                    'snapshot-bound file ' + path + '; this establishes no repository or external API absence.')
                digest = static_search_content_digest(snapshot.snapshot_id, locator, excerpt)
                search_facts[digest] = dict(id='SEARCH-'+digest[:16], kind='STATIC_SEARCH',
                    snapshot_id=snapshot.snapshot_id, locator=locator, content_digest=digest,
                    excerpt=excerpt, origin='bounded exact query over verified complete snapshot file')
        for index,(_,unit) in enumerate(scored[:2]):
            if match_tier(unit)<match_tier(scored[0][1]):continue
            first=scored[0][1]
            if (index and first.get('symbol') and not unit.get('symbol')
                    and unit['locator']['start_line']<=first['locator']['end_line']
                    and first['locator']['start_line']<=unit['locator']['end_line']):continue
            if index and scored[index][0]<.7*scored[0][0]:continue
            tier = match_tier(unit)
            item={**unit,'gap_role_keys':[gap['id']+(':anchor' if index==0 and tier else ':context')],
                  'optional_gap_context': not bool(tier),
                  'evidence_gap_refs':[gap['id']],'anchor_priority':4,
                  'scope_extension':'EVIDENCE_GAP_EXACT_PATH','hop_distance':0,
                  'association_status':('EXACT_PUBLIC_GAP_QUERY' if tier else 'BOUNDED_NONMATCHING_LEXICAL_CONTEXT')+'; lexical location only, not causality or edit grant'}
            prior=next((u for u in output if u['id']==item['id']),None)
            if prior is None:output.append(item)
            else:
                prior['optional_gap_context'] = merge_gap_context_optional(prior, item)
                prior['gap_role_keys']=sorted(set(prior['gap_role_keys'])|set(item['gap_role_keys']))
                prior['evidence_gap_refs']=sorted(set(prior.get('evidence_gap_refs',[]))|set(item['evidence_gap_refs']))
            audit.append(dict(gap_id=gap['id'],unit_id=item['id'],path=path,
                status='FOUND' if match_tier(unit) else 'LEXICAL_CONTEXT',
                match_basis={2:'EXACT_SYMBOL',1:'EXACT_LITERAL_REFERENCE',0:'LEXICAL_FALLBACK'}[match_tier(unit)]))
    # Connector calls belong to the located gap spans, not to every function
    # in the files that supplied them. Keep these spans before adding receiver
    # prefixes or other read-only support below.
    connector_endpoints = tuple(output)
    # Method-name references do not include Go receiver types. Recover a known
    # same-package receiver declaration before generic name matches consume the
    # six role slots. The scope index is a locator only: re-read/hash the file.
    receiver_keys = set()
    declarations = [*limits.get('seed_declarations', []), *seeds]
    receiver_context = []
    receiver_import_support = []

    def receiver_declarations(path, text, owner):
        # The lexical parser can expose a declaration-looking line inside a
        # block comment or raw string. Verify its start against the full file,
        # where the opening delimiter is still available to the code masker.
        code = _go_code(text)
        starts = {code.count('\n', 0, match.start()) + 1 for match in re.finditer(
            r'(?m)^[ \t]*type\s+' + re.escape(owner) + r'\s+struct\s*\{', code)}
        return [unit for unit in parse_source_units(path, text, snapshot.snapshot_id,
                    max_lines=160, max_chars=12000)
                if unit.get('symbol') == owner and unit['locator']['start_line'] in starts
                and re.match(r'\s*type\s+' + re.escape(owner) + r'\s+struct\s*\{', unit['text'])]

    for endpoint in list(output):
        if not endpoint['path'].endswith('.go'):
            continue
        receiver = re.match(r'\s*func\s*\(\s*\w+\s+\*?(\w+)\s*\)', endpoint['text'])
        if not receiver:
            continue
        owner = receiver[1]
        directory = PurePosixPath(endpoint['path']).parent
        if (directory, owner) in receiver_keys:
            continue
        receiver_keys.add((directory, owner))
        locations = {(u['path'], u['locator']['start_line']) for u in declarations
                     if u.get('symbol') == owner and u.get('path', '').endswith('.go')
                     and not u['path'].endswith('_test.go')
                     and PurePosixPath(u['path']).parent == directory}
        record = dict(receiver_type=owner, endpoint_path=endpoint['path'])
        found_declarations = {}
        if not locations:
            # The seed index is bounded and may never have parsed this package's
            # receiver file. Inspect only same-package production files, using
            # the existing read budget. A filename orders reads; only an exact
            # declaration establishes a location. Do not claim uniqueness when
            # an unread file could still contain another declaration.
            package_paths = [path for path in inventory if path.endswith('.go')
                and not path.endswith('_test.go') and PurePosixPath(path).parent == directory]
            package_paths.sort(key=lambda path: (
                path not in scanned,
                -int(PurePosixPath(path).stem.casefold() == owner.casefold()),
                -len(_terms(PurePosixPath(path).stem) & _terms(owner)), path))
            complete = True
            for path in package_paths:
                text = try_read(path, 'RECEIVER_DECLARATION')
                if text is None:
                    complete = False
                    if len(scanned) >= max_files or used >= max_bytes:
                        break
                    continue
                for unit in receiver_declarations(path, text, owner):
                    location = (path, unit['locator']['start_line'])
                    locations.add(location)
                    found_declarations[location] = unit
                if len(locations) > 1:
                    break
            if len(locations) <= 1 and not complete:
                receiver_context.extend((unit, endpoint) for unit in found_declarations.values())
                audit.append(dict(record, status='RECEIVER_DECLARATION_UNRESOLVED'))
                continue
        if len(locations) != 1:
            receiver_context.extend((unit, endpoint) for unit in found_declarations.values())
            audit.append(dict(record, status='RECEIVER_DECLARATION_AMBIGUOUS' if locations
                              else 'RECEIVER_DECLARATION_NOT_IN_SCOPE'))
            continue
        path, _ = next(iter(locations))
        record['path'] = path
        if len(output) >= 6:
            audit.append(dict(record, status='RECEIVER_DECLARATION_ROLE_BUDGET'))
            continue
        text = try_read(path, 'RECEIVER_DECLARATION')
        if text is None:continue
        matches = receiver_declarations(path, text, owner)
        if len(matches) != 1 or matches[0].get('truncated'):
            audit.append(dict(record, status='RECEIVER_DECLARATION_UNRESOLVED'))
            continue
        unit = matches[0]
        # Keep the complete type at its own original coordinates. Import
        # semantics travel separately as complete, real import declarations;
        # unrelated constants and the file prefix are not part of this type.
        qualifiers = set(re.findall(r'\b([A-Za-z_]\w*)\s*\.', _go_code(unit['text'])))
        spans = _go_import_declaration_spans(text)
        code = _go_code(text)
        dot_import = any(re.search(r'\bimport\s*\.\s|(?:^|[;(])\s*\.\s', code[start:end], re.M)
                         for start, end in spans)
        # A default package name need not equal the import path basename.
        # Keep the actual complete import syntax, including raw literals and
        # semicolon-packed specs, without pretending to infer that binding.
        imports = [_unit(snapshot.snapshot_id, path, text, start, end,
                    'BOUNDED_RECEIVER_DECLARATION_CONTEXT', unit_kind='SOURCE_TEXT', max_chars=12000)
                   for start, end in spans] if qualifiers or dot_import else []
        if ((qualifiers and not imports) or any(u.get('truncated') for u in imports)
                or sum(len(u['text']) for u in [unit, *imports]) > 12000
                or sum(u['locator']['end_line'] - u['locator']['start_line'] + 1
                       for u in [unit, *imports]) > 160):
            audit.append(dict(record, status='RECEIVER_IMPORT_CONTEXT_NOT_INCLUDED'))
            continue
        # An already-required span can carry the same import declaration for
        # another receiver in this file. Do not emit that source twice.
        imports = [imp for imp in imports if not any(
            prior.get('required_for_gap_delivery') and prior['path'] == path
            and prior.get('snapshot_id') == snapshot.snapshot_id
            and prior['locator']['start_char'] <= imp['locator']['start_char']
            and imp['locator']['end_char'] <= prior['locator']['end_char']
            and prior['text'][imp['locator']['start_char'] - prior['locator']['start_char']:
                              imp['locator']['end_char'] - prior['locator']['start_char']] == imp['text']
            for prior in [*output, *receiver_import_support])]
        sources = [unit, *imports]
        ordered = sorted(sources, key=lambda u: u['locator']['start_char'])
        if imports and all(not text[a['locator']['end_char']:b['locator']['start_char']].strip()
                           for a, b in zip(ordered, ordered[1:])):
            # Adjacent import/type declarations are one real continuous span.
            # Do not spend an extra slot merely for representational splitting;
            # intervening comments, constants or other code keep spans separate.
            start, end = ordered[0]['locator']['start_char'], ordered[-1]['locator']['end_char']
            if end - start <= 12000 and text[start:end].count('\n') <= 160:
                sources = [_unit(snapshot.snapshot_id, path, text, start, end,
                    'BOUNDED_RECEIVER_DECLARATION_CONTEXT', unit_kind='FUNCTION_OR_TYPE',
                    symbol=owner, declared=[owner], max_chars=12000)]
        group = []
        for source in sources:
            group.append({**source, 'gap_role_keys': ['RECEIVER_DECLARATION:' + source['id']],
                'required_for_gap_delivery': True, 'anchor_priority': 4, 'hop_distance': 1,
                'association_status': 'SAME_PACKAGE_RECEIVER_DECLARATION; lexical source context only, not API behavior, causal proof or edit permission'})
        existing = {u['id']: u for u in [*output, *receiver_import_support]}
        # Import syntax supports an existing owner; splitting its representation
        # must not consume one of the six semantic-role discovery slots. Append
        # it after the existing lexical/helper discovery. These are additional
        # physical records, still required under final record/byte/dispatch caps.
        if len(output) + int(group[0]['id'] not in existing) > 6:
            audit.append(dict(record, status='RECEIVER_DECLARATION_ROLE_BUDGET'))
            continue
        for index, item in enumerate(group):
            if item['id'] not in existing:
                (output if index == 0 else receiver_import_support).append(item)
                existing[item['id']] = item
            else:
                prior = existing[item['id']]
                prior['gap_role_keys'] = sorted(set(prior.get('gap_role_keys', [])) | set(item['gap_role_keys']))
                prior['required_for_gap_delivery'] = True
            audit.append(dict(record, unit_id=item['id'], status='FOUND', basis=item['association_status']))
    _local_field_contracts(snapshot, gaps, output, read, limits, audit)
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
            hint=gap.get('locator_hint',{})
            query=hint.get('search_term','')
            for receiver,method in re.findall(r'\b(\w+)\.(\w+)\b',query):
                requested_receivers.setdefault((hint.get('path'),receiver),set()).add(method)
        for endpoint in connector_endpoints:
            path=endpoint['path']
            if path not in scanned or not path.endswith('.go'):
                continue
            imports={}
            for alias,imported in _go_import_specs(scanned[path]):
                if imported.startswith(local_module+'/'):
                    imports[alias]=imported[len(local_module)+1:]
            # Imports can precede a bounded endpoint. Constructor assignments
            # and method calls must be in that endpoint's actual code; sibling
            # functions, comments and strings are not association evidence.
            text=_go_code(endpoint['text'])
            for match in re.finditer(r'\b(\w+(?:\s*,\s*\w+)*)\s*(?::=|=)\s*(\w+)\.(\w+)\s*\(',text):
                variables,alias,constructor=match.groups()
                if alias not in imports:continue
                variables=[v.strip() for v in variables.split(',')]
                methods={m[1] for variable in variables for m in re.finditer(r'\b'+re.escape(variable)+r'\.(\w+)\s*\(',text)
                         if _terms(m[1])&query_terms}
                requested=set().union(*(requested_receivers.get((path,v),set()) for v in variables))
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
                text = try_read(path, 'LOCAL_CONNECTOR')
                if text is None:continue
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
    # Qualified references are handled by exact anchors/local imports above.
    # Splitting processor.Options into generic words must not bind api.Options
    # (or any other same-named declaration) as a public handler.
    named_terms=set(); named_identifiers=set()
    for gap in gaps:
        hint=gap.get('locator_hint',{})
        for value in (gap.get('summary',''),hint.get('search_term',''),hint.get('symbol','')):
            unqualified=re.sub(r'\b[A-Za-z_]\w*(?:\s*\.\s*[A-Za-z_]\w*)+\b',' ',value)
            named_terms.update(_terms(unqualified))
            named_identifiers.update(token.casefold() for token in re.findall(r'\b[A-Za-z_]\w*\b',unqualified))
    generic_names={'get','set','new','api','handler','handle','request','response','read','write','public','method','function','state'}
    endpoints=[]
    for unit in [*limits.get('seed_declarations',[]),*seeds]:
        path=unit.get('path','');parts=PurePosixPath(path).parts;symbol=unit.get('symbol','')
        if (not symbol or not path.endswith('.go') or path.endswith('_test.go')
                or any(p in {'test','tests','vendor','generated','restapi','client'} for p in parts)
                or 'api' not in parts):continue
        if 'text' in unit and not re.match(r'\s*func\b',_go_code(unit['text'])):continue
        # A general API directory contains types and utility functions too.
        # A generic word such as "value" must not bind getValueNode as a
        # required public endpoint. Preserve literal function names and the
        # existing explicit Handler naming convention; do not infer a handler
        # from arbitrary CamelCase token overlap.
        score=(int(symbol.casefold() in named_identifiers and symbol.casefold() not in generic_names)
               or (len((_terms(symbol)-generic_names)&named_terms)
                   if re.search(r'(?:Handler|_handler)$',symbol) else 0))
        if score:endpoints.append((score,unit))
    endpoints.sort(key=lambda pair:(-pair[0],pair[1]['path'],pair[1]['locator']['start_line']))
    for _,unit in endpoints:
        if len(output)>=6:break
        path=unit['path']
        if any(u['path']==path and u.get('symbol')==unit.get('symbol') for u in output):continue
        text = try_read(path, 'NAMED_ENDPOINT')
        if text is None:continue
        # Revalidate against the full current file: a lexical parser can also
        # expose function-shaped text inside a block comment or raw string.
        code=_go_code(text)
        declarations={(code.count('\n',0,match.start())+1,match[1]) for match in re.finditer(
            r'(?m)^[ \t]*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\(',code)}
        expanded=[u for u in parse_source_units(path,scanned[path],snapshot.snapshot_id,max_lines=768,max_chars=32000)
                  if u.get('symbol')==unit['symbol']
                  and (u['locator']['start_line'],u.get('symbol')) in declarations]
        if not expanded:continue
        # Structured exact symbol requests remain obligations even when the
        # locator lacks a path. Prose/concept overlap alone is optional.
        def within_locator(hint):
            requested = hint.get('path')
            if not requested:
                return True
            scope = PurePosixPath(requested)
            target = PurePosixPath(path)
            return (not scope.is_absolute() and '..' not in scope.parts
                    and target.parts[:len(scope.parts)] == scope.parts)
        explicit = any((hint := gap.get('locator_hint', {})).get('symbol') == unit['symbol']
                       and within_locator(hint) for gap in gaps)
        item={**expanded[0],'gap_role_keys':['NAMED_PUBLIC_ENDPOINT:'+expanded[0]['id']],
              'optional_gap_context':not explicit, 'required_for_gap_delivery':explicit,
              'anchor_priority':4 if explicit else 0,'hop_distance':2,
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
        if path not in inventory:continue
        text = try_read(path, 'RESTORED_SEED')
        if text is None:continue
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
            item={**unit,'gap_role_keys':['CONNECTOR:'+unit['id']], 'anchor_priority':0,
                'optional_gap_context':True, 'hop_distance':depth,'association_status':'LEXICAL_REFERENCE_HINT; no verified dispatch or causal edge'}
            output.append(item);frontier.append(item);seen.add(item['id'])
    # Reuse the same two optional slots for direct same-file calls from
    # located endpoints/connectors. A connector can be a free function too;
    # receiver-only lookup silently omitted its helper implementation. Fresh
    # reads still share the original snapshot/file/byte limits. No recursion.
    optional_count = 0
    call_terms = shared_queries | set().union(*(_terms(g.get('summary', '')) for g in gaps))
    # Prefer the declaration actually named by the gap over incidental output
    # order; otherwise an earlier connector can consume both optional slots
    # before the requested lookup/reader's direct helpers are considered.
    callee_roots = sorted(output, key=lambda unit:
        -len(_terms(str(unit.get('symbol', ''))) & call_terms))
    for endpoint in callee_roots:
        if optional_count >= 2:break
        roles = endpoint.get('gap_role_keys', [])
        if not any(role.endswith(':anchor') or role.startswith('CONNECTOR:') for role in roles):continue
        path = endpoint['path']
        if not path.endswith('.go') or path.endswith('_test.go'):continue
        calls = _go_direct_calls(endpoint['text'])
        if not calls:continue
        text = try_read(path, 'LOCAL_CALLEE')
        if text is None:continue
        masked = _go_code(text)
        starts = {masked.count('\n', 0, m.start()) + 1 for m in re.finditer(r'(?m)^[ \t]*func\b', masked)}
        declarations = parse_source_units(path, text, snapshot.snapshot_id,
                                          max_lines=160, max_chars=12000)
        for owner, name in calls:
            if optional_count >= 2:break
            matches = []
            for unit in declarations:
                _, declared = _go_declaration(unit['text'])
                if (declared and unit['locator']['start_line'] in starts
                        and declared[2] == owner and declared[3] == name):
                    matches.append(unit)
            if len(matches) != 1 or matches[0].get('truncated'):continue
            unit = matches[0]
            if any(u['id'] == unit['id'] or (u['path'] == path and u.get('symbol') == name
                   and not u.get('truncated') and (prior := _go_declaration(u['text'])[1])
                   and prior[2] == owner) for u in output):continue
            output.append({**unit, 'evidence_gap_refs': list(endpoint.get('evidence_gap_refs', [])),
                'optional_gap_context': True, 'direct_local_callee_support': True,
                **({'same_receiver_callee_support': True} if owner else {}),
                'anchor_priority': 4, 'hop_distance': 1,
                'scope_extension': 'BOUNDED_DIRECT_LOCAL_CALLEE',
                'association_status': 'EXPLICIT_LOCAL_CALL; optional lexical support, not verified dispatch, readiness, causality or edit permission'})
            optional_count += 1
            audit.append(dict(path=path, symbol=name, unit_id=unit['id'], status='OPTIONAL_LOCAL_CALLEE'))
    # A verified source declaration remains a fact even when bounded package
    # lookup cannot establish the receiver binding. Keep it optional and add it
    # only after binding-dependent roles have been resolved. Reuse the same two
    # optional slots; do not let uncertain owner context consume preferred roles
    # or feed _local_field_contracts as a unique receiver declaration.
    for unit, endpoint in receiver_context:
        if optional_count >= 2:
            break
        if any(item['id'] == unit['id'] for item in output):
            continue
        gap_ids = list(endpoint.get('evidence_gap_refs', []))
        output.append({**unit, 'evidence_gap_refs': gap_ids,
            'gap_role_keys': [gap_id + ':context' for gap_id in gap_ids],
            'optional_gap_context': True, 'anchor_priority': 0, 'hop_distance': 1,
            'association_status': 'LOCATED_TEXT_ONLY; receiver binding unresolved, not a verified causal/reference edge or edit grant'})
        optional_count += 1
    output.extend(receiver_import_support)
    return output,dict(inspected_files=len(scanned),inspected_bytes=used,records=audit,
        static_search_evidence=list(search_facts.values()),
        role_basis='Task-specific requested endpoints plus bounded lexical connectors',
        truncated=len(scanned)>=max_files or used>=max_bytes)


def audit_gap_delivery(gaps: list[dict], selected: list[dict], serialized_request: str, *,
                       drop_reasons: Mapping[str, str] | None = None,
                       model_citations=None):
    """Record the bounded evidence path from retrieval to model input.

    ``available_unit_ids`` and ``drop_reasons`` are engine facts from the
    selector.  They are deliberately separate from the serialized request so
    a required unit that was found but clipped cannot be mistaken for a unit
    that was never found.  ``model_citations`` is optional because it only
    exists after a response has been validated.
    """
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
    selected_ids={str(unit.get('id')) for unit in selected if isinstance(unit, dict) and unit.get('id') is not None}
    explicit_drop_reasons = {str(key): str(value) for key, value in (drop_reasons or {}).items()}
    # The bounded retrieval stage can provide IDs that were found but dropped;
    # legacy callers omit that pool and expose only their selected candidates.
    available_ids={str(i) for gap in gaps for i in gap.get('available_unit_ids', ())}
    if not available_ids:
        available_ids=set(selected_ids)
    cited_ids = None if model_citations is None else {str(item) for item in model_citations}
    required={i for gap in gaps for i in gap.get('required_unit_ids',[])}
    for unit in selected:
        # A required endpoint may be introduced by a bounded extension (for
        # example a same-package receiver declaration) without a gap role of
        # its own.  It still has to be audited against the serialized source
        # catalog; otherwise the delivery ledger reports it as missing even
        # though it was selected and delivered.
        if not unit.get('gap_role_keys') and str(unit.get('id')) not in required:
            continue
        r=catalog.get('SRC-'+unit['id'],{})
        delivered=(r.get('excerpt')==unit['text'] and r.get('content_digest')==unit['content_digest']
            and hashlib.sha256(unit['text'].encode()).hexdigest()==unit['content_digest'])
        cited = None if cited_ids is None else (
            str(unit['id']) in cited_ids or 'SRC-' + str(unit['id']) in cited_ids)
        drop_reason = None if delivered else 'NOT_DELIVERED'
        rows.append(dict(unit_id=unit['id'],roles=list(unit.get('gap_role_keys', ())),found=True,
                         selected=True,delivered=delivered,cited=cited,
                         drop_reason=drop_reason,
                         status=(('NOT_DELIVERED' if not delivered else
                                  'MODEL_DID_NOT_USE_EVIDENCE' if cited is False else 'DELIVERED'))))
    delivered_ids={r['unit_id'] for r in rows if r['delivered']}
    for unit_id in sorted(required-selected_ids):
        found=unit_id in available_ids
        reason = explicit_drop_reasons.get(unit_id, 'FOUND_NOT_SELECTED' if found else 'NOT_FOUND')
        rows.append(dict(unit_id=unit_id,roles=[],found=found,selected=False,
                         delivered=False,cited=None,drop_reason=reason,status=reason))
    missing=sorted(required-delivered_ids)
    failures=sorted({row['status'] for row in rows if row.get('status') not in {None, 'DELIVERED'}})
    return dict(status='DELIVERED' if all(r['delivered'] for r in rows) and not missing and
                not any(r.get('cited') is False for r in rows) else 'INCOMPLETE',records=rows,
        missing_required_ids=missing, failure_codes=failures,
        model_citation_status=('CITED' if cited_ids is not None and any(r.get('cited') for r in rows)
                               else 'NOT_CITED' if cited_ids is not None and rows else 'NOT_AVAILABLE'),
        interpretation='Found, selection and exact request delivery are recorded separately; delivery does not prove causal relevance')


def annotate_gap_delivery_citations(receipt: Mapping, diagnosis: Mapping | None) -> dict:
    """Record whether the accepted diagnosis cited delivered gap evidence."""
    cited=set()
    if isinstance(diagnosis, Mapping):
        for item in diagnosis.get('evidence_refs', ()):
            if isinstance(item, Mapping):
                cited.update(str(value) for value in (item.get('id'), item.get('unit_id')) if value is not None)
            elif isinstance(item, str):
                cited.add(item)
    result=dict(receipt);rows=[]
    for row in receipt.get('records', ()):
        row=dict(row)
        if row.get('delivered'):
            row['cited']=str(row.get('unit_id')) in cited or 'SRC-'+str(row.get('unit_id')) in cited
            row['status']='DELIVERED' if row['cited'] else 'MODEL_DID_NOT_USE_EVIDENCE'
        elif row.get('found') and not row.get('selected'):
            row['status']=row.get('drop_reason') or 'FOUND_NOT_SELECTED'
        elif not row.get('found'):
            row['status']='NOT_FOUND'
        else:
            row['status']=row.get('drop_reason') or 'NOT_DELIVERED'
        rows.append(row)
    result['records']=rows
    result['model_citation_status']=('CITED' if any(row.get('cited') for row in rows)
                                     else 'NOT_CITED' if rows else 'NOT_AVAILABLE')
    result['receipt_digest']=hashlib.sha256(json.dumps(
        {key:value for key,value in result.items() if key!='receipt_digest'},
        ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return result
