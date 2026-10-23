# Public failure export and front-half replay (2026-10-23)

`python scripts/public_failure.py export/check/replay` is a developer entry point.
It selects explicitly listed files from one authorized run root and one public
source root. It never discovers run directories or copies the whole run root.
The package is a directory of existing JSON roles, an explicit Broken file list
and a hash manifest. No dataset, executable method, virtual environment, cache,
credential, GT/Fixed or hidden grader belongs in the package.

The current supported scope is `front_semantic_failure`, with the optional
semantic reviewer disabled. It replays completed saved model responses through
the actual front pipeline and application validators. It requires every
provider-boundary ModelRequest parameter to match exactly: model, prompt version,
messages, schema, metadata, output limit, temperature and thinking. A changed
request stops at its first divergent index; no response is consumed for it.
Every selected call must be consumed, and terminal status, errors, diagnosis,
changeset and structural validation must match the saved trace.

This is not HTTP wire, transport-identity-error, latency, external execution or
full repair replay. A successful replay of an invalid plan means the same
failure was reproduced; it is not a successful patch or evidence of improved
repair effectiveness. Provider transport transformations are outside this scope.
The package hashes detect accidental corruption and bind selected identities;
they do not authenticate an untrusted publisher.

## Existing run artifacts and exact parameters

New BudgetedProvider records retain `request` with all ModelRequest fields in
the existing durable model_calls ledger, alongside usage, responses and existing
reservations. No new model call or allowance is added. Old ledgers are still
readable by the system but cannot certify fields that were never saved. This
tool reports missing parameters and never fills them from guessed defaults.

New full front invocations within repair also persist the effective
`front.config.json`, `front.public_observations.json`,
`front.execution_feedback.json` and `front.replay.json` next to `front.trace.json`.
The last sidecar supplies the half-open index range in the original whole-run
ledger. The whole ledger is preserved; the recipe selects the front call range.
Reused traces and plan-only revisions do not fabricate a new full front record.
Effective front config nests `model` as a mapping; the outer repair profile has
a model string and is not an interchangeable replay input.

## Export, check and replay

Start from `configs/public-failure-recipe.example.json`; replace placeholders
with the original attempt identities and its explicit public file inventory.
The input digest is the canonical MethodInput digest; the Broken identity is
the complete explicit source file map digest. Missing files yield INCOMPLETE;
wrong hashes, mixed run/snapshot/input identities or inconsistent ledgers yield
INVALID. Unknown artifact roles, traversal, symlinks, non-public path classes,
credential fields and oversized packages are rejected. Content checks complement
an explicit public selection; they cannot certify arbitrary source text as public.

```bash
# From a clean installed development environment; no API key is used.
cd system
python scripts/public_failure.py export \
  --run-root /absolute/path/to/authorized/case-run \
  --source-root /absolute/path/to/that-attempt-public-broken-source \
  --recipe /absolute/path/to/reviewed-recipe.json \
  --output /absolute/path/to/new-independent-package
python scripts/public_failure.py check --package /absolute/path/to/package
python scripts/public_failure.py replay --package /absolute/path/to/package \
  --method-root /absolute/path/to/trusted-frozen-system \
  --expected-method-sha256 ORIGINAL_ATTEMPT_RUNTIME_SOURCE_SHA256
```

The original method must be supplied independently and match both the caller's
expected hash and the package identity. Replay launches an isolated fresh Python
process, loads only that method's `src`, uses a saved-response provider, denies
network connections and subprocess launches, and checks identities again after
the run. Temporary source reconstruction preserves a relative MethodInput
source_root without changing its digest. These guards are for trusted frozen
methods; this tool is not an OS security sandbox for hostile Python code.

Tokenizer is explicit: `utf8_byte_bound`, the synthetic-only
`whitespace_fixture`, or `qwen35_local` using the method's pinned local tokenizer.
The latter needs its already installed dependency/cache; there is no download
fallback. Missing tokenizer assets are an error. An original attempt requiring
another tokenizer, reviewer, incomplete response, budget-wrapper overhead replay
or executable public runner is outside the current scope and needs an explicit
future adapter, rather than a silent approximation.

## Fixed development fixture and ACK boundary

The fixed synthetic test uses the real front pipeline, one bounded evidence
extension and the existing correction allowance. Five captured requests end in
INVALID_PLAN with PRESERVATION_IMPACT_INVALID for NEEDS_EVIDENCE, empty changes
and CHANGES/empty change_refs. The exported package replays that failure exactly.
Tests also cover missing roles/legacy parameters, changed final requests,
source/input/run/method mismatches, relative source roots, explicit ledger ranges,
non-public selection, credentials, symlinks and unregistered extra package files.

The published ACK final plan has the same local invalid-impact shape, but its
complete original requests, MethodInput, front trace, source and observations are
absent from the authorized collaboration checkout. Local schema/field inspection
does not identify the earliest causal failure. No ACK request, response,
Diagnosis or source has been reconstructed from summaries. Real ACK diagnosis
and any evidence-based repair remain pending its original public package.
Original ACK 0.6.27 must use its bound frozen method source
`362780da39f585408b4b725a6cd72f08c6d66caaf8ef52b27d0086d466e80d22`,
not the new development source. The original freeze and failed attempts remain;
formal_ready=false. No new real model HTTP calls or formal experiments occur.

Development validation counts and exact source/configuration/panel identities
are recorded in the adjacent snapshot. The portable suite still excludes the
six documented historical/native modules. See the portable-entry PR for clean
installation/vendor setup and the runtime-preflight PR for actual container
limits. These independent branches are combined and tested separately.
