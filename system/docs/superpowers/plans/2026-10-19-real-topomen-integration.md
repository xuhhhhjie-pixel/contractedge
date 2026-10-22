# Real TopoMen Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline. Steps use checkbox syntax.

**Goal:** 依据真实审查结果把TopoMen Agent接入ContractEdge第7步并验证正式开发案例。
**Architecture:** 真正DefaultAgent消费受限Model/Environment桥接；ContractEdge保持原有公开输入、计划验收、文件事务、反馈及预算。部署Docker调度器不承担案例语义判定。
**Tech Stack:** Python3.10，TopoMen1.1.2，Pydantic2/Jinja2，现有ContractEdge JSON schema/事务编辑。
**Spec:** docs/specs/ContractEdge_TopoMen_Integration_v1.md

## Global Constraints

冻结150不修改；隐藏评价独立。实际源码路径与hash绑定，缺失拒绝而非fallback。共享BudgetedProvider且不执行shell。现有codex/first-three-stages工作分支保留全部原有改动；后台v5在冻结副本运行，不能修改它。

## Review Focus

源码路径/哈希漂移必须失败；非法编辑不执行；TopoMen全局.env与密钥不能进入轨迹；模型预算不能重置；新后端确实经实际DefaultAgent而非仅标注名字。

### Task1：完成审查并修复已复现Python3.10兼容性
- [x] 原始pytest出现9个datetime.UTC收集错误，保存完整日志及原始源码身份。
- [x] 修改4个模块UTC引用，运行原生pytest/ruff，记录实际结果。
- [x] 验证典型Python项目与当前Helm案例的实际planner结果，定位可复用边界。

### Task2：真实Agent受限接入
**Files:** execution/topomen.py提取请求/响应校验；新execution/topomen_agent.py；orchestration/repair.py按显式后端选择；tests/test_topomen_agent.py；可选依赖/本地配置。
**Consumes:** 已验证plan/source/policy、共享provider/token_counter/config。
**Produces:** TopoMenAgentExecutor.execute，返回真实APPLIED、原生agent轨迹、源码来源与模型响应。
- [x] 编写成功/源码漂移/非法shell/预算耗尽回归，先运行确认失败。
- [x] 实现共用request/response校验，真实DefaultAgent Model/Environment协议桥接及来源检查。
- [x] 默认配置选真实后端并接通repair；原direct显式选择，错误后端不回退。
- [x] 全ContractEdge suite、拓扑原生测试、lint/compile检查。

### Task3：真实验收与报告
- [x] 当前正式开发案例隔离准备→真实Agent执行公开回放补丁→公开launcher→选定候选→独立评分。
- [x] 独立代码审查；修复Important并重复针对性检查。
- [x] 更新CURRENT_STATUS、README、框架规格和源码审查报告，明确自定义格式与原生能力边界。

Ruling: 用户已委托按真实合理情况完成接入，不增加逐阶段审批。保留原有工作分支及未提交修改，不整体提交。模型回放只证明接口和工程效果，不声称自主模型成功。
