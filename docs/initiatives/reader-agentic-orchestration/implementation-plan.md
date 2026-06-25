# Reader Agentic Orchestration 实施计划

> 状态：`D6 进行中`
> 最后更新：2026-06-25

## 成功标准

当 Web 用户提交 learning 输入后，系统能够提供：

- 长期存在的 Reading Record。
- Stable Reading Document、Stable Document Blocks、Canonical Text Layer 和不可变 Reading Units。
- 早于完整增强完成的 `article_ready`。
- 可恢复的渐进 Enhancement Layers。
- 带 audit / eval hooks 的 Parsed Decisions。
- 基于新 base 的 Ask Claread sidecar，且不成为 orchestrator。
- run / step / layer 级 usage events。
- 可重置的 baseline schema，并保留词典三表。
- 当前记录内可构建和恢复的 RAG substrate，覆盖 Stable Reading Document 并支持 block-scoped citation。
- 文本、URL、PDF、OCR、文件上传等输入模式的统一适配入口，产出 Candidate Document / Stable Reading Document。
- 不依赖旧 `render_scene_json` contract 的新 Web Reader Plate projection。

## 阶段门禁

| Phase | 名称 | 状态 | 门禁 |
|---|---|---|---|
| D0 | 边界决策 | ✅ 完成 | scope、数据重置、Web first、framework spike、queue posture 已记录 |
| D0.5 | 接入边界确认 | ✅ 完成 | RAG、输入适配、orchestration 入口三类粗合同已写入目标架构 |
| D1 | 架构 RFC | ✅ 完成 | schema、runtime、events、milestones、product states 可评审 |
| D2 | 技术 spikes | ✅ 完成 | 依赖升级、DB job lease、SSE/polling、bounded worker、RAG/OCR/OSS、Length Class、成本基线有结果 |
| D3 | 后端骨架 | ✅ 完成 | 新 schema、domain services、run/job 状态机、events、usage audit 编译并通过 focused tests |
| D4 | 最小纵切 | ✅ 完成 | Web submit -> article_ready -> translation layer -> parsed decision -> progressive Reader display |
| D5 | 增强扩展 | ✅ 完成 | vocabulary、grammar bundle（grammar_note + sentence_analysis）、summary/outline policy、anchor validation、repair、eval |
| D6 | 产品硬化 | 🔄 进行中 | Candidate preview、Library states、quota/action_required、Ask sidecar actions、failure recovery |

### D5 子阶段索引

| 子阶段 | 内容 | 状态 |
|---|---|---|
| D5-V1 | Vocabulary Layer Backend Slice | ✅ 完成 |
| D5-V2 | Vocabulary Projection / Web Read-only Rendering | ✅ 完成 |
| D5-V3 | Real Vocabulary Executor / Prompt | ✅ 完成 |
| D5-V4 | Grammar Bundle Backend Slice | ✅ 完成 |
| D5-V5 | Sentence Analysis Long-text Validation | ✅ 完成 |
| D5-E1 | Vocabulary Eval Seed Disposition | ✅ accepted_with_changes |
| D5-R1 | LangGraph / Orchestration Architecture Review | ✅ accepted_with_changes |
| D5-R2 | Main Chain Runner + Web Record Load | ✅ 完成 |
| D5-R4 | Real Provider Local Chain Validation | ✅ 完成 |
| D5-R5 | Schema Health Check + Worker Lease Duration | ✅ 完成 |
| D5-R6 | Sentence Analysis Long-text Projection Consistency | ✅ 完成 |
| D5-W1 | Local / Deployment Worker Loop Evaluation | ✅ accepted_with_changes |
| D5-W2 | Local / Deployment Worker Loop | ✅ 完成 |
| D5-G1/G2 | Runtime Guardrails（parsed decision same-tx + boundary policy） | ✅ 完成 |

### D6 子阶段索引

| 子阶段 | 内容 | 状态 |
|---|---|---|
| D6-A0 | Ask / Notes / Highlights Dependency Audit | ✅ 完成 |
| D6-A1 | Web read-only anchor draft helper | ✅ 完成 |
| D6-A3 | Ask tool signature / write-proposal anchor contract | ✅ 完成 |
| D6-A5 | Notes / Highlights dual-contract spike | ✅ 完成 |
| D6-A6 | Reading Record Ask minimal slice（F1 后端切线 + F2/B1 RR scope Web 接线） | ✅ 完成 |
| D6-U2 | Multi-anchor contract decision（single-range first） | ✅ 完成 |
| D6-U3 | V1c single-range persistence design | ✅ 完成 |
| D6-U4 | V1c single-range persistence 实现（migration + runtime 写入） | ✅ 完成 |
| D6-U5 | user_assets read projection into ReaderPlateSnapshot | ✅ 完成 |
| D6 product-state | failed_terminal classifier（保守映射为 failed） | ✅ 完成 |
| W3-D0~D9 | Web cutover：submit landing / recent recovery / list source / Library section / command palette / activity indicator / Vocabulary guard | ✅ 完成 |
| UI-D4~D6C | ReaderRecordPlateSurface 阅读态打磨 / 默认 Plate mode / 真实流程验收 / UI polish | ✅ 完成 |
| D6-I0 | Input / document model product decision：Candidate Document、Stable Reading Document、Stable Document Blocks、Canonical Text Layer、V1 输入范围、PDF/OCR gate、RAG source scope | ✅ 完成 |
| D6-I1 | Stable Document Block contract + schema design | ⏳ 待做 |
| D6-I2 | Candidate Document persistence + preview/confirm API | ⏳ 待做 |
| D6-I3 | Upload / Source Artifact adapter（OSS + local dev） | ⏳ 待做 |
| D6-I4 | Markdown/txt document parser + Input Suitability Gate | ⏳ 待做 |
| D6-I5 | PDF parser + page quality gate + optional LLM reviewer | ⏳ 待做 |
| D6-I6 | OCR provider adapter + multi-image Candidate Document | ⏳ 待做 |
| D6-RAG1 | Block-scoped RAG substrate schema/chunker/citation validator | ⏳ 待做 |
| D6-RAG2 | VectorStoreAdapter + record-scoped indexing worker | ⏳ 待做 |
| D6 后续 | 旧入口改线（Library/Vocabulary/active task 切新 Reading Record） | ⏳ 待做 |
| D6 后续 | Ask 切线（依赖 reader_ask/service.py 拆分或 adapter 化） | ⏳ 待做 |
| D6 后续 | 词典 AI / 词汇保存 / 阅读模式切换 / projection_ops 增量 applier | ⏳ 待做 |
| D6 后续 | 删除 legacy analysis/ + reader_scene.py + ReaderWorkbench | ⏳ 待做 |

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
- OSS / OCR / 文档解析只产生 Source Artifact / Extraction Result / Candidate Reading Document，不直接写 Stable Reading Document / Blocks / Canonical Text。
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
- D2 spike 的输出只用于校准技术选型、版本和参数，不再改变 Reading Record / Stable Reading Document / Canonical Text / Event / RAG 的核心合同。

评审重点：

- Product state、run/job state、event/projection state 是否足够分离。
- Candidate Reading Document 是否覆盖 PDF/OCR/网页抽取的 source loss 风险。
- `article_ready` 是否足够轻，不被 RAG、全文增强或 Semantic Outline 阻塞。
- API / BFF contract 是否能支持刷新恢复、渐进渲染和 Library states。
- 旧 workflow 处理策略是否避免把 `analysis_tasks` 和 `render_scene_json` 语义带入新架构。

## D2. 技术 Spikes

状态：active。入口见 `spikes/README.md`；D2-S1 Reading Unit Builder 已完成并以 `accepted_with_changes` 写回模块合同。D2-P0 Plate dependency 已通过并对齐 Web 依赖到 Plate 53.x 稳定主线；D2-P1 到 D2-P4 与 fragment sanitize 的调研结论已由 TMP disposition 汇总，正式合同以本目录模块文档为准。

必做 spike：

| Spike | 输出 |
|---|---|
| Reading Unit Builder | D4 过渡 Stable Base / D6 Canonical Text Layer -> Reading Units 的 deterministic builder、UTF-16/hash 校验、focused tests |
| 依赖基线 | D3-P0 已完成：PydanticAI 1.107.0、DashScope 1.25.23、asyncpg 0.31.0；LangGraph/FastAPI/LangSmith/OpenAI 保持当前锁定版本；现有 focused tests 通过 |
| 当前成本基线 | 代表性 learning 样本的 token、latency、retry 数据 |
| DB job lease 原型 | claim、heartbeat、stale recovery、幂等 resume、cancel/supersede |
| Policy Planner / Skip Gate | deterministic policy table、Decision schema、rationale_code、pre-claim gate |
| Translation Worker structured output | PydanticAI typed output、usage limits、retry policy、provider transport |
| SSE + polling 原型 | event log、Last-Event-ID/cursor 恢复、snapshot fallback |
| Model Profile / Cost Baseline | 当前官方 model id、route lookup、fallback chain、translation benchmark |
| Prompt Cache / Usage Bucket | cache hit/miss audit、usage_by_layer、usage_by_cache_status |
| RAG substrate 原型 | Stable Reading Document / Blocks / Canonical Text / Units -> chunk -> embedding -> Zilliz search -> cited block/unit |
| 阿里云 RAG 可替换性 spike | 验证百炼知识库或阿里云向量检索是否可通过 adapter 替换 Zilliz |
| OSS 上传 spike | Web 直传/后端签名、对象 metadata、checksum、权限、过期清理 |
| OCR / 文档解析 spike | 百炼 OCR/VL/文档解析输出能否稳定形成 Extraction Result 和 Candidate Reading Document |
| Anchor validation 原型 | span-bound layer 发布前必须通过稳定 anchor validation |
| Parsed Decision eval | human/LLM judge rubric 能识别“合理跳过”和“偷懒跳过” |
| Length Class 与 envelope 预算 | 文本长度分类、默认 unit range、token/cost/continuation 策略 |
| D2-P0 Plate dependency / API / license | Plate core、Markdown、comment/suggestion/AI 插件的 license、版本、API 可用性；不可默认依赖未验证商业能力 |
| D2-P1 Base Plate Snapshot | D4 过渡 Stable Base / Units / Anchor Segments -> Base Plate Snapshot，不经过旧 `render_scene_json` |
| D2-P2 Projection Operations / Replay | domain-targeted `projection_ops`、snapshot reload、event replay、gap recovery；不持久化 raw Slate path ops |
| D2-P3 Selection / Anchor / Owner | Plate selection -> domain anchor，UTF-16/hash 校验，owner 权限拦截和后端 policy 对齐 |
| D2-P4 Ask Document Tools | `read_range`、`propose_highlight`、`propose_note`、`write_ai_supplement`、`revise_ai_annotation` 的用户确认和事件投影 |
| 旧依赖矩阵 | 标记旧 analysis/reader scene/Ask/user asset 依赖的 delete / rewrite / keep 策略 |

完成标准：

- 每个 spike 只产出短结果，写回本计划或 PR summary。
- spike 不新增长期设计文档；需要固化的结论写回 `target-architecture.md`。

## D3-P0. Backend Dependency Alignment

状态：completed on 2026-06-18。

- PydanticAI 升级到 `1.107.0`，DashScope SDK 升级到 `1.25.23`，asyncpg 升级到 `0.31.0`；FastAPI、LangSmith、OpenAI SDK 未升级。
- LangGraph 保持 `0.6.11`，D4/D5 主路径不引入 LangGraph；LangGraph v1+ 只作为 D6+ 隔离 spike 候选。
- asyncpg job lease、record-scoped event counter、rollback no-gap、SSE `Last-Event-ID` 等运行时语义延后到 D3-P4 在新 schema 上测试。

## D3. 后端骨架

Schema / Domain Contract：见 `modules/schema-and-domain-contract.md`。D3-P1 到 D3-P4 的四份 TMP 评审和两份 D3 contract review 已合并为该正式合同；实现以正式合同为准，不以 TMP 中的 `ReaderPlateSnapshotV2` 等临时命名为准。

### D3-P1. Schema Baseline

状态：completed on 2026-06-19。

- Fresh baseline migration 新增 Reader 全部核心表（reading_records/bases/units/segments/runs/jobs/events/layers/parsed_decisions）。
- `reader_jobs` 和 `enhancement_layers` 用复合 FK 绑定 base generation；`reader_event_sequences` 使用 record-scoped counter。
- `active_base_id -> reading_bases.status='active'` 作为 service invariant，不做 DDL trigger。

### D3-P2. Reading Base Builder + Base Plate Snapshot

状态：completed on 2026-06-19。

- 实现低影响纯文本路径的 deterministic Reading Base Builder，从 D4 过渡 Stable Base 生成 Reading Units、Anchor Segments 和 Navigation Skeleton。
- `ReaderPlateSnapshot` serializer / Base Plate Snapshot builder 只从 domain facts 生成 Plate `value`，不读取旧 `render_scene_json`，并拒绝不属于当前 base/unit/anchor 的 layers/decisions/supplements/assets。
- 当前 Unit baseline 是 `1 structure block -> 1 reading unit`；target-length aggregation 留给 D5+ builder refinement。

### D3-P3. Article Ready Persistence Service

状态：completed on 2026-06-20。

- 实现纯文本低风险提交的内部 application service，单事务内创建 records/inputs/bases/units/segments，并推进 `readiness_state=article_ready`、`product_state=readable_enhancing`。
- `active_base_id` 设置时显式校验 active base 属于同一 record、同一 generation 且 `status='active'`；`reader_event_sequences` 从 `1` 开始，rollback 不产生 gap。
- Snapshot reload 使用 read-only `repeatable_read` transaction 从 DB facts 重建 `ReaderPlateSnapshot`，保证 `last_event_sequence` 与 domain facts 来自同一 consistent read。

### D3-P4. Runtime Skeleton

状态：completed on 2026-06-20。

- 在新 schema 上实现最小 Reader run/job/event runtime 骨架：claimable `reader_jobs`（`SELECT FOR UPDATE SKIP LOCKED` + lease token/expiry/heartbeat）、stale recovery、`retry_later` 调度。
- base/generation fence：claim/publish 拒绝 stale generation、非 active base、`active_base_id != job.base_id` 或 lease token mismatch。
- event publisher helper 在 publish transaction 内分配 committed UI sequence；polling event read model 支持 `after_sequence`/`limit`/`last_event_sequence`/truncated/gap-reload 语义。
- 保持不引入 LangGraph；runtime 主控仍是 PostgreSQL run/job/event。

### D4-P0. Backend Reader API + Snapshot/Polling Vertical Slice

状态：completed on 2026-06-20。

- 新增最小后端 Reader API surface：`POST /reader/records/plain-text`、`GET /reader/records/{record_id}/snapshot`、`GET /reader/records/{record_id}/events`，分别接入 article-ready service、D3-P3 snapshot reload 和 D3-P4 polling event read model。
- 用户隔离复用 `AuthUserDep`，跨用户 record 返回 404；`client_record_id` 重复返回 409。
- 新 API 路径不读取旧 `render_scene_json`；polling 先行，SSE 纵切后置。

### D4-P1. Translation Layer Worker + Layer Publish Vertical Slice

状态：completed on 2026-06-21。

- 新增 deterministic translation run/job bootstrap 和 base-scoped `reader_jobs`；`claim_next_job()` 支持 `job_type`/`target_type` 过滤，避免 worker 跨队列 claim。
- Layer publisher 单事务内写 `enhancement_layers(layer_type='translation')`、发布 `layer_published` event、完成 job/run transition；snapshot reload 投影 `reader_translation` node。
- 成功和失败路径均写 `ai_usage_events` 带 record/run/job/layer attribution；retryable failure 后重新成功会清空 run 旧失败状态。

### D4-P2. Backend Orchestration Integration + Parsed Decision

状态：completed on 2026-06-21。

- 新增 `ReaderOrchestrator` service 作为 D4 后端最小 orchestration facade；`POST /reader/records/plain-text` 先创建 article-ready facts，再启动 translation run/job。
- Translation layer published 后写最小 `parsed_decisions` 并发布 `parsed_decision_updated` event；snapshot reload 和 event polling 顺序覆盖两者。
- Parsed decision 写入与 layer publish 暂不同事务（D4 单线程 tick 可接受）；未新增 HTTP tick endpoint，未引入 LangGraph。

### D4-P3. Web Reader Plate Read-only Surface + BFF Polling Slice

状态：completed on 2026-06-21。

- 新增 Web BFF routes（submit/snapshot/events）和只读 `ReaderPlateSnapshotSurface` + `/app/reader-plate` 提交入口；BFF 复用 Web session token，拒绝 anonymous/mock phone。
- Web polling 在 `layer_published`、`projection_reset_required` 或 server reload signal 时触发 snapshot reload；D4 不应用 `projection_ops`。
- 新 Web 路径不读取旧 `/scene` 或 `render_scene_json`；页面文案不暴露 D4/Plate.js/Snapshot/cursor/sequence 等实现术语。

### D4-P4. Worker Runner Hardening + Web Smoke/Test Gap Closeout

状态：completed on 2026-06-21。

- 新增 `TranslationWorkerRunner` 作为 D4 内部 callable runner（single tick + bounded drain），不新增 public HTTP endpoint、不启动后台进程、不引入 LangGraph/MQ/Temporal/SSE。
- Worker result 分类为 `no_job`/`succeeded`/`retry_later`/`failed_terminal`/`fence_rejected`；drain 遇到非成功不立即停止，由 caller 决定是否继续。
- 新增 orphan diagnostic（published translation layer 缺失 `parsed_decisions`）；Web 侧补齐 BFF auth/error tests 和 reader-plate Playwright smoke（mocked BFF）。

## D4. 最小纵切

流程：

1. Web 提交文本。
2. 后端创建 Original Input 和 Reading Record。
3. 低影响 base path 创建 D4 过渡 Stable Reading Base（D6 口径下为 Canonical Text Layer）。
4. 创建 Reading Units、Anchor Segments 和 Navigation Skeleton。
5. 创建 Base Plate Snapshot。
6. Reader 到达 `article_ready`。
7. Translation layer 为第一个/当前 units 发布。
8. 记录 Parsed Decision。
9. Web Plate Reader Surface 渲染稳定文章 + 渐进译文。

完成标准：

- 用户可在 full coverage 前开始阅读。
- 刷新/恢复后状态正确。
- Annotation layer 不修改 Stable Reading Document / Canonical Text source text；只通过 projection 呈现在 Plate Article Body。
- LLM 调用有 usage event。
- 旧 `render_scene_json` contract 不参与新 Web Reader 路径。
- RAG substrate 可以在后台构建，不阻塞阅读。
- **Plate.js 承接 Article Body**：Web 直接加载 Base Plate Snapshot；readOnly 起步，具备 selection bridge。
- **D4 不要求 projection_ops 端到端**：translation layer 可先通过 snapshot reload 或 simple projection refresh 呈现；D5 才接增量 `projection_ops`。

明确不包含：

- URL / PDF / OCR / 文件上传实现。
- Candidate Document preview/edit/confirm UI。
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

状态：completed on 2026-06-21。

- 新增 `VocabularyLayerOutput` typed schema，三类 item subtype（`vocab_highlight`/`phrase_gloss`/`context_gloss`）同属 `layer_type='vocabulary'`；anchor 以 `anchor_segment_id`+UTF-16 range+`selected_text`+FNV hash 为权威，`sentence_id` 仅作兼容 alias。
- DB baseline 正式支持 `reader_jobs.job_type='build_vocabulary_layer'`，不再挪用 `build_base`；worker 默认未配置 executor 走 `failed_terminal`，只有显式 fake executor 才允许发布空 items。
- Publisher 事务内校验 unit/anchor/UTF-16/text/hash 后写 layer 与 `layer_published` event；snapshot reload 暂只暴露 top-level metadata，不投影 Plate marks。

### D5-V2. Vocabulary Projection / Web Read-only Rendering

状态：completed on 2026-06-21。

- `VocabularyLayerOutput` 仍是 domain truth；Plate marks 是 snapshot projection，不是持久事实。Snapshot reload 按 base/unit/anchor 重新校验后投影为 stable source leaf 上的 `reader_vocabulary_marks`。
- Vocabulary mark 使用 `anchor_segment_id` + unit-local UTF-16 offsets；serializer 派生 leaf 内 segment offsets 和 `starts_here`/`ends_here`。
- Review 修正：vocabulary snapshot layer 必须 `target_scope='unit'`，不能投到 `anchor_segment` scope。未读旧 `render_scene_json`，未启用 `projection_ops`。

### D5-V3. Real Vocabulary Executor / Prompt

状态：completed on 2026-06-21。

- 新增 `reader_layer_vocabulary` model route + `reader_vocabulary_model_profile` 配置；必须显式配置才注册，不得 fallback 到 annotation profile。
- `PydanticAIVocabularyExecutor` 让 LLM 输出内部候选 schema，deterministic postprocess 在目标 Anchor Segment 内 exact-match 后生成 unit-local UTF-16 offsets + FNV hash；找不到文本/重复命中/结构化无效时 fail closed 或跳过，原因写入 `quality_json.diagnostics`。
- 同一 span 冲突按 `context_gloss > phrase_gloss > vocab_highlight` 保留；candidate 有硬上限和字段长度限制；空 items 或全部跳过时允许发布空 output 标记 unit 已处理。

### D5-V4. Grammar Bundle Backend Slice

状态：completed on 2026-06-21。

- 新增 `reader_jobs.job_type='build_grammar_bundle'`（`target_type='unit'`、`operation_fingerprint='grammar_bundle_unit_v1'`）和 grammar typed schema（`GrammarNoteItem`/`SentenceAnalysisChunk`/`SentenceAnalysisItem` 等）。
- `GrammarBundleLayerPublisher` 将一次 bundle 拆成 `grammar_note_unit_v1` 与 `sentence_analysis_unit_v1` 两个独立 layer rows；empty sanitized output 走 no-op success（不插 layer、不发布 event、`output_ref_json.no_op=true`）。
- Usage attribution 用单条 job-level `ai_usage_events` 避免双 layer 重复计费；fallback_window 命中时 sentence_analysis 跳过、grammar_note 整条 item 跳过；snapshot reload 只暴露 top-level metadata。

### D5-E1. Vocabulary Eval Seed Disposition

状态：accepted_with_changes on 2026-06-21。

- 评估方向接受：优先建立 vocabulary deterministic eval seed，覆盖 anchor resolution、bounds compliance、diagnostics coverage、same-span arbitration 和 item quality；用本地 deterministic graders + pytest 验收，LangSmith `evaluate()` 不进入下一步。
- 实现必须匹配现有 `evals` harness 的 `dataset.yaml + cases/*.json` 目录形态，不采纳单文件 `vocabulary_seed_v1.jsonl`；LLM judge 泛化单独后置。
- vocabulary `boundary_low_fallback_window` 在 D5-G2 后已成为 acceptance gate，与 grammar bundle 口径一致。

### D5-R1. LangGraph / Orchestration Architecture Review Disposition

状态：`accepted_with_changes` on 2026-06-22。

- 接受当前三层架构（PostgreSQL durable control plane + PydanticAI typed worker + Plate snapshot projection）；D5 主链路不引入、不升级 LangGraph，LangGraph 不得替换 reader_runs/jobs/events/layers 或 product state。
- D6-LG0 仅作为隔离 spike 候选，触发条件必须是具体 Ask/human approval/multi-branch repair 需求；D5 主链路继续用 snapshot reload，`projection_ops` 不阻塞 smoke。
- 风险排序：`parsed_decisions` 跨事务和 vocabulary boundary policy 是 P1；`active_base_id -> status='active'` 是 invariant hardening 非 P0；`projection_ops` race 只在启用 incremental applier 前需 spike。

### D5-R2. Main Chain Runner + Web Record Load Closeout

状态：completed on 2026-06-22。

- 新增 `ReaderEnhancementPipelineRunner` 统一 bootstrap/drain translation/vocabulary/grammar_bundle jobs，复用现有 services/workers/publisher，不另建控制面；drain 顺序 translation -> vocabulary -> grammar bundle，遇 `retry_later`/`failed_terminal`/fence supersede 返回 attention summary。
- `claim_next_job()` 支持可选 `reading_record_id`/`base_id`/`expected_generation` scope，runner 不会消费其他 record 的 queued jobs；不新增 public endpoint、不启动 daemon、不引入 LangGraph/MQ/Temporal/SSE、不启用 `projection_ops`。
- 新增本地 D5 dev smoke harness/CLI（fake executors 默认禁用、生产禁用）；Web `/app/reader-plate` 支持 `record_id` query 直达加载已有 snapshot，不回退旧 `/scene`。

### D5-W1. Local / Deployment Worker Loop Evaluation

状态：`accepted_with_changes` on 2026-06-22。

- 接受独立 worker process 方向：本地 CLI entrypoint，部署时独立 worker service/container；复用 `ReaderEnhancementPipelineRunner`，不挂到 FastAPI lifespan，不新增 worker-control endpoint。
- Web submit 仍只创建 `article_ready` facts，不同步塞 runner；fake executors 不进产品路径，真实 model profile 缺失保持 fail-closed。
- 扫描条件默认考虑 `article_ready` 与 `initial_enhancement_ready`，`coverage_complete` 为默认停止态；粗筛只找候选 record，exact missing work 交由 bootstrap/runner 决定；per-record advisory lock 必须有，per-user/per-worker concurrency 默认 `1`；`retry_later` 尊重 `available_at` 避免 hot-loop，`failed_terminal` 只进 logs/metrics/summary。

### D5-W2. Local / Deployment Worker Loop Closeout

状态：completed on 2026-06-22。

- 新增 `ReaderEnhancementWorkerLoopService`（coarse eligibility scan + per-record/per-user advisory locks 调度 runner）和 `scripts/run_reader_enhancement_worker.py`（`--once` + loop mode，本地/部署共用入口）。
- scanner 只筛 coarse readiness，不复制 missing-work 判定；优先处理有 runnable jobs 的 record，无 runnable jobs 时仅对无 tracked jobs 的 record 允许重新 bootstrap，避免 `retry_later` hot-loop 和 `failed_terminal` 反复重建。
- 新增 worker settings；本地真实链路 runbook 落到 `modules/local-real-chain-runbook.md`；未新增 public endpoint、未挂 FastAPI lifespan、未引入 LangGraph/MQ/SSE/`projection_ops`。

### D5-G1/G2. Runtime Guardrails Closeout

状态：completed on 2026-06-22。

- D5-G1 把 translation layer publish 与最小 `parsed_decisions` 写入收敛到同一 publisher transaction，消除 crash gap；`diagnose_orphaned_translation_decisions()` 保留为 diagnostic，snapshot reload 不做隐式 repair。
- D5-G2 统一 vocabulary 与 grammar bundle 的 fallback_window boundary policy：`segment_type=fallback_window` 的 anchor segment 不产出 vocabulary/grammar item；vocabulary skip reason_code 写入 diagnostics，空有效 output 仍可发布标记 unit 已处理。
- Vocabulary eval seed 已新增 fallback-window skip fixture 并更新 baseline。

### D5-R4. Real Provider Local Chain Validation

状态：completed on 2026-06-22。

- 真实 DashScope `workflow-qwen37-max` provider 下短文本主链路端到端跑通（`plain_text -> article_ready -> worker loop -> snapshot reload`），未使用 smoke harness 或 fake executor；events 推进到 `article_ready + layer_published x3 + parsed_decision_updated`。
- Snapshot projection 出现 `reader_translation`/`reader_vocabulary_marks`/`reader_grammar_note_marks`；`sentence_analysis` 未出现是短文本真实 LLM 行为，非 bug；第二次 `--once` 扫描为空证明不重复 publish。
- Follow-up：`ai_usage_events`/`user_credit_ledger` 列缺失按本地 DB schema drift 处理（D5-R5）；长文本 lease duration 单独评估；`sentence_analysis` 验收需长文本 fixture。

### D5-R5. Schema Health Check + Worker Lease Duration Setting

状态：completed on 2026-06-22。

- 新增 dev/admin schema health entrypoint `scripts/check_reader_schema_health.py`，显式检查 D5 attribution columns/FK/index 并在失败时输出 reset/rebuild 指引；`infra/scripts/check_schema_baseline.sql` 同步扩展 drift 检查。
- worker loop 新增 `reader_worker_lease_duration_seconds` setting 和 CLI `--lease-duration-seconds`，默认从 30s 提高到 120s，优先降低长文本 `LeaseExpiredError`，代价是 stale lease 恢复最慢约 2 分钟。
- `process_candidate()`/`run_once()` 只透传 `lease_duration`，不改变 claim/retry/publish fence 语义；未新增 public endpoint、未挂 FastAPI lifespan、未引入 fake executor 产品路径。

### D5-V5 / D5-R6. Sentence Analysis Long-text Validation + Projection Consistency

状态：completed on 2026-06-22。

- 新增 250+ 词英文长文本 deterministic fixture；`ReaderEnhancementPipelineRunner` 可完成 `article_ready -> bootstrap -> worker drain -> snapshot reload` 并发布 `grammar_note` + `sentence_analysis` 两类 layer；reload 不写 projection side effects。
- 真实 DashScope provider 下长文本链路通过 Web BFF submit、worker once 和浏览器实渲染验证；snapshot 同时包含 translation/vocabulary/grammar_note/sentence_analysis，`snapshot.value` 出现 2 个 `reader_sentence_analysis` nodes。
- 后续问题：worker stdout 仍有 PydanticAI deprecation warnings；250+ 词单段正文仍只生成 1 个 `reader_unit`（`boundary_quality=low`），需 Boundary/Unit Builder v2 与 sentence_analysis coverage policy 独立评估；D5 不改过渡 Stable Base contract，D6+ 再把 richer structure retention 前移到 Input Adapter / Candidate Document / Stable Document Blocks。

## D6. 产品硬化

任务包：

- 高影响适配的 Candidate Reading Document preview/edit/confirm。
- Library states：processing、readable/enhancing、paused、needs_confirmation、failed、quota_required。
- continuation、quota、retry、re-parse-as-new-record 的 action-required UX。
- Ask sidecar action envelope：continue enhancement、save note、context expansion。
- cost / credit decision surfaces。
- 失败恢复和 support/debug details。
- Stable Document Blocks / Canonical Text Layer contract：Reader 内容区是文档型 Plate projection，但后端 truth 不能是 Plate JSON。
- V1 输入方式：粘贴文本、公开网页 URL、PDF 页码范围、多图 OCR；`.txt` / `.md` 作为上传文档低风险子类型。
- Input Suitability Gate：所有输入先判断是否足够支撑 Claread 英语阅读解读，以及格式处理是否会改变关键含义。
- PDF/OCR 输入：PDF text layer parser 优先，页级 quality gate 后可进入 LLM reviewer；OCR 通过 provider adapter 与 model route/profile 配置，不写死领域合同。
- RAG substrate：必须在 Stable Document Block contract 之后实现，覆盖 table/image OCR/footnote/code 等 source scopes。

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

### D5 已全部完成

D5 增强扩展（vocabulary / grammar bundle / worker loop / runtime guardrails / 真实链路验证）已全部完成。详细子阶段状态见上方 D5 子阶段索引表。

### D6 已完成项

- Ask / notes / highlights 依赖审计与边界声明（D6-A0）
- Reading Record anchor gate + single-range persistence（D6-U4：migration + runtime 写入，不启用 UI 写入口）
- user_assets read projection（D6-U5）
- Web cutover：`/app/read` 默认提交到新 Reading Record，`/app/reader-record/{recordId}` 默认 Plate surface，Library/command palette/activity indicator 新增新 Reading Record 发现入口（W3-D0~D9）
- ReaderRecordPlateSurface 真实流程验收与 UI polish（UI-D4~D6C）
- failed_terminal 保守映射为 `failed` product state
- Input / document model 产品决策：旧 Candidate Base 升级为 Candidate Document；旧 Stable Reading Base 语义拆为 Stable Reading Document、Stable Document Blocks 和 Canonical Text Layer；V1 输入范围、PDF/OCR quality gate、OCR provider adapter、RAG source scope 已确认。

### D6 下一步（按优先级）

1. **先补 Stable Document Block contract**：定义 Stable Reading Document / Stable Document Blocks / Canonical Text Layer 的 schema、DTO、snapshot 投影边界和 block identity。RAG 与高级输入都依赖这一步。
2. **打通 Candidate Document 纵切**：实现 Candidate Document persistence、preview/confirm API、`needs_confirmation` product state、确认后冻结 Stable Reading Document 并复用现有 article_ready / worker chain。
3. **上线输入适配最小路径**：先做 Markdown/txt parser + Input Suitability Gate + Source Artifact adapter，再做 PDF parser/page quality gate，最后接 OCR provider adapter 与多图合并。
4. **实现 block-scoped RAG**：在 Stable Document Blocks 已落地后，做 RAG substrate schema、chunker、citation validator、VectorStoreAdapter 和 indexing worker。不要先做只绑定线性文本的 RAG。
5. **收敛新旧双轨**：逐个把 Library / Vocabulary source links / active task / command palette legacy records 切到新 Reading Record（前提：BFF 提供 `sourceReadingRecordId`）；评估删除 `ReaderRecordWorkbenchSurface` fallback 和 legacy `ReaderWorkbench` 的时点。
6. **补齐 Plate surface 功能缺口**：Ask 切线（依赖 `reader_ask/service.py` 拆分或 adapter 化）、词典 AI、词汇保存、阅读模式切换、`projection_ops` 增量 applier。
7. **架构深化（可选）**：提取 `BaseEnhancementWorker` 消除 worker/publisher 三重复制；拆分 `repository.py`（1471 行）和 `reader_ask/service.py`（222KB 巨石）。
8. **清理 TMP 文档**：`docs/tmp/reader-orchestration/` 下 ~60 份 TMP closeout 的结论已回写本计划，按 AGENTS.md 规则删除或归档。

### 仍保持的口径

- LangGraph 只作为 D6+ 隔离 spike 候选，不进入主路径。
- 不做旧 `render_scene_json` 兼容映射。
- PydanticAI deprecation warnings 剩余 `app/agents` legacy/daily agents、`eval_adapter/*`、`services/daily_reader/scoring.py` 后续分域清理。
- Boundary / Unit Builder v2 维持 D5 过渡口径：text-only Canonical Text Layer + deterministic baseline，不在 Stable Document Block contract 落地前做生产 split。
