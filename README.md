# ContractEdge

真实云原生声明式部署与配置缺陷修复 Benchmark。当前冻结资源为 **R1–R5各30条，共150条**；R1另27条备用保留于原资源池，不进入本实验分母。

**GitHub发布准备中。** 仓库保存说明、成员索引与下载工具；完整实验包使用Releases分卷提供，待全部附件上传及核验后发布。

## Benchmark分类

| 类别 | 生产故障边界 | 正式案例 |
|---|---|---:|
| [R1](benchmark/R1/) | Helm chart 生成 | 30 |
| [R2](benchmark/R2/) | Kubernetes控制器调和 | 30 |
| [R3](benchmark/R3/) | 部署工件转换或生成 | 30 |
| [R4](benchmark/R4/) | 外部控制平面资源协调 | 30 |
| [R5](benchmark/R5/) | 运行时配置消费 | 30 |

五类是本项目的操作性分类，不是Kubernetes、Helm或某篇论文规定的标准。共同范围为“声明/事件 → 生产执行路径 → 被破坏的协调关系 → 可观察结果”。完整规范见[分类说明](benchmark/RULES_AND_TAXONOMY.md)。

## 下载完整实验包

```bash
git clone https://github.com/xuhhhhjie-pixel/contractedge.git
cd contractedge
python3 tools/download_benchmark.py --class R1 --dest ./datasets --extract
# 其余类别将R1替换为R2、R3、R4或R5；也可以使用 --class all。
```

GitHub Release单个附件必须小于2GiB，本项目较大的包会分卷。下载工具按发布索引获取各卷、校验SHA-256并合并，可选择安全解压。仓库首页的“Code→Download ZIP”只包含说明和工具，不包含多GB实验包。

## 实验内容与运行范围

每类完整包包含：公开Requirement与Broken源码、可编辑范围、独立私有GT/评分器、固定依赖和运行镜像、成员与分区、身份绑定校准证据。

支持 **Linux/amd64、rootless Docker、git、Python3.10+且支持tarfile data filter（推荐3.12+）**。Mac可下载保存；原生macOS/Apple Silicon执行未经本版本验收。具体CPU、内存、空间与超时以每案运行契约为准，部分Go构建需20GiB可用内存。

完整修复实验链为：公开任务 → 方法修改Broken → 导出patch → 独立隐藏评分 → 指标汇总。Agentless、SWE-agent、OpenHands等比较方法需要适配该任务/patch接口，可保留自身检索与编辑流程。完整包交给实验管理者；模型进程仅可访问public白名单和允许的源码工作区，不能读取evaluation/、GT、Fixed或历史修复。

公开EXECUTION_PASS只是观察程序完成，不等于隐藏语义PASS。不同案例的终点可为渲染对象、控制决策、转换工件、外部API映射或运行消费行为，不一律声称真实集群/云最终收敛。当前没有模型baseline结果。沿用精确身份绑定历史证据，不重复全量3+3。

## 版本、分区与来源

来源：`ContractEdge-benchmark150-v1-20261019`。每类以各自manifest为唯一成员入口；旧case状态用于历史追溯，不覆盖当前发布入口。继承仓库/修复家族分区及开发暴露记录；公开历史训练暴露UNKNOWN，不把全部150条称为未见盲测。

第三方源码和镜像的原始许可证随对应归档保留，见[来源与许可说明](THIRD_PARTY_NOTICES.md)。
