# Public repair examples

These are tiny authored Python/YAML tasks, with published runtime inputs and controls.
They load no dataset, scoring fixture or private evaluator. The default provider is
deterministic and makes no network calls. They still run the production diagnosis,
semantic plan validators, file-edit consumer, budgets, public review and feedback.

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
Docker daemon is required. `two-consumers` intentionally fixes only one consumer
on round one. Fresh public checks fail the selected obligation, pass preservation
controls, and feed an EXECUTE retry that repairs the second consumer.

Inspect `summary.json`, `run.config.json`, `run.freeze.json`, `frozen-public/`, and
`repair/{model_calls.json,candidate.patch,repair.trace.json}`. Each round retains
exact requests/responses, plan, actual edits, subprocess stdout/stderr/process
record, public observations, review and feedback. `combined.model_calls.json`
contains every dispatch including a resumed READY ledger. The repair loop's own
`repair/model_calls.json` contains only calls dispatched by that invocation.
The child observer's exit status records whether collection ran successfully;
each JSON check independently records behavioral PASS/FAIL.

READY continuation is a deliberate checkpoint, not arbitrary crash recovery.
The original complete ledger is hash-bound, copied as `initial.model_calls.json`
and referenced by `continuation.json`; calls, token reservations and elapsed time
are inherited. Source/config/runtime-input identity drift is rejected. Unknown
or incomplete dispatched calls are never automatically resent. Use a new run
after an arbitrary interrupted dispatch and account for it separately.

`--provider-config provider.json` explicitly opts into the existing provider
factory for later authorized API work. Put credentials only in the referenced
environment variable, never the JSON. Config and budgets are frozen at READY and
cannot be overridden on continuation. The money ledger path is made absolute;
continuation requires its original existing bytes and never creates a fresh
allowance when that ledger is missing or changed. API config must explicitly
supply token/time/call/round/money budgets. For DeepSeek use `deepseek-flash`,
`https://api.deepseek.com`, `output_mode: json_object`,
`supports_json_schema: false`, and `schema_in_prompt: true`; this does not declare
strict JSON Schema support. Supply an appropriate tokenizer and an explicit
model/token/time/call/money budget. Public examples are developer checks and
establish no benchmark effectiveness or hidden PASS.
