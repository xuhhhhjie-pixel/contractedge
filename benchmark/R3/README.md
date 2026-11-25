# R3 · 部署工件转换或生成

## 当前 R3 版本

[R3 v3.3 发布](https://github.com/xuhhhhjie-pixel/contractedge/releases/tag/r3-v3.3-20261125)覆盖固定的 30 条 R3 成员。每条通过行为与 GT 技术验收，采用 Codex 辅助语义审查和自动校准，由维护者授权发布。

[公开任务增量包](https://github.com/xuhhhhjie-pixel/contractedge/releases/download/r3-v3.3-20261125/r3-v3.3-20261125-public-overlay.tar.gz)绑定实际任务和公开资源摘要。先按下方命令把 `benchmark-v1-20261019` 的 R3 包提取到独立目录，再按增量包内的 `ASSETS.json` 核对原始文件及替换文件的 SHA-256。在独立副本上应用增量文件，使用该副本的公开目录准备候选工作区。

[当前资产索引](CURRENT_ASSETS.json)记录版本、下载地址和摘要。评分器与任务投影 GT 由管理员走受控入口，候选环境只读取专用的公开目录。旧版本和历史成绩可从 [v1 发布](https://github.com/xuhhhhjie-pixel/contractedge/releases/tag/benchmark-v1-20261019) 获取。

独立于具体chart内容的清单转换、叠加、生成、依赖处理、schema契约或打包路径缺陷。

正式成员：**30条**。分类依据生产故障边界；verifier观察终点另行记录。case_id中的历史前缀不自动决定当前类别。

本目录是成员索引，完整实验包位于本仓库Releases，包含源码、公开任务、私有评分材料、离线依赖和固定镜像。

```bash
python3 tools/download_benchmark.py --class R3 --dest ./datasets --extract
```

模型仅可见公开白名单任务与Broken工作区，不能读取完整包中的evaluation/、GT和历史修复。支持Linux/amd64；各案例实际观察边界和资源要求见包内README、task.yaml及运行契约。
