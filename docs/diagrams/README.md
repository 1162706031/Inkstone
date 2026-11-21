# 架构图索引

更新日期：2026-11-02。

| 文件 | 定位 | 使用方式 |
| --- | --- | --- |
| [refactor-target.mmd](refactor-target.mmd) | 可信内部团队 AI 平台的重构目标架构 | 对应设计文档 v0.7 第 8 节，用支持 Mermaid 的编辑器预览 |
| [architecture.excalidraw](architecture.excalidraw) | 原 AgentScope 智能客服系统参考图 | 用于理解迁移资产，不作为新平台实施依据 |
| [architecture-uml.html](architecture-uml.html) | 原 AgentScope 智能客服系统网页图 | 在浏览器查看旧系统模块关系 |

重构设计以 [设计文档](../design/2026-10-31-ai-platform-refactor.md) 为准。目标采用 FastAPI、LangChain/LangGraph、PostgreSQL 和 Redis；API/Worker 分离，Worker 仅调度根任务，主从模型调用统一计量，图文消息及 API 对话持久化。

修改目标架构关系时，同步更新设计文档第 8 节与 `refactor-target.mmd`。旧图保留原文件与历史语义；当前图描述设计目标，不代表系统已经完成实现。

本图展示完整目标，不是第一版范围。P1 交付基础文本对话；附件 P2、工具 P3、子智能体 P4、团队应用与开放 API P5、RAG P7 逐步加入。阶段交付和验收以设计文档第 16 节为准。

新接手者阅读 [进度说明](../design/refactor-handoff.md) 和 [P1 实施方案](../design/2026-11-02-p1-implementation.md)，按当前阶段建设图中模块。
