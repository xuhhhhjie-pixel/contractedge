# 固定版本核验与协作方式

当前分支为`fix/four-step-consolidation-20261104`，草稿PR为[#33](https://github.com/xuhhhhjie-pixel/contractedge/pull/33)。生产候选标签固定为`system-15141-fourstep-candidate-20261104`；修改前对照标签为`system-e6784-pre-consolidation-20261104`。当前状态以[STATUS.json](STATUS.json)为准，不把文档更新当作新的方法版本。

## 核验克隆得到的生产源码

在仓库的`system`目录运行以下只读命令。它核对全部`.py/.json`的精确文件集合、每个文件的SHA-256以及总摘要，不依赖服务器绝对路径：

```bash
python3 - <<'PYCODE'
import hashlib, json
from pathlib import Path
root = Path.cwd()
proof = json.loads((root / 'docs/implementation/20261104/ENGINEERING_VERIFICATION.json').read_text())
actual = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
          for p in sorted((root / 'src/contractedge').rglob('*'))
          if p.is_file() and p.suffix in {'.py', '.json'}}
assert actual == proof['source_files'], 'Production files changed'
digest = hashlib.sha256(json.dumps(actual, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
assert digest == proof['source_sha256'], 'Source digest changed'
print(len(actual), digest)
PYCODE
```

当前应输出123个生产文件和摘要`15141db36fde55f526e18be0b288a24548b0a91a6c5ce40b7763603aaa4636f4`。这只证明收到同版源码，不能代替测试或真实效果验证。

## 检查与运行的边界

按`requirements-dev.lock`准备独立Python开发环境，保留`vendor/topomen`源码。统一portable入口为`python scripts/test_portable.py`，脚本显式列出六个历史／原生模块排除；不能删失败或新增排除以得到通过。原生TopoMen依赖、Docker运行检查和六组流程模拟另行核验，具体执行环境及原始回执摘要见[ENGINEERING_VERIFICATION.json](ENGINEERING_VERIFICATION.json)。本轮证书是指定服务器环境上的结果，尚未声称干净机器安装流程已经重新验收。

真实实验使用另行获取的公开Benchmark输入和固定运行镜像；方法仅访问公开材料，独立评分在方法结束后执行。Git不保存密钥、费用账本、原始模型请求、私有评分实现、运行工作区或下载缓存。当前公开对照协议见[REAL_PANEL_PROTOCOL.md](REAL_PANEL_PROTOCOL.md)，补充样本名单已在结果产生前固定，见[PRESELECTED_PUBLIC_CHECKS.json](PRESELECTED_PUBLIC_CHECKS.json)。这些是可审查协议与身份，不冒充一个无需配置即可复现实验的发布包。

完整32槽验证已经结束，见[最终报告](FINAL_IMPLEMENTATION_REPORT.md)及[逐槽公开汇总](REAL_VALIDATION_SUMMARY.json)。同容量新旧各4/12，候选保持开发状态；不要据工程回归通过直接升级正式实验。

## 后续协作修改

真实验证发现的具体入口、修改方向及正反例要求见[剩余失败与后继修改](REMAINING_FAILURES_AND_NEXT_CHANGES.md)。该页明确区分已实现代码和待做的继承版本。

1. 从候选标签或当前分支另建工作分支，先标明[问题编号](ISSUE_CLOSURE_MATRIX.md)、失败反例、改动范围及保留的约束。
2. 保持Diagnosis找原因、ChangeSet表达修改与保持要求、TopoMen执行修改的分工。优先复用已有入口，不增加案例名称判断或另一套标签账本。
3. 已采用／仍被引用的材料正文保持完整，来源与身份绑定；容量不足明确报告，不通过暗中删正文或放行非法关系继续。
4. 审查真实diff，运行所改边界的正反例及规定工程检查。模型收益必须另做同源码、同配置、固定分母的真实对照，保留负结果。
5. 现有冻结面板不改写、不替换失败槽；继承版本使用新摘要和新登记。通过工程检查但真实退步的候选继续保留为开发版本，不能直接宣称正式就绪。

本轮源码改动为12个生产文件，346行新增、164行删除。`repair.py`净减少70行，但部分职责移入既有`feedback.py`和`transitions.py`；总体净增182行，不能宣称整个系统代码规模缩小。简化的是重复控制职责和错误强制规则，是否改善性能以真实对照为准。TopoMen自主权限本轮保持原设计，只有剩余共同瓶颈确实在执行后，才考虑独立验证有限搜索、读取和编译能力。
