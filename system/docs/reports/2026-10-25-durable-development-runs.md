# 开发实验持久运行与中断审计

长实验不应依赖聊天工具的临时进程生命周期。本轮遇到过控制器已退出、receipt仍为METHOD_RUNNING的情况：4个模型请求已有完成记录，公开探测PENDING，不能据旧状态继续等待，更不能自动重复派发。

## 启动方式

沿用当前`run_isolated_method.py`的独立登记、源/配置/虚拟环境摘要、共享费用上限及有限调用协议。用现有用户级systemd运行同一命令，无需修改系统服务或增加分析模块：

```bash
systemd-run --user --unit=contractedge-UNIQUE_ATTEMPT \
  --working-directory=/ABSOLUTE/REPOSITORY \
  --property=Restart=no --property=RuntimeMaxSec=18000 --property=TimeoutStopSec=90 \
  /ABSOLUTE/VENV/bin/python -B /ABSOLUTE/REPOSITORY/system/scripts/run_isolated_method.py \
  ...原先已经校验并登记的参数...
```

这里的整体任务期限只覆盖方法、清理和父侧评分；方法自己的9000秒、12调用、2轮及费用限制保持原值。不得把systemd重启作为方法重试，也不得重复使用attempt-id或输出目录。密钥由父侧读取本机私有文件，不能写进命令参数值、Git、方法环境或日志。其他协作者使用自己的账号、配置与费用账本。

检查任务时只读必要状态，避免输出ExecStart、环境变量及完整进程参数：

```bash
systemctl --user show contractedge-UNIQUE_ATTEMPT.service \
  --property=ActiveState,SubState,MainPID,ExecMainStatus
```

原始receipt、请求账本、模型响应和源码快照继续由既有执行器持久记录。独立评分仅在方法闭合、候选导出核验、全部请求确定且所属容器清理后运行；评分不回流方法。

## 中断处理

1. 核对任务状态、准确输出路径对应的控制器/工作进程，以及独占运行锁。不能仅凭一个PID存在判断进程身份，PID可能复用。
2. 保留原始receipt与RPC记录，不改写为成功。另存带时间、原始文件摘要和具体事实的中断审计。
3. 模型请求PENDING表示费用/响应可能未知：保留预留并停止新增相关调用，不重复请求直到有外部核验。模型请求均DONE、仅公开操作PENDING时，也不能把公开结果补成PASS。
4. 只按本次session标签核验并清理所属容器；不做全局Docker清理。核对实际源码与最后已应用事务的快照和文件摘要。
5. 未完成最终导出的候选不能冒充原端到端结果。允许单独导出当前已核验候选、重新公开检查和父侧评分，必须标为“中断候选工程审计”、0新增模型调用，不移入原方法成绩或46槽。
6. 新案例只有在前案资源、未知请求及来源状态核实后，才按新的独立登记启动。不得以新目录包装同案无限付费重试。

当前0.6.44的RW130验证已通过上述一次性用户任务完成，公开/独立均PASS。Alert原尝试保留为中断；其候选零调用工程审计已完成，公开4项/独立均PASS。两者身份和统计口径分开，均不授权正式150。
