# ContractEdge

真实云原生声明式部署与配置缺陷修复 Benchmark。历史完整发布包含 **R1–R5 各 30 条，共 150 条**。R1 另有 27 条备用案例，保留于原资源池。

## 当前 v3.3 验收版

| 类别 | 行为与 GT 双指标合格 | 当前发布范围 | 入口 |
|---|---:|---|---|
| R1 | 29/30 | 合格子集（v4.2 冻结） | [成员与资产](benchmark/R1/README.md) · [发布说明](benchmark/R1/RELEASE-NOTES-v4.2.md) |
| R3 | 30/30 | 完整类别 | [成员与资产](benchmark/R3/README.md) · [Release](https://github.com/xuhhhhjie-pixel/contractedge/releases/tag/r3-v3.3-20261125) |
| R4 | 27/30 | 合格子集 | [成员与资产](benchmark/R4/README.md) · [Release](https://github.com/xuhhhhjie-pixel/contractedge/releases/tag/r4-v3.3-subset-20261125-r3) |
| R5 | 28/30 | 合格子集 | [成员与资产](benchmark/R5/README.md) · [Release](https://github.com/xuhhhhjie-pixel/contractedge/releases/tag/r5-v3.3-subset-20261125-r3) |

按类别页的公开输入覆盖包、成员清单和说明取得验收版。当前子集仅纳入清单所列案例。管理员评价和 GT 材料由实验管理者按同一版本提供。技术验收采用 Codex 辅助审查、自动校准和单一维护者授权。

历史完整实验包和历史成绩用于复现。下载脚本的默认发布标签为 `benchmark-v1-20261019`。 [历史完整 Release](https://github.com/xuhhhhjie-pixel/contractedge/releases/tag/benchmark-v1-20261019)

## Benchmark分类

| 类别 | 生产故障边界 | 历史登记成员 |
|---|---|---:|
| [R1](benchmark/R1/) | Helm chart 生成 | 30 |
| [R2](benchmark/R2/) | Kubernetes控制器调和 | 30 |
| [R3](benchmark/R3/) | 部署工件转换或生成 | 30 |
| [R4](benchmark/R4/) | 外部控制平面资源协调 | 30 |
| [R5](benchmark/R5/) | 运行时配置消费 | 30 |

五类是本项目的操作性分类，不是Kubernetes、Helm或某篇论文规定的标准。共同范围为“声明/事件 → 生产执行路径 → 被破坏的协调关系 → 可观察结果”。完整规范见[分类说明](benchmark/RULES_AND_TAXONOMY.md)。

## 下载历史完整实验包

```bash
git clone https://github.com/xuhhhhjie-pixel/contractedge.git
cd contractedge
python3 tools/download_benchmark.py --tag benchmark-v1-20261019 --class R1 --dest ./datasets --extract
# 其余类别将R1替换为R2、R3、R4或R5；也可以使用 --class all。
```

GitHub Release单个附件必须小于2GiB，本项目较大的包会分卷。下载工具按发布索引获取各卷、校验SHA-256并合并，可选择安全解压。仓库首页的“Code→Download ZIP”只包含说明和工具，不包含多GB实验包。

## 实验内容与运行范围

每类完整包包含：公开Requirement与Broken源码、可编辑范围、独立私有GT/评分器、固定依赖和运行镜像、成员与分区、身份绑定校准证据。

支持 **Linux/amd64、rootless Docker、git、Python3.10+且支持tarfile data filter（推荐3.12+）**。Mac可下载保存；原生macOS/Apple Silicon执行未经本版本验收。具体CPU、内存、空间与超时以每案运行契约为准，部分Go构建需20GiB可用内存。

完整修复实验链为：公开任务 → 方法修改Broken → 导出patch → 独立隐藏评分 → 指标汇总。Agentless、SWE-agent、OpenHands等比较方法需要适配该任务/patch接口，可保留自身检索与编辑流程。完整包交给实验管理者；模型进程仅可访问public白名单和允许的源码工作区，不能读取evaluation/、GT、Fixed或历史修复。

公开EXECUTION_PASS只是观察程序完成，不等于隐藏语义PASS。不同案例的终点可为渲染对象、控制决策、转换工件、外部API映射或运行消费行为，不一律声称真实集群/云最终收敛。当前没有模型baseline结果。沿用精确身份绑定历史证据，不重复全量3+3。

## 版本、分区与来源

历史完整包来源为 `ContractEdge-benchmark150-v1-20261019`。v3.3 验收版以对应 Release 清单确定成员与分母。各版继承仓库／修复家族分区及开发暴露记录，公开历史训练暴露为 UNKNOWN。

第三方源码和镜像的原始许可证随对应归档保留，见[来源与许可说明](THIRD_PARTY_NOTICES.md)。

## ContractEdge 修复系统（开发交接版）

当前系统源码、测试、TopoMen运行源码、配置和设计文档见 [system/](system/README.md)。请先读 [正式实验准入与最终优化实施计划](system/docs/superpowers/plans/2026-10-22-formal-experiment-delivery.md)。系统0.6.27尚未通过正式实验准入；历史开发成绩不代表150条正式结果。Benchmark冻结资源与系统开发状态分别管理。
