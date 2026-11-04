"""Prepare a frozen150 run; --execute additionally requires a readiness receipt.

Default is a zero-call preflight. The credential stays in its private file.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BENCHMARK = Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-formal150-v1-build')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files(directory):
    return {str(p.relative_to(directory)): digest(p) for p in sorted(directory.rglob('*'))
            if p.is_file() and p.suffix in {'.py', '.json'}}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def _digest_refs(value):
    """Return immutable container references nested in a JSON value."""
    found = set()
    if isinstance(value, str) and '@sha256:' in value:
        # Runtime manifests and execution contracts use the same immutable
        # reference grammar as check_runtime.  Keep the preflight strict: a
        # digest-looking string with a malformed name is still a declaration
        # error rather than silently ignored coverage.
        found.add(value)
    elif isinstance(value, dict):
        for item in value.values():
            found.update(_digest_refs(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_digest_refs(item))
    return found


def runtime_image_refs(benchmark):
    """Collect every exact digest-pinned image from all public route sources."""
    benchmark = Path(benchmark).resolve(strict=True)
    refs = set()
    sources = []
    runtime_manifest = benchmark / 'runtime' / 'manifest.json'
    if runtime_manifest.is_file():
        sources.append(runtime_manifest)
    public_manifest = benchmark / 'public' / 'manifest.jsonl'
    if public_manifest.is_file():
        # Only selected task directories are part of the formal cohort.  A
        # build may retain superseded v1/v2 runner assets beside the selected
        # task; scanning those would create false missing-image failures.
        for line in public_manifest.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            task = (benchmark / 'public' / row['task_path']).resolve(strict=True)
            task_manifest = task / 'manifest.json'
            names = set()
            if task_manifest.is_file():
                declared = json.loads(task_manifest.read_text(encoding='utf-8'))
                names.update(name for name in ('environment.json', 'execution_contract.json')
                             if name in (declared.get('files') or []))
            else:
                names.update(name for name in ('environment.json', 'execution_contract.json')
                             if (task / name).is_file())
            sources.extend(task / name for name in sorted(names))
    else:
        sources.extend(sorted((benchmark / 'public').rglob('environment.json')))
        sources.extend(sorted((benchmark / 'public').rglob('execution_contract.json')))
    if not sources:
        raise ValueError('Benchmark contains no runtime manifest or public execution declarations')
    for path in sources:
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise ValueError('Invalid runtime declaration: ' + str(path)) from exc
        declared = _digest_refs(value)
        if path.name != 'manifest.json' and not declared:
            raise ValueError('Public execution declaration has no digest-pinned runtime image: ' + str(path))
        refs.update(declared)
    if not refs:
        raise ValueError('Benchmark contains no digest-pinned runtime image declarations')
    return sorted(refs)


def benchmark_identity(benchmark):
    """Bind the exact public/tools/runtime assets used by a formal run."""
    benchmark = Path(benchmark).resolve(strict=True)
    public = benchmark / 'public'
    tools = benchmark / 'tools'
    if not public.is_dir() or not tools.is_dir():
        raise ValueError('Benchmark root must contain public/ and tools/: ' + str(benchmark))
    manifest = public / 'manifest.jsonl'
    if not manifest.is_file():
        raise ValueError('Benchmark root is missing public/manifest.jsonl: ' + str(benchmark))
    runtime = benchmark / 'runtime' / 'manifest.json'
    if not runtime.is_file():
        raise ValueError('Benchmark root is missing runtime/manifest.json: ' + str(benchmark))
    return {'root': str(benchmark), 'public_manifest_sha256': digest(manifest),
            'runtime_manifest_sha256': digest(runtime),
            'tools_sha256': digest_tree(tools)}


def digest_tree(directory):
    return hashlib.sha256(json.dumps({str(path.relative_to(directory)): digest(path)
        for path in sorted(directory.rglob('*')) if path.is_file()}, sort_keys=True).encode()).hexdigest()


def run_runtime_preflight(benchmark, config):
    """Verify all public runtime images without pulling or running a model."""
    from contractedge.execution.runtime_preflight import check_runtime
    settings = dict(config.get('runtime_preflight') or {})
    allowed = {'cpus', 'memory_mib', 'context', 'timeout_seconds'}
    if set(settings) - allowed:
        raise ValueError('runtime_preflight contains unsupported settings')
    images = runtime_image_refs(benchmark)
    reports = []
    for image in images:
        report = check_runtime(verify_image=image, **settings)
        reports.append(report)
        # A shared Docker daemon/context failure cannot be repaired by probing
        # the remaining images; preserve them as explicitly unverified.
        if report.get('status') == 'INFRA_ERROR' and any(
                str(error).startswith(('DOCKER_', 'API_', 'OSError', 'PermissionError'))
                for error in report.get('errors', [])):
            break
    if len(reports) < len(images):
        reports.extend({'status': 'SKIPPED_SHARED_INFRA_FAILURE', 'image': image,
                         'runtime_verified': False, 'errors': ['shared Docker preflight failed']}
                        for image in images[len(reports):])
    return {'schema_version': 'ce.formal_runtime_preflight.v1',
            'status': 'PASS' if reports and all(r.get('status') == 'PASS' for r in reports) else 'FAIL',
            'image_count': len(images), 'reports': reports}


def configure_api_key(config, *, api_key_file=None, environ=None):
    """Populate the provider variable without embedding credentials.

    An explicitly supplied environment value wins. A private file may be
    supplied by the CLI or CONTRACTEDGE_API_KEY_FILE; it is never part of the
    frozen method or any receipt.
    """
    env = os.environ if environ is None else environ
    variable = config.get('api_key_env')
    if not isinstance(variable, str) or not variable.strip():
        raise ValueError('Formal configuration requires a non-empty api_key_env')
    if env.get(variable):
        return {'source': 'environment', 'env': variable}
    filename = api_key_file or env.get('CONTRACTEDGE_API_KEY_FILE')
    if not isinstance(filename, str) or not filename.strip():
        raise ValueError('Formal execution requires api_key_file via --api-key-file or CONTRACTEDGE_API_KEY_FILE when the provider environment is unset')
    path = Path(filename).expanduser()
    if path.is_symlink() or not path.is_file():
        raise ValueError('Configured private API key file is missing or is a symlink: ' + str(path))
    value = path.read_text(encoding='utf-8').strip()
    if not value:
        raise ValueError('Configured private API key file is empty: ' + str(path))
    env[variable] = value
    return {'source': 'private_file', 'env': variable, 'path': str(path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--benchmark-root', type=Path, default=DEFAULT_BENCHMARK,
                        help='frozen benchmark build containing public/, tools/ and runtime/')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--runtime-preflight', action='store_true',
                        help='verify every digest-pinned public runtime image before constructing the provider')
    parser.add_argument('--api-key-file', type=Path,
                        help='private API key file used only when the configured environment is unset')
    parser.add_argument('--money-ledger', type=Path,
                        help='run-local money ledger; defaults to the path relative to the output directory in the profile')
    parser.add_argument('--readiness-report', type=Path, default=ROOT / 'outputs/deepseek-formal150-readiness-budget80-20261020/readiness.json')
    args = parser.parse_args()
    out = args.output.resolve()
    benchmark = args.benchmark_root.resolve(strict=True)
    benchmark_info = benchmark_identity(benchmark)
    if out.is_relative_to(benchmark) or benchmark.is_relative_to(out):
        raise ValueError('Output must be outside the frozen benchmark')
    if (out / 'STOPPED_BY_USER.json').exists():
        raise ValueError('User-stopped experiment cannot be resumed by this launcher')
    ready = None
    if args.execute:
        ready = json.loads(args.readiness_report.read_text())
        if ready.get('status') != 'READY_FOR_FORMAL150':
            raise ValueError('Formal execution requires an accepted readiness report')
    freeze = out / 'method-freeze'
    manifest_path = freeze / 'manifest.json'
    profile = ROOT / 'configs/deepseek-formal150-production-v2-budget80.json'
    if not out.exists():
        before = source_files(ROOT / 'src')
        out.mkdir(parents=True)
        for name in before:
            target = freeze / 'src' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / 'src' / name, target)
            target.chmod(0o444)
        shutil.copy2(profile, freeze / 'config.json')
        (freeze / 'config.json').chmod(0o444)
        if source_files(ROOT / 'src') != before or source_files(freeze / 'src') != before:
            raise ValueError('Source changed while preparing the method freeze')
        write(manifest_path, {'source_files': before, 'config_sha256': digest(freeze / 'config.json'),
                              'benchmark': benchmark_info})
    if not manifest_path.is_file():
        raise ValueError('Output is not a completely prepared formal run; use a new directory')
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('benchmark') != benchmark_info:
        raise ValueError('Frozen benchmark root or runtime/public/tool identity changed; use a new output')
    if source_files(freeze / 'src') != manifest['source_files'] or digest(freeze / 'config.json') != manifest['config_sha256']:
        raise ValueError('Frozen method or configuration changed')
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT / '.topomen-deps'))
    sys.path.insert(0, str(freeze / 'src'))
    from contractedge.cli import _build_provider
    from contractedge.orchestration.batch import run_batch, _method_digest
    from contractedge.orchestration.repair import repair_case
    config = json.loads((freeze / 'config.json').read_text())
    if config.get('development_only') is not False or config.get('expected_case_count') != 150:
        raise ValueError('Formal profile must retain all150 members')
    if (str(config.get('base_url', '')).rstrip('/') != 'https://api.deepseek.com'
            or config.get('model') != 'deepseek-flash'
            or config.get('model_identity_policy') != 'require_match'):
        raise ValueError('Formal profile must bind the official DeepSeek endpoint, deepseek-flash, and require_match identity')
    code_digest = _method_digest()
    if ready is not None and (ready.get('method_code_digest') != code_digest
            or ready.get('production_config_sha256') != manifest['config_sha256']
            or ready.get('launcher_sha256') != digest(Path(__file__))):
        raise ValueError('Readiness report does not bind this exact method/configuration/launcher')
    money = dict(config.get('money_budget') or {})
    if not money:
        raise ValueError('Formal profile must declare money_budget')
    ledger = args.money_ledger or Path(str(money.get('path', 'money-ledger.json')))
    if not ledger.is_absolute():
        ledger = out / ledger
    money['path'] = str(ledger.resolve())
    config['money_budget'] = money
    runtime_report = None
    if args.runtime_preflight:
        runtime_report = run_runtime_preflight(benchmark, config)
        write(out / 'runtime-preflight.json', runtime_report)
        if runtime_report['status'] != 'PASS':
            raise RuntimeError('FORMAL_RUNTIME_PREFLIGHT_FAILED; inspect ' + str(out / 'runtime-preflight.json'))
    if args.execute:
        if not args.runtime_preflight:
            raise ValueError('--execute requires --runtime-preflight')
        configure_api_key(config, api_key_file=str(args.api_key_file) if args.api_key_file else None)
    provider, tokenizer = _build_provider(config)
    before_money = provider.money_budget.summary()
    def execute(case, directory):
        if not args.execute:
            raise AssertionError('Preflight must not dispatch a case or call a model')
        return repair_case(benchmark / 'public', benchmark / 'tools', case, directory, provider, tokenizer, config)
    result = run_batch(benchmark / 'public', out, config, execute,
                       max_new_cases=150 if args.execute else 0, tools_root=benchmark / 'tools')
    if not args.execute and result['attempted'] != 0:
        raise ValueError('Preflight requires a fresh unattempted formal cohort')
    summary = {'mode': 'EXECUTE' if args.execute else 'ZERO_CALL_PREFLIGHT', 'case_count': result['case_count'],
        'attempted': result['attempted'], 'status_counts': result['status_counts'],
        'stop_reason': result.get('stop_reason'), 'method_code_digest': code_digest,
        'production_config_sha256': manifest['config_sha256'], 'launcher_sha256': digest(Path(__file__)),
        'benchmark': benchmark_info,
        'readiness_report': str(args.readiness_report.resolve()) if args.execute else None,
        'money_before': before_money, 'money_after': provider.money_budget.summary(),
        'scope': 'Frozen150 dispatch capability; the80CNY global cap may stop before all150 are attempted'}
    write(out / 'invocations' / (str(time.time_ns()) + '.json'), summary)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
