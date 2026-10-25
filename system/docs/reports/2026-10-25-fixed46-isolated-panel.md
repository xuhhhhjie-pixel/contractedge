# 原固定46槽：隔离协议迁移与运行约束

方法源码为冻结0.6.45（116运行文件）；基线保持原0.5.5（71文件，c9c5b75060549e0e22eb725b3082dee2e7994deecc0d653379c801b0a1f9fc6e）。本轮是共同实验工具迁移，不借新版本号重复模型尝试，不继承旧资格单条、合成24格或历史最佳分数。

## 原登记和可重建输入

A固定7条、B固定10条，各自baseline/candidate配对34槽；KOPF/Alert额外重复4槽，KPT/Alert公开失败种子续修8槽，共46。案例、顺序规则、种子原字节、次数和分母不替换。ORIGINAL_REGISTRATION_v038.json保留原登记。baselines/v0.5.5/src保存原源码；SOURCE_REFERENCE.json可与原36/38冻结逐文件核对。两份原失败补丁仅用于对应案例的recovery槽，普通A/B/重复不挂载种子。

源码修复消费者继续是实际TopoMen DefaultAgent。共用的隔离边界、模型服务/参数、12调用、2新编辑、2M保守token、220k上下文、9000秒及150元累计总账不变。baseline保留原公开探测能力，candidate保留其当前探测能力；不把candidate代码或探测器移入baseline。共同续修协议显式升级v6：两者先应用原公开失败种子并观察，在当前快照规划。原始Broken归档身份保留，旧v5结果不迁移；该共同修订必须有双方兼容性证据。

## 实际入口

- scripts/prepare_fixed_panel.py：核验原登记、原源码参考、当前候选参考、配置、种子、公共数据/分区和共同Python/依赖/控制器/工具摘要，生成不可覆盖的46槽登记；不读密钥，不创建运行目录，不发模型调用。
- scripts/run_isolated_method.py：资格单条仍counts_in_46_slot_panel=false；面板槽仅在显式--panel-registration、--panel-slot及精确登记匹配后授权。普通--registration不能冒称面板。
- scripts/run_fixed_panel.py：parent-only顺序队列、持久派发意图、原子一次性调用登记、独立评分与已完成评分finalize。结果未知时停止并保留，不自动重发模型；原生消费者仍沿各版本自己的实现运行。
- scripts/score_isolated_method.py：冻结候选的独立评分原语；其单条结果不自行声明整套面板资格。面板是否计数由核验派发、候选和评分证据的外层注册记录决定。

控制器的records.json保留独立status与public_decision两个维度。公开FAIL会阻止候选准入，但不会被改写为独立评分失败；独立PASS也不会抹掉公开FAIL。原v2效果汇总使用真实独立结果，公开一致性另列门槛。

## 零调用与恢复验收

重点反例包括：重复/替换/重排槽位、源码/配置/分区/种子/共同环境漂移、旧单条冒称面板、手写PASS标记、未运行/UNKNOWN当作终态、未满足前置组就启动B、模型派发未知时重发、评分完成后重复运行模型、候选/评分被更换等。实际沙箱对两个冻结方法执行公开运行；真实前端API在普通Broken及两个种子快照捕获首个请求，使用明确synthetic执行观察夹具，不声称模型或行为效果。公开失败种子范围与原始归档始终单独保留。

约29GiB空闲下，方法和评分结束、候选/日志/终态核验并封存后，仅退役已关闭会话producer/workspace内可由公开归档+补丁重建的源码和缓存。事务日志、补丁、公开观察、评分、原数据与其他目录保留；软链接或归属不明时拒绝清理。评分未知/基础设施错误不清理现场。受控中断先让原隔离协调器记录清理，不能把RUNNING文本当成仍有进程。

## 开始运行和组门槛

使用本机密钥文件、同一持久账本和原登记，先验证当前development freeze。准备命令的--baseline-root可指向baselines/v0.5.5，--candidate-root指向完整当前system目录或经摘要核验的源码副本；配置里的本机native/lock/ledger路径须在准备前明确物化，运行后不得改动。--candidate-reference读取相应源码冻结的files；--baseline-reference读取SOURCE_REFERENCE的baseline_source_files。

准备好兼容性证据后，先运行run_fixed_panel.py --panel PATH --dry-run，确认精确46、0派发。实际运行按--group A、repeat、recovery、B分阶段，由持久用户任务承接。A先完整核查；后续组需原前置组终态和candidate独立PASS且无公开FAIL。B尤其要求A、重复和续修均满足，不靠手写布尔或历史结果开闸。B首次启动需更新曝光账本；看到B结果后用于调参，则后续版本不能继续声称这批B未参与调参。

每对派发前按剩余总账和原最大调用/token上限做费用上界准入；未知预留保留，不换账本、不增加150上限。预算、源漂移、评分不确定或隔离基础设施错误停止；方法自己的失败及无候选如实计入。若出现新通用缺陷，先用公开轨迹定位最早断点并回放，再冻结新版本；旧面板和未运行槽位保留，不拼接多版本最佳成绩。

## 结果与后续

当前完整进度以outputs/fixed46-isolated-panel-20261025/COMPLETION_AUDIT.md和私有运行目录state/records为准。报告必须给出46槽状态、独立配对胜负、公开与独立矛盾、UNKNOWN、无候选、真实续修、费用及未运行。完成面板后才根据公开失败证据决定组件优化。原G0—G6最终准入、干净机器全流程和正式150尚不由本工具自动宣布通过。
