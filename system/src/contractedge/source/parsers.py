from __future__ import annotations

import hashlib
import ast
import re
from pathlib import PurePosixPath
from typing import Any, Iterable

import yaml
from yaml.nodes import MappingNode, Node, SequenceNode


_CONTROL_WORDS = {"if", "for", "switch", "select", "while", "catch", "return", "sizeof", "typeof", "new", "function"}
_GO_DECL = re.compile(r"(?m)^[ \t]*func\b|^[ \t]*type\s+[A-Za-z_]\w*\s+(?:struct|interface)\s*\{")
_JS_DECL = re.compile(
    r"(?m)^[ \t]*(?:export\s+|async\s+|default\s+)*(?:"
    r"function\s+\*?[A-Za-z_$][\w$]*"
    r"|class\s+[A-Za-z_$][\w$]*"
    r"|interface\s+[A-Za-z_$][\w$]*\s*\{"
    r"|type\s+[A-Za-z_$][\w$]*\s*=\s*\{"
    r"|(?:const|let|var)\s+[A-Za-z_$][\w$]*\s*=.*?=>)"
)
_CALL = re.compile(r"\b([A-Za-z_$][\w$]*)\s*\(")
_C_DECL = re.compile(r"(?m)^[ \t]*(?:(?:static|inline|extern|const|unsigned|signed|struct|enum)\s+)*[A-Za-z_]\w*(?:[ \t*]+[A-Za-z_]\w*)*[ \t*]+[A-Za-z_]\w*\s*\([^;{}]*\)\s*\{")
_JAVA_DECL = re.compile(r"(?m)^[ \t]*(?:(?:public|private|protected|static|final|abstract|synchronized|native|default)\s+)*(?:(?:class|interface|enum)\s+[A-Za-z_]\w*[^;{}]*\{|[A-Za-z_][\w<>\[\]., ?]*\s+[A-Za-z_]\w*\s*\([^;{}]*\)\s*(?:throws\s+[^{}]+)?\{)")
_HELM_ACTION = re.compile(r"\{\{[-]?\s*(.*?)\s*[-]?\}\}", re.DOTALL)


def _unit(snapshot_id: str, path: str, text: str, start: int, end: int, parser_kind: str, *,
          unit_kind: str, symbol: str | None = None,
          declared: Iterable[str] = (), references: Iterable[str] = (),
          max_chars: int = 16_000, extra: dict[str, Any] | None = None,
          start_line_number: int | None = None, end_line_number: int | None = None) -> dict[str, Any]:
    start = max(0, min(start, len(text)))
    end = max(start, min(end, len(text)))
    fragment = text[start:end]
    start_line = start_line_number if start_line_number is not None else text.count("\n", 0, start) + 1
    if end_line_number is None:
        end_line = text.count("\n", 0, end) + (1 if end > start and not text[:end].endswith("\n") else 0)
    else:
        end_line = end_line_number
    end_line = max(start_line, end_line)
    clipped = len(fragment) > max_chars
    if clipped:
        fragment = fragment[:max_chars]
    locator = {"start_line": start_line, "end_line": end_line, "column_start": 1}
    seed = f"{snapshot_id}:{path}:{start}:{end}"
    if clipped:
        seed+=':excerpt:'+hashlib.sha256(fragment.encode('utf-8')).hexdigest()
    item = {
        "id": "CU-" + hashlib.sha256(seed.encode()).hexdigest()[:16],
        "kind": unit_kind, "path": path, "locator": locator,
        "snapshot_id": snapshot_id, "content_digest": hashlib.sha256(fragment.encode("utf-8")).hexdigest(),
        "parser_kind": parser_kind, "text": fragment, "truncated": clipped,
        "declared_symbols": sorted(set(declared)), "references": sorted(set(references)),
    }
    if symbol:
        item["symbol"] = symbol
    if extra:
        item.update(extra)
    return item


def _find_brace(text: str, start: int) -> int | None:
    state = "code"
    quote = ""
    escaped = False
    i = start
    while i < len(text):
        char = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if state == "line_comment":
            if char == "\n":
                state = "code"
        elif state == "block_comment":
            if char == "*" and nxt == "/":
                state = "code"
                i += 1
        elif state == "string":
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                state = "code"
        else:
            if char == "/" and nxt == "/":
                state = "line_comment"
                i += 1
            elif char == "/" and nxt == "*":
                state = "block_comment"
                i += 1
            elif char in ('"', "'", "`"):
                state = "string"
                quote = char
            elif char == "{":
                return i
        i += 1
    return None


def _balanced_end(text: str, opening: int) -> int | None:
    state = "code"
    quote = ""
    escaped = False
    depth = 0
    i = opening
    while i < len(text):
        char = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if state == "line_comment":
            if char == "\n": state = "code"
        elif state == "block_comment":
            if char == "*" and nxt == "/": state = "code"; i += 1
        elif state == "string":
            if escaped: escaped = False
            elif char == "\\": escaped = True
            elif char == quote: state = "code"
        else:
            if char == "/" and nxt == "/": state = "line_comment"; i += 1
            elif char == "/" and nxt == "*": state = "block_comment"; i += 1
            elif char in ('"', "'", "`"): state = "string"; quote = char
            elif char == "{": depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return i + 1
        i += 1
    return None


def _decl_name(header: str, extension: str) -> str | None:
    if extension in {'.c', '.java'}:
        match = re.search(r'\b(?:class|interface|enum)\s+([A-Za-z_]\w*)', header)
        if match:
            return match.group(1)
        match = re.search(r'([A-Za-z_]\w*)\s*\(', header)
        return match.group(1) if match else None
    if extension == ".go":
        match = re.search(r"\bfunc\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\(", header)
        if match:
            return match.group(1)
        match = re.search(r"\btype\s+([A-Za-z_]\w*)\s+(?:struct|interface)", header)
        return match.group(1) if match else None
    match = re.search(
        r"\bfunction\s+\*?([A-Za-z_$][\w$]*)"
        r"|\bclass\s+([A-Za-z_$][\w$]*)"
        r"|\binterface\s+([A-Za-z_$][\w$]*)"
        r"|\btype\s+([A-Za-z_$][\w$]*)\s*=\s*\{"
        r"|(?:const|let|var)\s+([A-Za-z_$][\w$]*)",
        header,
    )
    return next((value for value in match.groups() if value), None) if match else None


def _code_units(path: str, text: str, snapshot_id: str, extension: str, max_chars: int) -> list[dict[str, Any]]:
    pattern = {'.go': _GO_DECL, '.c': _C_DECL, '.java': _JAVA_DECL}.get(extension, _JS_DECL)
    output = []
    for match in pattern.finditer(text):
        line_start = text.rfind("\n", 0, match.start()) + 1
        opening = _find_brace(text, match.start())
        if opening is None:
            continue
        end = _balanced_end(text, opening)
        if end is None:
            continue
        block = text[line_start:end]
        name = _decl_name(block, extension)
        if not name:
            continue
        references = {m.group(1) for m in _CALL.finditer(block)} - _CONTROL_WORDS - {name}
        output.append(_unit(snapshot_id, path, text, line_start, end,
                            {'.go': 'GO_LEXICAL_DECLARATION', '.c': 'C_LEXICAL_DECLARATION',
                             '.java': 'JAVA_LEXICAL_DECLARATION'}.get(extension, 'JS_TS_LEXICAL_DECLARATION'),
                            unit_kind="FUNCTION_OR_TYPE", symbol=name, declared=[name], references=references,
                            max_chars=max_chars))
    return output


def _yaml_units(path: str, text: str, snapshot_id: str, max_chars: int,
                parser_kind: str = "YAML_NODE_TREE") -> list[dict[str, Any]]:
    docs = list(yaml.compose_all(text, Loader=yaml.SafeLoader))
    output: list[dict[str, Any]] = []
    line_starts = [0]
    line_starts.extend(match.end() for match in re.finditer("\n", text))
    if line_starts[-1] != len(text):
        line_starts.append(len(text))

    def line_offset(line: int) -> int:
        return line_starts[min(max(line, 0), len(line_starts) - 1)]

    def walk(node: Node, path_parts: tuple[str, ...], depth: int) -> None:
        if not isinstance(node, (MappingNode, SequenceNode)) or depth > 2:
            return
        start = line_offset(node.start_mark.line)
        end = line_offset(node.end_mark.line + 1)
        if end <= start:
            end = len(text)
        path_label = ".".join(path_parts) or "$"
        output.append(_unit(snapshot_id, path, text, start, end, parser_kind,
                            unit_kind="CONFIG_SUBTREE", max_chars=max_chars,
                            extra={"node_path": path_label, "node_tag": node.tag},
                            start_line_number=node.start_mark.line + 1,
                            end_line_number=node.end_mark.line + 1))
        if isinstance(node, MappingNode):
            for key_node, value_node in node.value:
                key = str(getattr(key_node, "value", "?"))
                walk(value_node, path_parts + (key,), depth + 1)
        else:
            for index, child in enumerate(node.value):
                walk(child, path_parts + (str(index),), depth + 1)

    for index, document in enumerate(docs):
        if document is not None:
            walk(document, (f"document[{index}]",), 0)
    dedup = {}
    for item in output:
        locator = item["locator"]
        dedup.setdefault((locator["start_line"], locator["end_line"], item.get("node_path")), item)
    return list(dedup.values())


def _helm_units(path: str, text: str, snapshot_id: str, max_chars: int) -> list[dict[str, Any]]:
    actions = list(_HELM_ACTION.finditer(text))
    output = []
    i = 0
    while i < len(actions):
        action = actions[i]
        content = action.group(1).strip()
        define = re.match(r'define\s+"([^"]+)"', content)
        if not define:
            i += 1
            continue
        depth = 1
        j = i + 1
        while j < len(actions) and depth:
            word = actions[j].group(1).strip().split(maxsplit=1)[0] if actions[j].group(1).strip() else ""
            if word in {"if", "range", "with", "define", "block"}:
                depth += 1
            elif word == "end":
                depth -= 1
            j += 1
        if depth == 0:
            end = actions[j - 1].end()
            output.append(_unit(snapshot_id, path, text, action.start(), end, "HELM_TEMPLATE_BLOCK",
                                unit_kind="TEMPLATE_HELPER", symbol=define.group(1), declared=[define.group(1)],
                                references=re.findall(r'include\s+"([^"]+)"', text[action.start():end]),
                                max_chars=max_chars))
            i = j
        else:
            i += 1
    return output


def _bound_units(units: list[dict[str, Any]], text: str, max_lines: int,
                 max_chars: int) -> list[dict[str, Any]]:
    """Apply common line/character limits and re-digest the exposed excerpt."""
    lines = text.splitlines(keepends=True)
    bounded = []
    line_limit = max(1, max_lines)
    char_limit = max(1, max_chars)
    for source_unit in units:
        unit = dict(source_unit)
        locator = dict(unit["locator"])
        start_line = max(1, int(locator["start_line"]))
        old_end_line = int(locator["end_line"])
        end_line = min(len(lines), old_end_line, start_line + line_limit - 1)
        excerpt = "".join(lines[start_line - 1:end_line])
        truncated = bool(unit.get("truncated")) or end_line < old_end_line or len(excerpt) > char_limit
        excerpt = excerpt[:char_limit]
        locator["end_line"] = end_line
        unit["locator"] = locator
        unit["text"] = excerpt
        unit["truncated"] = truncated
        unit["content_digest"] = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
        if truncated:
            # A clipped prefix and its subsequently restored declaration cannot
            # share an evidence ID while carrying different text/locations.
            unit['id']='CU-'+hashlib.sha256((source_unit['id']+':'+str(start_line)+':'+str(end_line)+':'+unit['content_digest']).encode()).hexdigest()[:16]
        if excerpt.strip():
            bounded.append(unit)
    return bounded


def _python_units(path: str, text: str, snapshot_id: str, max_chars: int) -> list[dict[str, Any]]:
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    output = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        start_line = min([node.lineno] + [d.lineno for d in node.decorator_list])
        end_line = node.end_lineno or node.lineno
        references = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                if isinstance(child.func, ast.Name): references.add(child.func.id)
                elif isinstance(child.func, ast.Attribute): references.add(child.func.attr)
        output.append(_unit(snapshot_id, path, text, offsets[start_line - 1], offsets[end_line],
            'PYTHON_AST_DECLARATION', unit_kind='SOURCE_DECLARATION', symbol=node.name,
            declared=[node.name], references=references, max_chars=max_chars))
    return output


def parse_source_units(path: str, text: str, snapshot_id: str, *, max_lines: int = 80,
                       max_chars: int = 16_000) -> list[dict[str, Any]]:
    extension = PurePosixPath(path).suffix.lower()
    templated_yaml = extension in {".yaml", ".yml"} and _HELM_ACTION.search(text) is not None
    try:
        if extension in {".yaml", ".yml", ".json"}:
            if templated_yaml:
                # Text resource boundaries are useful even though Helm is not a YAML AST.
                cuts=[0]+[m.end() for m in re.finditer(r'(?m)^---[ \t]*\r?\n',text)]+[len(text)]
                resources=[]
                for start,end in zip(cuts,cuts[1:]):
                    body=text[start:end];kind=re.search(r'(?m)^kind:\s*([A-Za-z][A-Za-z0-9]*)',body)
                    if kind:
                        resources.append(_unit(snapshot_id,path,text,start,end,'HELM_RESOURCE_DOCUMENT_TEXT',
                            unit_kind='SOURCE_TEXT',max_chars=max_chars,extra={'resource_kind':kind[1],
                            'association_status':'Literal Helm document boundary, not a parsed template AST or edit grant'}))
                if resources:
                    return _bound_units(resources,text,max_lines,max_chars)
            if not templated_yaml:
                units = _yaml_units(path, text, snapshot_id, max_chars,
                                    "JSON_NODE_TREE" if extension == ".json" else "YAML_NODE_TREE")
                if units:
                    return _bound_units(units, text, max_lines, max_chars)
        elif extension in {".tpl", ".gotpl"}:
            units = _helm_units(path, text, snapshot_id, max_chars)
            if units:
                return _bound_units(units, text, max_lines, max_chars)
        elif extension == '.py':
            units = _python_units(path, text, snapshot_id, max_chars)
            if units:
                return _bound_units(units, text, max_lines, max_chars)
        elif extension in {".go", ".ts", ".tsx", ".js", ".jsx", '.c', '.h', '.java'}:
            language = extension if extension in {'.go', '.java'} else '.c' if extension in {'.c', '.h'} else '.js'
            units = _code_units(path, text, snapshot_id, language, max_chars)
            if units:
                return _bound_units(units, text, max_lines, max_chars)
    except (yaml.YAMLError, SyntaxError):
        pass

    lines = text.splitlines(keepends=True)
    output = []
    fallback_parser = "HELM_YAML_TEMPLATE_FALLBACK" if templated_yaml else "BOUNDED_TEXT_FALLBACK"
    for start_line in range(0, max(1, len(lines)), max(1, max_lines)):
        end_line = min(len(lines), start_line + max(1, max_lines))
        start = sum(len(line) for line in lines[:start_line])
        end = min(len(text), sum(len(line) for line in lines[:end_line]))
        if text and end <= start:
            end = len(text)
        if text[start:end].strip():
            output.append(_unit(snapshot_id, path, text, start, end, fallback_parser,
                                unit_kind="SOURCE_TEXT", max_chars=max_chars))
    return output
