# C2 persistent trials and independent repair consumers

Stacked on C1/CLI PR #11, base `2bb059bcae111bd4098d9562b7b11dfb889190bf`.
The current frozen snapshot and validation JSON define the exact version.
`formal_ready=false`; no formal150, private grading or real model requests are
part of this engineering stage.

## Candidate effects and terminal recovery

TrialSession persists accepted authority, edit intent/completion, observation,
selection intent/completion and rollback facts. Recovery validates original
input, method/prompt/config/mode/consumer, source paths and the original ledger.
It does not resume the model loop or reset call/token/time/money allowances.
Unknown requests retain reservations and are never resent.

Incomplete trials restore their incumbent; a complete verified selection can
finish idempotently. A rollback failure cannot be overridden by selection
intent. Literal staging and backups stay outside the source snapshot in owned
transaction storage, so a hard exit during staging does not masquerade as an
external source edit. Real external modifications remain a rejection.

Rejected old observations retain their original payload identity and explicit
OBSERVATION_REJECTED event. They use the existing no-dispatch UNKNOWN review
and candidate guard rather than claiming a successful observation effect.

## Repair consumers

The engine consumes RepairIntent, ExecutionInput.target_paths and the common
RepairConsumer protocol. TopoMen translation, acceptance and execution remain
inside its adapter. MODIFY-only uses an independent replacement format; both
share literal permissions, snapshot validation, trial and budget boundaries.
Persisted raw mappings never grant authority: the same consumer reaccepts the
original context/policy/plan and compares the original digests before consuming
a durable response. Supported direct/MODIFY paths consume it at most once;
native DefaultAgent explicitly refuses response replay.

## Reproduce actual developer isolation

Run the normal development snapshot verifier first, using the current tree and
vendored TopoMen. Cache the frozen trusted runtime image and provide your owned
Docker endpoint; RuntimeSession never falls back to host execution.

```bash
python -B scripts/run_trial_isolation_panel.py --system "$PWD" \
  --binding configs/trial-isolation-acceptance-r2.example.json \
  --output /tmp/contractedge-trial-panel-new \
  --image "$CONTRACTEDGE_RUNTIME_IMAGE" --docker-host "$DOCKER_HOST"
```

The harness runs two consumers across normal, after-edit, after-observation and
before-selection boundaries, checking real candidate/observer receipts,
resource/mount identities, confirmed cleanup, absence of owned containers,
original calls/reservations and repeated recovery coherence. Complete trusted
public behavior checks aggregate locally without a fourth model call.
Hard-exit recovery retains the Broken incumbent and is not a repaired-target
PASS. Fixed exposed developer predicates are not generalization evidence.

The stage CI includes focused recovery/consumer tests and actual isolated
trial acceptance. The portable subset retains six explicit historical/native
exclusions; native and actual runtime evidence have separate scopes. D2 mode
generation and the six-case/24-cell final comparison remain the next stage.

The r2 source also preserves the current actual provider error if ledger sealing
fails during its settlement callback. Predispatch sealing and independent
sealing failures still block; an older journal error is never used as authority
to suppress a new recording failure. The original r1 freeze and evidence remain.
