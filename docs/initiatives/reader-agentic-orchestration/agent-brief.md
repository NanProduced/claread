# Reader Agentic Orchestration Post-Cutover 维护简报

> 状态：`Architectural Cutover Complete`
> 最后更新：2026-08-03（DOC-TRUTH-LIFECYCLE-R2：从实施简报改为 post-cutover 维护简报，删除历史阶段叙事与已闭合任务的逐项证据）

给 coding agent 分配 Reader agentic orchestration post-cutover 维护任务时，使用本简报作为最小上下文。Architectural Cutover 已完成；新会话不再读取 D0-D6 实施流水账。

## 必读顺序

1. `AGENTS.md`
2. `RTK.md`
3. `docs/initiatives/reader-agentic-orchestration/README.md`
4. `docs/initiatives/reader-agentic-orchestration/target-architecture.md`
5. `docs/initiatives/reader-agentic-orchestration/concepts.md`
6. `docs/initiatives/reader-agentic-orchestration/adaptive-reader-orchestration-design.md`
7. 当前任务涉及的 `docs/initiatives/reader-agentic-orchestration/modules/*.md`
8. `docs/initiatives/reader-agentic-orchestration/implementation-plan.md`
9. 涉及代码目录最近的 `AGENTS.md`

除非任务明确要求研究回溯，不要读取 `docs/initiatives/reader-agentic-orchestration/tmp/` 与 `docs/tmp/reader-orchestration/` 下的过程材料。

## 当前状态

- Reader / Ask 主链已单轨化，旧 Learning Workflow、Analysis 写入路径、Ask legacy lane、旧 Web Reader 产品页实现、旧 Directus Eval Center / Workflow Lab / Node Lab 已物理删除。
- 新链事实源：Reading Record、Stable Document、Reading Units、Anchor Segments、Enhancement Layers、`reader_events`。
- 新 Web 入口：`/app/read`（提交）、`/app/reader/[recordId]`（产品页）、`/api/web/reader/records/*`（BFF）。
- 新 Directus 控制面：`endpoints-bundle/src/reader-orch/` 4 个只读 JSON endpoint（`trace` / `run` / `record-summary` / `dashboard`），无 Console heatmap / span-tree UI。
- LLM Config 为 6 个 Directus collection（`llm_providers` / `llm_models` / `llm_profiles` / `llm_presets` / `llm_ask_options` / `llm_ask_config`），数据源为 JSON 配置文件，支持 `directus:llm-config:sync-metadata` 与 UI 可见。
- Web 是新用户提交 Reader orchestration 的唯一客户端；小程序仍是稳定客户端，共享后端业务核心与数据库。
- 三模式（短文 / 长文 / 超长文）合同、deterministic routing、durable ExecutionBudget、publish fence、representation event contract、L0/L1 deterministic navigation、T5.3 semantic outline durable layer、T5.4a/T5.4b/T5.5a/T5.6a-c/T5.7、T5.8a-c/dev-activation 均已闭合或实施。

## 不可违反的决策

### 数据与边界

- 不做旧开发数据迁移，本地业务数据可清空，但必须保留词典三表（`dict_entries`、`dict_lookup_targets`、`dict_redirects`）。
- 不做旧 `render_scene_json` 兼容映射；Web Reader UI 跟随新 contract。
- Daily Reader runtime 不进入本轮重构；与旧 Learning Workflow 已解耦。
- Reader 页面不是常驻 LLM 线程。
- PostgreSQL 拥有 durable business state；LangGraph / PydanticAI 是执行层，不是产品事实源。
- Stable Reading Base 和 Reading Units 在同一 Reading Record 内不可变。
- 高影响输入适配必须先进行 Candidate Reading Base 预览与确认。
- RAG 只服务当前 Reading Record，不做全局 User Editorial Assets RAG；RAG/OCR/OSS 等外部服务必须 adapter 化。

### Schema / Contract

- `anchor_segment_id` 是权威锚点；`sentence_id` 仅作兼容 alias，不出现在对外 DTO / persistence。
- 开发期核心类型和 DTO 不加 `V1` / `V2` 后缀；使用 `ReaderPlateSnapshot`，不创建 `ReaderPlateSnapshotV1` / `ReaderPlateSnapshotV2`。
- `ReaderPlateSnapshot` wrapper 使用 `schema_kind = "reader_plate_snapshot"`；`schema_version` 只用于 layer output、fragment 等 serialized boundary payload。
- `reader_jobs` 中 base-scoped jobs 必须携带 `base_id`；active job fingerprint 必须包含 `base_id + expected_generation + operation_fingerprint`。
- `enhancement_layers.generation` 必须匹配 target base `record_generation`。
- `reader_events.sequence` 必须是 per-record committed UI event sequence，从 `1` 开始；不能用 PostgreSQL global sequence 作为 UI catch-up sequence。
- `operation_fingerprint` 表示 business intent，不包含临时 fallback actual provider/model。
- job 可重试调度状态统一为 `retry_later`；`failed_retryable` 不作为可 claim 的长期 job status。
- Snapshot reload 必须从 DB domain facts 重建，使用 read-only `repeatable_read` transaction 或等价 consistent read；`last_event_sequence` 与 snapshot facts 必须来自同一一致性视图。
- Plate document 不是 truth，是 domain fact 的 projection；`enhancement_layers` / `user_annotations` / `reader_notes` 等表结构**不改为 patch sequence**。
- `reader_events.event_type` 必须支持 `projection_ops` 子类型。Projection op payload 使用稳定 domain target；不得把 raw Plate path / raw Slate path ops 作为后端持久合同。
- 刷新恢复**从 domain truth 重建 Plate Value**，不是从 Plate value 反推 domain。
- 禁止使用 `plate_patches` 作为正式事件名或合同名；它只能作为已拒绝的 TMP 旧口径出现。
- 所有 domain 回写（user_highlight / reader_note / ai_supplement）必须经过 anchor/path adapter 输出 domain anchor，**不直接走 node path**。

### Layer / Worker

- 译文是 parsed 的最低门槛；禁止用固定批注数量判断 parsed。
- Translation Group 是产品阅读体验合同，不是 worker 调度单位。Batch/window/longform 只能改变计算形态，不能把 group-native translation 退化成 one-anchor-one-group、one-sentence-one-group 或 one-unit-one-group。
- Vocabulary Worker 必须保留旧 workflow 的三类 item subtype：`vocab_highlight`、`phrase_gloss`、`context_gloss`（同 `vocabulary` layer `output_json.items[].item_type`，不是三个顶层 layer type）。
- Grammar Bundle Worker 可以一次生成 `grammar_note` 与 `sentence_analysis`，但发布、存储、RAG、projection、policy、eval 必须按 subtype 独立处理。`long_sentence` 不是权威 layer type。
- Semantic Outline durable truth 复用 `enhancement_layers`（`layer_type='semantic_outline'`，record/`document` scope），不新增 outline 专用事实表；默认 eligibility=false；不进入 ExecutionBudget / coverage 必需集 / ordinary supersede；当前不挂 `ReaderPlateSnapshot`（T5.4a 后才有 projection）。

### Ask / Plate Owner

- Ask Claread 是侧边助手；侧边动作必须走同一 Authorization Envelope；Ask supplement 必须标记来源，不伪装成系统层。
- owner 权限层必须覆盖：`stable` / `system_ai` / `ask_supplement` / `user` / `ephemeral`。owner 校验双层：后端权威拒绝 + 前端 Plate UX 镜像。
- Ask Sidecar 主路径工具集：`read_range` / `propose_highlight` / `propose_note` / `write_ai_supplement` / `revise_ai_annotation`。写 User Editorial Assets 必须用户确认。
- Ask 不能直接覆盖 System Annotation Layer truth；系统层修订走 proposal 或 Layer Publisher/system worker。
- LLM 不能直出 arbitrary Plate JSON 或 raw Slate ops 作为持久事实；AI / Markdown fragment 必须经过 typed schema、strict allowlist、length cap、source grounding 和 link protocol policy。
- D5 默认禁用 image / table / inline HTML / math / frontmatter / definition / footnote；启用前必须另做 spike。
- 非 Web 客户端继续 polling snapshot，不订阅 Plate projection ops。

### Budget / Fence / Publish

- T4.2a-R2 durable per-layer `ExecutionBudget`：`max_effective_calls = planned * max_multiplier`（默认 `max_multiplier=3`，与生产 `max_attempts=3` 对齐）；durable 跨 `runner.run()` 持久化，每次入口通过 `ExecutionBudget.load_durable()` 从 `reader_jobs` 聚合重建；budget 提供确定性上限和观测/调度作用，**本身不降低原有 retry 成本**；是否调低 `max_attempts` 是独立数据驱动决策。
- `BUDGET_CONSUMING_OUTCOMES = {succeeded, retry_later, failed_terminal}` 消耗预算；`superseded / no_job / skipped / budget_denied` 不消耗。
- 预算耗尽时 `stopped_reason = budget_exhausted`（全 layer）或 `partial_budget_exhausted`（部分 layer）；`display_title` 不纳入预算；`NON_FINALIZABLE_STOPPED_REASONS` 只含 `attention_required`。
- `budget_denied` 记为独立 outcome（不是 `no_job`），计入 `outcome_counts["budget_denied"]` 和 `round_no_job_count`；持久化到 `reader_runtime_spans.metadata_json`（pipeline root span）和 WorkerLoop 结构化日志。
- T4.2a-R2 fingerprint 模型（方案 B：保守排序集合）：`load_durable()` 返回排序后的 `non_superseded_fingerprints` 集合 per layer；预算保守聚合所有非 superseded fingerprint 的 `attempt_count`；不新增 schema/migration。
- T4.2a-R2 fallback guardrail：`_should_suppress_grammar_per_unit_fallback` 只有 batch job 为 `superseded` 或不存在 batch job 时允许 per-unit fallback；`succeeded` / `failed_terminal` / `skipped` / 任何非终态 batch job 均抑制 fallback。translation / vocabulary 无等效 guard，per-unit fallback 路径已删除。
- T4.2a-R2 route flip fencing：bootstrap supersede + claim-time `_validate_fence` → `_check_route_consistency`（对比 `reader_jobs.input_json.article_route` 与 `reader_runs.envelope_json.article_route`，mismatch 返回 `stale_route_fingerprint`）+ publish-time 同一 `_validate_fence`（所有 6 个 publisher 方法）。旧 fingerprint job 不可 claim、不可 publish；publish fence 失败后 worker/service 层调用 `ReaderJobRuntime.transition(job_id, target_status="superseded", rationale_code="publish_fence_failed")`，对应 reader_run 标记 superseded；pipeline summary 只统计 DB 中真实 superseded transition，移除所有 `max(1, superseded_jobs)` 虚报（改为 `max(0, ...)`）。
- 测试约束：不得通过 test-local worker subclass 或临时改写数据库 fingerprint 模拟成功；测试使用确定性 fake executor + call counter，不调用真实 LLM。publish fence 测试必须经过真实 publisher + worker/pipeline catch；partial exhaustion 测试必须经过 WorkerLoop + Finalizer；budget diagnostics 测试必须查询持久 `reader_runtime_spans.metadata_json`，不只检查 summary 返回值。

### 禁止未批准的传输改造

- SSE、WebSocket、JSON Patch、ETag/304、通用 Plate tree diff 均未批准；不得预先实现。
- SSE 只作为带 sequence / generation / target 的通知通道，不能承载整份 snapshot，也不能替代局部 Plate projection。
- `snapshot_id` 不可复用为 HTTP ETag（LP-R4 结论 B）；G1 user assets、G2 Ask supplements、G3 用户可见 record metadata 存在 event coverage gap，需先完成 O4-R2 transactional representation event coverage，才评估 PUX-R4 interaction-stable incremental projection，再评估 semantic fragment transport，最后才评估以 SSE 替换可见页 event polling。

### Observability

- T4.2a-O3 duration provenance：`agent_run_duration_ms` 仅表示本地 `agent.run` 单调时钟耗时；`provider_request_duration_*` **仅**在专用 adapter envelope（`_claread_provider_response_timing`，kind/version 校验）下为 `available`，否则 `unavailable`。禁止把 worker_tick `duration_ms`、pipeline wall 或 agent-run duration 命名/写入为 provider latency 或改写 `ai_usage_events.latency_ms`。
- Sample A grammar 0-token usage attribution 仍 **UNRESOLVED**；Cost/Latency 仍 **PARTIAL**；不得宣称 token / 成本 / 时延已可靠或 Sample A 已修复。
- T4.2 bounded LLM document profiler 继续暂缓；只有 deterministic router 在真实边界样本上出现稳定误判时再评估。

## 外部服务假设

- RAG 测试阶段优先使用 Zilliz Cloud；上线前评估迁移到阿里云 RAG / 向量检索服务或百炼知识库。
- OCR / 富文档解析优先评估阿里云百炼图像理解、Qwen OCR/VL、文档解析能力。
- 文件上传测试阶段使用阿里云 OSS；上线目标为 OSS + CDN。

## 编码规则

- 只修改当前任务范围内的文件；不自行新增架构文档。
- 不做小程序改动，除非任务明确要求。
- 不改 Daily Reader runtime，除非任务是回归兼容。
- 实现 contract 代码时必须补硬约束测试。
- 如果发现目标架构与代码事实冲突，先停下报告，不要绕开架构随意实现。
- 不为旧 Web Reader 或旧 `render_scene_json` contract 增加兼容映射。

## 验证要求

- 后端任务优先跑聚焦测试。涉及 shared workflow、database、usage audit、User Editorial Assets、RAG adapter、input adapter 时，再扩大测试范围。
- 前端任务需要验证 Web Reader 行为，包括刷新恢复。
- 纯文档任务只改本专项目录或稳定文档里的少量指针。
- 验收节奏：短文、长文、超长文三种模式先分别完成代码级合同闭环，再统一真实 LLM / 页面验收；中间实现阶段优先使用 deterministic tests、fake executor、recorded LLM response 和 DB contract checks；不要每修一个局部就反复真实跑长文/超长文。

## 关键 backlog 指针

post-cutover backlog 与路线详见 [`implementation-plan.md`](implementation-plan.md)；cutover 落地结论与旧依赖审计见 [`modules/cutover-and-old-workflow.md`](modules/cutover-and-old-workflow.md)；D2 spike 历史索引见 [`archive/README.md`](archive/README.md)（spike 全文已删除，结论已压缩进 `target-architecture.md` 与对应 `modules/*.md`）。