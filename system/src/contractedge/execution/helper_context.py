"""Bounded public source support for local calls, lifecycle and exceptions."""
import hashlib
import re
from pathlib import Path, PurePosixPath

from contractedge.source.parsers import parse_source_units
from contractedge.contracts.validation import canonical_digest


def _helper_source_record(snapshot, path, locator, excerpt, origin):
    """Equal helper bodies in different files/positions are distinct evidence."""
    locator = dict(path=path, **locator)
    digest = hashlib.sha256(excerpt.encode()).hexdigest()
    identifier = canonical_digest({'locator': locator, 'content_digest': digest})[:16]
    return dict(id='HELPER-'+identifier, kind='SOURCE_SPAN', snapshot_id=snapshot.snapshot_id,
        locator=locator, content_digest=digest, excerpt=excerpt, origin=origin)


def compiler_dependency_support(snapshot, feedback, *, max_bytes=24000, max_records=2, already_supplied=None):
    """Keep the compiler selector; share the pinned public package reader."""
    from contractedge.execution.public import has_candidate_build_failure
    from contractedge.retrieval.gap_delivery import _go_code, _go_import_specs
    from contractedge.source.dependency import PublicGoDependencies

    records, audit = [], []
    if not isinstance(feedback, dict) or max_bytes <= 0 or max_records <= 0:
        return records, audit
    failures = [d for d in feedback.get('observation_diagnostics', [])
        if d.get('kind') == 'CANDIDATE_BUILD_FAILURE' and has_candidate_build_failure(dict(
            status='EXECUTION_FAILED', snapshot_id=feedback.get('to_snapshot'), candidate_build_failures=[d]))]
    if not failures:
        return records, audit
    try:
        reader = PublicGoDependencies(snapshot, feedback.get('case_id'),
            feedback.get('public_execution_capabilities'))
    except (OSError, ValueError, KeyError, TypeError, UnicodeError) as exc:
        return [], [dict(result='DEPENDENCY_API_UNAVAILABLE', reason=str(exc))]
    targets = []
    for failure in failures[:4]:
        matches = re.findall(r'\b\w+\.\w+ undefined \(type \*?(\w+)\.([A-Z]\w*) has no field or method \w+\)',
                             failure['stderr_excerpt'])
        for location in failure['source_locations'][:4]:
            try:
                text = reader.current_text(location['path'])
            except (OSError, ValueError, UnicodeError):
                continue
            imports = _go_import_specs(text)
            for package, typename in matches:
                paths = {p for alias, p in imports if alias not in {'_', '.'} and p.rsplit('/', 1)[-1] == package}
                if len(paths) != 1:
                    audit.append(dict(result='DEPENDENCY_PACKAGE_AMBIGUOUS', package=package))
                    continue
                imported = next(iter(paths))
                target = (imported, package, typename)
                if reader.resolve(imported) and target not in targets:
                    targets.append(target)
    used = 0
    for imported, package, typename in targets[:2]:
        members, scan_audit, complete = reader.package(imported, package_name=package,
            max_member_bytes=min(max_bytes-used, 65536))
        audit.extend(scan_audit)
        candidates = [row for row in members if re.search(r'\btype\s+' + re.escape(typename)
            + r'\s+(?:struct|interface)\b', _go_code(row['excerpt']))]
        module, version, _ = reader.resolve(imported)
        if complete and len(candidates) == 1:
            row = candidates[0]
            size = len(row['excerpt'].encode())
            reason = dict(type_name=typename, failure_snapshot=feedback['to_snapshot'],
                current_source_snapshot=snapshot.snapshot_id)
            if row in records or already_supplied is not None and already_supplied(row):
                audit.append(dict(result='ALREADY_DELIVERED_DEPENDENCY_API', id=row['id'], **reason))
                continue
            if len(records) < max_records and used+size <= max_bytes:
                records.append(row)
                used += size
                audit.append(dict(result='INCLUDED_PUBLIC_DEPENDENCY_API', id=row['id'], module=module,
                    version=version, **reason))
        else:
            audit.append(dict(result='DEPENDENCY_API_INCOMPLETE_OR_AMBIGUOUS', module=module,
                version=version, type_name=typename))
    return records, audit


def _go_split(code):
    """Split an already masked Go list; reject unbalanced syntax."""
    stack, parts, start = [], [], 0
    pairs = {')': '(', ']': '[', '}': '{'}
    for i, char in enumerate(code):
        if char in '([{': stack.append(char)
        elif char in ')]}':
            if not stack or stack.pop() != pairs[char]: return []
        elif char == ',' and not stack:
            parts.append(code[start:i].strip()); start = i + 1
    return [] if stack else [p for p in [*parts, code[start:].strip()] if p]


def _go_close(code, start):
    depth = 0
    for i in range(start, len(code)):
        if code[i] == code[start]: depth += 1
        elif code[i] == {'(': ')', '{': '}'}[code[start]]:
            depth -= 1
            if depth == 0: return i
    return None


def _go_signature(unit):
    from contractedge.retrieval.gap_delivery import _go_code
    code = _go_code(unit['text'])
    match = re.match(r'\s*func\s+' + re.escape(unit.get('symbol', '')) + r'\s*\(', code)
    if not match: return None
    end = _go_close(code, match.end()-1)
    if end is None: return None
    parameters = _go_split(code[match.end():end]); names = []
    for part in parameters:
        name = re.match(r'^(\w+)\s+\S', part)
        if not name: return None  # Grouped/unnamed parameters need explicit evidence.
        names.append(name[1])
    tail = code[end+1:].lstrip()
    if tail.startswith('('):
        close = _go_close(tail, 0)
        if close is None: return None
        returns = _go_split(tail[1:close])
    else:
        returns = [tail.split('{', 1)[0].strip()]
    types = [re.fullmatch(r'\*?(\w+)', part) for part in returns]
    opening = code.find('{', end + 1)
    if opening < 0: return None
    return names, [m[1] if m else None for m in types], code[opening + 1:]


def _go_initializers(code, aliases, module):
    writes, found = {}, {}
    pattern = r'(?<![\w.])(\w+(?:\s*,\s*\w+)*)\s*(?::=|=(?!=))\s*'
    for match in re.finditer(pattern, code):
        variables = [v.strip() for v in match[1].split(',')]
        for variable in variables: writes[variable] = writes.get(variable, 0) + 1
        call = re.match(r'(\w+)\.(\w+)\s*\(', code[match.end():])
        if not call or call[1] not in aliases: continue
        opening = match.end() + call.end() - 1
        end = _go_close(code, opening)
        if end is None: continue
        args = _go_split(code[opening+1:end])
        for slot, variable in enumerate(variables):
            found[variable] = dict(package=aliases[call[1]][len(module)+1:],
                symbol=call[2], slot=slot, args=args, position=match.start())
    shadowed = set()
    for match in re.finditer(r'\bfunc\s*(?:\w+\s*)?\(', code):
        end = _go_close(code, match.end()-1)
        if end is not None:
            for part in _go_split(code[match.end():end]):
                parameter = re.match(r'^(\w+)\s+\S', part)
                if parameter: shadowed.add(parameter[1])
    return {v: record for v, record in found.items()
            if writes[v] == 1 and v != '_' and v not in shadowed}


def _go_async_complete(rows, entries):
    """Stop reading package siblings once the explicit lifecycle is located."""
    from contractedge.retrieval.gap_delivery import _go_code
    for name, entry in entries.items():
        constructors = [u for _,_,u in rows if u.get('symbol') == name and not u.get('truncated')]
        sig = _go_signature(constructors[0]) if len(constructors) == 1 else None
        if not sig or not sig[1] or not sig[1][0]: return False
        owner = sig[1][0]; methods = {}
        for _,_,u in rows:
            match = re.match(r'\s*func\s*\(\s*(\w+)\s+\*?'+re.escape(owner)+r'\s*\)\s*(\w+)\s*\(',u['text'])
            if match and not u.get('truncated'): methods[match[2]] = (match[1], u)
        required = set(entry['methods'])
        for method in entry['async_methods']:
            if method not in methods: return False
            receiver, unit = methods[method]
            called = list(dict.fromkeys(re.findall(r'\b'+re.escape(receiver)+r'\.(\w+)\s*\(',_go_code(unit['text']))))
            required.update(n for n in called[:2] if n != method)
        if not required <= methods.keys(): return False
    return True


def _go_field_dependencies(scanned, bindings, audit):
    """Literal argument -> constructor field -> concrete factory return.

    Uses declarations already read under the existing shared scan budget. This
    locates candidates only; build tags, dispatch, readiness and causality are
    not inferred. Ambiguous/indirect assignments are deliberately unsupported.
    """
    from contractedge.retrieval.gap_delivery import _go_code
    output = []
    for binding in bindings:
        rows = scanned.get(binding['package'], [])
        constructors = [r for r in rows if r[2].get('symbol') == binding['symbol'] and not r[2].get('truncated')]
        if len(constructors) != 1: continue
        signature = _go_signature(constructors[0][2])
        if not signature: continue
        names, returns, code = signature
        if binding['slot'] >= len(returns) or not returns[binding['slot']]: continue
        owner = returns[binding['slot']]
        literals = list(re.finditer(r'&?\b' + re.escape(owner) + r'\s*\{', code))
        if len(literals) != 1: continue
        literal = literals[0]; close = _go_close(code, literal.end()-1)
        if close is None: continue
        prefix = code[:literal.start()]
        direct = bool(re.search(r'\breturn\s*$', prefix))
        assigned = re.search(r'\b(\w+)\s*(?::=|=)\s*$', prefix)
        if not direct and not (assigned and re.search(r'\breturn\s+'+re.escape(assigned[1])+r'\b',code[close+1:])):
            continue
        if len(re.findall(r'\breturn\b', code)) != 1: continue
        if assigned and (len(re.findall(r'(?<![\w.])'+re.escape(assigned[1])+r'\s*(?::=|=(?!=))',code)) != 1
                         or re.search(r'\b'+re.escape(assigned[1])+r'\.\w+\s*=(?!=)',code)):
            continue
        fields = {}
        for part in _go_split(code[literal.end():close]):
            pair = re.fullmatch(r'(\w+)\s*:\s*(\w+)', part)
            if pair and pair[2] in names: fields[pair[1]] = names.index(pair[2])
        requested = set()
        for _, _, unit in rows:
            receiver = re.match(r'\s*func\s*\(\s*(\w+)\s+\*?'+re.escape(owner)+r'\s*\)\s*(\w+)\s*\(',unit['text'])
            if receiver and receiver[2] in binding['methods']:
                requested.update(re.findall(r'\b'+re.escape(receiver[1])+r'\.(\w+)\.(\w+)\s*\(',_go_code(unit['text'])))
        for field, method in sorted(requested):
            index = fields.get(field)
            if index is None or index >= len(binding['args']): continue
            factory = binding['factories'].get(binding['args'][index])
            if not factory or factory['position'] >= binding['position']:
                audit.append(dict(result='FIELD_FACTORY_BINDING_UNRESOLVED', caller_path=binding['caller_path'],
                    constructor=binding['symbol'], field=field, argument=binding['args'][index], method=method))
                continue
            candidates = scanned.get(factory['package'], [])
            definitions = [r for r in candidates if r[2].get('symbol') == factory['symbol'] and not r[2].get('truncated')]
            sig = _go_signature(definitions[0][2]) if len(definitions) == 1 else None
            if not sig or factory['slot'] >= len(sig[1]):
                audit.append(dict(result='FIELD_FACTORY_DECLARATION_UNRESOLVED', package=factory['package'],
                    factory=factory['symbol'], field=field, method=method))
                continue
            concrete = sig[1][factory['slot']]
            if not concrete or not any(re.match(r'\s*type\s+'+re.escape(concrete)+r'\s+struct\b',r[2]['text']) for r in candidates):
                audit.append(dict(result='FIELD_RECEIVER_TYPE_UNRESOLVED', package=factory['package'],
                    factory=factory['symbol'], field=field, method=method))
                continue
            matches = [r for r in candidates if not r[2].get('truncated') and re.match(
                r'\s*func\s*\(\s*\w+\s+\*?'+re.escape(concrete)+r'\s*\)\s*'+re.escape(method)+r'\s*\(',r[2]['text'])]
            if len(matches) != 1:
                audit.append(dict(result='FIELD_RECEIVER_IMPLEMENTATION_UNRESOLVED',field=field,method=method,package=factory['package']));continue
            link = dict(caller_path=binding['caller_path'], constructor=binding['symbol'],
                field=field, argument=binding['args'][index], factory=factory['symbol'],
                factory_return_slot=factory['slot'], receiver_type=concrete, method=method)
            output.append((*matches[0], link))
    return output


def local_go_helper_support(snapshot, targets, operations, *, max_bytes=24000,
                            max_scan_files=128, max_scan_bytes=4*1024*1024,
                            max_records=8, already_supplied=None):
    """Read-only implementation context, never a causal claim or edit grant."""
    if type(max_records) is not int or not 0 <= max_records <= 8:
        raise ValueError('Helper output limit must be between zero and eight')
    from contractedge.retrieval.gap_delivery import _go_code, _go_import_specs
    root = Path(snapshot.source_root).resolve()
    if 'go.mod' not in snapshot.file_digests: return [], []
    module_path = root/'go.mod'
    if module_path.is_symlink() or module_path.stat().st_size > 64000: return [], []
    module_raw = module_path.read_bytes()
    if hashlib.sha256(module_raw).hexdigest() != snapshot.file_digests['go.mod']: return [], []
    module = re.search(r'(?m)^module\s+(\S+)', module_raw.decode(errors='replace'))
    if not module: return [], []
    module = module[1]; queries = set(); audit = []; result = []; async_queries = {}; bindings = []; input_packages = set()
    receiver_queries = {}; receiver_links = {}
    for target in targets:
        if not target['path'].endswith('.go') or not target['content']: continue
        text = target['content']
        aliases = {alias:path for alias,path in _go_import_specs(text) if alias not in {'_', '.'} and path.startswith(module+'/')}
        # Locators use complete-file coordinates, never display-window offsets.
        # Follow explicit receiver calls only; availability is not causality.
        current = hashlib.sha256(text.encode()).hexdigest() == snapshot.file_digests.get(target['path'])
        code_lines = _go_code(text).splitlines()
        declarations = {i+1 for i,line in enumerate(code_lines) if re.match(r'\s*func\s*\(', line)}
        method_units = [u for u in parse_source_units(target['path'], text, snapshot.snapshot_id,
            max_lines=768, max_chars=32000) if current and not u.get('truncated')
            and u['locator']['start_line'] in declarations]
        package_name = re.search(r'(?m)^\s*package\s+(\w+)\b', _go_code(text))
        for operation in operations:
            selected = operation['target']
            if selected.get('path') != target['path']: continue
            loc = selected.get('locator', {}); start, end = loc.get('start_line'), loc.get('end_line')
            if type(start) is not int or type(end) is not int: continue
            body = '\n'.join(text.splitlines()[max(0,start-1):end]); code = _go_code(body)
            for unit in method_units:
                loc = unit['locator']
                if not package_name or end < loc['start_line'] or start > loc['end_line']:
                    continue
                method_code = _go_code(unit['text'])
                receiver = re.match(r'\s*func\s*\(\s*(\w+)\s+\*?(\w+)\s*\)\s*(\w+)\s*\(', method_code)
                if not receiver:
                    continue
                variable, owner, caller = receiver.groups()
                tail = method_code[receiver.end():]
                # Local rebinding or closure parameters can shadow a receiver.
                lhs = r'(?<![\w.])(?:\w+\s*,\s*)*' + re.escape(variable) + r'(?:\s*,\s*\w+)*'
                shadowed = bool(re.search(lhs + r'\s*(?::=|=(?!=))', tail)
                    or re.search(r'\bvar\s+(?:\w+\s*,\s*)*'+re.escape(variable)+r'\b', tail)
                    or any(re.search(r'(?m)^\s*(?:\w+\s*,\s*)*'+re.escape(variable)+r'\b', block)
                           for block in re.findall(r'\bvar\s*\(([\s\S]*?)\)', tail)))
                for function in re.finditer(r'\bfunc\s*\(', tail):
                    close = _go_close(tail, function.end()-1)
                    if close is None or any(re.match(r'(?:\w+\s*,\s*)*'+re.escape(variable)+r'\b', part)
                            for part in _go_split(tail[function.end():close])):
                        shadowed = True
                    # Named result parameters bind identifiers inside the
                    # closure just as input parameters do.
                    if close is not None:
                        returned = tail[close+1:].lstrip()
                        result_end = _go_close(returned, 0) if returned.startswith('(') else None
                        if result_end is not None and any(re.match(
                                r'(?:\w+\s*,\s*)*'+re.escape(variable)+r'\b', part)
                                for part in _go_split(returned[1:result_end])):
                            shadowed = True
                if shadowed:
                    audit.append(dict(path=target['path'], receiver=variable, result='HELPER_LOCAL_CALL_SHADOWED'))
                    continue
                called_code = '\n'.join(code_lines[max(start, loc['start_line'])-1:min(end, loc['end_line'])])
                for call in re.finditer(r'\b'+re.escape(variable)+r'\s*\.\s*(\w+)\s*\(', called_code):
                    if called_code[:call.start()].rstrip().endswith('.') or call[1] == caller:
                        continue
                    key = (str(PurePosixPath(target['path']).parent), package_name[1], owner, call[1])
                    receiver_queries.setdefault(key, []).append(dict(caller_path=target['path'],
                        caller_symbol=caller, receiver_type=owner, method=call[1]))
            for alias,symbol in re.findall(r'\b(\w+)\.(\w+)\s*\(',code):
                if alias in aliases: queries.add((aliases[alias][len(module)+1:], symbol))
            factories = _go_initializers(code, aliases, module)
            # Retain the original explicit asynchronous-root policy; tuple
            # factory returns are followed only as arguments of these roots.
            assignments = {}
            for match in re.finditer(r'\b(\w+)\s*(?::=|=)\s*(\w+)\.(\w+)\s*\(',code):
                variable,alias,constructor=match.groups()
                if alias in aliases: assignments.setdefault(variable,set()).add((aliases[alias][len(module)+1:],constructor))
            for variable, choices in assignments.items():
                methods = {m[1] for m in re.finditer(r'\bgo\s+'+re.escape(variable)+r'\.(\w+)\s*\(',code)}
                if not methods: continue
                if len(choices)!=1:
                    audit.append(dict(path=target['path'],receiver=variable,result='AMBIGUOUS_LOCAL_CONSTRUCTION'));continue
                writes=re.findall(r'(?<![\w.])'+re.escape(variable)+r'\s*(?::=|=(?!=))',code)
                constructions=re.findall(r'(?<![\w.])'+re.escape(variable)+r'\s*(?::=|=)\s*\w+\.\w+\s*\(',code)
                if len(writes)!=len(constructions):
                    audit.append(dict(path=target['path'],receiver=variable,result='ASYNC_RECEIVER_REASSIGNMENT'));continue
                package,constructor=next(iter(choices));entry=async_queries.setdefault(package,{})
                found=entry.setdefault(constructor,dict(async_methods=set(),methods=set()))
                found['async_methods'].update(methods)
                found['methods'].update(m[1] for m in re.finditer(r'\b'+re.escape(variable)+r'\.(\w+)\s*\(',code))
                if (variable in factories and hashlib.sha256(text.encode()).hexdigest()==snapshot.file_digests.get(target['path'])):
                    binding={**factories[variable], 'caller_path':target['path'],'factories':factories,'methods':set(methods)}
                    if (factories[variable]['position'] >= min(m.start() for m in re.finditer(r'\bgo\s+'+re.escape(variable)+r'\.\w+\s*\(',code))
                            or re.search(r'\b'+re.escape(variable)+r'\.\w+\s*=(?!=)',code)):
                        continue
                    bindings.append(binding)
                    input_packages.update(factories[a]['package'] for a in binding['args'] if a in factories)
    files, scanned_bytes = 0, 0; scanned = {}; scan_complete = {}; queued = []
    receiver_packages = {key[0] for key in receiver_queries}
    target_paths = {t['path'] for t in targets}
    def scan_package(package):
        nonlocal files, scanned_bytes
        if package in scanned:
            return scanned[package]
        async_entry=async_queries.get(package,{})
        paths=[p for p in sorted(snapshot.file_digests) if str(PurePosixPath(p).parent)==package and p.endswith('.go') and not p.endswith('_test.go')
               and (p not in target_paths or package in receiver_packages)]
        candidates=[]
        complete = True
        for path in paths:
            if files>=max_scan_files or scanned_bytes>=max_scan_bytes:
                complete = False
                break
            candidate=root/path
            if (candidate.is_symlink() or not candidate.resolve().is_relative_to(root)
                    or any(p.is_symlink() for p in candidate.parents if p!=root and p.is_relative_to(root))):
                complete = False
                audit.append(dict(path=path,result='HELPER_SOURCE_UNAVAILABLE'));continue
            size=candidate.stat().st_size
            if scanned_bytes+size>max_scan_bytes:
                complete = False
                audit.append(dict(path=path,result='HELPER_SCAN_BYTE_LIMIT'));continue
            files+=1
            with candidate.open('rb') as stream:
                raw=stream.read(size)
            scanned_bytes+=len(raw)
            if len(raw)!=size or candidate.stat().st_size!=size or hashlib.sha256(raw).hexdigest()!=snapshot.file_digests[path]:
                complete = False
                audit.append(dict(path=path,result='SOURCE_CHANGED_AFTER_SNAPSHOT'));continue
            text=raw.decode(errors='replace');lines=text.splitlines(keepends=True)
            long_body = async_entry or package in input_packages or package in receiver_packages
            for unit in parse_source_units(path,text,snapshot.snapshot_id,max_lines=768 if long_body else 80,max_chars=32000 if long_body else 16000):
                candidates.append((path,lines,unit))
            if async_entry and package not in receiver_packages and _go_async_complete(candidates, async_entry): break
        scanned[package]=candidates
        scan_complete[package]=complete
        return candidates

    for (package, package_name, owner, method), callers in sorted(receiver_queries.items()):
        candidates = scan_package(package)
        matches = []
        for path, lines, unit in candidates:
            if unit.get('symbol') != method:
                continue
            full_code = _go_code(''.join(lines))
            declared_package = re.search(r'(?m)^\s*package\s+(\w+)\b', full_code)
            actual_line = full_code.splitlines()[unit['locator']['start_line']-1]
            pattern = r'\s*func\s*\(\s*\w+\s+\*?'+re.escape(owner)+r'\s*\)\s*'+re.escape(method)+r'\s*\('
            if declared_package and declared_package[1] == package_name and re.match(pattern, actual_line):
                matches.append((path, lines, unit))
        if not scan_complete[package] or len(matches) != 1 or matches[0][2].get('truncated'):
            audit.append(dict(package=package, receiver_type=owner, symbol=method,
                              result='HELPER_RECEIVER_IMPLEMENTATION_UNRESOLVED'))
            continue
        path, lines, unit = matches[0]
        queued.append((-1, path, lines, unit, None))
        receiver_links[(path, unit['id'])] = ('DIRECT_SAME_RECEIVER_CALL', dict(callers=callers))

    for package in sorted({p for p,_ in queries},key=lambda p:(p not in async_queries,p not in input_packages,p)):
        symbols={s for p,s in queries if p==package};async_entry=async_queries.get(package,{})
        candidates=scan_package(package);receiver_types={}
        for _,_,unit in candidates:
            if unit.get('symbol') not in async_entry or unit.get('truncated'):continue
            signature=_go_signature(unit)
            if signature and signature[1] and signature[1][0]:receiver_types[unit['symbol']]=signature[1][0]
            else:
                # Preserve the old single-return lifecycle locator even when
                # parameter positions cannot be bound by the stricter parser.
                legacy=re.search(r'\bfunc\s+'+re.escape(unit['symbol'])+r'\s*\([\s\S]*?\)\s*\*?(\w+)\s*\{',unit['text'])
                if legacy:receiver_types[unit['symbol']]=legacy[1]
        for constructor in async_entry:
            if constructor not in receiver_types:audit.append(dict(package=package,symbol=constructor,result='ASYNC_RETURN_TYPE_UNRESOLVED'))
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
        for _,_,unit in candidates:
            if priority(unit)!=0:continue
            receiver=re.match(r'\s*func\s*\(\s*(\w+)\s+\*?(\w+)\s*\)\s*(\w+)\s*\(',unit['text'])
            called=list(dict.fromkeys(re.findall(r'\b'+re.escape(receiver[1])+r'\.(\w+)\s*\(',_go_code(unit['text']))))
            inner_methods.update((receiver[2],name) for name in called[:2] if name!=receiver[3])
        for binding in bindings:
            if binding['package']==package:binding['methods'].update(name for typ,name in inner_methods if typ==receiver_types.get(binding['symbol']))
        for path,lines,unit in candidates:
            rank=priority(unit)
            if (rank==4 and unit.get('symbol') not in symbols) or unit.get('truncated'):continue
            if rank==4 and unit['locator']['end_line']-unit['locator']['start_line']>=80:continue
            queued.append((rank,path,lines,unit,None))
    queued.extend((1,path,lines,unit,link) for path,lines,unit,link in _go_field_dependencies(scanned,bindings,audit))
    # A helper's input type is part of its calling contract. Locate only simple
    # concrete local types and methods literally mentioned by that helper; this
    # remains lexical context rather than inferred dispatch or type checking.
    contract_links=dict(receiver_links)
    for rank,path,lines,unit,link in tuple(queued):
        if rank!=4 or unit.get('truncated'):
            continue
        code=_go_code(unit['text'])
        signature=re.match(r'\s*func\s+(?:\([^)]*\)\s*)?'+re.escape(unit.get('symbol',''))+r'\s*\(',code)
        end=_go_close(code,signature.end()-1) if signature else None
        if end is None:
            continue
        aliases={alias:import_path[len(module)+1:] for alias,import_path in _go_import_specs(''.join(lines))
                 if alias not in {'_', '.'} and import_path.startswith(module+'/')}
        mentioned_methods=set(re.findall(r'\.\s*(\w+)\s*\(',code[end+1:]))
        own_package=str(PurePosixPath(path).parent)
        parameters=_go_split(code[signature.end():end])
        bound_names={match[1] for part in parameters if (match:=re.match(r'(\w+)',part))}
        body=code[end+1:]
        for match in re.finditer(r'(?<![\w.])(\w+(?:\s*,\s*\w+)*)\s*:=',body):
            bound_names.update(name.strip() for name in match[1].split(','))
        for names in re.findall(r'\b(?:var|const|type)\s+(\w+(?:\s*,\s*\w+)*)',body):
            bound_names.update(name.strip() for name in names.split(','))
        for block in re.findall(r'\b(?:var|const)\s*\(([\s\S]*?)\)',body):
            for names in re.findall(r'(?m)^\s*(\w+(?:\s*,\s*\w+)*)',block):
                bound_names.update(name.strip() for name in names.split(','))
        for function in re.finditer(r'\bfunc\s*\(',body):
            close=_go_close(body,function.end()-1)
            if close is not None:
                bound_names.update(match[1] for part in _go_split(body[function.end():close])
                                   if (match:=re.match(r'(\w+)',part)))
        found_input=False
        for parameter in parameters:
            named=re.fullmatch(r'(\w+)\s+((?:\[\]|\*|\.\.\.)*(?:(\w+)\.)?(\w+))',parameter)
            if not named or named[1]=='_':
                continue
            alias,type_name=named[3],named[4]
            package=aliases.get(alias) if alias else own_package
            if package is None:
                continue
            rows=scan_package(package)
            declarations=[row for row in rows if re.match(
                r'\s*type\s+'+re.escape(type_name)+r'\s+struct\b',_go_code(row[2]['text']))]
            if len(declarations)!=1 or declarations[0][2].get('truncated'):
                audit.append(dict(result='HELPER_INPUT_CONTRACT_UNRESOLVED',helper=unit['symbol'],
                                  package=package,type_name=type_name))
                continue
            found_input=True
            dependency=dict(helper_path=path,helper_symbol=unit['symbol'],package=package,type_name=type_name)
            type_path,type_lines,type_unit=declarations[0]
            queued.append((5,type_path,type_lines,type_unit,None))
            contract_links[(type_path,type_unit['id'])]=('LOCAL_HELPER_INPUT_TYPE',dependency)
            for method in sorted(mentioned_methods):
                implementations=[row for row in rows if re.match(
                    r'\s*func\s*\(\s*\w+\s+\*?'+re.escape(type_name)+r'\s*\)\s*'+re.escape(method)+r'\s*\(',
                    _go_code(row[2]['text']))]
                if len(implementations)!=1 or implementations[0][2].get('truncated'):
                    continue
                method_path,method_lines,method_unit=implementations[0]
                queued.append((6,method_path,method_lines,method_unit,None))
                contract_links[(method_path,method_unit['id'])]=('LOCAL_HELPER_INPUT_METHOD',{**dependency,'method':method})
        if found_input:
            calls=set(re.findall(r'(?<![\w.])(\w+)\s*\(',code[end+1:]))
            for name in sorted(calls-{unit['symbol']}):
                if name in bound_names:
                    audit.append(dict(result='HELPER_LOCAL_CALL_SHADOWED',helper=unit['symbol'],name=name))
                    continue
                matches=[row for row in scanned.get(own_package,[]) if re.match(
                    r'\s*func\s+'+re.escape(name)+r'\s*\(',_go_code(row[2]['text']))]
                if len(matches)!=1 or matches[0][2].get('truncated'):
                    continue
                call_path,call_lines,call_unit=matches[0]
                queued.append((7,call_path,call_lines,call_unit,None))
                contract_links[(call_path,call_unit['id'])]=('LOCAL_HELPER_LOCAL_CALL',
                    dict(helper_path=path,helper_symbol=unit['symbol'],callee=name))
    included_bytes=0;seen=set()
    for rank,path,lines,unit,link in sorted(queued,key=lambda row:(row[0],row[1],row[3]['locator']['start_line'],row[4] is None)):
        loc=unit['locator'];start=loc['start_line']-1
        while start>max(0,loc['start_line']-13) and lines[start-1].lstrip().startswith('//'):start-=1
        excerpt=''.join(lines[start:loc['end_line']]);digest=hashlib.sha256(excerpt.encode()).hexdigest()
        if (path,digest) in seen:continue
        size=len(excerpt.encode())
        contract=contract_links.get((path,unit['id']))
        basis=contract[0] if contract else 'LOCAL_CONSTRUCTOR_FIELD_RECEIVER' if link else 'LOCAL_CONSTRUCTOR_ASYNC_RECEIVER' if rank<4 else 'DIRECT_LOCAL_IMPORT'
        origin=('Local helper input type/method or direct local callee; exact read-only declaration, not inferred dispatch, causality or edit permission' if contract else
                'Explicit constructor argument/field/return link; read-only concrete-method candidate, not verified dispatch, readiness, causality or edit permission' if link else
                'Explicit local construction and asynchronous receiver call; read-only lexical hint, not readiness or verified dispatch/causality/edit permission' if rank<4 else
                'Direct qualified call in accepted target; local Go import lexical lookup; read-only, not a verified causal edge or edit permission')
        record=_helper_source_record(snapshot,path,dict(start_line=start+1,end_line=loc['end_line']),excerpt,origin)
        # Coverage is checked on the final exact record before charging the
        # shared output budget. Already delivered source must not crowd out a
        # different callee; scan limits remain independent and unchanged.
        if already_supplied is not None and already_supplied(record):
            seen.add((path,digest))
            audit.append(dict(path=path,symbol=unit['symbol'],result='ALREADY_DELIVERED_HELPER'))
            continue
        if included_bytes+size>max_bytes or len(result)>=max_records:
            audit.append(dict(path=path,symbol=unit['symbol'],result='READ_ONLY_SUPPORT_BUDGET'));continue
        included_bytes+=size;seen.add((path,digest))
        result.append(record)
        audit.append(dict(id=record['id'],path=path,symbol=unit['symbol'],result='INCLUDED_READ_ONLY_HELPER',basis=basis,
            lifecycle_role=None if contract else 'FIELD_INPUT' if link else ('ASYNC_ENTRY' if rank==0 else 'ASYNC_SUPPORT') if rank<4 else None,
            declaration_locator=dict(loc),declaration_content_digest=hashlib.sha256(unit['text'].encode()).hexdigest(),
            **({'input_contract_link':contract[1]} if contract else {}),**({'field_link':link} if link else {})))
    audit.append(dict(result='HELPER_SCAN_TOTALS',indexed_files=files,indexed_bytes=scanned_bytes))
    return result,audit


def planning_helper_source_support(snapshot, selected, *, max_bytes=24000, source_context=None):
    """Fresh selected caller contracts before a plan exists; read-only only.

    Reuse the existing helper scanner and its ambiguity/type/record limits.
    The caller seeds have an independent finite full-file read limit; no
    generated plan, inferred causal edge or expanded edit scope is required.
    """
    from contractedge.retrieval.gap_delivery import _go_code,_go_import_specs
    from contractedge.diagnosis.evidence import source_context_records
    from contractedge.model.budget import BudgetExceeded
    adopted=source_context_records(source_context, producer='planning_helper')
    byte_limit=max(0,min(int(max_bytes),24000))
    adopted_bytes=sum(len(r['excerpt'].encode()) for r in adopted)
    if len(adopted)>8 or adopted_bytes>byte_limit:
        raise BudgetExceeded('Adopted planning helpers exceed the explicit support budget')
    if len(adopted)==8 or adopted_bytes==byte_limit:
        return adopted, []
    known=source_context_records(source_context)
    def already_supplied(record):
        # Repeated discovery is not a new adoption. Identity includes source
        # coordinates and namespace; identical prose in another file is not
        # an interchangeable helper contract.
        return any(all(record.get(k)==r.get(k) for k in
            ('kind','snapshot_id','locator','content_digest','excerpt')) for r in known)
    root=Path(snapshot.source_root).resolve()
    targets={};operations=[];audit=[];used=0
    for seed in selected:
        path=seed.get('path','')
        if (seed.get('kind')!='FUNCTION_OR_TYPE' or not path.endswith('.go')
                or path.endswith('_test.go') or seed.get('truncated')):
            continue
        if seed.get('snapshot_id')!=snapshot.snapshot_id:
            audit.append(dict(path=path,result='STALE_SOURCE_SEED'));continue
        source=root/path
        if (path not in snapshot.file_digests or source.is_symlink()
                or not source.is_file() or not source.resolve().is_relative_to(root)
                or any(p.is_symlink() for p in source.parents if p!=root and p.is_relative_to(root))):
            audit.append(dict(path=path,result='SOURCE_SEED_UNAVAILABLE'));continue
        if path not in targets:
            size=source.stat().st_size
            if len(targets)>=4 or size>512*1024 or used+size>2*1024*1024:
                audit.append(dict(path=path,result='SOURCE_SEED_BUDGET'));continue
            with source.open('rb') as stream:raw=stream.read(size)
            used+=len(raw)
            if (len(raw)!=size or source.stat().st_size!=size
                    or hashlib.sha256(raw).hexdigest()!=snapshot.file_digests[path]):
                audit.append(dict(path=path,result='SOURCE_CHANGED_AFTER_SNAPSHOT'));continue
            targets[path]=dict(path=path,content=raw.decode(errors='replace'))
        loc=seed.get('locator',{});start=loc.get('start_line');end=loc.get('end_line')
        lines=targets[path]['content'].splitlines(keepends=True)
        if type(start) is not int or type(end) is not int or not 1<=start<=end<=len(lines):
            audit.append(dict(path=path,result='SOURCE_SEED_BODY_MISMATCH'));continue
        exact=''.join(lines[start-1:end])
        if (exact!=seed.get('text') or hashlib.sha256(exact.encode()).hexdigest()!=seed.get('content_digest')):
            audit.append(dict(path=path,result='SOURCE_SEED_BODY_MISMATCH'));continue
        aliases={a for a,_ in _go_import_specs(targets[path]['content']) if a not in {'_','.'}}
        if not any(a in aliases for a,_ in re.findall(r'\b(\w+)\.(\w+)\s*\(',_go_code(exact))):
            continue
        operations.append(dict(target=dict(path=path,locator=dict(loc))))
    if not operations:return adopted,audit
    caller_paths={op['target']['path'] for op in operations}
    records,lookup=local_go_helper_support(snapshot,[t for p,t in targets.items() if p in caller_paths],operations,
        max_bytes=byte_limit-adopted_bytes, max_records=8-len(adopted),
        already_supplied=already_supplied if source_context is not None else None)
    return [*adopted,*records],[*audit,*lookup]


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
    lifecycle_ids = {r['id'] for r in lookup if r.get('basis') in {'LOCAL_CONSTRUCTOR_ASYNC_RECEIVER', 'LOCAL_CONSTRUCTOR_FIELD_RECEIVER'}}
    entry_ids = {r['id'] for r in lookup if r.get('lifecycle_role') in {'ASYNC_ENTRY', 'FIELD_INPUT'}}
    declarations = {r['id']:r for r in lookup if r.get('declaration_locator')}
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
            declaration_locator=declarations[record['id']]['declaration_locator'],
            declaration_content_digest=declarations[record['id']]['declaration_content_digest'],
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


def local_helper_support(snapshot, targets, operations, *, max_bytes=24000,
                         max_records=8, already_supplied=None):
    """Shared finite budget for Go and directly imported Python helper bodies."""
    import ast
    result,audit=local_go_helper_support(snapshot,targets,operations,max_bytes=max_bytes,
        max_records=max_records,already_supplied=already_supplied)
    remaining=max_bytes-sum(len(r['excerpt'].encode()) for r in result)
    root=Path(snapshot.source_root).resolve();queries=set();scanned=used=0
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
        if len(result)>=max_records or scanned>=128:break
        if path not in snapshot.file_digests or path in {t['path'] for t in targets}:continue
        candidate=root/path
        if (candidate.is_symlink() or not candidate.resolve().is_relative_to(root)
                or any(p.is_symlink() for p in candidate.parents if p!=root and p.is_relative_to(root))):
            audit.append({'path':path,'result':'HELPER_SOURCE_UNAVAILABLE'});continue
        size=candidate.stat().st_size
        if used+size>4*1024*1024:audit.append({'path':path,'result':'HELPER_SCAN_BYTE_LIMIT'});continue
        scanned+=1
        with candidate.open('rb') as stream:raw=stream.read(size)
        used+=len(raw)
        if (len(raw)!=size or candidate.stat().st_size!=size
                or hashlib.sha256(raw).hexdigest()!=snapshot.file_digests[path]):
            audit.append({'path':path,'result':'SOURCE_CHANGED_AFTER_SNAPSHOT'});continue
        text=raw.decode(errors='replace')
        matches=[unit for unit in parse_source_units(path,text,snapshot.snapshot_id)
                 if unit.get('symbol')==symbol]
        if len(matches)!=1 or matches[0].get('truncated'):
            audit.append({'path':path,'symbol':symbol,'result':'HELPER_IMPLEMENTATION_UNRESOLVED'});continue
        for unit in matches:
            loc=unit['locator'];excerpt=''.join(text.splitlines(keepends=True)[loc['start_line']-1:loc['end_line']])
            digest=hashlib.sha256(excerpt.encode()).hexdigest()
            record=_helper_source_record(snapshot,path,loc,excerpt,
                'Direct imported local Python call in accepted target; read-only, not verified causality or edit permission')
            if already_supplied is not None and already_supplied(record):
                audit.append({'path':path,'symbol':symbol,'result':'ALREADY_DELIVERED_HELPER'});continue
            if len(excerpt.encode())>remaining:
                audit.append({'path':path,'symbol':symbol,'result':'READ_ONLY_SUPPORT_BUDGET'});continue
            remaining-=len(excerpt.encode())
            result.append(record);audit.append({'id':record['id'],'path':path,'symbol':symbol,'result':'INCLUDED_READ_ONLY_HELPER'})
    return result,audit
