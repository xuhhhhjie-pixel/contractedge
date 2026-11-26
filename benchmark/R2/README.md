# R2 · Kubernetes控制器调和

控制器在事件过滤、入队、状态比较、调和决策、Kubernetes对象动作或反馈中出现缺陷。

正式成员：**30条**。分类依据生产故障边界；verifier观察终点另行记录。case_id中的历史前缀不自动决定当前类别。

本目录是成员索引，完整实验包位于本仓库Releases，包含源码、公开任务、私有评分材料、离线依赖和固定镜像。

```bash
python3 tools/download_benchmark.py --class R2 --dest ./datasets --extract
```

模型仅可见公开白名单任务与Broken工作区，不能读取完整包中的evaluation/、GT和历史修复。支持Linux/amd64；各案例实际观察边界和资源要求见包内README、task.yaml及运行契约。

## 冻结版本 v2（2026-11-26）

`frozen_case_ids.txt` 是 R2 冻结版 `R2-quality30-full-20261019-v2-frozen-20261126` 的 21 条冻结名单；其余 9 条仍是候选，不进论文主表，原因见 `RELEASE-NOTES-v2.md`。本版本**没有公开输入变更**：公开任务文件与原发布包逐文件相同，本目录的 `manifest.jsonl` 与 `case_ids.txt` 保持不变；变化只在评估侧（私有判据与场景，不在本仓库）。摘要见 `RELEASE-DIGESTS.json`。**冻结不等于授予资格**；完整实验包尚未上传，`../RELEASE_INDEX.json` 仍描述 v1 资产。
