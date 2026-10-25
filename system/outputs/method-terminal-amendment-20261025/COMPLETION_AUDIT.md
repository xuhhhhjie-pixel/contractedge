# 原固定开发面板 A 组收束审计

当前0.6.45方法的A组7案、14个配对尝试全部关闭并经协议验证。候选独立PASS 5/7，基线3/7。候选新增KOPF、Provider Helm、ACK三个PASS；Cert-manager出现基线PASS而候选FAIL；Alertmanager双方FAIL。候选总数较高不能抵消明确回退。

原登记46项保留，余下32项记为NOT_RUN_PREREQUISITE_FAILED。重复、续修和B未开启；当前formal_ready=false。原评估器返回PENDING（登记尚未执行完），同时behavior_gates_passed=false和delivery_effect_gates_passed=false；本审计明确当前版本已知效果门未通过。

|案例|基线独立结果|候选独立结果|候选公开判断|
|---|---|---|---|
|RW130|PASS|PASS|PASS|
|KPT|PASS|PASS|UNKNOWN|
|KOPF|FAIL|PASS|PASS|
|Provider Helm|FAIL|PASS|UNKNOWN|
|ACK Cloudwatch|FAIL|PASS|UNKNOWN|
|Alertmanager|FAIL|FAIL|UNKNOWN|
|Cert-manager|PASS|FAIL|UNKNOWN|

Alertmanager候选在计划补证后仍NEEDS_EVIDENCE，未产生编辑；Cert-manager候选以EVIDENCE_GAP退出，未产生编辑。独立评分均对原候选执行并保留。当前并无证据将它们解释为评分基础设施错误。

此前两个父侧协议缺陷（非零评分退出码误分类、合法EVIDENCE_GAP终态遗漏）已经通过受限兼容解释闭合，原回执保持不变；未再次调用模型改写原结果。资格工程验证1812项及91个子测试、实际隔离8格和双方公开沙箱通过，仅证明其明确工程范围。

后续先离线修复通用断点，验证关键证据在最终请求中共同存在，再登记最小真实验证。方法源码变化后使用新冻结身份，旧14项只作为对照，不继承效果成绩。完整逐项身份和32项未运行原因见A_EFFECT_AUDIT.json；原始结果及证据摘要见A_RESULTS.json。
