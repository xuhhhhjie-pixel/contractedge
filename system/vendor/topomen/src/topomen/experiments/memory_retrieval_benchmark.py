from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any

from pydantic import BaseModel, Field
from rich.console import Console

from topomen.memory import (
    PatternMemory,
    RepairMemory,
    RuntimeValidationMemory,
)

console = Console(highlight=False)


class MemoryRetrievalCase(BaseModel):
    case_id: str
    memory_type: str = "pattern"
    case_kind: str = "hit"
    query_text: str = ""
    signals: list[str] = Field(default_factory=list)
    expected_ids: list[str] = Field(default_factory=list)
    forbidden_ids: list[str] = Field(default_factory=list)
    expected_prefill_ids: list[str] = Field(default_factory=list)
    project_name: str = ""
    project_key: str = ""
    allow_same_project_memory: bool = False
    top_k: int = Field(default=3, ge=1)


def load_retrieval_cases(path: Path) -> list[MemoryRetrievalCase]:
    cases: list[MemoryRetrievalCase] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"Expected object in {path} line {line_number}")
        cases.append(MemoryRetrievalCase.model_validate(payload))
    return cases


def evaluate_memory_retrieval(
    cases: list[MemoryRetrievalCase],
    *,
    repair_memory_path: Path | None = None,
    pattern_memory_path: Path | None = None,
    runtime_validation_memory_path: Path | None = None,
) -> dict[str, Any]:
    repair_memory = RepairMemory(repair_memory_path, create=False) if repair_memory_path else None
    pattern_memory = PatternMemory(pattern_memory_path, create=False) if pattern_memory_path else None
    runtime_memory = (
        RuntimeValidationMemory(runtime_validation_memory_path, create=False)
        if runtime_validation_memory_path
        else None
    )
    case_results = [
        _evaluate_case(
            case,
            repair_memory=repair_memory,
            pattern_memory=pattern_memory,
            runtime_memory=runtime_memory,
        )
        for case in cases
    ]
    return {
        "cases": len(case_results),
        "by_case": case_results,
        "summary": _summarize_case_results(case_results),
    }


def _evaluate_case(
    case: MemoryRetrievalCase,
    *,
    repair_memory: RepairMemory | None,
    pattern_memory: PatternMemory | None,
    runtime_memory: RuntimeValidationMemory | None,
) -> dict[str, Any]:
    matches: list[Any] = []
    if case.memory_type == "repair":
        if repair_memory is None:
            raise ValueError("repair memory path is required for repair cases")
        query_text = case.query_text or " ".join(case.signals)
        matches = repair_memory.retrieve(
            query_text,
            top_k=case.top_k,
            project_name=case.project_name,
            project_key=case.project_key,
            allow_same_project_memory=case.allow_same_project_memory,
            package_signals=case.signals,
        )
        retrieved_ids = [_repair_match_id(match) for match in matches]
        prefill_ids = [_repair_match_id(match) for match in matches if match.evidence_level == "prefill"]
    elif case.memory_type == "pattern":
        if pattern_memory is None:
            raise ValueError("pattern memory path is required for pattern cases")
        matches = pattern_memory.retrieve(
            case.signals,
            top_k=case.top_k,
            project_name=case.project_name,
            project_key=case.project_key,
            allow_same_project_memory=case.allow_same_project_memory,
        )
        retrieved_ids = [match.record.pattern_id for match in matches]
        prefill_ids = [match.record.pattern_id for match in matches if match.prefill_ready]
    elif case.memory_type in {"runtime", "runtime_validation"}:
        if runtime_memory is None:
            raise ValueError("runtime validation memory path is required for runtime cases")
        matches = runtime_memory.retrieve(
            case.signals,
            top_k=case.top_k,
            project_name=case.project_name,
            project_key=case.project_key,
            allow_same_project_memory=case.allow_same_project_memory,
        )
        retrieved_ids = [match.record.runtime_id for match in matches]
        prefill_ids = [match.record.runtime_id for match in matches if match.prefill_ready]
    else:
        raise ValueError(f"Unsupported memory_type: {case.memory_type}")

    expected = set(case.expected_ids)
    forbidden = set(case.forbidden_ids)
    expected_prefill = set(case.expected_prefill_ids)
    retrieved = set(retrieved_ids)
    prefilled = set(prefill_ids)
    relevant_count = len(retrieved & expected)
    forbidden_hits = sorted(retrieved & forbidden)
    prefill_false_positive_ids = sorted(prefilled - expected_prefill) if expected_prefill else sorted(prefilled & forbidden)
    hit_case = bool(expected)
    block_case = _is_block_case(case)
    block_success = not forbidden_hits and not prefill_false_positive_ids
    return {
        "case_id": case.case_id,
        "memory_type": case.memory_type,
        "case_kind": "block" if block_case else case.case_kind,
        "retrieved_ids": retrieved_ids,
        "prefill_ids": prefill_ids,
        "expected_ids": case.expected_ids,
        "forbidden_hits": forbidden_hits,
        "precision_at_k": relevant_count / case.top_k if hit_case else None,
        "recall_at_k": relevant_count / len(expected) if expected else 1.0,
        "mrr": _reciprocal_rank(retrieved_ids, expected),
        "prefill_precision": _prefill_precision(prefill_ids, expected_prefill),
        "prefill_false_positive_ids": prefill_false_positive_ids,
        "negative_transfer": bool(forbidden_hits or prefill_false_positive_ids),
        "forbidden_hit": bool(forbidden_hits),
        "prefill_leakage": bool(prefill_false_positive_ids),
        "block_success": block_success if block_case else None,
    }


def _summarize_case_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        return {
            "precision_at_k": 0.0,
            "recall_at_k": 0.0,
            "mrr": 0.0,
            "prefill_precision": 0.0,
            "negative_transfer_rate": 0.0,
            "hit_cases": 0,
            "block_cases": 0,
            "block_success_rate": 0.0,
            "forbidden_hit_rate": 0.0,
            "prefill_leakage_rate": 0.0,
        }
    hit_results = [item for item in results if item["precision_at_k"] is not None]
    block_results = [item for item in results if item.get("block_success") is not None]
    return {
        "precision_at_k": round(mean(float(item["precision_at_k"]) for item in hit_results), 4) if hit_results else 0.0,
        "recall_at_k": round(mean(float(item["recall_at_k"]) for item in hit_results), 4) if hit_results else 0.0,
        "mrr": round(mean(float(item["mrr"]) for item in hit_results), 4) if hit_results else 0.0,
        "prefill_precision": round(mean(float(item["prefill_precision"]) for item in results), 4),
        "negative_transfer_rate": round(sum(1 for item in results if item["negative_transfer"]) / len(results), 4),
        "hit_cases": len(hit_results),
        "block_cases": len(block_results),
        "block_success_rate": (
            round(sum(1 for item in block_results if item["block_success"]) / len(block_results), 4)
            if block_results
            else 0.0
        ),
        "forbidden_hit_rate": round(sum(1 for item in results if item["forbidden_hit"]) / len(results), 4),
        "prefill_leakage_rate": round(sum(1 for item in results if item["prefill_leakage"]) / len(results), 4),
    }


def render_memory_retrieval_markdown(result: dict[str, Any]) -> str:
    summary = result.get("summary", {})
    lines = [
        "# Memory Retrieval Benchmark",
        "",
        f"- cases: {result.get('cases', 0)}",
        f"- hit cases: {summary.get('hit_cases', 0)}",
        f"- block cases: {summary.get('block_cases', 0)}",
        f"- precision@k: {summary.get('precision_at_k', 0.0):.4f}",
        f"- recall@k: {summary.get('recall_at_k', 0.0):.4f}",
        f"- MRR: {summary.get('mrr', 0.0):.4f}",
        f"- prefill precision: {summary.get('prefill_precision', 0.0):.4f}",
        f"- negative transfer rate: {summary.get('negative_transfer_rate', 0.0):.4f}",
        f"- block success rate: {summary.get('block_success_rate', 0.0):.4f}",
        f"- forbidden hit rate: {summary.get('forbidden_hit_rate', 0.0):.4f}",
        f"- prefill leakage rate: {summary.get('prefill_leakage_rate', 0.0):.4f}",
        "",
        "| case | type | kind | retrieved | prefill | forbidden hits | block success | negative transfer |",
        "|---|---|---|---|---|---|---:|---:|",
    ]
    for item in result.get("by_case", []):
        lines.append(
            "| {case_id} | {memory_type} | {case_kind} | {retrieved} | {prefill} | {forbidden} | {block_success} | {negative} |".format(
                case_id=item["case_id"],
                memory_type=item["memory_type"],
                case_kind=item["case_kind"],
                retrieved=", ".join(item["retrieved_ids"]) or "-",
                prefill=", ".join(item["prefill_ids"]) or "-",
                forbidden=", ".join(item["forbidden_hits"]) or "-",
                block_success="-" if item["block_success"] is None else int(bool(item["block_success"])),
                negative=int(bool(item["negative_transfer"])),
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def _repair_match_id(match: Any) -> str:
    metadata_id = str(match.record.metadata.get("memory_id", "")).strip()
    return metadata_id or match.record.error_signature


def _is_block_case(case: MemoryRetrievalCase) -> bool:
    if case.case_kind == "block":
        return True
    return not case.expected_ids and bool(case.forbidden_ids or not case.expected_prefill_ids)


def _reciprocal_rank(retrieved_ids: list[str], expected_ids: set[str]) -> float:
    if not expected_ids:
        return 1.0
    for index, item in enumerate(retrieved_ids, start=1):
        if item in expected_ids:
            return 1.0 / index
    return 0.0


def _prefill_precision(prefill_ids: list[str], expected_prefill_ids: set[str]) -> float:
    if not prefill_ids:
        return 1.0 if not expected_prefill_ids else 0.0
    if not expected_prefill_ids:
        return 0.0
    return len(set(prefill_ids) & expected_prefill_ids) / len(prefill_ids)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate TopoMen memory retrieval quality against gold cases.")
    parser.add_argument("cases", type=Path, help="JSONL gold cases for memory retrieval evaluation.")
    parser.add_argument("--repair-memory-path", type=Path, help="Repair Memory JSONL path.")
    parser.add_argument("--pattern-memory-path", type=Path, help="Pattern Memory JSONL path.")
    parser.add_argument("--runtime-validation-memory-path", type=Path, help="Runtime Validation Memory JSONL path.")
    parser.add_argument("--json-path", type=Path, help="Optional JSON summary output path.")
    parser.add_argument("--report-path", type=Path, help="Optional Markdown report output path.")
    parser.add_argument("--print-markdown", action="store_true", help="Print Markdown instead of JSON.")
    return parser


def main_cli(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = evaluate_memory_retrieval(
        load_retrieval_cases(args.cases),
        repair_memory_path=args.repair_memory_path,
        pattern_memory_path=args.pattern_memory_path,
        runtime_validation_memory_path=args.runtime_validation_memory_path,
    )
    if args.json_path:
        args.json_path.parent.mkdir(parents=True, exist_ok=True)
        args.json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    report = render_memory_retrieval_markdown(result)
    if args.report_path:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(report, encoding="utf-8")
    if args.print_markdown:
        console.print(report)
    else:
        console.print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
