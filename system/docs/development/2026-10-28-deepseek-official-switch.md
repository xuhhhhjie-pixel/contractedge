# DeepSeek 官方服务切换记录（2026-10-28）

用户授权以 DeepSeek 官方服务替换中转站用于后续优化。本次仅完成配置和连接验证，benchmark 优化与正式实验仍暂停。

- 当前配置：`configs/development-deepseek-official-budget200.json`。
- 地址：`https://api.deepseek.com`；模型：`deepseek-flash`；严格核对返回模型身份。
- 官方文档标注实际服务为 DeepSeek-V4.1-Flash-1017；旧名 deepseek-v4-flash 已映射到新版，不能宣称此次调用了独立旧版 V4。
- 官方模型列表认证通过；通过系统现有 OpenAICompatibleProvider 完成非思考和低强度思考两个 JSON 调用，均 PASS；没有读取或发送 benchmark 数据。
- 证据：`outputs/alert-repeat-closure-20261028/DEEPSEEK_OFFICIAL_CONNECTION.json`。新增2次调用，保守费用上界 0.000410 元；累计含预留占用 149.093596 元，累计上限仍200元。
- 价格按官方高峰未命中上界输入2元/百万token、输出8元/百万token计量；未利用缓存或空闲折扣，记录不是服务商账单。
- 新账本：`/home/xhj/contractedge-reports/deepseek-batch-readiness-20261019/money-ledger-deepseek-official-20261028.json`。完整复制旧账本的1419条调用和3条未结预留，历史条目逐条一致，原账本SHA核验一致；没有重置费用。
- 新运行只使用官方配置及新账本。旧中转配置、旧账本与历史结果保留用于审计，不再作为新运行入口；不编辑旧冻结文件，也不混合两种服务的成绩。
- 密钥在仓库外的用户私有文件中，权限0600；没有写入配置、日志或Git。路径由`/home/xhj/.config/contractedge/active-provider.json`记录。

## 后续恢复入口

`active-provider.json`是交接记录，不是CLI自动读取的默认配置。恢复工作时读取其中的`provider_config`与`api_key_file`，向既有隔离派发入口显式传入；不得复用旧控制脚本内写死的中转配置/密钥。

先完成尚未资格核验的行为失败重诊断与Go注释无效修改拦截，再将官方配置纳入新冻结/登记，随后按已批准顺序开展有界真实验证。此次连接成功不代表修复效果提高，也不代表正式准入通过。源码逻辑与实验上限未因服务切换额外修改；原200元累计授权继续有效。

官方依据：
- https://api-docs.deepseek.com/quick_start/agent_integrations/
- https://api-docs.deepseek.com/zh-cn/quick_start/pricing/
- https://api-docs.deepseek.com/guides/thinking_mode/
