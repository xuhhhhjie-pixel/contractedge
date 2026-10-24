# 隔离开发验证入口

当前支持Linux、bubblewrap、本地Unix Docker端点及串行执行。修复算法仍为0.6.38，执行边界和评分检查点单独记录版本。正式150及固定面板准入不能由本页的一条成功代替。

安装公开依赖并运行工程验证：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.lock
.venv/bin/python -m pip install --no-deps .
.venv/bin/python -m pip check
.venv/bin/python scripts/test_portable.py
CONTRACTEDGE_TOPOMEN_SOURCE="$PWD/vendor/topomen" .venv/bin/python -m pytest -q tests/test_topomen_agent.py
```

可移植子集排除6个依赖历史夹具或原生集成的模块；原生TopoMen测试单列。完整服务器回归971项及22子测试、可移植938项及17子测试，是本次交付的工程证据。

把`configs/development.example.json`复制为本地配置，设置绝对路径：`topomen_source_root`指向本仓库`vendor/topomen`，`lock_root`为本地实验锁目录，`money_budget.path`为唯一共享账本。保留登记的模型、12调用、2轮、上下文、探测和财务上限。首次使用账本可用`contractedge.model.money.MoneyBudget(**config['money_budget']).summary()`初始化，不能通过更换账本重置已有实验费用。

以下变量由操作者设置；数据目录必须是当前冻结150条的正式发行包，而非历史117/147包。API密钥文件只传给父侧入口。

```bash
CE_SYSTEM_ROOT="$PWD"
CE_PYTHON="$PWD/.venv/bin/python"
CE_CONFIG=/absolute/path/to/local-config.json
CE_BENCHMARK_ROOT=/absolute/path/to/ContractEdge-benchmark150-v1-20261019
CE_DEPENDENCIES="$PWD/.local/empty-dependencies"
CE_CASE=RW-CAND-130
CE_OUTPUT="$PWD/.local/attempt-001/cases/$CE_CASE"
CE_ATTEMPT=development-001
CE_REGISTRATION="$PWD/.local/registration-001.json"
mkdir -p "$CE_DEPENDENCIES"
```

先用`--inspect`生成完全匹配代码、配置、数据、已安装环境、输出路径和尝试身份的登记文件，**不会读取密钥或调用模型**：

```bash
"$CE_PYTHON" scripts/run_isolated_method.py --inspect \
  --method-root "$CE_SYSTEM_ROOT" --benchmark-root "$CE_BENCHMARK_ROOT" \
  --config "$CE_CONFIG" --output "$CE_OUTPUT" --case "$CE_CASE" \
  --python "$CE_PYTHON" --dependency-root "$CE_DEPENDENCIES" \
  --attempt-id "$CE_ATTEMPT" > "$CE_REGISTRATION"
```

核对登记范围后，将该JSON的`allow_paid`设为`true`；`role`保留`ISOLATED_DEVELOPMENT_VALIDATION`，`counts_in_46_slot_panel`保留`false`。这是工程单例登记，不是正式实验登记。执行时使用相同参数，将`--inspect`改为`--execute`，另加：

```bash
  --registration "$CE_REGISTRATION" --api-key-file "$CE_API_KEY_FILE"
```

已声明的尝试或输出不能重用，包括中断、失败及尚未形成最终输出的情况。原始状态位于同级`.isolated-<session>`目录；保留该目录、声明与全局费用账本。仅持久化完成的同ID响应可以取回，未知付费请求不能重新发送。新的独立尝试须事前登记不同身份和输出，并保留旧结果。

方法结束、候选已由可信导出绑定且本次容器清理确认后，父侧独立评分：

```bash
"$CE_PYTHON" scripts/score_isolated_method.py \
  --receipt /absolute/path/to/.isolated-session/receipt.json \
  --benchmark-root "$CE_BENCHMARK_ROOT" \
  --output /absolute/path/to/separate-independent-score --phase run
```

`run`阶段持久化评分器退出状态及产物摘要；后续使用完全相同参数和`--phase finalize`核验并汇总，不重新运行评分器或模型。若停留在`SCORING_STARTED`而没有完整退出记录，需人工核查，不自动重试。评分输出必须在方法、状态目录和数据包之外，不能输入模型或用来挑选同例补丁。

`scripts/run_reliability_delivery.py`仍是原协议驱动器，尚未迁移到这个执行协议；不得把旧入口直接当作本版G3隔离验收入口。最后还需冻结协议、登记完整同版面板并通过G0—G6，才能开启正式实验。
