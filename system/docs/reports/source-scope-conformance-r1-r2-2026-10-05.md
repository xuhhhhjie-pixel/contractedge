# R1/R2 bounded scope conformance audit

Date: 2026-10-05  
Input: `outputs/pilot-v1_1-r1-r2-v2/public/manifest.jsonl`  
Reproducer: `scripts/audit_source_scope.py`  
Detailed per-case output: `outputs/source-scope-conformance-r1-r2-v2.json`

## Method

The audit enumerates only the 87 public MethodInputs in the release manifest. It validates each MethodInput, checks the Broken archive digest against both the manifest and MethodInput, safely extracts the archive, checks the configured source root and initial snapshot, then resolves every declared scope hint under the production defaults: 20,000 files, 64 MiB aggregate content, 2 MiB per file, and 64 matches per hint. It does not expose evaluator data, parse every returned unit, or run the model.

## Results

| Release | Members | Fully resolved | Incomplete coverage | Unmatched hint(s), search completed |
|---|---:|---:|---:|---:|
| R1 | 57 | 57 | 0 | 0 |
| R2 | 30 | 2 | 25 | 3 |
| Total | 87 | 59 | 25 | 3 |

All 87 archive digests and initial snapshots matched their public MethodInputs. The audit evaluated 326 hints. Thirty-one searches hit file/byte limits; 66 hint results exceeded the per-hint match cap; 8 hints had no matching path. A capped result list is now recorded as incomplete scope coverage even when the repository scan itself completed. The per-case seed count ranged from 2 to 179 (median 5), showing that concept hints can produce wide seed sets.

Three R2 cases have at least one unmatched hint without a search-budget or result-cap signal: `RW-DAPR-10132`, `RW-KUSTOMIZE-CONTROLLER-1614`, and `RW-SOURCE-WATCHER-371`. Eight zero-match hints occur across seven cases; several are path-shaped descriptions rather than literal repository paths. A path token with trailing explanation now resolves when it is exact at the source root or has a unique suffix under the archived repository root; ambiguous suffixes remain unresolved. Those cases can still have other resolved seeds. The 25 incomplete R2 cases retain any found seeds and unresolved hint metadata; this does not prove those seeds are exhaustive or that an implementation is absent.

## Implementation changes and interpretation

- Diagnosis prompt `diagnosis.v1.1.19` receives a bounded scope summary, unresolved hints, scan counts, result-cap flags, and previews. It explicitly says an unresolved, truncated, or capped search is not absence evidence.
- Scope results are ranked deterministically by explicit path matches and lexical concept-match score, with active-source paths preferred on ties. Unit extraction preserves this order so the fixed unit budget does not discard the highest-ranked seeds solely because their paths sort later alphabetically.
- A path hint may include trailing descriptive text or omit one archive-root prefix. Resolution accepts an exact source-root token (`PATH_DESCRIPTION_TOKEN`) or a unique suffix match (`PATH_UNIQUE_SUFFIX`) for known source-file extensions; ambiguous suffixes are never guessed.
- Regressions verify that match capping remains incomplete, the limitation reaches Diagnosis, and the first selected unit respects scope priority.

The audit establishes integrity and bounded-search behavior, not semantic precision of the 64 paths returned for a concept, parser coverage, or end-to-end Diagnosis accuracy. R2's high incomplete rate remains an implementation limitation to report and improve; do not describe all R2 hints as fully resolved. Incomplete scope is a retrieval limitation, never proof of absence.
