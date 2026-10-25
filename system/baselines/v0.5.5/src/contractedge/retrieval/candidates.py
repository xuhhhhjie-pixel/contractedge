from __future__ import annotations

import re
import math
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from contractedge.retrieval.edges import build_direct_reference_edges


class TokenCounterUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class CandidateSet:
    candidates: tuple[Mapping[str, Any], ...]
    selected_ids: tuple[str, ...]
    dropped_ids: tuple[str, ...]
    ranking_features: Mapping[str, Mapping[str, int]]
    edges: tuple[Mapping[str, Any], ...]
    context_tokens: int
    max_context_tokens: int
    top_n: int
    extension_used: bool = False
    drop_reasons: Mapping[str, str] | None = None
    supplemental_evidence: tuple[Mapping[str, Any], ...] = ()
    context_audit: tuple[Mapping[str, Any], ...] = ()

    @property
    def selected_count(self) -> int:
        return len(self.selected_ids)


def _terms(value: str) -> set[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value).replace('_', ' ')
    stop = {'the', 'and', 'for', 'that', 'this', 'with', 'from', 'when', 'then', 'must', 'should',
            'would', 'could', 'have', 'has', 'had', 'are', 'was', 'were', 'been', 'being', 'into',
            'without', 'also', 'instead', 'than', 'through', 'after', 'before', 'even', 'though',
            'while', 'its', 'their', 'these', 'those', 'not', 'both', 'only', 'supplied', 'provided', 'is', 'be'}
    terms = {token.casefold() for token in re.findall(r"[A-Za-z0-9_]+", spaced)
             if len(token) > 1 and token.casefold() not in stop}
    # Share adopt/adopts, group/groups etc. without losing the original token.
    return terms | {t[:-1] for t in terms if len(t) > 4 and t.endswith('s') and not t.endswith('ss')}


def literal_identifiers(requirement: str) -> set[str]:
    quoted = re.findall(r'`([^`\n]+)`', requirement)
    camel = [t for t in re.findall(r'[A-Za-z_$][A-Za-z0-9_$]*', requirement)
             if re.search(r'[a-z][A-Z]', t) or '_' in t]
    return {s.casefold() for s in [*quoted, *camel] if s.strip()}


def _identity(candidate: Mapping[str, Any]) -> tuple[str, str, str]:
    locator = candidate.get("locator", {})
    return (str(candidate.get("snapshot_id", "")), str(candidate.get("path", "")),
            repr(sorted(locator.items())) if isinstance(locator, Mapping) else repr(locator))


def _rank(candidates: Sequence[Mapping[str, Any]], requirement: str, observable: str | None):
    requirement_terms = _terms(requirement)
    observable_terms = _terms(observable or "")
    identifiers = literal_identifiers(requirement)
    unique: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for candidate in candidates:
        identity = _identity(candidate)
        if identity not in unique:
            unique[identity] = candidate
        else:
            previous = unique[identity]
            unique[identity] = {**previous, **candidate,
                'anchor_priority': max(previous.get('anchor_priority', 0), candidate.get('anchor_priority', 0)),
                'evidence_gap_refs': sorted(set(previous.get('evidence_gap_refs', [])) | set(candidate.get('evidence_gap_refs', [])))}
    from contractedge.retrieval.frontier import production_frontier
    sibling_ids = {r['source_evidence_id'].removeprefix('SRC-')
                   for r in production_frontier(list(unique.values()), requirement)}
    features: dict[str, dict[str, int]] = {}
    for candidate in unique.values():
        text = str(candidate.get("text", ""))
        content_terms = _terms(text)
        path_terms = _terms(str(candidate.get("path", "")))
        exact = len(requirement_terms & (content_terms | path_terms))
        observed = len(observable_terms & content_terms) if observable else 0
        requirement_hits = len(requirement_terms & content_terms)
        features[str(candidate["id"])] = {"lexical_sibling_candidate": int(candidate["id"] in sibling_ids),
                                           "exact_identifier_hits": exact,
                                           "resource_kind_hits": int(str(candidate.get("resource_kind","")).casefold() in requirement_terms or str(candidate.get("resource_kind","")).casefold()+"s" in requirement_terms) if candidate.get("resource_kind") else 0,
                                           "literal_identifier_hits": sum(t in text.casefold() for t in identifiers),
                                           "mechanism_symbol_hits": len(requirement_terms & _terms(' '.join(
                                               [*map(str, candidate.get('declared_symbols', [])), *map(str, candidate.get('references', []))]))),
                                           "anchor_priority": int(candidate.get('anchor_priority', 0)),
                                           "observable_term_hits": observed,
                                           "requirement_term_hits": requirement_hits,
                                           "hop_distance": int(candidate.get("hop_distance", 0))}
    ordered = sorted(unique.values(), key=lambda item: (
        -features[str(item['id'])]['anchor_priority'],
        -features[str(item["id"])]["exact_identifier_hits"],
        -features[str(item["id"])]["observable_term_hits"],
        -features[str(item["id"])]["requirement_term_hits"],
        features[str(item["id"])]["hop_distance"], str(item.get("path", "")),
        int(item.get("locator", {}).get("start_line", 0)) if isinstance(item.get("locator"), Mapping) else 0,
    ))
    return ordered, features


def _overlap(a, b):
    if (a.get('path'), a.get('origin_kind'), a.get('snapshot_id')) != (b.get('path'), b.get('origin_kind'), b.get('snapshot_id')):
        return 0.0
    x, y = a.get('locator', {}), b.get('locator', {})
    a0, a1, b0, b1 = x.get('start_line'), x.get('end_line'), y.get('start_line'), y.get('end_line')
    if not all(type(v) is int for v in (a0, a1, b0, b1)):
        return 0.0
    return max(0, min(a1, b1) - max(a0, b0) + 1) / max(1, min(a1 - a0 + 1, b1 - b0 + 1))


def _select(ordered, features, edges, *, top_n: int, max_context_tokens: int,
            token_counter: Callable[[str], int], extension_used: bool) -> CandidateSet:
    if token_counter is None:
        raise TokenCounterUnavailable("an explicit model tokenizer/token counter is required")
    selected: list[str] = []
    dropped: list[str] = []
    reasons: dict[str, str] = {}
    total = 0
    selected_payload: list[Mapping[str, Any]] = []
    remaining = list(ordered)
    while remaining:
        def marginal(item):
            f = features[str(item['id'])]
            same_path = sum((p.get('path'), p.get('origin_kind')) == (item.get('path'), item.get('origin_kind'))
                            for p in selected_payload)
            overlap = max((_overlap(item, p) for p in selected_payload), default=0)
            relevance = (1 + f['exact_identifier_hits'] + f['observable_term_hits']
                         + 12 * f.get('literal_identifier_hits', 0) + 2 * f.get('mechanism_symbol_hits', 0))
            scope_cost = 1 + math.log2(1 + max(0, int(item.get('scope_path_rank', 0)))) / 2
            covered_gaps = {g for p in selected_payload for g in p.get('evidence_gap_refs', [])}
            new_gaps = set(item.get('evidence_gap_refs', [])) - covered_gaps
            # A restored, query-related declaration answers a body-coverage
            # gap more directly than its import/header fallback. Soft preference
            # only; documents, configuration and necessary text remain eligible.
            restored = (bool(item.get('expanded_from_unit_id')) and not item.get('truncated')
                        and f.get('mechanism_symbol_hits', 0) > 0)
            score = ((1 + 8 * f['anchor_priority']) * relevance * (4 if f.get('resource_kind_hits') else 1) * (1 - .9 * overlap)
                     * (1 + 4 * len(new_gaps)) * (2 if restored else 1)
                     * (2 if f.get("lexical_sibling_candidate") else 1)
                     / ((1 + same_path) * scope_cost))
            return score
        # Stable initial ordering resolves equal marginal scores. No category is
        # excluded and no file quota forces irrelevant evidence into the request.
        candidate = max(remaining, key=marginal)
        remaining.remove(candidate)
        cid = str(candidate["id"])
        if len(selected) >= top_n:
            dropped.append(cid)
            reasons[cid] = "TOP_N_LIMIT"
            continue
        cost = int(token_counter(str(candidate.get("text", ""))))
        if cost < 0:
            raise ValueError("token counter cannot return a negative count")
        if total + cost > max_context_tokens:
            dropped.append(cid)
            reasons[cid] = "CONTEXT_BUDGET"
            continue
        selected.append(cid)
        features[cid] = {**features[cid], 'selection_step': len(selected),
                         'new_gap_ids': sorted(set(candidate.get('evidence_gap_refs', [])) -
                            {g for p in selected_payload for g in p.get('evidence_gap_refs', [])}),
                         'max_overlap_percent': int(100 * max((_overlap(candidate, p) for p in selected_payload), default=0))}
        selected_payload.append(candidate)
        total += cost
    selected_set = set(selected)
    valid_edges = tuple(dict(edge) for edge in edges
                        if edge.get("source") in selected_set and edge.get("target") in selected_set
                        and edge.get("edge_kind") in {"SYNTACTIC", "INFERRED"})
    return CandidateSet(tuple(selected_payload), tuple(selected), tuple(dropped), features, valid_edges,
                        total, max_context_tokens, top_n, extension_used, reasons)


def trim_context_bundle(candidate_set: CandidateSet, *, request_tokens: int, limit: int) -> CandidateSet:
    """Remove the lowest-priority connected evidence bundle, recording lost edges.

    Reference edges are retrieval hints, not proven causal relations. Isolated
    evidence is a one-element bundle; immutable input and source IDs stay intact.
    """
    from dataclasses import replace
    selected = list(candidate_set.candidates)
    neighbors = {str(c['id']): set() for c in selected}
    for edge in candidate_set.edges:
        a, b = str(edge['source']), str(edge['target'])
        if a in neighbors and b in neighbors:
            neighbors[a].add(b); neighbors[b].add(a)
    groups = []
    unseen = set(neighbors)
    while unseen:
        pending = [min(unseen)]; group = set()
        while pending:
            key = pending.pop()
            if key not in group:
                group.add(key); pending.extend(neighbors[key] - group)
        unseen -= group; groups.append(group)
    if not groups:
        return candidate_set
    positions = {str(c['id']): i for i, c in enumerate(selected)}
    removed = max(groups, key=lambda g: (min(positions[k] for k in g), sorted(g)))
    kept = tuple(c for c in selected if str(c['id']) not in removed)
    edges = tuple(e for e in candidate_set.edges if str(e['source']) not in removed and str(e['target']) not in removed)
    return replace(candidate_set, candidates=kept, selected_ids=tuple(str(c['id']) for c in kept),
        dropped_ids=tuple(dict.fromkeys([*candidate_set.dropped_ids, *sorted(removed)])), edges=edges,
        drop_reasons={**(candidate_set.drop_reasons or {}), **{k: 'FULL_REQUEST_CONTEXT_BUNDLE' for k in removed}},
        context_audit=candidate_set.context_audit + ({'request_tokens_before': request_tokens, 'limit': limit,
            'removed_ids': sorted(removed), 'removed_reference_edges': len(candidate_set.edges) - len(edges),
            'interpretation': 'Bounded reference bundle removed; no causal correctness claim'},))


def build_candidate_set(requirement: str, observable: str | None, units: Sequence[Mapping[str, Any]],
                        edges: Sequence[Mapping[str, Any]], *, top_n: int,
                        max_context_tokens: int, token_counter: Callable[[str], int] | None) -> CandidateSet:
    if top_n <= 0 or max_context_tokens <= 0:
        raise ValueError("top_n and max_context_tokens must be positive")
    ordered, features = _rank(units, requirement, observable)
    return _select(ordered, features, edges, top_n=top_n, max_context_tokens=max_context_tokens,
                   token_counter=token_counter, extension_used=False)


def extend_once(candidate_set: CandidateSet, evidence_gaps: Sequence[Mapping[str, Any]],
                extension_candidates: Sequence[Mapping[str, Any]], *, max_context_tokens: int,
                token_counter: Callable[[str], int] | None) -> CandidateSet:
    if candidate_set.extension_used:
        raise ValueError("candidate set permits only one evidence-gap extension")
    locatable = [gap for gap in evidence_gaps
                 if any((gap.get("locator_hint") or {}).get(key) for key in ("search_term", "path", "symbol"))]
    if not locatable:
        raise ValueError("an evidence-gap extension requires a locatable gap")
    combined = list(candidate_set.candidates) + list(extension_candidates)
    search_terms = []
    for gap in locatable:
        hint = gap.get("locator_hint") or {}
        search_terms.extend(str(hint.get(key, "")) for key in ("search_term", "path", "symbol"))
    # A located gap takes priority over initial anchors. Otherwise Top-N=1
    # could keep the original seed forever and never inspect its direct callee.
    combined = [{**dict(item), 'anchor_priority': 3}
                if any(term and term.casefold() in (str(item.get('path', '')) + '\n' +
                           str(item.get('text', ''))).casefold() for term in search_terms)
                else item for item in combined]
    gap_ids = {str(gap.get('id', '')) for gap in locatable}
    combined = [{**dict(item), 'anchor_priority': 4}
                if item.get('scope_extension') in {'EVIDENCE_GAP_SEED_UNIT', 'EVIDENCE_GAP_DIRECT_NEIGHBOR', 'EVIDENCE_GAP_EXACT_PATH', 'EVIDENCE_GAP_DIRECTORY_PATH'}
                and gap_ids.intersection(item.get('evidence_gap_refs', [])) else item
                for item in combined]
    ordered, features = _rank(combined, " ".join(search_terms), None)
    combined_edges = build_direct_reference_edges(combined)
    return _select(ordered, features, combined_edges, top_n=candidate_set.top_n,
                   max_context_tokens=max_context_tokens, token_counter=token_counter, extension_used=True)
