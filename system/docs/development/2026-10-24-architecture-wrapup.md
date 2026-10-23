# ContractEdge 架构阶段收尾（2026-10-24）

本次将已验证的架构开发进度汇总到主分支。运行源码与 PR #13 的 `d487c4648b144c4eb2f04a84ab37010bfa8043f9` 相同；本次新增收尾说明，不把尚未完成验证的协议补丁带入发布。包版本仍为 0.6.27，`formal_ready=false`。

## 可以使用的范围

- 公开需求与 Broken 源码经过检索/诊断、修复意图、TopoMen 或 MODIFY-only 消费者、受控编辑、实际隔离运行、独立公开观察、反馈及一致候选导出。
- RuntimeSession 实际核验镜像、只读挂载、UID、网络、资源限制、有界输出和清理；不降级为宿主执行。
- 阶段引用、唯一调用账本、显式状态、候选事务与保守恢复；未知调用不自动重发，回滚失败禁止导出。
- Full、without_relation、without_changeset、Direct 独立路径与六例公开开发面板。主模式比较共用 MODIFY-only；原生 TopoMen 兼容性另报。

这些能力是开发工程范围。部分历史 worker、tape 及无 recording_dir 的 programmatic 入口仍走兼容路径；不能称所有入口重写或任意断点自动恢复。

## 已有同版验证

冻结：`docs/development/architecture-d2-method-modes-20261024-r2.snapshot.json`，SHA256 `d9bd920dbcc963aa1e13af8e312e46ce31a2c0a30ede09f4720b515cda56ea34`；源码摘要 `b9f9a83ebf9ced04a5b1672dd9870de5c57b54d5b6c257246d487759e323735a`。

- 组合回归 417 项及 45 子测试，portable 开发子集 1308 项及 86 子测试，native 单独 9 项。六项历史/native 排除保留，不是完整历史实验复现。
- 两消费者实际 8 格恢复矩阵通过；四模式实际 24 格通过，48 会话/96 容器；固定目标、保持项、资源及清理回执完整，独立原字节审计通过。
- 准确 PR #13 HEAD 的两组 GitHub Actions 共 10 项全部成功。后续汇总提交及 main 的 CI 应按各自准确 HEAD 另核对。
- 收尾时额外在当前实验室服务器上，用相同冻结源码检查实际 Docker 限额、三个 isolated synthetic 公开例子及独立进程 READY 续跑，结果随本文件提交的 `architecture-wrapup-20261024.validation.json` 提供。

24 格使用固定 synthetic 响应，不能作为论文修复率或模型采用语义的证明。actual24 没有实际触发格式纠错/第二轮反馈；相关分支证据单列。三类手工语义干预改变请求、未改变固定响应下的编辑和行为，不能证明 Full 收益。

## 首次真实模型结果

官方 DeepSeek 的新架构 config-binding 尝试已完成 4 次 HTTP、1 轮，生成 307 字节补丁（SHA256 `fa8820d9d35babd0e47afd7e02380bdf8f08e5d6fa4b6a47806ab9389c28ecbb`）。候选经过实际隔离执行，selected、disabled、stable 三项独立公开检查 PASS，review 与候选选择实际执行。

最终为 `PUBLIC_EXECUTION_COMPLETE`、`exportable=true`、`public_semantic_status=UNKNOWN`。PROMOTE 表示符合当前候选保留协议，不等于语义修复成功。本次收尾没有新增模型调用，也没有把历史结果改成 PASS。

## 尚未完成的项目

1. **P0，检查绑定 schema**：`changeset/prompt.py` 目前仅用 `method_behavior_probes` 决定绑定字段。首次真实请求虽有 `registered_public_checks`，实际 P/PC schema 却不允许 `behavior_check_refs/check_coverage`，且 additionalProperties=false。需要对齐两类能力来源与最终 schema，禁止替模型补绑定。
2. **P0，观察场景协议**：`execution/support.py` 要求计划 scope 与 observation.scenario 一致，而当前独立公开 observer 没有提供该字段。三项 submitted PASS 因 OBSERVATION_SCENARIO_MISMATCH 降 UNKNOWN；修复应由冻结检查定义提供显式场景/聚合范围，不能从 check ID 猜场景。
3. **P1，修复后的同版验证及有限复测**：协议修复草稿仅做 AST 语法检查，尚无有效旧红/新绿、生产入口或运行验证，未提交合并。继续时先复用原包定位并验证合法/非法双向控制，新源码建立新 freeze，再决定是否使用原授权剩余的至多一次有限真实复测；不为追 PASS 追加调用。
4. **研究效果与迁移**：正式真实案例消融、反馈收益、泛化、隐藏评价与全新机器完整迁移未验收。合成面板、CI 和单个公开例子不能替代这些证据。当前不运行 formal150，不读取 GT/Fixed/隐藏评分。

旧 ACK 原包缺失仍是独立历史限制，不是当前开发版启动前置条件，不追索或伪造。

## 使用与下一步

安装、镜像、隔离运行、READY 及测试命令见 [开发入口](../../README.md)。Linux / Python 3.10 或 3.12 为当前已验证范围；先执行 `python scripts/verify_development_snapshot.py`，再运行公开例子。默认 host 路径只允许 synthetic，真实 provider 只走已验证 isolated 路径。

恢复优化时从本收尾版本接续，先处理上述 P0 协议问题，保护旧冻结与原始失败证据；同版回归通过后再发布新方法身份。此次仅完成架构阶段收尾，不宣布整个优化目标或论文实验完成。
