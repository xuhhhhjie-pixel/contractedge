# 公开检查绑定协议与阶段收尾

本次修订补齐真实官方 config-binding 单例暴露的计划与观察协议缺口。原单例已产生候选并完成独立检查和选择，三个公开检查 PASS，但语义 UNKNOWN；其结果保留，不能改写成修复成功。

此前计划 schema 只从 method_behavior_probes 枚举检查，只有 registered_public_checks 时会删除绑定字段；公开观察缺少 scenario，无法支持计划声明的场景。现在两类来源各自保留 producer、摘要、scope 和有限检查成员，通过只读 catalog 提供给计划与审核；重复完整检查 ID 在规划前拒绝。模型必须显式声明引用、coverage、理由与 scenario，系统不会为真实模型补绑定或覆盖声明。缺失声明仍为 UNKNOWN。

审核和反馈使用本轮 snapshot 与 capabilities，机械聚合保留完整 observation 集，避免漏掉同场景未引用的操作失败。selected、disabled 与覆盖三个有限场景的 stable 检查均由可信检查定义声明场景；六例开发面板从公开 scenario/assertion 定义生成 scope。直接调用 support_error 等低层接口且 capabilities=None 的 legacy 路径仍兼容旧 producer 记录，不能据此声称全部旧来源均经 catalog 身份核验。

当前开发身份为 architecture-public-check-protocol-20261025。新冻结、独立 acceptance binding 与 validation 对应本轮源码；旧 snapshot、旧红/绿及旧失败原样保留。新增协议测试覆盖两来源、重复 ID、缺失/无效 coverage、scope/snapshot/producer 不匹配，以及同场景未引用操作失败。原正例只补明确测试前提，不删原断言或缺声明反例。

验收采用当前 Linux 实验室服务器和既有任务 Docker socket，保留 2 CPU、2 GiB 内存及其他隔离边界。准确测试结果、日志摘要、实际 8 格恢复与四模式六例 24 格在同名 validation.json 中登记；portable 保留六项历史/native 排除，native 单独报告。开发 fixture 和固定 synthetic provider 结果用于工程验收，不是模型修复率或论文消融结果。

本阶段收尾后暂停继续优化。剩余工作是新冻结的有限真实模型复测、原 ACK 失败包的精确复现（包仍未找到）、论文实验及消融/泛化验证、新机器部署迁移验收；低层 legacy 无 catalog 路径若未来外部接入需单独迁移。formal_ready=false，本轮新增模型 HTTP=0，不启动 formal150 或隐藏评分。

可用入口与安装、isolated 演示、READY 续跑、恢复面板的命令见 system/README.md。使用者先执行 python scripts/verify_development_snapshot.py，再按当前 validation 核对验收范围。GitHub 合并与准确提交 CI 以实际 PR/main 状态为准。
