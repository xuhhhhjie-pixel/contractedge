"""Finite local construction/caller context from already verified source reads.

This is source location, not a call graph or a proof of dispatch/data flow.
No file is opened here; the acquisition owner supplies its bounded read set.
"""
import re
from pathlib import PurePosixPath

from contractedge.source.parsers import mask_go_code, parse_source_units


_PLATFORM = re.compile(r'_(?:aix|android|darwin|dragonfly|freebsd|illumos|ios|js|linux|netbsd|openbsd|plan9|solaris|wasip1|windows|386|amd64|arm|arm64|loong64|mips|mips64|mips64le|mipsle|ppc64|ppc64le|riscv64|s390x|wasm)\.go$')


def _signature_uses_type(signature, name):
    """Simple named/unnamed parameter or return types, not variable names."""
    token = re.escape(name) + r'\b'
    shape = r'(?:\[\]\s*)?\*?' + token
    return bool(re.search(r'\b\w+(?:\s*,\s*\w+)*\s+' + shape + r'(?=\s*[,)]|\s*$)', signature)
                or re.search(r'[(,]\s*' + shape + r'(?=\s*[,)]|\s*$)', signature)
                or re.search(r'\)\s*' + shape + r'\s*$', signature))


def bounded_go_context(snapshot, texts, seeds, gaps, *, module_text=None, max_units=6,
                       max_lines=512, max_chars=16000):
    from contractedge.retrieval.gap_delivery import (_go_declaration, _go_direct_calls,
        _go_import_specs, _go_import_declaration_spans, _go_identifier_shadowed)

    limit = min(6, max(0, max_units))
    output, audit, parsed = [], [], {}
    if not limit:
        return output, audit

    def production(path):
        return (path.endswith('.go') and not path.endswith('_test.go')
                and not set(PurePosixPath(path).parts) & {'test', 'tests', 'testdata', 'vendor'})

    by_directory = {}
    for path in snapshot.file_digests:
        if production(path):
            by_directory.setdefault(str(PurePosixPath(path).parent), []).append(path)

    def package(directory):
        paths = by_directory.get(directory, [])
        if not paths or any(path not in texts for path in paths):
            return None
        names = set()
        for path in paths:
            text = texts[path]
            if '//go:build' in text or '// +build' in text or _PLATFORM.search(path):
                return None
            name = re.search(r'(?m)^\s*package\s+([A-Za-z_]\w*)\b', mask_go_code(text))
            if name is None:
                return None
            names.add(name[1])
        return next(iter(names)) if len(names) == 1 else None

    def declarations(path):
        if path not in parsed:
            # Initial retrieval retains its historical lexical references.
            # This bounded resolver independently excludes declarations whose
            # apparent start is actually inside a comment or string literal.
            code = mask_go_code(texts[path])
            starts = {code.count('\n', 0, match.start()) + 1 for match in re.finditer(
                r'(?m)^[ \t]*(?:func\b|type\s+\w+\s+(?:struct|interface)\s*\{)', code)
                if code[:match.start()].count('{') == code[:match.start()].count('}')}
            parsed[path] = [unit for unit in parse_source_units(path, texts[path], snapshot.snapshot_id,
                max_lines=max_lines, max_chars=max_chars) if unit['locator']['start_line'] in starts]
        return [u for u in parsed[path] if u.get('symbol') and not u.get('truncated')
                and u.get('kind') == 'FUNCTION_OR_TYPE']

    def functions(directory):
        return [(u, *_go_declaration(u['text'])) for path in sorted(by_directory.get(directory, []))
                for u in declarations(path) if _go_declaration(u['text'])[1] is not None]

    def add(unit, gap_id, basis, depth):
        old = next((u for u in output if u['id'] == unit['id']), None)
        if old is not None:
            old['evidence_gap_refs'] = sorted(set(old['evidence_gap_refs']) | {gap_id})
            old['gap_role_keys'] = [g + ':context' for g in old['evidence_gap_refs']]
            return
        if len(output) >= limit:
            return
        output.append({**unit, 'evidence_gap_refs': [gap_id], 'gap_role_keys': [gap_id + ':context'],
            'optional_gap_context': True, 'anchor_priority': 4, 'hop_distance': depth,
            'scope_extension': 'BOUNDED_GO_CONSTRUCTION_CONTEXT',
            'association_status': basis + '; source context only, not verified dispatch, causality or edit permission'})
        audit.append(dict(gap_id=gap_id, path=unit['path'], unit_id=unit['id'], basis=basis))

    module = None
    dependency_prefixes = set()
    if module_text and 'go.work' not in snapshot.file_digests:
        match = re.search(r'(?m)^\s*module\s+(\S+)\s*$', mask_go_code(module_text))
        if match:
            module = match[1]
            # Another selected/replaced module under the main import prefix
            # can own that subtree. Unrelated dependency replacements do not
            # prevent locating a different package in the main module.
            dependency_prefixes = set(re.findall(r'(?m)^\s*(?:(?:require|replace|exclude)\s+)?('
                + re.escape(module) + r'/[^\s()]+)\s+(?:v[^\s]+|=>)', mask_go_code(module_text)))

    owners = []
    for gap in gaps:
        hint = gap.get('locator_hint') or {}
        requested = str(hint.get('path', '')).rstrip('/')
        if (not requested or PurePosixPath(requested).is_absolute() or '..' in PurePosixPath(requested).parts
                or not (hint.get('symbol') or hint.get('search_term'))):
            continue
        for seed in seeds:
            path = seed.get('path', '')
            if (path not in texts or not production(path)
                    or not (path == requested or path.startswith(requested + '/'))
                    or seed.get('snapshot_id') != snapshot.snapshot_id):
                continue
            directory = str(PurePosixPath(path).parent)
            if package(directory) is None:
                continue
            # Re-find the exact seed declaration in verified current bytes.
            for unit in declarations(path):
                if unit['id'] != seed.get('id') or unit['content_digest'] != seed.get('content_digest'):
                    continue
                code, declaration = _go_declaration(unit['text'])
                structure = re.match(r'\s*type\s+(\w+)\s+struct\b', code)
                owner = declaration[2] if declaration else structure[1] if structure else None
                if not owner:
                    continue
                definitions = [u for p in by_directory[directory] for u in declarations(p)
                               if re.match(r'\s*type\s+' + re.escape(owner) + r'\s+struct\b', mask_go_code(u['text']))]
                key = (str(gap['id']), directory, owner)
                if len(definitions) == 1 and key not in owners:
                    owners.append(key)
    # At most two source-owned types per acquisition. Other scopes remain
    # ordinary retrieval candidates, without speculative type binding.
    for gap_id, directory, owner in owners[:2]:
        local = functions(directory)
        constructions = []
        for unit, code, declaration in local:
            opening = code.find('{', declaration.end())
            signature, body = code[:opening], code[opening:]
            if (re.search(r'\b(?:struct|interface)\b', signature)
                    or re.search(r'\bfunc\s*\(', body)
                    or _go_identifier_shadowed(owner, signature, body)):
                continue
            if any(not body[:m.start()].rstrip().endswith('.') for m in re.finditer(
                    r'(?<![\w.])' + re.escape(owner) + r'\s*\{', body)):
                constructions.append(unit)
        for unit in constructions[:2]:
            add(unit, gap_id, 'EXACT_SAME_PACKAGE_TYPE_CONSTRUCTION', 1)
        if not constructions or not module:
            continue
        # Resolve only this module's exact package import, not a same-named
        # receiver, other module, nested module, or a guessed dispatch target.
        if any(p != 'go.mod' and p.endswith('/go.mod') and
               (directory == str(PurePosixPath(p).parent) or directory.startswith(str(PurePosixPath(p).parent) + '/'))
               for p in snapshot.file_digests):
            continue
        imported = module + ('/' + directory if directory != '.' else '')
        if any(imported == prefix or imported.startswith(prefix + '/') for prefix in dependency_prefixes):
            continue
        free = {}
        for unit, code, declaration in local:
            if declaration[2] is None:
                free.setdefault(declaration[3], []).append(unit)
        # The package may expose many unrelated helpers. Locate entries with
        # explicit type context around the construction, not the first import
        # call in filename order. Two simple alias/field-containment steps are
        # source facts only; no element receiver or dynamic dispatch is bound.
        family = {owner}
        for unit in constructions:
            declaration = _go_declaration(unit['text'])[1]
            if declaration[2]:
                family.add(declaration[2])
        for _ in range(2):
            additions = set()
            for path in by_directory[directory]:
                code = mask_go_code(texts[path])
                for alias in re.finditer(r'(?m)^[ \t]*type\s+(\w+)\s+(?:=\s*)?(?:\[\]\s*)?\*?(\w+)[ \t]*(?:\n|$)', code):
                    if (alias[2] in family
                            and code[:alias.start()].count('{') == code[:alias.start()].count('}')):
                        additions.add(alias[1])
                for unit in declarations(path):
                    structure = re.match(r'\s*type\s+(\w+)\s+struct\s*\{', mask_go_code(unit['text']))
                    if structure:
                        fields = mask_go_code(unit['text'])[structure.end():].rsplit('}', 1)[0]
                        for line in fields.splitlines():
                            field = re.fullmatch(r'\s*(?:(?:\w+(?:\s*,\s*\w+)*)\s+)?(?:\[\]\s*)?\*?(\w+)\s*', line)
                            if field and field[1] in family:
                                additions.add(structure[1])
            family.update(additions)
        related = {u['id'] for u in constructions}
        for unit, code, declaration in local:
            if declaration[2] is not None:
                continue
            opening = code.find('{', declaration.end())
            signature, body = code[:opening], code[opening:]
            if any(_signature_uses_type(signature, name)
                   or (not _go_identifier_shadowed(name, signature, body) and
                       any(not body[:m.start()].rstrip().endswith('.') for m in re.finditer(
                       r'(?<![\w.])' + re.escape(name) + r'\s*\{', body))
                       )
                   for name in family):
                related.add(unit['id'])
        for _ in range(2):
            prior = set(related)
            related.update(unit['id'] for unit, _, declaration in local
                if declaration[2] is None and any(target_owner is None and len(free.get(name, [])) == 1
                    and free[name][0]['id'] in prior for target_owner, name in _go_direct_calls(unit['text'])))
        entries = []
        for path in sorted(texts):
            if not production(path) or str(PurePosixPath(path).parent) == directory:
                continue
            if package(str(PurePosixPath(path).parent)) is None:
                continue
            imports = _go_import_specs(texts[path])
            aliases = [alias for alias, name in imports if name == imported and alias not in {'_', '.'}
                       and sum(a == alias for a, _ in imports) == 1]
            if package(directory) != imported.rsplit('/', 1)[-1]:
                # The legacy import locator guesses an implicit alias from
                # the path. Only an explicit alias establishes that spelling
                # when the verified declaration uses another package name.
                literal_code = mask_go_code(texts[path], keep_strings=True)
                specs = [literal_code[start:end] for start, end in _go_import_declaration_spans(texts[path])]
                aliases = [alias for alias in aliases if any(re.search(
                    r'(?<!\w)' + re.escape(alias) + r'\s+"' + re.escape(imported) + r'"', spec)
                    for spec in specs)]
            for caller in declarations(path):
                code, declaration = _go_declaration(caller['text'])
                if declaration is None:
                    continue
                opening = code.find('{', declaration.end())
                signature, body = code[:opening], code[opening:]
                if re.search(r'\bfunc\s*\(', body):
                    continue
                for alias in aliases:
                    if _go_identifier_shadowed(alias, signature, body):
                        continue
                    for call in re.finditer(r'(?<![\w.])' + re.escape(alias) + r'\s*\.\s*(\w+)\s*\(', body):
                        if body[:call.start()].rstrip().endswith('.'):
                            continue
                        found = free.get(call[1], [])
                        if len(found) == 1 and found[0]['id'] in related:
                            entries.append((caller, found[0]))
        # Two exact external call sites, their declarations, and at most
        # two direct local callees. Unknown receivers are deliberately skipped.
        for caller, entry in entries[:2]:
            add(caller, gap_id, 'EXACT_LOCAL_MODULE_IMPORT_CALL', 1)
            add(entry, gap_id, 'EXACT_IMPORTED_FUNCTION_DECLARATION', 1)
            for target_owner, name in _go_direct_calls(entry['text'])[:2]:
                found = [u for u, _, decl in local if decl[2] == target_owner and decl[3] == name]
                if len(found) == 1 and found[0]['id'] in related:
                    add(found[0], gap_id, 'EXPLICIT_DIRECT_LOCAL_CALL_CONTEXT', 2)
    return output, audit
