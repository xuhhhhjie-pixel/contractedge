# R4 · 外部控制平面资源协调

## 当前 v3.4 验收版

完整类别共 30 条，延续 27 条已验收合同，采用 3 条独立重建合同。行为与 GT 轨道各 30 条。

[成员清单](r4-v3.4-rebuilt-20261126-members.jsonl) · [资产及摘要](CURRENT_ASSETS.json) · [当前 Release](https://github.com/xuhhhhjie-pixel/contractedge/releases/tag/r4-v3.4-rebuilt-20261126) · [下载](https://github.com/xuhhhhjie-pixel/contractedge/releases/download/r4-v3.4-rebuilt-20261126/r4-v3.4-rebuilt-20261126-public.tar.gz)

## v3.3 历史合格子集

R4 历史登记 30 条。r4-v3.3-subset-20261125-r3 冻结行为 27 条、GT 27 条，名单一致。其余登记成员的未纳入原因见资产索引。完整类别发布仍沿用下方历史入口。

[成员及分母](r4-v3.3-subset-20261125-r3-members.jsonl) · [资产索引和摘要](CURRENT_QUALIFIED_SUBSET.json) · [新版 Release](https://github.com/xuhhhhjie-pixel/contractedge/releases/tag/r4-v3.3-subset-20261125-r3) · [公开输入覆盖包](https://github.com/xuhhhhjie-pixel/contractedge/releases/download/r4-v3.3-subset-20261125-r3/r4-v3.3-subset-20261125-r3-public-overlay.tar.gz)

以固定历史包的公开任务目录为基础，仅提取子集名单中的公开输入。管理员评估包另行保存在受限环境。历史暴露记录随案例保留。本版不声明未见测试。技术验收采用 Codex 辅助审查、自动校准及单一维护者授权。

## 历史完整 30 条入口

声明意图与外部受管资源的请求、状态、标识、生命周期或能力映射出现缺陷。

正式成员：**30条**。分类依据生产故障边界；verifier观察终点另行记录。case_id中的历史前缀不自动决定当前类别。

本目录是成员索引，完整实验包位于本仓库Releases，包含源码、公开任务、私有评分材料、离线依赖和固定镜像。

```bash
python3 tools/download_benchmark.py --class R4 --dest ./datasets --extract
```

模型仅可见公开白名单任务与Broken工作区，不能读取完整包中的evaluation/、GT和历史修复。支持Linux/amd64；各案例实际观察边界和资源要求见包内README、task.yaml及运行契约。
