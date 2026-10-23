> CURRENT frozen0.6.38 / 5f9e90c459b95e703b0c9f32b9f9d70fff1eb859f90e95489f0083c632f422fe. Full870+22, portable837+17. Prior.37 originalBrokenPASS after real failed-first-trial recovery,9calls/2edits/public8PASS; never transfer that score to.38. Necessary pending-preservation checks protected; fresh.38 slots0/46. Formal150/B closed. No GitHub push. Read CURRENT_STATUS.json and docs/reports/2026-10-24-pending-preservation-guard.md.

**当前为开发交接快照，formal_ready=false。** 完整847+22、可移植814+17。0.6.35单列编译失败续修独立PASS，原始Broken独立失败；0.6.36修复带反馈重诊错误套用初诊容量，回放已恢复当前生产源码送达。0.6.36原始Broken已INVALID_PLAN/NO_CANDIDATE；已定位计划纠错未携带原草稿。0.6.37正在隔离副本补齐，历史和补充结果不拼入固定面板成绩。

见[本版机制修复与验收](docs/reports/2026-10-24-optional-gap-context-closure.md)、[公开证据连续性](docs/reports/2026-10-24-public-evidence-continuity.md)和[0.6.32真实修复结果](docs/reports/2026-10-24-execution-contract-closure.md)。

首先阅读 [正式实验准入与最终优化实施计划](docs/superpowers/plans/2026-10-22-formal-experiment-delivery.md)。它逐项审查0—10步骤、三个核心模块、现有证据和未完成事项，并给出后续任务、验收与停止条件。

## 内容

- src/：完整当前ContractEdge运行源码及JSON契约，与本地0.6.36冻结摘要一致。
- tests/、scripts/、configs/、docs/：当前全部测试、工程脚本、历史配置和设计文档。
- vendor/topomen/：所接入TopoMen运行源码与原MIT许可；调用其DefaultAgent作为源码修复消费者，非完整部署调度系统接入。
- outputs/：精简历史审计文档、冻结元数据和结果摘要；不是全部多GB工作目录或完整历史回放输入。
- DELIVERY_MANIFEST.json：文件摘要和明确排除范围。

## 零付费开发检查

Linux，Python3.10+，推荐3.12。以下仅运行自包含测试，不读取API密钥：

```bash
cd system
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --index-url https://pypi.org/simple -r requirements-dev.lock
python -m pip install --index-url https://pypi.org/simple -e ".[topomen]"
python scripts/contractedge_local.py --help
python scripts/test_portable.py
```

开发锁文件固定PyYAML6.0.3，包元数据兼容PyYAML5.4.1至7之前的版本；实际实验必须固定具体依赖清单，不能把兼容范围当作已冻结环境。此开发环境不等同于历史PyYAML5.4.1环境。安装后应运行`python -m pip check`，版本冲突必须先解决。

测试入口默认将所需的原生TopoMen源码定位到随库的`vendor/topomen`，避免部分测试回退到学长服务器的`/home/xhj`路径；显式设置`CONTRACTEDGE_TOPOMEN_SOURCE`时保留该选择。Docker相关测试仍要求当前daemon实际支持其声明的资源限制；依赖缺失或容器启动失败必须报告，不能通过移除限额计为通过。

`test_portable.py`明确排除6个依赖服务器历史数据的测试模块，输出其名单。此结果不等价于服务器完整验收；历史662+22仅代表当时源码，全量`pytest tests`需要额外恢复历史fixture和benchmark public路径，详见优化计划Task5。其他测试仍完整运行。不会静默把缺fixture算通过。

## 数据与真实运行

同仓库根目录已有benchmark Release下载工具：`python tools/download_benchmark.py --class all --dest ./datasets --extract`（在仓库根运行）。分类包包含公开/独立评价边界；内部系统历史实验脚本使用统一public/evaluation/tools布局。**不要假定分类包路径就是统一布局**，先核验版本、成员、来源和manifest，并完成计划Task5的路径适配。

通用CLI查看`python scripts/contractedge_local.py --help`。历史`run_reliability_worker_v8.py`等脚本和configs中的`/home/xhj/...`是当时原始记录，不能直接当作可移植启动器。复制配置、设定本地路径、API环境变量`CONTRACTEDGE_API_KEY`、独立账本及development准入后才启动付费任务。密钥不存储在仓库。

`configs/development.example.json`给出本地路径模板；必须在运行前填入正确绝对路径。当前模板拒绝未准备的数据/来源，完整正式实验启动入口仍属于待验收工作。独立评分在方法结束后运行，隐藏数据不能送入模型上下文。

## 复现边界与证据

`outputs/reliability-closure-20261020/`保留各版本摘要和失败，不混合历史最佳值。原始4.6GB outputs、源码副本、模型调用全文、数据包、Docker镜像、虚拟环境及费用账本不进入Git。原服务器保留它们；精确历史回放需从持有者获得相应公开fixture。outputs内保留历史回放/冻结脚本，它们的绝对路径和外部输入仍需迁移。文档中的绝对路径是历史来源，不代表克隆后存在。

不承诺所有案例成功。后续版本须满足固定配对、原成功回归、真实失败恢复及未参与调参开发面板的门槛，再设置formal_ready=true。

## 本次上传验证

2026-10-22：独立Python3.10虚拟环境安装成功，CLI启动成功，自包含测试629 PASS、17 subtests PASS；原服务器依赖环境下同一子集也通过。完整原服务器测试为662+22，本交接不把6个排除模块计为通过。`python scripts/verify_delivery.py`校验交付文件摘要。

2026-10-23本地续接：0.6.31完整792+22、可移植子集766+17通过；本轮使用已有开发虚拟环境，不声称全新虚拟环境复现。新源码与公开生产探测已经冻结，真实效果仍在开发验收中，本次尚未推送远端。

2026-10-24：0.6.32完整799+22、可移植子集766+17；新探测三方公开校准与错误目标反例通过，当前真实开发验证仍待结束。详见最新记录，不能将此Git快照当作正式准入。

2026-10-24续接0.6.35：完整841+22，可移植808+17。新增公开Go编译错误恢复，见`docs/reports/2026-10-24-build-failure-recovery.md`。历史夹具测试应从具备这些相对输出路径的运行根目录执行，同时通过绝对PYTHONPATH和测试目录选择待验证源码；在缺夹具的干净交付目录仅运行已声明的可移植子集，不把夹具缺失解释成源码回归或测试通过。
