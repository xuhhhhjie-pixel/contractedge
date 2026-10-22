# Source consumer operation capabilities (2026-10-23)

The planner previously advertised REMOVE and accepted it as a semantic plan, but
the current source consumer intentionally rejects whole-file deletion. The
actual backend capability is now bound before planning, reflected in the final
model schema and prompt, and checked before READY. Its feedback uses the existing
single correction allowance. Both direct and topomen_agent use the same bounded
source consumer; unknown explicit backends/consumers and capability expansion fail.
Configuration may narrow the supported set. General wire syntax still permits REMOVE.

Consumer limits are retained during task projection, execution, resumed trace
validation and plan revision. The consumer retains its final rejection. Prompt
versions are changeset.v1.1.10/.11. This is a new development derivative, identified
by the adjacent source/configuration/panel snapshot, not the original 0.6.27 method.
The original package version, publication manifest and freeze remain historical.

Verification uses generated public fixtures and zero real model HTTP calls:

- Four old-pipeline assertions failed before the implementation.
- 82 related tests pass, including operation violations, finite corrections,
  budgets, legal CREATE/MODIFY, configuration narrowing after task projection,
  resumed capability mismatch, source permissions, snapshots and repair feedback.
- The portable subset reports 646 passed, 17 subtests passed and one unchanged
  Docker --cpus=2 startup failure. Six historical/native modules remain excluded.
- A separate public fixture captured six serialized requests across diagnosis,
  planning, execution request construction, re-diagnosis, planning and execution
  request construction. Snapshot lineage changed while requirement identity and
  supported operations were preserved. Its executor applies literal fixture edits;
  this is not a real-model repair or independent effectiveness score.

Run the fixed test paths recorded in the adjacent snapshot with PYTHONPATH=$PWD/src
and CONTRACTEDGE_TOPOMEN_SOURCE=$PWD/vendor/topomen. Then run
python scripts/test_portable.py. On the current lab daemon DOCKER_API_VERSION=1.43
is task-local; CPU quota remains unavailable. PR #1 independently fixes the test
entry default; this branch remains based on main and uses the explicit override.

Original failed outputs and both portable test attempts are retained in the
collaboration evidence directory. The first expanded run also exposed a test
fixture matching the old prompt version; its stage dispatch was updated while
retaining the assertion that exactly one correction occurs. formal_ready=false.
ACK root cause, provider policies and real container readiness are separate work.
