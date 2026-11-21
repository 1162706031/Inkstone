# 架构图索引

更新日期：2026-11-19。

| 文件 | 定位 | 使用方式 |
| --- | --- | --- |
| [refactor-target.mmd](refactor-target.mmd) | 可信内部团队 AI 平台的重构目标架构 | 对应[总体架构](../design/ai-platform-refactor.md#architecture)，用支持 Mermaid 的编辑器预览 |
| [architecture.excalidraw](architecture.excalidraw) | 原 AgentScope 智能客服系统参考图 | 用于理解迁移资产，不作为新平台实施依据 |
| [architecture-uml.html](architecture-uml.html) | 原 AgentScope 智能客服系统网页图 | 在浏览器查看旧系统模块关系 |

重构设计以 [设计文档](../design/ai-platform-refactor.md) 为准。目标采用 FastAPI、LangChain/LangGraph、PostgreSQL 和 Redis；API/Worker 分离，Worker 仅调度根任务，主从模型调用统一计量，图文消息及 API 对话持久化。

图中的 PostgreSQL 和 Redis 由应用连接独立实例。环境隔离和连接配置见 [P1 启动与测试](../design/ai-platform-refactor.md#p1-commands)。

修改目标架构关系时，同步更新[总体架构](../design/ai-platform-refactor.md#architecture)与 `refactor-target.mmd`。旧图保留原文件与历史语义；当前图描述设计目标，不代表系统已经完成实现。

本图展示完整目标。P1 交付基础文本对话；附件 P2、工具 P3、子智能体 P4、团队应用与开放 API P5、RAG P7 逐步加入。各阶段交付和验收见[路线图](../design/ai-platform-refactor.md#roadmap)。

P1 的交付顺序与执行状态见[实施步骤](../design/ai-platform-refactor.md#implementation)和[进度与交接](../design/ai-platform-refactor.md#handoff)。
