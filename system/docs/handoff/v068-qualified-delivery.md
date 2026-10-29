# 0.6.68 获准入方法的运行与交接

当前结论：[最终交付复核](../../outputs/alert-repeat-closure-20261028/FINAL_DELIVERY_AUDIT.md)。方法0.6.68/c7376405通过原46槽开发验收及G0—G6，正式150尚未运行。冻结的system/README.md、AGENTS.md和旧计划保留历史状态；当前入口以本说明、CURRENT_STATUS.json和最终准入报告为准。

## 获取与安装

从仓库交付分支获取，保存实际提交SHA；交付提交不等于默认main已经合并。

```bash
git clone --branch fix/planning-helper-contracts-20261027 https://github.com/xuhhhhjie-pixel/contractedge.git
cd contractedge
git rev-parse HEAD
cd system
python3 -m venv .venv
. .venv/bin/activate
env -u PYTHONPATH python -m pip install -r requirements-dev.lock
env -u PYTHONPATH python -m pip install --no-deps -e .
python -m pip check
export PYTHONPATH="$PWD/src:$PWD"
export PYTHONDONTWRITEBYTECODE=1
python scripts/verify_development_snapshot.py --snapshot docs/development/quote-binding-v068-20261028.snapshot.json --require-current-bindings
python scripts/run_isolated_method.py --help
python scripts/score_isolated_method.py --help
```

需要Linux/amd64、git、bubblewrap(`/usr/bin/bwrap`)、Docker CLI及支持用户命名空间的内核。rootless Docker须通过实际CPU/内存/PID限制核验。使用每案锁定镜像，运行时不联网下载、不降低限额或退回host执行。资源至少按每案契约准入；全150静态检查涉及24GiB工作磁盘、20GiB可用主机内存及4GiB用户runtime空间，不能仅依据示例容器2GiB就判所有案例可运行。

原服务器2354+91完整测试使用登记的外部历史公开fixtures。干净克隆可运行`python scripts/test_portable.py`及单列TopoMen测试；该子集不等于原服务器全量。外部fixture路径与摘要见V068_FULL_REGRESSION.json；缺失明确报告SKIP/NOT_RUN，不能补造PASS。

## 从发布的五分类包准备系统目录

仓库首页下载器校验发布卷及安全解压。任选存储空间足够的位置，以下目录从仓库根计算：

```bash
python3 tools/download_benchmark.py --class all --dest /data/contractedge/packages --extract
python3 tools/assemble_formal150.py --packages /data/contractedge/packages --output /data/contractedge/unified150
```

输出必须是新目录；所有包保留原样。ASSEMBLY_RECEIPT.json存在且status=ASSEMBLED才可使用。只检查映射可加`--verify-only`，不算真实目录复制。重建public、evaluation、partition清单须与原统一150摘要完全相同；同名共享载荷冲突、缺失、篡改或路径越界均拒绝。公开补充只含元数据和缺失公共工具，不含隐藏评分正文。该目录是实际运行视图，不重建全部历史校准证据，不能冒充原完整归档SHA256SUMS通过。

统一目录必须包含RELEASE_ENTRY.json、public/manifest.jsonl、partition/member_groups.jsonl、tools及运行载荷；独立评分另外使用evaluation。完整目录只能由管理层持有，不能把整个目录挂入模型进程。

## 本地配置、预算和零模型检查

`configs/development-deepseek-official-budget200.json`保留原服务器精确字节，含本机路径。迁移时复制到Git以外的本地配置，调整以下三项路径：lock_root、topomen_source_root、money_budget.path；TopoMen仍用当前vendor/topomen，其余方法参数保持一致。新配置SHA不同，必须单独登记；不能冒用原配置证书。

沿用当前项目时继续使用同一实际费用账本，不能复制为空账本、换路径重置费用或释放不确定预留。另一个独立实验的额度由实验管理者登记，模板200元不代表自动授权。通过`contractedge.model.money.MoneyBudget(**config['money_budget'])`初始化/核验管理侧账本，过程不调用模型；已有账本设置不符则停止核对。

设置以下本地绝对路径（值由管理者指定）：CE_SYSTEM、CE_BENCHMARK_ROOT、CE_CONFIG、CE_PYTHON、CE_DEPENDENCIES、CE_OUTPUT_PARENT。CE_PYTHON应指向安装已完成且不再变化的venv/python；CE_DEPENDENCIES可指向已创建的空目录。每次输出必须为不存在的路径且basename等于案例ID。

```bash
python "$CE_SYSTEM/scripts/run_isolated_method.py" --inspect \
  --method-root "$CE_SYSTEM" --benchmark-root "$CE_BENCHMARK_ROOT" \
  --config "$CE_CONFIG" --python "$CE_PYTHON" --dependency-root "$CE_DEPENDENCIES" \
  --case RW-CAND-130 --attempt-id local-dev-001 \
  --output "$CE_OUTPUT_PARENT/RW-CAND-130" > /tmp/ce-dev-inspection.json
```

inspect不运行候选或模型，默认allow_paid=false。管理者核对其身份、环境与预算，登记后才将本次登记的allow_paid设true；不能手写摘要跳过环境检查。可用同样参数把`--inspect`替换为`--dry-public`完成真实公开准备/执行检查；dry-public也要求有效管理侧账本与资源，必须换新输出目录。不得复用已经存在的dry-public输出去执行付费修复。

## 经登记的单条修复与独立评分

这里只描述现有已验证入口。先登记唯一attempt-id、精确输出、当前配置和环境；凭据保存到管理侧0600私有文件，通过CE_KEY_FILE路径提供，不写入命令、JSON或Git。单条付费执行参数与inspect登记完全相同：

```bash
python "$CE_SYSTEM/scripts/run_isolated_method.py" --execute \
  --method-root "$CE_SYSTEM" --benchmark-root "$CE_BENCHMARK_ROOT" \
  --config "$CE_CONFIG" --python "$CE_PYTHON" --dependency-root "$CE_DEPENDENCIES" \
  --case RW-CAND-130 --attempt-id local-dev-001 \
  --output "$CE_OUTPUT_PARENT/RW-CAND-130" \
  --registration "$CE_REGISTRATION" --api-key-file "$CE_KEY_FILE"
```

固定面板执行另需panel-registration/panel-slot，不能把上面的开发单条计入原46。官方endpoint为https://api.deepseek.com，请求模型deepseek-flash；收到模型身份不匹配、费用不足、未知调用、隔离/数据/资源失败时停止排查，不切回中转。模型别名和实际响应身份都保留。

方法结束后读取本次输出父目录新产生的`.isolated-*/receipt.json`；必须人工/程序核对receipt.identity.attempt_id和output_path确实属于本次运行，不能靠任意glob取另一次候选。已确认METHOD_RESULT、输出封存且cleanup.status=OWNED_CONTAINERS_CLOSED后，在管理侧执行：

```bash
python "$CE_SYSTEM/scripts/score_isolated_method.py" \
  --receipt "$CE_RECEIPT" --benchmark-root "$CE_BENCHMARK_ROOT" \
  --output "$CE_SCORE_OUTPUT"
```

评分不调用模型；隐藏详情不回流修复。已发送但响应未知的模型请求不自动重发；评分已存在完整checkpoint时，仅用相同参数和`--phase finalize`复核聚合；已派发但无完整checkpoint须管理者审查，不能直接重跑`--phase run`或再次调用模型。源切换崩溃使用已有持久恢复入口，失败记录和账本不删除；它不是任意进程均能无条件自动续跑的保证。

## 复核本次历史准入

原服务器可用已资格核验的`/home/xhj/contractedge-parent402-v068-20261028/system/scripts/assess_formal_readiness.py --manifest <本交付的V068_FINAL_ADMISSION_MANIFEST.json> --output <新路径>`重新验证，0模型调用。涉及ACK账户中断的父侧扩展可用outputs/alert-repeat-closure-20261028/parent402-authority/reconstruct_parent.py重建到新目录，禁止修改冻结方法本身。

证书及46条索引随Git发布；完整运行树/历史fixture仍保留服务器，JSON中的绝对路径是审计来源定位，不是另一主机已有文件的承诺。另一主机须以实际下载/安装后的身份验证，缺外部历史证据时报告不可复核，不能绕过摘要检查。[实际目录还原记录](../../outputs/alert-repeat-closure-20261028/V068_RELEASE_LAYOUT_VERIFICATION.json)验证了9090文件还原、98公共工具、150评价注册资产摘要，以及封存补丁的真实公开触发/对照执行；不代表另一主机150条实际运行成功。

历史scripts/run_formal150.py、audit_formal150_readiness.py及旧host repair-batch示例没有获得当前官方隔离正式派发资格。当前配置`development_only=true`仅开放开发成员；正式运行须另行登记配置及其摘要，不能修改原冻结配置后冒用旧身份。正式150的预算、指标、曝光分层、串行调度及停止恢复协议需在启动前单独冻结；本交付不自动派发正式实验。方法有效失败与基础设施失败必须分别计数，保留全部分母。
