# 阶段 B：隔离运行与可信公开观察

基线为阶段 A 的 `6b5b5d3f2ce4eedeb1ceec0702165950dd9ba0ca`。本次候选隔离和公开检查面向三个已有合成开发例子；不运行正式数据实验、隐藏评价或自动新增模型调用。

## 已接入的边界

`contracts/runtime.py` 定义冻结 argv allowlist 和可信 RuntimeSpec。`execution/runtime_session.py` 只调用显式 Docker Unix endpoint，以最小客户端环境和新空 Docker config 运行；镜像固定 digest 且 pull=never。快照先复制到本次 owned staging，不直接绑定原始源码目录；拒绝路径越界、符号链接、额外挂载及不受支持的权限配置。

每个候选/observer 使用独立容器，核验实际 inspect 与内核 cgroup、UID、capabilities/no-new-privileges、只读挂载和有界 tmpfs。输出持续读取并分别/合计限额；超时或洪泛停止运行并按 ID 与 ownership token 清理。session token 在创建前持久化，CID 在 start/exec 前持久化；清理失败阻断执行 PASS，保留原始进程结果。

`execution/public_observer.py` 中候选 bridge 仅产生有限进程输出；独立可信 observer 解析实际输出，对冻结公开规范生成 selected/disabled/stable 检查。expected 不进入候选请求，候选自报 PASS、旧快照、重复/缺失检查或截断均不能升级为可信 PASS。required profile 与实际逐项观察绑定，不能单独改 profile 抹掉 FAIL，也不把 review UNKNOWN 自动提升为 PASS。候选保护集合来自固定公开 profile 的基线 PASS，不由模型是否生成保持项决定；观察/清理不完整不能晋级，不能因保护集合为空而自动 PROMOTE。

`development/public_demo.py` 和 `orchestration/repair.py` 接入 isolated runner、初始 Broken 检查、同版 READY 继承、全局剩余时间与强制公开 profile。host 候选执行保持 synthetic-only；本轮真实 provider 验收仅使用通过门槛的 isolated 路径。有限输出检查不证明内部函数实现事实或真实云环境收敛。

## 同版冻结与结果登记

计划新身份为 `architecture-b-runtime-observer-20261023-r2`。本说明先登记实现与验收协议，不填写未完成的测试数字或 PR 身份。最终 snapshot/validation 由本轮 root 汇总同版命令和真实终态后生成，历史冻结不改写。

必须登记以下独立范围：

- focused：既有契约/provider/预算/交付回归，加 runtime/control checker 单元与 isolated demo 的离线门禁；不依赖 Docker。
- actual runtime：RuntimeSession 宿主哨兵/网络/env/socket、非 root/只读、内核 CPU/内存/PIDs、tmpfs、输出洪泛、超时后代、镜像/路径及 ownership 清理反例。
- public panel：config-binding、reload-state、two-consumers 的实际 isolated 闭环，以及新进程 READY 继承。
- portable/native：保持原 portable 六项排除；vendored DefaultAgent fixture 单列，明确显式新 tree 的 PYTHONPATH。
- freeze/CI：新源码/配置/工具/面板文件摘要，显式和默认校验；CI 状态以实际 GitHub checks 为准。
- paid attempt：本阶段没有因文档或 CI 自动新增真实调用；若后续按授权登记真实单例，沿用原持久账本，单独记录版本、调用和终态。

候选、observer 与 close 的运行回执、原始有界输出、trusted-checks、observation、repair trace、调用账本和最终 patch/snapshot 均保留。失败和 UNKNOWN 不删除、不挪用其他版本结果；`formal_ready=false`。

## 可复现命令

当前入口和固定可信镜像见 [README](../../README.md)。建议最终冻结校验：

```bash
python scripts/verify_development_snapshot.py --snapshot docs/development/architecture-b-runtime-observer-20261023-r2.snapshot.json --require-current-bindings
python scripts/verify_development_snapshot.py
```

仓库现有脚本只提供 verifier，没有自动生成器。新快照需按 `ce.development_snapshot.v1` 构造新的精确文件映射，补齐配置、工具、面板及摘要；文件落盘并显式校验通过后才切换 CURRENT_STATUS 的当前指针。
