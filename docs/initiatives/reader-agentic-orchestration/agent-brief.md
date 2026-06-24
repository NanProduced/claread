# Reader Agentic Orchestration 执行简报

> 状态：`权威简报`
> 最后更新：2026-06-24

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

### 产品边界

- Web 优先，小程序实现暂缓。
- Academic workflow 暂缓重构；待 learning workflow 验证稳定后再单独设计。
- Daily Reader runtime 不进入本轮重构。
- 不做旧开发数据迁移，本地数据可清空，但必须保留词典三表（`dict_entries`、`dict_lookup_targets`、`dict_redirects`）。
- 不做旧 `render_scene_json` 兼容映射；Web Reader UI 跟随新 contract 改写。

### 架构原则

- Reader 页面不是常驻 LLM 线程；产品对象是 `Reading Record`，不是 workflow run。
- PostgreSQL 拥有 durable business state；LangGraph / PydanticAI 是执行层，不是产品事实源。
- Stable Reading Base 和 Reading Units 在同一 Reading Record 内不可变。
- 高影响输入适配必须先进行 Candidate Reading Base 预览与确认。
- 译文是 parsed 的最低门槛；禁止用固定批注数量判断 parsed。
- 开发期核心类型和 DTO 不加 `V1` / `V2` 后缀。使用 `ReaderPlateSnapshot`，不创建 `ReaderPlateSnapshotV1` / `V2`。
- `ReaderPlateSnapshot` wrapper 使用 `schema_kind = "reader_plate_snapshot"`；`schema_version` 只用于 layer output、fragment 等 serialized boundary payload。

### 数据与状态

- `reader_events.sequence` 必须是 per-record committed UI event sequence，从 `1` 开始；不能用 PostgreSQL global sequence 作为 UI catch-up sequence。
- `reader_jobs` 中 base-scoped jobs 必须携带 `base_id`；只有 `build_base + record` job 可无 `base_id`。active job fingerprint 必须包含 `base_id + expected_generation + operation_fingerprint`。
- `enhancement_layers.generation` 必须匹配 target base `record_generation`。
- `active_base_id -> reading_bases.status='active'` 是 service / publisher invariant，不是 DDL trigger。设置 active base、supersede base、publish job/layer 时必须显式校验。
- Job claim/publish fence 必须同时校验 record generation、target base generation、target base `status='active'`、record `active_base_id == job.base_id` 和 lease token。
- Job 可重试调度状态统一为 `retry_later`；`failed_retryable` 不作为可 claim 的长期 job status。
- Snapshot reload 必须从 DB domain facts 重建，使用 read-only `repeatable_read` transaction；`last_event_sequence` 与 snapshot facts 必须来自同一一致性视图。DB hydration 后必须调用 `validate_reading_base_build_result` 校验。
- Polling cursor 在 `after_sequence == last_event_sequence` 或 empty stream 时返回空 events；只有发现 missing committed event / sequence gap 时才要求 reload。
- Snapshot builder 必须拒绝不属于当前 base / unit / anchor 的 layers、parsed decisions、ask supplements 和 user assets。
- `operation_fingerprint` 表示 business intent，不包含临时 fallback actual provider/model。

### Worker 与执行

- Worker loop 必须是独立 process（本地 CLI / 部署独立 service），不挂 FastAPI lifespan、不塞 Web submit、不新增 public worker-control endpoint。
- Worker loop 扫描候选 record 时，粗筛可用 `product_state in ('processing','readable_enhancing')` + active base/generation/status + `readiness_state in ('article_ready','initial_enhancement_ready')`；exact missing work 由 bootstrap service / pipeline runner 决定。
- Translation worker 必须使用 job-type filtered claim，不 claim mixed queue 中的非 translation jobs；成功和失败路径必须写 `ai_usage_events` attribution。
- Vocabulary worker 保留三类 item subtype：`vocab_highlight`、`phrase_gloss`、`context_gloss`（同一 `vocabulary` layer 的 `output_json.items[].item_type`，不是三个顶层 layer type）。
- Vocabulary worker 对同一 span 按 `context_gloss > phrase_gloss > vocab_highlight` 仲裁；`fallback_window` anchor segment 不产出 vocabulary item。
- Grammar bundle worker 发布时拆成 `grammar_note` 与 `sentence_analysis` 两个独立 layer rows；`fallback_window` 命中时整条 item 跳过。
- 模型选择走 Model Profile / route lookup，不由 planner 即兴决定。Vocabulary route 必须显式配置 `reader_vocabulary_model_profile`，不 fallback 到 annotation profile。
- Worker 默认未配置时必须失败且不发布空 layer，只有显式 fake executor 才能发布空 output。

### Plate 与渲染

- Reader Article Body 渲染层走 Plate.js（`platejs/react`），不是其他编辑器。
- Plate document 不是 truth，是 domain fact 的 projection。`enhancement_layers` / `user_annotations` / `reader_notes` 等表结构不改为 patch sequence。
- 刷新恢复从 domain truth 重建 Plate Value，不从 Plate value 反推 domain。
- `anchor_segment_id` 是新权威锚点；`sentence_id` 只作为兼容 alias。
- owner 权限层覆盖 `stable` / `system_ai` / `ask_supplement` / `user` / `ephemeral`；后端权威拒绝 + 前端 Plate UX 镜像。
- 所有 domain 回写必须经过 anchor/path adapter 输出 domain anchor，不直接走 node path。
- `reader_events.event_type` 支持 `projection_ops` 子类型；projection op payload 使用稳定 domain target，不把 raw Plate/Slate path ops 作为后端持久合同。禁止使用 `plate_patches` 作为正式事件名。
- 默认禁用 image / table / inline HTML / math / frontmatter / definition / footnote；启用前必须另做 spike。
- 非 Web 客户端继续 polling snapshot，不订阅 Plate projection ops。

### Ask 与 RAG

- Ask Claread 是侧边助手；侧边动作走同一 Authorization Envelope。
- Ask Sidecar 主路径工具集：`read_range` / `propose_highlight` / `propose_note` / `write_ai_supplement` / `revise_ai_annotation`。写 User Editorial Assets 必须用户确认。
- Ask 不能直接覆盖 System Annotation Layer truth；系统层修订走 proposal 或 Layer Publisher/system worker。
- LLM 不能直出 arbitrary Plate JSON 或 raw Slate ops 作为持久事实。
- RAG 只服务当前 Reading Record，不做全局 User Editorial Assets RAG。
- RAG/OCR/OSS 等外部服务必须 adapter 化，不能成为 Claread 业务事实源。

### LangGraph 口径

- LangGraph 不进入主路径 run/job/event/layer durable control plane。
- LangGraph v1+ 的 persistence、streaming、interrupt/resume、subgraph 只作为 D6+ 隔离 spike 候选，且必须有具体 Ask HITL / multi-branch repair 需求。

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
  -> optional LangGraph local flow in D6+ only after isolated spike
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

实现边界：

- PydanticAI 用于 LLM-backed workers；LangGraph 不进入主路径。
- 不继承旧"每用户一个 active task"产品约束；并发由 envelope 控制。
- Text anchors 复用现有 UTF-16 offsets 和 `fnv1a32-utf16` hash contract；span anchor 使用 `anchor_segment_id` + unit-local offsets，且 offset 必须落在对应 Anchor Segment range 内。
- Stable Reading Base 是输入适配和必要用户确认后的可读英文正文；Unit Builder 不负责 OCR 修复、boilerplate 删除、多栏顺序修复或正文重写。
- Anchor Segment 是 sentence-like segment，通常是句子；必要时可为 clause 或 fallback window，并通过 `segment_type` 标记。Unit Boundary Refiner 只能建议既有 Anchor Segments 的 split/merge，不能改写文本或生成坐标。
- Translation worker 不携带 Ask history、planner trace 或整篇文章上下文。
- System Annotation Layers 不得写入或覆盖 User Editorial Assets。
- Usage audit 必须能按 record、job、layer、model profile、cache status 归因。

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
