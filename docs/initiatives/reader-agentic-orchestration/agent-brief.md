# Reader Agentic Orchestration 执行简报

> 状态：`权威简报`
> 最后更新：2026-06-21

给 coding agent 分配 Reader agentic orchestration 重构任务时，使用本简报作为最小上下文。

## 必读顺序

1. `AGENTS.md`
2. `RTK.md`
3. `docs/initiatives/reader-agentic-orchestration/README.md`
4. `docs/initiatives/reader-agentic-orchestration/target-architecture.md`
5. `docs/initiatives/reader-agentic-orchestration/concepts.md`
6. 当前任务涉及的 `docs/initiatives/reader-agentic-orchestration/modules/*.md`
7. D2 spike 任务读取 `docs/initiatives/reader-agentic-orchestration/spikes/README.md`
8. `docs/initiatives/reader-agentic-orchestration/implementation-plan.md`
9. 涉及代码目录最近的 `AGENTS.md`

除非任务明确要求研究回溯，不要读取 `docs/tmp/reader-orchestration/` 下的全部文件。

## 任务目标

把用户提交内容的 `learning` 解析，从固定 AI Workflow 重构为 bounded agentic Reader orchestration。

产品对象是 `Reading Record`，不是 workflow run。

## 不可违反的决策

- Web 优先，小程序实现暂缓。
- Academic workflow 暂缓重构；待 learning workflow 验证稳定后再单独设计。
- 不做旧开发数据迁移，本地数据可清空，但必须保留词典三表。
- 保护 `dict_entries`、`dict_lookup_targets`、`dict_redirects`。
- 不做旧 `render_scene_json` 兼容映射；Web Reader UI 跟随新 contract 改写。
- Daily Reader runtime 不进入本轮重构。
- Reader 页面不是常驻 LLM 线程。
- PostgreSQL 拥有 durable business state。
- LangGraph / PydanticAI 是执行层，不是产品事实源。
- Stable Reading Base 和 Reading Units 在同一 Reading Record 内不可变。
- 高影响输入适配必须先进行 Candidate Reading Base 预览与确认。
- 译文是 parsed 的最低门槛。
- 禁止用固定批注数量判断 parsed。
- Ask Claread 是侧边助手；侧边动作必须走同一 Authorization Envelope。
- RAG 只服务当前 Reading Record，不做全局 User Editorial Assets RAG。
- RAG/OCR/OSS 等外部服务必须 adapter 化，不能成为 Claread 业务事实源。
- D4 最小纵切只做纯文本低风险路径和 translation layer。
- D4 不使用 LLM Planner；Policy Planner 是 deterministic code。
- D4 不使用 LangGraph Planner；LangGraph 不进入 D4 主路径。
- D4 必须包含最小 `reader_runs` 和 immutable `envelope_json` snapshot；完整 envelope counters 可后置。
- Semantic Reviewer 是 D5+ 的 typed LLM worker，不是默认 planner。
- 模型选择走 Model Profile / route lookup，不由 planner 即兴决定。
- `operation_fingerprint` 表示 business intent，不包含临时 fallback actual provider/model。
- `reader_events.sequence` 必须是 per-record committed UI event sequence，从 `1` 开始；不能用 PostgreSQL global sequence 作为 UI catch-up sequence。
- D4 snapshot 默认实时聚合；`reader_snapshots` cache、PG LISTEN/NOTIFY 和 event TTL 是 D5+ 优化。
- D3 Schema / Domain Contract 的正式入口是 `modules/schema-and-domain-contract.md`；实现不以 TMP 报告中的临时 type 名或字段建议为准。
- 开发期核心类型和 DTO 不加 `V1` / `V2` 后缀。使用 `ReaderPlateSnapshot`，不创建 `ReaderPlateSnapshotV1` / `ReaderPlateSnapshotV2`。
- `ReaderPlateSnapshot` wrapper 使用 `schema_kind = "reader_plate_snapshot"`；`schema_version` 只用于 layer output、fragment 等 serialized boundary payload。
- D4 snapshot 恢复 cursor 只使用 `last_event_sequence`，不暴露或依赖 snapshot-level `projection_version`。
- `reader_jobs` 中 base-scoped jobs 必须携带 `base_id`；active job fingerprint 必须包含 `base_id + expected_generation + operation_fingerprint`。
- job 可重试调度状态统一为 `retry_later`。`failed_retryable` 不作为可 claim 的长期 job status。
- D3-P0 已完成后端依赖对齐：PydanticAI 1.107.0、DashScope 1.25.23、asyncpg 0.31.0；LangGraph/FastAPI/LangSmith/OpenAI 保持当前锁定版本。
- D3-P1 schema baseline 已完成并通过 review。实现已新增 D3-P1 最小 Reader tables、usage/ledger attribution、record-scoped event counter 和 focused tests。
- `reader_jobs` 必须用 base/generation fence 防止 stale worker：base-scoped jobs 绑定 `(base_id, reading_record_id, expected_generation)`；只有 `build_base + record` job 可无 `base_id`。
- `enhancement_layers.generation` 必须匹配 target base `record_generation`。
- `active_base_id -> reading_bases.status='active'` 当前是 service / publisher invariant，不是 D3-P1 trigger。设置 active base、supersede base、publish job/layer 时必须显式校验。
- D3-P2 Reading Base Builder + Base Plate Snapshot 已完成并通过 review。后续任务必须复用 `services/api/app/services/reader_orchestration/base_builder.py` 和 `snapshot.py`，不得另起一套 Unit/Anchor/Snapshot 逻辑。
- D3-P2 当前 Unit baseline 是 `1 structure block -> 1 reading unit`；不要在 D3-P3 临时加入 LLM semantic unit split 或 target-length aggregation。
- Snapshot builder 必须拒绝不属于当前 base / unit / anchor 的 layers、parsed decisions、ask supplements 和 user assets；不能把 wrong-base facts 混入当前 Reader snapshot。
- Published translation layer 的 D4 最小 snapshot projection 已可用；非 translation 的 `unit_range` / `record` 复杂 membership 校验留给 D5 Layer Publisher。
- D3-P3 Article Ready Persistence Service 已完成并通过 review。后续低风险纯文本 `article_ready` 内部路径应复用 `ArticleReadyPersistenceService`，不得另写一套 record/input/base/unit/anchor/event 持久化逻辑。
- Snapshot reload 必须从 DB domain facts 重建，使用 read-only `repeatable_read` transaction 或等价 consistent read；`last_event_sequence` 与 snapshot facts 必须来自同一一致性视图。
- DB hydration 后必须调用 `validate_reading_base_build_result` 校验 Reading Base / Unit / Anchor Segment 全局 invariant。后续新增 persisted facts 时也要接入同一校验链。
- D3-P4 Runtime Skeleton 已完成并通过 review。后续 job runtime 应复用 `ReaderJobRuntime`，event publish / polling 应复用 `ReaderEventRuntime`，不得另写 sequence/cursor/lease 控制面。
- Job claim/publish fence 必须同时校验 record generation、target base generation、target base `status='active'`、record `active_base_id == job.base_id` 和 lease token。
- Polling cursor 在 `after_sequence == last_event_sequence` 或 empty stream 时返回空 events，不要求 reload；只有发现 missing committed event / sequence gap 时才要求 reload。
- D4-P0 Backend Reader API + Snapshot/Polling 纵切已完成并通过 review。新 API surface 是 `POST /reader/records/plain-text`、`GET /reader/records/{record_id}/snapshot`、`GET /reader/records/{record_id}/events`；不得让新 Web Reader 回到旧 `/scene` 或 `render_scene_json` 路径。
- D4-P0 `client_record_id` blank 规范化为 `NULL`；同一用户重复 active `client_record_id` 返回 409。后续如果改为幂等 submit，必须显式更新 API contract 和测试。
- D4-P1 Translation Layer Worker + Layer Publish 纵切已完成并通过 review。Translation worker 必须使用 job-type filtered claim，不得 claim mixed queue 中的非 translation jobs；成功和失败路径必须写 `ai_usage_events` attribution；retry 后成功必须清空 run failure fields。
- D4-P2 Backend Orchestration Integration + Parsed Decision 已完成并通过 review。`ReaderOrchestrator` 是 D4 后端最小 facade：submit path 创建 article-ready facts 并 bootstrap translation job，tick path 处理 translation job、发布 layer、写最小 parsed decision、发布 `parsed_decision_updated` event。
- D4-P2 tick 目前是 service/testable entry，不是公开 HTTP endpoint。若后续需要 API 驱动 tick，必须补 route、auth、worker 权限和 focused tests。
- D4-P3 Web Reader Plate Read-only Surface + BFF Polling 已完成并通过 review。Web D4 入口走真实 submit/snapshot/events，不走 demo record、旧 `/scene` 或 `render_scene_json`；polling 收到 layer/projection reset/reload signal 后 reload snapshot，不应用 `projection_ops`。
- D4-P4 Worker Runner Hardening + Web Smoke/Test Gap 已完成并通过 review。`TranslationWorkerRunner` 是内部 callable runner，不是 public HTTP endpoint；Web reader-plate smoke 使用 mocked BFF routes，只证明浏览器渲染/交互，不等价于真实 auth/backend E2E。
- D5-V1 Vocabulary Layer Backend Slice 已完成并通过 review。Vocabulary 使用正式 `reader_jobs.job_type = 'build_vocabulary_layer'`，不是 `build_base`；worker 默认未配置时必须失败且不发布空 layer，只有显式 fake executor 才能发布空 output。
- D5-V2 Vocabulary Projection / Web Read-only Rendering 已完成并通过 review。Published vocabulary layer 在 snapshot reload 时从 domain facts 重建为 stable source leaf 上的 `reader_vocabulary_marks`，Web 只读展示 `vocab_highlight`、`phrase_gloss`、`context_gloss`；不读取旧 `render_scene_json`，不持久化 Plate path/op，不启用 `projection_ops` incremental applier。
- 当前下一步进入 D5-V3 选择点：优先实现 real PydanticAI vocabulary executor / prompt / eval sample，或先做 Grammar Bundle Worker schema+publisher 纵切。二者都不得改写 D5-V2 的 snapshot truth/projection 边界。
- D4 worker 实现中不得临时升级 PydanticAI、LangGraph、LangSmith 或 provider SDK；如 D3-P4 runtime tests 暴露缺口，先形成单独 closeout/update，再改依赖。
- LangGraph 1.x 的 typed streaming、per-node timeout、error handler、graceful shutdown 和 DeltaChannel 只作为 D5+ 复杂 repair / branching / interrupt spike 候选，不改变 D4 PostgreSQL run/job/event 主控。
- Grammar Bundle Worker 可以一次生成 `grammar_note` 与 `sentence_analysis`，但发布、存储、RAG、projection、policy、eval 必须按 subtype 独立处理。`long_sentence` 不是权威 layer type，只是触发 `sentence_analysis` 的适用场景。
- Vocabulary Worker 必须保留旧 workflow 的三类 item subtype：`vocab_highlight`、`phrase_gloss`、`context_gloss`。它们属于同一个 `vocabulary` layer 的 `output_json.items[].item_type`，不是三个顶层 layer type。

## 渲染层与 Plate 不可违反规则（D1-012 ~ D1-017）

- Reader Article Body 渲染层与交互引擎走 Plate.js（`platejs/react`），不是其他编辑器。
- `apps/web/src/lib/reader-plate*`、`apps/web/src/components/reader/plate/` 和相关 BFF/API client 是 Claread 对 Plate.js projection 的领域封装；实现必须显式基于 Plate.js（`platejs/react`），不能回到自建固定 UI scene。
- **Plate document 不是 truth**，是 domain fact（Stable Reading Base / Reading Units / Anchor Segments / Enhancement Layers / User Editorial Assets / Ask Supplements）的 projection。`enhancement_layers` / `user_annotations` / `reader_notes` 等表结构**不改为 patch sequence**。
- `reader_events.event_type` 必须支持 `projection_ops` 子类型。Projection op payload 使用稳定 domain target；不得把 raw Plate path / raw Slate path ops 作为后端持久合同。
- D4 不要求 `projection_ops` 端到端；translation layer 可以先通过 snapshot reload 或 simple projection refresh 呈现，D5 再接增量 applier。
- 禁止使用 `plate_patches` 作为正式事件名或合同名；它只能作为已拒绝的 TMP 旧口径出现。
- 刷新恢复**从 domain truth 重建 Plate Value**，不是从 Plate value 反推 domain。
- D4 正式路径从 Stable Base / Reading Units / Anchor Segments 直接生成 Base Plate Snapshot；旧 `renderSceneToPlateDocument` 只能作为参考或 spike adapter，不是新 contract。
- `anchor_segment_id` 是新权威锚点；`sentence_id` 只作为兼容 alias。新 target、projection op、Ask tool、RAG citation、User Editorial Asset 不得只依赖 `sentence_id`。
- owner 权限层必须覆盖：`stable` / `system_ai` / `ask_supplement` / `user` / `ephemeral`。owner 校验双层：后端权威拒绝 + 前端 Plate UX 镜像。
- 所有 domain 回写（user_highlight / reader_note / ai_supplement）必须经过 anchor/path adapter 输出 domain anchor，**不直接走 node path**。
- Ask Sidecar 在 D5+ 改 document tools 模式，主路径工具集：`read_range` / `propose_highlight` / `propose_note` / `write_ai_supplement` / `revise_ai_annotation`。写 User Editorial Assets 必须用户确认。
- Ask 不能直接覆盖 System Annotation Layer truth；系统层修订走 proposal 或 Layer Publisher/system worker。
- LLM 不能直出 arbitrary Plate JSON 或 raw Slate ops 作为持久事实。AI / Markdown fragment 必须经过 typed schema、strict allowlist、length cap、source grounding 和 link protocol policy。
- D5 默认禁用 image / table / inline HTML / math / frontmatter / definition / footnote；启用前必须另做 spike。
- 非 Web 客户端继续 polling snapshot，不订阅 Plate projection ops。

## 当前外部服务假设

- RAG 测试阶段优先使用 Zilliz Cloud。
- 上线前评估迁移到阿里云 RAG / 向量检索服务或百炼知识库。
- OCR / 富文档解析优先评估阿里云百炼图像理解、Qwen OCR/VL、文档解析能力。
- 文件上传测试阶段使用阿里云 OSS；上线目标为 OSS + CDN。

## 预期架构形态

```text
Web Reader
  -> Reader API / BFF
  -> PostgreSQL Reading Record + run/job state + event log
  -> worker abstraction
  -> typed execution units
  -> PydanticAI LLM-backed workers
  -> optional LangGraph local flow in D5+ only after separate dependency spike
  -> LangSmith + ai_usage_events
```

模块边界：

- Input Adapter：统一接收文本、URL、PDF、OCR、文件上传，产出 Original Input / Source Artifact / Extraction Result。
- Reading Base Builder：生成 Candidate 或 Stable Reading Base，并冻结 Reading Units / Anchor Segments / Navigation Skeleton。
- Orchestration Planner：基于持久状态和 Authorization Envelope 规划下一批 bounded jobs。
- Guarded Executor：claim jobs、heartbeat、retry、cancel/supersede、usage audit。
- Layer Workers / Publisher：生成并校验增强层和系统 AI 批注层，发布前做 schema、anchor、source grounding。
- Event / Projection：持久 reader events、snapshot、SSE、polling fallback。
- Plate Reader Projection：从 Stable Base / Units / Anchor Segments 和 layers 生成 Base Plate Snapshot 与 domain-targeted projection ops。
- RAG Substrate：只服务当前 Reading Record，查询强制限定 Stable Base / Units。
- Ask Sidecar Bridge：Ask 动作进入同一 Authorization Envelope；保存 note/highlight 必须用户确认后写 User Editorial Assets，Ask Supplement 必须标记来源。
- Policy / Cost Control：Skip Gate、Prompt Cache、Model Profile、Usage Bucket、cost baseline。

D4 默认实现边界：

- Planner 先用 deterministic policy function。
- PydanticAI 用于 LLM-backed workers。
- LangGraph 不进入 D4 主路径。
- 不继承旧“每用户一个 active task”产品约束；并发由 envelope 控制。
- Text anchors 复用现有 UTF-16 offsets 和 `fnv1a32-utf16` hash contract；span anchor 使用 `anchor_segment_id` + unit-local offsets，且 offset 必须落在对应 Anchor Segment range 内。Segment-local offsets 只作为 Plate leaf projection metadata 派生。
- Stable Reading Base 是输入适配和必要用户确认后的可读英文正文；Unit Builder 不负责 OCR 修复、boilerplate 删除、多栏顺序修复或正文重写。
- Anchor Segment 是 sentence-like segment，通常是句子；必要时可为 clause 或 fallback window，并通过 `segment_type` 标记。
- D4 不启用 LLM Unit Builder；D5+ Unit Boundary Refiner 只能建议既有 Anchor Segments 的 split/merge，不能改写文本或生成坐标。
- D4 Web Article Body 加载 Base Plate Snapshot，不经过旧 `render_scene_json`。
- Translation worker 不携带 Ask history、planner trace 或整篇文章上下文。
- System Annotation Layers 不得写入或覆盖 User Editorial Assets。
- Usage audit 必须能按 record、job、layer、model profile、cache status 归因。
- D5 grammar 初版可以保留一个 `reader_layer_grammar_bundle` route；后续如成本或质量目标分化，再拆 `grammar_note` / `sentence_analysis` worker，但不改变 layer subtype 合同。

输入链路：

```text
Input Adapter
  -> Original Input
  -> Source Artifact / Extraction Result
  -> low-impact Stable Base 或 high-impact Candidate Base
  -> Stable Reading Base
  -> Reading Units + Anchor Segments
```

RAG 链路：

```text
Stable Reading Base / Reading Units / Anchor Segments
  -> RAG chunks
  -> embeddings
  -> VectorStoreAdapter / KnowledgeRetrievalAdapter
  -> cited retrieval results
```

状态边界：

- Product state 表达 Library / Reader 可见状态。
- Run/job state 表达 worker 执行状态。
- Reader events / snapshot 表达前端 streaming 与刷新恢复。
- 不要用一个 task status 替代这三层。

## 编码规则

- 只修改当前任务范围内的文件。
- 不自行新增架构文档。
- 不做小程序改动，除非任务明确要求。
- 不改 Daily Reader runtime，除非任务是回归兼容。
- 实现 contract 代码时必须补硬约束测试。
- 如果发现目标架构与代码事实冲突，先停下报告，不要绕开架构随意实现。
- 不为了旧 Web Reader 或旧 `render_scene_json` contract 增加兼容映射。

## 验证要求

后端任务优先跑聚焦测试。涉及 shared workflow、database、usage audit、User Editorial Assets、RAG adapter、input adapter 时，再扩大测试范围。

前端任务需要验证 Web Reader 行为，包括刷新恢复。

纯文档任务只改本专项目录或稳定文档里的少量指针。
