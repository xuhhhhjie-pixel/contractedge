"""Resume qualification preparation with a fresh output root; no frozen source edit."""
import hashlib, importlib.util, json
from pathlib import Path
root=Path('/home/xhj/contractedge-publication-context-20261026/system')
source=root/'.superpowers/sdd/2026-10-26-publication-context/qualify-v054-v3.py'
spec=importlib.util.spec_from_file_location('frozen_qualification',source)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
assert module.ROOT==Path('/home/xhj/contractedge-source054-v2-qualification-20261026')
assert not module.STATE.exists()
module.ROOT=Path('/home/xhj/contractedge-source054-v3-qualification-20261026')
assert not module.ROOT.exists()
receipt=root/'outputs/publication-context-20261026/QUALIFICATION_OUTPUT_ROOT_CORRECTION_v3.json'
with receipt.open('x') as f:
 json.dump(dict(reason='Frozen v3 preparer retained existing v2 output root; first invocation stopped before any state, test or paid call',effective_output_root=str(module.ROOT),frozen_driver_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),wrapper_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),source_modified=False,paid_calls=0),f,indent=2)
module.main()
