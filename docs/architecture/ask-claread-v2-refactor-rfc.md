# Ask Claread V2 Refactor RFC

## 文档状态

- 状态：active RFC
- 日期：2026-05-19
- 适用范围：Claread Web Reader 2.0 完成前置底座改造后，Ask Claread V2 的正式重构实施
- 文档关系：
  - `docs/product/ask-claread-v2-product-spec.md` 定义目标产品模型与能力边界
  - 本文定义 Ask Claread V2 的目标实施架构、模块边界、前后端 contract 方向与迁移顺序
  - `docs/product/ask-claread-v1.md` 仅保留为第一版对照，重构完成后删除

## 1. 背景与目的

Ask Claread V1 已证明两件事：

- LLM 调用、文章绑定线程、SSE、引用、结构化 sidecar 与保存确认在技术上可行
- 但当前实现仍偏“解析页里的 AI 控制台”，不适合 Claread 作为英文阅读与解析产品的核心场景

Ask Claread V2 的目标不是修补现有 chatbox，而是在新的 Web Reader 2.0 底座上，把 Ask 重构为：

`article-rooted, intent-first, planner-driven, active-document-first, schema-aware, write-confirmed`

的阅读助手 harness。

## 2. 先决条件

Ask Claread V2 **不应先于 Web Reader 2.0（Plate 底座接入）正式开工**。

Reader 2.0 至少要先稳定以下前置能力：

- `canonical render_scene -> Web document runtime` 的统一 projection
- 统一的 `attachable object model`
- 统一的 selection / attachment / jump contract
- translation、workflow analysis object 与 future supplement 的稳定对象身份
- SelectionToolbar 到 Ask 的显式上下文注入链路

换句话说：

`先 Plate Reader 2.0，再 Ask Claread V2`

不是项目管理顺序，而是架构依赖顺序。

## 3. 非目标

本 RFC 首批明确不覆盖以下方向：

- 全局 AI 页面
- 泛聊天 / 泛任务执行
- 默认全历史注入
- 团队协作 / 社群化权限模型
- 独立练习系统
- 多线程 / 历史会话列表
- 复杂 source management 面板
- 将 Ask 结果直接污染 workflow canonical output

这些方向不代表永久不做，但不进入本次重构范围。

## 4. 产品边界约束

Ask Claread V2 的首批边界已经确定：

- Reader 内、`article-rooted`
- 每篇解析页一个 `active conversation`
- 首批不开放 `New chat`
- `reset conversation state` 只清空会话影响，不删除已落地资产
- 默认工作域是当前文章
- 读取范围可按需扩到当前用户自己的其他解析页与学习资产
- 写入范围首批仅限当前文章当前页可见对象
- 分享页预览不开放 Ask；只有保存/复制为自己的页面后才进入 Ask 范围

## 5. 目标运行模型

### 5.1 公开请求 contract

V2 公开请求 contract 去 `task_mode`，以以下输入为核心：

- `content`
- `attachments`
- `entry_action`
- `model`（可选）

前端不再替用户做模式分类；最终 intent 由服务端 planner / agent 决定。

### 5.2 上下文 contract

公开上下文输入统一为：

- `page_identity`
- `attachments`
- `entry_action`

V2 不再保留：

- `reader_focus`
- 隐式当前句
- 隐式当前段

服务端必须显式产出：

- `context_plan`
- `resolved_context_input`

### 5.3 会话 / 运行时分层

Ask Claread V2 采用四层分离：

- `conversation`
- `turn_run`
- `user_visible_output`
- `eval_trace`

其中：

- `reset` 只清空会话影响
- `regenerate / retry` 是同一 user turn 的新 run，不新增 user turn

## 6. Harness 架构

### 6.1 总体分层

Ask Claread V2 采用：

`planner + typed capability execution + productized post-process`

而不是：

- 纯 service if/else
- 纯 tool-call agent
- 把结构化结果散落在流尾部补丁中

### 6.2 主要子层

#### A. Intent Planner

职责：

- 解析用户自然语言
- 结合 `page_identity / attachments / entry_action`
- 决定 `resolved_intent`
- 判断是否需要局部上下文、全文上下文、跨文章读取、supplement candidate、写动作 candidate

#### B. Context Planner

职责：

- 显式 attachment 优先
- 语义引用解析优先于隐式焦点
- 局部扩展先于跨文章扩展
- 产出可解释的 `context_plan`

#### C. Typed Capabilities

职责：

- 用稳定、窄 schema 的能力接口执行：
  - local context
  - article overview
  - annotation / analysis context
  - cross-article lookup
  - dictionary
  - structured supplement generation

#### D. Productized Post-Process

产出显式产品输出层：

- `response_blocks`
- `evidence`
- `action_proposals`
- `supplement_candidates`
- `trace_summary`

#### E. Confirm / Commit Layer

职责：

- 统一处理笔记、摘录、收藏、supplement 写入
- 保证来源、删除、回显与生命周期一致

## 7. Page Identity 与 Article Overview

### 7.1 Page Identity

`page_identity` 首批采用“解析页运行身份包”，而不是通用文档元数据包。

最小字段方向：

- `record_id`
- `title`
- `surface=reader`
- `source=reader_2_0`
- `available_context_capabilities`
- `has_article_overview`
- `has_sentence_entries`
- `has_annotations`
- `has_user_assets`

说明：

- `content_language`
- `content_length_class`
- `workflow_version`

这些字段不进入首批公开 request contract；如后续 planner / context planner 确认需要，只允许作为内部运行产物补充，而不是直接扩大 `page_identity` 对外字段集。

### 7.2 Article Overview

`article_overview` 是高价值文章级上下文资产，但不默认注入每轮运行。

长期正确方案：

- 在 analyze workflow 中条件产出并持久化
- 流程为：
  - `prepare_input` 做 gate
  - `parallel_agents` 运行独立 `article_overview` 子任务
  - `project_render_scene` 投影到最终 render scene

约束：

- 输入只是一小段或几句英语时，不应硬总结
- Ask 默认只知道 overview 是否存在；是否读取由 planner 决定

## 8. Attachments 与 Reference Resolution

### 8.1 Attachment 一级类型

首批 attachment 一级类型收敛为：

- `text_selection`
- `annotation_ref`
- `analysis_ref`
- `supplement_ref`
- `record_ref`

其中：

- `analysis_ref` 覆盖稳定可引用分析对象，包括：
  - `grammar_note`
  - `sentence_analysis`
  - `translation`
  - 稳定 glossary / vocab analysis object
- `favorite` / `vocabulary` 暂不作为一级 attachment 类型

### 8.2 Reference Resolution

Reference resolution 采用：

`planner 语义判断 + typed resolver`

原则：

- 先 resolve 成显式对象，再进入回答层
- `disambiguation / HITP` 是正式机制，不是异常 fallback
- 历史检索必须带 stop policy：
  - 尝试次数上限
  - 候选质量阈值
  - 明确退出方式

## 9. Retrieval / RAG 路线

### 9.1 总原则

Ask Claread 的 retrieval 总原则是：

`active-document-first, retrieval-second`

当前文章内问答默认不上 RAG。

### 9.2 路线

首批跨文章读取采用分层路线：

1. `reference resolution`
   - 已知文章引用优先按 `title / alias / metadata` 解析
2. `structured asset lookup`
   - 指定文章或当前文章内结构化对象查找
3. `hybrid retrieval`
   - 仅用于未知范围的跨文章查找、历史资产语义查询与相似内容召回

### 9.3 基础设施原则

先定 retrieval architecture，再定具体产品：

- `source of truth = Postgres`
- `retrieval index = rebuildable`
- `retrieval strategy = hybrid`

在当前阿里体系下，优先评估：

- `OpenSearch Vector Search Edition`

当前 few-shot 使用的 Zilliz 不直接延展为 Ask 主检索层。

## 10. Prompt Registry

Ask Claread 的 prompt 不应继续硬编码在 service 运行时拼接逻辑里。

建议建立独立 prompt registry，至少分层为：

- `system`
- `planner`
- `answer`
- `schema`
- `policy/examples`

要求：

- prompt version 可追踪
- 变量注入
- 可与 eval 绑定
- 不再依赖一个万能 prompt 同时承担回答、检索、schema 输出与写动作策略

## 11. Structured Supplement Generation

### 11.1 首批目标

首批结构化 AI 补充写入只开放：

- `assistant_supplement.grammar_note`

### 11.2 Supplement Layer

补充写入单独建模为 `overlay / supplement layer`，不复用 `user_annotations`。

最小模型必须支持：

- 身份
- 绑定
- 内容
- 来源
- 生命周期
- 资产管理

并保留：

- 稳定 `target_key / anchor payload`
- `schema_version`
- `created_from_turn_run_id`

### 11.3 生成链路

主回答链路与结构化 schema 生成链路分离：

- 主回答负责用户可读输出
- 独立 typed capability 负责 supplement candidate
- candidate 需经过：
  - 适配性判断
  - schema 约束
  - 业务校验
  - 用户确认
  - supplement layer 写入

AI supplement 结果必须：

- 当前页可见
- 可删除
- 可收藏并纳入学习资产
- 与 workflow 结果明确区分来源

## 12. Streaming Contract

Ask Claread V2 采用两层流：

- `user-visible stream`
- `internal trace`

规则：

- 主回答通过 `content_delta` 流式输出
- thinking / steps 默认折叠
- `evidence / action_proposals / supplement_candidates` 整块 ready 后发送
- `completed payload` 是单轮结果的最终真相源

## 13. 模型路由

Ask Claread 不假设所有能力都使用同一个模型。

首批按任务角色做模型分层，而不是按功能按钮散配：

- `primary_answer`
- `planner_or_resolution`
- `structured_writer`
- `retrieval_aux`

由于当前统一使用阿里百炼，模型配置应抽象为三层：

- `user-facing primary model`
- `task-role routing policy`
- `provider-backed model mapping`

首批最值得优先独立的角色是：

- `structured supplement generation`

## 14. 模块边界

Ask Claread 在项目结构上应作为独立复杂模块隔离。

### 前端

- Ask surface
- composer / attachments
- output rendering
- confirm actions
- Ask-specific transport/state

### Web BFF

- Ask route
- request / response projection
- streaming transport

### 后端 Ask 模块

- planner
- context planner
- typed capabilities
- post-process
- actions / confirm
- supplement repository
- conversation / turn_run / eval trace

## 15. 迁移顺序

### Phase A: Reader 2.0 先行

- Plate Reader 2.0 接入
- attachable object model
- selection / jump / Ask bridge 稳定
- translation / analysis object / future supplement 的对象语义打底

### Phase B: Ask Contract Rewrite

- 去 `task_mode`
- 去 `reader_focus`
- 引入 `attachments + entry_action + page_identity`
- 重做 request / response / completed payload contract

### Phase C: Planner / Context / Retrieval

- intent planner
- context planner
- reference resolution
- article overview 接入
- controlled cross-article retrieval

### Phase D: Supplement / Confirm / Persist

- supplement layer
- typed `grammar_note` candidate generation
- confirm / persist flow

### Phase E: Trace / Eval / Polish

- user-visible stream polish
- eval trace 完整化
- 专项 eval 接入
- regenerate / reset / evidence UX 打磨

## 16. 仍待后续实施评审的事项

以下事项方向已大致确认，但具体实现暂不在本 RFC 写死：

- Ask 专项 eval 的最终平台化落地
- retrieval 生产引擎的最终选型与索引同步方案
- attachments / supplement / SSE 的最终字段命名与 API 细节
- Directus 可视化评测与运营面板如何接入 Ask 的 trace / eval 数据

## 17. 一句话结论

Ask Claread V2 不是现有 chatbox 的续修，而是：

`以 Plate Reader 2.0 为前置底座、以 planner-first harness 为核心、以 active-document-first retrieval 为约束、以 typed supplement generation 为特色的 article-bound 阅读助手重构。`
