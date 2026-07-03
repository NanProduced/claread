# Reader Agentic Orchestration 实施计划

> 状态：`D5 active`
> 最后更新：2026-06-22

## 成功标准

当 Web 用户提交 learning 输入后，系统能够提供：

- 长期存在的 Reading Record。
- Stable Reading Base 和不可变 Reading Units。
- 早于完整增强完成的 `article_ready`。
- 可恢复的渐进 Enhancement Layers。
- 带 audit / eval hooks 的 Parsed Decisions。
- 基于新 base 的 Ask Claread sidecar，且不成为 orchestrator。
- run / step / layer 级 usage events。
- 可重置的 baseline schema，并保留词典三表。
- 当前记录内可构建和恢复的 RAG substrate。
- 文本、URL、PDF、OCR、文件上传等输入模式的统一适配入口。
- 不依赖旧 `render_scene_json` contract 的新 Web Reader Plate projection。

## 阶段门禁

| Phase | 名称 | 门禁 |
|---|---|---|
| D0 | 边界决策 | scope、数据重置、Web first、framework spike、queue posture 已记录 |
| D0.5 | 接入边界确认 | RAG、输入适配、orchestration 入口三类粗合同已写入目标架构 |
| D1 | 架构 RFC | schema、runtime、events、milestones、product states 可评审 |
| D2 | 技术 spikes | 依赖升级、DB job lease、SSE/polling、bounded worker、RAG/OCR/OSS、Length Class、成本基线有结果 |
| D3 | 后端骨架 | 新 schema、domain services、run/job 状态机、events、usage audit 编译并通过 focused tests |
| D4 | 最小纵切 | Web submit -> article_ready -> translation layer -> parsed decision -> progressive Reader display |
| D5 | 增强扩展 | vocabulary、grammar bundle（grammar_note + sentence_analysis）、summary/outline policy、anchor validation、repair、eval |
| D6 | 产品硬化 | Candidate preview、Library states、quota/action_required、Ask sidecar actions、failure recovery |

## D0. 边界决策

状态：已完成，后续只接受评审修正。

完成标准：

- 本专项目录存在，并作为专项权威上下文。
- TMP 研究材料已标记为仅作证据库。
- 旧开发记录迁移已移出本轮约束。
- 词典三表保护已明确。
- Web 优先与小程序暂缓已明确。
- Daily Reader 不进入本轮重构已明确。
- Academic workflow 暂缓重构，待 learning workflow 验证稳定后再单独设计。
- runtime 首选 PostgreSQL-backed job state，外部队列等 spike 结果后再决定。

## D0.5. 接入边界确认

状态：已完成，结论已写入 `target-architecture.md`。

目标：在 D1 schema/runtime 设计前，先确认 RAG、输入模式、orchestration 接入三类粗合同，避免 D1 反复返工。

完成标准：

- RAG 只服务当前 Reading Record，不做全局 User Editorial Assets RAG。
- RAG substrate 不阻塞 `article_ready`，归入 `substrate_ready`。
- 测试阶段 RAG vector store 初步选择 Zilliz Cloud；上线前评估阿里云 RAG / 向量检索服务。
- 所有 RAG 供应商通过 adapter 隔离。
- 文本、URL、PDF、OCR、文件上传统一进入 Input Adapter。
- OSS / OCR / 文档解析只产生 Source Artifact / Extraction Result / Candidate Base，不直接写 Stable Base。
- 测试阶段文件上传使用阿里云 OSS；上线目标为 OSS + CDN。
- OCR / 富文档解析优先评估阿里云百炼图像理解、Qwen OCR/VL 和文档解析能力。
- Orchestration 入口必须是 bounded run/job，不是页面常驻 thread。
- Ask sidecar action 走同一 Authorization Envelope。

## D1. 架构 RFC

状态：草案已形成；R12 policy/cost 合同已补；可进入 D2 spikes。

交付物：

- 完善 `target-architecture.md`。
- 完成 `concepts.md`，统一概念口径。
- 完成 `modules/` 下的模块合同文档。
- 定义 D1 模块边界。
- 设计数据模型和 API contract。
- 定义 Reader milestones 与状态流转。
- 定义 layer publish policy。
- 定义 Authorization Envelope。
- 定义 input adapter、source artifact、extraction result。
- 定义 RAG substrate 与 adapter contract。
- 定义 worker/run 失败语义。
- 定义 streaming event envelope、domain events、snapshot/polling fallback。
- 定义 Plate.js Article Body、Base Plate Snapshot、projection operations、owner 权限和 document tools。
- 定义旧 AI Workflow 的复用、改造和隔离边界。
- 定义 Policy Planner、Semantic Reviewer、Skip Gate、Model Profile、Prompt Cache 和 Usage Bucket。
- 定义 eval 与 observability hooks。

完成标准：

- coding agent 不读 TMP research 也能基于 RFC 实现 backend skeleton。
- 待决问题与已接受决策分离。
- 硬约束都能转成测试或 schema 校验。
- D2 spike 的输出只用于校准技术选型、版本和参数，不再改变 Reading Record / Stable Base / Event / RAG 的核心合同。

评审重点：

- Product state、run/job state、event/projection state 是否足够分离。
- Candidate Reading Base 是否覆盖 PDF/OCR/网页抽取的 source loss 风险。
- `article_ready` 是否足够轻，不被 RAG、全文增强或 Semantic Outline 阻塞。
- API / BFF contract 是否能支持刷新恢复、渐进渲染和 Library states。
- 旧 workflow 处理策略是否避免把 `analysis_tasks` 和 `render_scene_json` 语义带入新架构。

## D2. 技术 Spikes

状态：active。入口见 `spikes/README.md`；D2-S1 Reading Unit Builder 已完成并以 `accepted_with_changes` 写回模块合同。D2-P0 Plate dependency 已通过并对齐 Web 依赖到 Plate 53.x 稳定主线；D2-P1 到 D2-P4 与 fragment sanitize 的调研结论已由 TMP disposition 汇总，正式合同以本目录模块文档为准。

必做 spike：

| Spike | 输出 |
|---|---|
| Reading Unit Builder | Stable Base -> Reading Units 的 deterministic builder、UTF-16/hash 校验、focused tests |
| 依赖基线 | D3-P0 已完成：PydanticAI 1.107.0、DashScope 1.25.23、asyncpg 0.31.0；LangGraph/FastAPI/LangSmith/OpenAI 保持当前锁定版本；现有 focused tests 通过 |
| 当前成本基线 | 代表性 learning 样本的 token、latency、retry 数据 |
| DB job lease 原型 | claim、heartbeat、stale recovery、幂等 resume、cancel/supersede |
| Policy Planner / Skip Gate | deterministic policy table、Decision schema、rationale_code、pre-claim gate |
| Translation Worker structured output | PydanticAI typed output、usage limits、retry policy、provider transport |
| SSE + polling 原型 | event log、Last-Event-ID/cursor 恢复、snapshot fallback |
| Model Profile / Cost Baseline | 当前官方 model id、route lookup、fallback chain、translation benchmark |
| Prompt Cache / Usage Bucket | cache hit/miss audit、usage_by_layer、usage_by_cache_status |
| RAG substrate 原型 | Stable Base / Units -> chunk -> embedding -> Zilliz search -> cited units |
| 阿里云 RAG 可替换性 spike | 验证百炼知识库或阿里云向量检索是否可通过 adapter 替换 Zilliz |
| OSS 上传 spike | Web 直传/后端签名、对象 metadata、checksum、权限、过期清理 |
| OCR / 文档解析 spike | 百炼 OCR/VL/文档解析输出能否稳定形成 Extraction Result 和 Candidate Base |
| Anchor validation 原型 | span-bound layer 发布前必须通过稳定 anchor validation |
| Parsed Decision eval | human/LLM judge rubric 能识别“合理跳过”和“偷懒跳过” |
| Length Class 与 envelope 预算 | 文本长度分类、默认 unit range、token/cost/continuation 策略 |
| D2-P0 Plate dependency / API / license | Plate core、Markdown、comment/suggestion/AI 插件的 license、版本、API 可用性；不可默认依赖未验证商业能力 |
| D2-P1 Base Plate Snapshot | Stable Base / Units / Anchor Segments -> Base Plate Snapshot，不经过旧 `render_scene_json` |
| D2-P2 Projection Operations / Replay | domain-targeted `projection_ops`、snapshot reload、event replay、gap recovery；不持久化 raw Slate path ops |
| D2-P3 Selection / Anchor / Owner | Plate selection -> domain anchor，UTF-16/hash 校验，owner 权限拦截和后端 policy 对齐 |
| D2-P4 Ask Document Tools | `read_range`、`propose_highlight`、`propose_note`、`write_ai_supplement`、`revise_ai_annotation` 的用户确认和事件投影 |
| 旧依赖矩阵 | 标记旧 analysis/reader scene/Ask/user asset 依赖的 delete / rewrite / keep 策略 |

完成标准：

- 每个 spike 只产出短结果，写回本计划或 PR summary。
- spike 不新增长期设计文档；需要固化的结论写回 `target-architecture.md`。

## D3-P0. Backend Dependency Alignment

状态：completed on 2026-06-18，详细记录见 `docs/tmp/reader-orchestration/D3/TMP-D3-P0-backend-dependency-closeout.md`。

Closeout 结论：

- 依赖升级已落到 `services/api/pyproject.toml` 与 `services/api/uv.lock`。
- PydanticAI 升级到 `1.107.0`，DashScope SDK 升级到 `1.25.23`，asyncpg 升级到 `0.31.0`。
- LangGraph 保持 `0.6.11`，D4/D5 主路径不引入 LangGraph；LangGraph v1+ 只作为 D6+ complex repair / branching / interrupt 隔离 spike 候选。
- FastAPI、LangSmith、OpenAI SDK 未升级；focused tests 未暴露必须升级的缺口。
- asyncpg job lease、record-scoped event counter、rollback no-gap、SSE `Last-Event-ID` 等运行时语义延后到 D3-P4 在新 schema 上测试。

在 D3 后端骨架正式实现前，先完成后端依赖升级与能力验证。该任务不实现 Reader runtime 业务功能，只解决依赖版本、provider capability 和 lockfile 风险。

任务包：

- 盘点 `services/api` 当前 Python dependencies、lockfile 和 LLM/provider 相关封装。
- 升级 PydanticAI 到当前最新稳定 1.x；不使用 2.0 beta。验证 typed output、ToolOutput / native output fallback、usage limits、validator retry 和 provider usage extraction。
- 升级 DashScope SDK 到当前最新 patch，验证 native streaming、`reasoning_content`、tool call、usage extraction 和错误分类。
- 升级 asyncpg 到 0.31.x，验证 job lease、record-scoped transactional counter、transaction rollback no-gap 和 pool timeout 行为。
- 明确 LangGraph posture：D4/D5 不主动升级、不引入主路径；若未来保留 D6+ 入口，必须在隔离 spike 中基于当时官方文档和 lockfile 实测记录 LangGraph v1+ 能力边界、breaking risk 和不进入 durable control plane 的理由。
- 对齐 LangSmith / tracing SDK，确认 trace id 与 `ai_usage_events` / reader run/job/layer 的关联字段；只有 focused tests 需要时升级。
- 验证 provider SDK / OpenAI-compatible adapters 的 structured output、tool calls、cache usage、provider request id 和 error classification。
- 验证 FastAPI SSE response/helper、Last-Event-ID、heartbeat、disconnect handling。
- 验证 asyncpg / SQLAlchemy transaction helper、pool timeout、serializable/read committed 策略。

完成标准：

- 依赖版本和 lockfile 更新完成，或明确记录阻塞和降级方案。D3-P0 已完成，后续不得在 D4 worker 实现中临时升级核心 LLM/runtime 包。
- focused tests 覆盖 PydanticAI structured output、usage extraction、provider cache normalization、route/profile resolution。
- LangGraph 明确不进入 D4 主路径，代码中不得临时引入第三个 orchestration 控制面。
- `app/llm/routes.py` 增加 Reader worker route 的设计确认，至少覆盖 `reader_layer_translation`，并为 D5 预留 `reader_layer_vocabulary` / `reader_layer_grammar_bundle`。
- D3 runtime skeleton 可以基于已验证依赖实现，不需要中途升级核心 LLM/runtime 包。

## D3. 后端骨架

Schema / Domain Contract：见 `modules/schema-and-domain-contract.md`。D3-P1 到 D3-P4 的四份 TMP 评审和两份 D3 contract review 已合并为该正式合同；实现以正式合同为准，不以 TMP 中的 `ReaderPlateSnapshotV2` 等临时命名为准。

### D3-P1. Schema Baseline

状态：completed on 2026-06-19，详细记录见 `docs/tmp/reader-orchestration/D3/TMP-D3-P1-schema-baseline-closeout.md`。

Closeout 结论：

- Fresh baseline migration 已新增 D3-P1 最小 Reader tables：`reading_records`、`original_inputs`、`reading_bases`、`reading_units`、`anchor_segments`、`reader_runs`、`reader_jobs`、`reader_job_events`、`reader_event_sequences`、`reader_events`、`enhancement_layers`、`parsed_decisions`。
- `ai_usage_events` 和 `user_credit_ledger` 已增加 nullable Reader attribution 字段。
- `reader_jobs` 已用 `(base_id, reading_record_id, expected_generation)` 复合 FK 绑定 `reading_bases(id, reading_record_id, record_generation)`。
- 只有 `job_type='build_base' AND target_type='record'` 可 `base_id IS NULL`；其他 job 必须带 base。
- `enhancement_layers` 已用 `(base_id, reading_record_id, generation)` 复合 FK 绑定 base generation。
- `reader_event_sequences` 使用 record-scoped counter，focused tests 覆盖 first sequence 和 rollback no-gap。
- `check_schema_baseline.sql` 已覆盖 D3-P1 全部新表。
- `active_base_id -> reading_bases.status='active'` 暂不做 DDL trigger，作为 service / publisher invariant。
- Focused tests 已通过：`test_reader_orchestration_schema_baseline.py`、`test_reader_orchestration_schema_models.py`、`test_jsonb_storage_contract.py`。

### D3-P2. Reading Base Builder + Base Plate Snapshot

状态：completed on 2026-06-19，详细记录见 `docs/tmp/reader-orchestration/D3/TMP-D3-P2-reading-base-builder-snapshot-closeout.md`。

Closeout 结论：

- 已实现低影响纯文本路径的 deterministic Reading Base Builder。
- 已从 Stable Base 生成 Reading Units、Anchor Segments 和 Navigation Skeleton。
- 已实现 D4 所需 `ReaderPlateSnapshot` serializer / Base Plate Snapshot builder。
- Snapshot builder 只从 domain facts 生成 Plate `value`，不读取旧 `render_scene_json`。
- Snapshot builder 会拒绝不属于当前 base / unit / anchor 的 layers、parsed decisions、ask supplements 和 user assets。
- 已实现最小 published translation layer snapshot projection，并补充 top-level layer 与 Plate value 对齐测试。
- 当前 Unit baseline 是 `1 structure block -> 1 reading unit`；target-length aggregation 留给 D5+ builder refinement。

D3-P2 不包含：

- Translation Worker。
- 数据库持久化 service。
- 公开 Reader API。
- Layer Publisher。
- `projection_ops` 端到端 applier。
- Web Reader UI 接入。
- LangGraph 或 LLM Planner。

Focused tests 已通过：

- `test_reader_orchestration_base_builder.py`
- `test_reader_orchestration_schema_models.py`
- `test_reader_orchestration_schema_baseline.py`
- targeted `ruff check`
- targeted `compileall`

### D3-P3. Article Ready Persistence Service

状态：completed on 2026-06-20，详细记录见 `docs/tmp/reader-orchestration/D3/TMP-D3-P3-article-ready-persistence-closeout.md`。

Closeout 结论：

- 已把 D3-P2 builder / snapshot 接到 D3-P1 schema。
- 已实现纯文本低风险提交的内部 application service。
- 已在一个事务内创建 `reading_records`、`original_inputs`、`reading_bases`、`reading_units`、`anchor_segments`。
- 已设置 `reading_records.active_base_id`，并显式校验 active base 属于同一 record、同一 generation 且 `status='active'`。
- 已初始化 `reader_event_sequences` 并写入 `reader_events.event_type='article_ready'`；sequence 从 `1` 开始，rollback 不产生 gap。
- 已将 `readiness_state` 推进到 `article_ready`，将 `product_state` 推进到 `readable_enhancing`。
- 已从数据库 facts 重建 `ReaderPlateSnapshot`，而不是复用提交时的内存对象。
- Snapshot reload 使用 read-only `repeatable_read` transaction，保证 `last_event_sequence` 与 domain facts 来自同一 consistent read。
- DB hydration 后调用 `validate_reading_base_build_result` 作为 Reading Base / Unit / Anchor Segment 全局 invariant 校验入口。

D3-P3 不包含：

- Translation Worker。
- run/job worker lease runtime。
- Layer Publisher。
- Web Reader UI 或 FastAPI 公开接口纵切。
- LangGraph、LLM Planner 或 PydanticAI worker。
- `projection_ops` 端到端 applier。

Focused tests 已通过：

- `test_reader_orchestration_article_ready_service.py`
- `test_reader_orchestration_base_builder.py`
- `test_reader_orchestration_schema_models.py`
- targeted `ruff check`
- targeted `compileall`

### D3-P4. Runtime Skeleton

状态：completed on 2026-06-20，详细记录见 `docs/tmp/reader-orchestration/D3/TMP-D3-P4-runtime-skeleton-closeout.md`。

Closeout 结论：

- 已在新 D3 schema 上实现最小 Reader run/job/event runtime 骨架。
- 已实现 claimable `reader_jobs` helper：`SELECT FOR UPDATE SKIP LOCKED`、lease token、lease expiry、attempt count、heartbeat。
- 已实现 stale claimed job recovery 和 `retry_later` 调度语义。
- 已实现 base/generation fence：claim/publish 拒绝 stale generation、非 active base、`active_base_id != job.base_id` 或 lease token mismatch。
- 已抽出 event publisher helper：在 publish transaction 内使用 `reader_event_sequences` 分配 committed UI sequence 并写入 `reader_events`。
- 已实现 polling event read model：`after_sequence`、`limit`、`last_event_sequence`、truncated response、empty stream、cursor already caught up 和 gap/reload 语义。
- D3-P4 保持不引入 LangGraph；runtime 主控仍是 PostgreSQL run/job/event。

D3-P4 不包含：

- 实际 Translation LLM Worker。
- PydanticAI worker 调用。
- Layer Publisher 业务发布完整逻辑。
- Web Reader UI。
- `projection_ops` 端到端 applier。
- LangGraph planner 或 branching flow。

Focused tests 已通过：

- `test_reader_orchestration_job_runtime.py`
- `test_reader_orchestration_event_runtime.py`
- `test_reader_orchestration_schema_baseline.py`
- targeted `ruff check`
- targeted `compileall`

### D4-P0. Backend Reader API + Snapshot/Polling Vertical Slice

状态：completed on 2026-06-20，详细记录见 `docs/tmp/reader-orchestration/D4/TMP-D4-P0-backend-reader-api-closeout.md`。

Closeout 结论：

- 已新增最小后端 Reader API surface，让 Web 可以走通 plain text submit、snapshot reload 和 event polling。
- `POST /reader/records/plain-text` 调用 `ArticleReadyPersistenceService.submit_plain_text`，返回 record id、base id、`article_ready` event sequence 和 `ReaderPlateSnapshot`。
- `GET /reader/records/{record_id}/snapshot` 调用 D3-P3 snapshot reload，从 DB facts 重建 `ReaderPlateSnapshot`。
- `GET /reader/records/{record_id}/events` 调用 D3-P4 `ReaderEventRuntime.poll_events`，支持 `after_sequence`、`limit`、`last_event_sequence`、truncated response 和 reload-required signal。
- 用户隔离复用 `AuthUserDep`；record 不存在或不属于当前 user 均返回 404。
- `client_record_id` blank 会规范化为 `NULL`；同一用户重复 active `client_record_id` 返回 409。
- 新 API 路径不读取旧 `render_scene_json`。

D4-P0 不包含：

- Translation Worker。
- Layer Publisher 业务逻辑。
- PydanticAI / LLM 调用。
- Web Reader UI。
- SSE endpoint 纵切；polling 先行。
- `projection_ops` 端到端 applier。

Focused tests 已通过：

- `test_reader_orchestration_api.py`
- `test_reader_orchestration_article_ready_service.py`
- `test_reader_orchestration_event_runtime.py`
- targeted `ruff check`
- targeted `compileall`

### D4-P1. Translation Layer Worker + Layer Publish Vertical Slice

状态：completed on 2026-06-21，详细记录见 `docs/tmp/reader-orchestration/D4/TMP-D4-P1-translation-layer-closeout.md`。

Closeout 结论：

- 已新增 deterministic translation run/job bootstrap，创建最小 `reader_runs` 与 base-scoped `reader_jobs`。
- `ReaderJobRuntime.claim_next_job()` 支持 `job_type` / `target_type` 过滤，translation worker 不会 claim mixed queue 中的非 translation jobs。
- Translation worker 使用 PydanticAI typed output 边界生成 `TranslationLayerOutput`；测试使用 fake translator，不调用真实 LLM。
- Layer publisher 在一个事务内写 `enhancement_layers(layer_type='translation')`、发布 `layer_published` event、完成 job transition 和 run completion。
- Snapshot reload 能看到 published translation layer，并在 Plate value 中投影 `reader_translation` node。
- 成功和失败路径均写 `ai_usage_events`，带 record / run / job / layer attribution、model route/profile/provider/name 和 operation fingerprint。
- retryable failure 后重新成功会清空 `reader_runs.failure_class` / `failure_code`，避免 completed run 带旧失败状态。

D4-P1 不包含：

- Web Plate Reader UI。
- vocabulary、grammar_note、sentence_analysis。
- SSE endpoint。
- Ask Document Tools。
- LangGraph flow。
- RAG substrate。
- URL / PDF / OCR / 文件上传。

Focused tests 已通过：

- `test_reader_orchestration_translation_worker.py`
- `test_reader_orchestration_layer_publisher.py`
- `test_reader_orchestration_job_runtime.py`
- `test_reader_orchestration_event_runtime.py`
- `test_reader_orchestration_article_ready_service.py`
- targeted `ruff check`
- targeted `compileall`

### D4-P2. Backend Orchestration Integration + Parsed Decision

状态：completed on 2026-06-21，详细记录见 `docs/tmp/reader-orchestration/D4/TMP-D4-P2-orchestration-parsed-closeout.md`。

Closeout 结论：

- 已新增 `ReaderOrchestrator` service，作为 D4 后端最小 orchestration facade。
- `POST /reader/records/plain-text` 现在通过 `ReaderOrchestrator.submit_plain_text_and_bootstrap_translation()` 先创建 article-ready facts，再启动 translation run/job。
- 已新增 testable tick path：`ReaderOrchestrator.tick_translation_worker()` 复用 D4-P1 `TranslationWorkerService`，从 queued translation job 推进到 `layer_published`。
- Translation layer published 后写最小 `parsed_decisions`，并发布 `parsed_decision_updated` event。
- Snapshot reload 可同时看到 translation layer 和 parsed decision；event polling 顺序覆盖 `layer_published` 后 `parsed_decision_updated`。
- 保持 PostgreSQL run/job/event 作为 durable control plane；未引入 LangGraph。
- D4-P2 没有新增 HTTP tick endpoint；worker tick 仍是 service/testable entry，后续是否暴露内部 route 另行设计。
- Parsed decision 写入与 layer publish 暂不同事务。D4 单线程 tick 可接受；如果 D5 需要强一致，应把 decision 写入收敛到 publisher transaction 或明确 compensating repair。

D4-P2 不包含：

- Web Plate Reader UI。
- SSE endpoint。
- vocabulary、grammar_note、sentence_analysis。
- Ask Document Tools。
- RAG substrate。
- URL / PDF / OCR / 文件上传。
- LangGraph flow。
- `projection_ops` 端到端 applier。

Focused tests 已通过：

- `test_reader_orchestration_orchestrator.py`
- `test_reader_orchestration_api.py`
- `test_reader_orchestration_translation_worker.py`
- `test_reader_orchestration_event_runtime.py`
- targeted `ruff check`

### D4-P3. Web Reader Plate Read-only Surface + BFF Polling Slice

状态：completed on 2026-06-21，详细记录见 `docs/tmp/reader-orchestration/D4/TMP-D4-P3-web-reader-plate-closeout.md`。

Closeout 结论：

- 已新增 Web BFF routes：
  - `POST /api/web/reader-plate/submit`
  - `GET /api/web/reader-plate/{recordId}/snapshot`
  - `GET /api/web/reader-plate/{recordId}/events`
- BFF 复用当前 Web session token，拒绝 anonymous / mock phone session；缺失或跨用户 record 仍由后端映射为 404。
- Web API client 只调用新 Reader API：`/reader/records/plain-text`、`/reader/records/{record_id}/snapshot`、`/reader/records/{record_id}/events`。
- 已新增 `ReaderPlateSnapshot` DTO mirror、只读 `ReaderPlateSnapshotSurface`、polling decision hook 和 `/app/reader-plate` 最小真实提交入口。
- Web polling 在 `layer_published`、`projection_reset_required` 或 server reload signal 时触发 snapshot reload；D4 不应用 `projection_ops`。
- 页面用户可见文案不暴露 D4、Plate.js、Snapshot、cursor、sequence 等实现术语。
- 新 Web 路径不读取旧 `/scene` 或 `render_scene_json`。

D4-P3 不包含：

- Rich Reader production UI polish。
- Selection bridge / anchor adapter。
- User highlights / notes。
- Ask Document Tools。
- `projection_ops` incremental applier。
- SSE endpoint。
- URL / PDF / OCR / 文件上传。

Focused tests 已通过：

- `pnpm --filter=@claread/web typecheck`
- `pnpm --filter=@claread/web test`
- `pnpm --filter=@claread/web build`

### D4-P4. Worker Runner Hardening + Web Smoke/Test Gap Closeout

状态：completed on 2026-06-21，详细记录见 `docs/tmp/reader-orchestration/D4/TMP-D4-P4-worker-web-hardening-closeout.md`。

Closeout 结论：

- 已新增 `TranslationWorkerRunner`，作为 D4 内部 callable runner，封装 single tick 与 bounded drain。
- Runner 不新增 public HTTP endpoint，不启动后台进程，不引入 LangGraph / MQ / Temporal / SSE。
- Runner 使用 `ReaderOrchestrator.tick_translation_worker()`，并把 worker result 分类为 `no_job`、`succeeded`、`retry_later`、`failed_terminal`、`fence_rejected`。
- Drain 遇到 retry / terminal failure / fence rejection 不立即停止，因为同一队列中可能仍有其他可处理 job；caller 通过 `WorkerDrainResult` 决定是否继续。
- 已新增 orphan diagnostic：查找 published translation layer 但缺失 `parsed_decisions` 的记录。D4 单线程 tick 下应返回空；D5 若引入并发 tick 或 crash recovery，再决定是否把 parsed decision 写入 publisher transaction 或补 repair。
- Web 侧补齐 Reader Plate BFF auth/error tests，覆盖 anonymous / mock phone 拒绝、上游 401/404/409/5xx/网络失败、空文本与成功提交。
- Web 侧新增 reader-plate Playwright smoke，使用 mocked BFF routes 验证真实页面交互、只读 Plate surface 渲染 source text 和 translation、polling caught-up 无错误。
- Web 页面与 polling 文案继续保持产品语义，不暴露 D4、Plate.js、Snapshot、cursor、sequence 等实现术语。

D4-P4 不包含：

- 真实后台 worker daemon。
- Public 或 internal HTTP tick endpoint。
- Crash-recovery repair job。
- `projection_ops` incremental applier。
- vocabulary、grammar bundle、Ask tools、RAG、SSE 或 LangGraph flow。
- 真实后端/auth 的 browser E2E；当前 smoke 只验证浏览器渲染与交互路径。

Focused tests 已通过：

- `uv run ruff check app/services/reader_orchestration tests/test_reader_orchestration_worker_runner.py tests/test_reader_orchestration_orchestrator.py`
- `uv run pytest tests/test_reader_orchestration_worker_runner.py tests/test_reader_orchestration_orchestrator.py tests/test_reader_orchestration_api.py tests/test_reader_orchestration_translation_worker.py tests/test_reader_orchestration_event_runtime.py -q`
- `pnpm --filter=@claread/web typecheck`
- `pnpm --filter=@claread/web test`
- `pnpm --filter=@claread/web build`
- `pnpm --filter=@claread/web test:e2e -- reader-plate-smoke.spec.ts`

## D4. 最小纵切

流程：

1. Web 提交文本。
2. 后端创建 Original Input 和 Reading Record。
3. 低影响 base path 创建 Stable Reading Base。
4. 创建 Reading Units、Anchor Segments 和 Navigation Skeleton。
5. 创建 Base Plate Snapshot。
6. Reader 到达 `article_ready`。
7. Translation layer 为第一个/当前 units 发布。
8. 记录 Parsed Decision。
9. Web Plate Reader Surface 渲染稳定文章 + 渐进译文。

完成标准：

- 用户可在 full coverage 前开始阅读。
- 刷新/恢复后状态正确。
- Annotation layer 不修改 Stable Base source text；只通过 projection 呈现在 Plate Article Body。
- LLM 调用有 usage event。
- 旧 `render_scene_json` contract 不参与新 Web Reader 路径。
- RAG substrate 可以在后台构建，不阻塞阅读。
- **Plate.js 承接 Article Body**：Web 直接加载 Base Plate Snapshot；readOnly 起步，具备 selection bridge。
- **D4 不要求 projection_ops 端到端**：translation layer 可先通过 snapshot reload 或 simple projection refresh 呈现；D5 才接增量 `projection_ops`。

明确不包含：

- URL / PDF / OCR / 文件上传实现。
- Candidate Base preview/edit/confirm UI。
- vocabulary、grammar_note、sentence_analysis、summary、Semantic Outline。
- 小程序适配。
- 旧 Reader scene 兼容映射。

## D5. 增强扩展

任务包：

- 增加 vocabulary、grammar bundle layers：发布为 `grammar_note` 与 `sentence_analysis` 两个 subtype。
- 增加 anchor validation gates。
- 增加 summary / Semantic Outline 作为 planner-selected optional layers。
- 完善 learning policy variants；academic 只保留未来扩展点，不在本轮实现。
- 增加 local retry / repair。
- 增加 Parsed Decision 和 anchor failures 的 eval sampling。

完成标准：

- 不使用机械 annotation-count threshold。
- 失败层不让文章不可读。
- Parsed coverage 单调递增。
- **projection_ops 端到端可用**：
  - Layer Publisher 在 publish 末尾同事务 emit domain event + `projection_ops`。
  - 前端订阅 `projection_ops` event，把 domain target 解析成当前 Plate path，再应用 Plate transforms；snapshot reload 作为 fallback。
  - Plate path adapter 提供 `unitIdToPath`、`anchorSegmentIdToPath`、`pathToAnchorSegment`、`selectionToDomainAnchor`。
  - owner 权限层覆盖 `stable`、`system_ai`、`ask_supplement`、`user`、`ephemeral`，前端镜像后端拒绝逻辑。
  - 增强层（vocab / grammar_note / sentence_analysis / summary）以 typed layer result + sanitized fragment 投影为 Plate marks/nodes；是否使用 Plate AI/suggestion 插件取决于 D2-P0 license/API 结论。

### D5-V1. Vocabulary Layer Backend Slice

状态：completed on 2026-06-21，详细记录见 `docs/tmp/reader-orchestration/D5/TMP-D5-V1-vocabulary-backend-closeout.md`。

Closeout 结论：

- 已新增 `VocabularyLayerOutput` typed schema，保留旧 AI Workflow 的三类 item subtype：`vocab_highlight`、`phrase_gloss`、`context_gloss`。
- 三类 item 属于同一个 `enhancement_layers.layer_type = 'vocabulary'`；不拆成三个顶层 layer type。
- Vocabulary anchor 使用 `anchor_segment_id`、UTF-16 range、`selected_text` 和 FNV text hash；`sentence_id` 只作为可选兼容 alias，不是权威锚点。
- DB baseline 正式支持 `reader_jobs.job_type = 'build_vocabulary_layer'`；不再挪用 `build_base`，也不把 job 语义藏在 `input_json.job_intent`。
- 已新增 `VocabularyJobBootstrapService`、`VocabularyWorkerService`、`VocabularyLayerPublisher` 与 focused tests。
- Worker 默认 executor 是未配置失败路径，会把 job/run 标为 `failed_terminal` 且不发布空 layer；只有显式注入 `FakeVocabularyExecutor()` 时才允许发布空 `VocabularyLayerOutput(items=[])`。
- Publisher 在事务内校验 unit、anchor segment、UTF-16 range、selected text 和 hash，成功后写 `enhancement_layers(layer_type='vocabulary')` 与 `reader_events(event_type='layer_published')`。
- Snapshot reload 目前只暴露 top-level `enhancement_layers` metadata；D5-V1 不实现 Plate vocabulary marks/nodes。

D5-V1 不包含：

- Web Plate vocabulary projection / rendering。
- `projection_ops` incremental applier。
- real PydanticAI vocabulary executor / prompt。
- parsed decision for vocabulary。
- grammar bundle、Ask tools、RAG、SSE 或 LangGraph flow。

Focused tests 已通过：

- `uv run ruff check app/schemas/reader_orchestration.py app/services/reader_orchestration tests/test_reader_orchestration_vocabulary_worker.py tests/test_reader_orchestration_schema_models.py tests/test_reader_orchestration_schema_baseline.py`
- `uv run pytest tests/test_reader_orchestration_schema_baseline.py tests/test_reader_orchestration_vocabulary_worker.py tests/test_reader_orchestration_translation_worker.py tests/test_reader_orchestration_orchestrator.py tests/test_reader_orchestration_job_runtime.py tests/test_reader_orchestration_layer_publisher.py tests/test_reader_orchestration_schema_models.py -q`

### D5-V2. Vocabulary Projection / Web Read-only Rendering

状态：completed on 2026-06-21，详细记录见 `docs/tmp/reader-orchestration/D5/TMP-D5-V2-vocabulary-projection-closeout.md`。

Closeout 结论：

- Published `vocabulary` layer 继续以 `VocabularyLayerOutput` 作为 domain truth；Plate marks 是 snapshot projection，不是持久事实。
- Snapshot reload 会按当前 base/unit/anchor 重新校验 layer output，并把三类 item 投影为 stable source leaf 上的 `reader_vocabulary_marks`。
- Vocabulary mark 使用 `anchor_segment_id` + unit-local UTF-16 `start_offset` / `end_offset`；serializer 派生 leaf 内 `segment_start_utf16` / `segment_end_utf16`、`starts_here`、`ends_here`。
- Web read-only surface 已能区分展示 `vocab_highlight`、`phrase_gloss`、`context_gloss`；translation node 保持原有 projection 形态。
- D5-V2 没有读取旧 `render_scene_json`，没有持久化 Plate path/op，也没有启用 `projection_ops` incremental applier。
- Review 修正：vocabulary snapshot layer 必须 `target_scope='unit'`，不能投到 `anchor_segment` scope；测试已覆盖该边界。

D5-V2 不包含：

- real PydanticAI vocabulary executor / prompt。
- grammar bundle。
- Ask tools / user editable vocabulary interactions。
- RAG、SSE 或 LangGraph flow。
- `projection_ops` incremental applier。

Focused tests 已通过：

- `uv run ruff check app/services/reader_orchestration/snapshot.py tests/test_reader_orchestration_base_builder.py tests/test_reader_orchestration_vocabulary_worker.py`
- `uv run pytest tests/test_reader_orchestration_base_builder.py tests/test_reader_orchestration_vocabulary_worker.py tests/test_reader_orchestration_schema_models.py -q`
- `pnpm --filter=@claread/web typecheck`
- `pnpm --filter=@claread/web test`
- `pnpm --filter=@claread/web build`（沙箱网络无法拉 Google Fonts 时会失败；联网重跑已通过）
- `pnpm --filter=@claread/web test:e2e -- reader-plate-smoke.spec.ts`

### D5-V3. Real Vocabulary Executor / Prompt

状态：completed on 2026-06-21，详细记录见 `docs/tmp/reader-orchestration/D5/TMP-D5-V3-real-vocabulary-executor-closeout.md`。

Closeout 结论：

- 已新增 `reader_layer_vocabulary` model route、`reader_vocabulary_model_profile` 配置和 prompt registry 入口。
- `reader_layer_vocabulary` 必须显式配置 `reader_vocabulary_model_profile` 才会注册；不得 fallback 到 annotation model profile。
- `PydanticAIVocabularyExecutor` 让模型输出内部候选 schema，不让 LLM 直接输出正式 `VocabularyLayerOutput`、UTF-16 offsets、hash、Plate JSON 或 raw ops。
- 后端 deterministic postprocess 只接收 `anchor_segment_id + selected_text`，在目标 Anchor Segment 内 exact-match 后生成 unit-local UTF-16 offsets、`selected_text` 和 `fnv1a32-utf16` hash。
- 找不到文本、重复命中、unknown segment、重复 candidate 或结构化输出无效时 fail closed 或跳过对应 item，并把原因写入 `quality_json.diagnostics`；不会发布错误 anchor。
- 同一 span 冲突按 `context_gloss > phrase_gloss > vocab_highlight` 保留，非冲突项保持稳定输入顺序。
- Candidate output 有硬上限和字段长度限制；diagnostics 会裁剪数量和文本长度，防止坏模型撑大 payload。
- 模型返回空 items 或全部候选被安全跳过时，允许发布空 `VocabularyLayerOutput(items=[])`，用于标记该 unit 已处理；跳过原因必须留在 diagnostics。
- D5-V3 不改变 public `VocabularyLayerOutput` schema，不读取旧 `render_scene_json`，不启用 `projection_ops` incremental applier。

D5-V3 不包含：

- Grammar bundle。
- vocabulary parsed decision / coverage policy。
- Ask tools / user editable vocabulary interactions。
- RAG、SSE 或 LangGraph flow。
- `projection_ops` incremental applier。

Focused tests 已通过：

- `uv run ruff check app/config/settings.py app/llm/routes.py app/llm/registry.py app/services/reader_orchestration/vocabulary_worker.py tests/test_reader_orchestration_vocabulary_worker.py tests/test_reader_orchestration_vocabulary_executor.py`
- `uv run pytest tests/test_reader_orchestration_vocabulary_executor.py tests/test_reader_orchestration_vocabulary_worker.py -q`
- `uv run pytest tests/test_reader_orchestration_translation_worker.py tests/test_reader_orchestration_orchestrator.py tests/test_reader_orchestration_layer_publisher.py tests/test_reader_orchestration_job_runtime.py -q`
- `uv run pytest tests/test_reader_orchestration_schema_models.py tests/test_reader_orchestration_schema_baseline.py -q`
- `uv run python -m compileall app/services/reader_orchestration/vocabulary_worker.py app/llm/routes.py app/llm/registry.py app/config/settings.py tests/test_reader_orchestration_vocabulary_executor.py tests/test_reader_orchestration_vocabulary_worker.py`

### D5-V4. Grammar Bundle Backend Slice

状态：completed on 2026-06-21，详细记录见 `docs/tmp/reader-orchestration/D5/TMP-D5-V4-grammar-bundle-backend-closeout.md`。

Closeout 结论：

- 已新增正式 `reader_jobs.job_type = 'build_grammar_bundle'`，固定 `target_type = 'unit'` 和 `operation_fingerprint = 'grammar_bundle_unit_v1'`。
- 已新增 grammar typed schema：`GrammarNoteItem`、`SentenceAnalysisChunk`、`SentenceAnalysisItem`、`GrammarNoteLayerOutput`、`SentenceAnalysisLayerOutput` 和 internal `GrammarBundleOutput`。
- `GrammarJobBootstrapService` 会选择当前 active base 下最早未处理 unit，并避免重复 active/succeeded grammar bundle job。
- `GrammarBundleWorkerService` 默认 unconfigured executor 失败且不发布 layer；显式 fake executor 可用于 focused tests。
- `GrammarBundleLayerPublisher` 将一次 bundle 发布拆成两个独立 layer rows：`grammar_note_unit_v1` 与 `sentence_analysis_unit_v1`，并发布对应 `layer_published` events。
- Empty sanitized output 采用 no-op success：不插入 layer，不发布 `layer_published` reader event，job/run 成功，`output_ref_json.no_op = true`。
- Usage attribution 采用单条 job-level `ai_usage_events`，`enhancement_layer_id = NULL`，metadata 记录 produced layer ids/types 与 no-op，避免双 layer 重复计费。
- fallback_window 处理：`sentence_analysis` 命中 fallback window 时跳过；`grammar_note` 任一 span 命中 fallback window 时整条 item 跳过，不发布部分 grounding。
- Snapshot reload 保持 read-only；D5-V4 只暴露 top-level grammar layer metadata，不投影到 `snapshot.value`。

D5-V4 不包含：

- real PydanticAI grammar executor / prompt。
- Web grammar projection / rendering。
- grammar parsed decision / coverage policy。
- Ask tools、RAG、SSE、LangGraph flow。
- `projection_ops` incremental applier。

Focused tests 已通过：

- `uv run ruff check app/schemas/reader_orchestration.py app/services/reader_orchestration/grammar_worker.py app/services/reader_orchestration/job_bootstrap.py app/services/reader_orchestration/layer_publisher.py app/services/ai_usage tests/test_reader_orchestration_grammar_worker.py tests/test_reader_orchestration_schema_models.py tests/test_reader_orchestration_schema_baseline.py`
- `uv run pytest tests/test_reader_orchestration_grammar_worker.py tests/test_reader_orchestration_schema_models.py tests/test_reader_orchestration_schema_baseline.py -q`

### D5-E1. Vocabulary Eval Seed Disposition

状态：accepted_with_changes on 2026-06-21，详细记录见 `docs/tmp/reader-orchestration/D5/TMP-D5-vocabulary-eval-seed-disposition.md`。

结论：

- 评估方向接受：优先建立 vocabulary deterministic eval seed，覆盖 anchor resolution、bounds compliance、diagnostics coverage、same-span arbitration 和 item quality。
- 原调研中的单文件 `vocabulary_seed_v1.jsonl` 不采纳；实现必须匹配现有 `evals` harness 的 `dataset.yaml + cases/*.json` 目录形态，或显式新增 vocabulary seed loader。
- 原调研中的 `evals/claread_eval/judge/judges/vocabulary_judge.yaml` 不采纳为下一步范围；当前 judge runner/rubric contract 仍是 article-analysis oriented，LLM judge 泛化单独后置。
- LangSmith `evaluate()` 不进入下一步；先用本地 deterministic graders 和 pytest 验收。
- 超过 5 个 candidate 的预期需按 D5-V3 真实实现修正：通常在 `VocabularyCandidateOutput` validation 阶段 fail closed，不作为普通 `candidate_limit_exceeded` diagnostics gate。
- vocabulary `boundary_low_fallback_window` 在 D5-G2 后已成为 acceptance gate；vocabulary worker 在 `_build_vocabulary_output_from_candidates` 中显式拒绝 `segment_type=fallback_window` 的候选 item，reason_code 写入 `diagnostics.skipped_items[]`，与 grammar bundle 口径一致。

### D5-R1. LangGraph / Orchestration Architecture Review Disposition

状态：`accepted_with_changes` on 2026-06-22，详细记录见 `docs/tmp/reader-orchestration/D5/TMP-D5-langgraph-orchestration-disposition.md`。

结论：

- 两份 LangGraph / orchestration 架构评估的大方向接受：当前 PostgreSQL durable control plane + PydanticAI typed worker + Plate snapshot projection 的三层架构符合本轮 Reader 重构目标。
- D5 主链路 runner、translation、vocabulary、grammar bundle、snapshot projection 和 eval 任务不引入、不升级 LangGraph。
- LangGraph 不得替换 `reader_runs`、`reader_jobs`、`reader_events`、`enhancement_layers` 或 Reader product state。
- D6-LG0 仅作为隔离 spike 候选；触发条件必须是具体 Ask Document Tools / human approval / multi-branch repair flow 需求。
- D5 第一条页面可测主链路继续使用 snapshot reload；`projection_ops` incremental applier 不阻塞 smoke。
- 评估中被修正的风险排序：`parsed_decisions` 跨事务和 vocabulary boundary policy 是 P1；`active_base_id -> status='active'` 是 service / publisher invariant hardening，不是 D5 主线 P0；`projection_ops` race 只在启用 incremental applier 前需要 spike。

下一步影响：

- D5 主链路 runner 只做 deterministic bootstrap/drain，不引入 LangGraph / MQ / Temporal / SSE。
- Runner review 后优先做页面 smoke，再进入 parsed decision repair、vocabulary boundary policy 和 projection ops consistency guardrails。

### D5-R2. Main Chain Runner + Web Record Load Closeout

状态：completed on 2026-06-22。

Closeout 结论：

- 已新增 `ReaderEnhancementPipelineRunner`，统一 bootstrap / drain `translation`、`vocabulary`、`grammar_bundle` jobs。
- Runner 复用现有 `ArticleReadyPersistenceService`、`EnhancementJobBootstrapService`、`ReaderJobRuntime`、三类 worker 和 Layer Publisher，不另建 orchestration 控制面。
- Runner drain 顺序为 translation -> vocabulary -> grammar bundle；遇到 `retry_later`、`failed_terminal` 或 publish fence supersede 时返回 attention summary。
- `ReaderJobRuntime.claim_next_job()` 已支持可选 `reading_record_id`、`base_id`、`expected_generation` scope；三类 worker 增加 record-scoped claim/process 入口，runner 不会消费其他 Reading Record 的 queued jobs。
- Runner 不新增 public HTTP endpoint，不启动后台 daemon，不引入 LangGraph / MQ / Temporal / SSE，也不启用 `projection_ops` incremental applier。
- 已新增本地 D5 dev smoke harness / CLI，用于准备 record 和验证 snapshot reload；fake executors 默认禁用，必须显式 opt-in，且生产环境禁用。该 harness 不是产品运行路径。
- Web `/app/reader-plate` 已支持 `record_id` / `recordId` query 直达加载已有 `ReaderPlateSnapshot`；提交成功后会把 URL replace 到 `?record_id=...`。
- Web 页面继续通过现有 BFF snapshot/events 路径读取新 Reader API，不回退旧 `/scene` 或 `render_scene_json`。
- 当前页面可测路径包括：已准备好的 record -> snapshot/events -> Reader Plate 渲染 source、translation、vocabulary、grammar_note、sentence_analysis。

D5-R2 不包含：

- 生产后台 worker loop / daemon。
- public 或 internal HTTP worker-control endpoint。
- 页面 submit 后自动同步执行真实 LLM 全链路。
- `projection_ops` incremental applier。
- parsed decision repair、vocabulary boundary policy 或 readiness/coverage policy。

Focused tests 已通过：

- `uv run ruff check app/services/reader_orchestration/smoke_harness.py scripts/prepare_reader_d5_smoke.py tests/test_reader_orchestration_smoke_harness.py`
- `uv run pytest tests/test_reader_orchestration_smoke_harness.py tests/test_reader_orchestration_pipeline_runner.py -q`
- `uv run pytest tests/test_reader_orchestration_smoke_harness.py tests/test_reader_orchestration_pipeline_runner.py tests/test_reader_orchestration_orchestrator.py tests/test_reader_orchestration_translation_worker.py tests/test_reader_orchestration_vocabulary_worker.py tests/test_reader_orchestration_grammar_worker.py -q`
- `pnpm --filter=@claread/web typecheck`
- `pnpm --filter=@claread/web test`
- `pnpm --filter=@claread/web test:e2e -- tests/e2e/reader-plate-smoke.spec.ts`

### D5-W1. Local / Deployment Worker Loop Evaluation

状态：`accepted_with_changes` on 2026-06-22，评估材料已移至 `docs/tmp/reader-orchestration/D5/TMP-D5-D6-worker-loop-evaluation-2026-06-22.md`。

结论：

- 接受独立 worker process 方向：本地用 CLI entrypoint 启动，部署时作为独立 worker service / process / container 运行。
- Worker loop 复用 `ReaderEnhancementPipelineRunner`，不另建 orchestration 控制面。
- API 服务保持 request-serving；worker loop 不挂到 FastAPI lifespan / startup task，避免 dev reload、多副本和 API 请求路径混杂。
- Web submit 仍只负责创建 `article_ready` facts；不要把 runner 同步塞进 submit request。
- 不新增 public 或 semi-public worker-control endpoint。
- 不使用 smoke harness / fake executors 作为产品运行路径；fake 仍只能显式 opt-in 用于 dev/test。
- 真实 model profile 缺失保持 fail-closed，不静默 fallback 到 fake 或 synthetic layer。

修正口径：

- 扫描条件不能只写死 `readiness_state = 'article_ready'`。因为 `readiness_state` 是单调 milestone，worker loop 初版应默认考虑 `article_ready` 与 `initial_enhancement_ready`，并把 `coverage_complete` 作为默认停止态；如后续有 repair / retry policy，再显式允许 coverage-complete records 回到 eligible set。
- 粗筛只负责找候选 record；exact missing work 仍由 `EnhancementJobBootstrapService` / `ReaderEnhancementPipelineRunner` 决定，不在 scanner 复制每个 layer 的 eligibility 逻辑。
- 初版并发保持保守：per-record advisory lock 必须有；per-user concurrency 和 per-worker concurrency 默认 `1`，后续再通过配置放宽。
- `retry_later` 不应导致 hot-loop；worker loop 应尊重 job `available_at` 或通过 runnable-job 优先扫描减少空转。
- `failed_terminal` 初版只进入 logs / metrics / summary；是否映射为 `product_state='action_required'` 留给 D6 product hardening。

最小实现建议：

1. `ReaderEnhancementWorkerLoopService`：扫描 eligible records、获取 advisory locks、调用 runner、解释 summary。
2. `scripts/run_reader_enhancement_worker.py`：初始化 DB，按 scan interval / batch size / runner limits 循环。
3. 新增 settings：`reader_worker_scan_interval_seconds`、`reader_worker_batch_size`、`reader_worker_max_ticks`、`reader_worker_max_jobs`、`reader_worker_lease_owner_prefix`。不新增 fake executor product config。
4. Focused tests 覆盖 eligibility scan、record/user lock、record-scoped runner、retry_later backoff、missing profile fail-closed 和 stale base fence。

### D5-W2. Local / Deployment Worker Loop Closeout

状态：completed on 2026-06-22。

Closeout 结论：

- 已新增 `ReaderEnhancementWorkerLoopService`，使用 coarse eligibility scan + per-record / per-user advisory locks 调度 `ReaderEnhancementPipelineRunner`。
- worker loop scanner 只筛 `reading_records` / `reading_bases` 的 coarse readiness，不复制 translation / vocabulary / grammar bundle 的 missing-work 判定。
- scanner 会优先处理当前 active base / generation 下存在 runnable jobs 的 record；无 runnable jobs 时，仅对当前 generation 不存在 tracked jobs 的 record 允许重新进入 bootstrap，从而避免 `retry_later` hot-loop 和 `failed_terminal` 反复重建。
- `retry_later` 继续尊重 `available_at`；`failed_terminal` 只进入 summary / log，不修改 `product_state='action_required'`。
- 已新增 `scripts/run_reader_enhancement_worker.py`，支持 `--once` 和 loop mode；本地和部署共用同一入口。
- 已新增 settings：`reader_worker_scan_interval_seconds`、`reader_worker_batch_size`、`reader_worker_max_ticks`、`reader_worker_max_jobs`、`reader_worker_lease_owner_prefix`。
- 未新增 public endpoint，未把 runner 放进 Web submit，未挂到 FastAPI lifespan，未引入 LangGraph / MQ / SSE / `projection_ops`。
- API / Web / worker 的真实本地启动步骤、mock phone session 获取、model profile wiring 和 fail-closed 观察方式见 `docs/initiatives/reader-agentic-orchestration/modules/local-real-chain-runbook.md`；该 runbook 只验证 CLI help / 参数解析与配置连线，未在文档任务中实跑真实 provider / LLM。

Focused tests 已通过：

- `uv run ruff check app/services/reader_orchestration app/config/settings.py tests/test_reader_orchestration_worker_loop.py scripts/run_reader_enhancement_worker.py`
- `uv run pytest tests/test_reader_orchestration_worker_loop.py tests/test_reader_orchestration_pipeline_runner.py -q`

### D5-G1/G2. Runtime Guardrails Closeout

状态：completed on 2026-06-22。

Closeout 结论：

- D5-G1 已把 translation layer publish 与最小 `parsed_decisions` 写入收敛到同一 publisher transaction，消除 layer 已发布但 parsed decision 正常缺失的 crash gap。
- `diagnose_orphaned_translation_decisions()` 保留为 diagnostic，用于发现 pre-D5 遗留数据或测试中人为制造的 partial state；snapshot reload 不做隐式 repair。
- D5-G2 已统一 vocabulary 与 grammar bundle 的 fallback_window boundary policy：`segment_type = fallback_window` 的 anchor segment 不产出 vocabulary / grammar item。
- Vocabulary fallback skip 使用 reason_code `boundary_low_fallback_window`，写入 worker diagnostics；空有效 vocabulary output 仍可发布，用于标记该 unit 已处理。
- Vocabulary eval seed 已新增 fallback-window skip fixture，并更新 baseline。

Focused tests 已通过：

- `uv run ruff check app/services/reader_orchestration app/config/settings.py tests/test_reader_orchestration_layer_publisher.py tests/test_reader_orchestration_orchestrator.py tests/test_reader_orchestration_vocabulary_worker.py tests/test_reader_orchestration_worker_loop.py scripts/run_reader_enhancement_worker.py`
- `uv run pytest tests/test_reader_orchestration_layer_publisher.py tests/test_reader_orchestration_orchestrator.py tests/test_reader_orchestration_vocabulary_worker.py tests/test_reader_orchestration_worker_loop.py tests/test_reader_orchestration_pipeline_runner.py -q`
- `uv run ruff check claread_eval/schemas/vocabulary.py claread_eval/graders/vocabulary.py scripts/build_vocabulary_seed.py tests/test_vocabulary_dataset.py tests/test_vocabulary_graders.py tests/test_vocabulary_seed_pipeline.py tests/test_vocabulary_runner.py tests/test_vocabulary_baseline.py`
- `uv run pytest tests/test_vocabulary_dataset.py tests/test_vocabulary_graders.py tests/test_vocabulary_seed_pipeline.py tests/test_vocabulary_runner.py tests/test_vocabulary_baseline.py -q`

## D6. 产品硬化

任务包：

- 高影响适配的 Candidate Reading Base preview/edit/confirm。
- Library states：processing、readable/enhancing、paused、needs_confirmation、failed、quota_required。
- continuation、quota、retry、re-parse-as-new-record 的 action-required UX。
- Ask sidecar action envelope：continue enhancement、save note、context expansion。
- cost / credit decision surfaces。
- 失败恢复和 support/debug details。

完成标准：

- action-required states 在 Reader 和 Library 都可发现。
- Ask 不能绕过 Authorization Envelope。
- 超长文 continuation 可 pause/resume。

## Coding Agent 任务规则

- 每个 coding task 尽量控制在 2-8 小时。
- 每个任务必须写清 touched areas、expected tests、done criteria。
- 除非任务明确要求，agent 不读取 TMP research。
- agent 只更新本计划的阶段/任务状态或决策引用。
- 发现架构冲突时，先更新或讨论 `target-architecture.md` 的决策记录，再继续实现。

## 当前下一步

进入 D5 guardrails 与运行形态收口：

1. D5-G1 parsed decision same-transaction decision 已完成；保留 orphan diagnostic 只用于历史/人为 partial state 检测。
2. D5-G2 vocabulary boundary policy 已完成；vocabulary 与 grammar 统一跳过 `fallback_window` 并记录 `boundary_low_fallback_window` diagnostics。
3. projection ops consistency spike 后置：D5 页面 smoke 继续使用 snapshot reload，只有启用 incremental applier 前才处理 race / replay / path adapter consistency。
4. D6 product hardening 再决定 `failed_terminal` 是否映射到 `action_required`、是否引入 coverage / rerun policy 和更细粒度调度 hint。
5. 保持 LangGraph D6+ 隔离 spike 口径，不在 D5 guardrails 中升级或引入。
