from __future__ import annotations

from pathlib import Path
from itertools import zip_longest
import hashlib
import re
from typing import Any, Mapping

from contractedge.contracts.models import Snapshot
from contractedge.source.parsers import parse_source_units, _unit


def extract_units(snapshot: Snapshot, scope: Mapping[str, Any], *, max_units: int = 80,
                  max_lines: int = 80, max_chars: int = 16_000,
                  max_extension_files: int = 24, query: str | None = None,
                  focused_paths=(), declaration_index=None) -> list[dict[str, Any]]:
    """Produce bounded syntax-aware source units, with explicit text fallback provenance."""
    if max_units <= 0:
        return []
    root = Path(snapshot.source_root).resolve()
    paths = tuple(scope.get("seed_paths", ()))
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()
    seed_units: list[dict[str, Any]] = []

    def read_units(relative: str) -> list[dict[str, Any]]:
        if relative not in snapshot.file_digests:
            return []
        source_path = (root / relative).resolve()
        try:
            source_path.relative_to(root)
        except ValueError:
            return []
        if not source_path.is_file() or source_path.is_symlink():
            return []
        try:
            text = source_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        parsed = parse_source_units(relative, text, snapshot.snapshot_id,
                                    max_lines=max_lines, max_chars=max_chars)
        if relative in focused_paths and relative.endswith('.go') and query:
            from contractedge.retrieval.candidates import _terms
            terms=_terms(query)
            # The public entry file is already read. A relevant long callback
            # must not be represented only by its flag/header prefix until a
            # model happens to ask for an extension. Keep at most two bounded
            # complete declarations, never a repository-wide call graph.
            complete=parse_source_units(relative,text,snapshot.snapshot_id,max_lines=768,max_chars=32000)
            options=[]
            for unit in complete:
                prefixes=[p for p in parsed if p.get('symbol')==unit.get('symbol') and p.get('truncated')]
                relevance=len(terms&_terms(unit['text']))
                if prefixes and not unit.get('truncated') and unit.get('kind')=='FUNCTION_OR_TYPE' and relevance:
                    options.append((relevance,unit,prefixes[0]['id']))
            options.sort(key=lambda row:(-row[0],row[1]['locator']['start_line']))
            for _,unit,prefix in options[:2]:
                parsed=[p for p in parsed if not(p.get('symbol')==unit.get('symbol') and p.get('truncated'))]
                parsed.append({**unit,'expanded_from_unit_id':prefix,'anchor_priority':2,
                    'source_focus_basis':'BOUNDED_PUBLIC_ENTRY_DECLARATION_BODY; fresh source only, not a causal claim or edit permission'})
        if query:
            from contractedge.retrieval.candidates import _terms, literal_identifiers
            identifiers, terms = literal_identifiers(query), _terms(query)
            # Explicit public identifiers may occur beyond a long declaration's
            # prefix. Read bounded seed-file windows; never assert a full AST.
            if relative in paths and identifiers:
                lines = text.splitlines(keepends=True); offsets = [0]
                for line in lines:
                    offsets.append(offsets[-1] + len(line))
                windows = set()
                for number, line in enumerate(lines, 1):
                    if not any(t in line.casefold() for t in identifiers):
                        continue
                    if any(int(u['locator']['start_line']) <= number <= int(u['locator']['end_line'])
                           for u in parsed):
                        continue
                    start = max(0, number - 1 - max_lines // 2); end = min(len(lines), start + max_lines)
                    if (start, end) in windows:
                        continue
                    windows.add((start, end))
                    parsed.append(_unit(snapshot.snapshot_id, relative, text, offsets[start], offsets[end],
                        'BOUNDED_TEXT_FALLBACK', unit_kind='SOURCE_TEXT', max_chars=max_chars,
                        extra={'query_window': True, 'query_origin': 'PUBLIC_REQUIREMENT_IDENTIFIER'}))
                    if len(windows) >= 8:
                        break
            parsed.sort(key=lambda u: (-(12 * sum(t in u['text'].casefold() for t in identifiers)
                + len(terms & _terms(u['text']))), int(u['locator']['start_line'])))
        return parsed

    # Scope resolution already supplies a deterministic relevance order. Keep it
    # so a tight unit budget does not silently discard the strongest seed paths.
    for relative in paths:
        seed_units.extend(read_units(relative))
    references = sorted({str(reference) for unit in seed_units for reference in unit.get("references", ())
                         if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", str(reference))})
    extension_paths: list[str] = []
    declaration_patterns = {
        ".go": lambda name: re.compile(r"\b(?:func\s+(?:\([^)]*\)\s*)?|type\s+)" + re.escape(name) + r"\b"),
        ".js": lambda name: re.compile(r"\b(?:function|class|const|let|var)\s+" + re.escape(name) + r"\b"),
        ".ts": lambda name: re.compile(r"\b(?:function|class|const|let|var|interface|type)\s+" + re.escape(name) + r"\b"),
        ".tsx": lambda name: re.compile(r"\b(?:function|class|const|let|var|interface|type)\s+" + re.escape(name) + r"\b"),
        ".jsx": lambda name: re.compile(r"\b(?:function|class|const|let|var)\s+" + re.escape(name) + r"\b"),
        ".tpl": lambda name: re.compile(r"define\s+[\"']" + re.escape(name) + r"[\"']"),
        ".gotpl": lambda name: re.compile(r"define\s+[\"']" + re.escape(name) + r"[\"']"),
        '.py': lambda name: re.compile(r'\b(?:def|class)\s+' + re.escape(name) + r'\b'),
        '.c': lambda name: re.compile(r'\b' + re.escape(name) + r'\s*\('),
        '.h': lambda name: re.compile(r'\b' + re.escape(name) + r'\s*\('),
        '.java': lambda name: re.compile(r'\b' + re.escape(name) + r'\s*\('),
    }
    seed_paths = set(paths)
    if references and max_extension_files > 0:
        for relative in sorted(snapshot.file_digests):
            if relative in seed_paths:
                continue
            suffix = Path(relative).suffix.lower()
            factory = declaration_patterns.get(suffix)
            if factory is None:
                continue
            source_path = (root / relative).resolve()
            try:
                source_path.relative_to(root)
                if not source_path.is_file() or source_path.is_symlink():
                    continue
                text = source_path.read_text(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                continue
            if not any(factory(name).search(text) for name in references):
                continue
            extension_paths.append(relative)
            for unit in read_units(relative):
                unit["scope_extension"] = "DIRECT_REFERENCE"
                unit["hop_distance"] = 1
                seed_units.append(unit)
            if len(extension_paths) >= max_extension_files:
                break

    # A long first document must not consume the entire source-unit pool before
    # the next relevant file gets even one unit. Within each file retain parser
    # order and exact unit boundaries; selection remains relevance based.
    by_path: dict[str, list[dict[str, Any]]] = {}
    if declaration_index is not None:
        # These files were already parsed under the normal scope budget.
        # Preserve bounded declaration metadata before the intermediate unit
        # pool is cut. No omitted body becomes model evidence without a fresh,
        # bounded read and final selection/delivery verification.
        declaration_index.extend({k:unit[k] for k in ('id','path','symbol','locator','kind','truncated') if k in unit}
            for unit in seed_units if unit.get('symbol') and unit.get('kind')=='FUNCTION_OR_TYPE')
        del declaration_index[2048:]
    for unit in seed_units:
        unit['scope_path_rank'] = paths.index(unit['path']) if unit['path'] in paths else len(paths)
        by_path.setdefault(str(unit.get('path', '')), []).append(unit)
    # A known public execution entry must contribute actual declarations before
    # many document files exhaust this intermediate pool. This is bounded pool
    # admission, not forced final Top-N membership or a new source permission.
    from contractedge.retrieval.candidates import literal_identifiers
    identifiers = literal_identifiers(query or '')
    focus = [u for u in seed_units if u['path'] in focused_paths and u.get('kind')=='FUNCTION_OR_TYPE'
             and any(t in u.get('text','').casefold() for t in identifiers)]
    focus_symbols = {v for u in focus for v in u.get('declared_symbols',[])}
    focus_calls = {v for u in focus for v in u.get('references',[])}
    focus += [u for u in seed_units if u['path'] in focused_paths and u.get('kind')=='FUNCTION_OR_TYPE'
              and (set(u.get('references',[])) & focus_symbols or set(u.get('declared_symbols',[])) & focus_calls)
              and u not in focus]
    from contractedge.retrieval.candidates import _terms
    requirement_terms=_terms(query or '')
    resources=[u for u in seed_units if u['path'] in focused_paths and u.get('resource_kind')
        and (str(u['resource_kind']).casefold() in requirement_terms or str(u['resource_kind']).casefold()+'s' in requirement_terms)]
    expanded=[u for u in seed_units if u.get('expanded_from_unit_id') and u['path'] in focused_paths]
    focus=resources+expanded+focus
    focus = focus[:min(8,max_units)]
    for unit in focus:
        unit['anchor_priority'] = max(1,unit.get('anchor_priority',0))
        unit['source_focus_basis'] = 'PUBLIC_ENTRY_OR_EXPLICIT_RESOURCE_FILE; bounded relevant resource/declaration, not edit permission'
    interleaved = iter(focus + [unit for row in zip_longest(*by_path.values()) for unit in row if unit is not None])
    for unit in interleaved:
        relative = str(unit.get("path", ""))
        key = (relative, int(unit["locator"]["start_line"]), int(unit["locator"]["end_line"]))
        if key in seen:
            continue
        seen.add(key)
        output.append(unit)
        if len(output) >= max_units:
            return output
    return output


def extract_declared_artifact_units(artifacts: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]],
                                    snapshot_id: str, *, max_units: int = 40,
                                    max_lines: int = 80, max_chars: int = 16_000) -> list[dict[str, Any]]:
    """Parse immutable deployment/configuration inputs as method-visible semantic units."""
    output: list[dict[str, Any]] = []
    for artifact in artifacts:
        for unit in parse_source_units(str(artifact["path"]), str(artifact["content"]), snapshot_id,
                                       max_lines=max_lines, max_chars=max_chars):
            unit["id"] = "CU-INPUT-" + str(artifact["id"]) + "-" + unit["id"].removeprefix("CU-")
            unit["origin_kind"] = "DECLARED_ARTIFACT"
            unit["artifact_id"] = artifact["id"]
            unit["mutable"] = False
            output.append(unit)
            if len(output) >= max_units:
                return output
    return output


def extract_gap_extension_units(snapshot: Snapshot, seed_paths: tuple[str, ...] | list[str],
                               existing_units: list[Mapping[str, Any]],
                               evidence_gaps: list[Mapping[str, Any]], *,
                               max_files: int = 2_000, max_units: int = 40,
                               max_lines: int = 80, max_chars: int = 16_000,
                               max_search_bytes: int = 64 * 1024 * 1024,
                               max_file_bytes: int = 4 * 1024 * 1024,
                               max_declaration_lines: int = 512
                               ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read bounded direct neighbours or precisely located public-source gap units.

    Exact-path reads are explicitly not syntax edges or new edit permissions.
    A filename alone cannot broaden scope: the same gap must supply a matching
    symbol/search term, and the path must be in the current source inventory.
    """
    if max_files <= 0 or max_units <= 0:
        return [], {"inspected_files": 0, "matched_files": [], "candidate_count": 0,
                    "truncated_search": False, "queries": []}
    hints = []
    for gap in evidence_gaps:
        locator = gap.get("locator_hint")
        if not isinstance(locator, Mapping):
            continue
        for key in ("symbol", "path", "search_term"):
            value = locator.get(key)
            if isinstance(value, str) and value.strip():
                hints.append({"gap_id": str(gap.get("id", "")), "kind": key, "value": value.strip()})
    if not hints:
        return [], {"inspected_files": 0, "matched_files": [], "candidate_count": 0,
                    "truncated_search": False, "queries": []}
    # A model may supply several identifiers and connective prose in one
    # search_term. Preserve the original literal query, then add at most four
    # explicit long identifiers from that same query. This is token location,
    # not semantic expansion or a guessed synonym. Same-gap paths still apply.
    for hint in list(hints):
        query = hint['value']
        if hint['kind'] != 'search_term' or re.fullmatch(r'[A-Za-z_]\w*', query):
            continue
        tokens = list(dict.fromkeys(re.findall(r'(?<![A-Za-z0-9_$])([A-Za-z_][A-Za-z0-9_]*)(?![A-Za-z0-9_$])', query)))
        precise = [token for token in tokens if len(token) >= 12
                   and (re.search(r'[a-z][A-Z]', token) or '_' in token)]
        hints.extend({'gap_id': hint['gap_id'], 'kind': 'search_term', 'value': token,
                      'literal_token': True, 'parent_query': query} for token in precise[:4])

    # A precise public identifier is itself a bounded read locator. Requiring
    # a pre-existing call edge first makes disconnected production consumers
    # impossible to discover even when this scan already reads their files.
    # Generic words/prose and path-specific gaps keep their existing scope.
    precise_hints = [h for h in hints if h['kind'] in {'symbol', 'search_term'}
        and not any(p['kind'] == 'path' and p['gap_id'] == h['gap_id'] for p in hints)
        and len(h['value']) >= 12 and re.fullmatch(r'[A-Za-z_]\w*', h['value'])
        and (re.search(r'[a-z][A-Z]', h['value']) or '_' in h['value'])]

    def precise_match(hint, text):
        # Preserve the same case-sensitive token boundary at every admission
        # stage; a sibling declaration must not borrow a file-level hit.
        return bool(re.search(r'(?<![A-Za-z0-9_$])' + re.escape(hint['value'])
                              + r'(?![A-Za-z0-9_$])', text))

    def query_match(hint, text):
        if hint in precise_hints or hint.get('literal_token'):
            return precise_match(hint, text)
        return hint['value'].casefold() in text.casefold()

    root = Path(snapshot.source_root).resolve()
    seed = set(seed_paths)
    anchors = [unit for unit in existing_units if str(unit.get("path", "")) in seed]
    seed_symbols = {str(symbol) for unit in anchors for symbol in unit.get("declared_symbols", ())}
    seed_references = {str(symbol) for unit in anchors for symbol in unit.get("references", ())}
    existing_keys = {
        (str(unit.get("path", "")), int(unit.get("locator", {}).get("start_line", 0)),
         int(unit.get("locator", {}).get("end_line", 0)))
        for unit in existing_units
    }
    extensions = {".go", ".ts", ".tsx", ".js", ".jsx", ".yaml", ".yml", ".json", ".tpl", ".gotpl", '.py', '.c', '.h', '.java'}
    output: list[dict[str, Any]] = []
    matched_files: list[str] = []
    inspected = 0
    candidate_count = 0
    read_bytes = 0
    byte_limit_hit = False
    inventory = set(snapshot.file_digests)
    # Resolve a normalized directory only with a query in that same gap.
    # This is bounded read-only location, never a syntax edge or edit grant.
    from pathlib import PurePosixPath
    exact_hints = {}
    directory_paths = set()
    for hint in hints:
        if hint['kind'] != 'path':
            continue
        value = hint['value'].rstrip('/')
        parts = PurePosixPath(value)
        if not value or parts.is_absolute() or any(p in {'.', '..'} for p in value.split('/')):
            continue
        queries = [h for h in hints if h['gap_id'] == hint['gap_id']
                   and h['kind'] in {'symbol', 'search_term'}]
        if value in inventory:
            exact_hints.setdefault(value, []).extend(queries)
        elif queries:
            for relative in sorted(p for p in inventory if p.startswith(value + '/'))[:max_files]:
                exact_hints.setdefault(relative, []).extend(queries)
                directory_paths.add(relative)
    requested_paths = list(exact_hints)
    priority_paths = list(dict.fromkeys([*requested_paths, *seed_paths]))
    eligible_paths = [path for path in priority_paths
                      if path in inventory and Path(path).suffix.lower() in extensions]
    eligible_paths.extend(path for path in sorted(inventory - set(eligible_paths))
                          if Path(path).suffix.lower() in extensions)
    truncated_search = len(eligible_paths) > max_files
    for relative in eligible_paths[:max_files]:
        inspected += 1
        if (root / relative).is_symlink():continue
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
            if not path.is_file() or path.is_symlink():
                continue
            size = path.stat().st_size
            if size > max_file_bytes:
                byte_limit_hit = True
                continue
            if read_bytes + size > max_search_bytes:
                byte_limit_hit = True
                break
            raw = path.read_bytes()
            read_bytes += len(raw)
            if read_bytes > max_search_bytes or len(raw) > max_file_bytes:
                byte_limit_hit = True
                break
            if hashlib.sha256(raw).hexdigest() != snapshot.file_digests[relative]:
                continue
            text = raw.decode(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
        literal_hints = [h for h in precise_hints if relative not in seed and relative.endswith('.go') and re.search(
            r'(?<![A-Za-z0-9_$])' + re.escape(h['value']) + r'(?![A-Za-z0-9_$])', text)]
        if literal_hints:
            exact_hints.setdefault(relative, []).extend(literal_hints)
        units = parse_source_units(relative, text, snapshot.snapshot_id,
                                   max_lines=max_lines, max_chars=max_chars)
        if relative in requested_paths and (relative in seed or exact_hints.get(relative)):
            # A long declaration exposes only its bounded prefix. A precise
            # same-file gap may read a bounded literal window around a later
            # variable/closure use, without claiming a complete syntax unit.
            queries = exact_hints.get(relative, [])
            lines = text.splitlines(keepends=True)
            offsets = [0]
            for line in lines:
                offsets.append(offsets[-1] + len(line))
            windows = []
            seen_windows = set()
            width = max(1, max_lines)
            for number, line in enumerate(lines, 1):
                if not any(query_match(query, line) for query in queries):
                    continue
                if any(int(u['locator']['start_line']) <= number <= int(u['locator']['end_line'])
                       and any(query_match(query, u['text']) for query in queries) for u in units):
                    continue
                start = max(0, number - 1 - width // 2)
                end = min(len(lines), start + width)
                if (start, end) in seen_windows:
                    continue
                seen_windows.add((start, end))
                windows.append(_unit(snapshot.snapshot_id, relative, text, offsets[start], offsets[end],
                    'BOUNDED_TEXT_FALLBACK', unit_kind='SOURCE_TEXT', max_chars=max_chars,
                    extra={'query_window': True}))
                if len(windows) >= max_units:
                    break
            units.extend(windows)
        # A query-located declaration may need its body rather than an arbitrary
        # 80-line prefix. Preserve a separate identity for the expanded excerpt;
        # the complete request budget remains authoritative.
        same_file_queries = exact_hints.get(relative, [])
        if max_declaration_lines > max_lines and same_file_queries and any(u.get('truncated') and u.get('kind') == 'FUNCTION_OR_TYPE' for u in units):
            wider = parse_source_units(relative, text, snapshot.snapshot_id,
                max_lines=max(max_lines, max_declaration_lines), max_chars=max_chars)
            wide_by_start = {(u.get('symbol'), u['locator']['start_line']): u for u in wider}
            for index, unit in enumerate(units):
                expanded = wide_by_start.get((unit.get('symbol'), unit['locator']['start_line']))
                if not (unit.get('truncated') and unit.get('kind') == 'FUNCTION_OR_TYPE' and expanded
                        and len(expanded['text']) > len(unit['text'])
                        and any(query_match(h, expanded['text']) for h in same_file_queries)):
                    continue
                expanded = dict(expanded)
                expanded['expanded_from_unit_id'] = unit['id']
                identity = f"{snapshot.snapshot_id}:{relative}:{expanded['locator']}:{expanded['content_digest']}"
                expanded['id'] = 'CU-' + hashlib.sha256(identity.encode()).hexdigest()[:16]
                units[index] = expanded
        if not units:
            continue
        # A directly connected file can contain sibling consumers behind a
        # helper (e.g. branch -> chain -> runner). A same-gap literal query may
        # locate those siblings without pretending they are one-hop calls.
        file_is_connected = any(
            set(map(str, u.get("declared_symbols", ()))) & seed_references
            or set(map(str, u.get("references", ()))) & seed_symbols for u in units)
        query_hints = [h for h in hints if h['kind'] in {'symbol', 'search_term'}]
        connected_query_ids = {u['id'] for u in units if file_is_connected and any(
            (precise_match(h, str(u.get('text', ''))) if h in precise_hints or h.get('literal_token') else
             re.search(r'(?<![A-Za-z0-9_$])' + re.escape(h['value']) + r'(?![A-Za-z0-9_$])',
                       str(u.get('text', '')), re.IGNORECASE)) for h in query_hints)}
        neighbors = []
        for unit in units:
            declared = {str(value) for value in unit.get("declared_symbols", ())}
            references = {str(value) for value in unit.get("references", ())}
            located_declaration = relative in seed and any(
                hint['kind'] == 'symbol' and hint['value'] in declared for hint in hints)
            located_seed_text = relative in seed and relative in requested_paths and any(
                hint['kind'] in {'symbol', 'search_term'}
                and hint['value'].casefold() in str(unit.get('text', '')).casefold()
                for hint in hints)
            located_exact = any(
                precise_match(h, str(unit.get('text', ''))) if h in precise_hints or h.get('literal_token') else
                (re.search(r'(?<![A-Za-z0-9_$])' + re.escape(h['value']) + r'(?![A-Za-z0-9_$])',
                           str(unit.get('text', '')), re.IGNORECASE) if h['kind'] == 'symbol'
                 else query_match(h, str(unit.get('text', ''))))
                for h in exact_hints.get(relative, []))
            if declared & seed_references or references & seed_symbols or located_declaration or located_seed_text or located_exact or unit["id"] in connected_query_ids:
                neighbors.append(unit)
        if not neighbors:
            continue
        matched_units: list[tuple[dict[str, Any], list[dict[str, str]]]] = []
        for unit in neighbors:
            unit_content = "\n".join([
                str(unit.get("text", "")), str(unit.get("symbol", "")),
                *map(str, unit.get("declared_symbols", ())), *map(str, unit.get("references", ())),
            ])
            unit_hints = []
            for hint in hints:
                value = hint["value"]
                if hint in precise_hints or hint.get('literal_token'):
                    match = precise_match(hint, str(unit.get('text', '')))
                elif hint["kind"] == "symbol":
                    match = re.search(r"(?<![A-Za-z0-9_$])" + re.escape(value) + r"(?![A-Za-z0-9_$])",
                                      unit_content, re.IGNORECASE)
                elif hint["kind"] == "path":
                    match = value.casefold() in relative.casefold()
                else:
                    match = value.casefold() in (relative + "\n" + unit_content).casefold()
                if match:
                    unit_hints.append(hint)
            if unit_hints:
                matched_units.append((unit, unit_hints))
        specific_matches = [entry for entry in matched_units
                            if any(hint["kind"] != "path" for hint in entry[1])]
        if specific_matches:
            # An exact symbol/search-term hit is more precise than a broad path
            # hint in the same file. Do not flood the extension with every unit
            # merely because the model supplied both a path and a symbol.
            matched_units = specific_matches
        if not matched_units:
            continue
        matched_files.append(relative)
        candidate_count += len(matched_units)
        for unit, matched_hints in matched_units:
            key = (str(unit.get("path", "")), int(unit.get("locator", {}).get("start_line", 0)),
                   int(unit.get("locator", {}).get("end_line", 0)))
            if key in existing_keys:
                continue
            item = dict(unit)
            direct = (set(map(str, unit.get('declared_symbols', ()))) & seed_references
                      or set(map(str, unit.get('references', ()))) & seed_symbols)
            item["scope_extension"] = ("EVIDENCE_GAP_SEED_UNIT" if relative in seed else
                                       "EVIDENCE_GAP_DIRECTORY_PATH" if relative in directory_paths else
                                       "EVIDENCE_GAP_EXACT_PATH" if relative in requested_paths
                                       else "EVIDENCE_GAP_PRECISE_IDENTIFIER" if not direct and any(h in literal_hints for h in matched_hints)
                                       else "EVIDENCE_GAP_DIRECT_NEIGHBOR")
            if item['scope_extension'] == 'EVIDENCE_GAP_PRECISE_IDENTIFIER':
                item['read_scope_basis'] = 'BOUNDED_PUBLIC_PRECISE_IDENTIFIER_TEXT_LOCATION'
                item['association_status'] = 'EXACT_IDENTIFIER_TEXT_LOCATION; not a verified reference/causal edge, unique binding or edit grant'
            if item['scope_extension'] in {'EVIDENCE_GAP_EXACT_PATH', 'EVIDENCE_GAP_DIRECTORY_PATH'}:
                item['read_scope_basis'] = ('BOUNDED_PUBLIC_DIRECTORY_AND_SAME_GAP_QUERY' if relative in directory_paths
                                            else 'EXACT_PUBLIC_SOURCE_PATH_AND_SAME_GAP_QUERY')
                item['association_status'] = 'LOCATED_TEXT_ONLY; not a verified causal/reference edge or edit grant'
            if (unit['id'] in connected_query_ids and not direct and relative not in seed
                    and relative not in requested_paths and item['scope_extension'] != 'EVIDENCE_GAP_PRECISE_IDENTIFIER'):
                item['scope_extension'] = 'EVIDENCE_GAP_CONNECTED_FILE_QUERY'
                item['association_status'] = 'LEXICAL_CONNECTED_FILE_QUERY; not a verified causal edge or edit grant'
                item['hop_distance'] = 2
            else:
                item["hop_distance"] = 1
            gap_ids = {hint['gap_id'] for hint in matched_hints if hint['gap_id']}
            # A generic word hit in another requested file does not satisfy a
            # path-specific gap. Keep discovery association separate from proof.
            qualified = []
            for gid in sorted(gap_ids):
                paths = [h['value'].rstrip('/') for h in hints if h['gap_id'] == gid and h['kind'] == 'path']
                own_queries = [h for h in hints if h['gap_id'] == gid and h['kind'] != 'path']
                own_hits = any(h in matched_hints for h in own_queries)
                if ((not paths or any(relative == p or relative.startswith(p + '/') for p in paths))
                        and (not own_queries or own_hits)):
                    qualified.append(gid)
            item["evidence_gap_refs"] = qualified
            output.append(item)
            existing_keys.add(key)
    # Interleave bounded matches across files before applying the pool cap.
    # Ranking still chooses final relevance; this is not a final-context quota.
    from itertools import zip_longest
    by_path = {}
    for item in output:
        by_path.setdefault(item['path'], []).append(item)
    if any(h.get('literal_token') for h in hints):
        def token_priority(item):
            hits = {h['value'] for h in hints if h.get('literal_token')
                    and h['gap_id'] in item.get('evidence_gap_refs', [])
                    and precise_match(h, str(item.get('text', '')))}
            # A constructor mentioning one generic type must not displace the
            # consumer containing both explicit query identifiers before the
            # final ranker ever sees it. No extra unit or read slots are added.
            return (-len(hits), -max(map(len, hits), default=0))
        for group in by_path.values():
            group.sort(key=token_priority)
        by_path = dict(sorted(by_path.items(), key=lambda pair: token_priority(pair[1][0])))
    output = [item for row in zip_longest(*by_path.values()) for item in row if item is not None][:max_units]
    return output, {"inspected_files": inspected, "matched_files": sorted(set(matched_files)),
                    "candidate_count": candidate_count, "truncated_search": truncated_search or byte_limit_hit,
                    "inspected_bytes": read_bytes, "queries": hints}
