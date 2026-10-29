# 0.6.45 固定面板：CI 来源绑定修正

## 根因与范围

PR #17 的 GitHub Actions 实际 Docker job 在完成 RuntimeSession 与公开示例测试后，进入恢复面板时失败：`Frozen implementation/input differs: system/pyproject.toml`。工作流仍选择 `trial-isolation-acceptance-v044.example.json`，当前代码已为0.6.45；这次拒绝是旧来源绑定的有效校验，不是已通过的容器资源限制失效。

本修正在独立工作树进行。正在付费执行的原固定46槽继续使用原封存源、配置、工具及登记；本修正没有向正在运行的队列替换任何文件，没有新增方法版本，也不改变模型、预算、用例、探测、PASS/UNKNOWN或评分规则。

## 最小修正

- 保留原v044和v045绑定文件的字节，新增 `configs/trial-isolation-acceptance-v045-fixed46.example.json`，绑定当前完整方法源码、共同脚本及原有公开夹具/原生消费者输入。
- 将CI的恢复面板入口改为显式选择该新绑定。继续先校验逐文件来源与祖先，再实际运行；不在CI现场自动重写期望摘要，不关闭校验器。
- 新增独立development freeze，原方法源码摘要保持 `df177bfcc7b18a2715851a901e39f10e6ad3879790473fc5f120c66cd3e695df`。旧冻结保留；默认指向与本工作树相符的新配置/工作流冻结。

## 验收证据

合法参数的只读旧绑定回放稳定拒绝pyproject.toml；新绑定只读准备通过。初次准备遗漏必需运行参数、随后证据目录选在源码内，均被拒绝，记录保留；它们不是代码根因或有效模型实验。

初次本地8格已经通过，但新配置最初复用了现有v045路径。最终保留旧文件，改用独立fixed46名称，并重新运行同一8格。前一次仅保存为preliminary工程记录，不替代最终具名绑定的验收。

最终8格的真实隔离/恢复结果：PASS，14个会话、28个候选与独立观察器容器；清理及daemon不存在检查通过，新增模型HTTP为0。原生TopoMen与MODIFY-only各覆盖normal、after-applied、after-observed、before-selection。详见 `outputs/fixed46-ci-binding-20261025/actual-eight-summary.json`。

可移植测试和远程CI结果更新于同目录 `AUDIT.json` / `COMPLETION_AUDIT.md`；不能用本机8格代替GitHub完整CI成功，也不能将其计作46槽模型修复结果。formal_ready仍为false。

## 后续

先核验本机可移植回归及GitHub新提交的实际checks，再将该CI修正按独立PR交付。原46槽继续按原登记完成A、重复、续修与有条件的B；效果与组件缺陷只依据同版完整记录判断。本修正不声称修复成功率提高。
