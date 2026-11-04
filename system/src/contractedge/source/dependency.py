"""Bounded, read-only Go APIs from the task's hash-pinned public archives.

The reader owns provenance and package completeness. Callers own selection and
delivery budgets. These records are API context, never editable or causal
evidence. No archive is extracted and no network/cache lookup is performed.
"""
from __future__ import annotations

import hashlib
import json
import re
import tarfile
import time
from pathlib import Path, PurePosixPath

from contractedge.dataset.benchmark import confined


_PLATFORM = re.compile(r'_(?:aix|android|darwin|dragonfly|freebsd|illumos|ios|js|linux|netbsd|openbsd|plan9|solaris|wasip1|windows|386|amd64|arm|arm64|loong64|mips|mips64|mips64le|mipsle|ppc64|ppc64le|riscv64|s390x|wasm)\.go$')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


class PublicGoDependencies:
    """Resolve the existing task authority once, then read exact pinned packages."""

    def __init__(self, snapshot, case_id, capabilities):
        self.snapshot = snapshot
        self.root = Path(snapshot.source_root).resolve()
        module_text = self.current_text('go.mod')
        if re.search(r'(?m)^\s*(?:replace|exclude)\b', module_text):
            raise ValueError('Replaced or excluded Go modules require effective selection evidence')
        self.versions = {}
        requiring = False
        for line in module_text.splitlines():
            line = line.split('//', 1)[0].strip()
            if line == 'require (':
                requiring = True
                continue
            if line == ')':
                requiring = False
                continue
            declaration = line if requiring else line.removeprefix('require ') if line.startswith('require ') else ''
            match = re.fullmatch(r'([a-z0-9_.~/-]+)\s+(v[0-9][\w.+-]*)', declaration)
            if match:
                if match[1] in self.versions and self.versions[match[1]] != match[2]:
                    raise ValueError('Conflicting module requirements')
                self.versions[match[1]] = match[2]
        if 'vendor/modules.txt' in snapshot.file_digests:
            raise ValueError('Vendored dependency selection is unsupported')
        workspace = None
        for parent in list(self.root.parents)[:4]:
            path = parent / 'workspace.json'
            if not path.is_file() or path.is_symlink() or path.stat().st_size > 65536:
                continue
            record = json.loads(path.read_text())
            if Path(record.get('source_root', '')).resolve() == self.root:
                workspace = parent
                break
        if workspace is None or record.get('private_materials_loaded') is not False or record.get('case_id') != case_id:
            raise ValueError('No matching public workspace')
        if any((parent / 'go.work').exists() for parent in [self.root, *self.root.parents]
               if parent == workspace or workspace in parent.parents):
            raise ValueError('Go workspace dependency selection is unsupported')
        self.inputs = Path(record['inputs_root'])
        if self.inputs.is_symlink() or self.inputs.resolve().parent != workspace:
            raise ValueError('Public inputs escape their workspace')
        self.authority = (capabilities or {}).get('source', {})
        environment_path = confined(self.inputs, self.authority.get('path'))
        if environment_path.stat().st_size > 1024 * 1024:
            raise ValueError('Public environment size limit exceeded')
        raw = environment_path.read_bytes()
        if _sha(raw) != self.authority.get('sha256'):
            raise ValueError('Public environment hash mismatch')
        self.environment = json.loads(raw)

    def current_text(self, name):
        path = confined(self.root, name)
        if name not in self.snapshot.file_digests or path.stat().st_size > 512 * 1024:
            raise ValueError('Current source unavailable or changed')
        raw = path.read_bytes()
        if _sha(raw) != self.snapshot.file_digests[name]:
            raise ValueError('Current source unavailable or changed')
        return raw.decode('utf-8')

    def resolve(self, imported):
        modules = [m for m in self.versions if imported == m or imported.startswith(m + '/')]
        if not modules:
            return None
        module = max(modules, key=len)
        return module, self.versions[module], imported[len(module):].strip('/')

    def package(self, imported, *, package_name=None, max_member_bytes=65536):
        """Return complete verified files, or nothing if the scan is incomplete.

        max_member_bytes is a scan bound, independent of delivered excerpt size.
        The compiler's legacy whole-file selector can request its smaller bound.
        """
        from contractedge.retrieval.gap_delivery import _go_code
        target = self.resolve(imported)
        if target is None:
            return [], [], False
        module, version, package_path = target
        records, audit, seen, names = [], [], set(), set()
        complete = True
        for entry in self.environment.get('public_mounts', [])[:4]:
            if 'archive' not in entry or 'sha256' not in entry:
                continue
            try:
                archive = confined(self.inputs, entry['archive'])
                if archive.stat().st_size > 512 * 1024 * 1024:
                    raise ValueError('Dependency archive size/hash mismatch')
                needle = module + '@' + version + '/' + (package_path + '/' if package_path else '')
                expanded = scanned = source_bytes = source_files = 0
                with archive.open('rb') as stream:
                    digest = hashlib.sha256()
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(chunk)
                    if digest.hexdigest() != entry['sha256']:
                        raise ValueError('Dependency archive size/hash mismatch')
                    stream.seek(0)
                    started = time.monotonic()
                    with tarfile.open(fileobj=stream, mode='r|*') as tar:
                        for member in tar:
                            scanned += 1
                            expanded += member.size
                            if scanned > 100000 or expanded > 4 * 1024**3 or time.monotonic() - started > 12:
                                complete = False
                                break
                            path = PurePosixPath(member.name)
                            if path.is_absolute() or '..' in path.parts or '\\' in member.name or not member.isfile():
                                continue
                            pos = member.name.find(needle)
                            if pos < 0 or (pos and member.name[pos - 1] != '/'):
                                continue
                            name = member.name[pos + len(needle):]
                            if '/' in name or not name.endswith('.go') or name.endswith('_test.go'):
                                continue
                            source_files += 1
                            source_bytes += member.size
                            if source_files > 128 or source_bytes > 4 * 1024 * 1024 or member.size > min(max_member_bytes, 65536):
                                complete = False
                                break
                            raw = tar.extractfile(member).read(member.size + 1)
                            if len(raw) != member.size:
                                raise ValueError('Incomplete dependency declaration')
                            text = raw.decode('utf-8')
                            code = _go_code(text)
                            if '//go:build' in text or '// +build' in text or _PLATFORM.search(name):
                                complete = False
                                break
                            package = re.search(r'(?m)^\s*package\s+(\w+)\b', code)
                            if not package:
                                raise ValueError('Missing dependency package declaration')
                            names.add(package[1])
                            identity = (entry['sha256'], member.name)
                            if identity in seen:
                                raise ValueError('Duplicate dependency declaration')
                            seen.add(identity)
                            records.append(dict(id='PUBLICDEP-' + _sha((entry['sha256'] + member.name).encode())[:16],
                                kind='PUBLIC_DEPENDENCY_DECLARATION', module=module, version=version,
                                package=package[1], current_source_snapshot=self.snapshot.snapshot_id,
                                archive_path=entry['archive'], archive_sha256=entry['sha256'], member=member.name,
                                content_digest=_sha(raw), excerpt=text, read_only=True,
                                environment_sha256=self.authority['sha256'],
                                interpretation='Exact hash-pinned public module declaration; not an editable target, verified replacement or causal proof'))
                audit.append(dict(result='PUBLIC_DEPENDENCY_PACKAGE_SCAN', module=module, version=version,
                    archive_sha256=entry['sha256'], inspected_members=scanned, inspected_source_files=source_files,
                    inspected_source_bytes=source_bytes, complete=complete))
            except (OSError, ValueError, TypeError, KeyError, tarfile.TarError, UnicodeError) as exc:
                complete = False
                audit.append(dict(result='DEPENDENCY_API_UNAVAILABLE', reason=str(exc)))
        if len(names) > 1 or package_name is not None and names != {package_name}:
            complete = False
        return (records if complete else []), audit, complete


def _close(code, start):
    pairs = {'(': ')', '[': ']', '{': '}'}
    stack = []
    for offset in range(start, len(code)):
        char = code[offset]
        if char in pairs:
            stack.append(pairs[char])
        elif char in ')]}':
            if not stack or char != stack.pop():
                return None
            if not stack:
                return offset + 1
    return None


def _declarations(text):
    """Complete top-level functions and type declarations; unsupported forms abstain.

    This is a source locator, not a Go type checker. Grouped types stay together.
    All offsets refer to the original member, whose comments/strings are masked
    only for finding boundaries and matching API names.
    """
    from contractedge.retrieval.gap_delivery import _go_code
    code = _go_code(text)
    output = []
    for match in re.finditer(r'(?m)^[ \t]*(func|type)\b', code):
        start = match.start()
        if code[:start].count('{') != code[:start].count('}'):
            continue
        tail = code[match.end():]
        if match[1] == 'func':
            named = re.match(r'\s+([A-Za-z_]\w*)\s*\(', tail)
            if not named:
                continue  # Methods/generic/anonymous signatures are not package functions.
            params_start = match.end() + named.end() - 1
            params_end = _close(code, params_start)
            if params_end is None:
                continue
            opening = code.find('{', params_end)
            header = code[start:opening]
            if opening < 0 or re.search(r'\b(?:func|type|struct|interface)\b', code[params_end:opening]):
                continue
            end = _close(code, opening)
            if end is None:
                continue
            output.append(dict(names=[named[1]], kind='func', start=start, end=end,
                signature=header, parameters=code[params_start + 1:params_end - 1],
                returns=code[params_end:opening].strip(), code=code[start:end]))
        else:
            first = match.end() + len(tail) - len(tail.lstrip())
            if code[first:first + 1] == '(':
                end = _close(code, first)
                if end is None:
                    continue
                group = code[first + 1:end - 1]
                names = []
                for item in re.finditer(r'(?:^|[\n;])\s*([A-Za-z_]\w*)\s+', group):
                    prefix = group[:item.start(1)]
                    if all(prefix.count(left) == prefix.count(right) for left, right in [('(', ')'), ('[', ']'), ('{', '}')]):
                        names.append(item[1])
                # A whole grouped declaration is preserved rather than slicing its members.
            else:
                named = re.match(r'\s+([A-Za-z_]\w*)\s+', tail)
                if not named:
                    continue
                names = [named[1]]
                body_start = match.end() + named.end()
                end = body_start
                valid = True
                while end < len(code):
                    if code[end] in '([{':
                        closing = _close(code, end)
                        if closing is None:
                            valid = False
                            break
                        end = closing
                    elif code[end] in '\n;':
                        break
                    else:
                        end += 1
                if not valid:
                    continue
            if names:
                output.append(dict(names=names, kind='type', start=start, end=end,
                    code=code[start:end]))
    return output


def _span_record(member, declaration):
    text = member['excerpt']
    start, end = declaration['start'], declaration['end']
    excerpt = text[start:end]
    locator = dict(start_char=start, end_char=end, start_line=text.count('\n', 0, start) + 1,
        end_line=text.count('\n', 0, end) + (0 if text[:end].endswith('\n') else 1), truncated=False)
    row = dict(member)
    row.update(id='PUBLICDEP-' + _sha(json.dumps([member['archive_sha256'], member['member'], locator],
        sort_keys=True, separators=(',', ':')).encode())[:16], locator=locator,
        member_content_digest=member['content_digest'], content_digest=_sha(excerpt.encode()), excerpt=excerpt)
    return row


def gap_dependency_support(snapshot, gaps, selected_units, *, case_id,
                           public_execution_capabilities, max_bytes=24000, max_records=4,
                           already_supplied=None):
    """Locate an imported API and matching option factories for an existing gap.

    Queries must intersect qualified references in already selected, current
    production text. Prose cannot choose another archive or invent an import.
    Return types connect optional factories to the actual API's parameter type;
    lexical matching never establishes a causal relation or changes readiness.
    """
    from contractedge.retrieval.gap_delivery import _go_code, _go_import_specs, _go_import_declaration_spans
    if max_bytes <= 0 or max_records <= 0:
        return [], []
    candidates, audit = [], []
    for gap in gaps:
        hint = gap.get('locator_hint') or {}
        query = ' '.join(str(part) for part in (gap.get('summary', ''), hint.get('symbol', ''), hint.get('search_term', '')))
        qualified = set(re.findall(r'\b([A-Za-z_]\w*)\.([A-Z]\w*)\b', query))
        if not qualified:
            continue
        path_hint = hint.get('path')
        for unit in selected_units:
            path = unit.get('path') or (unit.get('locator') or {}).get('path')
            text = unit.get('text', unit.get('excerpt'))
            if (not isinstance(path, str) or not path.endswith('.go') or path.endswith('_test.go')
                    or path_hint and path != path_hint or not isinstance(text, str)
                    or unit.get('snapshot_id') != snapshot.snapshot_id
                    or unit.get('content_digest') != _sha(text.encode())):
                continue
            code = _go_code(text)
            for alias, name in sorted(qualified):
                if any(not code[:match.start()].rstrip().endswith('.') for match in re.finditer(
                        r'(?<![\w.])' + re.escape(alias) + r'\s*\.\s*' + re.escape(name) + r'\b', code)):
                    candidates.append((path, text, alias, name, str(hint.get('search_term', '')), gap.get('id')))
    if not candidates:
        return [], audit
    try:
        reader = PublicGoDependencies(snapshot, case_id, public_execution_capabilities)
    except (OSError, ValueError, KeyError, TypeError, UnicodeError) as exc:
        return [], [dict(result='DEPENDENCY_API_UNAVAILABLE', reason=str(exc))]
    targets = []
    for path, selected_text, alias, name, search, gap_id in candidates:
        try:
            actual = reader.current_text(path)
        except (OSError, ValueError, UnicodeError):
            continue
        if selected_text not in actual:
            continue
        imports = {imported for qualifier, imported in _go_import_specs(actual) if qualifier == alias and qualifier not in {'_', '.'}}
        if len(imports) != 1:
            continue
        # An imported qualifier is only used as the left side of a selector.
        # Outside import declarations, a bare occurrence can be a parameter,
        # grouped var/const, multi-assignment or named return shadowing it.
        # Reject that file/alias rather than guessing the binding or scope.
        code = _go_code(actual)
        for start, end in _go_import_declaration_spans(actual):
            code = code[:start] + ' ' * (end - start) + code[end:]
        if any(not code[match.end():].lstrip().startswith('.')
               for match in re.finditer(r'\b' + re.escape(alias) + r'\b', code)):
            continue
        imported = next(iter(imports))
        target = (imported, name, search, gap_id)
        if reader.resolve(imported) and target not in targets:
            targets.append(target)
    records, used, packages = [], 0, {}
    for imported, name, search, gap_id in targets[:2]:
        if imported not in packages:
            packages[imported] = reader.package(imported)
            audit.extend(packages[imported][1])
        members, _, complete = packages[imported]
        if not complete:
            continue
        declarations = [(member, declaration) for member in members for declaration in _declarations(member['excerpt'])]
        anchors = [(m, d) for m, d in declarations if name in d['names']]
        if len(anchors) != 1:
            audit.append(dict(result='DEPENDENCY_API_INCOMPLETE_OR_AMBIGUOUS', imported=imported, symbol=name))
            continue
        selected = list(anchors)
        types = {n: [(m, d) for m, d in declarations if d['kind'] == 'type' and n in d['names']]
                 for n in re.findall(r'\b[A-Z]\w*\b', anchors[0][1].get('parameters', ''))}
        types = {n: rows[0] for n, rows in types.items() if len(rows) == 1}
        if anchors[0][1]['kind'] == 'type':
            types[name] = anchors[0]
        selected.extend(types.values())
        terms = re.findall(r'[A-Za-z_][A-Za-z0-9_]*', search)[:4]
        if terms and types:
            selected.extend((m, d) for m, d in declarations if d['kind'] == 'func'
                and d.get('returns') in types and any(re.search(r'\b' + re.escape(term) + r'\b',
                    d['code'], re.IGNORECASE) for term in terms))
        seen = set()
        for member, declaration in selected:
            row = _span_record(member, declaration)
            if row['id'] in seen:
                continue
            seen.add(row['id'])
            if row in records or already_supplied is not None and already_supplied(row):
                audit.append(dict(result='ALREADY_DELIVERED_DEPENDENCY_API', id=row['id'], gap_id=gap_id))
                continue
            size = len(row['excerpt'].encode())
            if len(records) >= max_records or used + size > max_bytes:
                audit.append(dict(result='DEPENDENCY_API_DELIVERY_LIMIT', id=row['id'], gap_id=gap_id))
                continue
            records.append(row)
            used += size
            audit.append(dict(result='INCLUDED_PUBLIC_DEPENDENCY_API', id=row['id'], gap_id=gap_id,
                imported=imported, symbol=name, declaration_names=declaration['names'], excerpt_bytes=size))
    return records, audit
