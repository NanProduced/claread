# Workflow 历史经验

本文是参考文档，只记录旧 workflow 方案为什么被放弃，以及迁移到新仓库后应避免重复的问题。当前实现基线见 `docs/architecture/workflow.md`，不要用本文覆盖当前代码事实。

## v0

v0 暴露的主要问题：

- 标注坐标和原文位置绑定脆弱，前端渲染容易错位。
- schema 容易过度贪婪，模型输出看似丰富但稳定性不足。
- 单点失败影响整次解析，缺少分层修复和降级策略。

保留经验：

- 输出必须可定位、可解释、可降级。
- schema 设计要服务客户端渲染，而不是只追求模型表达力。

## v1

v1 尝试引入用户配置和输出契约，但仍偏早期草案。

保留经验：

- reading goal、用户偏好和 prompt 策略需要显式建模。
- 输出契约必须进入测试和文档，否则 agent 很容易改破客户端依赖。

## v2 / v2.1

v2.1 推进了更统一的设计，但仍与小程序联调、前端渲染和旧实现细节绑定较深。它不是新仓库当前主线。

保留经验：

- workflow 文档不能只描述后端，要同时说明客户端消费边界。
- 需要区分 canonical result 和 render projection。
- 历史设计文档可以解释决策背景，但不能覆盖当前代码事实。

## v3 成为当前基线的原因

v3 的核心改进是分层：

- 输入预处理与快速退出。
- reading goal 差异化策略。
- 多 agent 生成。
- normalize / ground / repair。
- canonical result 与客户端 render snapshot 分离。
- 通过 `workflow_version`、`schema_version`、prompt version 和 LangSmith metadata 支持回看。

## 新仓库原则

- 新功能优先沿 v3 的分层思路扩展。
- Web 端可以有更强 render profile，但不应要求后端专门复制一套业务服务。
- 如果要引入 Directus、LLM-as-a-Judge 或 few-shot RAG，应作为 v3 之后的扩展层，而不是把旧 regression 脚本复活。

## v3 锚点重构（2026-06）

重构原因：

- 旧 projection 依赖 text-search 型 anchor resolution（6 级 fallback），LLM 无法精确复制原文导致 40-50% anchor grounding 失败
- display text 和 source anchor 混用，短语如 "turn ... into" 被当作单一锚点

核心决策：

- 拆分 DraftAnnotation 与 NormalizedAnnotation，LLM 输出 exact source quote，后端 resolve 成 canonical span
- RenderScene 使用 UTF-16 sentence-local range，前端直接 slice 定位，不依赖 indexOf
- fail-closed 原则：错误高亮比高亮缺失更严重
- repair 从 full-result 改为 item-level patch
- tool_choice=required 对 grammar 节点有显著延迟和 token 收益，但作为可选实验配置

保留经验：

- 结构化输出可以约束 JSON 形状，不能保证模型复制的 quote 一定来自原文
- 必须同时建设 Pydantic schema、normalize 阶段 source quote resolve、清晰 drop log、Eval Center 可观察性
