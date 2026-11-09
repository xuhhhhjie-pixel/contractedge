# graph_repair 接口 v1（冻结稿，2026-11-09）

本文件是 `contractedge-graph` 方法中两人并行开发的接口：契约图 JSON、`ce-*` 命令的输入输出、`ce-submit --check` 的返回结构，以及容器内的目录约定。模块内部实现可以替换，只要满足这里的输入输出，并通过 `system/tests/graph_repair/test_interfaces.py` 中的 schema 测试。

## 1. 容器内目录约定

| 路径 | 挂载 | 内容 |
|---|---|---|
| `/testbed` | 读写（与 Live 相同） | 待修复源码。工具一律不在这里写文件 |
| `/ce-tools` | 只读 | 工具包：`lib/contractedge/`（源码副本）、`bin/` |
| `/usr/local/bin/ce-{search,read,follow,links,graph,closure,probe,rollback,ckpt,submit}` | 只读，逐个文件挂载 | 命令入口，解释器为挂载的 venv312（`python -I`） |
| `/ce-inputs` | 只读 | `run.json`：机制开关、基线提交、墙钟、公开提交规则（与任务文本中公开的规则相同） |
| `/ce-state` | 读写，主机侧为运行目录下的 `ce-state/` | 契约图、探针、检查点索引、命令日志、预算计数 |

所有开关都关闭时（`live-equivalent` 配置），不挂载 `/ce-inputs` 与 `/ce-state`，只多 `ce-tools` 的只读挂载。

## 2. 契约图 `contractedge.contract_graph.v1`（`/ce-state/graph.json`）

```json
{
  "schema": "contractedge.contract_graph.v1",
  "nodes": [{"id": "N1", "role": "CONFIG_DECLARATION", "path": "values.yaml", "anchor": "ingress.annotations",
             "span": [209, 242], "source": "linker:helm", "text_sha256": "<span 文本的 SHA-256>"}],
  "edges": [{"id": "E1", "type": "PROPAGATION", "from": "N1", "to": "N2", "status": "VIOLATED", "note": "...", "probe": "P1"}],
  "preserve": [{"edge": "E3", "scenario": "未配置 annotations", "probe": "P3"}],
  "closure": [{"of": "E1", "candidate": "N3", "decision": "UNDECIDED|NEEDS_FIX|NO_FIX", "reason": "..."}],
  "unknown": [{"question": "...", "check": "..."}]
}
```

- `role` ∈ `CONFIG_DECLARATION, PRODUCTION_CONSUMER, GENERATED_STATE, RUNTIME_CONSUMER, OBSERVED_STATE`
- `type` ∈ `PROPAGATION, TRANSFORMATION, BINDING, SYNCHRONIZATION, RECONCILIATION`
- `status` ∈ `EXPECTED, VIOLATED, UNKNOWN`
- 唯一的程序硬校验：添加节点时 `path` 必须是 `/testbed` 下的普通文件，`span` 在文件行数之内；记录 `text_sha256`。`ce-graph validate` 报告当前文本与记录不一致的节点（`STALE`），不阻止提交。其余内容只检查格式。

## 3. 命令

所有命令成功时退出码为 0，输出以一行 JSON 或带 `CE:` 前缀的文本给模型；参数错误退出码 2；命令被预算停用时退出码 3，并说明原因。每次调用在 `/ce-state/events.jsonl` 追加一行 `{"tool", "argv", "started", "ended", "exit_code"}`。

| 命令 | 输入 | 输出 |
|---|---|---|
| `ce-search [--scope P]... [--symbol S] [--receiver R] [--text T] [--kind K] [--cursor C]` | 同 `SourceNavigator.search` | 命中列表，每项带 `handle`、`path`、`start_line`、`end_line` |
| `ce-read HANDLE` 或 `ce-read --path P [--symbol S] [--start N --end M]` | 同 `SourceNavigator.read` | 原文与行号 |
| `ce-follow HANDLE [--selector X]` | 同 `SourceNavigator.follow_dependency` | 绑定结果或 `UNRESOLVED`/`AMBIGUOUS` |
| `ce-links SEED [--linker NAME]` | `SEED` 为键名、字段名、环境变量名、`path` 或 `path:line` | `{"seed", "candidates":[{"linker","type","from":{path,line,anchor},"to":{path,line,anchor},"evidence"}]}` |
| `ce-graph add-node/add-edge/set-edge/preserve/decide/unknown/show/validate/import` | 见 `ce-graph --help` | 变更后的对象或校验报告 |
| `ce-closure EDGE_ID` | 图中的边 | 同类消费者候选，写入 `closure`，决定为 `UNDECIDED` |
| `ce-probe add --edge E --kind violate|preserve --cmd CMD [--local]` | 探针命令；默认通过 `ce-run /bin/sh -c CMD` 在公开运行时执行 | 探针 `P<n>` |
| `ce-probe gen` | — | 列出缺少探针的 `VIOLATED` 边与 `preserve` 项，以及建议的探针写法 |
| `ce-probe calibrate` | — | 在原版本上逐个运行未校准的探针：违反边探针须失败、保持边探针须通过；否则丢弃 |
| `ce-probe run [--id P]` | — | 当前版本上的结果，按工作区树记录 |
| `ce-ckpt list` | — | 自动检查点 `refs/ce/ckpt/N` 及其公开检查、探针结果 |
| `ce-rollback [N|best]` | — | 把工作区恢复到检查点（不动 HEAD 与索引） |
| `ce-submit --check [--json]` | — | 见第 4 节 |

探针记录（`/ce-state/probes.json`）：`{"id","edge","kind","cmd","local","status":"NEW|CALIBRATED|DISCARDED|SUSPECT","calibration":{...},"results":{"<tree>":{"exit_code","seconds"}}}`。

## 4. `ce-submit --check` 返回结构 `contractedge.submit_check.v1`

```json
{
  "schema": "contractedge.submit_check.v1",
  "tree": "<当前工作区树 id>",
  "passed": false,
  "unmet": [{"id": "PUBLIC", "kind": "public_check", "detail": "ce-public exit 1"},
            {"id": "P1", "kind": "violate_probe", "detail": "still failing"},
            {"id": "P3", "kind": "preserve_probe", "detail": "changed from pass to fail"},
            {"id": "E1/N3", "kind": "closure", "detail": "UNDECIDED"},
            {"id": "tests/foo_test.go", "kind": "policy", "detail": "matches forbidden_paths"}],
  "checks": {"public": {"exit_code": 0, "cached": true}, "probes": {...}, "closure": {...}, "policy": {...}},
  "notes": ["UNKNOWN edges do not block: E4"],
  "probes_disabled": null
}
```

检查顺序：提交规则（`policy`）→ 闭包 → 公开检查 → 违反边探针 → 保持边探针。前面的项未满足时，后面耗时的项仍会运行，以便一次返回全部未满足项；公开检查按工作区树缓存。

## 5. 执行器子类 `ContractGraphAgent`

只重写 `execute_action` 与 `has_finished`：

- `execute_action`：执行前若墙钟用到 85%（且开启停止规则）就回滚到最好的候选并强制提交；执行后在工作区变化时打检查点（临时索引 `write-tree` + `commit-tree` + `update-ref refs/ce/ckpt/N`，不动 HEAD、索引和工作区）；公开检查、构建或探针命令失败时附加归因。
- `has_finished`：识别提交标记后执行 `ce-submit --check --json`；通过就提交；未通过则把未满足项作为观察返回；连续两次未满足项相同则回滚到最好的候选并强制提交。

“最好的候选”：公开检查通过 > 通过的违反边探针更多 > 更新。
