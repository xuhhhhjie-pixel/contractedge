# Task5方法隔离执行边界 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans in the existing isolated checkout. Steps use checkbox syntax. This is the already-authorized delivery Task5 implementation detail, not a request for new model features or a new approval loop.

**Goal:** 在不改变模型修复逻辑的前提下，关闭方法进程的私有数据和Docker直接入口，保留真实公开执行、账本和有限恢复证据。

**Architecture:** 用现有public_runner的prepare/observe/export接口接受受限JSON通道，父侧保持原模型参数和财务账本。bwrap隔离方法进程及可信公开生产进程；后者执行原公开工具，独立评分不进入通道。

**Tech Stack:** Python3.10+标准库、现有bwrap、冻结Docker工具和已有ContractEdge/TopoMen。

**Spec:** docs/reports/2026-10-24-isolation-boundary-design.md；上位docs/superpowers/plans/2026-10-22-formal-experiment-delivery.md G3/G6。

## Global Constraints

- 不改变150数据、115/18/17分区、评分器或GT；不把新验证计入46槽。
- 原配置模型、220000上下文、120000初诊、12调用、2000000 token、9000秒、Top12、2轮及150元总账保持。
- 0.6.38源码与失败/成功记录保留；新执行器须单独冻结，不修改正在运行或已评分的冻结单。
- 无任意命令/文件读取接口；仅JSON，8MiB上限；只支持串行和当前Linux/本地Unix Docker环境。
- 执行已授权方案，由主工程师实施，不派实现Agent；一轮新的只读审查，Important/Critical一次修复并完整回归。

## Review Focus

- 目录或输出在检查后被链接/重命名时，父侧不得读写私有目标：Task2 FD反例。
- 子侧输入/Git被篡改时，公开执行仍使用可信输入/基线且不泄露私有文件：Task2/3反例。
- 结果已计费但本地响应丢失时，同ID回收一次，UNKNOWN不重发：Task1持久化和断连反例。
- 旧baseline和保存续修不得丢预算、类型、探测身份或走未隔离fallback：Task3兼容回放。
- 生命周期中断留下容器、部分补丁或不完整评分时不得宣布完成：Task4真实/模拟停止恢复。

### Task 1: JSON通道及持久化

**Files:** create scripts/isolated_protocol.py, tests/test_isolated_protocol.py.
**Interfaces:** RpcClient.call(op:str,payload:dict)->dict；Journal.execute(id:str,identity:dict,effect:Callable)->dict；JsonRpcServer公开固定dispatch。只缓存完成结果，未知调用不重发。
- [ ] 写固定操作/帧大小/认证/换输入同ID/完成取回/PENDING不重发/敏感值不返回测试，运行看到预期失败。
- [ ] 实现标准库协议与日志，持久化先PENDING后DONE，保留错误，不暴露任意执行。
- [ ] 相关测试及已有预算测试通过后提交。

### Task 2: 只读快照和安全发布

**Files:** create scripts/isolated_paths.py, tests/test_isolated_paths.py.
**Interfaces:** SafeRoot(root).clone_source(relative,destination,limits)->manifest；publish_dir(stage,relative)；publish_bytes(data,relative)；相对路径、目录FD、O_NOFOLLOW。
- [ ] 写越界/链接祖先/特殊文件/输入硬链接污染/安全原子发布及快照完整性反例，先RED。
- [ ] 实现最小文件操作，不读子侧Git/输入作为可信基线，不修改其他目录。
- [ ] 相关和原候选守卫测试全部通过后提交。

### Task 3: 命名空间与公开执行代理

**Files:** create scripts/isolated_public_worker.py, scripts/run_isolated_method.py, scripts/run_reliability_worker_v10.py, tests/test_isolated_method.py; keep worker_v9 immutable.
**Interfaces:** ProxyProvider及ProxyPublicRunner实现原complete_json/request_overhead/request_metadata和prepare/observe/export接口。公开worker执行父侧固定配置和同版BenchmarkPublicRunner；源副本、探测缓存保持同一生命周期。原生DefaultAgent保持。
- [ ] 写无网络/PID/私有路径/socket canary、非法通道和合法公开操作测试，先RED。
- [ ] 实现系统目录白名单、无密钥方法环境、受控provider和公开生产；记录原/派生配置及实际生产身份。
- [ ] current及baseline接口、保存续修政策和全轨迹零HTTP回放通过；不增加调用/编辑次数。

### Task 4: 资格、恢复和冻结

**Files:** tests/test_isolated_recovery.py，独立outputs/isolation-boundary-20261024 receipts，当前状态/交付文档。
- [ ] 先测试请求前退出、请求未知、缓存响应取回、部分写入拒绝/受控回滚、独立评分恢复及本例容器清理。
- [ ] 跑必要完整回归，保留所有失败日志；一轮只读审查后修复实际Important问题并全套验证。
- [ ] 单次预登记真实开发验收（共享150元账本）后绑定全部摘要；未完成项仍拒绝准入。
- [ ] 最终协议稳定后单独冻结并继续原46槽；不迁移历史PASS、不开放正式150或未满足条件的B。
