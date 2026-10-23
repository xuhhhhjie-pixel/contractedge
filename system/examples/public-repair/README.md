# Public repair examples

These are tiny authored Python/YAML tasks with published runtime inputs and
controls. They load no dataset, scoring fixture or private evaluator. The default
provider is deterministic and makes no network calls. The examples run the
production diagnosis, semantic plan validators, file-edit consumer, budgets,
public review and feedback.

From `system/` after installing `requirements-dev.lock`:

```sh
python scripts/run_public_demo.py --list
python scripts/run_public_demo.py --case config-binding --output /tmp/ce-config-new
python scripts/run_public_demo.py --case reload-state --output /tmp/ce-reload-new
python scripts/run_public_demo.py --case two-consumers --output /tmp/ce-two-new
python scripts/run_public_demo.py --case config-binding --save-ready --output /tmp/ce-ready-new
python scripts/run_public_demo.py --resume-ready /tmp/ce-ready-new --output /tmp/ce-continued-new
```

Every output must be new. `config-binding` uses the actual vendored TopoMen
`DefaultAgent` file-edit lifecycle; the other two use the direct consumer. No
Docker daemon is required. `two-consumers` intentionally fixes one consumer on
round one. Fresh public checks fail the selected obligation, pass preservation
controls, and feed an EXECUTE retry that repairs the second consumer.

The observer runs host Python with `-I`, an explicit environment allowlist and the
candidate source as its working directory. API credentials and user Python
startup paths are not inherited. This is **not an OS filesystem/network sandbox**;
its public capability record states that boundary. Only synthetic-provider
execution is admitted. A non-synthetic provider may use `--save-ready` to diagnose
and plan against the trusted original examples; direct execution and continuation
of non-synthetic READY plans fail before provider construction/dispatch. Executing
live model-produced code requires a future sandbox observer.

Inspect `summary.json`, `run.config.json`, `run.freeze.json`, `frozen-public/`, and
`repair/{model_calls.json,candidate.patch,repair.trace.json}`. Every round retains
exact requests/responses, plan, actual edits, subprocess stdout/stderr/process
record, public observations, review and feedback. `combined.model_calls.json`
contains all dispatches including the resumed READY ledger. The repair loop's own
`repair/model_calls.json` contains only calls dispatched by that invocation. The
child exit status records successful collection; JSON checks independently carry
behavioral PASS/FAIL. Source/config/runtime-input identities and method/runtime
versions are frozen and checked before continuation.

READY is a deliberate **single-use** handoff, not arbitrary crash recovery. The
original complete ledger remains unchanged, is copied to
`initial.model_calls.json`, and is referenced by `continuation.json`. An exclusive,
durable `continuation.claim.json` is created on the saved checkpoint before any
new provider dispatch and binds its sole continuation output. A concurrent or
later second continuation is rejected after either success, failure, or crash;
inspect the first attempt's ledger. Calls, token reservations and elapsed time
are inherited. Unknown or incomplete dispatches are never automatically resent.
There is no automatic claim reset. Original ledgers and failed attempts remain
available for explicit accounting.

`--provider-config provider.json --save-ready` explicitly opts into the existing
provider factory for later authorized API planning. Credentials belong only in
the referenced environment variable. Config must be a JSON object; credential
fields/headers and endpoint userinfo/query/fragment are rejected before config
persistence. API config must explicitly supply token/time/call/round budgets and
a nonempty `money_budget` object with `path`, `limit_cny`, `model`,
`input_cny_per_million`, and `output_cny_per_million`. `MoneyBudget` validates the
amounts. Its path is made absolute and its existing bytes are bound to READY;
missing/changed ledgers never create fresh allowances. Config/provider/budgets
cannot be overridden on continuation.

For DeepSeek use `deepseek-flash`, `https://api.deepseek.com`,
`output_mode: json_object`, `supports_json_schema: false`, and
`schema_in_prompt: true`; this does not declare strict JSON Schema support.
Supply a suitable tokenizer. Public examples establish no benchmark effectiveness
or hidden PASS, and live planning does not demonstrate a complete live repair.
