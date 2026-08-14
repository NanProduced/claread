# 多端架构

> **状态**: `CURRENT` | **最后验证**: 2026-08-03（Architectural Cutover Complete；旧 Analysis service 写入路径与 `render_scene_json` 事实源已物理删除，新链以 Reader orchestration 为当前生产架构；旧 `analysis_*` 表的精确状态见 `docs/architecture/workflow-history.md`）

## 结论

Claread 使用一套后端业务内核，服务多个客户端。

不为 Web 端另写一套业务后端。客户端差异通过认证 adapter、render profile 和 capability profile 处理。

当前 Web、小程序和后端的能力差异，按用户可感知功能追踪在 `docs/architecture/multi-client-capability-matrix.md`。该矩阵区分某端"可操作"和"仅展示"，例如 Web 局部 text range 批注可由小程序复现展示，但小程序不一定提供同样的选区操作。

## 客户端

| 客户端 | 目录 | 定位 |
|--------|------|------|
| Web | `apps/web/` | 新用户提交 Reader orchestration 的唯一客户端（不是 Claread 唯一用户客户端），通过 `/app/read` 与 `/app/reader/[recordId]` + BFF `/api/web/reader/records/*` 接入新 Reader orchestration 主链 |
| 微信小程序 | `apps/miniprogram/` | 稳定客户端，功能子集，受平台能力限制；旧文章分析在 cutover 中下线，后续按新 contract 单独评估 |
| Directus / Admin | `apps/directus/` | 当前内部控制面，承接通用 metadata 展示、LLM Config 与后续按新 orchestration 重建的治理化控制面 |

小程序是稳定客户端，不是一次性冻结的旧客户端。cutover 后小程序仍会继续迭代，只是它的新增能力应在多端契约下推进。

## 后端

后端位于：

```text
services/api/
```

职责：

- 用户与身份。
- Reader orchestration runtime（run/job/event/layer）。
- Ask agentic v2 执行链。
- 模型调用。
- 结构化结果生成（Stable Document / Reading Units / Anchor Segments / Enhancement Layers）。
- 记录、收藏、生词、批注、反馈。
- 词典查询。
- 每日精读。
- 配额和积分。

后续可拆分出 worker 服务：

```text
services/worker/
```

用于异步任务、RAG ingestion、LLM-as-a-Judge 或 Directus action worker。当前稳定基线仍以 `services/api/` 为主。

## 数据真相源

PostgreSQL 是事务型数据真相源。

核心数据对象（cutover 后当前生产链）：

- users
- user_identities
- user_sessions
- reading_records（Reading Record）
- stable_reading_documents / stable_document_blocks（Stable Document）
- reading_units / anchor_segments（Reading Units / Anchor Segments）
- enhancement_layers（Enhancement Layers）
- reader_events（事件日志）
- reader_runtime_spans（runtime span）
- reader_ask_threads / reader_ask_turn_runs / reader_ask_supplements（Ask agentic v2）
- vocabulary_book
- favorite_records
- user_annotations / reader_notes
- feedback
- daily_readers
- dict_entries / dict_lookup_targets / dict_redirects
- ai_usage_events（usage/ledger）

旧 Analysis service 写入路径（`services/api/app/services/analysis/` 整目录 `.py` 源文件）已在 cutover 中物理删除；后端代码对 `analysis_records`、`analysis_results`、`analysis_tasks` 的引用已全部迁移到 Reading Record 事实，这些旧表与 legacy 孤儿表（`analysis_debug_snapshots`、`analysis_task_events`、`analysis_overview_tasks`、`analysis_overview_task_events`）已不在 baseline migration（`infra/migrations/0001_initial.sql`）中。`analysis_windows` 与 `layer_analysis_plans` 是新链在用表，必须保护。残留本地开发库中的旧表清理属于 post-cutover 数据清理 backlog。

Redis 用于缓存和多 worker 场景下的共享状态。

Zilliz 用于 grammar few-shot / RAG 示例检索；示例控制面当前由 Directus `Example Lab`（`eval_example_lab_entries` Collection）管理。

## 结果分层（cutover 后）

Reader orchestration 的结果分层：

```text
Stable Document / Reading Units / Anchor Segments
  -> Enhancement Layers
  -> snapshot projection
  -> client local UI state
```

### Stable Document / Reading Units / Anchor Segments

后端 Reader orchestration 生成的稳定语义结果，是跨端复用的 canonical truth。一经用户确认不可被后续增强改写。

### Enhancement Layers

增量增强层（translation / vocabulary / grammar / sentence_analysis / semantic_outline），通过 `reader_events` 发布，支撑渐进式渲染。

### Snapshot Projection

面向阅读页消费的精简视图层。Web 通过 BFF `/api/web/reader/records/*` 消费 snapshot projection，不直接吃 FastAPI 原始 DTO。

### Client Local State

客户端本地 UI 状态，例如展开状态、滚动位置、本地同步队列，不应成为后端 canonical 模型的一部分。

## 来源元数据

记录应该保存来源，但来源不是访问边界。

当前已经稳定落地的来源 / 请求快照字段挂在 Reading Record 上，持久化 reading goal / variant / source_type 等请求侧事实。

更细的跨客户端来源元数据（`created_client_type` / `created_client_version` / `requested_render_target` / `prompt_version` / `model_profile` / `schema_version` / `source_input_type`）仍属于后续增强项。

用途：

- 追踪记录生成来源。
- 判断当前客户端是否能直接展示。
- 必要时为目标客户端懒生成新的 snapshot projection。

## 调试摘要层

Reader orchestration 的调试摘要通过 `reader_events` 和 `reader_runtime_spans` 承载，记录 runtime span、job lifecycle、layer publish 事实。

旧 `analysis_debug_snapshots` 表的写入路径已在 cutover 中物理删除；该表属于 legacy 孤儿表，DROP 属于 post-cutover 数据清理 backlog。

## 认证策略

微信小程序使用：

```text
wx.login -> /auth/wechat/login -> Claread session_token
```

Web 端当前优先使用手机号验证码登录，后续可评估微信开放平台 OAuth2 或其他身份提供方。

后端不应把微信 `openid` 当业务用户主键。统一模型：

```text
users
user_identities
user_sessions
```

后端签发 Claread 自己的 session token。微信只负责证明用户身份。

## 共享与不共享

应该共享：

- API 契约。
- DTO / OpenAPI 生成类型。
- 数据库模型。
- Reader orchestration runtime。
- 词典服务。
- 评测数据。
- 设计 token。
- 纯业务工具函数。

不应强行共享：

- 小程序页面组件。
- Web 页面组件。
- 复杂 reader UI。
- 平台 API 封装。
- 本地 storage 具体实现。

## 后续扩展方向

当前先在 Web 单端稳定基线之上评估记录来源元数据、Web 高保真 render profile、Directus、eval 和 RAG 的数据边界。具体落地顺序以后续产品与技术评审为准。
