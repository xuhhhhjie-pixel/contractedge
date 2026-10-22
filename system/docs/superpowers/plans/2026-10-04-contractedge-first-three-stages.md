# ContractEdge First Three Stages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Tasks follow TDD and use Python unittest.

**Goal:** Implement the trusted data boundary and the Requirement → Diagnosis → Semantic ChangeSet pipeline described in the supplied v1.1 spec, with reproducible pilot artifacts and no method-side access to private labels.

**Architecture:** A Python 3.10+ modular monolith exposes versioned JSON contracts across dataset preparation, requirement normalization, source retrieval, diagnosis, ChangeSet planning, orchestration, and private evaluation. The method process receives only a prepared public task root; trusted preparation and evaluator code run outside that root. A model provider and TopoMen adapter are ports; protocol tests can use deterministic fixtures, while live pilot acceptance requires the real provider configuration and TopoMen consumer.

**Tech Stack:** Python 3.10, PyYAML 5.4.1, jsonschema 4.26.0 (versions currently present on the server), stdlib `unittest`, JSON/JSONL contracts. Do not install dependencies until disk headroom is restored; current root filesystem is nearly full.

**Spec:** `docs/specs/ContractEdge_First_Three_Stages_v1.1.md` (copied from the user-attached text; the Mac-local original and v1 backup are not available in this server workspace).

## Global Constraints

- Public input schema is `ce.method_input.v1_1`; contracts are versioned JSON and use explicit null/empty values.
- Requirement, Diagnosis, and ChangeSet outputs keep source provenance, case/release identity, snapshot and content digests.
- Only manifest-listed release members enter the exporter; method-side process cannot access Fixed, GT, patches, repair provenance, hidden verifier, or evaluator paths.
- Broken archives are extracted only after path, symlink, and size validation; source root and frozen file-content snapshot are explicit.
- Default candidate top-N is 12; pilot compares 8/12/16; at most one evidence-gap extension and one ChangeSet revision.
- Method claims distinguish observed facts, static facts, and causal inference; `oracle_boundary` does not shrink Requirement text.
- Partial plans preserve open obligations and can include justified NEW targets; unsupported causal paths produce `NEEDS_EVIDENCE`.
- Evaluation records infrastructure failures separately from semantic outcomes and retains all launched formal cases in denominators.
- No R3/R4 queue candidate is added to formal membership by directory scan; current R3/R4 manifests are empty.
- No actual TopoMen compatibility or live pilot pass is claimed until its consumer interface and model backend are available and exercised.

## Review Focus

1. R1 requirement as a string (`RW-CAND-136`) and missing `broken/evidence` keys — Task 2/3 must normalize without losing original text and must not infer observations.
2. R2 nested PR/commit provenance in four records — Task 2 must strip it from method-visible data while retaining a trusted source map.
3. R5 private `case.yaml` with nested relations/history — Task 2 exports from formal manifests and its public projection contains no answer material.
4. Tar traversal, escaping symlinks, multiple repo roots, `path:selector`, symbol and conceptual scopes — Task 3 rejects unsafe extraction and records unresolved hints instead of guessing.
5. Stale snapshot references, unsupported relations, partial evidence, and finite iteration budgets — Tasks 5–8 reject stale/unsupported plans and stop at configured limits.

## File Structure

- `src/contractedge/contracts/`: typed dataclasses and JSON schema validation for method input, evidence, RequirementSpec, DiagnosisPacket, SemanticChangeSet, RunContext and status enums.
- `src/contractedge/dataset/`: trusted release readers, format-specific projections, secure archive handling, and public/private export.
- `src/contractedge/requirement/`: conservative RequirementSpec normalization and coverage checks.
- `src/contractedge/source/`: safe extraction, source-root registration, immutable snapshots and scope hint resolution.
- `src/contractedge/retrieval/`: syntax adapters, candidate de-duplication, one-hop edges, ranking, and explicit token-budget accounting.
- `src/contractedge/diagnosis/`: prompt payload construction, model-provider protocol, structured-output parsing and hard/semantic review hooks.
- `src/contractedge/changeset/`: existing/new target validation, coverage validation and TopoMen adapter port.
- `src/contractedge/orchestration/`: finite call budget, snapshot progression, trace and normalized failure routing.
- `src/contractedge/evaluation/`: evaluator-only GT normalization and metric denominator accounting; not imported by method CLI.
- `src/contractedge/cli.py`: `prepare`, `validate-input`, `run`, and `evaluate` entry points with separate public/private roots.
- `tests/`: unittest suites mirror module boundaries; `tests/fixtures/` contains synthetic, non-benchmark examples.
- `docs/reports/`: actual data inventory, release eligibility decisions, pilot manifest and limitations.

## Tasks

### Task 1: Contracts and project shell

**Interfaces:** `parse_method_input(dict) -> MethodInput`; `validate_diagnosis(dict, MethodInput, RunContext) -> DiagnosisPacket`; `validate_changeset(dict, DiagnosisPacket, Snapshot) -> ValidationResult`. Malformed schemas/versions raise `ContractError(code, path, message)`; semantically invalid plans return `ValidationResult` with structured errors, and UNKNOWN is never coerced to EXPECTED_MISSING.

**Files:** `pyproject.toml`, `src/contractedge/contracts/models.py`, `src/contractedge/contracts/validation.py`, `src/contractedge/contracts/schema/*.json`, `tests/test_contracts.py`.

- [ ] Write tests for schema-version rejection, ID foreign keys, snapshot mismatch, unknown-vs-missing units, and legal status enums.
- [ ] Run `PYTHONPATH=src python3 -m unittest tests.test_contracts`; confirm failures come from missing contracts.
- [ ] Implement minimal dataclasses and deterministic validators for `ce.method_input.v1_1`, `ce.requirement_spec.v1_1`, `ce.diagnosis_packet.v1_1`, and `ce.semantic_changeset.v1_1`.
- [ ] Re-run the contract suite and confirm explicit null/empty array handling.

### Task 2: Trusted dataset preparation and isolation

**Interfaces:** `load_release_members(root, release_id) -> list[ReleaseMember]`; `project_method_input(member, package_root) -> MethodInput`; `safe_extract(archive, destination, limits) -> SourceTree`; `prepare_release(root, release_ids, output_root) -> PreparationReport`. The method root contains `public/`; evaluator root contains `private/`; neither projection is symlinked to the other.

**Files:** `src/contractedge/dataset/releases.py`, `projections.py`, `archive.py`, `export.py`, `tests/test_dataset_export.py`, `tests/test_archive_safety.py`.

- [ ] Write fixture tests for R1 `requirement.text`/string, R2 `requirement.statement`, R2 missing Broken/evidence, four provenance-bearing R2 inputs, nested R5 relations/history, and public/private separation.
- [ ] Verify RED; implement manifest-only enumeration, conflict detection when multiple requirement sources disagree, provenance allow-list projection, sidecar archive mapping, and separate evaluator output.
- [ ] Add archive tests for `../`, absolute paths, escaping symlinks, excessive expanded size, and explicit source roots.
- [ ] Run unit tests; then export the 117 formal R1/R2/R5 members into a new versioned system pilot root without modifying official releases. Check method directory has zero fixed/GT/patch/verifier/provenance-bearing files.

### Task 3: Requirement normalization, extraction and snapshots

**Interfaces:** `normalize_requirement(payload, public_observable=None) -> RequirementSpec`; `compute_snapshot(source_root) -> Snapshot`; `resolve_scope(hints, snapshot) -> ScopeResolution`; `extract_units(snapshot, scope) -> list[CandidateUnit]`. Every locator is relative to `Snapshot.source_root` and binds to `snapshot_id` plus `content_digest`.

**Files:** `src/contractedge/requirement/normalize.py`, `src/contractedge/source/snapshot.py`, `scopes.py`, `units.py`, syntax adapters under `retrieval/parsers/`, `tests/test_requirement.py`, `tests/test_source_scope.py`, `tests/test_parsers.py`.

- [ ] Test exact text preservation, negation/conditions, unknowns, `path:selector`, symbols, concepts, root-level and nested archives, and repeatable content snapshots.
- [ ] Add bounded Go/TypeScript/JavaScript/YAML/JSON/Helm extractors; unsupported syntax returns bounded text units with an explicit parser kind, never fabricated AST facts.
- [ ] Freeze normalized path, locator, content digest, search scope, and candidate inventory before model invocation.
- [ ] Run tests and compare exported source roots/hashes against the trusted preparation ledger.

### Task 4: Retrieval and candidate budgeting

**Interfaces:** `build_candidate_set(requirement, scope, units, edges, *, top_n, max_context_tokens, token_counter) -> CandidateSet`; `extend_once(candidate_set, evidence_gaps, snapshot) -> CandidateSet`. Syntactic and inferred edges use distinct `edge_kind` values.

**Files:** `src/contractedge/retrieval/candidates.py`, `rank.py`, `budget.py`, `tests/test_retrieval.py`.

- [ ] Test deduplication by snapshot/path/locator, direct one-hop only, scope-extension evidence, deterministic tie ordering, top-N 8/12/16, and explicit no-tokenizer failure.
- [ ] Implement matching and one-hop adjacency; label syntactic vs inferred edges and preserve selected/dropped candidates and ranking features.
- [ ] Enforce configured context-token budget via an injected model tokenizer; do not use provider defaults silently.
- [ ] Run deterministic retrieval tests on synthetic Go, JS/TS, YAML/JSON and Helm fixtures.

### Task 5: Requirement and Diagnosis calls

**Interfaces:** `ModelProvider.complete_json(request: ModelRequest) -> ModelResponse`; `build_diagnosis_request(method_input, requirement, candidates, run_context) -> ModelRequest`; `validate_diagnosis(payload, method_input, run_context, candidates) -> DiagnosisPacket`. Provider requests include model, prompt version, budget and retry limits; provider response metadata is retained in the trace.

**Files:** `src/contractedge/model/provider.py`, `diagnosis/prompt.py`, `diagnosis/parse.py`, `diagnosis/validate.py`, `tests/test_diagnosis.py`.

- [ ] Add fixture-model tests for evidence-only facts, unsupported causal claims, missing roles, same-file distinct roles, multiple violations, incomplete evidence and invalid references.
- [ ] Build provider protocol with explicit model/prompt/budget config and JSON-only response capture; keep deterministic fixture provider for unit tests.
- [ ] Implement diagnosis structural checks and statuses READY, PARTIAL_READY, EVIDENCE_GAP, NO_SUPPORTED_VIOLATION, INVALID_OUTPUT.
- [ ] Add semantic-review hook that records ACCEPT/REVISE/UNCERTAIN without representing model self-review as proof.
- [ ] Run tests and verify observed boundary and full Requirement horizon are recorded separately.

### Task 6: Semantic ChangeSet and consumer boundary

**Interfaces:** `validate_semantic_changeset(payload, diagnosis, snapshot) -> ValidationResult`; `translate_for_topomen(changeset, consumer_version) -> TopoMenPlan`. The canonical ContractEdge schema is stable even if a consumer-specific translation is later added.

**Files:** `src/contractedge/changeset/plan.py`, `validate.py`, `topomen.py`, `tests/test_changeset.py`, `tests/test_topomen_contract.py`.

- [ ] Test MODIFY/CREATE/REMOVE, missing field inside EXISTING, justified NEW file target, dependency cycles, conflicting creates, unsupported causes, preserved outcomes, open obligations, and `NEEDS_EVIDENCE`.
- [ ] Implement coverage rules: every change links a supported planned violation, every planned violation is covered, each outcome is repaired/preserved/open, and all target digests match current snapshot.
- [ ] Implement a versioned canonical TopoMen interchange adapter behind an interface; inspect real TopoMen source/consumer contract before claiming compatibility. Keep the pilot acceptance marked BLOCKED until integration tests pass against that consumer.

### Task 7: Finite orchestration, private evaluation and pilot harness

**Interfaces:** `run_front_half(method_input, workspace, provider, config) -> RunTrace`; `normalize_gt(private_case, release_id) -> NormalizedGroundTruth`; `score_run(run_trace, normalized_gt) -> EvaluationRecord`. The public runner API cannot accept a private-root path.

**Files:** `src/contractedge/orchestration/runner.py`, `trace.py`, `evaluation/gt_adapter.py`, `metrics.py`, `cli.py`, `tests/test_orchestration.py`, `tests/test_evaluation_isolation.py`.

- [ ] Test at most two semantic Diagnosis calls, one evidence extension, two ChangeSet calls, bounded format/transport retries, BUDGET_EXHAUSTED, stale snapshot rejection, and SEMANTIC_FAIL despite wrapper exit 0.
- [ ] Normalize R1/R2 GT overlays and R5 nested/top-level relations in evaluator-only code; preserve `oracle_limit`, recall eligibility and ambiguity rather than coercing missing fields to true.
- [ ] Ensure method CLI has no evaluator imports and cannot traverse to private root; record environment, failure kind, denominator and all exclusions.
- [ ] Build a frozen ~20-case pilot manifest spanning R1/R2/R5, existing/new targets, partial/full evidence and distinct observed depths. Run only when model configuration and actual TopoMen consumer are present; otherwise emit an explicit blocked report, not a mock success.

### Task 8: End-to-end acceptance and evidence bundle

**Interfaces:** `audit_prepared_release(report) -> AuditResult`; `run_pilot(manifest, config) -> PilotReport`; `audit_topomen_acceptance(plan, consumer) -> ConsumerAcceptance`. A missing consumer or model backend yields a named blocked gate, never a synthetic pass.

**Files:** `docs/reports/data-adaptation-audit.json`, `docs/reports/pilot-report.json`, `docs/reports/limitations.md`, command wrappers.

- [ ] Run all unit tests and CLI smoke tests on synthetic fixtures.
- [ ] Run data preparation in a fresh output root and verify hashes, no leakage, and all 117 release members are accounted for as prepared/held/unusable with reasons.
- [ ] Validate every pilot plan against the actual TopoMen consumer once available; retain plan IDs, schema/consumer version, acceptance result and oracle boundary.
- [ ] Report pre-pilot engineering gates separately from live pilot and behavioral repair results; never label the system complete on schema checks alone.

## Handoff assumptions

- Direct native execution by the primary agent is selected because module contracts are sequential and no system repository exists.
- The v1.1 text supplied by the user is the governing design, and the request authorizes implementation against it.
- A working model endpoint and the real TopoMen consumer location/interface are not currently discoverable in this server workspace; implement their ports and report these external integration gates accurately.
