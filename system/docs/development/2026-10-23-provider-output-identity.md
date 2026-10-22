# Model output transport and reported identity (2026-10-23)

OpenAI-compatible ports default to json_object, preserving unknown endpoint
compatibility. The application still validates the complete semantic contract.
json_schema is opt-in and requires supports_json_schema=true as a declared
endpoint capability. This declaration is not a live verification of support.

Strict transport compiles the closed inline model schema without mutating it:
optional properties become required/nullable on the wire. Only transport-added
null values are removed on return; originally nullable values remain. The raw
response is retained in metadata. Invalid JSON/schema output is not repaired or
accepted by this adapter: it remains available to the application validation and
original bounded correction flow. Root unions, open objects and unhandled
references/compositions fail explicitly, rather than silently weaken contracts.
The existing model schema lowering remains a separate, potentially weaker layer.

Set schema_fallback=json_object only to allow a logged preflight downgrade when
support is undeclared or the inline transport projection is unsupported. HTTP
rejection never triggers a hidden second request. With schema_in_prompt=false,
the CLI requires a schema-constrained provider and no downgrade path. Serialized
schema overhead is included in shared token and front-half context reservations;
the configured physical model context is also checked before OpenAI HTTP.

Both OpenAI-compatible and Ollama responses record requested_model,
reported_model, model_identity_policy and MATCH/ALIAS/MISMATCH/UNREPORTED status.
Missing names stay null, not an invented echo. record is the compatibility
policy; require_match rejects unlisted/missing names and prevents later HTTP on
that provider instance. Aliases are explicit per requested model, not prefix
matches. Self-reported names cannot prove backend weights.

Rejected received responses, usage and error metadata survive both durable
BudgetedProvider callbacks and front-half failure traces. Money usage settles at
the pinned rate only for matching/registered names; unverified identities keep
the entire conservative reservation, with reported usage still in call metadata.
The normal settlement HTTP fixture now reports its requested model; its original
exact-cost assertion remains. Unknown identity accounting has separate coverage.
Preparation failures do not leave unrecorded token reservations; deadlines are
rechecked after metadata preparation before transport. Ollama keeps its native
format and gains the same identity policy/metadata.

Configuration is illustrated in configs/provider-contracts.example.json. Replace
the reserved example endpoint/model deliberately; no credentials belong in Git.
Strict mode additionally needs output_mode=json_schema and declared support.
The example is an adapter profile, not a runnable paid experiment registration.

Validation: 67 related tests pass; the portable development subset reports
660 passed, 17 subtests passed and one unchanged Docker CPU-quota startup failure.
Six historical/native modules remain excluded. A mocked HTTP transport exercised
the actual diagnosis and planning schemas with schema_in_prompt=false through
READY, and identity errors through persisted usage/response records. The first
wire-fixture test needed conversion from legacy canonical coverage to the current
model-facing representation; its original failure is retained. These are public
fixtures, not real model calls or repair-effectiveness results.

The adjacent snapshot records the new source/configuration/fixed-panel identity.
It is independent of PR #2 and retains the original 0.6.27 freeze and
formal_ready=false. API references used to verify the wire shape:
https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create
https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=chat
