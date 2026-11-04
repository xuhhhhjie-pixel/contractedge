from __future__ import annotations

import re
import math
import hashlib
from pathlib import PurePosixPath
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
    required_endpoint_ids: tuple[str, ...] = ()
    # Engine-only facts: selection and final request delivery are distinct.
    delivery_audit: tuple[Mapping[str, Any], ...] = ()
    # Keep the task across bounded evidence extensions. These fields live in
    # local traces; diagnosis requests explicitly project only selected units.
    ranking_requirement: str = ''
    ranking_observable: str | None = None

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
    uppercase = {t for t in re.findall(
        r'(?<![A-Za-z0-9_$])([A-Z][A-Z0-9_]{1,63})(?![A-Za-z0-9_$])', requirement)
        if _terms(t)}
    # Keep the established folded quoted/camel/underscore lookup. Uppercase
    # operation identifiers retain the distinction from CamelCase type names.
    return {s if s in uppercase else s.casefold() for s in [*quoted, *camel] if s.strip()} | uppercase


def literal_identifier_hits(text: str, identifiers: set[str]) -> int:
    """Count explicit literals, with exact case/token boundaries for ALLCAPS."""
    folded = text.casefold()
    return sum(bool(re.search(r'(?<![A-Za-z0-9_$])' + re.escape(token)
                             + r'(?![A-Za-z0-9_$])', text))
               if re.fullmatch(r'[A-Z][A-Z0-9_]{1,63}', token) else token in folded
               for token in identifiers)


def test_example_path(path: str) -> bool:
    """Filename heuristic used only to rank examples, never to exclude evidence."""
    source_path = PurePosixPath(path.casefold())
    return (bool(set(source_path.parts) & {'test', 'tests', '__tests__'})
            or source_path.name.endswith(('_test.go', '_test.py'))
            or source_path.name.startswith('test_')
            or bool(re.search(r'\.(?:test|spec)\.[^.]+$', source_path.name)))


def example_context_cost(candidate: Mapping[str, Any], identifiers: set[str]) -> int:
    """Soft eightfold discount for generic test examples sharing query words.

    Explicit source focus and exact requested declarations retain their prior
    priority. Test-only evidence remains eligible; no path proves semantics.
    """
    names = {str(candidate.get('symbol', '')), *map(str, candidate.get('declared_symbols', ()))} - {''}
    exact_declaration = any(token == name or token == name.casefold()
                            for token in identifiers for name in names)
    return (8 if test_example_path(str(candidate.get('path', '')))
            and not candidate.get('source_focus_basis') and not exact_declaration else 1)


def _source_origin(candidate: Mapping[str, Any]) -> tuple[str, str]:
    return (str(candidate.get('origin_kind') or 'SOURCE_SPAN'),
            str(candidate.get('artifact_id') or ''))


def _identity(candidate: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
    locator = candidate.get("locator", {})
    text = str(candidate.get('text', ''))
    declaration = candidate.get('declaration_locator')
    if isinstance(locator, Mapping) and isinstance(declaration, Mapping):
        a, b = locator.get('start_line'), locator.get('end_line')
        start, end = declaration.get('start_line'), declaration.get('end_line')
        if (all(type(v) is int for v in (a, b, start, end)) and a <= start <= end <= b
                and hashlib.sha256(text.encode()).hexdigest() == candidate.get('content_digest')):
            body = ''.join(text.splitlines(keepends=True)[start-a:end-a+1])
            if hashlib.sha256(body.encode()).hexdigest() == candidate.get('declaration_content_digest'):
                locator, text = declaration, body
    # Producers may omit the default first column; byte content and line
    # identity must still agree. Non-default columns remain distinct.
    if isinstance(locator, Mapping):
        locator = {k:v for k,v in locator.items() if not (k == 'column_start' and v == 1)}
    return (*_source_origin(candidate), str(candidate.get("snapshot_id", "")), str(candidate.get("path", "")),
            repr((sorted(locator.items()) if isinstance(locator, Mapping) else locator,
                  hashlib.sha256(text.encode()).hexdigest())) )


def requires_gap_delivery(unit: Mapping[str, Any]) -> bool:
    """Keep real/legacy required roles; nonmatching lexical context is optional.

    An exact anchor or explicit requirement always wins when identities merge.
    """
    roles = [role for role in unit.get("gap_role_keys", [])
             if not role.startswith(('SOURCE_CONTRACT:', 'SOURCE_READER:'))]
    return bool(unit.get("required_for_gap_delivery") or (roles and (
        unit.get("optional_gap_context") is not True or any(role.endswith(":anchor") for role in roles))))


def merge_gap_context_optional(*units: Mapping[str, Any]) -> bool:
    """A merged role is optional only if every role contribution is optional."""
    contributors = [unit for unit in units if unit.get("gap_role_keys")]
    return bool(contributors) and all(not requires_gap_delivery(unit) for unit in contributors)


def _rank(candidates: Sequence[Mapping[str, Any]], requirement: str, observable: str | None,
          *, fixed_context=()):
    requirement_terms = _terms(requirement)
    observable_terms = _terms(observable or "")
    identifiers = literal_identifiers(requirement)
    unique: dict[tuple[str, str, str, str, str], Mapping[str, Any]] = {}
    for candidate in candidates:
        identity = _identity(candidate)
        if identity not in unique:
            unique[identity] = candidate
        else:
            previous = unique[identity]
            # Retain a complete excerpt and its real locator/ID. Adjacent API
            # comments add source contracts, not a second declaration slot.
            winner = max((candidate, previous), key=lambda u:(
                not u.get('truncated', False), len(str(u.get('text', '')))))
            unique[identity] = {**previous, **candidate, **winner,
                'anchor_priority': max(previous.get('anchor_priority', 0), candidate.get('anchor_priority', 0)),
                'gap_role_keys': sorted(set(previous.get('gap_role_keys',[]))|set(candidate.get('gap_role_keys',[]))),
                'evidence_gap_refs': sorted(set(previous.get('evidence_gap_refs', [])) | set(candidate.get('evidence_gap_refs', []))),
                'references': sorted(set(previous.get('references', [])) | set(candidate.get('references', []))),
                'declared_symbols': sorted(set(previous.get('declared_symbols', [])) | set(candidate.get('declared_symbols', []))),
                'superseded_unit_ids': sorted((set(previous.get('superseded_unit_ids', []))
                    | set(candidate.get('superseded_unit_ids', [])) | {previous['id'], candidate['id']}) - {winner['id']})}
            if 'optional_gap_context' in previous or 'optional_gap_context' in candidate:
                unique[identity]['optional_gap_context'] = merge_gap_context_optional(previous, candidate)
            if previous.get('required_for_gap_delivery') or candidate.get('required_for_gap_delivery'):
                unique[identity]={**unique[identity],'required_for_gap_delivery':True}
            if previous.get('precondition_review_required') or candidate.get('precondition_review_required'):
                unique[identity]={**unique[identity],'precondition_review_required':True}
            if previous.get('direct_local_callee_support') or candidate.get('direct_local_callee_support'):
                unique[identity]={**unique[identity],'direct_local_callee_support':True}
            if previous.get('same_receiver_callee_support') or candidate.get('same_receiver_callee_support'):
                unique[identity]={**unique[identity],'same_receiver_callee_support':True}
            # Retain legacy descriptive metadata when duplicate source spans
            # merge. These boundary flags grant no selection or trim priority.
            if previous.get('boundary_scope_anchor') or candidate.get('boundary_scope_anchor'):
                unique[identity]['boundary_scope_anchor'] = True
            if previous.get('boundary_scope_critical') or candidate.get('boundary_scope_critical'):
                unique[identity]['boundary_scope_critical'] = True
    from contractedge.retrieval.frontier import production_frontier
    sibling_ids = {r['source_evidence_id'].removeprefix('SRC-')
                   for r in production_frontier([*fixed_context,*unique.values()], requirement)}
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
                                           "literal_identifier_hits": literal_identifier_hits(text, identifiers),
                                           "mechanism_symbol_hits": len(requirement_terms & _terms(' '.join(
                                               [*map(str, candidate.get('declared_symbols', [])), *map(str, candidate.get('references', []))]))),
                                           "anchor_priority": int(candidate.get('anchor_priority', 0)),
                                           "observable_term_hits": observed,
                                           "requirement_term_hits": requirement_hits,
                                           "hop_distance": int(candidate.get("hop_distance", 0))}
    ordered = sorted(unique.values(), key=lambda item: (
        -features[str(item['id'])]['anchor_priority'],
        -features[str(item["id"])]["literal_identifier_hits"],
        -features[str(item["id"])]["requirement_term_hits"],
        -features[str(item["id"])]["exact_identifier_hits"],
        -features[str(item["id"])]["observable_term_hits"],
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
            token_counter: Callable[[str], int], extension_used: bool, requirement: str,
            local_context_ids: frozenset[str] = frozenset(), context_calls=None, prior_ids=frozenset(),
            max_source_bytes: int | None = None, require_complete_endpoints: bool = False,
            fixed_context=()) -> CandidateSet:
    if token_counter is None:
        raise TokenCounterUnavailable("an explicit model tokenizer/token counter is required")
    if max_source_bytes is not None and (type(max_source_bytes) is not int or max_source_bytes < 0):
        raise ValueError('Source byte limit must be a nonnegative integer')
    selected: list[str] = []
    dropped: list[str] = []
    reasons: dict[str, str] = {}
    total = 0
    source_bytes = 0
    selected_payload: list[Mapping[str, Any]] = []
    # Retained context participates in the existing marginal/relationship
    # rules, while only newly admitted records consume this selection's caps.
    context_payload = list(fixed_context)
    delivery_audit: list[Mapping[str, Any]] = []
    remaining = list(ordered)
    identifiers = literal_identifiers(requirement)
    literal_weight = max(12, 2 * len(_terms(requirement)) + 1)
    required_roles={role for unit in context_payload for role in unit.get('gap_role_keys',[])}
    while remaining:
        def marginal(item):
            f = features[str(item['id'])]
            same_path = sum((p.get('path'), p.get('origin_kind')) == (item.get('path'), item.get('origin_kind'))
                            for p in context_payload)
            overlap = max((_overlap(item, p) for p in context_payload), default=0)
            scope_cost = 1 + math.log2(1 + max(0, int(item.get('scope_path_rank', 0)))) / 2
            # Scope order is a secondary locator heuristic. It cannot erase an
            # exact requirement-body hit; repeated same-file hits still pay
            # the ordinary overlap and already-selected-path costs below.
            # Stronger source-body location must not also amplify immutable
            # input fragments competing with their source consumers. Preserve
            # their established literal relevance and ordinary eligibility.
            body_literal_weight = 12 if item.get('origin_kind') == 'DECLARED_ARTIFACT' else literal_weight
            relevance = ((1 + f['requirement_term_hits'] + f['exact_identifier_hits']
                          + 2 * f.get('mechanism_symbol_hits', 0)) / scope_cost
                         + body_literal_weight * f.get('literal_identifier_hits', 0))
            covered_gaps = {g for p in context_payload for g in p.get('evidence_gap_refs', [])}
            new_gaps = set(item.get('evidence_gap_refs', [])) - covered_gaps
            # A restored, query-related declaration answers a body-coverage
            # gap more directly than its import/header fallback. Soft preference
            # only; documents, configuration and necessary text remain eligible.
            restored = (bool(item.get('expanded_from_unit_id')) and not item.get('truncated')
                        and f.get('mechanism_symbol_hits', 0) > 0)
            score = ((1 + 8 * f['anchor_priority']) * relevance * (4 if f.get('resource_kind_hits') else 1) * (1 - .9 * overlap)
                     * (1 + 4 * len(new_gaps)) * (2 if restored else 1)
                     * (2 if f.get("lexical_sibling_candidate") else 1)
                     / ((1 + same_path) * example_context_cost(item, identifiers)))
            # Observation prose is secondary context, never a substitute for
            # a named requirement literal in a source body. Existing explicit
            # source anchors keep their independent priority and all selected
            # bodies still pay the same overlap, Top-N and context costs.
            return (score, f['observable_term_hits'])
        # Stable initial ordering resolves equal marginal scores. No category is
        # excluded and no file quota forces irrelevant evidence into the request.
        uncovered=[u for u in remaining if requires_gap_delivery(u)
                   and set(u.get('gap_role_keys',[]))-required_roles]
        primary=[u for u in uncovered if u.get('required_for_gap_delivery')
                 or any(role.endswith(':anchor') for role in u.get('gap_role_keys',[]))]
        reserved=sum(bool(p.get('gap_role_keys')) and requires_gap_delivery(p)
                     for p in context_payload)
        # Explicit delivery requirements are hard constraints within the same
        # Top-N/token bounds. The six-slot preference limits legacy role hints;
        # explicitly optional context instead competes on ordinary relevance.
        # Neither can silently demote required lifecycle evidence.
        mandatory=[u for u in remaining if u.get('required_for_gap_delivery')
                   or any(role.endswith(':anchor') for role in u.get('gap_role_keys',[]))]
        # After required endpoints, prefer the at-most-two bodies reached by
        # explicit same-receiver calls over unconnected lexical matches. They
        # still consume ordinary Top-N/token space and may be dropped for cost.
        dependencies=[u for u in remaining
                      if u.get('same_receiver_callee_support') or u.get('direct_local_callee_support')]
        local_context=[u for u in remaining if str(u['id']) in local_context_ids]
        called_ids = set().union(*((context_calls or {}).get(unit['id'], set()) for unit in context_payload))
        direct_context=[u for u in remaining if str(u['id']) in called_ids]
        # Same-owner peers are context, not a reason to erase an explicit
        # task identifier in a production declaration (for example its config
        # field). Documents remain ordinary lexical candidates.
        task_bodies=[u for u in remaining if local_context
                     and (str(u['id']) in prior_ids or set(u.get('superseded_unit_ids', ())) & prior_ids)
                     and u.get('kind') == 'FUNCTION_OR_TYPE'
                     and not test_example_path(str(u.get('path', '')))
                     and features[str(u['id'])].get('literal_identifier_hits', 0)]
        pool = (mandatory or
                ((primary or uncovered) if uncovered and reserved<min(6,len(fixed_context)+top_n)
                 else dependencies or direct_context or task_bodies or local_context or remaining))
        candidate = max(pool, key=marginal)
        remaining.remove(candidate)
        cid = str(candidate["id"])
        if len(selected) >= top_n:
            dropped.append(cid)
            reasons[cid] = "TOP_N_LIMIT"
            delivery_audit.append({'id': cid, 'selected': False, 'delivered': False,
                                   'cited': None, 'drop_reason': 'TOP_N_LIMIT'})
            continue
        if require_complete_endpoints and requires_gap_delivery(candidate) and _truncated_source(candidate):
            dropped.append(cid)
            reasons[cid] = 'INCOMPLETE_REQUIRED_ENDPOINT'
            delivery_audit.append({'id':cid,'selected':False,'delivered':False,
                                   'cited':None,'drop_reason':reasons[cid]})
            continue
        text = str(candidate.get('text', ''))
        size = len(text.encode('utf-8')) if max_source_bytes is not None else 0
        if max_source_bytes is not None and source_bytes + size > max_source_bytes:
            dropped.append(cid)
            reasons[cid] = 'CUMULATIVE_SOURCE_ADMISSION'
            delivery_audit.append({'id':cid,'selected':False,'delivered':False,
                                   'cited':None,'drop_reason':reasons[cid]})
            continue
        cost = int(token_counter(text))
        if cost < 0:
            raise ValueError("token counter cannot return a negative count")
        if total + cost > max_context_tokens:
            dropped.append(cid)
            reasons[cid] = "CONTEXT_BUDGET"
            delivery_audit.append({'id': cid, 'selected': False, 'delivered': False,
                                   'cited': None, 'drop_reason': 'CONTEXT_BUDGET'})
            continue
        selected.append(cid)
        features[cid] = {**features[cid], 'selection_step': len(selected),
                         'new_gap_ids': sorted(set(candidate.get('evidence_gap_refs', [])) -
                            {g for p in context_payload for g in p.get('evidence_gap_refs', [])}),
                         'max_overlap_percent': int(100 * max((_overlap(candidate, p) for p in context_payload), default=0))}
        selected_payload.append(candidate)
        context_payload.append(candidate)
        delivery_audit.append({'id': cid, 'selected': True, 'delivered': True,
                               'cited': None, 'drop_reason': None})
        required_roles.update(candidate.get('gap_role_keys',[]))
        total += cost
        source_bytes += size
    selected_set = set(selected)
    aliases = {alias:u['id'] for u in selected_payload for alias in u.get('superseded_unit_ids', [])}
    remapped = [{**edge, 'source':aliases.get(edge.get('source'),edge.get('source')),
                'target':aliases.get(edge.get('target'),edge.get('target'))} for edge in edges]
    valid_edges = tuple(edge for edge in remapped
                        if edge.get("source") in selected_set and edge.get("target") in selected_set
                        and edge.get('source') != edge.get('target')
                        and edge.get("edge_kind") in {"SYNTACTIC", "INFERRED"})
    return CandidateSet(tuple(selected_payload), tuple(selected), tuple(dropped), features, valid_edges,
                        total, max_context_tokens, top_n, extension_used, reasons,
                        delivery_audit=tuple(delivery_audit))



def trim_context_bundle(candidate_set: CandidateSet, *, request_tokens: int, limit: int,
                        adopted_ids=(), token_counter=None, reason='FULL_REQUEST_CONTEXT_BUNDLE') -> CandidateSet:
    """Remove the lowest-priority connected evidence bundle, recording lost edges.

    Reference edges are retrieval hints, not proven causal relations. Isolated
    evidence is a one-element bundle; immutable input and source IDs stay intact.
    """
    from dataclasses import replace
    selected = list(candidate_set.candidates)
    if adopted_ids:
        active = set(adopted_ids)
        if not active <= set(candidate_set.selected_ids):
            raise ValueError('Request fitting lost adopted candidates')
        additions = [u for u in selected if u['id'] not in active]
        if not additions:
            return candidate_set
        removed_id = additions[-1]['id']
        kept = tuple(u for u in selected if u['id'] != removed_id)
        return replace(candidate_set, candidates=kept, selected_ids=tuple(u['id'] for u in kept),
            context_tokens=(sum(int(token_counter(str(u.get('text','')))) for u in kept)
                if token_counter is not None else candidate_set.context_tokens),
            dropped_ids=tuple(dict.fromkeys([*candidate_set.dropped_ids, removed_id])),
            drop_reasons={**(candidate_set.drop_reasons or {}), removed_id:reason},
            edges=tuple(e for e in candidate_set.edges if removed_id not in (e['source'],e['target'])),
            delivery_audit=tuple({**row,'delivered':False,'drop_reason':reason}
                if row.get('id')==removed_id else row for row in candidate_set.delivery_audit),
            context_audit=candidate_set.context_audit+({'request_tokens_before':request_tokens,
                'limit':limit,'removed_ids':[removed_id], 'adopted_ids':list(adopted_ids)},))
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
    protected=({str(c['id']) for c in selected if requires_gap_delivery(c)}
               | set(candidate_set.required_endpoint_ids) | set(adopted_ids))
    unprotected=[g for g in groups if not g&protected]
    removed = max(unprotected or groups, key=lambda g: (min(positions[k] for k in g), sorted(g)))
    if (removed & protected or len(removed)==len(selected)) and len(selected)>1:
        # A lexical component is not an indivisible semantic proof. Even when
        # there are several components, remove optional scaffolding before
        # sacrificing a required source endpoint in any of them.
        removable=[c for c in selected if str(c['id']) not in protected]
        ordinary=[c for c in removable if not candidate_set.ranking_features.get(str(c['id']),{}).get('lexical_sibling_candidate')]
        if ordinary or removable:
            item=max(ordinary or removable,key=lambda c:(bool(c.get('truncated')),len(c.get('text','')),positions[str(c['id'])]))
            removed={str(item['id'])}
        else:
            # Every remaining connected component is protected.  Never drop
            # a source-backed required endpoint merely to satisfy an
            # impossible context cap; the caller will surface a bounded
            # budget failure if these anchors cannot fit together.
            return replace(candidate_set, context_audit=candidate_set.context_audit + ({
                'request_tokens_before': request_tokens, 'limit': limit,
                'removed_ids': [], 'removed_reference_edges': 0,
                'trim_blocked': 'ALL_REMAINING_CONTEXT_PROTECTED',
                'interpretation': 'Required source endpoints retained; request exceeds bounded context'},))
    kept = tuple(c for c in selected if str(c['id']) not in removed)
    edges = tuple(e for e in candidate_set.edges if str(e['source']) not in removed and str(e['target']) not in removed)
    kept_ids = {str(c['id']) for c in kept}
    audit = tuple({**row, 'delivered': str(row.get('id')) in kept_ids,
                   'drop_reason': (row.get('drop_reason') or reason)
                   if str(row.get('id')) in removed else row.get('drop_reason')}
                  for row in candidate_set.delivery_audit)
    return replace(candidate_set, candidates=kept, selected_ids=tuple(str(c['id']) for c in kept),
        context_tokens=(sum(int(token_counter(str(u.get('text','')))) for u in kept)
            if token_counter is not None else candidate_set.context_tokens),
        dropped_ids=tuple(dict.fromkeys([*candidate_set.dropped_ids, *sorted(removed)])), edges=edges,
        drop_reasons={**(candidate_set.drop_reasons or {}), **{k: reason for k in removed}},
        context_audit=candidate_set.context_audit + ({'request_tokens_before': request_tokens, 'limit': limit,
            'removed_ids': sorted(removed), 'removed_reference_edges': len(candidate_set.edges) - len(edges),
            'interpretation': 'Bounded reference bundle removed; no causal correctness claim'},),
        delivery_audit=audit)


def build_candidate_set(requirement: str, observable: str | None, units: Sequence[Mapping[str, Any]],
                        edges: Sequence[Mapping[str, Any]], *, top_n: int,
                        max_context_tokens: int, token_counter: Callable[[str], int] | None) -> CandidateSet:
    if top_n <= 0 or max_context_tokens <= 0:
        raise ValueError("top_n and max_context_tokens must be positive")
    ordered, features = _rank(units, requirement, observable)
    from dataclasses import replace
    selected = _select(ordered, features, edges, top_n=top_n, max_context_tokens=max_context_tokens,
                       token_counter=token_counter, extension_used=False, requirement=requirement)
    return replace(selected, ranking_requirement=requirement, ranking_observable=observable)


def _gap_local_context(ordered, prior_ids, gaps):
    """Prefer bounded declaration context around an actually located gap root.

    This is a read-only retrieval preference, not required delivery, type
    checking, causal proof, or edit permission. A directory by itself supplies
    no root and no priority. Go receiver ownership and construction syntax
    supplement the parser's call-name references, which omit receiver types.
    """
    from contractedge.retrieval.gap_delivery import _go_code, _go_direct_calls
    by_id = {str(u['id']): u for u in ordered}
    scopes = [str((g.get('locator_hint') or {}).get('path', '')).rstrip('/') for g in gaps]

    def in_gap_scope(unit):
        path = str(unit.get('path', ''))
        return any(scope and (path == scope or path.startswith(scope + '/')) for scope in scopes)

    def declaration(unit):
        return unit.get('kind') == 'FUNCTION_OR_TYPE' and bool(unit.get('symbol'))

    def query_root(unit):
        # Same-gap source hits are soft context even when another exact symbol
        # already owns the one mandatory endpoint. Otherwise a requested config
        # assignment can lose every slot to neighbors of that endpoint.
        if (not str(unit.get('path', '')).endswith('.go')
                or unit.get('truncated') or unit.get('locator', {}).get('truncated')
                or unit.get('origin_kind') not in (None, 'SOURCE_SPAN')
                or test_example_path(str(unit.get('path', '')))):
            return False
        body = str(unit.get('text', ''))
        if hashlib.sha256(body.encode()).hexdigest() != unit.get('content_digest'):
            return False
        from contractedge.source.units import _gap_query_matches
        from contractedge.retrieval.gap_delivery import _go_declaration
        code, function = _go_declaration(body)
        structure = re.match(r'\s*type\s+(\w+)\s+(?:struct|interface)\b', code)
        name = function[3] if function else structure[1] if structure else None
        if not name or name != unit.get('symbol'):
            return False
        target = PurePosixPath(str(unit.get('path', '')))
        for gap in gaps:
            hint = gap.get('locator_hint') or {}
            scope = PurePosixPath(str(hint.get('path', '')).rstrip('/'))
            if (not hint.get('path') or scope.is_absolute() or '..' in scope.parts
                    or target.parts[:len(scope.parts)] != scope.parts
                    or str(gap.get('id', '')) not in set(map(str, unit.get('evidence_gap_refs', ())))):
                continue
            # A symbol names a declaration; mentions/calls/signatures inside a
            # different declaration do not make it that symbol. Only the
            # explicit search term may match the actual code body.
            symbol = str(hint.get('symbol', '')).strip()
            query = str(hint.get('search_term', '')).strip()
            query_hit = bool(query) and _gap_query_matches(query, code)
            if query_hit and re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*', query):
                # Broad scan recall is not enough to promote PlainHTTPEnabled
                # for a PlainHTTP query (or notify for Not).
                query_hit = bool(re.search(r'(?<![A-Za-z0-9_$])' + re.escape(query)
                    + r'(?![A-Za-z0-9_$])', code, re.IGNORECASE))
            if (symbol and symbol == name) or query_hit:
                return True
        return False

    roots = [u for u in ordered if declaration(u) and in_gap_scope(u)
             and (str(u['id']) in prior_ids or set(u.get('superseded_unit_ids', ())) & prior_ids
                  or u.get('required_for_gap_delivery')
                  or any(str(role).endswith(':anchor') for role in u.get('gap_role_keys', ()))
                  or query_root(u))]
    root_ids = {str(u['id']) for u in roots}
    context = set(); calls = {}
    # Parser reference edges are retained as lexical context only. Restrict
    # ordinary code names to their source namespace; same names elsewhere do
    # not justify importing another package. Literal Helm includes already
    # carry a precise template-name binding and may cross files.
    for edge in build_direct_reference_edges(ordered):
        a, b = edge['source'], edge['target']
        if not ({a, b} & root_ids):
            continue
        left, right = by_id[a], by_id[b]
        template = edge.get('mechanism') == 'LITERAL_TEMPLATE_INCLUDE'
        namespace = (left.get('path') == right.get('path')
                     and not str(left.get('path', '')).endswith('.go'))
        if template or namespace and declaration(left) and declaration(right):
            context.update({a, b} - root_ids)

    # Resolve only literal unqualified Go calls and same-receiver calls among
    # the already bounded current declarations. A qualified external call is
    # not a same-package function merely because its final name matches.
    method_pattern = r'\s*func\s*\(\s*(?:(\w+)\s+)?\*?(\w+)\s*\)\s*(\w+)\s*\('
    go = {}; definitions = {}
    for unit in ordered:
        if not declaration(unit) or not str(unit.get('path', '')).endswith('.go'):
            continue
        code = _go_code(str(unit.get('text', '')))
        method = re.match(method_pattern, code)
        free = re.match(r'\s*func\s+(\w+)\s*\(', code)
        if not method and not free:
            continue
        directory = PurePosixPath(unit['path']).parent
        owner = method[2] if method else None
        name = method[3] if method else free[1]
        definitions.setdefault((directory, owner, name), []).append(str(unit['id']))
        go[str(unit['id'])] = (directory, code, method)
    active_directories = {PurePosixPath(u['path']).parent for u in roots
                          if str(u.get('path', '')).endswith('.go')}
    for identifier, (directory, code, method) in go.items():
        if directory not in active_directories:
            continue
        names = _go_direct_calls(code)
        targets = set()
        for owner, name in names:
            found = [target for target in definitions.get((directory, owner, name), [])
                     if str(by_id[identifier]['path']).endswith('_test.go')
                     or not str(by_id[target]['path']).endswith('_test.go')]
            if len(found) == 1 and found[0] != identifier:
                targets.add(found[0])
        if targets:
            calls[identifier] = targets
        if identifier in root_ids:
            context.update(targets - root_ids)
        elif targets & root_ids:
            context.add(identifier)
    owners = set()
    for unit in roots:
        if not str(unit.get('path', '')).endswith('.go'):
            continue
        code = _go_code(str(unit.get('text', '')))
        receiver = re.match(method_pattern, code)
        structure = re.match(r'\s*type\s+(\w+)\s+struct\b', code)
        owner = receiver[2] if receiver else structure[1] if structure else None
        if owner:
            owners.add((PurePosixPath(unit['path']).parent, owner, str(unit['path']).endswith('_test.go')))
    for unit in ordered:
        if not declaration(unit) or not str(unit.get('path', '')).endswith('.go'):
            continue
        code = _go_code(str(unit.get('text', '')))
        receiver = re.match(method_pattern, code)
        structure = re.match(r'\s*type\s+(\w+)\s+struct\b', code)
        for directory, owner, test_root in owners:
            if not test_root and str(unit['path']).endswith('_test.go'):
                continue
            if PurePosixPath(unit['path']).parent != directory:
                continue
            if ((receiver and receiver[2] == owner) or (structure and structure[1] == owner)
                    or any(not code[:m.start()].rstrip().endswith('.') for m in re.finditer(
                        r'(?<![\w.])' + re.escape(owner) + r'\s*\{', code))):
                context.add(str(unit['id']))
    # The located root remains part of its own context. Rebuilding selection
    # must not retain a callee while silently losing the declaration that made
    # that lookup relevant. This is still soft: exact new endpoints and token
    # limits take precedence.
    return frozenset(context | root_ids), calls


def _extension_context(ordered, prior_ids, gaps, features):
    local_context, context_calls = _gap_local_context(ordered, prior_ids, gaps)
    for identifier in local_context:
        if identifier in features:
            features[identifier] = {**features[identifier], 'gap_declaration_context': 1}
    return local_context, context_calls


def _truncated_source(unit):
    return bool(unit.get('truncated') or unit.get('locator',{}).get('truncated'))


def extend_once(candidate_set: CandidateSet, evidence_gaps: Sequence[Mapping[str, Any]],
                extension_candidates: Sequence[Mapping[str, Any]], *, max_context_tokens: int,
                token_counter: Callable[[str], int] | None,
                allow_reextension: bool = False, source_context=None,
                source_reservations=()) -> CandidateSet:
    if candidate_set.extension_used and not allow_reextension:
        raise ValueError("candidate set permits only one evidence-gap extension")
    locatable = [gap for gap in evidence_gaps
                 if any((gap.get("locator_hint") or {}).get(key) for key in ("search_term", "path", "symbol"))]
    if not locatable:
        raise ValueError("an evidence-gap extension requires a locatable gap")
    gap_ids = {str(gap.get('id', '')) for gap in locatable}
    # A newly located exact-path/query unit is the reason for this round. Keep
    # it as a hard delivery endpoint even when the initial candidate set has a
    # full public-boundary anchor budget; otherwise a same-file POST helper can
    # displace the newly found OPTIONS branch before re-diagnosis.
    # A located gap can return both the exact endpoint and bounded lexical
    # context.  Only the endpoint is a hard delivery requirement.  Marking
    # every unit carrying the gap reference as required turns the finite role
    # context (and optional same-receiver callers) into an impossible
    # all-or-nothing Top-N constraint; a single gap can then require more
    # endpoints than the configured request capacity and is reported as a
    # false EVIDENCE_GAP.  The locator already marks anchors/connectors and
    # field contracts explicitly; preserve their role semantics here.
    # ``extract_gap_extension_units`` predates role delivery and therefore
    # carries only a gap reference/scope marker, not an explicit anchor role.
    # Pick one best query hit per gap as its endpoint; leaving all such units
    # hard would recreate the Top-N explosion for broad path queries.
    from contractedge.source.units import _gap_query_matches
    endpoint_ids = set()
    for gap in locatable:
        gap_id = str(gap.get('id', ''))
        # The role locator has already selected an explicit endpoint. Do not
        # spend another hard slot on a lexical hit for the same gap.
        if any(gap_id + ':anchor' in item.get('gap_role_keys', ())
               for item in [*candidate_set.candidates, *extension_candidates]):
            continue
        hint = gap.get('locator_hint') or {}
        path_hint = str(hint.get('path', '')).strip().rstrip('/')
        terms = [str(hint.get(key, '')).strip() for key in ('symbol', 'search_term')
                 if str(hint.get(key, '')).strip()]
        matches = []
        for item in extension_candidates:
            if gap_id not in set(map(str, item.get('evidence_gap_refs', []))):
                continue
            path = str(item.get('path', ''))
            if path_hint and not (path == path_hint or path.startswith(path_hint + '/')):
                continue
            body = str(item.get('text', ''))
            exact_symbol = bool(hint.get('symbol')) and (
                str(item.get('symbol', '')) == str(hint.get('symbol'))
                or str(hint.get('symbol')) in set(map(str, item.get('declared_symbols', ()))))
            query_hit = any(_gap_query_matches(term, body) for term in terms)
            # Path-only requests can retain one already-qualified location;
            # this is representative evidence, never full-file coverage.
            path_only = bool(path_hint) and not terms
            if not (exact_symbol or query_hit or path_only):
                continue
            complete_declaration = (item.get('kind') == 'FUNCTION_OR_TYPE'
                                    and bool(item.get('symbol') or item.get('declared_symbols'))
                                    and not item.get('truncated'))
            # An equally precise implementation hit is preferable to a test
            # example. This is only a ranking tie-break, never semantic proof
            # or a read/edit exclusion: explicit test locators and test-only
            # results remain usable. Source line numbers from different files
            # cannot establish which hit answers the requested implementation.
            test_example = test_example_path(path)
            matches.append((int(exact_symbol), int(complete_declaration),
                            not bool(item.get('truncated')), int(query_hit),
                            not test_example,
                            -int(item.get('locator', {}).get('start_line', 0)), str(item.get('id'))))
        if matches:
            matches.sort(reverse=True)
            endpoint_ids.add(matches[0][-1])

    extension = []
    for item in extension_candidates:
        value = dict(item)
        same_gap = gap_ids.intersection(item.get('evidence_gap_refs', []))
        optional = (item.get('optional_gap_context') is True
                    or item.get('same_receiver_callee_support') is True
                    or item.get('direct_local_callee_support') is True)
        anchor_role = any(str(role).endswith(':anchor')
                          for role in item.get('gap_role_keys', ()))
        if same_gap and (str(item.get('id')) in endpoint_ids
                         or (not optional and (item.get('required_for_gap_delivery')
                                               or anchor_role))):
            value['required_for_gap_delivery'] = True
        extension.append(value)
    search_terms = []
    for gap in locatable:
        hint = gap.get("locator_hint") or {}
        search_terms.extend(str(hint.get(key, "")) for key in ("search_term", "symbol")
                           if hint.get(key))
    # Task relevance and gap location are different selection responsibilities.
    # Appending gap prose to the task can let long documents displace a short
    # production bridge. Exact gap endpoints are already mandatory; ordinary
    # context keeps the original task, and local declaration context gets a
    # bounded soft preference rather than a directory-wide anchor promotion.
    ranking_query = candidate_set.ranking_requirement or "\n".join(search_terms)
    if source_context is not None:
        return _append_source_extension(candidate_set, extension, max_context_tokens,
            token_counter, source_context, gaps=locatable, ranking_query=ranking_query,
            source_reservations=source_reservations)
    if source_reservations:
        raise ValueError('Source reservations require the canonical source context')
    combined = [{**dict(u), 'required_for_gap_delivery': True}
                if u['id'] in candidate_set.required_endpoint_ids else u
                for u in candidate_set.candidates] + extension
    ordered, features = _rank(combined, ranking_query, candidate_set.ranking_observable)
    local_context, context_calls = _extension_context(ordered, set(candidate_set.selected_ids), locatable, features)
    combined_edges = build_direct_reference_edges(combined)
    result=_select(ordered, features, combined_edges, top_n=candidate_set.top_n,
                   max_context_tokens=max_context_tokens, token_counter=token_counter, extension_used=True,
                   requirement=ranking_query, local_context_ids=local_context, context_calls=context_calls,
                   prior_ids=frozenset(candidate_set.selected_ids))
    from dataclasses import replace
    # _rank canonicalizes identical locations; require the actual canonical ID
    # whose exact excerpt will enter the rebuilt diagnosis, not a discarded alias.
    required=tuple(dict.fromkeys(str(u['id']) for u in ordered
        if u.get('required_for_gap_delivery') or any(role.endswith(':anchor') for role in u.get('gap_role_keys',[]))))
    return replace(result, required_endpoint_ids=required,
                   ranking_requirement=candidate_set.ranking_requirement,
                   ranking_observable=candidate_set.ranking_observable)


def source_extension_dependency_allowance(local_selection, source_context):
    """Remaining shared acquisition space after canonical required endpoints.

    The local selection is an immutable feasibility draft, not adoption. Its
    optional units do not reserve dependency space; the final selector will
    allocate those only after the dependency reader has used this allowance.
    """
    return _dependency_allowance(local_selection.candidates,
        local_selection.required_endpoint_ids, source_context)


def _dependency_allowance(units, required_ids, source_context):
    from contractedge.diagnosis.evidence import (source_context_records,
        source_extension_limits, source_context_key)
    by_id = {u['id']: u for u in units}
    if not set(required_ids) <= by_id.keys():
        return 0, 0
    records = source_context_records(source_context)
    adopted_keys = {source_context_key(r) for r in records}
    required = [by_id[identifier] for identifier in dict.fromkeys(required_ids)
                if ('SRC-' + identifier, by_id[identifier].get('snapshot_id')) not in adopted_keys]
    if any(_truncated_source(u) for u in required):
        return 0, 0
    used = [r for r in source_context_records(source_context, producer='extension')
            if r['kind'] != 'STATIC_SEARCH']
    batch_count, batch_bytes, total_count, total_bytes = source_extension_limits(source_context)
    return (max(0, min(batch_count, total_count-len(used)) - len(required)),
            max(0, min(batch_bytes, total_bytes-sum(len(r['excerpt'].encode()) for r in used))
                - sum(len(u['text'].encode()) for u in required)))


def _append_source_extension(base, proposals, max_context_tokens, token_counter, source_context,
                             *, gaps=(), ranking_query=None, source_reservations=()):
    """Only unsent offers compete; the existing source owner retains its bodies."""
    from dataclasses import replace
    from contractedge.diagnosis.evidence import (source_context_records, source_extension_limits,
        source_context_offer, source_context_key)
    adopted = set(source_context['candidate_ids'])
    if not adopted <= set(base.selected_ids):
        raise ValueError('Extension input removed adopted candidates')
    old = {u['id']: u for u in base.candidates}
    physical = {(*_source_origin(u),u.get('snapshot_id'),u.get('path'),json_locator(u),u.get('content_digest')):u
                for u in base.candidates}
    aliases, fresh = {}, []
    for unit in proposals:
        previous = old.get(unit['id'])
        if previous is not None:
            if (_source_origin(previous)!=_source_origin(unit) or any(previous.get(k)!=unit.get(k)
                    for k in ('snapshot_id','path','locator','text','content_digest'))):
                raise ValueError('Extension changed an adopted candidate identity')
            if requires_gap_delivery(unit) and _truncated_source(previous):
                raise ValueError('Incomplete adopted source cannot satisfy a required extension endpoint')
            aliases[unit['id']] = previous['id']
            continue
        same = physical.get((*_source_origin(unit),unit.get('snapshot_id'),unit.get('path'),
                             json_locator(unit),unit.get('content_digest')))
        if same is not None and same.get('text') == unit.get('text'):
            if requires_gap_delivery(unit) and _truncated_source(same):
                raise ValueError('Incomplete adopted source cannot satisfy a required extension endpoint')
            aliases[unit['id']] = same['id']
        else:
            fresh.append(unit)
    ranking_query = base.ranking_requirement if ranking_query is None else ranking_query
    ordered, features = _rank(fresh, ranking_query, base.ranking_observable,fixed_context=base.candidates)
    # Fresh proposals may alias one another in _rank. Reserve only their
    # canonical required endpoints; adopted IDs and bodies never compete.
    required = tuple(dict.fromkeys([*base.required_endpoint_ids,
        *(aliases[u['id']] for u in proposals
          if u['id'] in aliases and requires_gap_delivery(u)),
        *(u['id'] for u in ordered if requires_gap_delivery(u))]))
    # Read-only dependency declarations compete for the same acquisition quota
    # as local source, while remaining outside candidate/evidence identities.
    # This draft is only a reservation: dispatch still owns actual adoption.
    if any(r.get('kind') != 'PUBLIC_DEPENDENCY_DECLARATION' for r in source_reservations):
        raise ValueError('Source reservations must be read-only dependency declarations')
    reserved_context = (source_context_offer(source_context, source_reservations, producer='extension')
                        if source_reservations else source_context)
    old_keys = {source_context_key(r) for r in source_context_records(source_context)}
    reserved = [r for r in source_context_records(reserved_context)
                if source_context_key(r) not in old_keys and r['kind'] != 'STATIC_SEARCH']
    used = [r for r in source_context_records(reserved_context, producer='extension') if r['kind'] != 'STATIC_SEARCH']
    batch_count, batch_bytes, total_count, total_bytes = source_extension_limits(source_context)
    reserved_bytes = sum(len(r['excerpt'].encode()) for r in reserved)
    if len(reserved) > batch_count or reserved_bytes > batch_bytes:
        raise ValueError('Read-only source reservation exceeds this acquisition quota')
    dependency_count, dependency_bytes = _dependency_allowance(
        [*base.candidates, *ordered], required, source_context)
    if len(reserved) > dependency_count or reserved_bytes > dependency_bytes:
        # Callers must select unsent dependencies within the allowance after
        # required local source. Never silently eject a required endpoint to
        # satisfy a previously unadopted supporting declaration.
        raise ValueError('Read-only source reservation conflicts with required local source admission')
    remaining = min(batch_count-len(reserved), max(0, total_count-len(used)))
    byte_limit = min(batch_bytes-reserved_bytes,
        max(0, total_bytes-sum(len(r['excerpt'].encode()) for r in used)))
    # Existing material remains fixed, but still supplies the same bounded
    # declaration/call context used by legacy extension selection.
    context_units = [*base.candidates, *ordered]
    local_context, context_calls = _extension_context(context_units,set(base.selected_ids),gaps,features)
    selected = _select(ordered, features, build_direct_reference_edges(context_units), top_n=remaining,
        max_context_tokens=max_context_tokens, token_counter=token_counter,
        extension_used=True, requirement=ranking_query, local_context_ids=local_context,
        context_calls=context_calls, prior_ids=frozenset(base.selected_ids),
        max_source_bytes=byte_limit, require_complete_endpoints=True,fixed_context=base.candidates)
    # Optional windows retain their exact parser text and explicit truncation
    # metadata; neither a window nor its delivery claims a complete function.
    added, dropped = list(selected.candidates), dict(selected.drop_reasons or {})
    candidates = tuple([*base.candidates, *added])
    kept = {u['id'] for u in candidates}
    audit={row['id']:row for row in [*base.delivery_audit,*selected.delivery_audit]}
    for identifier in dropped:
        audit[identifier]={**audit.get(identifier,{'id':identifier,'selected':False,'cited':None}),
                           'delivered':False,'drop_reason':dropped[identifier]}
    return replace(base, candidates=candidates, selected_ids=tuple(u['id'] for u in candidates),
        context_tokens=sum(int(token_counter(str(u.get('text','')))) for u in candidates),
        top_n=max(base.top_n, len(base.candidates) + remaining), extension_used=True,
        max_context_tokens=max_context_tokens, required_endpoint_ids=required,
        edges=tuple(build_direct_reference_edges(candidates)),
        ranking_features={**base.ranking_features, **features},
        dropped_ids=tuple(dict.fromkeys([i for i in [*base.dropped_ids,*dropped] if i not in kept])),
        drop_reasons={key:value for key,value in {**(base.drop_reasons or {}),**dropped}.items()
                      if key not in kept},
        delivery_audit=tuple(audit.values()),
        context_audit=base.context_audit+({'source_extension':{'base_ids':list(base.selected_ids),
            'added_ids':[u['id'] for u in added], 'cumulative_record_limit':total_count,
            'cumulative_byte_limit':total_bytes}},))


def json_locator(unit):
    # Metadata identity, never a body digest or a cross-path alias.
    import json
    return json.dumps(unit.get('locator',{}),sort_keys=True,separators=(',',':'))
