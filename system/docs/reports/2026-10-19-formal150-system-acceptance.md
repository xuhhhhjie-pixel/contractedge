# ContractEdge 正式150系统审查与验收

更新：2026-10-19T21:13:04.899252+08:00。当前代码：0.2.2。

## 当前结论

系统已经具有流程图0–10的对应实现，正式150条公开输入与运行路由接入完成。真实开发案例RW-CAND-130的源码消费者、1474字节补丁、公开执行反馈及独立隐藏评分已实际贯通，隐藏评分为PASS。

这次真实后半验收使用开发者依据公开需求和公开values指定的补丁，不是自动模型的诊断/规划成功。自动模型v4已因900秒HTTP超时退出，未生成补丁；v5已在冻结0.2.2副本独立后台重跑；整个150条模型修复实验没有执行。不能把工程接通或单案例PASS写成150条实验成功，也不能据此保证模型诊断总是正确。

## 正式数据身份

唯一当前基准：`/home/xhj/contractedge/benchmark-ready-export/benchmark/ContractEdge-benchmark150-v1-20261019/RELEASE_ENTRY.json`。总计150，R1/R2/R3/R4/R5各30；包括当前发布清单内成员，不因旧reserve身份剔除。历史117/147及旧pilot只供参考，不能混入当前分母。

| 项目 | 实际检查结果 | 证据 |
| --- | --- | --- |
| 当前公开MethodInput/S0 | 150/150准备通过；不导入GT，不展开150源码树 | `outputs/benchmark150-adapter-v2-20261019/preparation.json` |
| 当前公开运行入口 | 150/150路由和发布pin校验通过 | `outputs/benchmark150-public-routes-20261019.json` |
| 当前关系GT接入 | 150/150可归一，全部未运行成员保留在分母 | `outputs/benchmark150-gt-adapter-final-20261019.json` |
| 当前评分入口实际执行 | RW-CAND-130独立score_case.py返回PASS | `outputs/benchmark150-independent-backhalf-score-20261019/score.json` |

150条均有行为评分入口。关系precision/recall是另一个指标：当前GT明确声明precision资格的7条、recall资格的5条；缺失声明保持unknown、散文关系保留原文，不虚构端点或资格。这不改变正式150条行为修复实验身份。

## 与图中阶段的对应

| 阶段 | 当前实现及进度 | 验收边界 |
| --- | --- | --- |
| 0 可信准备 | 冻结清单、成员、task/source哈希核对；150通过 | 没有修改正式包 |
| 1 MethodInput/S0 | 公共需求/输入、源码根、原始快照固定 | 缺少公开commit时用archive-sha256，不反填私有commit |
| 2 Requirement/O | 保留原文与来源，原子义务引用可校验 | 结构可追溯不等于语义提取总是完整 |
| 3 检索 | 范围解析、语法/词法单元、一跳、Top-N、有界扩展 | C/Java等词法回退明确标记，不宣称完整AST/全仓库召回 |
| 4 Diagnosis U/E/V | 角色/关系/违反/证据/不确定性检查与有界纠错 | 自动模型开发试跑尚未验收完成 |
| 5 Semantic ChangeSet C/P | EXISTING/NEW、依赖、P/保留/开放义务 | 模型意见不能充当隐藏oracle |
| 6 校验与修订 | 累计纠错历史、一次共享证据扩展与计划修订；全局token/时间/次数 | 超窗、超时、额度耗尽明确退出 |
| 7 消费与补丁 | 版本化plan接受、目标hash/作用域、字面编辑事务；真实1474字节补丁 | 新ContractEdge源码消费者承接TopoMen交换协议；旧部署/记忆调度器没有被冒称运行 |
| 8 公开执行 | 原始Broken补丁基线、S1副本运行、冻结launcher | 单案例真实检查；150路由通过不等于150原生运行都重跑 |
| 9 ExecutionFeedback | from/to快照、补丁hash、公开观察、待办、归因路由、跨轮保留清单 | 实际反馈保留UNKNOWN/pending；开发者确定性检查另存，不伪装模型复核 |
| 10 独立评价 | 私有评价独立进程；旧trace/current input/S0隔离 | 真实开发者候选PASS；尚无全150模型实验结果 |

## 本轮修复与审查

已修复累计诊断重试丢草稿/早期反馈、公开观察变量覆盖、长Helm输出尾部目标资源遗漏，以及单层PASS误当语义成功的风险。native请求消除重复schema/公开证据并为纠错留空间；发布范围中的Python/C/Java文本处理与归档根识别补齐。

独立代码审查随后复现并修复：计划补证据调用漏记耗时导致READY无法续跑；ChangeSet漏检输入上限；末次迟到响应仍READY及HTTP等待超过剩余时限；重新诊断后旧PASS细粒度P消失；前半run未执行次数上限。回归先观察失败，再验证修复。跨轮保留清单中的FAIL/UNKNOWN不会删除旧义务，新快照必须重新复核。

最终独立只读复核未发现Critical/Important残留。该结论不等于不存在任何缺陷，也不等于模型语义能力已验收。

验证命令：`PYTHONPATH=src python3 -m unittest discover -s tests -v`，174项全部通过。编译检查与`git diff --check`通过。额外真实两轮文件/子进程回归确认：新计划省略旧P时，系统保留pending并拒绝PUBLIC_REVIEW_COMPLETE。

## 真实开发案例结果

RW-CAND-130属于development。公开要求是两个cluster-gateway TLS Job继承根nodeSelector、tolerations、affinity，禁用multicluster时不生成目标Job。

开发者补丁只改允许的`templates/cluster-gateway/job-patch.yaml`；源自公开需求及公开trigger/control values。Broken公开输出缺失6项调度字段；修改后6/6匹配，禁用对照目标Job数为0。消费者记录S0→S1，发布工具导出相对原始Broken的补丁，并在独立输出目录评分。候选在读取隐藏结果前已选定，隐藏评分没有送回修复循环。

补丁SHA256：`c5006ae25d78c4ba5e16a2a105452fa94b00af187a3f8d5211085a7c4f492b49`。独立评分：PASS，reason_code=`ALL_EXPECTED_CELLS_MATCH`。

自动模型v1是中止的front-only探针，v2因冗余上下文/纠错预算退出，v3在响应前中断；均未记作成功。v4首个诊断调用900秒超时，以INFRASTRUCTURE_ERROR退出，补丁为空。v5在`outputs/benchmark150-development-closure-v5-20261019/`使用冻结0.2.2、单次2400秒/全局7200秒/12调用上限后台继续；最终非空候选选定后才自动独立评分，不回流隐藏结果。完成后会生成completion.json并更新CURRENT_STATUS.json；其结果须单独验收，不用开发者补丁PASS替代。

## 尚未验收的范围

- 自动模型完成一个真实案例的Diagnosis→ChangeSet→补丁→公开语义复核整链。
- 全150自动模型修复结果、成本、失败分布与独立行为分数。
- 当前平台之外的原生运行可移植性，以及未声明关系GT资格的人工裁定。

工作区保留原有未提交改动与历史输出。本轮没有合并、发布或覆盖冻结正式数据。

动态进度入口：`/home/xhj/contractedge-system/CURRENT_STATUS.json`。本报告是本次验收时点快照，后台新结果以该状态和completion.json为准。
