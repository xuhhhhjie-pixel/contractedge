# 一次恢复后的前端生成模式连续性

公开证据：source057 KPT调用0为诊断length（16384 completion，其中15601 reasoning）；调用1按既有规则关闭thinking，正常stop且诊断READY；调用2 ChangeSet恢复enabled，length（16384 completion，其中11672 reasoning）。共享一次恢复额度已经耗尽，未进入编辑。旧终止规则符合当前策略，但prompt_version隔离使同一前端流程重新遭遇已观察到的输出饥饿。

最小改动：新增配置`planning_generation_recovery_scope`，合法值`prompt_version`（默认，旧行为）与`front_half`。选择后者时，只有既有推理占比、length及预算规则真正触发恢复，并收到非截断回答后，后续本次前端诊断/计划请求沿用disabled。记录最初恢复stage/prompt和CURRENT_FRONT_HALF范围。不跨案例、修复轮的新front调用或恢复进程继承；不改变已有执行器策略。

不提高一次截断恢复、格式纠错、12总调用、2语义轮、Top12、输出或上下文上限。未发生恢复、恢复仍截断、缺少可靠usage或非推理截断时，没有新模式传播。所有语义契约继续执行，disabled生成本身不证明诊断或计划正确。旧配置缺省字节行为保持。

验证：通用fixture复现“诊断恢复后计划又截断”→有界连续性通过；默认模式、全局预算、缺usage/非推理、失败恢复、重新诊断及新front复位等反例。保存source057公开需求/观察/源码和第一截断、第二完成回答，实际front流程捕获计划请求；旧版本enabled，新配置disabled，证据/消息/schema/输出额度一致。完成工程/隔离后才能登记真实验证；不重启source057失败。
