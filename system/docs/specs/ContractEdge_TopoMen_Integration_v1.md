# ContractEdge 第7步真实TopoMen接入规格

用户授权：先按真实源码审查，再合理接入；上传内容不作为必然正确的设计依据。采用设计梳理、实施计划与TDD流程；用户已明确委托按审查结果执行接入。

## 审查依据与选择

TopoMen源码44个Python模块、13901行，版本1.1.2。原生TopologyScheduler接收BuildTopology并生成Dockerfile，判定build/run状态，不接收ContractEdge的U/E/V/C/P语义计划。备选一：强行改造Docker调度器，目标与正式数据多语言/Helm验证不匹配；备选二：只沿用本地消费者并改名，不能形成真实集成。选择三：实际调用TopoMen的DefaultAgent.run/query/execute_actions循环，使用符合原生Model/Environment协议的适配器，模型产生字面编辑，受限环境原子应用，正式公开launcher继续负责第8步。

`contractedge.topomen_plan.v1`是ContractEdge自定义交换格式，并非上传TopoMen原生协议。完整Docker/记忆部署能力不等同源码编辑任务，不启动其二次部署诊断或共享记忆。

## 必须满足

- 正式150冻结数据身份不变；只给执行器公开需求、已验证plan、允许目标及公开反馈。
- 真实TopoMen源码路径及哈希显式绑定，导入错误/源码漂移明确失败，不静默退回自建消费者。
- actual_agent_class为topomen.collaboration.base_agent.DefaultAgent；记录实际模块路径/源码身份、模型调用、原生轨迹和Submitted退出。
- Model桥接原有BudgetedProvider，执行阶段最多一次模型调用，不重置token/时间/次数预算。
- Environment只接受一个结构化apply_edits动作，不提供shell；执行前后检查plan/snapshot/path/hash/依赖并沿用事务编辑。
- 外层ContractEdge控制诊断/计划/执行反馈循环，TopoMen不能自行改需求、扩大范围或另起部署任务。
- 隔离TopoMen全局配置路径，不加载机器全局.env；保存轨迹不导出环境变量/密钥。
- Python3.10兼容修复只把datetime.UTC改为timezone.utc；保留原始文件和失败测试记录。
- 默认实验配置使用真实TopoMen Agent；原本direct源码消费者作为显式可选后端保留，不冒充TopoMen。
- 真实正式开发案例通过模型接口回放公开确定性补丁、实际TopoMen Agent、真实公开launcher和独立隐藏评分。回放属于集成验收，不计为自主LLM成功。

## 验收

原始TopoMen Python3.10测试9收集错误→兼容修复后全suite/ruff。ContractEdge全suite与新增真实Agent回归：成功修改、错误/漂移拒绝且原文件不变、共享预算拒绝、必须经实际DefaultAgent.run、序列化轨迹无shell/全局配置。当前开发案例RW-CAND-130验收实际APPLIED/公开行为与独立score，保留原始Broken基线和选择后评分边界。
