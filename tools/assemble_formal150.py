"""Parent-only packaging: reconstruct the operative150 layout, never run a model.

Inputs are the five extracted archives from tools/download_benchmark.py. All
listed payload bytes are checked against the published checksum-list pins.
Private evaluation files are copied as opaque bytes, not interpreted as tasks.
This tool is outside the frozen repair method; it does not issue admission.
"""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess


ROOT = Path(__file__).resolve().parent.parent
META = ROOT / 'tools/formal150-layout'
CLASSES = ('R1', 'R2', 'R3', 'R4', 'R5')
# These are class-specific summaries, not shared executable payloads.
SUMMARIES = {'README.md', 'RELEASE_ENTRY.json', 'public/manifest.jsonl',
             'evaluation/manifest.jsonl', 'partition/member_groups.jsonl',
             'runtime/manifest.json', 'evidence/calibration/index.jsonl',
             'evidence/current-case-audit.jsonl', 'evidence/export-identity.json',
             'evidence/public-prepare-route-check.json',
             'evidence/relocated30-score-routes.json'}


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(8 * 1024**2), b''):
            h.update(block)
    return h.hexdigest()


def confined(root, name):
    p = PurePosixPath(name)
    if (not name or '\\' in name or p.is_absolute() or p.as_posix() != name
            or any(x in {'.', '..'} for x in p.parts)):
        raise ValueError('Unsafe package path')
    path = root
    for part in p.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError('Package symlink is not a registered regular file')
    if not path.is_file():
        raise ValueError('Missing package file: ' + name)
    return path


def prepare(packages):
    index = json.loads((ROOT / 'benchmark/RELEASE_INDEX.json').read_text())
    plan = json.loads((META / 'layout.json').read_text())
    payload = {}; manifests = defaultdict(dict); package_pins = {}; cache = {}
    for cl in CLASSES:
        spec = index['classes'][cl]
        root = packages / spec['archive_root']
        if root.is_symlink():
            raise ValueError('Package root must not be a symlink')
        sums = confined(root, 'SHA256SUMS')
        if sha(sums) != spec['payload_checksums_sha256']:
            raise ValueError('Published checksum-list mismatch: ' + cl)
        entry = confined(root, 'RELEASE_ENTRY.json')
        if sha(entry) != spec['package_entry_sha256']:
            raise ValueError('Published package identity mismatch: ' + cl)
        package_pins[cl] = dict(checksums_sha256=sha(sums), entry_sha256=sha(entry))
        seen = set()
        for line in sums.read_text().splitlines():
            expected, name = line.split(maxsplit=1)
            name = name.removeprefix('*')
            if name in seen or len(expected) != 64:
                raise ValueError('Duplicate or invalid checksum entry')
            seen.add(name)
            path = confined(root, name)
            stat = path.stat(); key = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
            if key not in cache:
                cache[key] = sha(path)
            if cache[key] != expected:
                raise ValueError('Payload checksum mismatch: ' + cl + '/' + name)
            if name in SUMMARIES:
                continue
            if name in payload and payload[name][1] != expected:
                raise ValueError('Conflicting shared payload: ' + name)
            payload.setdefault(name, (path, expected))
        for name in plan['manifest_case_order']:
            if name not in seen:
                raise ValueError('Unpinned manifest')
            for row in map(json.loads, confined(root, name).read_text().splitlines()):
                case = row['case_id']
                if case in manifests[name]:
                    raise ValueError('Duplicate case across classes')
                manifests[name][case] = row
    generated = {}
    for name, order in plan['manifest_case_order'].items():
        if len(order) != 150 or len(set(order)) != 150 or set(order) != set(manifests[name]):
            raise ValueError('The exact150 membership is required')
        raw = ''.join(json.dumps(manifests[name][case], ensure_ascii=False) + '\n' for case in order).encode()
        if hashlib.sha256(raw).hexdigest() != plan['manifest_sha256'][name]:
            raise ValueError('Reconstructed manifest identity differs: ' + name)
        generated[name] = raw
    for name, expected in plan['metadata_sha256'].items():
        path = confined(META / 'metadata', name)
        if sha(path) != expected:
            raise ValueError('Layout supplement identity differs')
        if name in payload and payload[name][1] != expected:
            raise ValueError('Supplement conflicts with published payload')
        payload[name] = (path, expected)
    return payload, generated, package_pins, plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packages', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        raise ValueError('Output must be new; never replace an existing dataset')
    payload, generated, pins, plan = prepare(args.packages.resolve(strict=True))
    report = dict(status='VERIFIED_MAPPING', cases=150, files=len(payload)+len(generated),
                  package_pins=pins, manifest_sha256=plan['manifest_sha256'],
                  metadata_sha256=plan['metadata_sha256'], model_calls=0,
                  scope='Operative input/tool/evaluator/runtime layout. Historical aggregate evidence is not recreated; not a new repair result or environment admission.')
    if not args.verify_only:
        # Private output; partial copies have no completion receipt and must not run.
        args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
        required = sum(p.stat().st_size for p, _ in payload.values())
        if shutil.disk_usage(args.output).free < required + 1024**3:
            raise ValueError('Insufficient space for an independent copy')
        for name, (source, expected) in payload.items():
            target = args.output / name; target.parent.mkdir(parents=True, exist_ok=True)
            # Reflinks are copy-on-write. Never create writable hardlinks/symlinks.
            subprocess.run(['cp', '--reflink=auto', '--', str(source), str(target)], check=True)
            if sha(target) != expected:
                raise ValueError('Copied payload changed: ' + name)
        for name, raw in generated.items():
            p = args.output / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(raw)
        report['status'] = 'ASSEMBLED'
        (args.output / 'ASSEMBLY_RECEIPT.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
