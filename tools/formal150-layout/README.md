# Published-class to unified150 layout bridge

`../assemble_formal150.py` restores the operative layout used by qualified ContractEdge0.6.68 from the five published class archives. `layout.json` records the original ordering and exact SHA256 of the three150-member manifests, plus six immutable supplemental public metadata/tool files. Evaluation manifest rows are reconstructed from the already published packages; hidden evaluator bodies are never included in this Git directory.

The bridge checks each package entry/checksum-list against `benchmark/RELEASE_INDEX.json`, verifies every listed payload, rejects shared-file conflicts and unsafe paths, copies independently, and checks copied bytes. It never edits the input packages or replaces an existing destination. A partial destination without `ASSEMBLY_RECEIPT.json` is not runnable. Validation-only mode does not certify a copied directory.

The resulting directory has the exact public/evaluation/partition identities and all98 Python/JSON tools used in the qualified execution identity. It is an operative view, not a byte-identical restoration of the entire historical parent release: some aggregate evidence/provenance summaries are intentionally absent. The included original `verify_release147.py`/`verify_release150.py` utilities are retained to match the frozen tool identity, not advertised as whole-archive checks for this view. Use the bridge receipt, input preflight and normal isolated admission instead.

Real verification: `system/outputs/alert-repeat-closure-20261028/V068_RELEASE_LAYOUT_VERIFICATION.json`. Five packaging guard tests: `python3 tools/test_assemble_formal150.py`. These are delivery checks outside the frozen repair method; no repair source, cohort result, hidden oracle or original certificate is changed.
