# Codex Handoff for TopoMen v1.1.1

## Project Identity

TopoMen is a thesis-oriented project for AI-assisted environment deployment. The research path is:

```text
记忆库 -> AI 拓扑部署 -> 协同矫正机制
```

The project evolved from an earlier prototype, but current code, commands and documentation must use `TopoMen` / `topomen`.

## Read First

1. `docs/project/overview.md`
2. `docs/project/progress.md`
3. New logs in `docs/project/logs/`
4. Claude handoff files in `.claude/handoffs/` when the task mentions Claude, Claude Code, verification handoff, real testing, or cross-agent review
5. `docs/project/logs/legacy/CODEX_MEMORY_MIGRATION_2026-04-29.md`
6. Relevant historical files in `docs/project/logs/legacy/`
7. `docs/project/experiment_protocol.md`

## Claude Handoff Directory

Use `.claude/handoffs/` as the fixed directory for Codex <-> Claude Code handoff materials.

- Before continuing any Claude-related task, read `.claude/handoffs/README.md` and the newest relevant Markdown file in `.claude/handoffs/`.
- New handoff files should follow `YYYY-MM-DD_<topic>_<stage>.md`.
- Large experiment outputs still belong in `experiments/results/`; handoff files should reference those outputs by path and summarize key evidence.
- Do not put secrets, API keys, or private `.env` contents into handoff files.

## Current Architecture

- `src/topomen/core/`
- `src/topomen/llm/`
- `src/topomen/environment/`
- `src/topomen/memory/`
- `src/topomen/topology/`
- `src/topomen/collaboration/`
- `src/topomen/experiments/`
- `src/topomen/cli/`

Experiments are validation materials and live under `experiments/`.

## Rules

- Do not reintroduce the old package name, old command prefixes or old config names.
- Do not add compatibility commands for removed historical interfaces unless the user explicitly requests that.
- Keep thesis-relevant logs, trace files, summaries, topology outputs and memory seeds.
- Treat `experiments/workspaces/` and `experiments/projects/` as generated runtime output.
- Do not edit `.idea/` unless the user explicitly asks.

## Local LLM Config

TopoMen's global runtime config lives at:

```text
C:\Users\Wen\AppData\Local\TopoMen\.env
```

Use TopoMen variables in that file:

```dotenv
TOPOMEN_MODEL_NAME='openai/gpt-5.4'
OPENAI_API_KEY='<your-chatanywhere-api-key>'
OPENAI_API_BASE='https://api.chatanywhere.tech/v1'
OPENAI_BASE_URL='https://api.chatanywhere.tech/v1'
TOPOMEN_CONFIGURED='1'
```

The default `deployment_base.yaml` model is routed through LiteLLM's OpenAI provider to ChatAnywhere's domestic endpoint.

The legacy `C:\Users\Wen\AppData\Local\mini-swe-agent\mini-swe-agent\.env` path is historical only. Do not point new experiments at it unless you are deliberately debugging migration behavior.
