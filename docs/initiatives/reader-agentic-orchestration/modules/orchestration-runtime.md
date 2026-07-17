# Orchestration Runtime

> 状态：`D6 ongoing；T5.7 partial：semantic_outline worker_type 依赖 migration 0020；真实 outline LLM 未注册 route`
> 最后更新：2026-07-18（T5.7：worker_loop 测试 schema 必须 apply 0020；outline generator 默认 Unconfigured）
> 范围：bounded run/job、worker lease、Authorization Envelope、并发和框架边界。

## Runtime 形态

```text
Web Reader
  -> Reader API / BFF
  -> PostgreSQL Reading Record + run/job state + event log
  -> worker abstraction
  -> typed execution units
  -> projection emitter / Reader snapshot
  -> LangSmith + ai_usage_events
```

PostgreSQL 拥有 durable business state。LLM framework 只是执行工具。

## Framework Posture

D4/D5 默认收窄：

- Planner 先用 deterministic policy function：`plan(state, envelope) -> typed plan`。
- PydanticAI 用于 LLM-backed workers，例如 translator、annotator、judge。
- LangGraph 不作为 D4/D5 主路径依赖；D6+ 如需要 branching、interrupt 或复杂 repair flow，再做单独隔离 spike 和版本评估。

这样避免 Planner、LangGraph、PydanticAI 三个控制面重叠。

Planner、Skip Gate、Model Profile、Prompt Cache 和 Usage Bucket 的细节见 `policy-and-cost-control.md`。本文件只定义 runtime 执行边界。

后端依赖版本必须在 D3 runtime skeleton 前完成 alignment spike。D4/D5 worker、runner、projection 或 eval 任务不应临时升级 PydanticAI、LangGraph、LangSmith 或 provider SDK。

### Plate Plugin 体系状态

前端 Plate.js plugin 体系已从阶段划分进入代码事实校准。**完整接入矩阵（package 状态、plugin kit、组件落点、弱接入风险）的唯一权威归宿是 [`reader-plate-component-integration.md`](./reader-plate-component-integration.md)**；本文件不再复制状态表，避免接入状态变更时多处同步漂移。

简要口径（详细以 owner 文档为准）：

- `ReaderRecordPlateSurface` + `ReaderPlateKit` + reader block/leaf plugins 已在 Reading Record 页面接入。
- `@platejs/floating` FloatingToolbar 已接入；旧 `SelectionActionStrip` 不再在生产路径。
- `@platejs/comment` CommentKit 部分接入（未接 DiscussionKit）；`@platejs/selection` CursorOverlay 部分接入（Structure Lens 未接）。
- `@platejs/ai` / `@platejs/suggestion` 依赖存在但源码未接入；Ask 继续走 Claread reader-ask。
- Stable Document Blocks -> source document projection 后端 schema/service 已推进，中心正文仍主要由过渡 Canonical Text / anchor segment 投影。

UI/UX 详细设计见 [`reader-record-plate-surface-ui.md`](./reader-record-plate-surface-ui.md)。

## LangGraph 评估结论

当前 D4/D5 不以 LangGraph 作为主控 runtime。LangGraph v1+ 的 persistence、streaming、interrupt/resume、subgraph 和 runtime observability 对未来复杂 repair、branching、human-in-the-loop 或长会话 checkpoint 优化可能有价值，但不是 D5 主链路的硬依赖。具体版本能力、breaking changes、PostgresSaver schema 影响和旧 workflow 兼容性必须在单独 spike 中基于当时官方文档与 lockfile 实测确认，不在本合同中冻结。

D4 仍以 PostgreSQL tables 持久化 business state 和 worker state：

- `reader_runs` / `reader_jobs` 是 run、lease、retry 和 budget 的权威。
- `reader_events` / `reader_event_sequences` 是 UI catch-up 的权威。
- PydanticAI 只负责单个 LLM-backed typed worker 调用。

D3-P0 不主动升级 LangGraph。若 D6+ 决定使用 LangGraph v1+，必须单独隔离 spike：

- checkpoint 与 Claread `reader_runs` / `reader_jobs` 的边界。
- interrupted / resumed graph state 与 record generation fence 的兼容。
- node rename / state schema 变更的 backward compatibility。
- graph streaming 与 `reader_events` / Plate projection event 的关系。

D5 双评审 disposition 更新：

- D5 主链路 runner、translation、vocabulary、grammar bundle、snapshot projection 和 eval 均不引入 LangGraph。
- LangGraph persistence / checkpointer 可以保存 thread-scoped graph state，但不是 Claread 的业务事实源；不得替换 `reader_runs`、`reader_jobs`、`reader_events` 或 `enhancement_layers`。
- LangGraph 的下一次评估点命名为 D6-LG0，且必须是隔离 spike：不修改生产依赖、不替换 durable control tables、不把 checkpoint 事件混入 Reader UI event truth。
- D6-LG0 只有在出现具体 Ask Document Tools / human approval / multi-branch repair 需求时启动。Spike 必须验证 thread id 映射、record generation fence、side-effect idempotency、PostgresSaver schema impact、旧 workflow compatibility 和回滚路径。
- `projection_ops` incremental applier 不阻塞 D5 第一条页面可测链路；D5 smoke 继续使用 snapshot reload。启用 projection ops 前再做 projection consistency / race spike。

当前风险排序：

| Priority | Risk | Posture |
|---|---|---|
| P2 | Translation `parsed_decisions` 依赖 publisher transaction 与 diagnostic 同时成立。 | D5-G1 已移入 publisher transaction；保留 orphan diagnostic 用于发现历史或人为 partial state。 |
| P2 | Vocabulary fallback window / boundary quality policy 需要和 grammar 保持一致。 | D5-G2 已实现：`segment_type=fallback_window` 的 vocabulary candidate 跳过并记录 `boundary_low_fallback_window` diagnostics。 |
| P1 | `projection_ops` incremental applier 未端到端启用。 | 不阻塞 D5 smoke；启用前做 projection consistency spike。 |
| P2 | `active_base_id -> reading_bases.status='active'` 当前是 service / publisher invariant。 | 保持显式校验；主链路稳定后再评估 DB trigger / equivalent hardening。 |
| P2 | Grammar bundle usage attribution 是 job-level。 | 保持 no-double-count；仅当成本视图需要时再定义 per-layer allocation policy。 |

## D5 Main Chain Runner

`ReaderEnhancementPipelineRunner` 是 D5 正式 runtime 组件，用于把当前 active base 的 enhancement jobs 作为一个 bounded batch 推进。它不是产品对象，也不是 public API。

当前 closeout 口径：

- runner 先通过 `EnhancementJobBootstrapService` 为当前 record/base/generation 创建缺失的 display title、translation、vocabulary 和 grammar bundle jobs；
- drain 顺序固定为 display title -> translation -> vocabulary -> grammar bundle；
- worker claim 必须带 `reading_record_id`、`base_id`、`expected_generation` scope，防止某个 record 的 runner 消费另一个 record 的 queued jobs；
- runner 只汇总 typed summary 和 attention outcome，不拥有 layer truth，不绕过 Layer Publisher；
- 遇到 `retry_later`、`failed_terminal` 或 publish fence supersede 时返回 attention summary，由调用方决定继续、告警或 repair；
- runner 不新增 public worker-control endpoint，不启动后台 daemon，不引入 LangGraph / MQ / Temporal / SSE；
- Web 页面继续通过 snapshot reload 和 polling events 感知结果，D5 smoke 不要求 `projection_ops` incremental applier。

当前 D5-W2 已补齐生产/本地 worker loop 的最小运行形态：独立 worker process 通过 CLI entrypoint 启动，扫描 eligible records 并调用 `ReaderEnhancementPipelineRunner`。该 loop 仍不是 public user-facing endpoint，也不会把 LLM execution 同步塞进 Web submit request。

### Display Title Generation Worker

`generate_display_title_zh` 是 Reader Header 中文标题的后端 worker job。它属于 enhancement runner 的首个 job，但不写 `enhancement_layers`，而是在 job/run fence 校验通过后更新 `reading_records.generated_title_zh` 和 `title_generation_status`。

Contract:

- job type: `generate_display_title_zh`;
- target scope: `record`;
- target key: `reading_record_id`;
- operation fingerprint: `display_title_zh_v1`;
- model route / usage capability: `reader_title_generation`; this route requires an explicit `reader_title_model_profile` and must not silently fall back to translation or annotation profiles;
- record states: `pending`, `succeeded`, `failed_retryable`;
- successful output: one Simplified Chinese masthead title, hard max 32 characters, recommended 8-24 Chinese characters, suitable for Header display;
- retryable failure: record `generated_title_zh = NULL`, `title_generation_status = failed_retryable`, job `retry_later`, run `failed_retryable`, and a record state-change event.

Input policy:

- prefer active Stable Reading Document title and Stable Document Blocks when present;
- for long text, PDF, OCR and other artifact-backed inputs, use heading/abstract-like blocks, first meaningful paragraphs, captions or OCR text blocks already normalized into Stable Document Blocks;
- during migration, if Stable Document Blocks are absent, use bounded `reading_units` / Canonical Text Layer preview from the active base;
- never send the full article text to the title model; the title prompt receives bounded headings, preview and source metadata only.

`failed_retryable` is recoverable by worker retry or compensation bootstrap. Snapshot/API must keep the title absent in this state and expose the retryable status instead of fabricating a fallback.

### Prompt 输出语言契约

四个 LLM worker（display title / translation / vocabulary / grammar bundle）的 prompt 必须强制输出简体中文：

| 层级 | 要求 | 实施位置 |
|------|------|----------|
| system prompt | 添加"输出语言规则（强制）"section，明确所有面向用户的讲解类字段必须使用简体中文 | `services/api/prompts/agents/reader_title_generation.yaml` and `services/api/prompts/agents/reader_layer_*.yaml` |
| user prompt | 添加 `Output language: All human-readable fields MUST be in Simplified Chinese (简体中文).` 或等价中文约束 | 各 worker 的 `_build_*_prompt` 函数 |
| schema description | 字段 description 明确语言契约（"简体中文讲解"） | `services/api/app/schemas/reader_orchestration.py` |

允许保持英文的内容：
- 原文锚点片段（selected_text）
- headword、phrase、pattern
- 专有名词、语法术语英文原形

阶段一不引入 reading_goal/variant 接线，仅修复输出语言。变体策略注入属于阶段二。

## D5/D6 Worker Loop Posture

D5-W1 worker loop 评估结论为 `accepted_with_changes`。

正式运行形态：

- 使用独立 worker process。
- 本地通过 `uv run reader-enhancement-worker` CLI entrypoint 启动；部署通过同一 entrypoint 作为独立 worker service / process / container 启动。
- Artifact-backed input 另有独立入口 `uv run reader-artifact-pipeline-worker`，负责 `input_artifact_extraction` 与 `extracted_artifact_materialization` 两类 record-level pre-base jobs。
- API 服务只负责 request-serving，不在 FastAPI lifespan / startup hook 中启动 worker loop。
- Web submit 不同步执行 runner；submit 只创建 durable `article_ready` facts。
- 不新增 public 或 semi-public worker-control endpoint。
- 不使用 smoke harness 或 fake executors 作为产品 runtime。

D6-P6 本地验证合同：

- `/app/read` 和 `/app/reader-plate` 提交成功只证明 API 写入了 `article_ready` facts，并不代表 enhancement worker 已运行。
- Web 页面通过 `/api/web/reader-plate/{recordId}/events` polling 和 snapshot reload 等待后续 `layer_published` / `parsed_decision_updated` 等 events；Web 不消费 `reader_jobs`。
- 本地验证纯文本增强链路时，API、Web 和 `reader-enhancement-worker` 必须同时运行；验证 PDF、Markdown、图片 OCR 等 artifact-backed input 时，还必须启动 `reader-artifact-pipeline-worker`。
- 当前共有 3 个进程级 worker entrypoint：enhancement 与 artifact 是默认完整链路的 2 个必需 worker；`reader-article-rag-index-worker` 仅在 `READER_ARTICLE_RAG_ENABLED=true` 时启用。
- 仓库根目录可用 `pnpm reader:dev` 聚合启动 API、Web 和两个默认 worker，日志带进程名前缀；需要隔离日志时使用 `pnpm reader:api`、`pnpm reader:web`、`pnpm reader:worker:enhancement`、`pnpm reader:worker:artifact` 分终端运行。
- `uv run reader-enhancement-worker --once` 是诊断单次消费入口；`uv run reader-enhancement-worker` 是持续消费入口。
- 如果 DB 中 `translate_unit` 或后续 jobs 长时间停在 `queued`，且 `reader_events` 只有 `article_ready`，这是 worker 未运行或未消费队列，不是 article parsing failure。具体排查 SQL 见 `local-real-chain-runbook.md`。

D6-E3 页面内验证 closeout：

- 本地正常验证入口是 `/app/read -> /api/web/reading-record/submit -> /app/reader-record/{recordId}`，不是旧 `/api/web/analysis/submit` 或 `/app/reader/{recordId}`。
- `/app/reader-record/{recordId}` 继续用 BFF snapshot/events polling 观察 worker 发布结果；页面应通过 snapshot reload 更新 Workbench-backed Plate read-only 中心区和 `enhancement_progress` 摘要。
- 诊断仍以 `reading_records`、`reader_jobs`、`reader_events`、`enhancement_layers` 和 snapshot `enhancement_progress` 为准；完整本地 checklist、recordId 获取和 stuck 判断表见 `local-real-chain-runbook.md`。
- Worker 独立进程是正式运行形态；不要为了本地页面验证把 worker 挂进 FastAPI lifespan 或 Web submit 请求。

D6-P7A Reader UI 可观察性合同：

- `ReaderPlateSnapshot.enhancement_progress` 是 snapshot projection，只从 `reading_records`、当前 active base/generation 的 `reader_jobs` 和 `enhancement_layers` 推导；它不是新的业务事实源。
- `enhancement_progress` 让 `/app/reader-record/{recordId}` 区分 enhancement work 的 `not_started`、`queued`、`processing`、`succeeded`、`failed` 和 `action_required`，避免只显示泛化的“批注生成中”。
- 长时间 `queued` 且没有 `layer_published` / `parsed_decision_updated` events 表示 worker 未运行、未 claim 到 job、或被 lease/eligibility 条件挡住；这仍不是 article_ready 解析失败。
- Published `translation` / `vocabulary` / `grammar_note` / `sentence_analysis` layers 在 progress 中显示 `succeeded`。`failed_terminal` job 会显示为 `failed` 或 D6-P4 user-actionable 的 `action_required`，但不会由 snapshot projection 改写 `reading_records.product_state`。

Eligible scan 初版口径：

- coarse filter：
  - `reading_records.deleted_at IS NULL`
  - `reading_records.lifecycle_status = 'active'`
  - `reading_records.product_state IN ('processing', 'readable_enhancing')`
  - `reading_records.readiness_state IN ('article_ready', 'initial_enhancement_ready')`
  - `reading_records.active_base_id IS NOT NULL`
- active base join 必须校验：
  - base belongs to the record
  - `reading_bases.status = 'active'`
  - `reading_bases.record_generation = reading_records.generation`
- `coverage_complete` 默认不再进入普通 enhancement scan；如 D6 repair / rerun policy 需要，必须单独定义回流条件。
- scanner 只做 coarse eligibility；不要复制 per-layer missing-work 判断。display title / translation / vocabulary / grammar bundle 的 exact bootstrap 继续由 `EnhancementJobBootstrapService` 和 runner 决定。

Concurrency / lock 初版口径：

- per-record advisory lock 必须有，防止两个 worker 同时推进同一 record。
- per-user concurrency 默认 `1`；可以通过后续配置放宽。
- per-worker process concurrency 默认 `1`；先通过增加 worker process 数量扩吞吐。
- `retry_later` 必须尊重 job `available_at`，不能 hot-loop 同一 record。
- D6-P0 product-state classifier 保守收口：
  - `all_workers_no_job` / `max_ticks_reached` / `max_jobs_reached` 不改 `product_state`
  - `retry_later` 不改 `product_state`
  - `failed_terminal` 默认映射到 `failed`
  - `action_required` 只允许来自 failed-terminal mapper 认定的 user-remediable `attention_code`；v1 仅提升 `reader_user_confirmation_required`
  - `publish_fence_failed`、executor/profile missing、model route missing 等 system failure 不映射成 `action_required`

Model profile / executor 口径：

- real worker loop 默认使用真实 executors。
- profile 缺失保持 fail-closed。
- 不静默 fallback 到 fake executors、annotation profile 或 synthetic layer。

当前实现补充：

- `ReaderEnhancementWorkerLoopService` 先做 coarse scan，再以 per-record / per-user advisory locks 串行推进单个 record 与单个用户。
- scanner 会跳过 `coverage_complete`，并通过 runnable/tracked job gate 避免 `retry_later` hot-loop 与 `failed_terminal` 反复 bootstrap。
- `services/api/pyproject.toml` 已注册 `reader-enhancement-worker = "scripts.run_reader_enhancement_worker:main"`，底层仍复用 `scripts/run_reader_enhancement_worker.py` 的 `--once` 和 loop mode，本地与部署共用同一入口。
- D6-P1 起，worker loop 在成功更新 `reading_records.product_state` 时同步发布 `record_product_state_updated` reader event；D6-P0 的保守分类规则保持不变。

### D6-I3 Artifact Pipeline Worker

Artifact-backed input 使用独立 pipeline，不复用 enhancement worker scan：

```text
source_artifacts submit-input
  -> input_artifact_extraction job
  -> ArtifactExtractionProviderRouter
     -> TextArtifactExtractionProvider
     -> PdfArtifactExtractionProvider
     -> OcrArtifactExtractionProvider
  -> original_inputs.source_text
  -> extracted_artifact_materialization job
  -> Input Suitability Gate
  -> Stable Reading Document 或 Candidate Document
```

运行入口：

- `uv run reader-artifact-pipeline-worker --once`：单次 drain，适合本地诊断。
- `uv run reader-artifact-pipeline-worker`：持续循环，适合部署为独立 worker process / container。

关键约束：

- `input_artifact_extraction` 和 `extracted_artifact_materialization` 都是 record-level pre-base jobs，`base_id = null` 是有意设计；runtime fence 必须在 `active_base_id IS NULL` 时才允许 claim，active base 已存在时 supersede。
- Extraction 成功只回写 `original_inputs.source_text` 和 job transition，并 enqueue materialization job；不直接创建 Stable Document、Candidate Document 或 reader events。
- Materialization 在同一事务内重新校验 record/input/artifact 绑定和 generation，再根据 suitability outcome 冻结 stable document 或创建 candidate document。
- OSS reader / PDF / OCR 都是 adapter。缺 OSS 凭证或 SDK 时 worker 仍可启动，但 extraction job fail closed；缺 `pypdf` 时仅 PDF job fail closed；OCR 默认 disabled，image job fail closed 为 `ocr_provider_unconfigured`。
- PDF/OCR provider 输出不是业务事实源。Stable Reading Document、Stable Document Blocks、Canonical Text、Reading Units 和 Anchor Segments 仍是后续渲染、Ask、RAG citation 的事实源。

## Run / Job

Reader Run 是一次 bounded background run。Reader Job 是 run 内可 claim、heartbeat、retry 的执行单位。

D4 最小纵切必须包含 `reader_runs`。可以后置完整 envelope schema、runtime counter 表和 DLQ，但不能后置 run/generation/envelope snapshot，因为 job claim、publish fence、usage attribution 和 budget/concurrency 都依赖它。

`reader_runs.status` 建议：

- `queued`
- `running`
- `waiting_user`
- `waiting_quota`
- `paused`
- `completed`
- `failed_retryable`
- `failed_terminal`
- `cancelled`
- `superseded`

`reader_jobs.status` 建议：

- `queued`
- `claimed`
- `retry_later`
- `paused`
- `succeeded`
- `failed_terminal`
- `cancelled`
- `superseded`
- `skipped`

Retryable failure is represented by `failure_class` plus transition to `retry_later`，不是长期 `failed_retryable` job status。`heartbeat_lost` 不必作为长期状态；watchdog 可根据 lease 过期判断并重新入队。

Base-scoped jobs 必须携带 `base_id`。只有 base 尚未存在的 record-level jobs 可以 `base_id = null`，当前白名单包括 `build_base`、`input_artifact_extraction` 和 `extracted_artifact_materialization`。这些 pre-base jobs 必须额外校验 record generation 和 `active_base_id IS NULL`；其他 non-build-base job 遇到 null base 必须 fail/supersede。`operation_fingerprint` 和 active job unique key 必须包含足以区分 record/generation/base-or-artifact target 的 scope，避免 supersede 或重新冻结 base 后 late worker result 被误发布。

## 并发模型

新 runtime 不继承旧 `analysis_tasks` 的“每用户一个 active task”产品约束。

D1 并发口径：

- 同一 Reading Record 同一 generation 只能有一个 mutating active run。
- 同一 Reading Record 可以有多个 layer jobs 并行，但必须受 envelope 的 `unit_range`、`concurrency` 和 `cost` 限制。
- 同一用户可以有多个 Reading Records 处于 processing，但有 `max_active_runs_per_user` 和 `max_claimed_jobs_per_user` 配额。
- worker 进程有全局 `max_claimed_jobs`。
- Ask sidecar action 与 Reader enhancement 共用同一用户级 concurrency / cost envelope。

D2 需要校准默认值。D4 可以沿用保守默认，例如单用户 active run 数较小、单 worker claimed jobs 不超过旧 `MAX_CONCURRENT_TASKS=4` 的经验值。

## Lease Contract

每个 job claim 必须包含：

- `lease_owner`
- `lease_token`，类型必须是 UUID
- `lease_expires_at`
- `attempt`
- `idempotency_key`
- `operation_fingerprint`
- `expected_generation`
- frozen input / envelope snapshot pointer

规则：

- claim 时从 `queued` 或可重试状态原子更新为 `claimed`。
- heartbeat 只允许当前 `lease_token` 更新。
- `lease_expires_at` 是 per-job absolute timestamp；watchdog 根据过期时间重新排队，不使用旧 `updated_at` 全局阈值。
- LLM 调用不可中途取消；旧 worker 返回后，发布前必须重新校验 lease、generation 和 record state。
- retry budget 分为 transient、repair、replan，不共享模糊 attempt。
- lease lost 可以 requeue；cancel、supersede 或 generation mismatch 代表 obsolete result，不应重新消耗 LLM。

## Operation Fingerprint

`operation_fingerprint` 至少由以下字段组成：

- `reading_record_id`
- `base_id`
- `job_type`
- `unit_id` 或 unit range
- `layer_type`
- `layer_version`
- `prompt_version`
- `model_route` 或 profile policy version
- `input_hash`

同一 fingerprint 的 published result 必须幂等。

Fingerprint 表达业务意图，不表达临时执行路径。deterministic fallback 选择到的 actual provider/model 不应改变同一个 job 的 fingerprint；actual profile/provider 写入 usage event 和 attempt metadata。

## Publish Guard

Layer Publisher 必须在单个数据库事务内完成：

1. 校验 run generation 与 expected generation 一致。
2. 校验 Reading Record 未 cancelled / superseded。
3. 校验 target base / unit 仍属于当前 record。
4. 校验 schema、anchor 和 source grounding。
5. CAS 发布 `unit_id + layer_type + layer_version` 的唯一 winner。
6. 写入 `reader_events` UI domain event。
7. 对已启用 Web projection event 的发布，写入同序列的 `projection_ops` 或 `projection_reset_required`。
8. 写入 usage / trace 关联。

任何一步失败，不能部分发布。

Projection emitter 不拥有业务事实。它只能从已通过 Publish Guard 的 Stable Base、Reading Units、Enhancement Layers、Ask Supplements 或 User Editorial Assets 生成 Web Plate projection event。若 projection event 生成失败，domain publish 必须按可恢复策略处理：要么同事务失败回滚，要么写 `projection_reset_required` 让前端刷新 snapshot；不能留下“业务已发布但前端永远无法恢复”的状态。

D4 不要求 projection ops 端到端。D3 可以建立 projection emitter/schema 骨架；D4 translation layer 可以通过 snapshot reload 或 simple projection refresh 呈现。D5 再把 Layer Publisher 末尾的 projection event 与 Web Plate applier 端到端接通。

## Authorization Envelope Enforcement

| 边界 | 强制点 | 说明 |
|---|---|---|
| token / cost budget | pre-plan、pre-claim、post-usage reconcile | 计划前估算，claim 前检查，usage 回写后扣减 |
| step count | pre-plan、pre-claim | 防止 planner 无限续跑 |
| unit range | pre-plan、pre-execute、pre-publish | job 不能越过授权 units |
| retry budget | retry scheduler | transient、repair、replan 分开计数 |
| concurrency | job claim | per-record、per-user、per-worker 都要检查 |
| context scope | tool/input builder | Ask 和 RAG 不能无授权读取 Original Input |
| user editorial asset write | pre-write-asset | AI 不能未确认写笔记、高亮、生词等 User Editorial Assets |

`reader_runs.envelope_json` 保存 immutable envelope snapshot。D4 可以把运行期 counters 放在 run state 字段或 usage 聚合中；独立 counter 表是 D5+ 优化，不阻塞最小纵切。

## D3 Runtime Baseline

D3/D4 runtime baseline：

- `reader_runs`：run status、record generation、immutable envelope snapshot、trigger/audit metadata。
- `reader_jobs`：lease、retry budget、expected generation、operation fingerprint、input/output/error metadata。
- `reader_events`：UI domain events 与 projection events。
- `reader_event_sequences` 或等价 record-scoped transactional counter。
- `reader_job_events`：claim、heartbeat lost、retry、diagnostics，不进入 SSE。
- `enhancement_layers`：Layer Publisher 的 CAS winner。

D4 orchestration integration：

- `ReaderOrchestrator.submit_plain_text_and_bootstrap_translation()` 是后端 D4 submit facade：先复用 `ArticleReadyPersistenceService` 创建 record/base/unit/anchor/event facts，再创建 display title run/job 和第一条 translation run/job。方法名保留历史命名，runtime contract 已要求中文标题 job 先于 translation job 进入队列。
- `ReaderOrchestrator.tick_translation_worker()` 是 D4/D5 最小 worker tick：复用 `TranslationWorkerService` claim/process/publish；translation publish 成功时，publisher transaction 同时写最小 `parsed_decisions` 并发布 `parsed_decision_updated` event。
- `TranslationWorkerRunner` 是 D4 内部 callable runner：封装 single tick 与 bounded drain，用 `WorkerDrainResult` 汇总 success / retry / terminal failure / fence rejection。
- D4 tick 是 service/testable entry，不是公开 HTTP endpoint。若后续暴露内部 route，必须补 worker auth、权限边界和 focused tests。
- D5-G1 已把 translation parsed decision 收敛到 layer publish transaction。`diagnose_orphaned_translation_decisions()` 继续保留，用于发现 pre-D5 遗留数据或测试中人为制造的 partial state；snapshot reload 不负责 repair。
- Runner drain 遇到 retry / terminal failure / fence rejection 不停止，因为队列中可能仍有其他可处理 job；调用方根据 aggregate result 决定是否再次 drain、告警或进入 repair。

不引入：

- External MQ / Temporal / DBOS / Prefect runtime。
- 独立 outbox 表；`reader_events` 在 D4 承担简化 outbox。
- DLQ 表；retry exhausted 进入 terminal failure，D5+ 再评估 DLQ。

## D2 Spike

D2 需要验证：

- PostgreSQL job lease、heartbeat、stale recovery。
- idempotent resume 与 publish guard。
- projection emitter 与 domain publish 的事务边界、幂等 replay 和失败恢复。
- cancel / supersede 后 worker late result 被正确拦截。
- deterministic planner 是否足以支撑 D4。
- 是否确实需要 LangGraph。

## Observability

reader_orchestration 的可观测性采用 PG span 为主、LangSmith 为 dashboard 的双轨设计。

### `reader_runtime_spans` 表契约

PG `reader_runtime_spans` 是 actor 链路 span 树的事实源（Temporal / Cadence history table 模式），单表覆盖一次 record 从 submit 到 publish 的端到端链路。schema 详见 `infra/migrations/0014_reader_runtime_spans.sql`。

Span kind（与 migration CHECK 约束对齐）：

| span_kind | 触发点 | 说明 |
|---|---|---|
| `pipeline_root` | `ReaderOrchestrator.submit_plain_text_and_bootstrap_translation` | 一次 submit 的根 span，承载 `trace_id` |
| `claim` | `ReaderJobRuntime.claim_next_job` | SKIP LOCKED claim，含 `claim_wait_ms` |
| `worker_tick` | `pipeline_runner._dispatch_worker_attempt` | 单 worker tick，LLM token / model 字段也合并到此 span（通过 `end_worker_span_success`） |
| `publish_fence` | `TranslationLayerPublisher.publish_unit_*` 三个 wrapper | 发布 fence + DB write 阶段 |

Status：`started` / `succeeded` / `failed` / `superseded` / `skipped`。

Retry class（gap report #4 retry budget transparency）：`transient` / `repair` / `replan`，优先级 replan > repair > transient，由 `derive_retry_class()` 推导。

Claim contention（gap report #2）：`claim_wait_ms` 字段在 `claim` span 上记录 SKIP LOCKED 等待耗时。

### LangSmith 双轨链接

- **PG 为事实源**：所有 actor 边界 span 都写入 `reader_runtime_spans`，含 status / duration / token / model_name / failure_class 等字段。
- **LangSmith 为 dashboard**：通过 `langsmith.integrations.otel.configure()` + `Agent.instrument_all()` 自动收集 PydanticAI LLM spans，含 GenAI 语义约定的 token / model / cost。
- **链接字段 `langsmith_run_id`**：采用 `"<trace_id>/<span_id>"` 复合格式，方便 Console deep-link 到 `https://smith.langchain.com/runs/<trace_id>/r/<span_id>`。

### `LangSmithIdBridgeProcessor` 与 ContextVar 桥接

`services/api/app/observability/langsmith_span_processor.py` 的 `LangSmithIdBridgeProcessor` 在每个 PydanticAI LLM span 的 `on_end` 时读取 `langsmith.trace.id` / `langsmith.span.id` attributes，写入 `contextvars.ContextVar`。`ReaderSpanRecorder.end_span` 在调用方未显式传 `langsmith_run_id` 时通过 lazy import `get_current_langsmith_ids()` 自动回填。这样无需在每个 worker 调用点显式串接 LangSmith run id。

processor 在 `_configure_pydantic_ai_otel` 中通过 `tracer_provider.add_span_processor()` 挂载，并用 `_claread_bridge_attached` 标志保证幂等（防止 `setup_langsmith` 多次调用造成重复注册）。

### publish_fence span 与 reading_record_id

`publish_fence` span 在 `TranslationLayerPublisher.publish_unit_*` wrapper 入口处启动，**早于** publisher 事务内 `SELECT reader_jobs` 读取 `reading_record_id`。因此 `reader_runtime_spans.reading_record_id` 列为 **NULLable**，publish_fence span 行的该字段为 NULL。Console 应通过 `trace_id` 或 `reader_job_id` 查询 publish_fence span，不应假设 `reading_record_id` 一定有值。

其他 span kind（`pipeline_root` / `worker_tick` / `claim`）在启动时已知 `reading_record_id`，正常写入。

### Console endpoint 契约

Directus endpoints-bundle 的 `parse-run-observability/reader-orch.js` 已实现 4 个 SQL endpoint：

- `GET /reader-orch/trace/:trace_id` — 按 trace_id 查询完整 span 树
- `GET /reader-orch/run/:run_id` — 按 reader_run_id 查询并 LEFT JOIN `ai_usage_events` 取 billing 信息
- `GET /reader-orch/record/:record_id/summary` — 按 reading_record_id 聚合 worker_type 维度的 latency / token 统计
- `GET /reader-orch/worker/:worker_type/summary` — 按 worker_type 跨 record 聚合

Console 前端从这些 endpoint 取数据渲染 latency / token / model cost heatmap 与 span tree 可视化。`langsmith_run_id` 字段作为 Console 跳转 LangSmith trace UI 的链接源。

### T4.2a-R2 Budget Diagnostics 持久化

T4.2a-R2-R2 把 budget diagnostics 持久化到 `reader_runtime_spans.metadata_json`（pipeline root span），任务结束后可从 Console / runtime spans 查询：

| 字段 | 含义 |
|---|---|
| `budget_denied` | 预算拒绝次数（executor/LLM 调用前；与 `no_job` 可区分） |
| `exhausted_layers` | 已耗尽 layer 列表 |
| `budget_diagnostics` | per-layer planned / max / consumed / remaining |
| `stopped_reason` | `budget_exhausted`（全 layer）/ `partial_budget_exhausted`（部分 layer）/ 其他 finalizable 原因 |

普通 `no_job` 场景 `budget_denied == 0`，与 budget-denied 场景可区分。WorkerLoop 结构化日志同步写入这些字段。

详细约束与不可违反决策见 [`../agent-brief.md`](../agent-brief.md) T4.2a-R2-R2 / R2-R1 系列；任务状态见 [`../implementation-plan.md`](../implementation-plan.md) T4.2a-R2 章节；决策记录见 [`../target-architecture.md`](../target-architecture.md#决策记录) `T4.2a-R2` 行。
