# Ask Claread

## 文档状态

- 状态：current agentic v2 baseline
- 日期：2026-08-08
- 适用范围：Claread Web Reader 内当前 Ask Claread 模块
- 事实基线：以当前代码、测试与可验证行为为准
- 文档关系：
  - 当前运行架构见 `docs/architecture/ask-claread.md`
  - 当前开发主线见 `docs/development/mainline.md`
  - 本文描述当前可用边界，不记录重构过程

## Ask module and sidecar boundary与 sidecar 边界（含编排交叉引用）
Ask 是 Reader 内 sidecar / floating surface，不拥有编排控制面。长会话记忆走 compaction 产品语义；操作入口见 `docs/operations/reader-runtime.md`。

Ask Claread 是 Reader 内、绑定当前文章的阅读助手。它的当前定位是：

`article-bound, agent-loop-first, evidence-backed`

它不是：

- 泛聊天产品
- 独立 AI 页面
- 默认全历史注入的学习总结器
- 跨文章自由检索入口

## 当前产品原则

### 自然语言优先

用户先提问，系统围绕当前文章上下文与显式选区取证后回答。回答必须是 grounded answer、澄清或明确的 `source_unavailable`，不允许无证据编造。

### 当前文章优先

默认工作域是当前文章。上下文由服务端 ContextEnvelope 按当前文章事实装配；用户通过 focus anchors（当前选区，最多 4 个）显式带入讨论对象。系统不会把"当前句/当前段/全历史"隐式塞进运行输入。

### 证据可见、可回源

每轮回答的引用以 citation badge / list 呈现在消息底部；用户可以点击 citation 回源定位到原文位置（服务端按持久化证据与当前文档 fence 解析，不向前端暴露内部证据句柄）。

### 状态文案产品化

Ask 面板向用户展示"系统正在做什么"（流式进度、上下文压缩、学习者推理摘要），不暴露 token budget、指针账本、fence 等技术细节。学习者推理只以一段中性的短摘要（≤80 字）呈现，不展示原始 chain-of-thought。

## 当前公开 contract

当前 Ask 请求主 shape（`POST /reader/records/{reading_record_id}/ask/threads/{thread_id}/messages/stream`）：

- `content`
- 可选 `entry_action`
- 可选 `focus_anchors`（`anchor` 单数仅作旧单选兼容入口；任一 anchor 校验失败则整个请求 fail-closed）
- 可选 `model`（Ask model option key）
- 可选 `web_search_mode`（`disabled` / `allowed`）
- 幂等用 `client_submission_id`

上下文装配完全由服务端决定，前端不拼装上下文细节。

### 模型档位

用户可在 `GET .../ask/model-options` 提供的已启用档位中选择。档位来自 `services/api/config/reader-ask-model-options.json`（当前默认 `deepseek-v4-flash`，备选 `qwen-max`、`deepseek-pro`），每个档位带 `price_multiplier`。档位的加权计费配置已挂载，turn run 的 usage/ledger 落账闭环尚未实现。

## 当前交互模型

### 会话模型

- 每篇文章一个默认 Ask 线程（`POST .../threads/default`）。
- 提供 `POST .../threads/{thread_id}/reset` 重置线程；reset 只影响会话，不影响已落地用户资产。

### retry / regenerate

- `retry / regenerate` 是同一 user turn 的新 run（`POST .../messages/{message_id}/retry/stream`），不新增 user turn，新 run 通过 `supersedes_run_id` 串联旧 run。
- 当前用户可见结果始终以最新 run 为准。
- 若上一轮 `interrupted`，前端文案显示为"继续生成"，底层仍复用同一 retry endpoint。

### 提交幂等

- 客户端为每次发送生成 `client_submission_id`；重复提交不会重复调用模型。
- 网络抖动后通过 `GET .../submissions/{client_submission_id}` 或 SSE `submission.reconcile` 事件获得 `wait / retry / resend` 提示。

## 当前已实现能力

### 当前文章内能力

- 基于当前文章 ContextEnvelope 的回答（baseline 上下文、选区 model view、文章语义地图）。
- 证据展开工具 `expand_evidence`：按 opaque 指针逐步展开已注册证据，零工具调用时也能基于初始选区证据回答。
- 文章 RAG 检索工具 `search_current_article`：在文章 RAG 索引可用时挂载（每轮最多 1 次调用）；索引未就绪/未建索引/检索不可用时返回 typed 状态，模型不会看到该工具。
- Provider reasoning：默认启用；provider 提供的可读思考内容经确定性安全闸后通过 `agentic.reasoning.started|delta|completed` 流式展示，并在所有正常终态保存用户已见文本。无第二次模型调用；紧急开关关闭时只隐藏 reasoning，不影响进度和答案。

### Web search（显式授权）

- 用户在请求中设 `web_search_mode="allowed"` 才授予本轮 Web search 能力；授权不等于强制搜索，由 agent 决定是否调用 `search_web`。
- 授权但 provider/模型组合不支持时，请求直接返回 503 `web_search_unavailable`，不静默降级。
- Web citation 与文章 citation 区分来源标识；决定性结果后工具自动退役。

### 上下文压缩

- 长对话由 thread memory 机制维护：LLM 只产出窄化压缩草稿，服务端负责存储、CAS 与校验；压缩生命周期通过 `context.compaction.started|completed|failed|fallback` 事件可见，失败有确定性无 LLM 兜底。

## 当前回答输出

`message.completed` 的 canonical payload 是 `ReaderRecordAskCompletedDTO`：

- `answer_text` / `answer_blocks`（带来源标注的回答块）
- `citations`（文章 citation 与 web citation）
- `knowledge_mode` / `source_status`
- 可选 `web_search` 摘要

非 ok 终态（`context_stale` / `invalid_citations` / `failed` / `cancelled`）走 `ReaderRecordAskTerminalDTO`，不携带可展示答案。

补充：

- 刷新页面时从已持久化的 turn run 回显历史消息；不会自动恢复原 SSE 连接或续跑未完成 run。
- 中断的流式 run 由路由收尾与后台 sweeper 收敛为 `cancelled` / `failed`，不会错误地变成已提交答案。

## 当前冻结边界

### 冻结为当前事实的部分

- article-bound agent-loop runtime（`reader_record_ask` 是唯一 Ask 生产链）
- turn run 持久化与 SSE 事件合同
- focus anchor 显式上下文入口
- citation 回源导航
- 客户端提交幂等 reconcile
- thread memory compaction 与 provider reasoning 投影

### 明确不作为当前承诺的部分

- 跨文章检索与跨文章引用入口（旧 `record_ref` / `analysis_ref` / `supplement_ref` 已随旧架构删除）
- Ask 内写动作（保存笔记/高亮、生成 AI supplement）：当前 Ask 是只读问答面，无 agent 写工具
- 多会话列表与独立 AI 工作台
- 对话级长期人格记忆
- "用户学习资产"作为独立可检索产品面
- 默认全历史搜索 / 无边界 hybrid retrieval
