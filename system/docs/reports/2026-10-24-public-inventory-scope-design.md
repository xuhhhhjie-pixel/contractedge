# 完整公开清单的范围匹配

依据：干净安装0.6.38 RW130独立PASS，但公开保持条件因OBSERVATION_RESOURCE_MISMATCH保留UNKNOWN。原始完整Job清单、正确control场景和引用均进入请求；producer没有给集合提供可表达范围的身份，support_error只支持单对象身份。这是原最终优化方案公开结构适配要求中的具体遗漏。

保留正在运行的0.6.38冻结、数据和结果。在独立工作树修复现有public.py/support.py，不新增Agent、模型调用、宽泛推理或专案规则。

公开生产者仅在成功、完整解析渲染后，给逐kind完整清单登记固定adapter_id和structured_complete，以及原始文件身份。范围校验只接受这种producer标记、整对象未截断、JSON合法、kind/来源一致的清单，允许它为同kind的名字/namespace子范围提供证据。集合覆盖范围并不证明语义结论，仍必须有模型给出的真实引文和既有检查。

拒绝错误场景/kind、未注册原始JSON、截断/不完整清单、伪造来源以及用结构证明运行时。范围缺失值不能导致相关操作失败被忽略；保留KOPF操作一致性约束。旧无标记观察保持原结论，不追认历史PASS。

工程通过后才考虑新方法冻结。绝不把0.6.38的PASS移给新源码，不修改在运行的clone/runtime冻结；正式150/B继续关闭。
