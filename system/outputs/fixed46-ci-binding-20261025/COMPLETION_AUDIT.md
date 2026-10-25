# CI来源绑定修正：本机验收通过，远程待验收

原GitHub job因仍使用v044绑定拒绝当前v045 pyproject.toml；其此前实际隔离测试已通过。原v044/v045配置文件字节保留，新增v045-fixed46配置并更新CI引用。方法源码116文件、摘要df177bfcc7b18a2715851a901e39f10e6ad3879790473fc5f120c66cd3e695df保持不变。

- 合法旧绑定零调用回放拒绝、新绑定只读准备通过。
- 新具名绑定实际8格PASS，14会话、28容器，清理和daemon消失核验通过；新增模型HTTP为0。
- 可移植子集1734项、86子测试通过，明确排除6个历史/native模块；不冒称完整1767项复测。
- development freeze验证PASS。新提交的GitHub实际checks尚未完成，不能声明远程CI已通过。

修正使用独立工作树，不改变正在执行的原固定46槽。8格及子集均为工程证据，不计入46槽模型效果，也不表示正式150准入。

初次8格用新内容临时复用旧v045名称，现已恢复旧文件并按最终新名称完整重跑；preliminary记录只作过程记录，不替代最终8格。最初两次不合法计划命令的拒绝日志也保留，不当作来源回放通过。

详见docs/reports/2026-10-25-ci-source-binding.md、AUDIT.json、actual-eight-summary.json及portable-final.log。formal_ready=false。

远程第一次重验：来源绑定及真实恢复面板已通过；可移植1732项、86子测试通过，2项命名空间测试因GitHub运行器缺少bwrap失败。v2仅补齐工作流必需的bubblewrap安装，不排除测试、不弱化隔离；默认冻结相应更新，待新提交实际CI复验。
