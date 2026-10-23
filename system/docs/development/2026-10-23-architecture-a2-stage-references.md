# 跨阶段引用、实际调用与 READY 原阶段继承

本阶段依赖 `architecture-b-runtime-observer-20261023-r2` / PR #9。当前源码、配置、测试面板与验证范围以 `CURRENT_STATUS.json.current_development` 指向的新冻结及独立验证报告为准。包版本仍为 0.6.27，`formal_ready=false`。本文不将公开开发用例解释为 benchmark 修复率。

## 事实和引用

`RunRecorder` 保存有版本的阶段回执、内容寻址产物与不可变调用账本片段。诊断、最终规划、执行、公开观察、复核、反馈及候选选择回指实际文件及其摘要；实际派发事实仍来自原 `BudgetedProvider`。记录器不另建费用总表、不重新派发请求，也不根据相同请求合并两次真实调用。

调用结算变化追加新 revision，已封存片段和历史引用保持不变。兼容 `model_calls.json` 及 trace 是当前视图；长期引用不能绑定一个不断改写的文件摘要。legacy 缺少的请求和阶段明确登记为兼容导入，不补造原阶段完成事实。录制失败必须保留原模型或执行异常，不以记录异常覆盖根因。

逐义务交付区分原文、证据、关系/违反、变更/计划，记录最终请求的位置与投影类别。完整原文、摘要投影、身份引用、未送达分别表达。实际派发引用在结算后绑定唯一调用片段；引用存在不证明模型正确采用。必要内容门禁只针对已声明、已有反例依据的具体内容，不将整份诊断或所有义务一概设为 mandatory，不关闭合法的部分修复与 UNKNOWN。

明确要求原摘录的必要项，只能由原文或可独立核验的原样重读满足。既有执行协议需要的当前目标文件，另记 `CURRENT_TARGET_SOURCE`，绑定当前快照、相对路径和实际字节摘要；它不能把缺失的历史摘录升级为已交付，也不能替代独立的 source prerequisite。身份漂移、同 ID 原记录冲突、必要原文缺失或无法核验均应在新增派发前保留具体原因；不得把未派发尝试写成一次模型调用或触发格式纠错重试。

## READY 保存和继续

`run_public_demo.py --save-ready` 使用真实 ProviderJournal，保存诊断、最终计划、前半阶段及 READY 回执；额外的 elapsed 结算追加 seal，保持原请求、路由和预算语义。

新进程 `--resume-ready` 核对输入、快照、方法源码、运行环境、配置、完整实际调用及不可变 journal 清单。只继承 `journal/` 下允许的不可变命名空间，拒绝绝对路径、目录穿越、符号链接和可变控制文件。配置、freeze、claim、费用账本不能通过导入覆盖。原阶段字节和引用保持不变，导入本身另有 `VERIFIED_STAGE_IMPORT` 回执；旧包不具有原回执时明确登记 `LEGACY_IMPORT`。

```bash
python scripts/run_public_demo.py --case config-binding --save-ready --output /tmp/contractedge-a2-ready-new
python scripts/run_public_demo.py --resume-ready /tmp/contractedge-a2-ready-new --output /tmp/contractedge-a2-continued-new
```

上述默认 synthetic 示例不产生模型 HTTP；实际隔离路径仍须遵守阶段 B 的受信镜像、资源、公开 profile、Broken 初始观察和清理门禁。READY 只支持已定义的单次主动保存边界，不代表任意崩溃恢复；持久候选事务和恢复属于后续 C2。

## 开发验证与边界

针对性验证覆盖实际阶段 hooks、封存引用、READY 新进程继承、路径与配置重绑定拒绝、录制失败根因、逐义务最终请求投影及必要内容门禁。固定时钟与固定输出路径对照比较实际请求、调用预留、预算、路由、反馈和候选补丁；方法策略变化与纯记录重构分别报告。

完整可移植开发子集仍使用原 `scripts/test_portable.py`，保留六项历史/native排除。该范围包含实际 Docker 测试，必须先按 README 核验并协商当前 daemon 能力，不能在无镜像或错误 API 环境把失败改为跳过。真实隔离三例和 READY、新增运行反例、native TopoMen fixture分别登记。当前冻结验证报告和 GitHub CI 必须对应同一份最终源码；前版通过不能证明新源码通过。

本阶段不完成 C1 状态重构、C2 事务恢复、D1 消费者解耦或 D2 四模式消融，不执行正式数据、隐藏评分或无界 API 重试。公开检查范围仍限已注册的实际观察终点；证据不足保留 UNKNOWN。
