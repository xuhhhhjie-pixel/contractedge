from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence


def build_direct_reference_edges(units: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    """Build auditable one-hop symbol edges; never infer transitive reachability."""
    declarations: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for unit in units:
        for symbol in unit.get("declared_symbols", ()):
            declarations[str(symbol)].append(unit)

    edges: list[dict[str, Any]] = []
    for source in units:
        source_id = str(source.get("id", ""))
        parser_kind = str(source.get("parser_kind", ""))
        for reference in source.get("references", ()):
            for target in declarations.get(str(reference), ()):
                target_id = str(target.get("id", ""))
                if not source_id or not target_id or source_id == target_id:
                    continue
                is_literal_template_include = parser_kind == "HELM_TEMPLATE_BLOCK" and str(reference) in source.get("references", ())
                edge_kind = "SYNTACTIC" if is_literal_template_include else "INFERRED"
                edges.append({
                    "source": source_id,
                    "target": target_id,
                    "edge_kind": edge_kind,
                    "mechanism": "LITERAL_TEMPLATE_INCLUDE" if is_literal_template_include else "LEXICAL_SYMBOL_REFERENCE",
                    "evidence": {
                        "source_path": source.get("path"),
                        "source_locator": source.get("locator"),
                        "target_path": target.get("path"),
                        "target_locator": target.get("locator"),
                        "parser_kind": parser_kind,
                        "reference": str(reference),
                    },
                })
    unique = {(edge["source"], edge["target"], edge["mechanism"]): edge for edge in edges}
    return tuple(unique[key] for key in sorted(unique))
