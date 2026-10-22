# TopoMen v1.1.2

TopoMen is a research project for AI-assisted environment deployment. It studies the path:

```text
repair memory -> AI topology deployment -> collaborative correction
```

The project evolved from an earlier prototype, but the current package and command surface are `TopoMen` / `topomen`. Experiments validate the research steps; they do not define the project structure.

Current focus: topology is being promoted from an explanatory artifact into an execution plan for Docker deployment. The conservative threaded merge path remains the default, while the opt-in `topology_dag` scheduler records ready queues, resource waits, critical path timing, per-node cache metrics and local repair/reuse boundaries.

## Structure

- `src/topomen/core/`: protocols, config loading, paths, logging, serialization, exceptions.
- `src/topomen/llm/`: LiteLLM adapters, deterministic test models, model utilities.
- `src/topomen/environment/`: local command execution environment.
- `src/topomen/memory/`: repair memory, pattern memory, repair records, retrieval and pattern mining.
- `src/topomen/topology/`: project analysis, dependency evidence, topology DAG, Dockerfile generation, failure attribution, scheduler.
- `src/topomen/collaboration/`: agent orchestration, repair-memory agent, topology guardrails, scheduler prevalidation, verifier/critic checks.
- `src/topomen/experiments/`: batch experiments, cumulative experiments, GitHub sample fetch, trace analysis.
- `src/topomen/cli/`: thin command wrappers.
- `experiments/`: fixtures, manifests, memory seeds, compact retained results and traces.
- `docs/project/`: project overview, research progress and research logs for Codex handoff.

## Current Status

- Runtime Validation Planner and Scheduler V2 are implemented with state transitions, checkpoints, rollback and trace summaries.
- `topology-threaded-merge-*` groups build topology branches under TopoMen control and merge them into one final public Docker image.
- `topology_dag` is available as an explicit scheduler strategy for topology-based resource/cache/repair metrics; default threaded merge configs still use `legacy_threaded`.
- Latest local verification: `python -m pytest -q tests` passed with `168 passed`; `ruff check src/topomen tests` passed.
- Real Docker validation for `topology_dag` is handed off in `.claude/handoffs/2026-05-05_topology-dag-scheduler-real-docker-verify_handoff.md`.

## Commands

```powershell
topomen-repair-experiment experiments/manifests/manifest_topology_memory_suite.txt `
  --groups topology-scheduler-guarded-nomem,topology-scheduler-guarded-memory `
  --retrieval-memory-path experiments/memory/repair_memory_topology_suite_seed.jsonl
```

```powershell
topomen-cumulative-experiment experiments/manifests/manifest_topology_memory_suite.txt
```

```powershell
topomen-fetch-github --limit 3 --destination experiments/projects/github --manifest-path experiments/manifests/github_small_projects.json
```

```powershell
topomen-trace "experiments/results/traces/*_trace.jsonl" --report-path experiments/results/traces/trace_report.md
```

## Install

For China mainland network conditions, use a domestic PyPI mirror if the default index is slow:

```powershell
python -m pip install -e ".[dev]" -i https://pypi.tuna.tsinghua.edu.cn/simple
```

If the mirror lags or dependency resolution fails, retry with the official PyPI index or your proxy.

## Local LLM Configuration

TopoMen loads its local runtime environment from:

```text
C:\Users\Wen\AppData\Local\TopoMen\.env
```

This file is machine-local and must not be committed. The current setup uses a ChatAnywhere OpenAI-compatible relay for domestic access:

```dotenv
TOPOMEN_MODEL_NAME='openai/gpt-5.4'
OPENAI_API_KEY='<your-chatanywhere-api-key>'
OPENAI_API_BASE='https://api.chatanywhere.tech/v1'
OPENAI_BASE_URL='https://api.chatanywhere.tech/v1'
TOPOMEN_CONFIGURED='1'
```

TopoMen's default deployment config also points LiteLLM at `https://api.chatanywhere.tech/v1`. Do not use the legacy `mini-swe-agent` config directory for new runs. `TOPOMEN_GLOBAL_CONFIG_DIR` should only be used as a temporary override when debugging old local setups.

## Validation

```powershell
pytest tests
ruff check src/topomen tests
```

The five public commands are:

- `topomen-repair-experiment`
- `topomen-cumulative-experiment`
- `topomen-fetch-github`
- `topomen-trace`

- `topomen-memory-benchmark`

Server source audit (2026-10-19): Python3.10 UTC imports corrected; native suite168 passed and ruff passed. ContractEdge integrates the actual DefaultAgent through Model/Environment ports; its custom semantic plan is not a native TopoMen Docker scheduler API.
