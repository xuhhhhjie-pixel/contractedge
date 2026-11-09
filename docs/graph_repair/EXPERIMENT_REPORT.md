# contractedge-graph 实验报告（草稿，随实验推进更新）

日期：2026-11-09　　执行：张耀文　　状态：M2–M4 已完成，M5 进行中

本报告回答两个问题：
1. 新方法 `contractedge-graph` 在与 Live-SWE-agent 同框架、同预算、同评分的条件下，是否胜过同期重跑的 Live；
2. 如果有增益，其中多少来自契约图本身（A 对 F），多少来自通用的提交机制（F 对 B）。

数字均来自实验根目录 `lab-server:~/contractedge-eval-graph-repair-20261109` 下的冻结产物，生成脚本见 `system/src/contractedge/graph_repair/harness/`（`summarize.py`、`report.py`、`eval_linkers.py`）。

---

## 一、方法与实现

| 部分 | 实现 |
|---|---|
| 执行器 | mini-swe-agent 1.17.5 `DefaultAgent` 的子类 `ContractGraphAgent`，只重写 `execute_action` 与 `has_finished`（`agent.py`） |
| 提示 | `livesweagent.yaml` 原样；完整版只替换 “Recommended Workflow” 一节为三阶段指引（`prompts/build.py`） |
| 容器内工具 | `ce-search/read/follow`（封装 `SourceNavigator`）、`ce-links`（Helm、Kustomize、Go 标签、CRD、环境变量与 flag、依赖 API）、`ce-graph`、`ce-closure`、`ce-probe gen/calibrate/run`、`ce-ckpt`、`ce-rollback`、`ce-submit --check`；只读挂载，用挂载的 venv312 解释器运行 |
| 自动检查点 | 私有索引 `write-tree` + `commit-tree` + `refs/ce/ckpt/N`，不动 HEAD、真实索引和工作区，不进入导出补丁 |
| 自动连接与闭包 | 首次修改某文件后，执行器调用 `ce-graph auto`：记录被改段落、找到它消费的声明、列出同一声明的其他消费者作为闭包候选 |
| 停止规则 | 提交标记触发 `ce-submit --check`：提交规则、闭包决定、公开检查、已校准探针；未满足项返回模型。强制提交：墙钟 85%、传输层 token 余量不足两次请求、或连续两次未满足项相同 |
| 失败归因 | 公开检查、构建、探针失败时，把报错位置映射到图节点与边，附在观察里 |
| 消融开关 | `graph/linkers/closure/probes/checkpoint/attribution/stop_rule`；变体表 `config.py`（A 完整、C 无探针、D 无闭包、E 无停止规则、F 仅提交检查与强制提交、Z 全关） |

连接器、闭包、探针、归因为按冻结接口（`INTERFACES.md`）写的临时实现，留待替换。

## 二、接入验收（M2）

| 验收项 | 结果 | 证据 |
|---|---|---|
| 控制器副本登记第四种方法，原冻结控制器未改 | 通过：副本中 9 个文件按断言替换，原控制器只读 | `E/controller/SOURCE_IDENTITY.json`（`edited_files` 记录替换前后哈希） |
| 框架身份覆盖 minisweagent 包 | 通过：1515 个文件哈希，含 minisweagent 与 `mini-swe-agent==1.17.5` | `E/evidence/FRAMEWORK_IDENTITY.json` |
| 全关版本与 Live 逐项一致 | 3/3 案例通过（26 项：native-config 各段、HostConfig、Config、挂载），只差 ce-tools 只读挂载与每次运行自有的路径、会话号 | `E/analysis/smoke-compare-*.json` |
| 真实冒烟 3 例 | 全关 3/3 通过、同期 Live 3/3 通过；调用 12/14、19/17、18/26 | `E/results/*/smoke-*.json` |
| 工具状态不进入补丁 | 通过：单元测试，以及截至面板所有真实候选补丁均只含源码文件 | `test_checkpoint.py`；`candidate.patch` |

## 三、机制测试（M3）

单元与模拟模型集成测试 57 项通过（`system/tests/graph_repair/`，venv312 + pytest 9.2）。覆盖：图结构与锚点校验、各连接器正反例、大仓库预筛与时间预算、检查点不动 HEAD/索引/补丁、回滚、探针在原版本上校准与丢弃、探针预算停用、停止规则各条件、提交规则与发布校验器同一语义、自动连接与闭包、强制提交（墙钟、token、重复未满足）、全关配置与 `DefaultAgent` 逐条消息一致。

## 四、面板试跑（M4）

（待按 `E/analysis/summary-panel2.json` 填入逐例表。）

要点：
- 第一轮（旧版本）中模型从未主动使用契约图工具；据此把连接器与闭包改为执行器自动机制。
- 第二轮（`panel2`）12 例中 11 例端到端通过；RW-HELM-CONTROLLER-1411 提交后改错。
- 探针占比：调用 0.5%、token 2.1%、耗时 0.6%；模型几乎不写探针。
- 发现并修复：提交规则匹配语义与发布校验器不一致；停止规则未覆盖 token 上限；ce-links 在大仓库上超时；清理子 uid 文件的权限问题。

## 五、对照与消融（M5）

（进行中。）

## 六、局限

（待写。）
