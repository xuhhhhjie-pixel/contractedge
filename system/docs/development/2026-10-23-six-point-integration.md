# Integrated development checkpoint (2026-10-23)

This branch combines the five independent development PRs for portable entry,
executor operation capabilities, provider output/identity, actual Docker admission
and public front-failure replay. It is a development checkpoint; real ACK
diagnosis/repair remains blocked by its absent original public package.

The only cherry-pick conflict was adjacent imports in repair.py; both the actual
consumer capability resolver and runtime-preflight imports were retained. The
remaining source changes applied without conflict. Individual stage snapshots
describe their original independent commits, not this combined source. Use the
adjacent integration snapshot for the current source/configuration/fixed panel.

Validation in an independent installed Python environment, explicitly loading
this worktree's src and vendored TopoMen:

- related: 119 passed in 9.61s
- portable: 730 passed, 17 subtests passed in 69.23s (0:01:09)
- native: 9 passed in 2.88s

The portable subset excludes the six documented historical/native modules;
the native fixture module is reported separately. This is not a full historical
suite reproduction. The original temporal resource test runs within the portable
subset under its unchanged limits. Actual runtime admission also passes with
2 CPUs and 2 GiB. A freshly captured five-request synthetic failure package
replays the same INVALID_PLAN result against this combined method hash. It does
not reuse the older stage's tape against changed code.

The independent transient daemon has separate socket/data/config/state paths;
the existing Docker daemon and global cgroups are untouched. Task endpoint/API
settings are scoped to this validation. Runtime lifecycle/ownership evidence is
preserved in the collaboration evidence directory. See the runtime-preflight
note for the supported environment recipe and explicit repair admission profile.

The output/identity tests use mock HTTP, and front/executor fixtures use saved or
synthetic responses. No real model HTTP call, dataset download, hidden evaluation,
formal experiment or merge to main occurred. Original dirty files, 0.6.27 freeze
and failed attempts remain; formal_ready=false. Model endpoint Schema support,
backend weights and research effectiveness are not established by these checks.

Next required external input: the authorized original ACK public model_calls,
MethodInput, front trace, Broken source, observations, effective configuration
and usage. Use its original method freeze for replay. Do not manufacture missing
parameters or infer the earliest cause from the published final invalid plan.
