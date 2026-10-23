# 0.6.28：沿原失败轨迹补齐证据送达（2026-10-23）

## 版本及范围

承接GitHub集成分支 `codex/six-point-integration` c892215，继续执行 `docs/superpowers/plans/2026-10-22-formal-experiment-delivery.md` 的Task1—3。新冻结版本0.6.28，源码摘要 `af928f66f341c1414114fdf42468af809a6015f2c3570a67b49f9e5f35bb1ecd`。正式准入仍false；下述工程结果不代表新的独立修复成功。

## 确认的根因与修复

1. ACK原始保存请求表明，补证后客户端所属类型声明仍未进入最终模型上下文。Go解析器的调用引用不含方法接收者类型；按通用调用名字扩展时，其他资源的同名Update/sdkUpdate占据片段，未保留同包resourceManager字段类型。现在只在已定位方法的同包、已有范围声明索引中寻找唯一接收者struct，重新读取并核验当前文件SHA256；用有限正文连同可容纳的import前缀送达。找不到、歧义、文件/字节/角色预算不足和快照漂移均明确记录，不推断API行为、不增加编辑权限、不读取外部依赖。
2. 新声明送达后，真实旧响应驱动的输入投影又触发REQUIRED_SOURCE_NOT_DELIVERED：多个词法关联组都含必需项时，裁剪逻辑会整组移除必需项，即使组内还有可选片段。修复为优先移除可选片段；若连必需内容也超限，仍保留原来的容量失败与缺口判定。
3. 原ACK第1、4条计划均为changes=[]、planned_violation_ids=[V1]。此前“包含执行动作”的总结不准确；状态校验消息同样将二者混为一谈。本版保留拒绝条件，但分别指出非空planned_violation_ids和changes，不让模型针对不存在的编辑动作纠错。第5条新增CHANGES/空change_refs问题仍由保持项校验拒绝，不予自动修正或放宽。

## 实际验证

- 新增11个通用反例/正例测试；接收者测试首先4失败、状态反馈测试首先2失败、关联组裁剪反例首先失败，红绿日志均保留。
- 完整服务器回归：774 passed、22 subtests passed；未排除六个历史模块。依赖由`.venv-dev`安装，保留隔离回放能力。
- ACK输入投影：补入同包接收者后，重新诊断包含客户端声明；输入119471、输出预留16384，初诊输入限额120000不变。计划输入150084+16384，整体220000不变。执行请求及输出合计116127，非目标只读正文25711字节，局部限额48000不变。
- 同一只读旧响应投影保持Broken摘要不变。旧响应不是新方法的模型结果；执行投影使用原0.6.25失败计划作为输入载体，不能解释成正确计划或正确补丁。
- 五条已曝光开发案例RW130/KPT/KOPF/Provider Helm/ACK的首个请求，与GitHub集成版逐字一致；分别为98426/117447/86635/100016/117564输入上界。不是跨案例修复分数。

## 真实开发验证及配置

沿原46槽位规则重新登记0.6.28；当前只派发A/ACK/0/candidate，从原始Broken开始，不重用成功补丁。主预算仍12调用、2轮、Top12、一次补证及累计150元，历史费用和不确定预留保留。新配置v8显式启用require_match模型返回身份、兼容json_object模式以及镜像内真实CPU/内存准入；不是只复制功能却不启用。Docker原任务镜像与探测镜像不同，探测PASS不替代具体公开任务执行检查。

真实结果写入 `outputs/reliability-closure-20261020/runs/delivery-0.6.28-A-R4-ACK-CLOUDWATCH-2985-0-candidate/`；结束后独立评分，不能根据此文的工程通过提前填写PASS。其他45槽位保持未运行；若ACK失败，保留终态并按新公开轨迹定位，不自动重复本槽位。若通过，先核验无明确公开FAIL，再进入原登记A/重复/续修；B与正式150仍须后续门通过。

## 原计划剩余内容

Task1已有请求事实和输入投影证据；真实新诊断/计划是否使用新信息尚待结果。Task2先补已确证同包缺口，没有根据就不增加SDK搜索模块。Task3错误反馈已细分，仍需真实模型表现确认。Task4固定同版46槽位、已知故障重复与失败恢复未完成。Task5还需要全150静态资源核对、公开与评分进程的实际读取隔离、G0—G6最终准入聚合、干净完整执行和中断恢复演练。不能把一个工程改动或一条成功当作这些门完成。

证据目录：`outputs/github-update-review-20261023/`，重点包括REVIEW.md、sync-receipt.json、receiver-owner-red.log、required-context-red.log、plan-state-red.log、receiver-owner-focused.log、plan-state-green.log、v0628-full-tests.log、ack-original-plan-audit.json、cross-case-comparison.json及三阶段输入投影JSON。原实验、源码冻结和GitHub历史摘要均保留。
