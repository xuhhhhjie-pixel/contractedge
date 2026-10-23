# D2 四模式与六例公开开发面板

四种 method_mode 为 `full`、`without_relation`、`without_changeset`、`direct`，与执行后端独立。研究主对照共用 `modify_only` 消费者，原生 TopoMen Full 单列兼容性验证。

- Full 运行 Diagnosis → ChangeSet → 执行。
- without_relation 从原始需求、Broken 和允许源码生成独立 DirectPlan；不调用 E/V 或 ChangeSet。
- without_changeset 在真实 Diagnosis gate 后停止语义生成，将保留关系交给 RelationOnly；不补造 C/P、步骤或依赖。
- direct 使用 RequirementOnly，不调用语义前端。

非 Full 反馈仅投影当前原始公开观察，拒绝复用 Full 推导产品；格式纠错再次核对模式来源。模式身份参与持久恢复准入，已收到响应仅能经原 AcceptedInput 再验后消费一次，跨模式恢复拒绝。

六个固定公开例子为 config-binding/base、config-binding/exception-guard、two-consumers/base、two-consumers/preserve-one、reload-state/base、reload-state/rejected-reload。Broken 必须先建立固定目标 FAIL 与全部保持状态，模型不能删除固定 requirement profile 或取消已观察的 PASS。

候选只获得只读源码与去除 expected 的公开输入；独立 checker 只获得冻结检查规范与有界实际输出。完整性包括两种 RuntimeSession 角色、实际容器/内核隔离、输入/源码/规范身份、输出长度与摘要及全部清理回执。缺失、陈旧、截断、伪造结果和清理失败均不得晋级。

保留 C2 r1/r2 acceptance 原字节；D2 的 `configs/trial-isolation-acceptance-d2.example.json` 继承 r2 的全部 212 条绑定，并更新 4 个修改源、新增 12 个 D2 文件。该绑定不包含自身，aggregate 为完整映射的规范 JSON SHA256。CI 的 C2 两消费者实际恢复 8 格使用此 D2 同版绑定，并增加 D2 无 Docker 接点与 4×6 实际隔离面板。工作流存在或本地 YAML 校验不代表 GitHub 已执行成功。运行命令见 README；实际矩阵、对应源码冻结与回执必须在执行后另行归档。

本版本为 synthetic 公开开发工程验证；没有新模型调用额度，不读取原费用账本、正式数据、GT/Fixed 或隐藏检查。`formal_ready=false`。host 与 runtime doubles 的成功不得替代实际 24 格；未执行项保持 NOT_RUN。
