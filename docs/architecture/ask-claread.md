# Ask Claread 架构说明

## 文档状态

- 状态：current implementation architecture
- 日期：2026-05-20
- 适用范围：Claread Web Reader 内当前 Ask Claread 模块
- 文档关系：
  - 当前产品边界见 `docs/product/ask-claread.md`
  - 当前主线与后续评估见 `docs/development/mainline.md`

## 架构目标

当前 Ask Claread 已冻结为：

`article-rooted, planner-first, turn-run-backed, write-confirmed`

的 Reader 内阅读助手。

它的核心目标不是做泛聊天，而是围绕当前文章、显式附件和受控跨文章扩展来回答问题、产出证据，并处理可确认写入动作。

## 当前运行分层

Ask Claread 当前采用四层真相源：

- `conversation`
- `turn_run`
- `user_visible_output`
- `eval_trace`

其中：

- `conversation` 代表单文章 active conversation
- `turn_run` 代表单次 assistant 运行
- `user_visible_output` 代表当前 run 的正式产品输出
- `eval_trace` 代表 planner、capability、action 与 supplement 的结构化审计

## 当前主链

当前 `service.py` 负责编排，稳定主链为：

1. 读取请求与线程状态
2. 解析 attachments / anchors / page identity
3. 调用 `planner.plan_request(...)`
4. 调用 reference resolver / structured asset lookup
5. materialize current / external record / external asset contexts
6. 构建 answer runtime input contract
7. 生成回答或生成 disambiguation output
8. post-process 为 `user_visible_output`
9. 写入 `turn_run.user_visible_output_json`
10. 更新 assistant message 的最小兼容状态指针

## 当前核心模块

### Planner

`planner.py` 当前统一产出：

- `resolved_intent`
- `reference_needs`
- `retrieval_needs`
- `resolved_references`
- `working_set`
- `context_plan`
- `trace_summary`
- `disambiguation_state`
- `asset_disambiguation_state`

Planner 负责决定“用什么上下文”，不负责直接生成最终回答。

### Resolver

`resolver.py` 当前负责两层解析：

- known record reference resolution
- structured asset lookup

当前支持：

- title / normalized title 命中
- explicit `record_ref.related_record`
- external stable `analysis_ref`
- external stable `supplement_ref`

当前不支持：

- hybrid retrieval
- external sentence window
- external dictionary path
- 自由的 excerpt / favorite / annotation 跨文章检索

### Runtime Contract

`runtime_contract.py` 当前是 answer runtime 的唯一输入构造入口。answer runtime 只消费：

- `planning_snapshot`
- `resolved_context_input`
- `response_contract`
- 必要的 history / attachment / citation 摘要

### Output Contract

`output_contract.py` 当前定义 Ask 的正式内部输出模型。新运行的正式产品输出统一来自 `turn_run.user_visible_output_json`，而不是 assistant message metadata。

### Repository

`repository.py` 当前负责：

- thread / message / turn_run / eval_trace 持久化
- current run hydration
- legacy metadata fallback

当前读取规则固定为：

1. 有 `current_turn_run_id` 时，优先 hydrate `user_visible_output_json`
2. 旧数据没有 current run 时，回退到 legacy metadata

## 当前公开 contract

当前公开请求固定为：

- `content`
- `page_identity`
- `attachments`
- `entry_action`
- 可选 `model`

当前完成态 payload 的正式来源是 `ReaderAskUserVisibleOutput`，对外仍保持兼容 DTO，不额外暴露内部 trace 结构。

## 当前上下文模型

### 当前文章上下文

当前文章上下文可以包括：

- primary anchor
- local context window
- article overview
- stable record insights
- dictionary context

这些内容是否进入运行，由 planner 决定。

### 跨文章上下文

当前跨文章上下文是受控扩展，只允许：

- explicit external `record_ref`
- known title reference
- external `analysis_ref`
- external `supplement_ref`

跨文章上下文当前分两层：

- `external_record_contexts`
- `external_asset_contexts`

## 当前 HITP / Disambiguation

当前 HITP 已经是正式机制，而不是异常 fallback。

### Record-level HITP

当标题引用命中多个候选文章时：

- planner 进入 `disambiguation_state`
- 当前 run 不走主回答生成
- Ask 面板展示 record-level candidate cards

### Asset-level HITP

当 external record 已确定，但 asset 命中多个候选时：

- planner 进入 `asset_disambiguation_state`
- 当前 run 不走主回答生成
- Ask 面板展示 asset-level candidate cards

## 当前 supplement 架构

当前 supplement 采用独立 supplement layer，不复用 `user_annotations`。

首批只开放：

- `assistant_supplement.grammar_note`

当前链路：

1. 生成 `supplement_candidate`
2. 用户 confirm
3. persist 到 supplement layer
4. 当前页 projection 可见
5. 可 delete
6. delete 后同步回写相关 run 的 `persisted_supplements`

## 当前持久化与恢复

### Turn Run

`reader_ask_turn_runs` 当前保存：

- run 身份
- run_attempt / supersedes_run_id
- status
- resolved_intent
- `user_visible_output_json`
- usage summary / usage event
- started / completed / failed 时间

### Eval Trace

`reader_ask_eval_traces` 当前保存：

- `planning_snapshot_json`
- `capability_trace_json`
- `action_audit_json`
- `supplement_audit_json`
- `metrics_json`

### Message

assistant message 当前只保留：

- 线程排序与 role / status
- `current_turn_run_id`
- 极薄兼容 metadata

它不再是新运行的主输出承载。

## 当前可解释性与审计

当前每轮运行都会保留：

- `context_plan`
- `resolved_context_input`
- `evidence`
- `trace_summary`
- `run_info`
- `eval_trace`

这使 Ask Claread 当前已经具备冻结后的评估基础。

## 当前明确不做

当前架构不覆盖：

- hybrid retrieval / default RAG
- 多线程列表与复杂 source management
- 独立 AI 工作台
- 直接保存整条 assistant 回答为笔记
- 把“用户学习资产自由检索”视为当前稳定主路径

## 后续评估点

当前最需要为后续产品调整保留弹性的部分是“用户学习资产”。当前架构对它仍有耦合，但稳定主路径已经收窄为：

- explicit record reference
- known title reference
- external stable analysis / supplement assets

如果后续决定缩减或移除“用户学习资产”范围，应优先评估：

- planner 的 history expansion 条件
- resolver 的 future structured lookup 扩展点
- agent tools 中仍保留的 user asset 查询接口
