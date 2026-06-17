# Ask Claread 架构说明

## 文档状态

- 状态：current implementation architecture
- 日期：2026-06-10
- 适用范围：Claread Web Reader 内当前 Ask Claread 模块
- 文档关系：
  - 当前产品边界见 `docs/product/ask-claread.md`
  - 当前主线与后续评估见 `docs/development/mainline.md`

## 架构目标

当前 Ask Claread 已冻结为：

`article-rooted, agent-loop-first, turn-run-backed, write-confirmed`

的 Reader 内阅读助手。

它的核心目标不是做泛聊天，而是围绕当前文章、显式附件和受控跨文章扩展来回答问题、产出证据，并处理可确认写入动作。

`agent-loop-first` 表示主回答 agent 直接消费最小 payload（overview、anchors、attachments、history），并按需调用 read tools 解析上下文，而不是先经过独立的 semantic planner LLM 预解析。`planner_first` 仅作为历史 trace value 保留，没有任何 live condition 触发它。

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
3. live service 不再调用 route resolver；`planner_first` 仅作为 trace value 保留
4. 基于 attachments / anchors 构造 minimal context plan 与 minimal trace summary，不调用任何 planner LLM
5. 若为 `selection_toolbar` 触发的快捷分析操作，先执行结构化句法生成
6. 构建 answer runtime input contract（包含 overview、anchors、attachments、history、agent-loop hints）
7. 主回答 agent 按需调用 read tools（`get_record_context` / `resolve_known_reference` / `get_record_insights` / `get_user_vocabulary_book` 等）解析上下文
8. 生成回答或生成 disambiguation output
9. post-process 为 `user_visible_output`
10. 写入 `turn_run.user_visible_output_json`
11. 更新 assistant message 的最小兼容状态指针

其中第 5 步的快捷分析路径具有固定约束：

- `entry_action in {"why_here", "explain_this"}`
- 且主 attachment `metadata.source_surface == "selection_toolbar"`
- Ask 会先运行 `generate_sentence_annotation`
- `grammar` 可基于整句理解，但必须保留 focus span
- `breakdown` 只接受 sentence-level 输入；片段级输入会返回结构化 `not_applicable`

这意味着快捷分析已经不是“先发一条默认聊天消息，再希望 agent 自己选 tool”，而是 Ask 主链中的显式结构化预处理阶段。

## 当前核心模块

### Planner

`planner.py` 当前保留为 agent-loop runtime 复用的 pure helper 集合，不再调用任何 planner LLM。当前仍提供的 helper 包括：

- `MinimalPlanningSnapshot` / `build_minimal_context_plan` / `build_minimal_trace_summary`
- attachment id parser
- context plan / trace summary 构造
- resolved context helper

`planner.plan_request(...)`、`build_planner_input(...)` 和基于 `ReaderAskPlannerDecision` 的 decision-consumption helper 已删除；旧 semantic planner 的 input / decision / fallback 组装逻辑不再作为可调用架构面存在。

`ReaderAskPlanningSnapshot` 作为 typed dataclass 仍保留，用于 trace 与 eval 观测，但 `planner_decision` / `planner_validation_status` / `reference_needs` / `retrieval_needs` / `resolved_references` / `structured_asset_needs` / `structured_asset_resolution` / `working_set` / `disambiguation_state` / `external_asset_disambiguation_state` / `clarification_only` 等字段在 agent-loop-first 路径下不再由独立 planner LLM 产出，而是由主回答 agent 在 tool loop 内按需解析。

live service 不再调用 route resolver；`"planner_first"` 仅作为 `PlannerRoute` Literal 与 trace value 保留，没有任何 live condition 触发它。`planner_route_policy.py` 仅保留历史 literal、兼容 helper 和 agent-loop hint predicate。

### Resolver

`resolver.py` 当前负责两层解析：

- known record reference resolution
- structured asset lookup

当前支持：

- title / normalized title 命中
- explicit `record_ref.related_record`
- external stable `analysis_ref`
- external stable `supplement_ref`

补充约束：

- external `analysis_ref / supplement_ref` 的 resolved asset 现在必须带正文级 `content_md` 与 compact summary，供 Ask runtime 直接回答
- explicit asset ref 与 resolver 命中同一对象时，以 resolver 返回的富上下文对象为准，不保留摘要级占位 ref
- known reference resolution 当前 pipeline 是 candidate pool -> deterministic title scoring -> optional semantic rerank -> deterministic resolution policy
- semantic rerank 边界已经可注入，但生产默认 `REFERENCE_RERANKER_ENABLED=False`，不启用真实 LLM rerank
- `resolution_meta` 是 planning snapshot / eval 观察数据，不进入 answer agent prompt

当前不支持：

- hybrid retrieval
- external sentence window
- external dictionary path
- 自由的 excerpt / favorite / annotation 跨文章检索

后续如果启用真实 LLM / embedding rerank，必须先补齐 timeout、candidate limit、成本控制、trace/eval 样本和 fallback 策略。`_CROSS_LANG_MAP` / `_cross_lang_score()` 作为 legacy semantic fallback 保留，只有能证明中文用户标题引用能力不回退时才评估移除。

### Runtime Contract

`runtime_contract.py` 当前是 answer runtime 的唯一输入构造入口。answer runtime 只消费：

- `planning_snapshot`
- `resolved_context_input`
- `response_contract`
- 必要的 history / attachment / citation 摘要
- `submission_mode`
- `quick_action_annotation`

planner schema 中的 `answer_policy` 当前属于可评估的后续输入，不等同于 answer prompt 的强约束。若要接入 answer agent，必须先明确它是硬约束、软偏好，还是可被 answer agent 覆盖的策略建议，并补对应 eval。

### Agent Tools / Write Gate

Ask Claread 的 agent-callable tool surface 由 `reader_ask_tool_registry.py` 统一定义。当前固定为 8 个 agent-callable 工具：

- `get_record_context`
- `get_record_insights`
- `get_user_vocabulary_book`
- `resolve_known_reference`
- `generate_sentence_annotation`
- `propose_save_note`
- `propose_save_highlight`
- `suggest_prompts`

此外 registry 中还有 3 个 non-callable 工具：

- `lookup_record_by_embedding` — reserved，未来 pgvector RAG 占位，`agent_callable=False`
- `lookup_dictionary_entry` — deprecated，仍被 dictionary attachment 闭包调用，`agent_callable=False`
- `run_dictionary_ai_context_explain` — deprecated，仍被 dictionary AI 闭包调用，`agent_callable=False`

`search_user_vocabulary` 已在 Round 5 完全移除（零调用者，由 `get_user_vocabulary_book` 替代）。

工具契约的稳定边界：

- tool name 必须来自 registry 常量；`@agent.tool(name=...)` 与 `run_tool(...)` 不允许回退为硬编码字符串。
- tool observation 经 `reader_ask_tool_observation.py` 规范化为 `status`、`summary`、`next_actions`、`artifacts`。
- `reader_ask_tool_runtime.py` 负责 budget、trace、SSE `tool.started / tool.completed / tool.failed` 事件，以及 availability hard enforcement。
- `reader_ask_tool_policy.py` 负责构造 tool availability。当前默认 policy 仍允许全部 8 个 agent-callable tools，以保持生产行为不变；后续收紧可用工具必须经由该 policy，不由 planner 直接越权。
- `reader_ask_tool_registry.py` 中的 `output_kind` / `observation_statuses` 描述 tool implementation 自身的 IO contract；runtime wrapper 注入的 policy error 另由 runtime contract 测试覆盖。

写动作采用 proposal-only 模型：

- `propose_save_note` / `propose_save_highlight` 只创建 runtime `action_request`，且 `requires_confirmation=True`。
- 无 primary anchor 时由 write gate 直接返回稳定 error payload，不消耗 tool budget，也不创建 action request。
- `note_text` 缺失属于 tool 内部校验，会经过 `run_tool`，消耗一次 tool budget，并返回稳定 error observation。
- grammar supplement 的 `create_supplement_grammar_note` 仍是 confirmation path 的 action type，不是 agent-callable tool，不进入上述 8 个 tool registry。

### Facade / Invocation Wiring

`service.py` 当前仍是 Ask Claread 的入口编排层，但不再直接构造 agent deps、reader-ask model route 或 agent stream lifecycle。

稳定 wiring 边界为：

- `agent_deps_factory.py` 是 `ReaderAskAgentDeps` 的唯一 service 路径构造入口，并统一注入 `tool_availability`。
- `agent_invocation.py` 负责 reader-ask agent/model resolution、non-streaming replan 调用与 agent stream lifecycle facade。`reader_ask_planner` model route 已在 Round 16 彻底移除，`planner_model_name` DTO 字段已在 Round 18 从后端与 Web 契约中删除。
- `ReaderAskAgentDeps.event_queue` 是 stream-wide event bus，类型语义为 `Queue[tuple[str, dict[str, Any]]]`；`ToolEventName` 只约束 tool runtime 内部 `_emit_tool_event(...)` 的 `tool.started / tool.completed / tool.failed`。

`service.py` 不应重新直接调用：

- `ReaderAskAgentDeps(...)`
- `build_tool_availability(...)` / `ToolAvailabilityInput(...)`
- `get_reader_ask_agent()` / `build_reader_ask_prompt(...)`
- reader-ask route 常量或 `build_model_for_route(...)`
- `agent_runner_svc` 的 stream lifecycle 函数

### Output Contract

`output_contract.py` 当前定义 Ask 的正式内部输出模型。新运行的正式产品输出统一来自 `turn_run.user_visible_output_json`，而不是 assistant message metadata。

### Retry / Regenerate

`retry / regenerate` 当前统一视为同一 user turn 下的新 assistant run：

- 不新增 user turn。
- 新 run 会 supersede 被 retry 的旧 run。
- 当前用户可见输出始终以最新 run 的 `turn_run.user_visible_output_json` 为准。
- interrupted run 可以保留 partial output 作为历史状态，但前端入口文案是“重新生成”，不承诺从断点续写。
- 刷新页面只恢复已持久化的正文与 thinking 快照，不自动恢复原 SSE 连接，也不继续跑同一个未完成 run。

### Repository

`repository.py` 当前负责：

- thread / message / turn_run / eval_trace 持久化
- current run hydration
- legacy metadata fallback

当前读取规则固定为：

1. 有 `current_turn_run_id` 时，优先 hydrate `user_visible_output_json`
2. 旧数据没有 current run 时，回退到 legacy metadata
3. `interrupted` 是正式持久状态，不再在 hydration 时退化成 `failed`

## 当前公开 contract

当前公开请求固定为：

- `content`
- `page_identity`
- `attachments`
- `entry_action`
- 可选 `model`

当前完成态 payload 的正式来源是 `ReaderAskUserVisibleOutput`，对外仍保持兼容 DTO，不额外暴露内部 trace 结构。

当前 SSE 额外稳定事件包括：

- `reasoning.started / reasoning.delta / reasoning.completed`
- `message.interrupted`

补充：

- `reasoning.*` 在流式阶段仍按 SSE 增量驱动
- `streaming` run 会按节流 checkpoint 持续回写 `turn_run.user_visible_output_json`，至少覆盖当前 `content_md / reasoning_md / reasoning_status`
- 一旦本轮 run 完成或中断，最终 `reasoning_md / reasoning_status` 会并入 `turn_run.user_visible_output_json`
- 刷新页面后的 thinking 恢复，优先读取当前 `turn_run.user_visible_output_json` 中最后一次已持久化快照；这只恢复已生成内容，不恢复原 SSE 连接或自动续跑模型

当前完成态 payload 额外暴露两个与快捷分析相关的稳定字段：

- `submission_mode`：`chat | quick_action`
- `response_cards`：包含 `grammar_note_card` 与 `sentence_breakdown_card`

## 当前上下文模型

### 当前文章上下文

当前文章上下文可以包括：

- primary anchor
- local context window
- article overview
- stable record insights
- dictionary context

这些内容是否进入运行，由主回答 agent 在 tool loop 内按需决定。

### Article Overview / Overview Hint

`article overview` 当前被视为可选增强 observation，而不是 Ask 的必要前提。

读取优先级：

1. learning `analysis_results.page_state_json.derived.overview_hint`，仅 `status=ready`
2. academic `render_scene.content_summary.overview`
3. academic `sentence_entries.content_summary` 的兼容降级文本
4. 无 overview

补充语义：

- learning overview 通过异步、best-effort 的轻量 `overview hint` 生成
- overview hint 允许返回 `unavailable`，表示文本过碎、过短或缺乏篇章逻辑
- Ask answer agent 只把它当弱线索，用于 record 判别和 article-level 首轮理解
- 需要更高覆盖度时，仍应主动拉 `record_context`、`source_excerpt` 或 external context

### 跨文章上下文

当前跨文章上下文是受控扩展，只允许：

- explicit external `record_ref`
- known title reference
- external `analysis_ref`
- external `supplement_ref`

跨文章上下文当前分两层：

- `external_record_contexts`
- `external_asset_contexts`

其中 external record context 的 article overview 同样遵循“有则用之”的弱增强语义，并保留来源标识：

- `learning_overview_hint_agent`
- `academic_render_scene`

## 当前快捷分析模型

快捷分析仍属于 Ask 线程，但不再伪装成普通聊天提问。

### Grammar Quick Action

- `sentence` 选区：按整句分析
- `text_range` 选区：保留原始 focus span，并自动扩展到所在整句理解
- 若片段过短、无法稳定套用语法结构，返回结构化 `not_applicable`

### Breakdown Quick Action

- 仅 `sentence` 选区允许产出正式 `sentence_breakdown_card`
- `text_range` 不强行拆句，会返回“建议扩展到整句”的结构化不可分析结果

### Frontend Rendering

- `submission_mode=quick_action` 的 assistant turn 采用 `response_cards -> answer -> citations` 的 block 顺序
- user turn 采用 compact operation header，不再显示伪聊天气泡
- composer context 只表示“下一轮待发送的上下文”，发送成功后清空

## 当前 HITP / Disambiguation

当前 HITP 已经是正式机制，而不是异常 fallback。

### Record-level HITP

当标题引用命中多个候选文章时：

- planner 进入 `disambiguation_state`
- 当前 run 不走主回答生成
- Ask 面板展示 record-level candidate cards

### Asset-level HITP

当 external record 已确定，但 asset 命中多个候选时：

- planner 进入 `external_asset_disambiguation_state`
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

补充：

- `user_visible_output_json` 现在同时承载 final output 和 streaming checkpoint
- checkpoint 由服务层按节流策略回写，不逐 token 落库
- `message` 层仍只保留最小兼容状态与 `current_turn_run_id` 指针

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
- 把用户高亮 / 用户笔记当作独立可检索资产中心
- 开放式 `plan -> act/tool -> observe -> revise` agent loop
- 新 tracing backend 或通用 checkpoint 表，除非有明确 eval、恢复或运维需求

## 当前边界说明

Reader 标注体系完成重构后，Ask Claread 已不再依赖“用户学习资产”聚合层。当前稳定主路径收口为：

- explicit record reference
- known title reference
- explicit annotation reference（通过 `annotation_ref.anchor_payload.anchor_type` 区分高亮 `user_annotation` 或笔记 `reader_note`）
- external stable analysis / supplement assets

后续若继续扩展跨文章能力，应优先评估：

- agent-loop runtime 的 history expansion 条件
- resolver 的 future structured lookup 扩展点
- agent tools 中是否需要新增受控的跨文章引用入口

Ask Claread 当前已是 agent-loop-first harness：主回答 agent 直接消费 minimal payload，按需调用 controlled read tools 解析上下文，再生成回答并 stream/checkpoint/recovery。后续若评估受限 multi-step reader loop，必须先限定最大 step 数、稳定每步 tool observation、接入 eval trace，并保证 UI 只表达用户能理解的处理状态。下一轮重构方向（single agent loop / multi-step reader loop）见 `docs/development/mainline.md`。

### 当前 Attachment 类型与 Anchor 类型

`ReaderAskAttachmentKind` 枚举当前值：

- `text_selection` — 用户选区
- `annotation_ref` — 引用用户批注资产；具体是高亮还是笔记，由 attachment payload / metadata 决定
- `analysis_ref` — 引用分析结果
- `supplement_ref` — 引用 AI 补充
- `record_ref` — 引用文章

`ReaderAskAnchorType` 枚举当前值：

- `sentence`
- `text_range`
- `multi_text`
- `sentence_entry`
- `user_annotation` — 锚点类型：用户高亮
- `reader_note` — 锚点类型：用户笔记
- `dictionary_entry`

当前明确不存在：

- `paragraph`
- `article`
- 独立的 `highlight` anchor type
- 独立的 `annotation` anchor type

当前公开 `ReaderAskAttachmentPayload.anchor_type` 只允许：

- `sentence`
- `text_range`
- `multi_text`

`annotation_ref` 在服务端解析后，才会被映射成 `user_annotation` 或 `reader_note`。也就是说：

- `annotation_ref` 是 attachment kind
- `user_annotation` / `reader_note` 是 normalized anchor type
- 不能把两者混写成同一层枚举
