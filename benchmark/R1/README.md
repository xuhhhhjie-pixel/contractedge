# R1 · Helm chart 生成

具体chart的values、模板或helper生成Kubernetes对象时破坏声明意图。

正式成员：**30条**。分类依据生产故障边界；verifier观察终点另行记录。case_id中的历史前缀不自动决定当前类别。

本目录是成员索引，完整实验包位于本仓库Releases，包含源码、公开任务、私有评分材料、离线依赖和固定镜像。

```bash
python3 tools/download_benchmark.py --class R1 --dest ./datasets --extract
```

模型仅可见公开白名单任务与Broken工作区，不能读取完整包中的evaluation/、GT和历史修复。支持Linux/amd64；各案例实际观察边界和资源要求见包内README、task.yaml及运行契约。

## 冻结版本 v4.2（2026-11-25）

本目录的 `manifest.jsonl` 已更新为 R1 冻结版 `R1-quality30-full-20261019-v4.2-frozen-20261125` 的公开清单（任务清单摘要与 106 的公开需求文本有变化）；`frozen_case_ids.txt` 是 29 条冻结名单（`RW-CAND-134` 来源不可核，不在名单内）。公开输入的变更见 `RELEASE-NOTES-v4.2.md` 与 `public-changes/`，摘要见 `RELEASE-DIGESTS.json`。**冻结不等于授予资格**；完整实验包尚未上传，`../RELEASE_INDEX.json` 仍描述 v1 资产。
