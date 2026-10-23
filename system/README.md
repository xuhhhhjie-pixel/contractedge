# ContractEdge developer entry

当前开发身份以 [CURRENT_STATUS.json](CURRENT_STATUS.json) 的 `current_development.snapshot` 为准，入口为 [本轮开发冻结](docs/development/next-iteration-20261023.snapshot.json)。包版本仍保留 `0.6.27`；修改后的源码不能沿用原 0.6.27 方法摘要或历史效果。`formal_ready=false`，本轮仅验收开发工程，不执行真实模型 HTTP、正式数据实验或独立评分。

## 安装与当前冻结校验

Linux、Python 3.10 或 3.12，在仓库根目录执行：

```bash
cd system
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e .
export PYTHONPATH="$PWD/src:$PWD"
export CONTRACTEDGE_TOPOMEN_SOURCE="$PWD/vendor/topomen"
python scripts/contractedge_local.py --help
python scripts/verify_development_snapshot.py
```

`requirements-dev.lock` 使用 PyYAML 6，与原实验的 PyYAML 5.4.1 环境不同；`--no-deps` 表示显式选择开发依赖，并不复现历史正式实验环境。原生 TopoMen 运行源码和原 MIT 许可保留在 `vendor/topomen/`，此处接入的是源码修复消费者 `DefaultAgent`。

新校验器不修改冻结。它核对 `src/` 中全部 `.py/.json` 的精确文件集、逐文件摘要、配置与工具文件、固定测试面板及对应摘要；缺失、额外源码或字节变化均失败。`--snapshot docs/development/NAME.snapshot.json` 可检查指定历史开发冻结，但旧版本本就可能与当前源码不同。最早没有逐测试文件摘要的 v1 冻结仅能显式加 `--allow-legacy-unbound` 检查，结果标为 `LEGACY_UNBOUND`。修改源码后应生成新的同版开发冻结，不能改写旧冻结使检查变绿。

原 `DELIVERY_MANIFEST.json` 与 `scripts/verify_delivery.py` 保留原上传语义，仅用于原上传字节；当前开发源码已有有意差异，不应刷新该历史清单。`docs/development/` 中旧冻结和报告也保留各自范围。

## 零模型调用的公开示例

这些小型合成公开案例不需要数据下载、模型服务或 API 密钥，每次使用新的输出目录：

```bash
python scripts/run_public_demo.py --case config-binding --output /tmp/contractedge-demo-config
python scripts/run_public_demo.py --case reload-state --output /tmp/contractedge-demo-reload
python scripts/run_public_demo.py --case two-consumers --output /tmp/contractedge-demo-consumers
```

默认 fixture provider，沿用真实前半流程、READY 校验、执行消费者与公开反馈；它们只证明公开示例中的工程行为，不证明 benchmark 修复率。保存 READY 后可在新的进程继续：

```bash
python scripts/run_public_demo.py --case config-binding --save-ready --output /tmp/contractedge-ready
python scripts/run_public_demo.py --resume-ready /tmp/contractedge-ready --output /tmp/contractedge-resumed
```

保存目录包含公开冻结、READY trace、配置和前段调用账本；继续运行必须核对身份与预算。`--provider-config` 是显式选择其他 provider 的入口，使用前先检查配置与费用，本页和 CI 的默认命令均不启用模型 HTTP。旧 `configs/development.example.json`、`development-local-model.json` 及历史脚本包含外部环境假定，不能直接当作默认公开示例配置。

## 回归与实际运行能力

快速离线回归：

```bash
python -m pytest -q tests/test_contracts.py tests/test_model_provider.py tests/test_provider_output_identity.py tests/test_run_budget.py tests/test_money_budget.py tests/test_public_failure.py tests/test_development_snapshot.py
```

完整可移植开发子集包含原有 Docker 时序探测，必须有实际可用的 CPU 配额和内存限制；不能去掉限额或跳过失败。先缓存固定可信镜像，再将 `runtime-check` 实际协商的 Docker 环境仅用于本次测试：

```bash
export CONTRACTEDGE_RUNTIME_IMAGE='golang:1.26.8-bookworm@sha256:a688600ca24f8a4d3ca77f95b0dd40704a9fc787c826660eb7ba0b641b8b175d'
docker pull "$CONTRACTEDGE_RUNTIME_IMAGE"
python - <<'PY'
import json, os, subprocess, sys
from contractedge.execution.runtime_preflight import check_runtime, scoped_runtime_environment
report = check_runtime(verify_image=os.environ['CONTRACTEDGE_RUNTIME_IMAGE'], timeout_seconds=60)
print(json.dumps(report, sort_keys=True), flush=True)
if report['status'] != 'PASS' or report['runtime_verified'] is not True:
    raise SystemExit(1)
with scoped_runtime_environment(report['environment_overrides']):
    raise SystemExit(subprocess.call([sys.executable, 'scripts/test_portable.py']))
PY
```

检查实际 CPU/内存值后才判定运行能力通过。API 版本由目标 daemon 协商，不能把某台历史机器的 `DOCKER_API_VERSION=1.43` 写成所有机器的要求；无 Docker、权限不足、资源限制无效都保留失败。

`test_portable.py` 明确列出 6 个历史/native 排除模块，原入口与排除名单保留。单独运行 vendored native fixture 模块，并单独报告结果：

```bash
python -m pytest -q tests/test_topomen_agent.py
```

这两种范围均不等于原服务器的全量 662+22 验收。原有历史 fixture 和真实 ACK 原始公开包未在干净克隆中恢复；该历史重放限制不阻塞上述合成公开示例。公开观察不充分时保持 UNKNOWN，工程测试通过也不代表方法效果已验收。

[GitHub Actions](../.github/workflows/development.yml) 分别运行 Python 3.10/3.12 的契约/provider/预算/回放测试、Linux 实际 Docker 资源核对与原有完整可移植子集、单独 native fixture，以及三个公开示例和跨进程 READY 继续。所有 job 都不配置模型密钥；工作流文件存在和本地 YAML 检查不代表 GitHub 已执行成功。

## 历史与后续工作

`CURRENT_STATUS.json.historical_status.original_record` 保存旧状态和结果，包含原 0.6.27 ACK 的 `INVALID_PLAN/NO_CANDIDATE`、旧预算、冻结和曾经运行中的状态，均为历史佐证，不是当前开发下一步阻塞项。2026-10-22 的 [正式实验计划](docs/superpowers/plans/2026-10-22-formal-experiment-delivery.md) 记录当时准入设计；当前开发入口和下一步以 `current_development`、`next_actions` 及新冻结为准。

根目录 `benchmark/` 和 `tools/` 保留原数据工具，本轮不下载或读取数据、GT/Fixed、隐藏评分、凭据及运行目录。正式实验、真实模型验证与效果验收仍需独立授权和同版登记；旧结果不得混入新版本分母。
