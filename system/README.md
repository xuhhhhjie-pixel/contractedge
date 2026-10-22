# ContractEdge system — 0.6.27 development snapshot

**当前为可继续开发的交接快照，formal_ready=false。** 662项测试+22项子测试在原服务器通过；最新ACK真实实验INVALID_PLAN/NO_CANDIDATE。还未通过固定同版效果、重复、恢复和迁移验收，不能将此GitHub上传视为正式实验就绪。

首先阅读 [正式实验准入与最终优化实施计划](docs/superpowers/plans/2026-10-22-formal-experiment-delivery.md)。它逐项审查0—10步骤、三个核心模块、现有证据和未完成事项，并给出后续任务、验收与停止条件。

## 内容

- src/：完整当前ContractEdge运行源码及JSON契约，与本地0.6.27冻结摘要一致。
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
# 历史工程固定PyYAML5.4.1可能需要旧构建工具；新环境先安装现代wheel。
python -m pip install --index-url https://pypi.org/simple -r requirements-dev.lock
python -m pip install --no-deps -e .
python scripts/contractedge_local.py --help
python scripts/test_portable.py
```

上述PyYAML6安装是交接开发环境方案，与原实验PyYAML5.4.1环境不同。正式实验前必须冻结并重新验收所选依赖环境。pyproject保留历史依赖记录；--no-deps是显式采用上面的开发依赖集合，不表示依赖解析已复现原服务器。

`test_portable.py`明确排除6个依赖服务器历史数据的测试模块，输出其名单。此结果不等价于原服务器662项全验收；全量`pytest tests`需要额外恢复历史fixture和benchmark public路径，详见优化计划Task5。其他测试仍完整运行。不会静默把缺fixture算通过。

## 数据与真实运行

同仓库根目录已有benchmark Release下载工具：`python tools/download_benchmark.py --class all --dest ./datasets --extract`（在仓库根运行）。分类包包含公开/独立评价边界；内部系统历史实验脚本使用统一public/evaluation/tools布局。**不要假定分类包路径就是统一布局**，先核验版本、成员、来源和manifest，并完成计划Task5的路径适配。

通用CLI查看`python scripts/contractedge_local.py --help`。历史`run_reliability_worker_v8.py`等脚本和configs中的`/home/xhj/...`是当时原始记录，不能直接当作可移植启动器。复制配置、设定本地路径、API环境变量`CONTRACTEDGE_API_KEY`、独立账本及development准入后才启动付费任务。密钥不存储在仓库。

`configs/development.example.json`给出本地路径模板；必须在运行前填入正确绝对路径。当前模板拒绝未准备的数据/来源，完整正式实验启动入口仍属于待验收工作。独立评分在方法结束后运行，隐藏数据不能送入模型上下文。

## 复现边界与证据

`outputs/reliability-closure-20261020/`保留各版本摘要和失败，不混合历史最佳值。原始4.6GB outputs、源码副本、模型调用全文、数据包、Docker镜像、虚拟环境及费用账本不进入Git。原服务器保留它们；精确历史回放需从持有者获得相应公开fixture。outputs内保留历史回放/冻结脚本，它们的绝对路径和外部输入仍需迁移。文档中的绝对路径是历史来源，不代表克隆后存在。

不承诺所有案例成功。后续版本须满足固定配对、原成功回归、真实失败恢复及未参与调参开发面板的门槛，再设置formal_ready=true。

## 本次上传验证

2026-10-22：独立Python3.10虚拟环境安装成功，CLI启动成功，自包含测试629 PASS、17 subtests PASS；原服务器依赖环境下同一子集也通过。完整原服务器测试为662+22，本交接不把6个排除模块计为通过。`python scripts/verify_delivery.py`校验交付文件摘要。
