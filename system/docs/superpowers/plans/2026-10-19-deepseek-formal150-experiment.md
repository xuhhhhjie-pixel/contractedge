# Formal150 real-model experiment

User authorizes ChatAnywhere https://api.chatanywhere.tech/v1, deepseek-v4.1-flash;
2026-10-19 user confirms total API hard cap CNY20 across probes, retries and batch.
Use current frozen ContractEdge-benchmark150-v1-20261019 only. Historical runs are reference.
Credentials reside outside repository in a mode0600 file and never enter traces/configs.

Execution design: existing repair loop and audited actual TopoMen DefaultAgent remain the method.
Add a persistent process-safe monetary reservation ledger to the API port; reserve before HTTP,
settle using provider usage at conservative undiscounted peak rates, retain reservations on
ambiguous failures/missing usage/crash. Never reset the ledger across experiments. No automatic
HTTP retries. Use UTF8 bytes as a conservative token bound, explicitly not a DeepSeek tokenizer.
Keep complete JSON schemas in prompt for the compatible JSON-object provider.

1. Reproduce/fix DECLARED_ARTIFACT binder/schema incompatibility without paid retries. DONE;
   actual diagnosis contract regression and full181 tests pass.
2. TDD persistent CNY cap, integrate existing API provider, verify official relay prices/model.
3. Cheap authenticated JSON generation probe, then genuine development case repair through
   diagnosis/planning/native TopoMen/patch/public run/review, separately score selected patch.
4. Add resumable all150 batch command with fixed manifest identity, no duplicate paid work,
   isolated public case execution and independent scoring. Validate execution-family coverage,
   failure accounting, public/private boundary and resource limits.
5. Run real cases/batch within remaining shared cap; inspect failures based only on public evidence.
6. Audit framework0–10 and formal150 readiness with reproducible commands, exact traces/cost
   and explicit completion limitations. Do not equate one smoke pass with150 successes.

Price evidence: public https://chatanywhere.tech/api/public/models/prices captured externally
2026-10-19 shows base prompt1e-6/completion4e-6 CNY per token, peak2e-6/8e-6,
paid-group ratio0.8. Reserve and account at undiscounted peak CNY2/8 per million,
ignoring cache/time discounts. This ledger is an upper bound, not an exact billing receipt.
