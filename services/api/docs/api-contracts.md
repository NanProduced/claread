# API 契约

> **状态**: `CURRENT` | **最后验证**: 2026-08-31

本文记录当前必须保持稳定的后端契约。

## 冻结原则

- Web 后续可以增加字段、adapter 和 render profile。
- 不能破坏小程序当前依赖的字段、状态码和 ID 语义。
- `@claread/contracts` 当前先承载跨端常量和轻量类型（仅 Web 接入）；OpenAPI 后续应作为完整 DTO 生成来源。

## 小程序当前依赖接口

小程序当前只依赖以下接口族；Reader 提交与阅读主链当前仅 Web 接入。

| 领域 | 接口 | 说明 |
|------|------|------|
| Auth | `POST /auth/wechat/login` | 小程序登录入口 |
| Auth | `GET /auth/session/me` | 登录态恢复和用户资料 |
| Auth | `PATCH /auth/profile` | 用户资料和阅读偏好更新 |
| Auth | `POST /auth/session/logout` | 退出登录 |
| Quota | `GET /me/quota` | 登录用户额度 |
| Quota | `GET /me/quota/anonymous` | 游客额度查询 |
| Quota | `POST /me/quota/check` | 额度检查；匿名路径会消耗试用次数 |
| Credit | `GET /me/credit/ledger` | 积分流水 |
| Dict | `GET /dict` | 查词 |
| Dict | `GET /dict/entry` | 词条详情 |
| Vocabulary | `POST /vocabulary` | 生词同步 |
| Vocabulary | `GET /vocabulary` | 生词列表 |
| Vocabulary | `POST /vocabulary/highlights` | 阅读页高亮和已收藏生词匹配 |
| Vocabulary | `GET /vocabulary/review/due` | 生词复习 |
| Vocabulary | `POST /vocabulary/{vocab_id}/review` | 提交复习结果 |
| Feedback | `POST /feedback` | 用户反馈 |
| Daily Reader | `GET /daily-reader/today` | 今日精读 |
| Daily Reader | `GET /daily-reader` | 往期精读列表 |
| Daily Reader | `GET /daily-reader/{article_id}` | 精读详情 |

## Web 邮箱认证接口

Web 浏览器只调用同源 Next.js BFF `/api/web/auth/email/**`；BFF 再调用以下 FastAPI 接口。登录页要求用户显式选择“登录”或“注册”：密码登录直接调用 password login，不调用 start；注册才通过 start 创建邮箱 OTP challenge。账号存在性在 OTP verify 请求内部由服务端解析；只有验证码校验成功，ticket 与 purpose 才返回 BFF 并投影下一步。用户提交 OTP 前的浏览器合同保持一致。

| 领域 | 接口 | 说明 |
|------|------|------|
| Email Auth | `POST /auth/email/start` | 显式注册入口；创建并发送 register OTP challenge |
| Email Auth | `POST /auth/email/otp/verify` | 请求内部解析账号状态与目标 purpose；OTP 成功才签发并返回短期 ticket 与 purpose |
| Email Auth | `POST /auth/email/register` | 消费 register ticket，设置密码并创建 Web session |
| Email Auth | `POST /auth/email/password/login` | 邮箱密码登录；不创建 OTP challenge |
| Email Auth | `POST /auth/email/password-reset/request` | 请求密码重置 challenge；响应保持统一的 `accepted` 合同 |
| Email Auth | `POST /auth/email/password-reset/complete` | 消费 password reset ticket，重置密码、撤销旧 session 并创建新 session |

FastAPI 上游响应中的 `challenge_id`、`ticket`、`purpose` 和 `session_token` 只由 BFF 服务端处理。challenge、ticket 与登录 session 均写入 HttpOnly Cookie；普通浏览器 JSON 和认证日志不暴露这些值。

## Web 接入接口（Reader orchestration 主链）

Web 浏览器不直接消费 FastAPI 原始端点；以下接口经 Next.js BFF `/api/web/reader/**` 代理接入。

| 领域 | 接口 | 说明 |
|------|------|------|
| Reader 提交 | `POST /reader/records/input` | 统一输入提交，路由到 stable freeze / candidate / action-required |
| Reader 提交 | `POST /reader/records/stable-ready-input` | stable-ready 输入直接冻结为 Reading Record |
| Source Artifact | `POST /reader/source-artifacts/init-upload`、`/complete-upload`、`/submit-input`、`GET /pipeline-status` | 文件上传链路的 artifact 注册、绑定与状态查询 |
| Candidate | `POST /reader/records/{record_id}/candidate-documents/{candidate_document_id}/confirm` | 确认候选文档并重建快照 |
| Reader 读取 | `GET /reader/records` | 当前用户 Reading Record 列表 |
| Reader 读取 | `GET /reader/records/{record_id}/snapshot` | 从 DB facts 重建的 ReaderPlateSnapshot |
| Reader 读取 | `GET /reader/records/{record_id}/events` | 按 sequence cursor 轮询已提交 reader events |
| Reader 读取 | `GET /reader/records/{record_id}/stable-document`、`/candidate-document`、`/confirmed-source` | Plate 投影事实源 |
| Reader 读取 | `POST /reader/records/{record_id}/opened` | 记录打开时间戳 |
| Reader 恢复 | `POST /reader/records/{record_id}/recovery` | failed record 手动恢复；无 body，trigger 服务端固定 `manual` |
| Source Preview | `GET /reader/records/{record_id}/source-preview` | record-scoped 原件预览元数据；owner-scoped，从持久化 record lineage 解析 |
| Source Preview | `GET /reader/source-artifacts/{artifact_id}/preview` | artifact-scoped 短期只读 presigned URL；仅 owner 且未软删、`available`、OSS 存储、PDF/允许图片 MIME 才可预览，其余一律 404 collapse；presigner 不可用时返回 `preview_url=null` + `degraded=true` |
| Confirmed Source | `PUT /reader/records/{record_id}/confirmed-source` | 整篇更新并触发重解析；`expected_revision` 乐观并发，冲突 409 `stale_source_revision` |
| Confirmed Source | `GET /reader/records/{record_id}/confirmed-source/revisions` | 不可变 revision 快照列表（metadata only） |
| Confirmed Source | `GET /reader/records/{record_id}/confirmed-source/revisions/{revision}`、`POST .../revisions/{revision}/restore` | 单版本读取；把目标快照恢复为新的 current revision，绝不回写历史 |
| Reader 增强 | `POST /reader/records/{record_id}/section-translation` | 显式段落同步翻译 |
| Article RAG | `GET /reader/records/{record_id}/article-rag-index/status`、`POST /ensure` | 文章 RAG 索引生命周期 |
| Ask | `GET /reader/records/{reading_record_id}/ask/threads`、`POST .../threads/default`、`GET .../threads/{thread_id}` | 当前文章 Ask 线程 |
| Ask | `GET /reader/records/{reading_record_id}/ask/model-options` | Ask 可选模型档位 |
| Ask | `POST /reader/records/{reading_record_id}/ask/threads/{thread_id}/messages/stream` | Ask Claread 流式回复；SSE，要求登录态 |
| Ask | `POST /reader/records/{reading_record_id}/ask/threads/{thread_id}/messages/{message_id}/retry/stream` | 重试某一 assistant 回复 |
| Ask | `GET /reader/records/{reading_record_id}/ask/threads/{thread_id}/submissions/{client_submission_id}` | 客户端提交幂等 reconcile |
| Ask | `POST /reader/records/{reading_record_id}/ask/threads/{thread_id}/reset` | 重置线程 |
| Ask | `POST /reader/records/{reading_record_id}/ask/messages/{message_id}/citations/{citation_id}/navigate` | citation 回源定位 |
| Dict AI | `POST /dict/ai` | 词典 AI 增强；登录用户的正式 AI 能力入口，支持 `context_explain` 与 `missing_fallback` |
| User Annotations | `POST /user-annotations`、`GET /user-annotations` | 用户高亮创建与列表（`reading_record_id` 可选过滤），Reading Record anchor |
| User Annotations | `PATCH /user-annotations/{annotation_id}`、`DELETE /user-annotations/{annotation_id}` | 按资源 ID 更新（颜色/payload）与删除高亮 |
| Reader Notes | `POST /reader-notes`、`GET /reader-notes`、`PATCH /reader-notes/{id}`、`DELETE /reader-notes/{id}` | 用户笔记（Reading Record anchor） |
| Favorites | `POST /favorites`、`GET /favorites` | 文章收藏创建（按 `target_type + target_key` 去重）与列表；Daily Reader 使用 `daily_reader_article:{articleId}` |
| Favorites | `DELETE /favorites/target` | 按 target identity（query `target_type + target_key`）取消收藏；不是资源 ID 路径 |
| Vocabulary | `GET /vocabulary`、`POST /vocabulary` | Web 生词列表与写入（BFF `/api/web/vocabulary`） |
| Vocabulary | `PATCH /vocabulary/{vocab_id}`、`DELETE /vocabulary/{vocab_id}` | 按资源 ID 更新（mastery/备注）与删除生词（BFF `/api/web/vocabulary/[id]`） |

## ID 语义

| 字段 | 含义 |
|------|------|
| `reading_record_id` | canonical 阅读记录身份，映射 `reading_records.id`（UUID） |
| `client_record_id` | 客户端生成的稳定记录 ID，`reading_records.client_record_id`，blank 规范化为 `NULL`，重复 active 值返回 409 |
| `cloud_record_id` | legacy 历史字段，原映射已删除的 `analysis_records.id`；当前仅作为 vocabulary `SourceRef.payload_json` 的可选兼容字段保留，新代码不得再写入或依赖 |

## 枚举域（PG CHECK 为权威）

| 字段 | 当前值域 |
|------|----------|
| `reading_records.reading_goal` | `daily_reading` / `exam` |
| `reading_records.reading_variant` | `daily_reading`：`beginner_reading` / `intermediate_reading` / `intensive_reading`；`exam`：`gaokao` / `cet` / `kaoyan` / `tem` / `ielts_toefl` |
| `reading_records.source_type` | `text` / `markdown` / `file` / `url` / `pdf` / `ocr` / `image` |
| `vocabulary_book.mastery_status` | `new` / `learning` / `review` / `mastered` / `archived`（默认 `new`，PG CHECK 是唯一权威） |
| `favorite_records.target_type` | `reading_record` / `daily_reader_article` |

## 当前契约状态

- Confirmed Source 版本历史：`confirmed_source_documents` 原地演进当前行，每个 durable 写入在同一事务内持久化不可变 `confirmed_source_revisions` 快照（`snapshot_reason ∈ initial | save | restore`）；版本列表 / 单版本读取 / 恢复均为 owner-scoped API，恢复产生单调递增新 revision，绝不回写历史行。409 冲突码族语义：`stale_source_revision`（save / restore 乐观并发冲突）、`stale_candidate_revision`（candidate 引用过期 source revision，重取 confirmed-source 后可恢复）、`source_frozen`、`record_state_advanced`。
- Source Preview 的 Web 消费走 BFF `/api/web/reader/records/[recordId]/source-preview` 受控二进制流代理；presigned URL 是敏感临时交付值，不得直接写入普通 DOM。交付安全合同见 `apps/web/docs/design/surface-read-intake-content-check.md`。
- `POST /reader/records/{record_id}/recovery` 是 failed Reading Record 的手动恢复入口：要求认证；无请求 body（trigger 由服务端固定为 `manual`，客户端不能传递或伪造 trigger）；HTTP 200 outcomes 为 `recovery_started` / `nothing_to_recover`；response 字段仅 `record_id`、`outcome`、`previous_product_state`、`next_product_state`、`record_generation`、`successor_job_count`，不暴露 base/job/run/event 内部 ID；404 = 不存在或非 owner，409 = 当前状态不可恢复，503 = 后端暂不可用。Web 经 BFF `POST /api/web/reader/records/[recordId]/recovery` 接入，同样无 body。
- `/dict`、`/dict/entry` 和 `POST /dict/ai` 都声明了 response model。
- `POST /dict/ai` 是首个正式用户侧词典 AI 能力入口；要求登录态、参与积分结算、写入统一 AI usage 审计，并在 `missing_fallback` 成功后把 AI 输出写入候选池 `dict_ai_candidate_entries`。
- `POST /dict/ai` 当前按固定价格结算：`context_explain` 与 `missing_fallback` 都是每次 `5` 点；真实 token usage 只用于审计，不直接映射用户侧扣点。
- Ask Claread 的正式用户侧入口是 `POST /reader/records/{reading_record_id}/ask/threads/{thread_id}/messages/stream`；要求登录态、默认绑定当前文章线程、SSE 流式输出。请求体公开 shape 为 `content`、可选 `entry_action`、可选 `focus_anchors`（`anchor` 单数仅作旧单选兼容入口）、可选 `model`、可选 `web_search_mode` 和幂等用 `client_submission_id`；上下文装配由服务端 ContextEnvelope 决定，前端不拼装上下文细节。
- Ask SSE 事件合同：`thread.ready`、`message.started`、`message.delta`、`message.preview_reset`、`message.completed`、`error`、`agentic.run_started`、`agentic.progress`、`agentic.terminal`、`agentic.reasoning.started|delta|completed`、`context.compaction.started|completed|failed|fallback`、`submission.reconcile`。Web 通过 Next.js BFF 代理消费 SSE，不直接拼装 FastAPI 原始实现细节。
- `message.completed` 的 canonical payload 是 `ReaderRecordAskCompletedDTO`：`answer_text`、`answer_blocks`、`citations`（含 web 与 RAG citation）、`knowledge_mode`、`source_status` 与可选 `web_search` 摘要；非 ok 终态走 `ReaderRecordAskTerminalDTO`（`agentic.terminal` / `error`），不携带可展示答案。provider 提供的可读 reasoning 经确定性安全闸后按 `agentic.reasoning.*` 流式下发；用户实际看到的投影随成功、失败、取消或中断终态持久化，刷新后按同一文本恢复。该路径不调用第二个模型。
- `POST .../messages/{message_id}/retry/stream` 只接受 assistant message id；若上一次 run 处于 `interrupted`，Web 应展示"继续生成"，但底层仍复用同一 retry endpoint 产生同一 user turn 的新 run（`supersedes_run_id` 串联）。
- 客户端提交幂等：`reader_ask_client_submissions` 按 `(thread_id, client_submission_id)` 去重；重复提交不会重复调用模型，reconcile 通过 SSE `submission.reconcile` 或 submissions 查询端点返回 `wait / retry / resend` 提示。
- 用户高亮（`user_annotations`）当前只有 Reading Record anchor 一种合同：`reading_record_id + base_id + generation + unit_id + anchor_segment_id + unit_start_utf16 + unit_end_utf16 + text_hash`，UTF-16 code unit offset + `fnv1a32-utf16` hash，由后端按当前 active base 的 anchor segment 切片校验；anchor gate 是唯一校验权威。高亮冲突统一走后端 resolver：exact hit 复用原对象，subset / superset / partial overlap 合并到单一高亮，多条重叠时保留最早记录并 soft-delete 其余记录。
- 用户笔记（`reader_notes`）`quote_mode='sentence' | 'text_range' | 'multi_text'` 与高亮共用同一套 UTF-16 text anchor 校验；`GET /reader-notes` 使用 `reading_record_id` 作为必填查询参数。笔记只做 exact-hit reopen，不参与高亮冲突合并；`PATCH /reader-notes/{id}` 只允许修改 `note_text`，修改 quote identity 必须删除后重建。
- `vocabulary_book.dict_entry_id` 指向 `dict_entries.id`；词典重导前必须处理 ID 稳定性。
- 收藏的身份模型：`POST /favorites` 以请求体 `target_type + target_key`（target identity）去重，响应返回收藏资源 `id`；取消收藏走 `DELETE /favorites/target`，同样以 query 中的 `target_type + target_key` 定位，当前没有按资源 ID 删除收藏的 route。Web BFF `/api/web/reader/records/[recordId]/favorite` 与 `/api/web/daily-reader/[articleId]/favorite` 的 DELETE 都在上游映射为该 target 删除；后者固定使用 `target_type='daily_reader_article'`、`target_key='daily_reader_article:{articleId}'`。
- 高亮/笔记/生词的更新与删除都按资源 ID 路径参数定位（`{annotation_id}` / `{id}` / `{vocab_id}`），与 favorites 的 target identity 模型不同，不得混用。
- 生词来源引用（`SourceRef`）的 canonical 字段是 `reading_record_id`（可搭配 `daily_reader_article_id`）；`cloud_record_id` 仅为 legacy 兼容字段。

## 后续增强方向

后续可结构化 Daily Reader payload，增强 records 搜索/筛选，补齐 contracts 生成策略，统一错误响应，并评估把匿名额度 check 拆分为 peek / consume。
