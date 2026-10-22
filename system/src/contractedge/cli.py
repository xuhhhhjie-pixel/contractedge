from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from contractedge.contracts.validation import parse_method_input, validate_run_trace
from contractedge.dataset.archive import inspect_archive, safe_extract
from contractedge.dataset.audit import audit_prepared_release
from contractedge.dataset.export import prepare_release
from contractedge.dataset.releases import load_private_gt_paths, load_release_members
from contractedge.dataset.benchmark import method_input_from_task, prepare_benchmark
from contractedge.evaluation.batch import build_cohort_report
from contractedge.model.provider import OllamaNativeProvider, OpenAICompatibleProvider
from contractedge.orchestration.runner import run_front_half


def _json_file(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _assignments(values: list[str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values or []:
        if "=" not in value:
            raise ValueError(f"expected NAME=VALUE, got {value!r}")
        key, item = value.split("=", 1)
        if not key or not item or key in result:
            raise ValueError(f"invalid or duplicate NAME=VALUE assignment: {value!r}")
        result[key] = item
    return result


def _read_jsonl_map(path: str | Path, key: str) -> dict[str, dict[str, Any]]:
    result = {}
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict) or not isinstance(value.get(key), str) or value[key] in result:
            raise ValueError(f"invalid/duplicate JSONL {key} at line {number}")
        result[value[key]] = value
    return result


def _read_jsonl_rows(path: str | Path) -> list[dict[str, Any]]:
    rows = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"expected JSON object at {path}:{number}")
        rows.append(value)
    return rows


def _safe_public_file(root: Path, relative: str) -> Path:
    item = PurePosixPath(relative)
    if (not relative or item.is_absolute() or item.as_posix() != relative
            or any(part in {"", ".", ".."} for part in item.parts) or "\\" in relative):
        raise ValueError(f"unsafe public relative path: {relative!r}")
    path = root.joinpath(*item.parts)
    if path.is_symlink():
        raise ValueError(f"public file is a symlink: {relative}")
    path.resolve(strict=True).relative_to(root.resolve(strict=True))
    if not path.is_file():
        raise ValueError(f"public file is missing: {relative}")
    return path


def validate_public_input(method_input_path: str | Path, public_root: str | Path) -> dict[str, Any]:
    root = Path(public_root).resolve(strict=True)
    input_file = Path(method_input_path)
    if input_file.is_symlink():
        raise ValueError("MethodInput is a symlink")
    resolved_input = input_file.resolve(strict=True)
    try:
        resolved_input.relative_to(root)
    except ValueError as error:
        raise ValueError("MethodInput must be inside the public release root") from error
    value = _json_file(resolved_input)
    method_input = parse_method_input(value)
    archive = _safe_public_file(root, method_input.archive_path)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != method_input.archive_sha256:
        raise ValueError("Broken archive SHA-256 does not match MethodInput")
    inspection = inspect_archive(archive, source_root=method_input.source_root)
    if inspection.snapshot_id != method_input.initial_snapshot_id:
        raise ValueError("Broken archive snapshot does not match MethodInput")
    for artifact in method_input.declared_artifacts:
        artifact_path = _safe_public_file(root / "cases" / method_input.case_id, artifact["path"])
        if hashlib.sha256(artifact_path.read_bytes()).hexdigest() != artifact["content_digest"]:
            raise ValueError(f"declared artifact digest mismatch: {artifact['id']}")
    for evidence in method_input.public_evidence:
        evidence_path = _safe_public_file(root, evidence["locator"]["path"])
        if hashlib.sha256(evidence_path.read_bytes()).hexdigest() != evidence["content_digest"]:
            raise ValueError(f"public evidence digest mismatch: {evidence['id']}")
    return {"schema_version": "ce.method_input_audit.v1", "status": "PASS",
            "case_id": method_input.case_id, "release_id": method_input.release_id,
            "input_digest": method_input.input_digest, "archive_sha256": digest,
            "snapshot_id": inspection.snapshot_id, "source_root": inspection.source_root,
            "source_file_count": len(inspection.file_digests)}


def _token_counter(specification: str):
    if ":" not in specification:
        raise ValueError("tokenizer must use module:callable form")
    module_name, attribute = specification.split(":", 1)
    if not module_name or not attribute:
        raise ValueError("tokenizer must use module:callable form")
    function = getattr(importlib.import_module(module_name), attribute)
    if not callable(function):
        raise ValueError("configured tokenizer is not callable")
    return function


def _build_provider(config: Mapping[str, Any]):
    provider_name = config.get("provider")
    if provider_name not in {"openai_compatible", "ollama_native"}:
        raise ValueError("provider must be explicitly set to 'openai_compatible' or 'ollama_native'")
    required = ["base_url", "model", "tokenizer"]
    if provider_name == "openai_compatible":
        required.append("api_key_env")
    for key in required:
        if not isinstance(config.get(key), str) or not config[key].strip():
            raise ValueError(f"model configuration requires non-empty {key!r}")
    counter = _token_counter(config["tokenizer"])
    identity = {'model_identity_policy': config.get('model_identity_policy', 'record'),
                'model_aliases': config.get('model_aliases')}
    if provider_name == "ollama_native":
        if config.get('output_mode', 'native_schema') != 'native_schema':
            raise ValueError('Ollama output_mode is native_schema; OpenAI format options do not apply')
        provider = OllamaNativeProvider(str(config["base_url"]), int(config.get("timeout_seconds", 600)),
                                        config.get('model_context_tokens'), **identity)
    else:
        strict = (config.get('output_mode', 'json_object') == 'json_schema'
                  and config.get('supports_json_schema') is True
                  and config.get('schema_fallback', 'error') == 'error')
        if config.get('schema_in_prompt') is False and not strict:
            raise ValueError('schema_in_prompt=false requires a native schema-constrained provider or strict JSON Schema without fallback')
        provider = OpenAICompatibleProvider(str(config["base_url"]), str(config["api_key_env"]),
                                            int(config.get("timeout_seconds", 120)), thinking=config.get('thinking'),
                                            reasoning_effort=config.get('reasoning_effort'),
                                            output_mode=config.get('output_mode', 'json_object'),
                                            supports_json_schema=config.get('supports_json_schema', False),
                                            schema_fallback=config.get('schema_fallback', 'error'),
                                            context_tokens=config.get('model_context_tokens'), token_counter=counter, **identity)
        if config.get('money_budget') is not None:
            from contractedge.model.money import MoneyBudget
            provider.money_budget = MoneyBudget(**config['money_budget'])
    return provider, counter


def _load_public_member(public_root: Path, case_id: str) -> tuple[dict[str, Any], Path]:
    rows = []
    for number, line in enumerate((public_root / "manifest.jsonl").read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            row = json.loads(line)
            if isinstance(row, dict) and row.get("case_id") == case_id:
                rows.append(row)
    if len(rows) != 1:
        raise ValueError(f"case {case_id!r} has {len(rows)} entries in the public release manifest")
    row = rows[0]
    relative = row.get("method_input") or (row["task_path"] + "/task.yaml")
    return row, _safe_public_file(public_root, relative)


def run_public_case(public_root: str | Path, case_id: str, config: Mapping[str, Any]):
    from contractedge.orchestration.runner import run_front_half
    from contractedge.dataset.admission import check_case_admission

    root = Path(public_root).resolve(strict=True)
    check_case_admission(root, case_id, config)
    row, method_path = _load_public_member(root, case_id)
    if 'task_path' in row:
        method_value, projection = method_input_from_task(root, case_id)
        config = {**dict(config), 'public_execution_capabilities': projection.get('public_execution_capabilities')}
    else:
        method_value = _json_file(method_path)
    method_input = parse_method_input(method_value)
    provider, tokenizer = _build_provider(config)
    run_config = {key: value for key, value in config.items()
                  if key not in {"provider", "base_url", "api_key_env", "timeout_seconds", "tokenizer"}}
    run_config["model"] = {"model": config["model"], "provider": config["provider"],
                           "schema_in_prompt": config.get('schema_in_prompt', True)}
    for key in ("model_revision", "tokenizer_revision"):
        if isinstance(config.get(key), str) and config[key]:
            run_config["model"][key] = config[key]
    if config.get("ollama_model_digest"):
        _verify_ollama_model(config)
    archive_path = _safe_public_file(root, method_input.archive_path)
    with tempfile.TemporaryDirectory(prefix=f"contractedge-{case_id}-") as temporary:
        workspace = Path(temporary) / "broken"
        safe_extract(archive_path, workspace)
        trace = run_front_half(method_input, workspace, provider, run_config, tokenizer)
    return trace


def _verify_ollama_model(config: Mapping[str, Any]) -> None:
    """Require a named Ollama model to resolve to the frozen local digest."""
    base_url = str(config.get("base_url", "")).rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]
    if not base_url.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]")):
        raise ValueError("ollama_model_digest verification is restricted to a loopback Ollama endpoint")
    with urllib.request.urlopen(base_url + "/api/tags", timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    matches = [row for row in payload.get("models", []) if row.get("name") == config.get("model")]
    if len(matches) != 1 or matches[0].get("digest") != config["ollama_model_digest"]:
        raise ValueError("configured Ollama model does not match the frozen model digest")


def _write_json(path: str | Path, value: Mapping[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)


def _prepare(args: argparse.Namespace) -> dict[str, Any]:
    manifests = _assignments(args.manifest)
    roots = _assignments(args.package_root)
    private_manifests = _assignments(args.private_manifest)
    private_roots = _assignments(args.private_root)
    members = []
    gt_paths = {}
    for release_id, manifest_path in manifests.items():
        manifest = Path(manifest_path).resolve()
        package_root = Path(roots[release_id]).resolve() if release_id in roots else manifest.parent
        members.extend(load_release_members(manifest, release_id, package_root))
        if release_id in private_manifests:
            private_manifest = Path(private_manifests[release_id]).resolve()
            private_root = Path(private_roots[release_id]).resolve() if release_id in private_roots else private_manifest.parent.parent
            gt_paths.update(load_private_gt_paths(private_manifest, private_root))
    source_roots = {}
    if args.source_roots:
        raw_roots = _json_file(args.source_roots)
        source_roots = {case_id: (row.get("source_root") if isinstance(row, Mapping) else row)
                        for case_id, row in raw_roots.items()}
    reviews = _read_jsonl_map(args.requirement_reviews, "case_id") if args.requirement_reviews else {}
    return prepare_release(members, args.output, private_gt_paths=gt_paths, source_roots=source_roots,
                           r5_requirement_reviews=reviews, materialize=args.materialize)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="contractedge", description="ContractEdge frozen-benchmark planning, source repair and public feedback")
    subparsers = parser.add_subparsers(dest="command", required=True)
    runtime = subparsers.add_parser('runtime-check', help='diagnose Docker and verify actual CPU/memory limits')
    runtime.add_argument('--cpus', type=float, default=2)
    runtime.add_argument('--memory-mib', type=int, default=2048)
    runtime.add_argument('--context')
    runtime.add_argument('--image', help='trusted cached digest-pinned image for actual cgroup verification')
    runtime.add_argument('--timeout-seconds', type=float, default=20)
    validate = subparsers.add_parser("validate-input", help="validate one MethodInput and its public Broken archive")
    validate.add_argument("--method-input", required=True)
    validate.add_argument("--public-root", required=True)
    prepare = subparsers.add_parser("prepare", help="preflight/materialize manifest-listed isolated release views")
    prepare.add_argument("--manifest", action="append", required=True, metavar="RELEASE_ID=PATH")
    prepare.add_argument("--package-root", action="append", default=[], metavar="RELEASE_ID=PATH")
    prepare.add_argument("--private-manifest", action="append", default=[], metavar="RELEASE_ID=PATH")
    prepare.add_argument("--private-root", action="append", default=[], metavar="RELEASE_ID=PATH")
    prepare.add_argument("--source-roots", help="trusted JSON sidecar, keyed by case_id")
    prepare.add_argument("--requirement-reviews", help="reviewed public task decisions as JSONL")
    prepare.add_argument("--output", required=True)
    prepare.add_argument("--materialize", action="store_true")
    current = subparsers.add_parser("prepare-benchmark", help="project the current frozen benchmark into public MethodInputs")
    current.add_argument("--benchmark-root", required=True)
    current.add_argument("--output", required=True)
    audit = subparsers.add_parser("audit-release", help="verify public/private hash manifests and isolation")
    audit.add_argument("--release-root", required=True)
    audit.add_argument("--expected-members", type=int)
    run = subparsers.add_parser("run", help="run Requirement → Diagnosis → Semantic ChangeSet on a public case")
    run.add_argument("--public-root", required=True)
    run.add_argument("--case-id", required=True)
    run.add_argument("--config", required=True, help="model, tokenizer and run-budget JSON; contains no API key value")
    run.add_argument("--output", required=True)
    repair = subparsers.add_parser("repair", help="run the bounded public plan/patch/feedback loop on the frozen benchmark")
    repair.add_argument("--public-root", required=True)
    repair.add_argument("--tools-root", required=True)
    repair.add_argument("--case-id", required=True)
    repair.add_argument("--config", required=True)
    repair.add_argument("--output", required=True)
    repair.add_argument("--initial-trace", help="resume one matching ready development trace; its prior budget is retained")
    batch = subparsers.add_parser('repair-batch', help='resumable public repair cohort; independent hidden scoring is separate')
    batch.add_argument('--public-root', required=True)
    batch.add_argument('--tools-root', required=True)
    batch.add_argument('--config', required=True)
    batch.add_argument('--output', required=True)
    batch.add_argument('--case-id', action='append')
    batch.add_argument('--max-new-cases', type=int)
    repair_score = subparsers.add_parser('evaluate-repair-batch', help='independent hidden scoring of frozen final batch patches')
    repair_score.add_argument('--evaluation-root', required=True)
    repair_score.add_argument('--batch-root', required=True)
    repair_score.add_argument('--tools-root', required=True)
    repair_score.add_argument('--output', required=True)
    repair_score.add_argument('--timeout-seconds', type=float, default=7200)
    current_evaluate = subparsers.add_parser("evaluate-benchmark", help="evaluator-only full150 GT/trace cohort accounting")
    current_evaluate.add_argument("--benchmark-root", required=True)
    current_evaluate.add_argument("--trace-manifest")
    current_evaluate.add_argument("--trace-root")
    current_evaluate.add_argument("--output", required=True)
    evaluate = subparsers.add_parser("evaluate", help="score one trace in the evaluator-only process")
    evaluate.add_argument("--trace", required=True)
    evaluate.add_argument("--private-gt", required=True)
    evaluate.add_argument("--release-id", required=True)
    evaluate.add_argument("--release-metadata",
                          help="evaluator-only JSON object carrying class/oracle boundary metadata")
    evaluate_cohort = subparsers.add_parser(
        "evaluate-cohort", help="score trace manifest against frozen evaluator manifests, retaining all members")
    evaluate_cohort.add_argument("--r1-r2-manifest", required=True,
                                 help="evaluator manifest JSONL for the prepared R1/R2 cohort")
    evaluate_cohort.add_argument("--r1-r2-root", required=True,
                                 help="root containing R1/R2 evaluator ground_truth files")
    evaluate_cohort.add_argument("--r5-manifest", required=True,
                                 help="R5 v28 private manifest JSONL")
    evaluate_cohort.add_argument("--r5-root", required=True,
                                 help="R5 v28 release root containing private GT files")
    evaluate_cohort.add_argument("--trace-manifest", required=True,
                                 help="JSONL with case_id and trace path relative to --trace-root")
    evaluate_cohort.add_argument("--trace-root", required=True)
    evaluate_cohort.add_argument("--output", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == 'runtime-check':
            from contractedge.execution.runtime_preflight import check_runtime
            report = check_runtime(cpus=args.cpus, memory_mib=args.memory_mib, context=args.context,
                                   verify_image=args.image, timeout_seconds=args.timeout_seconds)
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            return 0 if report['status'] == 'PASS' else 1
        if args.command == "validate-input":
            report = validate_public_input(args.method_input, args.public_root)
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "prepare":
            report = _prepare(args)
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "prepare-benchmark":
            report = prepare_benchmark(args.benchmark_root, args.output)
            print(json.dumps({key: value for key, value in report.items() if key != "records"}, ensure_ascii=False))
            return 0 if report["status"] == "PASS" else 2
        if args.command == "audit-release":
            report = audit_prepared_release(args.release_root, expected_members=args.expected_members)
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            return 0 if report["status"] == "PASS" else 1
        if args.command == "run":
            config = _json_file(args.config)
            if not isinstance(config, Mapping):
                raise ValueError("run config must be a JSON object")
            trace = run_public_case(args.public_root, args.case_id, config)
            payload = validate_run_trace(trace.to_wire())
            _write_json(args.output, payload)
            print(json.dumps({"case_id": trace.case_id, "status": trace.status,
                              "run_id": trace.run_id, "trace_path": str(Path(args.output).resolve())},
                             ensure_ascii=False, sort_keys=True))
            return 0 if trace.status in {"READY", "READY_PARTIAL"} else 1
        if args.command == 'evaluate-repair-batch':
            from contractedge.evaluation.repair_batch import evaluate_repair_batch
            report = evaluate_repair_batch(args.evaluation_root,args.batch_root,args.tools_root,args.output,
                                           timeout_seconds=args.timeout_seconds)
            print(json.dumps({k:report[k] for k in ('case_count','status_counts','pass_count','pass_rate_fixed_denominator')}, ensure_ascii=False))
            return 0
        if args.command == 'repair-batch':
            from contractedge.orchestration.batch import run_batch
            from contractedge.orchestration.repair import repair_case
            config = _json_file(args.config)
            provider, tokenizer = _build_provider(config)
            report = run_batch(args.public_root, args.output, config,
                lambda case_id, directory: repair_case(args.public_root, args.tools_root, case_id,
                    directory, provider, tokenizer, config),
                case_ids=args.case_id, max_new_cases=args.max_new_cases, tools_root=args.tools_root)
            print(json.dumps({k: report[k] for k in ('case_count','attempted','unattempted','status_counts')}, ensure_ascii=False))
            return 0
        if args.command == "repair":
            from contractedge.orchestration.repair import repair_case
            config = _json_file(args.config)
            provider, tokenizer = _build_provider(config)
            if config.get("ollama_model_digest"):
                _verify_ollama_model(config)
            initial = _json_file(args.initial_trace) if args.initial_trace else None
            report = repair_case(args.public_root, args.tools_root, args.case_id, args.output,
                                 provider, tokenizer, config, initial_trace=initial)
            print(json.dumps({key: report[key] for key in ["case_id", "status", "run_id", "candidate", "errors"]}, ensure_ascii=False))
            return 0 if report["status"] in {"PUBLIC_REVIEW_COMPLETE", "PUBLIC_EXECUTION_COMPLETE"} else 1
        if args.command == "evaluate-benchmark":
            from contractedge.evaluation.benchmark import evaluate_benchmark
            report = evaluate_benchmark(args.benchmark_root, args.trace_manifest, args.trace_root)
            _write_json(args.output, report)
            print(json.dumps({key: report[key] for key in ["release_id", "case_count", "attempted", "status_counts", "eligible_for_relation_precision", "eligible_for_relation_recall"]}, ensure_ascii=False))
            return 0
        if args.command == "evaluate":
            from contractedge.evaluation.gt_adapter import load_private_gt, normalize_gt, score_run
            trace = _json_file(args.trace)
            private = load_private_gt(args.private_gt)
            metadata = _json_file(args.release_metadata) if args.release_metadata else None
            if args.release_id.split("-", 1)[0] == "R5" and metadata is None:
                raise ValueError("R5 evaluation requires evaluator-only --release-metadata to preserve its oracle boundary")
            result = score_run(trace, normalize_gt(private, args.release_id, release_metadata=metadata))
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "evaluate-cohort":
            r1_r2_members = _read_jsonl_rows(args.r1_r2_manifest)
            r5_members = _read_jsonl_rows(args.r5_manifest)
            members = []
            for row in r1_r2_members:
                members.append({**row, "ground_truth": row["ground_truth"],
                                "ground_truth_root": str(Path(args.r1_r2_root).resolve())})
            for row in r5_members:
                if row.get("formal_membership") is not True:
                    continue
                members.append({
                    "case_id": row["case_id"], "release_id": "R5",
                    "ground_truth": row["private_gt"],
                    "ground_truth_root": str(Path(args.r5_root).resolve()),
                    "ground_truth_sha256": row.get("private_gt_sha256"),
                    "primary_class": row.get("primary_class"),
                    "fault_boundary": row.get("fault_boundary"),
                    "observed_depth": row.get("observed_depth"),
                    "oracle_boundary": row.get("oracle_boundary"),
                })
            report = build_cohort_report(
                members, _read_jsonl_rows(args.trace_manifest), args.trace_root,
                args.r1_r2_root,
            )
            _write_json(args.output, report)
            print(json.dumps({"schema_version": report["schema_version"],
                              "case_count": report["case_count"],
                              "attempted_count": report["attempted_count"],
                              "report_path": str(Path(args.output).resolve())},
                             ensure_ascii=False, sort_keys=True))
            return 0
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error_type": type(exc).__name__, "error": str(exc)},
                         ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
