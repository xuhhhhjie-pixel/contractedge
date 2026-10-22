"""Verify exact tracked delivery bytes and excluded-runtime boundary."""
import hashlib,json
from pathlib import Path
root=Path(__file__).resolve().parents[1]
manifest=json.loads((root/'DELIVERY_MANIFEST.json').read_text())
failures=[name for name,digest in manifest['files'].items() if not (root/name).is_file() or hashlib.sha256((root/name).read_bytes()).hexdigest()!=digest]
if failures:raise SystemExit('Mismatched/missing files: '+', '.join(failures))
print('Verified',len(manifest['files']),'delivery files; formal_ready=',manifest['formal_ready'])
