# 当前上下文容量与证据缺口：实际原因和修改方向

**可以且值得验证扩容。已确认系统配置偏保守，并有真实案例因源码支持只超出826字节而停止。但并非所有EVIDENCE_GAP都来自截断：没有发现函数、依赖读取入口太晚、无效关系结构和模型推理失败也实际存在。**

本报告区分历史806e真实运行与最终e6784离线验证。当前安装仍为063d，最终候选e6784已完成同源工程核验，未安装或付费验证。原先的需求/已采用正文交接、成功响应来源及终态修复继续保留。

## 哪些环节确实需要改进

| 环节与代码 | 已核实的具体问题 | 明确的改进方向 |
|---|---|---|
| 输入预算：`tokenizers.py:8`、`runner.py:1424`、`model/admission.py:66` | 本地220000不是官网token窗口，而是UTF-8字节保守计数下的配置。Helm第三次本地输入预留197920，官网实际prompt_tokens61134；初诊另有120000上限。 | 同一源码比较总请求220000→440000、初诊120000→240000、model_context262144→1000000。继续以保守计数做发送前检查，实际费用按可信usage结算。 |
| 原始源码抽取：`source/units.py:13`、`source/parsers.py:281` | 普通摘录默认80行/16000字符。保证后续交接完整，并不自动恢复最初就被截去的函数尾部。 | 先使用已有max_lines_per_unit做独立取材对照。一个118行函数在256行配置恢复完整，仅增加1138字节；16000字符仍独立限制，不能声称所有函数因此完整。真正关键声明仍被截时，再做完整声明的整体准入。 |
| 名额和源码字节：`diagnosis/evidence.py:23,107`、`retrieval/candidates.py` | 初选canonical上限12；补查每批12条/24000字节、两批累计24条/48000字节固定在代码中。仅调top_n或模型窗口可能无效，甚至与canonical额度冲突。 | 保持一个共同容量来源；若需要扩大这一层，由同一配置向选择和采用边界传递，不能只改其中一处。当前纯窗口容量对照尚不改这层。 |
| 跨阶段支持准入：`runner.py:1348`的planning_source_draft | 总请求放得下，源码支持仍可能被独立100000字节上限拦下。 | 与完整请求分开测量，再比较100000→200000；不通过删除已采用正文凑上限。 |
| 材料必要性：`evidence.py:385`、`runner.py:1375`、`candidates.py:125` | 词法字段/reader提示会进入required_endpoint_ids和强制gap_role通道。Certmanager新出现的generated Solver字段，因普通词solver匹配而被提升为必达；此前从未发送或采用。 | 审查并最小化“词法提示→强制材料”的提升。设计仅需runner/candidates两处，不新增模型标签。真实required/anchor、已采用正文、已送达字段的precondition_review仍保留；不能直接移除Dapr类token契约保护。此项方案尚未实施。 |

## 真实案例告诉我们什么

- **Certmanager：有明确容量阻断。** 初诊从12项裁到4项。对最后未发送的历史草案，已用806e和e6784两份实际函数核对：100826字节源码支持、8个helper、9条前置条件。原100000字节上限失败；仅扩大请求窗口仍失败；提高support到200000后同一草案通过，保留全部已采用材料和Solver声明。这是准入改善，不是模型修复成功。
- **KPT4746：原缺口是发现能力。** 四段核心函数合计2716字节，在旧代码中从未入候选池，没有full-request裁减或显式截断。最终e6784已经补齐；原容量、单独扩大窗口和配套扩大窗口三个正常runner回放，实际prepared正文逐字一致，四函数均完整送达。扩大容量只增加余量。
- **Helm：容量压力与依赖访问缺口并存。** 旧版确实移除过一段未采用的可选isUpToDate，但其完整函数不含PlainHTTP；不能因此认定关键API被截断。新共享依赖读取层已在原额度内向诊断提供三条完整声明，共2579字节。
- **KPT4673：最终失败是关系结构。** 两个所需函数已送达，最后DIRECT_EDGE却使用两源端点且没有所需说明。扩大容量不会自动修正这种输出。
- **CloudWatch：入口正文没有反复丢失。** 更新入口及相关材料已送达，模型仍寻找原本需要新增的helper；还存在关系表达缺口。不能统一归因为上下文太小。

五个未执行案例的16个真实请求均核验了wire摘要、采用正文和原文件坐标，旧已采用记录完整保留；全部响应finish_reason=stop，没有这一批模型输出被截断的证据。可信用量结算已经启用，不能再次把预留之和当作最终占用。

## 已准备的实际验证

[容量配置草案](followup/context-capacity-comparison-draft/PROPOSAL.json)包括原配置、仅增请求容量、增请求并增support三组。业务源码和运行配置没有被改写。

下一轮付费面板准备在**同一冻结e6784源码、同一固定八案例**下，对比原配置与扩大后的请求/support配置，共16槽。只允许四个容量字段变化；每段抽取行数、12条/24KB策略、输出上限、模型标识、调用次数、累计token/时间/费用保持。这样先判断容量组合的整体收益，不同时把源码策略修改混进归因。抽取行数和词法强制化简化另作明确变化。新登记准备中，尚未派发，继续使用原累计¥100授权。

完整Certmanager三配置回放中，扩大初始窗口会改变候选组合，原第三次历史回复引用的INPUT1身份在新池中不再适用，第四次是格式纠正请求。这不能当作通过第二次补查或修复成功；必须以新真实模型对新材料的回答来验证。固定最终草案的100KB→200KB对照则已独立成立。

官网当前[规格](https://api-docs.deepseek.com/quick_start/pricing/)列出1M上下文。1000000为本地选择的保守工程整数，不猜测1M的精确二进制定义。[模型页](https://api-docs.deepseek.com/)当前说明deepseek-flash映射DeepSeek-V4.1-Flash-1017；历史调用只记录实际请求/响应名称，不反推其底层权重修订。

## 可核查证据

- [完整分层代码与真实用量](analysis/SOURCE_ACCESS_CAPACITY_LAYER_REVIEW.md)
- [五失败案例断点矩阵](analysis/ROUTING806E_FIVE_FAILURE_CAPACITY_MATRIX.md)
- [Certmanager实际门槛测量与推断校正](analysis/ROUTING806E_CERTMANAGER_GUARD_CORRECTION_AND_COUNTERFACTUAL.md)
- [KPT三容量逐字请求对照](analysis/SOURCE_ACCESS_CAPACITY_COUNTERFACTUAL.md)
- [完整源码抽取与词法必需性简化设计](analysis/SOURCE_EXTRACTION_LEXICAL_REQUIREMENT_DESIGN_REVIEW.md)
- [e6784工程证书](followup/source-access-admission-fix/ENGINEERING_VERIFICATION.json)与[独立核验](analysis/SOURCE_ACCESS_FINAL_CERTIFICATE_INDEPENDENT_REVIEW.json)

当前formal_ready=false。工程证书是同源码完整测试3347通过加原7个路径失败节点精确恢复通过，共3354个不同测试，4跳过、90项subtest；原完整运行exit1仍保留，不宣称单次全量全绿。native9项与六分支流程模拟通过，不能替代真实模型效果。
