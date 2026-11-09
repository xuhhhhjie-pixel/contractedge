# graph_repair 进度记录

工作树：`lab-server:~/contractedge-collab-20261023/worktrees/graph-repair-20261109`（分支 `codex/graph-repair-20261109`，起点 `139f6a6`，**未提交**）
实验根目录：`lab-server:~/contractedge-eval-graph-repair-20261109`（下称 E）
本地编辑副本：`D:/工商/科研/ContractEdge_Topomen/graph-repair-dev/`（与工作树同步）

## 当前步骤

| 步骤 | 状态 | 说明 |
|---|---|---|
| M0 现场核对与冻结 | 完成 | 见“已验证的事实” |
| M1 接口冻结 | 完成 | `INTERFACES.md`；`system/tests/graph_repair/test_interfaces.py` |
| M2 接入与骨架 | 部分完成 | 控制器副本、工具包、实验根目录、调度器已建；冒烟 1/3 例完成且配置比对通过 |
| M3 契约机制 | 代码完成，单元与模拟模型集成测试 50 项通过 | 待：12 例上连接器召回、探针可用率 |
| M4 面板试跑 | 未开始 | |
| M5 对照与消融 | 未开始 | |
| M6 交付 | 未开始 | |

## 已验证的事实

- 服务器上的 `/tmp/contractedge-deepseek-preflight-20261019` 是**符号链接**，指向 `/home/zyw/contractedge-official-pilot-20261020/frameworks/contractedge-deepseek-preflight-20261019`；数据在 /home。主机重启只会丢失链接。备份：`E/backup/contractedge-deepseek-preflight-20261019.tar.zst`（399 MB），逐文件哈希 `E/backup/framework-files.sha256`，归档哈希 `E/backup/archive.sha256`。
- `~/.config/mini-swe-agent/.env` 只有一个键 `MSWEA_CONFIGURED`，不含模型参数；对 Live 与新方法作用相同（两者同一进程类型、同一解释器）。
- 部署的控制器：全量运行用 `~/contractedge-full150-20261022/controller`（与本地副本 `artifacts/full150_autonomous_20261022/recovery-source/controller` 全部 36 个文件哈希一致）；恢复阶段用 `~/contractedge-recovery-20261025/controller`，另改了 `freeze_method.py`、`public_runtime_server.py`、`resource_collector.py`、`submission_policy.py`，新增 `source_snapshot.py`。7 处登记点所在文件两者相同。新副本以恢复阶段控制器为底（最后实际产出 Live 结果的版本）。
- 无根 Docker（`unix:///run/user/1009/ceofficial-docker.sock`，29.5.3）在运行；系统默认 docker 客户端版本过新，须用 `READY.json` 里的客户端。
- mini-swe-agent 1.17.5 `DefaultAgent`：`execute_action` 内调用 `has_finished`；`NonTerminatingException` 的消息作为 user 消息返回模型，`Submitted` 结束。`DockerEnvironment.execute` 以 `docker exec -w cwd ... bash -lc` 执行。
- 补丁导出：`git add -N . && git diff --binary BASELINE`。检查点改用私有索引 `write-tree`（包含未跟踪文件）+ `commit-tree` + `update-ref refs/ce/ckpt/N`，单元测试确认 HEAD、真实索引与导出补丁都不变。
- 评测名单已冻结：`E/freeze/EVALUATION_LISTS.json`（副本在 `docs/graph_repair/`）。评测名单 46 例（改错 9、修对未提交 8、违反规则 6、无人修对且不重叠 3、不退步 20），留出集 8 例。

## 设计判断（方案文档未覆盖的细节）

1. 检查点不用 `git stash create`（不含未跟踪文件），改为私有索引上的 `write-tree`；同样不动 HEAD 与工作区。
2. 工具状态 `/ce-state` 为宿主机运行目录下的 `ce-state/` 读写挂载，运行参数 `/ce-inputs` 只读挂载；全关配置不挂这两项。
3. 墙钟以 wire 账本中第一条 `request_started` 为准（与监督进程同一时钟）；停止规则开启时，命令超时会收紧到截止前 90 秒，留出回滚与导出的时间。
4. 探针调用占比按 `探针调用数 / max(总调用数, 40)` 计，避免早期分母过小立即停用。
5. 不在新方法里加入 DeepSeek 标记恢复（`salvage_action`）：它会改变动作解析，Live 没有，加了就不再是同一框架。
6. 每个对照组的每次重复是一个独立“槽位”（`attempts-<标签>/<方法>`），工作区从基线源码重新复制，单独冻结 profile 与任务文本哈希。
7. `ce-follow` 的依赖追踪在容器内暂不接入固定版本依赖（`PublicGoDependencies` 依赖宿主机布局），只在项目源码内追踪；连接器 `deps` 只给出调用点。属于临时实现。
8. 预算：目标条件写的是“总预算确认前只使用约 200 元的开发额度”，比任务说明中的 40 元高；按 200 元执行，总上限仍为 260 元。

## 临时实现（待许洪杰替换）

`graph/closure.py`、`linkers/*`、`probes/*`、`attribution.py`：按冻结接口写的最小版本，文件头已标注。

## 未解决的问题

- 7 个案例不在全量计划中（6 个面板案例和留出集的 R3-HELM-9175），正在从 official75 计划补行（`E/augment_plan.py`）。
- 面板案例 RW-HELM-CONTROLLER-1411 同时在“Live 改错”名单中，开发时会被看到；报告中单独标出。

## 暂停点（2026-11-09 01:05，按用户要求暂停）

- 已放置 `E/PAUSE`：调度器（`graph_eval.py daemon smoke`，PID 755936）不再派发新作业；已在运行的作业照常结束，恢复后再收集与评分。
- 暂停时仍在运行：付费生成 `generate-R3-SKAFFOLD-9732-smoke-Z1-contractedge-graph`（墙钟上限 1800 秒）；零模型准备 R4-PROVIDER-HELM-408（孤立进程，跑完即止）与 RW-PROMETHEUS-19838（调度器作业）。面板准备循环 `prepare_list.sh` 已停止，未准备：R3-KPT-4746、R3-KPT-4673、R4-ACK-CLOUDWATCH-2985、R4-CERTMANAGER-9204、RW-ALERTMANAGER-4705、RW-CAND-091、RW-HELM-CONTROLLER-1411。
- 已花费 0.160 元（2 次付费运行），账户余额 212.96 元（首次运行后的快照）。
- 已有结果：RW-CAND-130 上全关配置（Z1）12 次调用、通过；同期 Live（B1）14 次调用、通过。配置逐项比对（`harness/compare_config.py`）26 项一致，只多 ce-tools 只读挂载。
- 已准备完成的案例：RW-CAND-130、R3-SKAFFOLD-9732、R3-KOPF-726。
- 恢复方式：`rm E/PAUSE`；调度器会接着收集 SKAFFOLD Z1、准备 PROMETHEUS 并跑完冒烟剩余 3 次；之后 `python3 graph_eval.py runs panel` 冻结面板名单并启动 `daemon panel`。

## 本次发现并处理的问题

- 恢复阶段的 `prepare_case150.py` 依赖 `RECOVERY_SCOPE.json` 与 `case_capacity.py`：已复制后者，`RECOVERY_SCOPE.json` 置空（热案例限流改由调度器的磁盘/内存准入负责）。
- 可写公开挂载的案例断言写死“3 种方法 × 2 阶段 = 6”：已改为 `2 × 方法数`（只改实验根目录里的副本）；RW-PROMETHEUS-19838 因此首次准备失败，已删除其自有的不完整目录与 socket 目录后重新准备。
- 控制器副本的 `bin/docker` 包装脚本指向恢复阶段目录里的 `docker_telemetry.py`：与副本中的同名文件逐字节相同（SHA-256 `1795a372…`），Live 与新方法经过同一包装，不影响公平；改动它会使已准备案例的冻结身份失效，故保留并在报告中说明。

## 下一步

1. 解除暂停，跑完冒烟 3 例（全关配置与同期 Live 各 3 次），对 3 例都做配置比对，核对步数与结果同量级。
2. 准备其余面板案例，跑面板 12 例完整版，统计探针在调用、token、耗时上的占比，以及连接器召回（`harness/eval_linkers.py`；RW-CAND-130 试算：单元对召回 0.727）。
3. 面板结论确认后进入 M5 对照与消融（`SETTINGS.json` 中已定义，预计约 270 次运行）。

## 夜间无人值守链（2026-11-09 01:12 启动）

- 入口：`E/overnight.sh`（PID 803647，`setsid` 后台运行），日志 `E/logs/overnight.log`，结束标志 `E/OVERNIGHT_DONE.json`。
- 步骤：
  1. 等先前的零模型准备结束，把未完成的案例目录移到 `E/cases-incomplete/`；
  2. 解除暂停，跑完冒烟（全关配置与同期 Live 各 3 例），逐例比对配置，结果在 `E/analysis/smoke-compare-*.json`；
  3. 冻结并运行面板 12 例完整版（`runs-panel.json`）；
  4. 写出汇总 `analysis/summary-{smoke,panel}.json` 和连接器召回 `analysis/linker-recall-panel.json`；
  5. 在剩余磁盘不少于 55G 时，零费用预先准备 M5 的前 10 个案例。
- 不会自动启动付费的 M5 对照组。
- 调度器改动：准备失败的案例记入 `E/issues/` 后跳过，不再中止整个阶段；生成证据异常的运行逐个记录，累计 3 个才停止付费运行；新增余额下限，余额减预留低于 15 元时不再派发。
- 预计花费：冒烟剩余约 1 元，面板约 5–12 元；上限仍由 `SETTINGS.json` 的 200 元控制。

## 2026-11-09 上午核对（夜间链结果）

- 冒烟（M2）完成：3 例全关配置 3/3 通过、同期 Live 3/3 通过；调用 12/14、19/17、18/26。配置逐项比对 3/3 通过（`E/analysis/smoke-compare-*.json`，26 项；环境变量改为按集合比较）。11 个真实候选补丁均不含工具状态或临时文件。
- 夜间两个调度器都在清理阶段崩溃：容器以子 uid 写入的文件宿主机无权删除。已改为权限不足时经无根 Docker 容器删除，清理失败只记入 `issues/`，不再中止调度。
- 面板（M4）已完成 5/12 次生成，3 次已评分且全部通过。**关键发现**：5 次运行中模型从未使用 `ce-search/ce-links/ce-graph/ce-closure/ce-probe`，契约图为空，没有校准任何探针；只用过 `ce-submit --check`（3 次），有 1 次在去读工具源码。因此当前“完整版”的实际行为接近 F 组（Live 提示加提交检查与强制提交）。R3-SKAFFOLD-9732 在 85% 墙钟时强制提交了撤销前的修改。
- 大仓库性能：provider-upjet-aws（17012 个文件）上 `ce-links` 超过 120 秒命令超时，导致该案例零模型预检失败。已加入 `git grep` 预筛选、25 秒预算（超时时返回部分结果并提示）、导航器按需计算哈希；实测 `ce-search` 0.3 秒，`ce-links` 1–25 秒。
- 提示词小改：说明 ce-* 命令可直接使用，不必阅读其实现。
- 工具包替换：`E/swap_tools.sh` 会在没有运行进行时替换 `E/ce-tools`（旧版保留为 `ce-tools-<时间>`，记录在 `tools-versions.jsonl`），然后恢复面板。每个槽位的 `SLOT_FROZEN.json` 现在记录所用工具包的清单哈希。
- M5 预备：已准备 RW-CERTMANAGER-9128、RW-KEDA-7777、RW-DAPR-9173、R4-KEDA-8227。R4-PROVIDER-UPJET-AWS-2117 因上面的性能问题失败（待重新准备）；R4-FLINK-OPERATOR-1168 失败在基线专用公开运行器（Live 自己的零模型预检就报错），列为缺口。剩余磁盘 54G。
- 已花费约 2.2 元。

### 待决定（影响方法本身）

模型不会主动使用契约图工具。可选做法：
1. 加强提示（把三阶段写成必须先执行的具体命令）——仍依赖模型；
2. 把连接器与闭包做成执行器的自动机制：首次修改某文件后自动运行 `ce-links`，把同类消费者候选附在观察里，并作为提交检查中的闭包项——不增加编辑前关卡，可单独消融（建议）；
3. 提交检查要求契约图非空——强制但成本高。

## 2026-11-09 10:00 方法调整：自动连接与闭包（按建议执行，待确认）

- 依据：面板第一轮 5 次运行中模型从不主动使用契约图工具。按“缺少的决定先按方案建议执行、记录、下次汇报提出”的约定，把连接器与闭包做成执行器的自动机制，不新增编辑前关卡。
- 做法：`execute_action` 在工作区出现新改动的文件时调用 `ce-graph auto --files ...`（`graph/auto.py`）：把改动段落记为节点，用连接器找它消费的声明并建边（状态 UNKNOWN），把同一声明的其他消费者列为闭包候选（UNDECIDED，需模型决定），并把 `[ce-auto]` 说明附在观察里；探针开启时提示可为该边加探针。开关沿用 `graph`+`linkers`（记录与提示）和 `closure`（是否进入提交检查），变体表不变，C/D/E/F 的消融含义不变。
- 测试：新增 `test_auto.py`，共 56 项通过。
- 工具包已替换：`be5592…` → `03d164…`（`E/tools-versions.jsonl`）；每个槽位在 `SLOT_FROZEN.json` 记录所用清单哈希。
- 面板第一轮（旧版本）不再派发新运行，只收集与评分已生成的 6 次（`NOGEN-panel`），作为开发记录；新阶段 `panel2` 用最终版本完整跑 12 例，作为 M4 的正式试跑。
- 初步观察（panel2 前 3 次）：都触发了自动连接；HELM-408 与 CAND-130 各列出 2 个同类消费者，模型均以理由决定 NO_FIX；HELM-408 调用 40→22。
- M5 磁盘：调度器增加 `release_case`，案例在 M5/留出集阶段的全部运行评分后，删除可重建的源码与包目录，并删除本实验加载（LastTagTime 晚于实验开始）且无容器使用的公开运行时镜像；运行证据保留。

## 2026-11-09 11:41 M4 完成，M5 启动

- 面板第二轮（`panel2`，工具包 `03d164…`）：12 例中 11 例端到端通过（RW-HELM-CONTROLLER-1411 提交后改错；它也是 Live 改错名单中的案例），花费 6.94 元，调用合计 429 次。强制提交 5 次（墙钟 85% 3 次、连续未满足 2 次），全部通过隐藏评分。
- 探针占比（M4 报告项）：调用 0.5%、token 2.1%（单次运行均值）、耗时 0.6%；只有 RW-CAND-091 一例写了并校准了 1 个探针。自动连接在 12 次运行中都触发，契约图平均约 5 个节点。
- 连接器召回（金标只用于评估）：`E/analysis/linker-recall-panel.json`。
- 面板暴露并已修复（M5 生效，`fe9bd906…`）：
  1. 提交规则检查用了 shell 通配符语义（`*` 不跨目录），而发布的校验器 `scorer/patches.py` 用 `fnmatch.fnmatchcase`（`*` 跨目录）；导致 panel2 中 2 次把真正的修复文件判为“不在 patchable_paths 内”，触发了连续未满足后的强制提交（两次都仍通过）。已改为与校验器相同的 `fnmatchcase`。
  2. 停止规则只看墙钟；面板第一轮 R3-KPT-4746 因 DeepSeek 原生工具调用标记（DSML）造成 92 次格式错误，最后被传输层 500 万 token 上限截停、补丁为空。新增 token 余量触发：剩余额度不够再发两次请求时提交最好的候选（只在执行动作时检查；纯格式错误循环中无法触发，这是只重写两个方法的代价）。
  3. DSML 标记问题对 Live 同样存在，两边不加恢复，作为共同噪声记录。
- 面板专用案例已释放（源码包与本实验加载的镜像），磁盘回到 62G。
- M5：`runs-m5.json` 共 272 次运行（A1/A2/B1/B2 各 46，F1 23，E1 17，C/D 各 2×12），开启同案例并行（各槽位工作区、socket、公开容器前缀互相独立），案例在本阶段全部运行评分后自动释放。
- 累计花费 10.61 元。

## 2026-11-09 13:40 M5 运行中的基础设施事件

- 同案例并行导致同一方法的槽位共用 `CE_API_USER_ID`，请求结算断言失败（RW-CERTMANAGER-9128 m5-A1）。模型运行已结束，用 `complete_slot.py` 补完计费、结局判定、证据冻结（零模型、不重采样，记录在 `COMPLETED_AFTER_SETTLEMENT_FAILURE.json`）；调度器以后遇到同类情况自动补全；同案例并行已关闭。
- 12:10–13:20 主机负载 380–520（系统进程约 1170 个，非本实验），远程登录握手超时约 1 小时；两次运行（R4-PROVIDER-UPJET-AWS-2117 与 RW-KEDA-7777 的 m5-A1）被作业级安全超时（2400 秒）在模型运行中途打断。已按 wire 账本计费（0.30 与 0.16 元），记为 `infra: true` 问题，不计入“3 次异常停止”阈值；作业级超时放宽到 4800 秒（监督进程自身的 1800 秒墙钟不变，两种方法一致）。
- 规则：M5 主体完成后，所有被基础设施中断的运行（不分组别）各补跑一次，标签加后缀 `r`，报告中单独列出；含此类运行的案例暂不释放源码。
- R4-FLINK-OPERATOR-1168 准备失败（基线专用公开运行器，Live 零模型预检同样失败），所有组跳过，列为缺口。
- 墙钟耗尽时最后一个请求未结算（`requests_outcome_unknown`>0）的运行，按“预算耗尽、未提交”的诊断候选收集（`verify_budget_stopped`：保留全部字节与身份校验，只放开该断言），并在生成记录中写明未结算请求数。
- 并行运行数 3→4（内存准入不变）。
- 预算：按历史 Live 费用推算 M5 全量约 215–245 元，加已花费会超过 200 元开发额度。按 A/B → F → E → C/D 的优先级，C、D 组暂缓（`SETTINGS.json` 的 `defer_groups`）；A/B/F/E 完成后若额度仍有剩余再补跑，否则列为缺口。已运行的 C1、D1 各 1 次保留。

## 2026-11-09 15:10 改为先小规模试跑（用户提出）

- 用户问为什么直接跑大规模消融、能否先小规模测试。判断：应先试跑——方法在进入 M5 前刚改了三处（自动连接与闭包、提交规则语义、token 余量触发），这些改动只在面板（多为 Live 能修好的开发案例）上跑过，没有在目标类别上验证；M5 前 17 次评分全为失败（均为 Live 改错类案例）。
- 做法：`SETTINGS.json` 的 `allow_only` 把 M5 的新派发限制为 A1/B1、14 个案例（已开始的 Live 改错 5 例；修对未提交、违反规则、无人修对、不退步集各按冻结名单顺序取前 3/2/1/3 例）。沿用 M5 标签，已完成运行直接计入，其余 M5 运行暂缓（不删除、可恢复）。
- 试跑之后再定：方向有希望则扩到全量 A/B×2 与 F/E；否则先改方法，不再跑大规模消融。

## 2026-11-09 17:20 试跑完成

- 14 例 A1/B1 全部评分（KEDA-7777、UPJET-AWS-2117 的 A1 被基础设施中断，用 A2 代替）：A 7/14，B 4/14，A 胜 3（SKAFFOLD-9892 强制提交、HC-1589 正常提交、KUBEBUILDER-5708 提交规则检查），负 0。Live 改错 5 例与 HELMFILE-2531 两边失败在同一评分单元。
- R3-SKAFFOLD-9892 A1 首次评分为基础设施错误，恢复发布包（`restore_package.py`）后重评 PASS，原结果另存 `.original-infra-error.json`。
- 结论：增益来自通用提交机制，契约图本身尚未显出作用。累计花费 30.88 元。
- 阶段总结与下一步计划：`REFACTOR_SUMMARY_20261109.md`。调度器已处于 COMPLETED（暂缓运行视为终态），扩大规模需改 `SETTINGS.json` 的 `allow_only`/`defer_groups`，已释放案例需重新准备。
