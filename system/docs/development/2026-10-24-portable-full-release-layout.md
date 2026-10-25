# 干净克隆运行与完整Release布局

当前方法0.6.38，正式准入false。本说明用于开发验证，不授权正式150条或消融实验。实际迁移记录见outputs/portable-real-development-20261024，当前结论以其终态为准。

## 必须保留的布局

CLI的public-root应属于同一冻结Release，不能只搬走public目录：

```text
<release>/
  RELEASE_ENTRY.json
  partition/member_groups.jsonl
  public/manifest.jsonl
  public/R1...R5/...
  tools/...
```

独立评分进程另外使用evaluation目录。它不属于需求、诊断、计划或执行输入。Release入口和分区是公开身份元数据；缺失或摘要不符时修复布局，保留分区及摘要校验。

## 安装与零调用预检

从Git仓库的system目录运行，相对配置路径按当前工作目录解析：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.lock -e .
.venv/bin/python -m pip check
.venv/bin/contractedge --help
.venv/bin/python scripts/test_portable.py
CONTRACTEDGE_TOPOMEN_SOURCE="$PWD/vendor/topomen" .venv/bin/python -m pytest -q tests/test_topomen_agent.py
```

可移植子集明确排除六个历史/原生模块；原生消费者在上述最后一条命令单列。Git交付的configs/development.example.json采用仓库自带TopoMen及.local账本/锁路径。服务器专用配置含本机绝对路径，迁移时使用相对路径示例并记录适配。按Release说明准备锁定镜像，保留CPU、内存、磁盘及runtime-check准入。

设置CE_BENCHMARK_ROOT为Release路径，CE_CONFIG为经确认的配置。以下调用现有适配器验证150条公开输入，流式检查归档，不展开源码、不调用模型、不读取evaluation：

```bash
.venv/bin/python - "$CE_BENCHMARK_ROOT" "$CE_CONFIG" <<'PY'
import json,sys
from pathlib import Path
from contractedge.dataset.benchmark import release_identity,read_members,method_input_from_task
from contractedge.dataset.admission import development_case_ids
from contractedge.contracts.validation import parse_method_input
root=Path(sys.argv[1]).resolve();config=json.loads(Path(sys.argv[2]).read_text())
identity=release_identity(root);development_case_ids(root/'public',config)
rows=read_members(root/'public')
for row in rows:
    value,_=method_input_from_task(root/'public',row['case_id'],release_id=identity['release_id'])
    parse_method_input(value)
print(json.dumps(dict(verified=len(rows),release=identity,private_materials_loaded=False,model_calls=0)))
PY
```

这只证明输入可解析、身份匹配；全部环境和评分器仍须分别验收。发现元数据缺失应在这里修复，不能关闭校验后付费尝试。

## 一条真实开发案例与独立评分

通过私有环境变量提供配置指定的API密钥，不写入Git、命令参数、配置JSON或日志。先确认个人费用上限和服务参数；示例固定计价用于保守预算，不是供应商账单。每次使用新输出目录，保留失败；INTERRUPTED和费用未知请求不得自动重发。

```bash
.venv/bin/contractedge repair-batch --public-root "$CE_BENCHMARK_ROOT/public" --tools-root "$CE_BENCHMARK_ROOT/tools" --config "$CE_CONFIG" --output .local/runs/dev-single --case-id RW-CAND-130 --max-new-cases 1
```

检查batch.json实际成员终态和trace。返回0不等于修复成功，批次可合法保留失败或中断。此命令只派发一个成员，其余149行NOT_RUN不属于已执行结果。

方法进程结束、候选固定后，独立进程执行：

```bash
.venv/bin/contractedge evaluate-repair-batch --evaluation-root "$CE_BENCHMARK_ROOT/evaluation" --batch-root .local/runs/dev-single --tools-root "$CE_BENCHMARK_ROOT/tools" --output .local/scores/dev-single
```

评分目录、隐藏细节、GT、Fixed不得回流诊断或修订。再次评分不得再次调用模型修改候选。

## 当前边界

本地提交8625d07在无硬链接的干净克隆、新venv、原四目录不可见条件下，通过837+17可移植子集及单列9项原生消费者夹具测试。账本迁移映射必须指向同一个实际账本，不能复制成新余额。

真实CLI迁移验证单列登记，不计46槽效果面板。迁移挂载最初遗漏分区和Release入口，都在模型请求前被拦截，失败记录保留；已改为完整150公开投影预检。Docker守护进程通道仍可用，挂载屏蔽不是G3强隔离验收。中断恢复、正式调度及完整G0—G6须分别通过。
