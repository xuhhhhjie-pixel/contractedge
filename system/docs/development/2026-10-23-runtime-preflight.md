# Task-scoped Docker admission (2026-10-23)

`runtime-check` negotiates a common Docker API without an inherited API override,
then uses that API for the check. An explicit DOCKER_HOST remains the endpoint;
an explicit context takes precedence. No Docker configuration is rewritten.
CPU quota/period and memory capability reports are necessary but insufficient.
PASS also requires an actual digest-pinned cached container to observe exactly
the requested cgroup CPU and memory limits. No image is pulled by preflight.
The probe uses no network, a read-only filesystem, the caller UID/GID and a PID
limit. Timeout cleanup targets only its UUID name with a matching ownership label.

From a supported Linux development environment with a trusted cached image:

```bash
cd system
# Use your existing supported endpoint. A context overrides DOCKER_HOST.
unset DOCKER_CONTEXT
export DOCKER_HOST=unix:///absolute/path/to/your-owned/docker.sock
export DOCKER_CONFIG=/absolute/path/to/an-empty-client-config-directory
export PYTHONPATH="$PWD/src:$PWD"
export CONTRACTEDGE_TOPOMEN_SOURCE="$PWD/vendor/topomen"
IMAGE=golang:1.26.8-bookworm@sha256:a688600ca24f8a4d3ca77f95b0dd40704a9fc787c826660eb7ba0b641b8b175d
python scripts/contractedge_local.py runtime-check --image "$IMAGE" --cpus 2 --memory-mib 2048
# This shell override is needed by legacy test helpers, which do not invoke admission.
export DOCKER_API_VERSION=1.43 # choose the negotiated version reported above
python -m pytest -q tests/test_temporal_response_bounds.py
python scripts/test_portable.py
```

Without `--image`, CAPABILITY_CHECK_ONLY returns nonzero; it is never runtime
PASS. Failure messages distinguish API negotiation, missing resource controls,
permissions, image/startup failures and ineffective limits. Supplying an image
does not prove every task-specific image or workload will work.

Merge configs/runtime-preflight.example.json into an existing repair profile
to opt in. Admission runs before workspace preparation/model calls and persists
runtime-preflight.json. Both status=PASS and runtime_verified=true are required.
Prior calls/cost are accounted before a failed resume gate, and preflight time
uses the original wall budget. No profile preserves legacy startup behavior.
Legacy public helpers inherit environment: a process-local reentrant lock
serializes repair_case calls and scoped API/context overrides restore on return
or error. This does not promise thread safety for unrelated external subprocess
launchers or processes mutating os.environ outside this wrapper.

Lab validation used an independent transient rootless Docker daemon with its
own socket, data/config/exec/PID/RootlessKit paths, systemd user unit and empty
client auth config. Existing user CPU delegation was already available; the old
daemon reported no CPU controls despite it. No global cgroup or original daemon
configuration was changed or restarted. The original docker.service PID stayed
1193153. No autostart was installed. After remaining integration checks, inspect
the recorded ownership file and stop only the task-owned transient unit; retain
its data and evidence. Never stop an unrelated docker.service for this recipe.

Actual limits: cpu.max=200000 100000 (2 CPUs), memory.max=2147483648.
The unchanged temporal test passes (1 test, 46.79 seconds); the development
subset passes 655 tests plus 17 subtests. Six historical/native modules remain
excluded; this is not a full historical suite reproduction. Related admission,
CLI and budget regressions pass 49 tests. The original daemon run still has the
original CPU-limit failure (654 passes plus 17 subtests); its failed evidence is
preserved. No real model HTTP requests or research-effectiveness tests occurred.

The adjacent snapshot freezes this development source/configuration/panel.
Original 0.6.27 metadata and formal_ready=false remain. This branch is independent
of the other development PRs; combined validation is recorded separately.
