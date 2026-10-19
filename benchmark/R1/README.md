# R1 · Helm chart 生成

具体chart的values、模板或helper生成Kubernetes对象时破坏声明意图。

正式成员：**30条**。分类依据生产故障边界；verifier观察终点另行记录。case_id中的历史前缀不自动决定当前类别。

本目录是成员索引，完整实验包位于本仓库Releases（发布完成后可下载），包含源码、公开任务、私有评分材料、离线依赖和固定镜像。

```bash
python3 tools/download_benchmark.py --class R1 --dest ./datasets --extract
```

模型仅可见公开白名单任务与Broken工作区，不能读取完整包中的evaluation/、GT和历史修复。支持Linux/amd64；各案例实际观察边界和资源要求见包内README、task.yaml及运行契约。
