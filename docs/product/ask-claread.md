# Ask Claread

## 文档状态

- 状态：current agent-loop baseline
- 日期：2026-06-17
- 适用范围：Claread Web Reader 内当前 Ask Claread 模块
- 事实基线：以当前代码、测试与可验证行为为准
- 文档关系：
  - 当前运行架构见 `docs/architecture/ask-claread.md`
  - 当前开发主线见 `docs/development/mainline.md`
  - 本文描述当前可用边界，不记录重构过程

## 模块定义

Ask Claread 是 Reader 内、围绕当前文章工作的阅读助手。它的当前定位是：

`article-rooted, attachment-first, agent-loop-first, write-confirmed`

它不是：

- 泛聊天产品
- 独立 AI 页面
- 默认全历史注入的学习总结器
- 以模式切换为主心智的教学工具箱

## 当前产品原则

### 自然语言优先

用户先提问，系统再根据问题、附件和页面身份决定回答意图、上下文范围和是否需要结构化附属内容。

### 当前文章优先

默认工作域是当前文章。系统不会默认把“当前句/当前段/全历史”隐式塞进运行输入。

### 上下文显式可见

每轮运行都会显式产出：

- `context_plan`
- `resolved_context_input`
- `evidence`
- `trace_summary`

用户应能知道本轮回答基于哪些对象、是否扩展到了其他文章、是否使用了词典或稳定资产。

composer context chip 是“下一轮发送时会带入的上下文承诺”。如果某个 selection 只是阅读态提示，不应放在 composer context chip 区；只要它以 chip 形式出现，发送时就必须进入 request attachments。

### 写动作必须确认

当前保留的写动作都需要确认后才执行，且必须明确目标锚点和写入结果。

高风险写动作，包括未来可能出现的批量写入、跨文章整理或删除类动作，也必须先计划/确认，不能由模型静默执行。

### 状态文案产品化

Ask 面板向用户展示“系统正在做什么”，不暴露 token budget、retrieval chunk、agent repair、checkpoint flush 等技术细节。

当前约定：

- 普通生成：`正在生成`
- 问题理解 / 意图归类：`正在理解问题`
- 上下文整理：`正在整理上下文`
- context compression：`上下文压缩中`
- 词典查询：`正在查词典`
- 外部文章或资产解析：`正在查找相关文档`
- 压缩失败时给可操作错误，例如提示用户移除部分上下文后重试

完整 `context_plan / evidence / trace_summary` 仍作为可展开的信任层，不默认占据普通用户界面。

## 当前公开 contract

当前 Ask 请求主 shape 固定为：

- `content`
- `page_identity`
- `attachments`
- `entry_action`
- 可选 `model`

当前不再使用：

- `task_mode`
- `reader_focus`
- `anchors` 作为公开主字段

### `page_identity`

当前 `page_identity` 采用轻量字段集：

- `record_id`
- `title`
- `surface=reader`
- `source=reader_2_0`
- `available_context_capabilities`
- `has_article_overview`
- `has_sentence_entries`
- `has_annotations`
- `has_reader_notes`

补充：

- `has_article_overview` 只是前端 affordance hint，不是 Ask 的唯一真相源
- Ask 当前会优先读取后端可用的 article overview / overview hint，再决定是否把它纳入 runtime

## 当前交互模型

### 会话模型

- 每篇文章只有一个 `active conversation`
- 前端不开放 `New chat`
- 提供 `reset conversation state`
- `reset` 只清空会话影响，不删除已落地资产

### retry / regenerate

- `retry / regenerate` 是同一 user turn 的新 run
- 不新增 user turn
- 当前用户可见结果始终以最新 run 为准
- retry 入口绑定 assistant run，不直接绑 user bubble
- 若上一轮流式中断并保留 partial output，前端文案显示为“重新生成”
- 当前不承诺断点续写；重新生成会创建新的 run，并 supersede 旧 run

### 输入与附件

Ask 当前采用 `attachment-first` 入口。显式带入当前讨论的对象包括：

- `text_selection`
- `annotation_ref`
- `analysis_ref`
- `supplement_ref`
- `record_ref`

Ask 面板内也支持显式加入“我的另一篇文章”作为 `record_ref.related_record`。

补充：

- Reader 工具条里的 `语法解析 / 句子拆分` 已视为 Ask 内的快捷分析操作
- 这类操作仍进入当前 Ask 线程，但不是普通聊天提问
- 当前 UI 以结构化分析结果卡为主，文字说明为辅助层
- composer context 只表示“下一轮待发送的上下文”，发送成功后会清空
- live selection 只作为“当前可带入”的阅读态提示，不等于已经排队发送
- context picker 选中的对象会变成 composer chip；chip 等于 runtime attachment，不作为技术 trace 展示
- chip 应短、小、可移除、可跳转；长文本只在 hover / popover 中展示

## 当前已实现能力

### 当前文章内能力

- 解释、拆句、词义、语法、练习等意图判定
- 局部上下文窗口
- 当前文章 stable record insights
- 当前文章 article overview（若已有）
- 生词本查询
- dictionary anchor / dictionary attachment 的文章语境解释

补充：

- learning workflow 的 article overview 当前通过异步、best-effort 的 `overview hint` 提供
- `overview hint` 允许返回 `unavailable`，表示文本过碎、过短或缺乏篇章逻辑，不视为失败
- Ask 只把 overview 当成弱增强线索，用于首轮理解和 record 判别；深入问题仍会按需拉全文 excerpt / record context

### 跨文章能力

当前跨文章能力是受控扩展，不是自由历史问答。当前已实现：

- known title reference resolution
- record-level disambiguation / HITP
- asset-level disambiguation / HITP
- explicit external `record_ref`
- external `analysis_ref`
- external `supplement_ref`
- external record article overview + stable insights
- external `analysis_ref / supplement_ref` 当前会把被显式引用对象的正文级内容并入 Ask runtime，不再只提供标题或短摘要

补充：

- external record overview 当前也遵循“有则用之”的语义
- academic record 继续复用现有 `content_summary.overview`
- learning record 若 overview hint 仍处于 `pending / failed / unavailable`，Ask 会正常回退到其他上下文，不进入特殊错误路径

当前不承诺：

- 默认全历史搜索
- hybrid retrieval
- 外部 sentence window
- 外部 dictionary path
- 跨文章用户资产检索（高亮/笔记/收藏）
- “所有上下文”“全部文章”这类超出实际能力的 source scope

## 当前回答输出

当前每轮 Ask 的正式输出真相源是 `turn_run.user_visible_output_json`。其中包含：

- `content_md`
- `submission_mode`
- `resolved_intent`
- `citations`
- `tool_trace`
- `evidence`
- `context_plan`
- `resolved_context_input`
- `trace_summary`
- `disambiguation`
- `external_asset_disambiguation`
- `response_cards`
- `action_proposals`
- `supplement_candidates`
- `persisted_supplements`
- `follow_up_suggestions`
- `run_info`
- `usage_summary`
- `billed_points`

与 article overview 相关的补充字段：

- `article_overview_status`
- `article_overview_source`
- `article_overview_confidence`

前端当前仍消费兼容 DTO，但新运行不再以 assistant message metadata 作为主输出承载。

补充：

- `context_plan / resolved_context_input / evidence / trace_summary` 在 Ask 面板中默认折叠展示
- `tool_trace` 在 Ask 面板中以紧凑 tool chip row 呈现，不默认展开技术细节
- `citations` 在消息底部以 citation badge / list 呈现，作为用户可见回源线索
- `follow_up_suggestions` 在 assistant 消息尾部以可点击 chips 呈现，点击后作为下一轮 prompt 输入
- `reasoning.*` 是 run-scoped 的正式输出字段
- Ask 在流式阶段会按节流 checkpoint 持续回写当前 `turn_run.user_visible_output_json`
- 刷新页面时会直接回显已持久化的正文与 thinking 快照，但不会自动恢复原 SSE 连接或继续跑同一个未完成 run

## 当前写动作边界

当前支持的写动作：

- 保存为笔记（写入 `reader_notes`）
- 保存为高亮（写入 `user_annotations`，类型为 `highlight`）
- 生成 AI supplement `grammar_note`

明确约束：

- 不直接保存整条 assistant 回答为笔记
- 若需要把 Ask 结果沉淀为笔记，应走专门整理后的 `save_note`
- supplement 必须与 workflow 输出区分来源
- supplement 可删除，且不因 `reset` 消失

## 当前 supplement 范围

当前 Ask 的结构化 AI 补充只开放：

- `assistant_supplement.grammar_note`

同时，Ask 当前也可在对话内直接生成两类结构化阅读卡：

- `grammar_note_card`
- `sentence_breakdown_card`

当前链路已经支持：

- candidate
- confirm
- persist
- delete
- 当前页投影
- 后续作为 `supplement_ref` 再次进入 Ask

补充约束：

- `grammar_note_card` 与 `sentence_breakdown_card` 明确标记为 `AI 助手生成`
- `grammar` 快捷分析允许从片段扩展到所在整句理解，但必须保留 focus span
- `breakdown` 快捷分析坚持 sentence-level，不为零散片段伪造拆句结果

## 当前冻结边界

### 冻结为当前事实的部分

- attachment-first Ask contract
- agent-loop-only runtime（`planner_first` 仅作为历史 trace value 保留，没有 live route）
- `conversation / turn_run / user_visible_output / eval_trace`
- record-level 与 asset-level HITP
- current run hydration 优先于 legacy metadata fallback
- `grammar_note` supplement 生命周期

### 明确不作为当前承诺的部分

- hybrid retrieval / 默认 RAG
- 多会话列表
- 独立 AI 工作台
- 对话级长期人格记忆
- 跨文章用户资产聚合查询
- “用户学习资产”作为独立产品面

## 当前跨文章能力边界

Ask 的跨文章能力限于受控扩展，不是自由历史检索。当前支持：

- 显式 `record_ref`（用户主动带入其他文章）
- 已知标题引用解析（record-level HITP）
- external `analysis_ref`（用户主动带入分析结果）
- external `supplement_ref`（用户主动带入 AI 补充内容）

不承诺：
- 跨文章的用户高亮/笔记/收藏检索
