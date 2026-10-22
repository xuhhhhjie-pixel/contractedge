from __future__ import annotations

from typing import Any, Mapping


def constrained_output_schema(schema: Mapping[str, Any], *, omit: set[str] | None = None) -> dict[str, Any]:
    """Lower a ContractEdge JSON Schema into Ollama's supported constrained-output subset.

    The lowered schema may be less restrictive where the native decoder does not support a
    contract keyword (currently ``anyOf``). The application always validates the full contract
    after generation, so this function must never be used as the final validator.
    """
    definitions = schema.get("$defs", {})

    def lower(value: Any, resolving: tuple[str, ...] = ()) -> Any:
        if isinstance(value, list):
            return [lower(item, resolving) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            reference = value["$ref"]
            prefix = "#/$defs/"
            if not isinstance(reference, str) or not reference.startswith(prefix):
                raise ValueError(f"unsupported schema reference: {reference!r}")
            key = reference[len(prefix):]
            if key not in definitions or key in resolving:
                raise ValueError(f"unknown or recursive schema reference: {reference!r}")
            resolved = lower(definitions[key], resolving + (key,))
            if not isinstance(resolved, dict):
                raise ValueError(f"schema reference must resolve to an object: {reference!r}")
            resolved.update({name: lower(item, resolving) for name, item in value.items() if name != "$ref"})
            return resolved
        if "oneOf" in value:
            branches = [lower(branch, resolving) for branch in value["oneOf"]]
            if not branches or any(not isinstance(branch, dict) or branch.get("type") != "object"
                                   for branch in branches):
                raise ValueError("only object oneOf schemas can be lowered")
            properties: dict[str, Any] = {}
            for branch in branches:
                for name, prop in branch.get("properties", {}).items():
                    if name not in properties:
                        properties[name] = prop
                    elif properties[name] != prop:
                        prior = properties[name]
                        if isinstance(prior, dict) and isinstance(prop, dict) \
                                and "enum" in prior and "enum" in prop:
                            properties[name] = {"enum": list(dict.fromkeys(prior["enum"] + prop["enum"]))}
                        else:
                            properties[name] = {}
            required_sets = [set(branch.get("required", [])) for branch in branches]
            merged: dict[str, Any] = {"type": "object", "properties": properties,
                                      "required": sorted(set.intersection(*required_sets))}
            if all(branch.get("additionalProperties") is False for branch in branches):
                merged["additionalProperties"] = False
            return merged

        result: dict[str, Any] = {}
        for name, item in value.items():
            if name in {"$schema", "$defs", "title", "description"}:
                continue
            if name == "const":
                result["enum"] = [item]
                continue
            if name == "anyOf":
                # Keep shared object properties and defer the disjunction to full validation.
                continue
            if name in {"allOf", "not", "if", "then", "else"}:
                raise ValueError(f"unsupported constrained-output schema keyword: {name}")
            result[name] = lower(item, resolving)
        if result.get("type") == "string":
            result.setdefault("maxLength", 600)
        if result.get("type") == "array":
            result.setdefault("maxItems", 32)
        return result

    lowered = lower(dict(schema))
    if not isinstance(lowered, dict):
        raise ValueError("output schema must be an object")
    omitted = omit or set()
    if omitted:
        lowered["required"] = [name for name in lowered.get("required", []) if name not in omitted]
        properties = lowered.get("properties", {})
        for name in omitted:
            properties.pop(name, None)
    for name, limit in {"requirement_horizon": 16, "inferred_causal_path": 8,
                        "unverified_outcomes": 16, "units": 16, "expected_relations": 12,
                        "violations": 12, "outcome_coverage": 32, "evidence_refs": 24,
                        "evidence_gaps": 4}.items():
        prop = lowered.get("properties", {}).get(name)
        if isinstance(prop, dict) and prop.get("type") == "array":
            prop["maxItems"] = limit
    return lowered
