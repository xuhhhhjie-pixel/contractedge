"""Evaluator-only adapters for the current frozen150, retaining the full denominator."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Mapping

import yaml

from contractedge.dataset.benchmark import confined, file_sha, read_members, release_identity, method_input_from_task
from contractedge.contracts.validation import canonical_digest, validate_run_trace
from contractedge.evaluation.gt_adapter import normalize_gt, score_run


def load_evaluator_members(benchmark_root: str | Path):
    root = Path(benchmark_root).resolve(strict=True)
    identity = release_identity(root)
    entry = json.loads((root / 'RELEASE_ENTRY.json').read_text())
    path = root / 'evaluation/manifest.jsonl'
    if file_sha(path) != entry['evaluation_manifest_sha256']:
        raise ValueError('Frozen evaluator manifest hash mismatch')
    members = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    public = {r['case_id']: r for r in read_members(root / 'public')}
    ids = [r['case_id'] for r in members]
    if len(ids) != len(set(ids)) or set(ids) != set(public):
        raise ValueError('Evaluator/public membership mismatch')
    for row in members:
        if row['primary_class'] != public[row['case_id']]['primary_class']:
            raise ValueError('Evaluator/public class mismatch')
    return root, identity, members


def normalize_member_gt(root: Path, identity: Mapping, member: Mapping) -> dict:
    path = confined(root / 'evaluation', member['gt'])
    if file_sha(path) != member['gt_sha256']:
        raise ValueError('Frozen GT hash mismatch: ' + member['case_id'])
    raw = yaml.safe_load(path.read_text())
    if raw.get('case_id', member['case_id']) != member['case_id']:
        raise ValueError('GT case identity mismatch')
    result = normalize_gt(raw, identity['release_id'], release_metadata=member)
    result.update(gt_sha256=member['gt_sha256'], gt_path=member['gt'])
    return result


def traceability(trace: Mapping) -> dict:
    """Structural O/E/V/C/P coverage, explicitly separate from semantic accuracy."""
    diagnosis, changeset = trace.get('diagnosis') or {}, trace.get('changeset') or {}
    relations = {e['id']: e for e in diagnosis.get('expected_relations', [])}
    violations = {v['id']: v for v in diagnosis.get('violations', [])}
    changes = changeset.get('changes', [])
    postconditions = {p['id']: p for p in changeset.get('postconditions', [])}
    outcomes = [o['id'] for o in (trace.get('requirement_spec') or {}).get('outcomes', [])]
    chains = []
    for change in changes:
        for violation_id in change.get('violation_refs', []):
            violation = violations.get(violation_id, {})
            relation = relations.get(violation.get('relation_id'), {})
            for outcome in relation.get('requirement_refs', []):
                for post_id in change.get('postcondition_refs', []):
                    post = postconditions.get(post_id, {})
                    if outcome in post.get('requirement_refs', []) and relation.get('id') in post.get('relation_refs', []):
                        chains.append({'O': outcome, 'E': relation['id'], 'V': violation_id,
                                       'C': change['id'], 'P': post_id})
    covered = {row['O'] for row in chains}
    return {'outcomes': len(outcomes), 'outcomes_with_linked_repair_chain': len(covered),
            'O_E_V_C_P_chains': chains, 'open_obligations': len(changeset.get('open_obligations', [])),
            'preserve_constraints': len(changeset.get('preserve_constraints', [])),
            'interpretation': 'Reference/coverage structure only; not a proof of evidence support or semantic correctness.'}


def evaluate_benchmark(benchmark_root: str | Path, trace_manifest: str | Path | None = None,
                       trace_root: str | Path | None = None) -> dict:
    root, identity, members = load_evaluator_members(benchmark_root)
    traces = {}
    if trace_manifest is not None:
        base = Path(trace_root).resolve(strict=True) if trace_root else Path(trace_manifest).resolve().parent
        selected = {r['case_id'] for r in members}
        for line in Path(trace_manifest).read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row['case_id'] in traces or row['case_id'] not in selected:
                raise ValueError('Duplicate or nonmember trace')
            path = confined(base, row['trace'])
            if row.get('trace_sha256') and file_sha(path) != row['trace_sha256']:
                raise ValueError('Trace identity mismatch')
            trace = json.loads(path.read_text())
            if trace.get('case_id') != row['case_id']:
                raise ValueError('Trace case mismatch')
            validate_run_trace(trace)
            current, _ = method_input_from_task(root / 'public', row['case_id'], release_id=identity['release_id'])
            projected = {**current, 'archive_path': f"cases/{row['case_id']}/source.tar.gz"}
            projected.pop('input_digest')
            allowed_inputs = {current['input_digest'], canonical_digest(projected)}
            if trace['input_digest'] not in allowed_inputs:
                raise ValueError('Trace is not bound to this frozen benchmark input; legacy results are reference only')
            if trace['snapshot_id'] != current['initial_snapshot_id']:
                if not (trace['snapshot_id'] is None and trace['status'] in {'INVALID_INPUT', 'INFRASTRUCTURE_ERROR'}):
                    raise ValueError('Relationship evaluation requires the current frozen Broken snapshot, not a legacy or repaired tree')
            if trace.get('requirement_spec') is not None and trace['requirement_spec'].get('raw_text') != current['requirement_text']:
                raise ValueError('Trace requirement differs from the frozen public task')
            traces[row['case_id']] = trace
    records = []
    for member in members:
        gt = normalize_member_gt(root, identity, member)
        trace = traces.get(member['case_id'])
        if trace is None:
            score = {'case_id': member['case_id'], 'primary_class': member['primary_class'],
                     'status': 'NOT_RUN', 'denominator_included': True, 'failure_kind': 'NOT_RUN',
                    'precision': None, 'recall': None,
                    'method_status': 'NOT_RUN', 'execution_status': 'NOT_RUN',
                    'public_status': 'NOT_APPLICABLE', 'independent_score_status': 'NOT_RUN',
                    'scoring_status': 'NOT_RUN'}
        else:
            score = score_run(trace, gt)
            score['traceability'] = traceability(trace)
        score['gt_policy'] = {key: gt[key] for key in ['precision_eligible', 'recall_eligible',
            'full_recall_eligible', 'ambiguity', 'unstructured_relation_annotations']}
        score['gt_sha256'] = member['gt_sha256']
        records.append(score)
    statuses = Counter(row['status'] for row in records)
    return {'schema_version': 'ce.benchmark_cohort_evaluation.v1', **identity,
            'case_count': len(records), 'attempted': len(traces), 'status_counts': dict(statuses),
            'eligible_for_relation_precision': sum(r['gt_policy']['precision_eligible'] is True for r in records),
            'eligible_for_relation_recall': sum(r['gt_policy']['recall_eligible'] is True for r in records),
            'cases': records, 'hidden_behavioral_scoring': 'Separate score_case.py process on final candidate patch',
            'scope': 'Current frozen benchmark only. Missing cases and undecided metric eligibility remain visible.'}
