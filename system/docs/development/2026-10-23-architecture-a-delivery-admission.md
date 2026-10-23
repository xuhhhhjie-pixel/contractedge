# 证据交付与统一请求准入（阶段 A 首个增量）

基线为 PR #7 的 a0705138f6130fa7e96551eeb95c5cb40377d907。本阶段改进真实生产入口，尚未完成阶段 B/C 的语义内核拆分、消费者解耦或隔离运行会话。

## 证据选择与失败出口

select_observations_delivery 返回 records 与 ce.evidence_delivery.v1 回执，保留原完整记录选择顺序。回执登记每项 selected/omitted/reason、快照/内容身份、必需项覆盖、预算和摘要；同 ID 不同记录、摘要错误及不可验证的声明失败拒绝交付。显式当前 snapshot 过滤陈旧观察，旧 tuple 入口仍可调用。

必需项只自动纳入经结构/摘要和完整性校验的当前 PUBLIC_BEHAVIOR FAIL 及已绑定的 FAILED operation facts，也可显式指定引用。可选观察允许省略；必要记录放不下则 INCOMPLETE。repair 在前半/执行前保存 public-evidence.delivery.json 并以 EVIDENCE_DELIVERY_INCOMPLETE 停止派发；公开复核保存 review-evidence.delivery.json，缺少必要证据时不调用 reviewer，保留 UNKNOWN。

## 请求与账本

model.admission.quote_request 只准备和计数，不消耗调用或费用；context_fits 在诊断、计划修订、直接执行、原生 TopoMen 包装后的最终消息和公开 review/纠错中使用同一口径。max_context_tokens 是包含 provider/schema overhead 的输入上限；配置 model_context_tokens 时再限制输入加输出预留。总 token、调用数、时间与 MoneyBudget 仍由原账本约束，本次未移除前半局部策略计数器。

complete_quoted 核对 provider、request 和 prepared bytes 绑定，复用准备的 HTTP 字节，只预留/记账一次。预留前请求变化拒绝；持久化回调之后沿用已提交 prepared bytes。无 prepared 协议的旧 provider 若此时请求被改则拒绝派发，避免发送未记账内容。未知响应继续保留原预留，不自动重发。

## 持久绑定和兼容边界

run_front_recorded 保存 front.method_input/config/public_observations/execution_feedback/replay.json，front.delivery.json 对真实文件字节、最终 ledger 请求和传输 body 记录摘要。selector receipt 必须与实际持久 observations 的内容、引用顺序和快照自洽；legacy 没有原 request 或 transport 时明确为 null，不伪造追溯证据。异常路径也尝试保存回执，录制错误不覆盖原异常。

这些记录证明交付与请求身份，不证明每条证据被模型采用或语义判断正确。模型输出 wire schema 保持兼容；当前反例包括必要 FAIL 超预算、旧保持项观察、各入口 schema overhead 超限、复核缺口和实际 HTTP/账本/输入绑定。

## 验证范围

实际结果见 architecture-a-delivery-admission-20261023.validation.json。portable 仍保留原六个历史/native 排除，native 单独报告。三个合成公开案例和新进程 READY 续跑已实际检查；容器探针核对 2CPU/2GiB。测试使用明确的新 worktree PYTHONPATH 和 vendored TopoMen，避免 editable 环境误读旧源码。

当前阶段新增真实 API 调用为 0；此前 a070513 的两次官方前半调用仅作旧版本参考。没有正式数据实验、隐藏评分或修复率结论，formal_ready=false。旧 0.6.27 DELIVERY_MANIFEST 与历史开发冻结保留，新源码使用本阶段新冻结。GitHub CI 状态以 PR 实际 checks 为准，冻结时尚未派发。
