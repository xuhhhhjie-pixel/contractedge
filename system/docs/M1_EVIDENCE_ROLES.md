# ContractEdge M1 for method experiments

The method profile `configs/m1-evidence-roles.json` selects B′ with exact,
source-bound citation location. `configs/m1-ordinary-control.json` selects A′
with the same protocol and mechanical checks. The prompts are packaged
unchanged from the eight-case experiment at
`dca4458db4be3ed147f6261ae2d2d480a168d443`. The locator comes from
`a71337b8adbea3f56ea19a00b6ea657bc025cbe1`. This stage runs before any
downstream method. Its handoff keeps original requirement, role, applicability,
relation, citations and clear/pending status. The mechanical record keeps
original and resolved coordinates. Pending items are uncertainties.

Run from `system/` after installing the project and the existing
`common-live` optional dependency for Live.

## Ordinary Live

The disabled profile delegates to the existing common Live entry and its
original, synthetic-local profile. This is the same profile and entry as
`contractedge-common-live --profile configs/common-live-local-20261127.json`.

```bash
python -m contractedge.experiments.m1 live --config configs/m1-live-baseline.json \
  --public "$PUBLIC_DIR" --tools "$PUBLISHED_TOOLS" \
  --live-yaml tests/graph_repair/livesweagent.fixture.yaml \
  --endpoint "$LOOPBACK_ENDPOINT" --out "$FRESH_RUN"
```

## Method experiment

For a new task, supply a reviewed, frozen JSON packet of public documents.
Each document includes `id`, `first_line`, and `text`. The packet comes from
public task material, approved interfaces and allowed Broken excerpts.

```bash
python -m contractedge.experiments.m1 prepare --config configs/m1-evidence-roles.json \
  --packet "$PUBLIC_PACKET" --packet-sha256 "$PUBLIC_SHA256" > "$REQUEST_FILE"
python -m contractedge.experiments.m1 run --config configs/m1-evidence-roles.json \
  --packet "$PUBLIC_PACKET" --packet-sha256 "$PUBLIC_SHA256" \
  --fixture-origin "$FIXTURE_HTTP_ORIGIN" --out "$FRESH_M1_RUN"
```

The fixture origin is an HTTP loopback origin. Request preparation performs no
network call. For a separately approved future online call, replace
`--fixture-origin` with `--authorization "$ATTEMPT_AUTHORIZATION"`. The
authorization contains schema `contractedge.m1_single_request.v1`, status
`AUTHORIZED`, `request_sha256`, the absolute `attempt_directory`, the
approved existing absolute cumulative `ledger` path, `ledger_sha256`, and `authorized_limit_cny`.
Use the reconciled budget and unchanged ledger identity. The call uses the
qualified official endpoint and the key supplied in the environment. The
attempt directory must be fresh. Each invocation admits at most one call and
preserves a technical failure. The result and budget ledgers are in the
attempt directory and approved cumulative ledger respectively.

Use `handoff.json` for the next method stage. For M4 comparisons on sealed
A′/B′ outputs, use `replay` with the matching packet, its hash and the saved
`response.json`. Replay makes no generation request.

## Observed evidence

In eight development cases, unsupported clear claims were 13/50 under A′ and
3/42 under B′. Supported clear claims were 36 in each group and complete
reference units were 23/27 and 26/27 respectively. The reference and evaluation
were Codex-assisted. Unsupported requirements, incomplete applicability, and
uncertainty still occurred. The subsequent M4 experiment did not show that
the improvement transferred to generated checks. Repair success remains
unmeasured by these M1 tests. Resolve the integration identity with
`git rev-parse HEAD` and the imported module with
`python -c 'from contractedge.experiments import m1; print(m1.__file__)'`.
