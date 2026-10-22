# 执行记录

基线：0.4.3，62文件已保存baseline/src；tests也保留。原工作区有大量历史未提交内容，仅修改本轮相关文件，不清理或重置。

上轮进度分类：进展——已完成源码审查、真实轨迹核对以及零调用负向复现，形成可执行改进依据。

Ruling: 用户已明确要求依据附件判断后行动，附件即上一轮完整设计建议；在此授权范围内落地实施计划，不重复询问许可。保持原架构与调用/评分隔离边界。代价：实现细节由工程判断承担，并用负向测试和最终审查约束。

任务1开始：观察能力与义务接收门禁，测试先行。

任务1 RED→GREEN：构建状态、结构冒充行为、缺失种类、错误场景及缺失/矛盾比较不再解除义务；正确类型证据保留。producer只标记自身解析的渲染结构，候选伪造JSON不升级证据种类；新元数据贯穿输入/诊断/反馈Schema。旧记录默认证据能力不足，保留原文不重标。
任务2 RED→GREEN：目录+同缺口查询、目录边界和文件/单元预算；gap池逐文件交错，避免前文件耗尽；宽泛同词命中不当作其他路径缺口覆盖。实际ACK/Alertmanager第一响应零HTTP回放取得生产实现/dispatch。
任务3 RED→GREEN：首次计划即使其他合同错误，独立合法且输入身份绑定的缺口也可在唯一修订前取证；校验错误保留。安全未知响应不重放，两次计划额度不增加。
任务4 RED→GREEN：实际literal edits与semantic action/intent/依赖/P/V对应保存在semantic_edit_trace；明确不证明语义忠实。已有模型调用中补充原文条件/对象/保持及已发现相关分支要求，没有新增Agent。
Ruling: 旧测试的公开模拟执行器必须真实声明其结构观察类型；未知旧记录不作兼容放行。一个测试专门覆盖连续两次诊断纠错，其原默认24000预算被更丰富Schema耗尽，改为该fixture显式40000，生产配置/总预算不变。代价：协议测试输入更大，预算边界仍由专门测试覆盖。
独立审查发现比较FAIL被误降UNKNOWN及无关等值字段绕过目标，已用三个RED回归复现。修复为事先声明comparison_requirements并核对全部字段；真实DIFFERENT可以支持FAIL。字段绑定仍是计划语义主张，不能冒充独立答案。

独立全范围审查完成：无Critical，4项Important（比较FAIL、必要字段覆盖、缺status被误归基础设施、跨gap符号合并）均已复现并修复；额外补强同义务跨轮不得丢失场景/比较绑定，bounded实际edit文本进入复核上下文且不是运行时证据。真实查询回放新增捕获完整第二请求正文和prompt_version。
用户后续授权清理xhj其他目录腾空间；仅移除经核实可再生成缓存/临时内容，保留实验身份材料。用户强调效果优先，调整具体工程额度必须有瓶颈证据；未将该表述解释为无上限付费，80元总账继续生效。

真实0.4.4 RW130：1474字节真实补丁、公开执行成功；复核P1/P2由于资源总索引无单一resource_identity被错误拒绝，即使同时引用正确Pod记录并有6项等值比较。原结果完整保留在deepseek-acceptance-single-v1-20261020，未用隐藏评分调参。
新增真实边界RED→GREEN：额外总索引不得否决另一个匹配场景/资源的精确记录；比较仍只能绑定符合要求的证据，只有错误资源的记录仍UNKNOWN。修复后0.4.5全套286项+14子项通过；同一原始公开资料离线回放得到PUBLIC_REVIEW_PASS，但不把该回放冒充新完整实验。0.4.5新单条已另起固定方法运行。

0.4.5有限失败验证终态：KPT在只读thirdparty目标被消费端拒绝后NEEDS_EVIDENCE、无候选；ACK真实目录扩展已看到delta/manager/tags/sdk，但长函数80行前缀不足，最终EVIDENCE_GAP、无候选。两者都没有伪装为成功，也没有隐藏评分反馈调参。
针对真实公开原因继续优化：先在隔离副本实现统一edit permission规则，过滤计划可编辑target selectors但保留完整只读诊断；同规则在计划验证/修订及消费端执行。3项RED→GREEN和KPT原计划离线验证通过。待旧批次终态后已晋升，未改进行中的冻结源码。
Ruling: 按用户“有些预算不必限制、质量优先”的指示，有证据地放宽单个已定位函数的80行前缀限制，加入独立max_gap_declaration_lines默认512；字符/文件读取/最终完整请求预算保留。扩展正文有新ID、原ID关联、真实摘要与truncated状态。原20行尾部窗口测试显式设置max_declaration_lines=20，继续验证严格窄窗口通路。代价：单个相关函数可能消耗更多上下文，最终请求仍会裁剪并保留审计。
0.4.6根源码全套290项测试+14子项通过，实际最新ACK/KPT第一诊断与Alertmanager历史第一诊断回放完成，保存完整第二请求及expanded_from_unit_id/truncated等信息。尚待冻结当前版本后的真实验证，不以离线回放当作行为成功。

0.4.6冻结摘要d132bd282545c0f21067f90dcb3db5e80d17bad99b7ba9bbb906e4d742c4590b。根源码291项测试+14子项通过。独立窄审查覆盖新增scope/长声明/排序：无Important/Critical，另独立运行15项+3子项通过。RW130最终新单条4次调用、1486字节补丁、公开闭环，独立PASS；另有公开原值六字段+关闭分支检查PASS。KPT/ACK最终版本复测仍在15532会话进行中。
保留边界：比较覆盖只对计划明确声明的绑定成立；义务/字段/分支选择及语义相关性仍是模型主张，未被字段或能力标记自动证明。公开模型复核依旧是不确定信号，独立评分不可省略。

## 0.4.7 known-return recovery allowance
0.4.6 KPT extension returned length:16384 completion,15120 reasoning; recovery yielded complete JSON with production-path errors but consumed the single format correction. New opt-in separates one known-return reasoning-starved recovery per front-half pass from contract correction. Shared12call/money80/token/time limits remain; default compatibility retained; no unknown transport replay. Isolated15tests+4subtests green after2of3newtests RED. Current0.4.6failure batch terminal before root promotion; KPTINVALID_OUTPUT andACKEVIDENCE_GAP, independentNO_CANDIDATE. Final bounded0.4.7development regression selected before calls, not formal150.

## 最终交付状态
0.4.7摘要9e9290382bc7f47f3931f600bdfa180794e8e5b25bf19adccd64cbb7a47b1293；294tests+14subtests PASS。最终预选RW1305调用1474B、公开闭环、独立PASS；KPT6调用1411B、公开构建PASSED、6项UNKNOWN保留、独立SEMANTIC_FAIL；两条均未触发截断恢复。0.4.6 ACK3调用无候选仍EVIDENCE_GAP，单列历史不混算。候选SHA与批次/评价身份已核对；独立评分pending0，148NOT_RUN保留。本轮真实结果不证明普遍修复率提升。费用上界22.624222/80元（含2笔历史未结），剩余57.375778元；无正式150/消融。源码最后冻结后未修改；所有实验终态。
