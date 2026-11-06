"""One read-only source tool surface: search, read and fixed-dependency follow.

FRONT_HALF_REDESIGN_IMPLEMENTATION_SPEC section 4. Search returns names, exact
positions and handles, never bodies; ``read`` returns the exact original bytes
of one declaration or an explicit line range. Nothing here ranks out or
permanently drops a result: a page only bounds what one call shows, and a scan
stopped by a tool resource limit is reported as incomplete and can continue.

The declaration index is a locator, not a type checker. Parsing problems leave
the file searchable as text and readable by explicit range; they never turn
into "no such declaration". Lexical/binding facts are navigation, not causal
evidence, completeness proof or edit permission.
"""
from __future__ import annotations

import ast
import base64
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Protocol

from contractedge.source.parsers import mask_go_code, parse_source_units

TEXT_SUFFIXES = {'.go', '.py', '.yaml', '.yml', '.json', '.tpl', '.gotpl', '.ts', '.tsx', '.js', '.jsx',
                 '.c', '.h', '.java', '.toml', '.md', '.txt', '.mod', '.sum', '.cfg', '.ini', '.sh', ''}
CONFIG_SUFFIXES = {'.yaml', '.yml', '.json', '.tpl', '.gotpl'}
CONFIG_KINDS = {'config_subtree', 'source_text', 'template_helper'}
STRUCTURED_SUFFIXES = {'.yaml', '.yml', '.json', '.tpl', '.gotpl', '.ts', '.tsx', '.js', '.jsx', '.c', '.h', '.java'}


@dataclass(frozen=True)
class NavigationLimits:
    """Local tool resource protection only; never a material admission quota."""
    max_files_per_call: int = 4000
    max_bytes_per_call: int = 128 * 1024 * 1024
    max_file_bytes: int = 8 * 1024 * 1024
    max_read_bytes: int = 4 * 1024 * 1024
    max_text_hit_lines: int = 50


class NavigationError(ValueError):
    """Explicit refusal with a machine-readable reason; the caller may change the query."""

    def __init__(self, reason: str, message: str, **details: Any):
        super().__init__(message)
        self.reason = reason
        self.details = details


@dataclass
class Declaration:
    path: str
    symbol: str
    kind: str
    parser_kind: str
    start_char: int
    end_char: int
    start_line: int
    end_line: int
    receiver: str = ''
    names: tuple[str, ...] = ()
    origin: str = 'SOURCE'          # SOURCE | DEPENDENCY
    handle: str = ''


@dataclass
class FileIndex:
    path: str
    text: str
    digest: str
    declarations: list[Declaration] = field(default_factory=list)
    parse_status: str = 'OK'        # OK | PARTIAL | TEXT_ONLY
    parse_problems: list[dict[str, Any]] = field(default_factory=list)
    identity: dict[str, Any] = field(default_factory=dict)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _line_starts(text: str) -> list[int]:
    # Only '\n' ends a line; CRLF keeps its '\r' inside the line, as on disk.
    return [0] + [m.end() for m in re.finditer('\n', text)]


def _line_of(starts: list[int], offset: int) -> int:
    lo, hi = 0, len(starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if starts[mid] <= offset:
            lo = mid
        else:
            hi = mid - 1
    return lo + 1


def _end_line(starts: list[int], text: str, end: int) -> int:
    return _line_of(starts, max(0, end - 1)) if end > 0 else 1


# --------------------------------------------------------------------- Go
_GO_TOP = re.compile(r'(?m)^(func|type|var|const)\b')
_PAIRS = {'(': ')', '[': ']', '{': '}'}


def _close(code: str, start: int) -> int | None:
    """Offset after the bracket matching code[start], on masked code."""
    stack = []
    for offset in range(start, len(code)):
        char = code[offset]
        if char in _PAIRS:
            stack.append(_PAIRS[char])
        elif char in ')]}':
            if not stack or stack.pop() != char:
                return None
            if not stack:
                return offset + 1
    return None


def _skip_ws(code: str, pos: int) -> int:
    while pos < len(code) and code[pos] in ' \t\r':
        pos += 1
    return pos


def _line_end_balanced(code: str, pos: int) -> int | None:
    """End of a declaration that ends at the first newline outside brackets."""
    while pos < len(code):
        char = code[pos]
        if char in _PAIRS:
            closing = _close(code, pos)
            if closing is None:
                return None
            pos = closing
        elif char == '\n':
            return pos
        else:
            pos += 1
    return pos


def _go_receiver_type(receiver: str) -> str:
    inner = receiver.strip()[1:-1].strip()
    match = re.search(r'\*?\s*([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*$', inner)
    return match[1] if match else ''


def go_declarations(path: str, text: str) -> tuple[list[Declaration], list[dict[str, Any]]]:
    """Top-level func/method/type/var/const declarations with exact char ranges.

    Supports receivers (including generic receivers), type parameters, grouped
    declarations and non-struct types. A declaration whose brackets cannot be
    balanced is reported as a parse problem at its line; the rest of the file
    is still indexed.
    """
    code = mask_go_code(text)
    starts = _line_starts(text)
    output, problems = [], []
    depth, cursor = 0, 0
    for match in _GO_TOP.finditer(code):
        # Running bracket depth: a column-0 keyword inside a body is not top level.
        for char in code[cursor:match.start()]:
            if char in '({[':
                depth += 1
            elif char in ')}]':
                depth -= 1
        cursor = match.start()
        if depth != 0:
            continue
        keyword, start = match[1], match.start()
        pos = _skip_ws(code, match.end())
        try:
            if keyword == 'func':
                receiver = ''
                if pos < len(code) and code[pos] == '(':
                    closing = _close(code, pos)
                    if closing is None:
                        raise SyntaxError('unbalanced receiver')
                    receiver = _go_receiver_type(code[pos:closing])
                    pos = _skip_ws(code, closing)
                name = re.match(r'([A-Za-z_]\w*)', code[pos:])
                if not name:
                    raise SyntaxError('unsupported func header')
                pos += name.end()
                if pos < len(code) and code[pos] == '[':
                    pos = _close(code, pos)
                    if pos is None:
                        raise SyntaxError('unbalanced type parameters')
                pos = _skip_ws(code, pos)
                if pos >= len(code) or code[pos] != '(':
                    raise SyntaxError('missing parameters')
                pos = _close(code, pos)
                if pos is None:
                    raise SyntaxError('unbalanced parameters')
                # Results end at the body brace or the end of a body-less declaration.
                while pos < len(code) and code[pos] not in '{\n':
                    if code[pos] in '([':
                        pos = _close(code, pos)
                        if pos is None:
                            raise SyntaxError('unbalanced results')
                    else:
                        pos += 1
                end = _close(code, pos) if pos < len(code) and code[pos] == '{' else pos
                if end is None:
                    raise SyntaxError('unbalanced body')
                kind = 'go_method' if receiver else 'go_func'
                names = (name[1],)
            else:
                if pos < len(code) and code[pos] == '(':
                    end = _close(code, pos)
                    if end is None:
                        raise SyntaxError('unbalanced group')
                    group = code[pos + 1:end - 1]
                    names, level = [], 0
                    for line in group.split('\n'):
                        stripped = line.strip()
                        if level == 0 and stripped:
                            item = re.match(r'([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)', stripped)
                            if item:
                                names.extend(n.strip() for n in item[1].split(','))
                        level += sum(stripped.count(c) for c in '([{') - sum(stripped.count(c) for c in ')]}')
                    names = tuple(n for n in names if n)
                    kind = 'go_' + keyword + '_group'
                else:
                    item = re.match(r'([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)', code[pos:])
                    if not item:
                        raise SyntaxError('unsupported ' + keyword + ' header')
                    names = tuple(n.strip() for n in item[1].split(','))
                    end = _line_end_balanced(code, pos + item.end())
                    if end is None:
                        raise SyntaxError('unbalanced ' + keyword)
                    kind = 'go_' + keyword
                receiver = ''
                if not names:
                    raise SyntaxError('empty ' + keyword + ' group')
        except SyntaxError as exc:
            problems.append({'path': path, 'line': _line_of(starts, start), 'reason': str(exc)})
            continue
        output.append(Declaration(path=path, symbol=names[0], names=tuple(names), kind=kind,
            receiver=receiver, parser_kind='GO_STRUCTURAL_DECLARATION', start_char=start, end_char=end,
            start_line=_line_of(starts, start), end_line=_end_line(starts, text, end)))
    return output, problems


# ----------------------------------------------------------------- Python
def python_declarations(path: str, text: str) -> list[Declaration]:
    tree = ast.parse(text)
    starts = _line_starts(text)
    output = []

    def visit(node, owner):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                first = min([child.lineno] + [d.lineno for d in child.decorator_list])
                last = child.end_lineno or child.lineno
                start = starts[first - 1]
                end = starts[last] if last < len(starts) else len(text)
                is_class = isinstance(child, ast.ClassDef)
                output.append(Declaration(path=path, symbol=child.name, names=(child.name,),
                    kind='py_class' if is_class else ('py_method' if owner else 'py_function'),
                    receiver=owner or '', parser_kind='PYTHON_AST_DECLARATION',
                    start_char=start, end_char=end, start_line=first, end_line=last))
                visit(child, child.name if is_class else owner)
            else:
                visit(child, owner)
    visit(tree, '')
    return output


def _generic_declarations(path: str, text: str) -> list[Declaration]:
    """Reuse the existing YAML/JSON/Helm/JS/C/Java unit parsers without any line/char cap."""
    units = parse_source_units(path, text, 'navigation', max_lines=text.count('\n') + 2,
                               max_chars=max(1, len(text)))
    output = []
    for unit in units:
        if 'FALLBACK' in str(unit.get('parser_kind', '')) or unit.get('truncated'):
            continue
        locator = unit['locator']
        symbol = unit.get('symbol') or unit.get('node_path') or unit.get('resource_kind') or ''
        output.append(Declaration(path=path, symbol=str(symbol), names=(str(symbol),),
            kind=str(unit.get('kind', 'unit')).lower(), parser_kind=str(unit['parser_kind']),
            start_char=int(locator['start_char']), end_char=int(locator['end_char']),
            start_line=int(locator['start_line']), end_line=int(locator['end_line'])))
    return output


def index_text(path: str, text: str, digest: str) -> FileIndex:
    suffix = PurePosixPath(path).suffix.lower()
    item = FileIndex(path=path, text=text, digest=digest)
    try:
        if suffix == '.go':
            item.declarations, problems = go_declarations(path, text)
            if problems:
                item.parse_status, item.parse_problems = 'PARTIAL', problems
        elif suffix == '.py':
            item.declarations = python_declarations(path, text)
        elif suffix in STRUCTURED_SUFFIXES:
            item.declarations = _generic_declarations(path, text)
            if not item.declarations and suffix in {'.yaml', '.yml', '.json'} and text.strip():
                if '{{' in text:
                    # Templated config (Helm, CI `${{ }}`) is text by design, not a failed parse.
                    item.parse_status = 'TEMPLATED_TEXT'
                else:
                    # The shared YAML/JSON parser falls back silently; a plain
                    # document without any node means its keys are not indexed.
                    item.parse_status = 'TEXT_ONLY'
                    item.parse_problems = [{'path': path, 'line': None, 'reason': 'no structured nodes parsed'}]
        else:
            # Prose/lock/script files have no declaration parser; that is not a parse failure.
            item.parse_status = 'NO_DECLARATION_PARSER'
    except (SyntaxError, ValueError, RecursionError) as exc:
        # e.g. syntax newer than this interpreter: keep text search and range reads.
        item.declarations, item.parse_status = [], 'TEXT_ONLY'
        item.parse_problems = [{'path': path, 'line': getattr(exc, 'lineno', None), 'reason': str(exc)[:200]}]
    except Exception as exc:  # third-party YAML composer errors and similar
        item.declarations, item.parse_status = [], 'TEXT_ONLY'
        item.parse_problems = [{'path': path, 'line': None, 'reason': type(exc).__name__}]
    return item


# ------------------------------------------------------------- dependency
class GoDependencySource(Protocol):
    """Hash-pinned read-only Go package files; see PublicGoDependencies.package_files."""

    def resolve(self, imported: str) -> tuple[str, str, str] | None: ...

    def package_files(self, imported: str) -> Mapping[str, Any]: ...


def _encode_cursor(payload: Mapping[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode()).decode()


def _decode_cursor(cursor: str | None) -> dict[str, Any]:
    if not cursor:
        return {}
    try:
        value = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
    except (ValueError, UnicodeError) as exc:
        raise NavigationError('BAD_CURSOR', 'Cursor is not one this tool returned') from exc
    if not isinstance(value, dict):
        raise NavigationError('BAD_CURSOR', 'Cursor is not one this tool returned')
    return value


class SourceNavigator:
    """Search/read/follow over one verified snapshot plus optional pinned dependencies."""

    def __init__(self, snapshot, *, limits: NavigationLimits | None = None,
                 dependencies: GoDependencySource | None = None):
        self.snapshot = snapshot
        self.root = Path(snapshot.source_root).resolve()
        self.limits = limits or NavigationLimits()
        self.dependencies = dependencies
        self._files: dict[str, FileIndex] = {}
        self._texts: dict[str, tuple[str, str]] = {}
        self._dependency_listings: dict[str, Mapping[str, Any]] = {}
        self._handles: dict[str, tuple[FileIndex, Declaration | None, int, int]] = {}
        self._cache: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------ identity
    def _handle(self, item: FileIndex, start: int, end: int, kind: str) -> str:
        source = item.identity.get('archive_sha256') or self.snapshot.snapshot_id
        prefix = 'DEP-' if item.identity else 'SRC-'
        seed = json.dumps([source, item.identity.get('member', item.path), item.digest, start, end, kind])
        handle = prefix + _sha(seed.encode())[:20]
        return handle

    def _register(self, item: FileIndex):
        for declaration in item.declarations:
            declaration.handle = self._handle(item, declaration.start_char, declaration.end_char,
                                              declaration.kind)
            declaration.origin = 'DEPENDENCY' if item.identity else 'SOURCE'
            self._handles[declaration.handle] = (item, declaration, declaration.start_char,
                                                 declaration.end_char)

    def _verified_text(self, relative: str) -> tuple[str, str]:
        if relative not in self.snapshot.file_digests:
            raise NavigationError('NOT_IN_SNAPSHOT', f'{relative} is not in the task snapshot')
        candidate = self.root / relative
        if candidate.is_symlink():
            raise NavigationError('SYMLINK', f'{relative} is a symlink')
        path = candidate.resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            raise NavigationError('OUTSIDE_SNAPSHOT', f'{relative} is outside the snapshot')
        size = path.stat().st_size
        if size > self.limits.max_file_bytes:
            raise NavigationError('FILE_EXCEEDS_TOOL_LIMIT', f'{relative} is {size} bytes',
                                  size=size, limit=self.limits.max_file_bytes)
        raw = path.read_bytes()
        digest = _sha(raw)
        if digest != self.snapshot.file_digests[relative]:
            raise NavigationError('SOURCE_CHANGED', f'{relative} differs from the snapshot')
        try:
            return raw.decode('utf-8'), digest
        except UnicodeDecodeError as exc:
            raise NavigationError('NOT_UTF8', f'{relative} is not UTF-8 text') from exc

    def _text(self, relative: str) -> tuple[str, str]:
        if relative not in self._texts:
            self._texts[relative] = self._verified_text(relative)
        return self._texts[relative]

    def _index(self, relative: str) -> FileIndex:
        if relative not in self._files:
            text, digest = self._text(relative)
            item = index_text(relative, text, digest)
            self._register(item)
            self._files[relative] = item
        return self._files[relative]

    # ---------------------------------------------------------------- scope
    def _scope_files(self, scope: str | Iterable[str] | None) -> list[str]:
        scopes = [scope] if isinstance(scope, str) or scope is None else list(scope)
        inventory = sorted(self.snapshot.file_digests)
        selected: list[str] = []
        for value in scopes:
            value = (value or '').strip().strip('/')
            parts = PurePosixPath(value).parts if value else ()
            if value and (PurePosixPath(value).is_absolute() or '..' in parts or '\\' in value):
                raise NavigationError('BAD_SCOPE', f'Scope {value!r} must be a relative snapshot path')
            if not value:
                matched = inventory
            elif value in self.snapshot.file_digests:
                matched = [value]
            else:
                matched = [p for p in inventory if p.startswith(value + '/')]
            selected.extend(p for p in matched if PurePosixPath(p).suffix.lower() in TEXT_SUFFIXES)
        return list(dict.fromkeys(selected))

    # --------------------------------------------------------------- search
    @staticmethod
    def _may_match(body: str, symbol, receiver, text, kind, case_sensitive) -> bool:
        """Cheap text precheck before parsing; never excludes a possible match."""
        if kind and not (symbol or receiver or text):
            return True
        names = []
        if symbol:
            owner, _, name = symbol.rpartition('.')
            names += [name, owner]
        if receiver:
            names.append(receiver.lstrip('*'))
        named = bool(names) and all(n in body for n in names if n)
        if text and not named:
            return (text in body) if case_sensitive else (text.casefold() in body.casefold())
        return named

    @staticmethod
    def _symbol_match(declaration: Declaration, symbol: str | None, receiver: str | None) -> bool:
        if symbol:
            owner, _, name = symbol.rpartition('.')
            if owner and not receiver:
                receiver = owner
                symbol = name
            if symbol not in declaration.names and symbol != declaration.symbol:
                return False
        if receiver and declaration.receiver != receiver.lstrip('*'):
            return False
        return bool(symbol or receiver)

    def _text_lines(self, item: FileIndex, text: str, case_sensitive: bool,
                    start: int = 0, end: int | None = None) -> list[int]:
        body = item.text[start:end]
        if case_sensitive:
            positions = [m.start() for m in re.finditer(re.escape(text), body)]
        else:
            positions = [m.start() for m in re.finditer(re.escape(text), body, re.IGNORECASE)]
        starts = _line_starts(item.text)
        return sorted({_line_of(starts, start + p) for p in positions})

    def _file_hits(self, item: FileIndex, symbol, receiver, text, kind, case_sensitive) -> list[dict[str, Any]]:
        limit = self.limits.max_text_hit_lines
        hits = []
        declared = [d for d in item.declarations if (not kind or d.kind == kind)]
        if kind and not (symbol or receiver or text):
            return [self._row(d, ['kind']) for d in declared]
        if symbol or receiver:
            for d in declared:
                if not self._symbol_match(d, symbol, receiver):
                    continue
                row = self._row(d, ['symbol' if symbol else 'receiver'])
                if text:
                    lines = self._text_lines(item, text, case_sensitive, d.start_char, d.end_char)
                    row['contains_text'] = bool(lines)
                    row['text_hit_lines'] = lines[:limit]
                    row['text_hit_count'] = len(lines)
                hits.append(row)
        if text:
            # The operation is answered wherever it occurs in scope, not only
            # inside a named declaration: keep both conditions visible.
            claimed = {(h['start_line'], h['end_line']) for h in hits}
            lines = self._text_lines(item, text, case_sensitive)
            groups: dict[Any, list[int]] = {}
            for line in lines:
                owner = self._innermost(item, line, kind)
                groups.setdefault(owner.handle if owner else None, []).append(line)
            for handle, owned in groups.items():
                declaration = self._handles[handle][1] if handle else None
                if declaration is not None and (declaration.start_line, declaration.end_line) in claimed:
                    continue
                if declaration is not None:
                    row = self._row(declaration, ['text'])
                else:
                    row = {'handle': None, 'path': item.path, 'symbol': None, 'receiver': '',
                           'kind': 'text_outside_declarations', 'start_line': owned[0], 'end_line': owned[-1],
                           'matched_by': ['text'], 'origin': 'SOURCE',
                           'read_hint': {'path': item.path, 'start_line': owned[0], 'end_line': owned[-1]}}
                row['contains_text'] = True
                row['text_hit_lines'] = owned[:limit]
                row['text_hit_count'] = len(owned)
                hits.append(row)
        return sorted(hits, key=lambda h: (h['start_line'], h['end_line'], h['kind']))

    @staticmethod
    def _innermost(item: FileIndex, line: int, kind: str | None) -> Declaration | None:
        owners = [d for d in item.declarations if d.start_line <= line <= d.end_line and (not kind or d.kind == kind)]
        return min(owners, key=lambda d: d.end_line - d.start_line) if owners else None

    @staticmethod
    def _row(d: Declaration, matched_by: list[str]) -> dict[str, Any]:
        return {'handle': d.handle, 'path': d.path, 'symbol': d.symbol, 'names': list(d.names),
                'receiver': d.receiver, 'kind': d.kind, 'start_line': d.start_line, 'end_line': d.end_line,
                'bytes': None, 'matched_by': matched_by, 'origin': d.origin}

    def search(self, *, scope: str | Iterable[str] | None = None, symbol: str | None = None,
               receiver: str | None = None, text: str | None = None, kind: str | None = None,
               case_sensitive: bool = False, page_size: int = 25, cursor: str | None = None) -> dict[str, Any]:
        """Locate declarations by name/receiver and/or text inside a scope.

        `scope`/`symbol`/`receiver` say where to look; `text` is the operation
        to inspect. With both, each named declaration reports whether it
        contains the text, and text occurrences elsewhere in scope are listed
        separately. Zero hits are a fact only when `scan.complete` is true.
        """
        text = text or None
        if not (symbol or receiver or text or kind):
            raise NavigationError('EMPTY_QUERY', 'Provide symbol, receiver, text or kind')
        query = {'scope': scope if isinstance(scope, (str, type(None))) else list(scope), 'symbol': symbol,
                 'receiver': receiver, 'text': text, 'kind': kind, 'case_sensitive': case_sensitive}
        query_key = _sha(json.dumps([self.snapshot.snapshot_id, query], sort_keys=True).encode())[:16]
        state = _decode_cursor(cursor)
        if state and state.get('q') != query_key:
            raise NavigationError('BAD_CURSOR', 'Cursor belongs to a different query')
        cache_key = query_key + ':' + (cursor or '') + ':' + str(page_size)
        if cache_key in self._cache:
            return self._cache[cache_key]
        files = self._scope_files(scope)
        file_index, hit_index = int(state.get('f', 0)), int(state.get('h', 0))
        skipped = list(state.get('skipped', []))
        partial = list(state.get('partial', []))
        page, scanned_files, scanned_bytes = [], 0, 0
        next_state = None
        stop_reason = None
        while file_index < len(files):
            relative = files[file_index]
            if scanned_files >= self.limits.max_files_per_call or scanned_bytes >= self.limits.max_bytes_per_call:
                stop_reason = 'TOOL_SCAN_LIMIT'
                next_state = {'q': query_key, 'f': file_index, 'h': 0}
                break
            try:
                body, _ = self._text(relative)
            except NavigationError as exc:
                skipped.append({'path': relative, 'reason': exc.reason})
                file_index += 1
                continue
            scanned_files += 1
            scanned_bytes += len(body)
            if self._may_match(body, symbol, receiver, text, kind, case_sensitive):
                item = self._index(relative)
                if item.parse_problems and relative not in partial:
                    partial.append(relative)
                hits = self._file_hits(item, symbol, receiver, text, kind, case_sensitive)
            else:
                # A declaration contains its own name, so a file without the
                # name cannot hide a match even if it would not parse.
                hits = []
            room = page_size - len(page)
            if len(hits) - hit_index > room:
                page.extend(hits[hit_index:hit_index + room])
                next_state = {'q': query_key, 'f': file_index, 'h': hit_index + room}
                stop_reason = 'PAGE_FULL'
                break
            page.extend(hits[hit_index:])
            file_index += 1
            hit_index = 0
            if len(page) >= page_size and file_index < len(files):
                next_state = {'q': query_key, 'f': file_index, 'h': 0}
                stop_reason = 'PAGE_FULL'
                break
        if next_state is not None:
            next_state.update(skipped=skipped, partial=partial)
        for row in page:
            if row.get('handle'):
                item, declaration, start, end = self._handles[row['handle']]
                row['bytes'] = len(item.text[start:end].encode('utf-8'))
        scan_complete = next_state is None
        config = [p for p in partial if PurePosixPath(p).suffix.lower() in CONFIG_SUFFIXES]
        code = [p for p in partial if p not in config]
        # Absence of a declaration is only a fact for a fully scanned scope whose
        # relevant files all parsed. Config parse failures matter only for
        # config-kind queries; their text is still fully searched either way.
        relevant = (code + config if kind in CONFIG_KINDS else code) if (symbol or receiver or kind) else []
        # Non-UTF-8 files are binary data, not source text. An unread oversized
        # config file can hide text or config nodes, not a code declaration.
        relevant += [s['path'] for s in skipped if s['reason'] != 'NOT_UTF8' and (
            text or kind in CONFIG_KINDS or PurePosixPath(s['path']).suffix.lower() not in CONFIG_SUFFIXES)]
        result = {
            'query': query, 'hits': page,
            'next_cursor': _encode_cursor(next_state) if next_state else None,
            'scan': {
                'files_in_scope': len(files), 'files_scanned_this_call': scanned_files,
                'scope_position': file_index if next_state else len(files),
                'reached_end_of_scope': scan_complete, 'stop_reason': stop_reason,
                'skipped_files': skipped, 'parse_incomplete_files': code,
                'config_parse_incomplete_files': config,
                'complete': scan_complete and not relevant,
            },
            'interpretation': 'Navigation names and positions only; read a handle for its body. '
                              'Text hits are lexical locations, not causal evidence or edit permission.',
        }
        self._cache[cache_key] = result
        return result

    # ----------------------------------------------------------------- read
    def read(self, handle: str | None = None, *, path: str | None = None, symbol: str | None = None,
             receiver: str | None = None, start_line: int | None = None,
             end_line: int | None = None) -> dict[str, Any]:
        """Exact original text of one declaration or an explicit line range.

        Never truncates: a body over the tool read limit is refused with its
        size so the caller can read a narrower declaration or range instead.
        """
        if handle:
            if handle not in self._handles:
                raise NavigationError('UNKNOWN_HANDLE', 'Handle was not returned by this tool for this task')
            item, declaration, start, end = self._handles[handle]
            if not item.identity:
                # Re-verify the bytes on every read; an index is not a stale cache of the source.
                text, digest = self._verified_text(item.path)
                if digest != item.digest:
                    raise NavigationError('SOURCE_CHANGED', f'{item.path} changed since it was indexed')
            return self._record(item, declaration, start, end, handle)
        if not path:
            raise NavigationError('EMPTY_READ', 'Provide a handle or a path')
        item = self._index(path)
        if start_line is not None or end_line is not None:
            starts = _line_starts(item.text)
            first = int(start_line or 1)
            last = int(end_line or len(starts))
            if not (1 <= first <= last <= len(starts)):
                raise NavigationError('BAD_RANGE', f'{path} has {len(starts)} lines', lines=len(starts))
            start = starts[first - 1]
            end = starts[last] if last < len(starts) else len(item.text)
            handle = self._handle(item, start, end, 'line_range')
            self._handles[handle] = (item, None, start, end)
            return self._record(item, None, start, end, handle)
        if not (symbol or receiver):
            raise NavigationError('EMPTY_READ', 'Provide symbol/receiver or a line range with the path')
        matches = [d for d in item.declarations if self._symbol_match(d, symbol, receiver)]
        if len(matches) != 1:
            reason = 'AMBIGUOUS' if matches else ('NOT_FOUND_PARSE_INCOMPLETE' if item.parse_problems
                                                  else 'NOT_FOUND')
            raise NavigationError(reason, f'{len(matches)} declarations match in {path}',
                                  candidates=[self._row(d, ['symbol']) for d in matches],
                                  parse_status=item.parse_status, parse_problems=item.parse_problems)
        declaration = matches[0]
        return self._record(item, declaration, declaration.start_char, declaration.end_char,
                            declaration.handle)

    def _record(self, item: FileIndex, declaration: Declaration | None, start: int, end: int,
                handle: str) -> dict[str, Any]:
        body = item.text[start:end]
        raw = body.encode('utf-8')
        if len(raw) > self.limits.max_read_bytes:
            raise NavigationError('READ_EXCEEDS_TOOL_LIMIT', f'{len(raw)} bytes exceed the tool read limit',
                                  size=len(raw), limit=self.limits.max_read_bytes)
        starts = _line_starts(item.text)
        start_byte = len(item.text[:start].encode('utf-8'))
        record = {
            'handle': handle, 'path': item.path, 'origin': 'DEPENDENCY' if item.identity else 'SOURCE',
            'symbol': declaration.symbol if declaration else None,
            'names': list(declaration.names) if declaration else [],
            'receiver': declaration.receiver if declaration else '',
            'kind': declaration.kind if declaration else 'line_range',
            'parser_kind': declaration.parser_kind if declaration else 'EXPLICIT_LINE_RANGE',
            'complete_declaration': declaration is not None,
            'start_line': _line_of(starts, start), 'end_line': _end_line(starts, item.text, end),
            'start_char': start, 'end_char': end, 'start_byte': start_byte, 'end_byte': start_byte + len(raw),
            'text': body, 'bytes': len(raw), 'content_digest': _sha(raw), 'file_digest': item.digest,
            'snapshot_id': None if item.identity else self.snapshot.snapshot_id,
            'parse_status': item.parse_status,
        }
        if item.identity:
            record['dependency'] = dict(item.identity)
            record['read_only'] = True
        return record

    # ------------------------------------------------------- follow (Go)
    def follow_dependency(self, handle: str, *, selector: str | None = None,
                          page_size: int = 25, cursor: str | None = None) -> dict[str, Any]:
        """Navigate from a read Go declaration through a real import or receiver.

        `selector` is `alias.Name`, `alias` (list the imported package) or a
        bare `Name`/`recv.Method` call in the declaration. Bindings come from
        the file's import declarations; a shadowed alias, an unknown receiver
        type or several candidates are returned as unresolved/ambiguous with
        candidates, never as a guessed unique edge.
        """
        from contractedge.retrieval.gap_delivery import _go_identifier_shadowed, _go_import_specs
        if handle not in self._handles:
            raise NavigationError('UNKNOWN_HANDLE', 'Handle was not returned by this tool for this task')
        item, declaration, start, end = self._handles[handle]
        if not item.path.endswith('.go') or declaration is None:
            raise NavigationError('UNSUPPORTED_FOLLOW', 'Follow needs a Go declaration handle')
        code = mask_go_code(item.text[start:end])
        opening = code.find('{')
        signature, body = (code[:opening], code[opening:]) if opening >= 0 else (code, '')
        selector = (selector or '').strip()
        imports: dict[str, set[str]] = {}
        # Import pairs carry no coordinates, so CRLF can be normalized safely here.
        for alias, imported in _go_import_specs(item.text.replace('\r\n', '\n')):
            imports.setdefault(alias, set()).add(imported)
        if '.' in selector:
            qualifier, name = selector.split('.', 1)
        elif selector in imports:
            qualifier, name = selector, ''      # list the imported package
        else:
            qualifier, name = '', selector      # bare same-package call
        if not (qualifier or name):
            raise NavigationError('EMPTY_SELECTOR', 'Provide alias.Name, alias, Name or recv.Method')
        binding: dict[str, Any] = {'from': handle, 'selector': selector}
        if qualifier and qualifier in imports:
            if _go_identifier_shadowed(qualifier, signature, body):
                binding.update(status='UNRESOLVED', reason='Import alias is shadowed inside the declaration')
                return {'binding': binding, 'hits': [], 'next_cursor': None}
            if len(imports[qualifier]) != 1 or qualifier in {'_', '.'}:
                binding.update(status='AMBIGUOUS', reason='Alias names several imports',
                               imports=sorted(imports[qualifier]))
                return {'binding': binding, 'hits': [], 'next_cursor': None}
            imported = next(iter(imports[qualifier]))
            binding.update(imported=imported)
            return self._follow_import(imported, name or None, binding, page_size, cursor)
        # Same-package function or method: receiver type is known only for the
        # declaration's own receiver variable; anything else stays a candidate set.
        directory = str(PurePosixPath(item.path).parent)
        testing = item.path.endswith('_test.go')
        package_files = [p for p in self._scope_files('' if directory == '.' else directory)
                         if p.endswith('.go') and str(PurePosixPath(p).parent) == directory
                         and (testing or not p.endswith('_test.go'))]
        owner = None
        if qualifier:
            recv_name = re.match(r'\s*func\s*\(\s*(\w+)\s', code)
            if recv_name and recv_name[1] == qualifier and declaration.receiver:
                owner = declaration.receiver
        candidates = []
        for relative in package_files:
            try:
                target = self._index(relative)
            except NavigationError:
                continue
            for d in target.declarations:
                if name in d.names and (owner is None or d.receiver == owner) \
                        and (qualifier or not d.receiver):
                    candidates.append(self._row(d, ['package_declaration']))
        binding.update(package_directory=directory, receiver_type=owner,
                       status='RESOLVED' if len(candidates) == 1 and (owner or not qualifier) else
                       'AMBIGUOUS' if candidates else 'NOT_FOUND')
        if qualifier and owner is None and candidates:
            binding['reason'] = 'Receiver type of the selector is not statically known here'
        return {'binding': binding, 'hits': candidates, 'next_cursor': None}

    def _follow_import(self, imported: str, name: str | None, binding: dict[str, Any],
                       page_size: int, cursor: str | None) -> dict[str, Any]:
        module = self._module_path()
        if module and (imported == module or imported.startswith(module + '/')):
            directory = imported[len(module):].strip('/')
            nested = [p for p in self.snapshot.file_digests if p != 'go.mod' and p.endswith('/go.mod')
                      and (directory + '/').startswith(str(PurePosixPath(p).parent) + '/')]
            if nested:
                # A nested module owns this subtree; its selection is not the main module's.
                binding.update(status='UNAVAILABLE', reason='Directory belongs to a nested Go module',
                               nested_modules=sorted(nested))
                return {'binding': binding, 'hits': [], 'next_cursor': None}
            files = [p for p in self._scope_files(directory or None)
                     if p.endswith('.go') and not p.endswith('_test.go')
                     and str(PurePosixPath(p).parent) == (directory or '.')]
            indexes = []
            for relative in files:
                try:
                    indexes.append(self._index(relative))
                except NavigationError as exc:
                    binding.setdefault('skipped_files', []).append({'path': relative, 'reason': exc.reason})
            binding.update(origin='MAIN_MODULE_PACKAGE', package_directory=directory or '.')
        elif self.dependencies is not None and self.dependencies.resolve(imported):
            if imported not in self._dependency_listings:
                self._dependency_listings[imported] = self.dependencies.package_files(imported)
            listing = self._dependency_listings[imported]
            indexes = []
            for member in listing.get('files', []):
                key = 'dep:' + member['identity']['archive_sha256'] + ':' + member['identity']['member']
                if key not in self._files:
                    idx = index_text(member['identity']['member'], member['text'], member['content_digest'])
                    idx.identity = dict(member['identity'])
                    self._register(idx)
                    self._files[key] = idx
                indexes.append(self._files[key])
            binding.update(origin='PINNED_DEPENDENCY', dependency_scan=listing.get('audit', []),
                           dependency_complete=bool(listing.get('complete')))
        else:
            binding.update(status='UNAVAILABLE', reason='Import is neither in the main module nor a pinned dependency')
            return {'binding': binding, 'hits': [], 'next_cursor': None}
        # Without a name, list the package's exported API (functions, types and
        # methods on exported types) so the caller need not guess an API name.
        rows = []
        for idx in indexes:
            for d in idx.declarations:
                if name is None:
                    if not (d.symbol[:1].isupper() and (not d.receiver or d.receiver[:1].isupper())):
                        continue
                elif name not in d.names:
                    continue
                rows.append(self._row(d, ['method_of_' + d.receiver] if d.receiver else ['package_declaration']))
        state = _decode_cursor(cursor)
        offset = int(state.get('o', 0))
        page = rows[offset:offset + page_size]
        more = offset + page_size < len(rows)
        if name is not None:
            binding['status'] = 'RESOLVED' if len([r for r in rows if r['kind'] != 'go_method']) == 1 \
                else 'AMBIGUOUS' if rows else 'NOT_FOUND'
        else:
            binding['status'] = 'PACKAGE_LISTING'
        if binding.get('origin') == 'PINNED_DEPENDENCY' and not binding.get('dependency_complete') \
                and binding['status'] == 'NOT_FOUND':
            binding['status'] = 'NOT_FOUND_SCAN_INCOMPLETE'
        return {'binding': binding, 'hits': page,
                'next_cursor': _encode_cursor({'o': offset + page_size}) if more else None}

    def _module_path(self) -> str | None:
        if 'go.mod' not in self.snapshot.file_digests:
            return None
        try:
            text, _ = self._verified_text('go.mod')
        except NavigationError:
            return None
        match = re.search(r'(?m)^\s*module\s+(\S+)\s*$', text)
        return match[1] if match else None
