# 汇总发布的 CI Git 历史修复

汇总提交 fb8266c 的运行源码与已验证 PR13 相同，但 runtime-portable job 只 fetch-depth=3。新增文档提交及 PR merge ref 增加了到 C1 基线的图距离，浅克隆无法证明已登记 C1 提交 2bb059bcae111bd4098d9562b7b11dfb889190bf 为祖先；run_trial_isolation_panel.py 因此正确失败关闭。原失败属于检出历史不完整，不是源码真实失去祖先，也不是候选恢复行为失败。

仅将该 Docker job 的 checkout 改为 fetch-depth=0，保留原祖先、225 项文件摘要、实际资源和 cleanup 校验。原 actual8 acceptance binding SHA 784bbe454e98e60d75e22e83e2cc6f7e3885aa8c49013b9f6abf2d9e0d3d689f 未修改；运行源码、固定测试与验收输入保持原字节。

新 development freeze 为 architecture-wrapup-ci-history-20261024，绑定更新后的 workflow；旧 r2 snapshot 与其结果保留。默认/显式新冻结、冻结校验器回归及实际8格准入 --plan 在本机服务器验证；真正的8格/24格及全CI以本次准确提交的GitHub作业为准。原源码行为成绩仅按摘要相同范围继承，不将旧CI变成新HEAD的CI。

没有改产品源码、运行限制、检查门槛或UNKNOWN判定，也没有合入未验证协议草稿或新增模型HTTP。后续协议问题和研究限制见同目录收尾说明。机器结果见 architecture-wrapup-ci-history-20261024.validation.json。
