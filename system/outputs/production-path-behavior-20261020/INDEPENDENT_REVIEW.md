# 0.4.7 → 0.4.10 独立审查及 0.4.11 修复

按 requesting-code-review 技能进行一次独立、只读、新上下文审查；未调用付费模型、未读取隐藏断言或 Fixed。结论 With fixes：3 项 Important，0 项 Critical；审查者自行运行17项相关测试通过，但已有测试未覆盖以下反例。

1. **同一义务的历史绑定被较弱待办覆盖**：feedback.py将retained与pending按statement/requirement_refs建字典，pending覆盖retained后[owner, final]变为[owner]。0.4.11改成累计合并全部历史与新计划的behavior_check_refs/comparison_requirements，并在继续保留时同步；验证种类或场景冲突保留NOT_PUBLICLY_OBSERVABLE，不能自动PASS。
2. **候选文件改名使探测静默消失**：public.py每轮对候选重新select_probes，选择规则依赖eval.go存在。0.4.11在prepare的Broken上冻结每个工作区的探测集合，后续使用同一集合；候选破坏API会实际运行并产生PROBE_ERROR，不能悄悄NOT_RUN。
3. **非连续源码单元的定位包络虚报分支MODIFY**：adapter把所选单元起止行合并，validate只判断包络重叠。0.4.11逐个检查unit_refs对应实际SOURCE_SPAN路径和行区间。引用前后单元不能代替中间分支；仍不把位置一致当语义实现正确。

三项反例均先在旧实现真实失败（review-red.log），再改产品代码；review-green.log相关17项通过。完整工程回归316项测试、14项子测试通过，方法源码与配置在真实复测前冻结。新增辅助函数取证独立经过helper-red/helper-green及实际公开计划零HTTP回放；不属于审查者已审差异，不冒称其背书。

**stdout信任边界**：候选导入包可恶意在init中伪造输出并退出；探测代码冻结与只读挂载不能消除此同进程限制。已在探测boundary和证据origin明确标记“candidate code and probe share a process / not tamper-proof”，保留原始输出和程序摘要，继续与独立评分区分。当前范围未新增完整防恶意候选执行架构。

审查未判定：修复率是否提高、全部150行为覆盖、外部容器/WASM行为，以及历史dirty改动。审查发现的三项实现问题由回归验证，不由独立评分通过或失败代替。
