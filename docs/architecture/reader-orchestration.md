# Reader 编排架构

> **状态**: `CURRENT` | **最后验证**: 2026-08-14
>
> 符号与代码路径优先；过期行号不是当前 authority。
>
> 本文描述用户提交内容的 `learning` 主链 Reader orchestration 的当前生产架构：运行时形态、执行策略、run/job 模型、发布围栏、策略与成本控制、失败语义和可观测性。事实以当前代码与测试为准；模块级细节在 `reader-orchestration.md` 的各章节内展开，不再按来源 initiative 文档逐份复制。

## 范围

本文覆盖：

- Reading Record 长期对象与稳定阅读文档的运行时推进。
- 三态执行策略（SHORT_BATCH / STRUCTURED_BATCH / GROUPED_WINDOWED）与路由判定。
- Job Bootstrap、Worker、Publisher、Completion 与产品状态推进。
- Policy / 预算 / 发布围栏 / 失败语义。
- 可观测性与运维入口（操作细节见 `docs/operations/reader-runtime.md`）。
- **Truth Chain Authority Layers**：从 Confirmed Source 到 Plate/DOM projection 的权威层、candidate/stable 边界、snapshot 重建合同、非事实源清单。

本文不覆盖：

- Daily Reader（固定 workflow，不进入 runtime 转换）。
- `academic workflow`（尚未实现，需单独设计）。
- 小程序 Reader orchestration 实现。
- 输入适配、Stable Reading Document、Reading Units 与 Anchor Segments 的字段级实现细节；本文只维护其跨模块稳定不变量，精确 schema/DTO 仍以 migration、代码和测试合同为准。
- 旧 `render_scene_json` 兼容映射（已物理删除，不再作为迁移源）。

## 产品形态

用户面对的对象是 **Reading Record**，不是 workflow run。

- 先出现稳定可读文章（`article_ready`），译文和其他增强层渐进到达。
- Ask Claread 是绑定当前文章的侧边助手，不是 orchestration 控制中心。
- 用户高亮、笔记和保存的 Ask 建议作为 User Editorial Assets 叠加在稳定正文上，系统层不得改写。
- 只在确认、配额、继续、失败修复等边界上出现需要用户处理的状态。
- Reader 页面不是常驻 LLM 线程；默认 UI 不展示 planner trace、token stream 或任务看板。

## 领域对象速览

| 对象 | 职责 | 可变性 |
|---|---|---|
| Reading Record | 用户面对的长期阅读对象 | 长期存在 |
| Original Input | 提交的原始材料 | 保留；默认不作为 Reader/Ask truth |
| Stable Reading Document / Blocks / Canonical Text Layer | 记录内文档事实源 | 同一 record/document 内不可变 |
| Reading Units / Anchor Segments | 稳定阅读单位与 sentence-like 锚点段（`segment_type = sentence \| clause \| fallback_window`） | 同一 record 内不可变 |
| Enhancement Layers / System Annotation Layers | 译文、词汇、语法、长难句、Semantic Outline 等 | 可再生、可局部重试；不可直接编辑 |
| Parsed Decisions | 单元级 parsed 判断与 rationale | 可审计 |
| User Editorial Assets | 高亮、笔记、生词动作 | 用户控制 |
| Reader Runs / Jobs | bounded background execution | 执行事实，不是产品对象 |
| Reader Events / Snapshots | streaming 和恢复用 projection | 可由业务表重建 |
| Reader Plate Document | Web Article Body 的 Plate.js 投影 | projection，不是 truth |

`anchor_segment_id` 是权威锚点；`sentence_id` 仅是兼容 alias。Span anchors 使用 `anchor_segment_id` + unit-local UTF-16 offsets；offset 必须落在目标 Anchor Segment 的 unit range 内。

## Truth Chain Authority Layers

> **Immutable evidence**: `services/api/app/schemas/reader_documents.py` §`CandidateReadingDocument` / `StableReadingDocument` / `StableDocumentBlock` / `ConfirmedSourceDocument` (lines 22-50, 264-540); `services/api/app/services/reader_orchestration/artifact_input_status_query_service.py` §module docstring (lines 1-13); `services/api/app/services/reader_orchestration/artifact_pipeline_worker_service.py` §module docstring (lines 1-50); `infra/migrations/0001_initial.sql` §`source_artifacts_status_check` (line 1445).
>
> 下面的链条描述"从用户提交到 Web 画布渲染"的权威顺序：每一层只能由其前序层产生，不能被后续层反向覆盖。所有"非事实源"声明放在本节末尾，禁止以它们为依据改写前序层。

### 链条总览

```text
(1) Confirmed Source
        ↓ derived-from
(2) Input Artifact (single-file) | plain-text | Markdown
        ↓ suitability-gate
(3) Candidate Document (high-impact, needs user confirmation)
        |   OR
        ↓ freeze
(3') Stable Reading Document / Stable Blocks / Canonical Text Layer
        ↓ deterministic-build
(4) Reading Units / Anchor Segments
        ↓ layer-publish
(5) Enhancement / System Annotation Layers
        ↓ domain-facts-rebuild
(6) Reader Snapshot (projection, rebuildable)
        ↓ web-consume
(7) Plate / DOM projection (Web Article Body)
```

### 每一层的 authority

| 层 | Authority 类型 | 谁写 | 谁不能改写 |
|---|---|---|---|
| (1) Confirmed Source | 单一 generation 全库唯一正文 | `confirmed_source_application_service` + migration 0025 | enhancement worker / publisher / Plate |
| (2) Input Artifact / plain-text / Markdown | 用户提交的原始载体 | `init-upload` / `complete-upload` / `submit-input` API + plain-text submit route | worker / publisher / Plate |
| (3) Candidate Document | 高影响适配的可预览候选 | `candidate_document_creation_service`（仅 high-impact 路径） | worker / publisher / Plate；用户未 confirm 前不得升级为 stable |
| (3') Stable Reading Document / Stable Blocks / Canonical Text Layer | record/base 内文档事实源 | `extracted_artifact_materialization_service` / `article_ready_service`（low-impact 直接冻结） | enhancement worker / publisher / Plate；需修正来源事实时创建新 generation/base 或 supersede 旧 record |
| (4) Reading Units / Anchor Segments | 稳定阅读单位与 sentence-like 锚点 | 确定性 `base_builder`（per active base 不可变） | enhancement worker / publisher / Plate |
| (5) Enhancement / System Annotation Layers | 译文、词汇、语法、semantic outline | Layer Publisher（事务内 CAS 发布） | Plate；不可直接编辑；可再生、可局部重试 |
| (6) Reader Snapshot | 从 domain facts 重建的 projection | snapshot builder（从 (1)(3')(4)(5) + 用户资产重建） | 任何业务表；snapshot 失败必须 reload，不得反向回写 |
| (7) Plate / DOM projection | Web Article Body 的 Plate.js 投影 | Web 前端 from snapshot + Plate runtime | 任何后端业务表；Plate path / Slate path 不得持久化为 truth |

### Candidate vs Stable 边界

- **Low-impact normalization**（不改变作者可见语义和阅读顺序）可直接冻结为 Stable Reading Document：plain-text / Markdown 主链走此路径。
- **High-impact adaptation**（可能删除内容、重排结构、改变可见文本）必须先形成 Candidate Document，等待用户确认；用户 confirm 后才升级为 Stable Reading Document。`CandidateReadingDocumentStatus = Literal["ready", "confirmed", "rejected", "superseded"]`（`reader_documents.py`），只有 `confirmed` 才能进入 stable 路径。
- Candidate Document 不是 truth：用户可以 `reject` 或 `supersede`，原 Candidate 行即历史化；只有 stable 路径写入的 `StableReadingDocumentStatus = Literal["active", "superseded"]` 才是不可变 truth。
- 所有 extracted artifact 都进入 suitability gate（`evaluate_input_suitability`，`input_suitability_gate.py`）；gate 基于内容风险决定 Stable / Candidate / Rejected，不因"artifact"身份自动 Candidate。
- **OCR source（`source_type='ocr_text'`）必须 Candidate**（`_requires_candidate_by_source_type` line 668: `if source_type == "ocr_text": return True`）。
- **PDF / url（`source_type in {'pdf_text','url_text'}`）默认 Candidate**；只有显式高置信（`extraction_confidence >= 0.95`）且文本明显简单（无 complex structure / 无 OCR low confidence / 无 layout uncertain / 无 noisy text / link_only_line_ratio < 0.4 / code_line_ratio < 0.25）时可直接 Stable（lines 670-686）。
- **TXT / Markdown（`source_type in {'pasted_text','txt_file','markdown_file'}`）不因"artifact"身份自动 Candidate**；按内容风险（code_dominant / table_structure_uncertain / image / footnote / math / too_long 等）得出 Stable / Candidate / Rejected。
- 不得写"所有 artifact/PDF/OCR 默认 Candidate"。

### Source / Artifact 与 Stable Base 的关系

- Original Input（`original_inputs` 表）保留原始提交，但**默认不作为 Reader/Ask truth**；Reader truth 由 Confirmed Source + Stable Reading Document 表达。
- Source Artifact（`source_artifacts` 表）是 OSS-backed 文件载体；其 `status` schema 允许 `pending | available | failed | deleted`（migration 0001 §`source_artifacts_status_check` line 1445），但当前生产 writer 只稳定到达 `pending` 与 `available`；`failed` 与 `deleted` schema-allowed 但当前无生产 writer（详见 `docs/operations/reader-runtime.md` §Artifact Input Operations Contract）。
- Reading Base（`reading_bases` 表）是同一 generation 内的 stable 文档载体；`record_generation` + `active_base_id` 是 record 的 stable identity；新 generation 或新 base 必须通过 supersede 路径创建，不得原地覆盖。
- Confirmed Source（migration 0025）是 single-per-(record, generation) 的规范化正文，由 DB CHECK 自校验 `content_sha256`；revision 乐观并发演进采用原地 UPDATE，不保留历史正文（与 `reading_bases` 先例一致）。

### Anchor / Unit / Layer 职责

- **Reading Units** 是稳定阅读单位；同一 base 内由确定性 builder 生成，不可由 enhancement worker 改写。
- **Anchor Segments** 是 sentence-like 锚点段（`segment_type = sentence | clause | fallback_window`）；`anchor_segment_id` 是持久权威锚点，raw DOM/Plate/Slate path 只是当前前端 tree 的临时地址。
- **Enhancement / System Annotation Layers** 是译文、词汇、语法、semantic outline 等；由 Layer Publisher 在单事务内 CAS 发布，幂等保证由 `operation_fingerprint` 提供。
- **Span anchors** 使用 `anchor_segment_id` + unit-local UTF-16 offsets；offset 必须落在目标 Anchor Segment 的 unit range 内；当前持久化交互锚点采用 single-range 合同，未来 multi-range 不能在没有独立 schema/UX 验收时被文档提前承诺。
- Layer 不可直接编辑；需要修正已发布 layer 时创建新 `layer_version`，旧版本历史化；需要修正来源事实时创建新 generation/base 或 supersede 旧 record。

### Snapshot 是可重建 projection，不是真相源

- Snapshot 从 (1)(3')(4)(5) + 用户资产 + record-scoped sequence 重建，**不持有任何业务事实**。
- `artifact_input_status_query_service.py` §module docstring 明确："The source of truth remains the existing domain tables (`reading_records` / `original_inputs` / `source_artifacts` / `reader_jobs` / `candidate_reading_documents` / `stable_reading_documents` / `reading_bases`). Plate / Markdown / Slate / DOM projections are never loaded here."
- Snapshot 失败必须 reload，不得反向回写业务表；业务发布与 snapshot 投影在同一事务内提交，业务回滚时 snapshot 也回滚。
- Snapshot 不暴露 snapshot-level `projection_version`；恢复 cursor 只使用 `last_event_sequence`。
- `projection_ops.payload.projection_version` 只是 applier 内部一致性 metadata；当前 `projection_ops` 增量 applier 未端到端启用，snapshot reload 是当前交付链。

### 非事实源清单（Non-Sources of Truth）

以下显式**不是**事实源，禁止以它们为依据改写前序层：

| 非事实源 | 当前状态 | 禁止行为 |
|---|---|---|
| `render_scene_json` | 已物理删除，当前不存在 | 不得作为迁移源、兼容映射源或 fallback projection |
| DOM / Slate / Plate path | 当前前端 tree 的临时地址 | 不得持久化为 truth；不得作为 canonical offset 基准 |
| `sentence_id`（legacy alias） | 仅作兼容 alias | 不得作为权威锚点；权威锚点是 `anchor_segment_id` |
| `Markdown 标记` | 渲染层 hint | 不得作为 canonical offset 基准；不安全 HTML/link/media 必须 sanitize、降级或转入 Candidate confirmation |
| `ephemeral UI 状态` | 不进入后端 truth | 不得持久化；不得进入 `reader_events` |
| `projection_ops.payload` | applier 内部 metadata | 不得作为 snapshot identity；不得替代 `last_event_sequence` |
| `Plate document JSON` | Web 前端 projection | LLM 不得直出 arbitrary Plate JSON 作为持久事实；必须经 typed schema、allowlist、length cap、source grounding、link protocol policy |
| `ai_usage_events` 单条记录 | 计费 attribution 事实 | 不是阅读事实源；不得用于重建 snapshot 或 layer 状态 |
| `reader_runtime_spans` | 可观测性事实 | 不是业务事实源；用于 trace/diagnostic，不参与 layer 发布决策 |
| 旧 `analysis_*` 表 | 旧架构历史 | 不作为当前事实源；精确状态以 `docs/architecture/workflow-history.md` + `docs/development/mainline.md` 为准 |


## 文档事实、冻结与 `article_ready`

输入首先形成可审计的 Original/Confirmed Source，再经过 suitability 与影响等级判断：不改变作者可见语义和阅读顺序的低影响规范化可以直接冻结；可能删除内容、重排结构或改变可见文本的高影响处理必须先形成 Candidate Document，等待用户确认。当前 plain-text / Markdown 主链可直接形成稳定文档；artifact/PDF/OCR 仍按条件能力和 fail-closed 边界处理，不能把设计中的多文件、页级 OCR fallback 或自动清理写成已交付能力。

冻结后的文档事实由 Stable Reading Document、Stable Blocks 与 Canonical Text Layer 共同表达：

- Stable Blocks 保存顺序、稳定 block identity、类型、source refs、结构降级与 interpretation policy；Canonical Text 只由进入主阅读链的叶子文本按确定性规则拼接。
- Markdown 标记、DOM/Plate/Slate path 不是 canonical offset 基准；不安全 HTML、link、media 与高风险结构必须 sanitize、降级或转入 Candidate confirmation。
- 同一 record/base 的稳定文档与 canonical text 不被 enhancement worker 改写。需要修正来源事实时创建新 generation/base 或 supersede 旧 record，而不是原地覆盖 truth。

`article_ready` 只在 record 有 active stable base、Stable Blocks/Canonical Text、Reading Units、Anchor Segments、基础导航与可重建 snapshot 后写入。它明确不等待 translation、vocabulary、grammar、semantic outline 或 RAG；enhancement worker 也不得在 `article_ready` 之前发布 layer。

## 坐标、Reading Units、Anchors 与所有权

- Canonical Text 与前端 JavaScript 都使用 UTF-16 code-unit offsets；range 必须同时受 unit/segment 边界与文本 hash 校验保护。
- Reading Units 和 Anchor Segments 由确定性 builder 生成并在同一 base 内保持稳定。`anchor_segment_id` 是持久锚点，raw DOM/Plate/Slate path 只能作为当前前端 tree 的临时地址。
- 当前持久化交互锚点采用 single-range 合同；未来 multi-range 不能在没有独立 schema/UX 验收时被文档提前承诺。
- `stable` 内容不可由投影改写；`system_ai` layer 可重建；Ask supplement 必须保留来源；`user` assets 只由用户或显式确认写入；`ephemeral` UI 状态不进入后端 truth。

## Snapshot、事件与投影一致性

Snapshot 从 record/base、blocks、units、anchors、已发布 layers 与用户资产等 domain facts 重建。会改变可观察表示的业务写入、record-scoped sequence 与 `reader_events` 必须同事务提交；真实 no-op 或 rollback 不推进 sequence。投递按 at-least-once 处理，客户端按 event `id`/`sequence` 去重。

Polling 使用 `after_sequence`：cursor 只能推进到已成功处理的最后一条 event；limit 截断时不得跳到 server 的 `last_event_sequence`。出现 sequence gap、未知 event/payload、generation/base/source identity 不一致或 target 无法解析时，丢弃未确认局部投影并完整 reload snapshot。

G1 user assets、G2 Ask supplements 与 G3 record metadata 的事件只携带稳定 opaque identifiers 和 allowlisted metadata，不携带原文、笔记正文、prompt/answer、provider exception 或 raw Plate path。当前客户端对这些 representation change 以 snapshot reload 为可靠路径；`projection_ops` 增量 applier、SSE、WebSocket、JSON Patch 与 ETag 均不是当前能力。same-snapshot early return 只是无需重复应用，不等于 rejected snapshot；真正 rejected 的 snapshot 不得推进 accepted identity 或 cursor。

## 运行时形态

```text
Web Reader
  -> Reader API / BFF
  -> PostgreSQL Reading Record + run/job state + event log
  -> worker abstraction
  -> typed execution units
  -> projection emitter / Reader snapshot
  -> LangSmith + ai_usage_events
```

PostgreSQL 拥有 durable business state；LLM framework 只是执行工具。

框架立场：

- Planner 是 deterministic policy function（`plan(state, envelope) -> typed plan`），不调用模型。
- PydanticAI 只负责单个 LLM-backed typed worker（translation / vocabulary / grammar bundle / display title / semantic outline）。
- LangGraph 不作为主路径依赖；只有出现具体 Ask Document Tools、human-in-the-loop 或 multi-branch repair 需求时才做隔离 spike，且不得替换 `reader_runs` / `reader_jobs` / `reader_events` / `enhancement_layers` 等 durable control tables。
- 外部 MQ / Temporal / DBOS / Prefect 不作为默认依赖。
- `reader_events` 承担简化 outbox；不新增独立 outbox / DLQ 表。

## Reading Record 删除生命周期

`DELETE /reader/records/{record_id}` 是产品层不可恢复删除，PostgreSQL 侧为软删除（`deleted_at` / `lifecycle_status='deleted'` / `product_state='deleted'`），幂等且同 owner 重复删除返回首次 `deleted_at`；非 owner 与不存在统一 404。

- 删除事务内收敛执行状态：`reader_jobs` 非终态 → `cancelled`（`rationale_code='reading_record_deleted'`，撤销 lease/pause 字段，每个变化 job 恰一条 `job_cancelled` 事件）；`reader_runs` 非终态 → `cancelled`；`reader_article_rag_index_runs` planned/queued/indexing/indexed → `superseded`（`error_json` 合并 `failure_code='reading_record_deleted'` / `rationale_code='user_deleted_record'`，不覆盖既有诊断）。终态行不改写。
- 数据保留：不物理删除解析数据、原始输入、Stable Document、Reading Base、Reading Units、Anchor Segments、Enhancement Layers、Ask 历史、批注或审计行；只新增 `reader_job_events` / `reader_events` 审计记录。
- Vector GC intent：删除事务内以 `record_state_changed` 事件写入固定 payload discriminator `event_schema='reading_record_deleted_v1'`（含 `actor_user_id`、`deleted_at`、`record_generation`、`article_rag_vector_gc_requested=true`、`transition_counts`），每个 record 至多一条；`reader_events` 即 GC intent outbox。历史软删但缺 intent 的行在再次删除时于锁内补写。
- 实际 Zilliz/Milvus 向量删除是异步、幂等的：由 GC worker 消费该事件执行，删除事务本身不调用 vector delete。
- 删除后用户入口 fail closed：list（full/recent）、opened、recent hide、snapshot、events polling、stable document、Article RAG status/ensure、Ask（threads list / default thread / thread detail）全部不可访问；后台 bootstrap/worker 不为 deleted record 创建或成功发布新 job/output（现有 publish fence 把 `deleted_at` 非空视为 missing_record）。

状态分层（禁止用一个 task status 表达所有状态）：

| 层 | 负责 | 不负责 |
|---|---|---|
| Product State | Library / Reader 可见状态 | worker 细节 |
| Run / Job State | claim、heartbeat、retry、cancel、execution failure | 用户产品语义 |
| Event / Projection State | polling、snapshot、刷新恢复 | 业务事实源 |

## Article RAG Vector GC

`reading_record_deleted_v1` intent 由现有 Article RAG index worker 的 drain cycle 消费（不新建进程/route/scheduler/daemon）。`reader_events` 同时是 intent、retry 与 outcome 的唯一持久化事实源——无新表、无 outbox、无迁移式 job framework。

- Drain 顺序固定：`recover_stale_leases` → `reconcile_orphaned_index_runs` → 至多一条到期 vector-GC intent → Article RAG index jobs。GC 控制面/数据库意外异常终止本轮并记安全日志（re-raise）；已分类的 provider/vector 异常在 service 内写 retry event，不静默丢失 intent。
- 删除/写入竞态闭环：新增最小共享 PostgreSQL session advisory-lock helper（stdlib 对 namespace + UUID 生成确定性 signed bigint key；acquire/try-acquire/unlock；unlock 必须在 finally；连接异常退出由 PG 自动释放 session lock）。两把锁：
  - intent lock（按 deletion event id）：多 GC worker 互斥处理同一 intent；
  - vector mutation lock（按 `stable_document_id`）：index writer 与 GC 共用。
- Index worker：vector upsert 前取 stable-document mutation lock；持锁时在短事务中重跑 claim/index-run/vector-write fence（含 record `deleted_at` 检查）；提交短事务；继续持锁在事务外执行 vector upsert；finally 释放。外部 vector I/O 期间可持有连接/session lock，但不持有数据库事务。
- GC 资格校验（intent lock 后重新读取并验证）：record 必须存在且 `deleted_at` 非空；无 active index run（planned/queued/indexing/indexed）；无未终结 `article_rag_index_build` job；未静止则零 vector I/O 并写 `retry_scheduled`。
- Identity 收集：从保留的 index-run 审计数据收集不同 `(stable_document_id, vector_store_provider, vector_collection)`。单路径合同下 NULL provider/collection 推断为当前配置（worker 每次 upsert 都校验冻结 contract collection，遗留行只可能存在于配置 collection；ponytail 注释标明第二 collection 出现时必须改 fail-closed）。显式 identity 与配置不一致（`unsupported_provider` / `collection_mismatch`）或 malformed 时，在任何 vector I/O 前写 `failed_terminal`。
- 精确删除：`MilvusClient.query_iterator` 全量枚举（filter 只用 `stable_document_id`，只取 `chunk_id`）→ 校验 chunk_id（固定长度小写 hex，16 位——plan 的确定性 chunk id 是 SHA-256 前 16 字符；固定安全上限 10,000）→ 按固定确定性批次 `delete(ids=[精确 chunk_id 主键])` → flush → 持锁复验零残留。collection 不存在或查询为空 = 幂等成功 `no_vectors`（delete 调用 0 次）。禁止宽泛 filter 删除、drop/recreate、compact、真实 provider smoke；删除路径绝不创建 collection。
- 事件（全部 `event_type='record_state_changed'`）：`article_rag_vector_gc_completed_v1`（intent_event_id / outcome: deleted|no_vectors / stable_document_count / discovered_chunk_count / deleted_chunk_count / completed_at）、`article_rag_vector_gc_retry_scheduled_v1`（intent_event_id / attempt_number / failure_code / available_at，确定性指数退避 30s×2^n 封顶 1h，无重试上限——provider/config 暂时不可用不因小上限变 terminal）、`article_rag_vector_gc_failed_terminal_v1`（intent_event_id / failure_code / failed_at；仅 malformed_identity / unsupported_provider / collection_mismatch / unsafe_chunk_id / discovery_limit_exceeded）。事件/日志/异常 DTO 只含固定 failure_code、exception_type、安全计数与 intent event id——无用户内容、chunk_id、stable_document_id、collection 名、URI/token、SDK 原始文本。
- `reader_events` 新增两个索引：`idx_reader_events_gc_intent_scan`（created_at,id；WHERE intent schema + gc_requested）与 `idx_reader_events_gc_outcome_intent`（表达式 `payload_json->>'intent_event_id'`,created_at；WHERE 三类 outcome schema）。
- retry 事件长期增长的已知上限：只有实际运维数据证明事件增长成为问题时，才升级为 delivery ledger（见 GC service 实现处 ponytail 注释）。

## 里程碑

| 里程碑 | 最小合同 |
|---|---|
| `candidate_document_ready` | 高影响适配产生可预览候选文档，需用户确认 |
| `article_ready` | Stable Document + Blocks + Canonical Text Layer + Units + Anchor Segments + Navigation Skeleton；必须轻量，不等待增强层或 RAG |
| `substrate_ready` | 当前 record RAG / Ask 基础上下文可用 |
| `initial_enhancement_ready` | 第一批有用可见增强可用（通常为起始 unit 译文） |
| `coverage_complete` | 当前策略下应 parsed 的 units 都已有 Parsed Decisions |
| `action_required` | 需要确认、配额、继续、重试或修复 |

译文是 parsed 的最低门槛；禁止用批注数量作为 parsed 阈值。

## 执行策略（三态路由）

执行策略是内部运行时选择，不是用户可见模式。当前实现的确定性三态路由：

| 路由 | 适用 | 计算形状 | 发布形状 |
|---|---|---|---|
| `SHORT_BATCH` | 短文章 | 每层整篇 batch | layer 级渐进，translation 优先 |
| `STRUCTURED_BATCH` | 中等文章，仍安全放入上下文 | 带结构提示的整篇 batch | layer 级渐进 |
| `GROUPED_WINDOWED` | 中长文，batch 有 schema/grounding 风险 | window/group 调用，带 target/context anchors | reading-order 的 group/window 发布 |

路由事实：

- 判定由确定性路由阈值完成；`>4000` 词的固定样本走 `GROUPED_WINDOWED` 多 window 拓扑（fixed-coverage tests 固定该边界）。
- `STRUCTURED_BATCH` 是可审计的独立模式：`operation_fingerprint` 基（`*_structured_v1`）与 `policy_version`（`*_structured_bootstrap_v1`）与 `SHORT_BATCH` 不同，每个 batch/window job 记录 `article_route` + `document_features`；route 变化（重建 base 上 short -> structured）触发 `_supersede_stale_fingerprint_jobs`。
- `SHORT_BATCH` 与 `GROUPED_WINDOWED` 共享 `*_v1` fingerprint 基以保留幂等合同，三态区分由 `input_json.article_route` 完成。
- Grammar：`SHORT_BATCH` / `STRUCTURED_BATCH` 走 compact batch（单个 `build_grammar_bundle` / `unit_range` job 覆盖全部未发布 units，publisher 拆分回 per-unit `grammar_note` / `sentence_analysis` layer，route 专属 fingerprint）；`GROUPED_WINDOWED` 走 Z+ analysis-window / window-publisher 合同。
- Vocabulary：短文 batch jobs、非短文 grouped jobs、重复 highlight 策略与保守 phrase_gloss guards；不宣称完整跨 window / 整 record 去重。
- Translation Group 合同：batch/window 计算不得把展示折叠成一句话、一个 anchor 或一个整 unit；Translation Group 是 Reading Unit 内的语义阅读组。
- **非能力**：section-oriented longform、selective longform、very-long progress UX 仍是设计目标（roadmap P3），不是可用运行时模式；`GROUPED_WINDOWED` 不得被描述为 section-oriented longform。

## Run / Job 模型

Reader Run 是一次 bounded background run；Reader Job 是 run 内可 claim、heartbeat、retry 的执行单位。

- `reader_runs.status`：`queued` / `running` / `waiting_user` / `waiting_quota` / `paused` / `completed` / `failed_retryable` / `failed_terminal` / `cancelled` / `superseded`。
- `reader_jobs.status`：`queued` / `claimed` / `retry_later` / `paused` / `succeeded` / `failed_terminal` / `cancelled` / `superseded` / `skipped`。
- Retryable failure 由 `failure_class` + 转 `retry_later` 表达；`heartbeat_lost` 不作为长期状态，watchdog 按 lease 过期重新入队。
- Base-scoped jobs 必须携带 `base_id`；仅 `build_base`、`input_artifact_extraction`、`extracted_artifact_materialization` 可 `base_id = null`（record-level pre-base jobs，额外校验 generation 与 `active_base_id IS NULL`）。
- `reader_runs.envelope_json` 保存 immutable envelope snapshot（token/cost、step、unit range、retry、concurrency、context scope、user asset 权限边界）。

### Job Bootstrap 与 Worker Loop

- `EnhancementJobBootstrapService` 为当前 record/base/generation 创建缺失的 display title、translation、vocabulary、grammar bundle jobs；drain 顺序固定为 display title -> translation -> vocabulary -> grammar bundle。
- `ReaderEnhancementPipelineRunner` 把当前 active base 的 enhancement jobs 作为 bounded batch 推进；worker claim 必须带 `reading_record_id`、`base_id`、`expected_generation` scope；runner 只汇总 typed summary 与 attention outcome，不拥有 layer truth，不绕过 Layer Publisher。
- `ReaderEnhancementWorkerLoopService` 先 coarse scan（`deleted_at IS NULL`、`lifecycle_status='active'`、`product_state IN ('processing','readable_enhancing')`、`readiness_state IN ('article_ready','initial_enhancement_ready')`、`active_base_id IS NOT NULL`，active base join 校验 base 属于 record、`status='active'`、`record_generation` 一致），再以 per-record / per-user advisory locks 串行推进；scanner 跳过 `coverage_complete`，并通过 runnable/tracked job gate 避免 `retry_later` hot-loop 与 `failed_terminal` 反复 bootstrap。
- 并发口径：同一 record 同一 generation 只有一个 mutating active run；per-user concurrency 默认 1；per-worker process 默认 1，先以增加 worker 进程扩吞吐；Ask sidecar action 与 Reader enhancement 共用同一用户级 concurrency / cost envelope。
- 运行形态：独立 worker process（`uv run reader-enhancement-worker` / `--once` 单次诊断）；API 服务不在 FastAPI lifespan / startup hook 启动 worker loop；Web submit 只创建 durable `article_ready` facts，不同步执行 runner；不新增 public worker-control endpoint；smoke harness / fake executors 不作为产品 runtime。
- Artifact-backed input 走独立 `reader-artifact-pipeline-worker`（`input_artifact_extraction` -> provider router -> `extracted_artifact_materialization` -> Input Suitability Gate -> Stable 或 Candidate Document）；extraction 成功只回写 `original_inputs.source_text` 并 enqueue materialization；OSS/PDF/OCR 是 adapter，缺配置时 fail closed，不影响主链。

### Lease 合同

每个 job claim 必须包含 `lease_owner`、`lease_token`（UUID）、`lease_expires_at`（per-job absolute timestamp）、`attempt`、`idempotency_key`、`operation_fingerprint`、`expected_generation`、frozen input / envelope snapshot pointer。

- claim 时原子更新 `queued` -> `claimed`（`SELECT FOR UPDATE SKIP LOCKED`）；heartbeat 只允许当前 `lease_token`。
- LLM 调用不可中途取消；旧 worker 返回后，发布前必须重新校验 lease、generation 和 record state。
- retry budget 分 transient / repair / replan，不共享模糊 attempt；lease lost 可 requeue；cancel / supersede / generation mismatch 代表 obsolete result，不重新消耗 LLM。

## 发布围栏与幂等

`operation_fingerprint` 至少由 `reading_record_id`、`base_id`、`job_type`、`unit_id` 或 unit range、`layer_type`、`layer_version`、`prompt_version`、`model_route` 或 profile policy version、`input_hash` 组成；同一 fingerprint 的 published result 必须幂等。Fingerprint 表达业务意图，不表达临时执行路径；fallback 选到的 actual provider/model 写入 usage event 和 attempt metadata，不改 fingerprint。

Layer Publisher 在单个数据库事务内完成：

1. 校验 run generation 与 expected generation 一致。
2. 校验 record 未 cancelled / superseded。
3. 校验 target base / unit 仍属于当前 record。
4. 校验 schema、anchor 和 source grounding。
5. CAS 发布 `unit_id + layer_type + layer_version` 的唯一 winner。
6. 写入 `reader_events` UI domain event。
7. 对已启用 Web projection event 的发布，写同序列 `projection_ops` 或 `projection_reset_required`。
8. 写入 usage / trace 关联。

任何一步失败不能部分发布。projection emitter 不拥有业务事实；projection event 失败必须按可恢复策略处理（同事务回滚或写 `projection_reset_required`），不允许“业务已发布但前端永远无法恢复”。

`projection_ops` incremental applier 当前未端到端启用；Web 页面通过 snapshot reload 和 polling events 承接结果。`projection_ops.payload.projection_version` 只作为 applier 内部一致性 metadata，snapshot 不暴露 snapshot-level `projection_version`，恢复 cursor 只使用 `last_event_sequence`。

## Policy 与成本控制

控制面尽量 deterministic；LLM token 花在需要语义生成或判断的 worker 上。

### Planner 角色

| 角色 | 类型 | 状态 |
|---|---|---|
| Policy Planner | deterministic code | 已实现；根据 record state、coverage、failure class、Authorization Envelope 输出 typed plan |
| Semantic Reviewer | PydanticAI typed worker | 设计目标（D5+），未进入主路径 |
| LLM Planner | LLM-driven | 当前未实现 |

### Skip Gate

在 job 入队、claim 和 publish 前运行，返回 `decision`（`run` / `skip` / `pause` / `retry_later` / `reject`）+ `rationale_code` + `policy_version` + `next_retry_at`。推荐 rationale：`already_published`、`record_not_readable`、`record_paused`、`record_cancelled`、`record_superseded`、`unit_missing`、`unit_hash_mismatch`、`budget_exhausted`、`retry_too_recent`、`not_applicable_to_goal`、`unit_too_short`、`layer_disabled`。纯幂等 skip 不写 UI event；worker heartbeat / claim / attempt 不进入 `reader_events`。

### Model Profile / route

模型选择是 deterministic route lookup，不是运行时 LLM 决策。Profile 字段（provider、model_id、route、context_limit、max_output_tokens、cost、fallback 链、benchmark_status）以 `services/api/config/model-profiles.json` 与 env 配置为准；fallback 不能绕过 route required capabilities、Authorization Envelope 或 usage audit。Display title 使用独立 `reader_title_generation` route，不得静默回退到 translation profile。

### Durable ExecutionBudget

per-layer `ExecutionBudget` 跨 `runner.run()` 持久化：每次入口从 `reader_jobs` 聚合 `SUM(attempt_count)` / `MAX(max_attempts)` per `(record, base, generation, layer)`；`max_effective_calls = planned_calls * max_multiplier`，默认 `max_multiplier=3`；`BUDGET_CONSUMING_OUTCOMES = {succeeded, retry_later, failed_terminal}` 消耗预算（`superseded / no_job / skipped / budget_denied` 不消耗）；预算耗尽时 `stopped_reason = budget_exhausted` 或 `partial_budget_exhausted`。预算诊断持久化到 `reader_runtime_spans.metadata_json`（`budget_denied`、`exhausted_layers`、`budget_diagnostics`、`stopped_reason`）。

### Route flip fencing

route 翻转由 bootstrap supersede + claim 时 `_validate_fence` -> `_check_route_consistency` + publish 时同一 `_validate_fence` 组成；mismatch 返回 `stale_route_fingerprint`。publish fence 失败后 worker 层 `transition(job, "superseded", rationale_code="publish_fence_failed")` 并标记 run superseded。

## 失败语义与产品状态

`ReaderEnhancementWorkerLoopService` 在成功更新 `reading_records.product_state` 时同步发布 `record_product_state_updated` reader event。保守分类规则：

- `all_workers_no_job` / `max_ticks_reached` / `max_jobs_reached` 不改 `product_state`；`retry_later` 不改 `product_state`。
- `failed_terminal` 默认映射到 `failed`。
- `action_required` 只允许来自 failed-terminal mapper 认定的 user-remediable `attention_code`；v1 仅 `reader_user_confirmation_required`。
- `publish_fence_failed`、executor/profile missing、model route missing 等 system failure 不映射成 `action_required`。
- profile 缺失保持 fail-closed；不静默 fallback 到 fake executors、annotation profile 或 synthetic layer。

## 可观测性

双轨设计：PG `reader_runtime_spans` 为事实源，LangSmith 为 dashboard。

- Span kinds：`pipeline_root`（submit 根 span，承载 `trace_id`）、`claim`（SKIP LOCKED claim，含 `claim_wait_ms`）、`worker_tick`（单 worker tick，LLM token/model 合并至此）、`publish_fence`（发布 fence + DB write；该 span 行 `reading_record_id` 为 NULL 是预期，按 `trace_id` / `reader_job_id` 查询）。
- Status：`started` / `succeeded` / `failed` / `superseded` / `skipped`；retry class `transient` / `repair` / `replan`。
- LangSmith：`langsmith.integrations.otel.configure()` + `Agent.instrument_all()` 自动收集 PydanticAI LLM spans；`langsmith_run_id` 用 `"<trace_id>/<span_id>"` 复合格式；`LangSmithIdBridgeProcessor` 在 `on_end` 写入 ContextVar，`ReaderSpanRecorder.end_span` 自动回填。
- Directus reader-orch bundle 提供 4 个只读 JSON endpoint（trace / run / record summary / dashboard）；Console 可视化 UI 尚未实现。

## 硬约束

- Reading Record 是长期产品对象；Stable Reading Document / Blocks / Canonical Text Layer / Reading Units 在同一 record/document 内不可变。
- Reader 页面不是常驻 LLM 线程；D4 正式路径不得经过旧 `render_scene_json`。
- 译文是 parsed 最低门槛；禁止用批注数量作为 parsed 阈值。
- Enhancement / System Annotation Layers 可再生，不得修改 User Editorial Assets。
- Ask 保存 note/highlight 必须经用户确认；Ask Supplement 必须标记来源，不能伪装成系统层；Ask 是侧边助手，不是 orchestration 控制面。
- Reader Article Body 走 Plate.js（同一稳定 major 主线）；Plate document 是 projection，不是后端 truth；持久化合同不得保存 raw Plate path / raw Slate path ops。
- AI / Markdown fragment 必须经过 typed schema、allowlist、length cap、source grounding 和 link protocol policy；LLM 不得直出 arbitrary Plate JSON 作为持久事实。
- 外部 RAG/OCR/OSS 服务必须通过 adapter 接入，不能成为业务事实源。
- Daily Reader 不进入 runtime 重构；academic workflow 待 learning 主链稳定后单独设计。

## 明确非能力（当前不承诺）

- Semantic outline：`semantic_outline` layer / `build_semantic_outline` job / worker 已实现为 durable layer（`layer_type='semantic_outline'`、`target_scope='record'`），默认 request eligibility = false，默认 generator = `UnconfiguredSemanticOutlineGenerator`（permanent fail-closed）；**无真实 LLM executor**（未注册 outline `MODEL_ROUTE` / prompt agent / profile settings），自动 eligibility 阈值未定。不得宣称 outline 已可产品使用。
- Reader events 无 SSE；当前为 GET poll `after_sequence`。
- `projection_ops` incremental applier 未端到端启用；snapshot reload 是当前交付链。
- 统一监测平台 / 完整跨进程 trace 关联产品化：尚未实现。
- 计费归因按代码支持的粒度描述（`ai_usage_events` + span token/link 字段），不推断完整成本平台；standalone enhancement worker 进程当前不调用 `setup_langsmith()`。

## 相关文档

- `docs/operations/reader-runtime.md` — 本地真实链路、worker、健康检查、确定性 smoke 与排障。
- `docs/architecture/reader-rag.md` — Article RAG / grammar few-shot RAG 运行时契约。
- `docs/architecture/ask-claread.md` / `docs/product/ask-claread.md` — Ask 侧车 runtime 与产品边界。
- `docs/product/learning-annotation-policy.md` — vocabulary / grammar / translation 生成质量策略。
- `apps/web/docs/reader-ia.md` — Web Reader 信息架构与 Plate 投影消费合同。
