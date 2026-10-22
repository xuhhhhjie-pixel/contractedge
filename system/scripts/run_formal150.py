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
BENCHMARK = Path('/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_files(directory):
    return {str(p.relative_to(directory)): digest(p) for p in sorted(directory.rglob('*'))
            if p.is_file() and p.suffix in {'.py', '.json'}}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--readiness-report', type=Path, default=ROOT / 'outputs/deepseek-formal150-readiness-budget80-20261020/readiness.json')
    args = parser.parse_args()
    out = args.output.resolve()
    if out.is_relative_to(BENCHMARK) or BENCHMARK.is_relative_to(out):
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
        write(manifest_path, {'source_files': before, 'config_sha256': digest(freeze / 'config.json')})
    if not manifest_path.is_file():
        raise ValueError('Output is not a completely prepared formal run; use a new directory')
    manifest = json.loads(manifest_path.read_text())
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
    code_digest = _method_digest()
    if ready is not None and (ready.get('method_code_digest') != code_digest
            or ready.get('production_config_sha256') != manifest['config_sha256']
            or ready.get('launcher_sha256') != digest(Path(__file__))):
        raise ValueError('Readiness report does not bind this exact method/configuration/launcher')
    if args.execute:
        os.environ[config['api_key_env']] = Path('/home/xhj/.config/contractedge/chatanywhere-experiment-20261019.key').read_text().strip()
    provider, tokenizer = _build_provider(config)
    before_money = provider.money_budget.summary()
    def execute(case, directory):
        if not args.execute:
            raise AssertionError('Preflight must not dispatch a case or call a model')
        return repair_case(BENCHMARK / 'public', BENCHMARK / 'tools', case, directory, provider, tokenizer, config)
    result = run_batch(BENCHMARK / 'public', out, config, execute,
                       max_new_cases=150 if args.execute else 0, tools_root=BENCHMARK / 'tools')
    if not args.execute and result['attempted'] != 0:
        raise ValueError('Preflight requires a fresh unattempted formal cohort')
    summary = {'mode': 'EXECUTE' if args.execute else 'ZERO_CALL_PREFLIGHT', 'case_count': result['case_count'],
        'attempted': result['attempted'], 'status_counts': result['status_counts'],
        'stop_reason': result.get('stop_reason'), 'method_code_digest': code_digest,
        'production_config_sha256': manifest['config_sha256'], 'launcher_sha256': digest(Path(__file__)),
        'readiness_report': str(args.readiness_report.resolve()) if args.execute else None,
        'money_before': before_money, 'money_after': provider.money_budget.summary(),
        'scope': 'Frozen150 dispatch capability; the80CNY global cap may stop before all150 are attempted'}
    write(out / 'invocations' / (str(time.time_ns()) + '.json'), summary)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
