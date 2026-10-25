# 公开检查协议验收工具 r2

首次 actual8 在候选、独立观察均完成后，被验收工具硬编码的旧 changeset.v1.1.10 提示版本拒绝。当前带检查 catalog 的请求正确使用 changeset.v1.1.12；验收工具仅将这个有限 fixture 的预期值更新为新值，继续逐项核对三次实际调用，没有放宽序列或增加人工 review 响应。

第一次失败记录和 architecture-public-check-protocol-20261025 冻结保留。r2 运行源码、公开检查、固定 fixture 与首版逐字节相同，仅更新验收脚本及其配置/文档身份；本轮最终验收绑定 r2，不覆盖旧 snapshot。完整结果见 architecture-public-check-protocol-20261025-r2.validation.json。

协议范围、legacy 无 catalog 边界和未完成研究项见前一修订说明。新版本尚无真实模型效果成绩；先前真实单例 semantic UNKNOWN 保持，formal_ready=false。按用户要求，提交合并和验收完成后暂停后续优化。
