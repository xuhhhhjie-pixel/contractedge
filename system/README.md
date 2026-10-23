# ContractEdge developer entry

当前开发身份以 [CURRENT_STATUS.json](CURRENT_STATUS.json) 的 `current_development.snapshot` 为准；待冻结变更见同一对象的 `status` 与 `planned_snapshot`。包版本仍为 `0.6.27`，方法身份由同版源码、配置和测试面板摘要确定，不能沿用历史版本效果。`formal_ready=false`，不运行正式数据实验或隐藏评分。阶段 B 的范围与验证登记见[隔离运行与可信公开观察说明](docs/development/2026-10-23-architecture-b-runtime-observer.md)。 跨阶段引用、调用账本封存及 READY 原阶段继承见[阶段 A2 说明](docs/development/2026-10-23-architecture-a2-stage-references.md)；该阶段的通过范围以同版验证报告为准。 状态转换及标准CLI持久记录见[阶段C1说明](docs/development/2026-10-24-architecture-c1-state-cli.md)。 持久候选事务、恢复与可替换消费者见[阶段C2/D1说明](docs/development/2026-10-24-architecture-c2-transactions-consumers.md)。

## 安装与冻结校验

Linux、Python 3.10 或 3.12，在仓库根目录执行：

```bash
cd system
python3 -m venv .venv
. .venv/bin/activate
env -u PYTHONPATH python -m pip install -r requirements-dev.lock
env -u PYTHONPATH python -m pip install --no-deps -e .
export PYTHONPATH="$PWD/src:$PWD"
export CONTRACTEDGE_TOPOMEN_SOURCE="$PWD/vendor/topomen"
python scripts/contractedge_local.py --help
python scripts/verify_development_snapshot.py
```

`requirements-dev.lock` 使用 PyYAML 6；此开发环境不复现历史 PyYAML 5.4.1 实验环境。安装时清除 PYTHONPATH，避免 pip 构建 editable metadata 时提前解析历史依赖；运行时使用上述新 tree 的显式路径。原生 TopoMen 源码与 MIT 许可保留在 `vendor/topomen/`，接入的是源码修复消费者 DefaultAgent。

校验器只读核对 `src/` 的全部 `.py/.json` 精确文件集、配置、工具及固定测试面板。`--snapshot docs/development/NAME.snapshot.json --require-current-bindings` 可指定新冻结；历史冻结可能与当前源码不同。最早的 list-only v1 仅能显式加 `--allow-legacy-unbound`，结果为 LEGACY_UNBOUND。源码变化后生成新冻结，不能修改旧冻结使其通过。`DELIVERY_MANIFEST.json` 和原上传校验器保留历史字节语义。

## 默认 host 示例：仅 synthetic 工程回归

默认 provider 为 synthetic，不使用模型服务或 API 密钥。host 路径的 Python 子进程没有操作系统文件系统或网络隔离，仅用于三个公开合成开发例子：

```bash
python scripts/run_public_demo.py --case config-binding --output /tmp/contractedge-host-config-new
python scripts/run_public_demo.py --case reload-state --output /tmp/contractedge-host-reload-new
python scripts/run_public_demo.py --case two-consumers --output /tmp/contractedge-host-consumers-new
```

每次使用新输出目录。它们经过真实诊断、计划、消费者和反馈入口，但不是 benchmark 修复率证据。host 候选执行不接受非 synthetic provider；本轮真实 provider 验收使用下面已验证的 isolated 路径。旧 host API 前半记录仅保留自身版本与范围。

## isolated：候选与公开检查分开运行

先为当前 Docker daemon 缓存固定可信镜像；RuntimeSession 自身始终使用 `--pull=never`，没有 host 降级。镜像已包含本例需要的 `/usr/bin/python3`，候选不依赖在容器里安装第三方包。

```bash
export CONTRACTEDGE_RUNTIME_IMAGE='golang:1.26.8-bookworm@sha256:a688600ca24f8a4d3ca77f95b0dd40704a9fc787c826660eb7ba0b641b8b175d'
export DOCKER_HOST='unix:///var/run/docker.sock'
docker pull "$CONTRACTEDGE_RUNTIME_IMAGE"

python scripts/run_public_demo.py --case config-binding --runtime isolated --runtime-image "$CONTRACTEDGE_RUNTIME_IMAGE" --output /tmp/contractedge-isolated-config-new
python scripts/run_public_demo.py --case reload-state --runtime isolated --runtime-image "$CONTRACTEDGE_RUNTIME_IMAGE" --output /tmp/contractedge-isolated-reload-new
python scripts/run_public_demo.py --case two-consumers --runtime isolated --runtime-image "$CONTRACTEDGE_RUNTIME_IMAGE" --output /tmp/contractedge-isolated-consumers-new
```

`DOCKER_HOST` 应指向自己有权限且资源限制有效的 daemon；上述是常见 Linux socket。API 版本由当前客户端和 daemon 协商，不把某台机器的 1.43 写成通用要求。资源/权限/镜像检查失败就保留失败，不取消 CPU、内存或 PID 限额。

候选与观察器使用不同容器。候选只见只读快照 `/source`、去除 expected 的公开请求 `/inputs/public-inputs.json` 和有界 `/tmp`；观察器只见可信复制的 `/observations/stdout.bin`、`stderr.bin`、`run.json` 与冻结 `/checks/spec.json`，不挂候选源码或候选可写目录。控制层不导入候选模块。

运行层核验实际镜像、非 root UID、只读挂载、network=none、capabilities/no-new-privileges 和内核 cgroup 限额。stdout/stderr 分别及合计限额，deadline 覆盖准备、运行和观察；清理只获得独立短宽限。清理未确认、非零退出、超时、截断、伪造 PASS 或缺检查不能变成可信完整行为 PASS。每次会话持久化 token、container ID、实际核验与清理回执。

当前观察能力限于三个公开开发例子的有限进程输出，不能据此宣称候选内部函数事实、完整部署收敛或正式数据集已全面隔离。注册的 selected/disabled/stable 公开断言不能被模型漏掉的 P 或模型自报 PASS 抹除；原本 UNKNOWN 的义务也不会仅因 profile PASS 被提升。候选保护中的保持项来自固定公开 profile 的基线 PASS，不能由模型删除 P2/P3 改变；观察或清理不完整时不得晋级。真实模型必须自行提供受支持的行为检查绑定，synthetic fixture 的显式绑定不是模型能力成绩。

## READY：继承同版配置与预算

isolated READY 可在新进程继续一次；续跑继承 runtime、provider、方法摘要及前段调用账本，不能改 runtime 或另建额度：

```bash
python scripts/run_public_demo.py --case config-binding --runtime isolated --runtime-image "$CONTRACTEDGE_RUNTIME_IMAGE" --save-ready --output /tmp/contractedge-isolated-ready-new
python scripts/run_public_demo.py --resume-ready /tmp/contractedge-isolated-ready-new --output /tmp/contractedge-isolated-resumed-new
```

原子 claim 防止重复派发；失败也保留 claim 和首次续跑账本。未知/不完整调用不重发，旧源码的 READY 不能替换 hash 后继续新方法。这是 deliberate READY 边界，尚不是任意崩溃恢复。

## 回归与 CI 范围

不需要 Docker 的控制层单测：

```bash
python -m pytest -q tests/test_runtime_session.py tests/test_public_observer.py
python -m pytest -q tests/test_public_isolated_demo.py -k 'not real_'
```

实际 Docker 集成与三例/READY 继承（先设置上面的固定镜像和 Docker socket）：

```bash
python -m pytest -q tests/test_runtime_session_integration.py
CONTRACTEDGE_RUN_ISOLATED_DEMOS=1 python -m pytest -q tests/test_public_isolated_demo.py -k real_
```

原完整 portable 子集保留六项历史/native 排除，不能去掉限额或删失败来变绿。先核验实际 CPU/内存，再将协商结果仅用于本次运行：

```bash
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
python -m pytest -q tests/test_topomen_agent.py
```

portable 与单列 native 均不等于原服务器全量 662+22 验收。历史 ACK 原始公开包缺失不阻塞这些合成示例，也不能由它们补造历史结果。

[GitHub Actions](../.github/workflows/development.yml) 的 focused job 只跑无 Docker 控制层测试；独立 Docker job 核对实际资源、RuntimeSession 反例、三例 isolated 和跨进程 READY 继承；native 与 host synthetic 示例另列范围。CI 不配置模型密钥。工作流存在或本地 YAML 可解析，不代表 GitHub 已运行成功，状态以实际 checks 为准。

## 真实 provider 与费用边界

本轮真实 provider 后半只在 isolated 正反例和 synthetic 闭环通过后开放，且先验证 Broken 的目标 FAIL 与保持控制 PASS。以下命令需要事先批准的配置、可用的安全凭据通道和同一个持久费用账本，本 README 不触发调用：

```bash
python scripts/run_public_demo.py --case config-binding --runtime isolated --runtime-image "$CONTRACTEDGE_RUNTIME_IMAGE" --provider-config /absolute/path/to/approved-provider.json --output /tmp/contractedge-provider-isolated-new-attempt
```

`--provider-config` 必须显式约束 token、时间、调用、轮次和费用；凭据只用环境变量引用，不放入配置、日志或产物。`configs/deepseek-official-front.example.json` 是前半示例，不能不经核对当作新的全流程预算。已授权尝试共享同一 30 元账本，不能改路径、删除账本或逐模式重置。未知响应保留预留，不自动重发；当前模型、价格及输出能力在实际付费前核对。同版真实单例是否运行及其 PASS/FAIL/UNKNOWN/无候选，均按新 validation 记录，不继承旧 READY 或历史费用结果。

## 历史与后续

阶段 A 的[交付与准入说明](docs/development/2026-10-23-architecture-a-delivery-admission.md)保留：必要失败证据缺失停止派发；front.delivery 绑定实际输入和最终请求；max_context_tokens 含 provider/schema 输入开销，model_context_tokens 限输入加输出。交付不等于模型采用或修复正确。

`CURRENT_STATUS.json.historical_status.original_record`、原上传清单和历史 freeze 保留旧版本结果。当前下一步以 current_development、next_actions 和新同版证据为准。跨阶段统一引用、StageResult/transition、持久候选事务/任意中断恢复、消费者解耦和研究模式仍按完整架构方案逐项推进，不能把 B1/B2 当作整个架构目标完成。

根目录 `benchmark/` 与 `tools/` 保留；本阶段不读取数据、GT/Fixed、隐藏评分或其他实验目录，不运行 formal150。工程闭环和公开单例结果不能替代论文效果、消融或泛化评价。
