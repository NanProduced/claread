# Reader Agentic Orchestration 执行简报

> 状态：`权威简报（Architectural Cutover Complete）`
> 最后更新：2026-08-03（CUTOVER-DOC-TRUTH-CLOSEOUT-R1：状态同步至 Architectural Cutover Complete；正文历史里程碑中出现的 `/app/reader-plate`、`/app/reader-record/{recordId}`、`/api/web/reader-plate/*` 等为 cutover 前 URL，cutover 后统一为 `/app/read`、`/app/reader/[recordId]`、`/api/web/reader/records/*`。）
> 前次更新：2026-07-17（T5.3b-P1：对齐 T5.2a/T5.3 semantic outline durable 已闭合；T4.2a 历史事实不变）

给 coding agent 分配 Reader agentic orchestration 重构任务时，使用本简报作为最小上下文。

## 必读顺序

1. `AGENTS.md`
2. `RTK.md`
3. `docs/initiatives/reader-agentic-orchestration/README.md`
4. `docs/initiatives/reader-agentic-orchestration/target-architecture.md`
5. `docs/initiatives/reader-agentic-orchestration/concepts.md`
6. `docs/initiatives/reader-agentic-orchestration/adaptive-reader-orchestration-design.md`
7. 当前任务涉及的 `docs/initiatives/reader-agentic-orchestration/modules/*.md`
8. D2 spike 任务读取 `docs/initiatives/reader-agentic-orchestration/spikes/README.md`
9. `docs/initiatives/reader-agentic-orchestration/implementation-plan.md`
10. 涉及代码目录最近的 `AGENTS.md`

除非任务明确要求研究回溯，不要读取 `docs/initiatives/reader-agentic-orchestration/tmp/` 下的过程材料。

## 任务目标

把用户提交内容的 `learning` 解析，从固定 AI Workflow 重构为 bounded, adaptive Reader orchestration。

产品对象是 `Reading Record`，不是 workflow run。

当前新增设计入口是 `adaptive-reader-orchestration-design.md`。短期实现应先恢复短文质量/成本和稳定发布；完整 adaptive planner、SSE/patch merge、长文/超长文 lazy enhancement 应按设计文档分阶段推进，不要混进一个任务。

2026-07-17 当前状态：M0 baseline harness、M1 short article recovery、T3.1 non-short translation grouped execution、T3.2b non-short vocabulary grouped execution、T3.3 phrase_gloss guard、T3.4a grammar diagnostics、T3.4b RECORD_DENSITY denominator fix、T3.5 completion finalizer、T4.1/T4.1a deterministic complexity routing、T4.1b structured article batch runtime mode、T4.1c short/medium compact grammar path、T4.2a-R1 evidence/observability closure 与 T4.2a-R2 execution budget/cutover safety 已完成代码级实施和 deterministic acceptance。**T4.2a-V1 已正式关闭**：4 个真实 records 覆盖三种 route（34 calls / 198,041 total tokens）；Contract、Output Integrity、样本级 Semantic Quality 与 Page UX Gate 通过；Cost/Latency Baseline PARTIAL。Page UX 在 baseline commit `760402c2c` clean worktree Web 上完成 final-ready 验收（4/4 页面、33/33 严格 interaction 断言、GROUPED 中后段联动、刷新/滚动/readiness/console-network），页面验收阶段无新 LLM 调用。没有历史同样本真实 LLM 对照，不得宣称已实现降本或时延降幅。**T4.2a-PUX-R1** = Progressive Transition **fixture 合同**已闭合；**T4.2a-PUX-R2** = **runtime 集成门**已闭合（page polling/reload 经 progressive 单调校验；cursor hold；status strip；scroll 保留；无 LLM）。**T5.2a** validation contract（`2bf3db97`）与 **T5.3** semantic outline durable worker/publisher（`781e4117`）已闭合：复用 `enhancement_layers`（`layer_type='semantic_outline'`，record/`document`）；默认 request eligibility=false；无真实 LLM 生产 executor；**未**挂 `ReaderPlateSnapshot`；**未**交付用户可见 L2 outline。outline 仍是 long/very-long optional enhancement 定位；不冻结 eligibility 数值阈值、L2 IA、partial 混排。导航相关下一步是 **T5.4-R0** snapshot projection 只读设计门——不是直接做 UI、不是默认全量生成、不是 SSE/patch。完整 adaptive planner、SSE patch merge 和 grammar quality tuning 仍要分任务推进，不能与 outline snapshot/UI 混成一个无边界任务。

V1 证明 route、job topology、publish/readiness contract、样本级批注质量与 final-ready 页面投影/交互在当前模型配置下可工作；它没有触发 retry、budget exhaustion 或 route cutover，相关 failure-path 仍以 T4.2a-R2 deterministic tests 为权威。实际 provider 成本不可从账单确认，理论区间约 `$0.0158-$0.0354`；`ai_usage_events.latency_ms` 缺失且没有前端用户感知埋点。Sample A grammar 的 0-token usage attribution 为 unresolved intermittent gap，不得描述为已有 worker 修复。T4.2 bounded LLM document profiler 继续暂缓，只有 deterministic router 在真实边界样本上出现稳定误判时再评估。

**T4.2a-V2-R1 已完成（deterministic）**：碎段新闻 `SHORT_BATCH`（Translation Group/anchor 完整）、STRUCTURED 边界独立 fingerprint/policy、>4000 words `GROUPED_WINDOWED` multi-window（translation/vocabulary `:window:`；grammar `target_key == input_json.window_id`）+ reading-order publish、empty grammar window → `no_op`/`llm_empty`/`attempt_count=1`/无重复 LLM 调用/`completed_with_no_op`。5 focused tests 通过；无生产代码改动、无真实 LLM。真实 LLM 质量/成本与 Page UX 仍后置。

**T4.2a-O1**、**T4.2a-O2-V1-R1** 已关闭；**T4.2a-O3** 代码级完成 duration provenance：`agent_run_duration_ms` 仅表示本地 `agent.run` 单调时钟耗时；`provider_request_duration_*` **仅**在专用 adapter envelope（`_claread_provider_response_timing`，kind/version 校验）下为 `available`，任意 usage/通用 timing 同名字段保持 `unavailable`。禁止把 worker_tick `duration_ms`、pipeline wall 或 agent-run duration 命名/写入为 provider latency 或改写 `ai_usage_events.latency_ms`。统一 `run_reader_scoped_agent` 继续承载 correlation + duration。Sample A 仍 **UNRESOLVED**；Cost/Latency 仍 **PARTIAL**。

验收节奏：短文、长文、超长文三种模式先分别完成代码级合同闭环，再统一真实 LLM / 页面验收。中间实现阶段优先使用 deterministic tests、fake executor、recorded LLM response 和 DB contract checks；不要每修一个局部就反复真实跑长文/超长文。

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
- Translation Group 是产品阅读体验合同，不是 worker 调度单位。Batch/window/longform 只能改变计算形态，不能把 group-native translation 退化成 one-anchor-one-group、one-sentence-one-group 或 one-unit-one-group。
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
- D5-V3 Real Vocabulary Executor / Prompt 已完成并通过 review。`reader_layer_vocabulary` route 必须显式配置 `reader_vocabulary_model_profile`，不得 fallback 到 annotation profile；LLM 只输出内部 candidate schema，后端确定性解析 `anchor_segment_id + selected_text` 为 unit-local UTF-16 offsets/hash。
- D5-V3 vocabulary real executor 对同一 span 按 `context_gloss > phrase_gloss > vocab_highlight` 仲裁；candidate items 和文本字段有硬上限；空有效 output 可以发布，但 diagnostics 必须保留跳过原因。
- D5-V4 Grammar Bundle Backend Slice 已完成并通过 review。Grammar bundle 使用正式 `reader_jobs.job_type = 'build_grammar_bundle'`，发布时拆成 `grammar_note` 与 `sentence_analysis` 两个独立 layer rows；usage 只记一条 job-level attribution，no-op success 不发布 layer/event。
- D5-V4 fallback policy：`grammar_note` 任一 span 命中 `fallback_window` 时整条 item 跳过，不允许部分保留 span；`sentence_analysis` 命中 fallback window 时跳过。
- D5-G2 boundary policy：vocabulary worker 与 grammar bundle 统一 fallback_window 口径 — `segment_type=fallback_window` 的 anchor segment 不产出 vocabulary item，reason_code `boundary_low_fallback_window` 写入 `diagnostics.skipped_items[]`，与 grammar 一致。
- D5 Vocabulary Eval Seed 评估结论是 `accepted_with_changes`。下一步只做本地 deterministic seed/schema/graders/tests；不得按 JSONL 单文件方案落地，不接 LangSmith，不新增 `evals/claread_eval/judge/judges/*` prompt catalog。vocabulary `boundary_low_fallback_window` 已成为 D5 eval seed acceptance gate（fixture `14-vocab-fallback-window-skip`）。
- D5 LangGraph / orchestration 双评估结论是 `accepted_with_changes`：D5 主链路 runner、workers、snapshot projection 和 eval 不引入、不升级 LangGraph；LangGraph 不得替换 PostgreSQL run/job/event/layer durable control plane；D6-LG0 只作为隔离 spike 候选，且必须有具体 Ask HITL / multi-branch repair 需求。
- D5-R2 Main Chain Runner + Web Record Load 已完成并通过 focused tests。`ReaderEnhancementPipelineRunner` 统一 bootstrap/drain translation、vocabulary、grammar bundle jobs；runner 使用 record-scoped claim，不会消费其他 Reading Record 的 queue；Web `/app/reader-plate?record_id=...` 可直达加载 snapshot。
- D5-R2 不包含页面 submit 后同步跑完整真实 LLM、SSE、LangGraph 或 `projection_ops` incremental applier。
- D5-W1 worker loop 评估结论为 `accepted_with_changes`：正式运行形态应是独立 worker process，本地用 CLI entrypoint，部署用独立 worker service；不得挂到 FastAPI lifespan，不得塞进 Web submit，不得新增 public worker-control endpoint，不得把 smoke harness / fake executors 作为产品路径。
- Worker loop 扫描候选 record 时，粗筛可用 `product_state in ('processing','readable_enhancing')`、active base/generation/status 和 `readiness_state in ('article_ready','initial_enhancement_ready')`；exact missing work 仍由 `EnhancementJobBootstrapService` / `ReaderEnhancementPipelineRunner` 决定。
- D5-W2 worker loop 已完成：`ReaderEnhancementWorkerLoopService` 通过 coarse scan + per-record / per-user advisory locks 调用 `ReaderEnhancementPipelineRunner`，CLI `scripts/run_reader_enhancement_worker.py` 支持 `--once` 与持续 loop。
- D5-R3 真实本地链路 runbook 已补充：见 `docs/initiatives/reader-agentic-orchestration/modules/local-real-chain-runbook.md`。当前只验证 worker CLI help / 参数解析和配置连线，未实跑真实 provider / LLM。
- 2026-07-08 M0/M1 自适应解析恢复已完成首轮调度实现：`compare_reader_chains.py` baseline harness、golden samples、short article translation/vocabulary batch path、grammar window strategy 注入、grammar/sentence budget key 对齐已落地；默认 worker/baseline budget 为 `max_ticks=96`、`max_jobs=48`。
- T1.1a 已完成并通过本轮代码层与页面抽查验收：短文 `translate_article` batch path 使用后端 `plan_translation_groups` 规划连续 semantic Translation Groups，后端 hydrate `group_id` / `source_text_hash` / `source_text`，translator 只返回 `group_id` + `translated_text`。M2 stable progressive delivery、T3 grouped execution 可按 `implementation-plan.md` 单项推进。**T5.1** L0/L1 deterministic navigation 已闭合；**T5.2a**（`2bf3db97`）+ **T5.3**（`781e4117`）semantic outline **durable backend 已闭合**（默认 eligibility=false；无真实 LLM 生产 executor；未进 `ReaderPlateSnapshot`；用户可见 L2 outline 未交付）。下一步 **T5.4-R0** snapshot projection 只读设计门，不是 UI、不是默认全量生成、不是 SSE/patch。完整 adaptive planner 与 SSE patch merge 仍分任务推进。
- 2026-07-09 长文抽查结论已被后续任务吸收：T3.1 已把 non-short translation 从 per-unit fan-out 改为 grouped/windowed `translate_article`；T3.4a 已补 grammar diagnostics；T3.4b 已修 RECORD_DENSITY denominator。后续重点从"修当前 bug"转为完成 short / long / very-long 三模式合同、评估 bounded enhancement planner + specialized structured workers，并持续用 token/耗时/质量数据验证。
- D4 worker 实现中不得临时升级 PydanticAI、LangGraph、LangSmith 或 provider SDK；如 D3-P4 runtime tests 暴露缺口，先形成单独 closeout/update，再改依赖。
- LangGraph v1+ 的 persistence、streaming、interrupt/resume、subgraph 和 runtime observability 只作为 D6+ 隔离 spike 候选；具体版本能力和 breaking changes 必须在 spike 中用当时官方文档与 lockfile 实测确认，不改变 PostgreSQL run/job/event 主控。
- Grammar Bundle Worker 可以一次生成 `grammar_note` 与 `sentence_analysis`，但发布、存储、RAG、projection、policy、eval 必须按 subtype 独立处理。`long_sentence` 不是权威 layer type，只是触发 `sentence_analysis` 的适用场景。
- Vocabulary Worker 必须保留旧 workflow 的三类 item subtype：`vocab_highlight`、`phrase_gloss`、`context_gloss`。它们属于同一个 `vocabulary` layer 的 `output_json.items[].item_type`，不是三个顶层 layer type。
- T4.2a-R2 执行预算与切换安全护栏（T4.2a-R2-R3 修复已实施，已通过代码级 review）：per-layer `ExecutionBudget` 是 **durable** 跨 `runner.run()` 持久化的成本上限（不再 in-memory per-run 清零）；每次 `runner.run()` 入口通过 `ExecutionBudget.load_durable()` 从 `reader_jobs` 聚合 `SUM(attempt_count)` / `MAX(max_attempts)` per `(record, base, generation, layer)` 重建；`max_effective_calls = planned_calls * max_multiplier`，默认 `max_multiplier=3` 与生产 `max_attempts=3` 对齐（首轮 `*2` 已修正）；durable budget 提供确定性上限和观测/调度作用，**本身不降低原有 retry 成本**；是否调低 `max_attempts` 是独立数据驱动决策；`BUDGET_CONSUMING_OUTCOMES = {succeeded, retry_later, failed_terminal}` 消耗预算，`superseded / no_job / skipped / budget_denied` 不消耗；预算耗尽时 `stopped_reason = budget_exhausted`（全 layer）或 `partial_budget_exhausted`（部分 layer），pipeline 停止该 layer 的 worker dispatch；`display_title` 不纳入预算；未新增 migration。
- T4.2a-R2-R2 fingerprint 模型（方案 B：保守排序集合）：现有 `reader_jobs` schema 无法可靠确定唯一 active fingerprint；`load_durable()` 不再使用 last-wins active fingerprint，改为返回排序后的 `non_superseded_fingerprints` 集合 per layer；预算保守聚合所有非 superseded fingerprint 的 `attempt_count`；SQL 添加 `ORDER BY operation_fingerprint ASC` 保证稳定；`to_diagnostics()` 暴露 fingerprint set；不新增 schema/migration。
- T4.2a-R2-R1 fallback guardrail（fail-closed 修正）+ T4.2a-R2-R2 legacy job 正式终态：`_should_suppress_grammar_per_unit_fallback` 不再以"终态"作为 fallback 充分条件；只有 batch job 为 `superseded` 或不存在 batch job 时允许 per-unit fallback；`succeeded` / `failed_terminal` / `skipped` / 任何非终态 batch job 均抑制 fallback。本轮 fail-closed：batch `failed_terminal` 不隐式运行遗留 per-unit job。translation / vocabulary 无等效 guard，因为 T3.1/T3.2b 已删除 per-unit fallback 路径。**T4.2a-R2-R2 新增**：`_cleanup_suppressed_grammar_legacy_jobs()` 在每次 `run()` 入口 bootstrap 后执行一次，正式 supersede 冲突 legacy grammar job（`repository.supersede_conflicting_legacy_grammar_jobs()`），写入 `reader_job_events`（event_type=`job_superseded`）+ rationale_code（`batch_path_authoritative` / `batch_fallback_not_authorized` / `stale_legacy_topology`）。不再留下永远不会执行的 queued/retry_later legacy job，避免 WorkerLoop scanner 热循环。
- T4.2a-R2 route flip fencing + T4.2a-R2-R2 publish fence 状态一致性：bootstrap supersede（已有）+ claim-time `_validate_fence` → `_check_route_consistency`（对比 `reader_jobs.input_json.article_route` 与 `reader_runs.envelope_json.article_route`，mismatch 返回 `stale_route_fingerprint`）+ publish-time 同一 `_validate_fence`（所有 6 个 publisher 方法）。旧 fingerprint job 不可 claim、不可 publish。**T4.2a-R2-R2 新增**：publisher raise `FenceViolationError` 后，worker/service 层（持有 claim/lease_token）调用 `ReaderJobRuntime.transition(job_id, target_status="superseded", rationale_code="publish_fence_failed")`，对应 reader_run 标记 superseded。pipeline summary 只统计 DB 中真实 superseded transition，**移除所有 `max(1, superseded_jobs)` 虚报**（改为 `max(0, ...)`）。translation/vocabulary/grammar 的 unit/batch/window 路径统一此契约。
- T4.2a-R2-R2 partial exhaustion 分层 force-fail：`budget_exhausted` 和 `partial_budget_exhausted` 均为 finalizable stopped reason（`NON_FINALIZABLE_STOPPED_REASONS` 只含 `attention_required`）；finalizer 读取 durable state（terminal job counts），不依赖 in-memory budget；**T4.2a-R2-R2 修正**：finalizer 只对 `summary.exhausted_layers` 对应的 job types 调用 `force_fail_non_terminal_jobs`（通过 `BUDGET_LAYER_TO_JOB_TYPES` 映射），不再无差别 force-fail `ENHANCEMENT_PIPELINE_JOB_TYPES`；`display_title` 不在 budget layer 映射中，不被误伤；非 exhausted layer 保留非终态 jobs，若仍有非终态 jobs 则返回 `non_terminal_jobs_present` 不提前 finalize；只有所有计划工作达到真实 terminal 状态后才进入 `coverage_complete`；`completed_with_failures` 准确表示哪些 layer 因预算失败。
- T4.2a-R2-R1 budget-denied 可观测性 + T4.2a-R2-R2 持久化：预算拒绝发生在 executor/LLM 调用前；denied 记为独立 `budget_denied` outcome（不是 `no_job`），计入 `outcome_counts["budget_denied"]` 和 `round_no_job_count`；`ExecutionBudget.to_diagnostics()` 接入 pipeline summary 的 `budget_diagnostics`（per-layer planned/max/consumed/remaining），`exhausted_layers()` 暴露已耗尽 layer。**T4.2a-R2-R2 新增持久化**：`budget_denied`、`exhausted_layers`、`budget_diagnostics`、`stopped_reason` 写入 `reader_runtime_spans.metadata_json`（pipeline root span）和 WorkerLoop 结构化日志，任务结束后可从 Console/runtime spans 查询；普通 `no_job` 场景 `budget_denied == 0`，与 budget-denied 可区分。
- T4.2a-R2 测试约束：不得通过 test-local worker subclass 或临时改写数据库 fingerprint 模拟成功；测试使用确定性 fake executor + call counter，不调用真实 LLM。publish fence 测试必须经过真实 publisher + worker/pipeline catch，不能只调 `_validate_fence`。partial exhaustion 测试必须经过 WorkerLoop + Finalizer。budget diagnostics 测试必须查询持久 `reader_runtime_spans.metadata_json`，不只检查 summary 返回值。
- T4.2a-R2-R3 review fix（5 项，已通过代码级 review）：
  - P1-1 Publish fence transition：Worker 层（translation_worker / vocabulary_worker / grammar_worker）在各自 `except FenceViolationError` handler 中已执行真实 `transition(job_id, target_status="superseded")` 和 `_mark_run_status(run_id, status="superseded")`；pipeline runner 只统计 DB-actual delta。已补 clarifying comments。
  - P1-2 Test J strong assertions：查询特定 job_id/run_id，断言 job.status == "superseded"、rationale == "publish_fence_failed"、run.status == "superseded"、summary.superseded >= 1、layers_after == layers_before（==，非 >=）、events_after == events_before（==，非 >=）。
  - P1-3 Full exhaustion excludes display_title：`budget_exhausted` 和 `partial_budget_exhausted` 均使用 `BUDGET_LAYER_TO_JOB_TYPES` 进行 force-fail；不再使用 `ENHANCEMENT_PIPELINE_JOB_TYPES`（包含 `generate_display_title_zh`）做 force-fail。
  - P2-1 Legacy cleanup explicit transaction：`_cleanup_suppressed_grammar_legacy_jobs()` body 包裹在 `async with conn.transaction():` 中，使 SELECT FOR UPDATE lock 覆盖整个 cleanup。
  - P2-2 Test G/I strengthened：Test G 断言 `finalized is True` + DB readiness == coverage_complete；Test I 断言 translation == failed_terminal、vocabulary == succeeded、DB readiness == coverage_complete。
- T4.2a-R2-R3a（display-title regression + 文档终态同步，已通过代码级 review）：
  - 新增 Test M `test_m_full_budget_exhaustion_preserves_display_title`：全预算层 exhaustion 时 display_title 不被误伤。第一次 WorkerLoop：display_title 保持 retry_later、attempt_count 不增加、无 budget_exhausted failure_code；finalizer `finalized=False, skip_reason=non_terminal_jobs_present`；readiness ≠ coverage_complete。第二次 WorkerLoop：display_title succeeded、attempt_count +1；budget 层保持 failed_terminal；finalizer `finalized=True, outcome=completed_with_failures`；readiness == coverage_complete；completion event `completion_outcome=completed_with_failures`。
  - 文档终态同步：T4.2a-R2 状态从 "review changes required" 更新为 "代码级 review 通过 / deterministic acceptance complete / real LLM validation pending"；测试数量更新为 35 focused + 94 combined；清理 R2-R1/R2-R2/R2-R3 旧状态漂移。
  - 测试结果：35 passed (test_execution_budget_cutover_safety.py) + 94 passed (8 文件组合回归)；Ruff clean；git diff --check clean。
  - 真实降本口径：durable budget formalizes `max_attempts=3` 确定性上限，不降低当前 retry ceiling；实际 token/latency/annotation quality 改善属于下一阶段 gated validation。
- T4.2a-V1 gated real-LLM + Page UX checkpoint（**closed**）：
  - 4 个 records 覆盖 `SHORT_BATCH`（2）、`STRUCTURED_BATCH`（1）、`GROUPED_WINDOWED`（1），共 34 calls / 142,990 input / 55,051 output / 198,041 total tokens；全部 jobs 首次成功，终态 `coverage_complete / completed_clean`，无 retry、duplicate fallback、stale publish、superseded residue 或 stuck lease。
  - Gate：Contract PASS；Output Integrity PASS；Semantic Quality PASS（仅代表本轮人工抽查样本）；**Page UX PASS**；Cost/Latency Baseline PARTIAL。实际 provider 账单 unavailable、无同样本旧链路真实对照，不得宣称实际降本增效。
  - Page UX：baseline commit `760402c2c` clean worktree Web；4/4 final-ready 页面；33/33 严格 interaction 断言；GROUPED 中后段 vocabulary/grammar/sentence 联动；刷新/滚动/readiness/console-network 通过；页面验收无新 LLM 调用。
  - Vocabulary 按 subtype 合同评估：`vocab_highlight` 使用 `headword`，`phrase_gloss` 使用 `phrase/gloss`，`context_gloss` 使用 `display/gloss/reason`；不得跨 subtype 检查不存在字段。
  - Sample A grammar usage event 为 0 tokens 的根因 unresolved；`extract_run_usage` 在 V1 前已存在，不能表述为当前 worker 已修复。`ai_usage_events.latency_ms` 全部 NULL，per-job provider latency 不可用。
  - **T4.2a-PUX-R1 fixture 合同 + T4.2a-PUX-R2 runtime 集成均已闭合**；**T4.2a-V2-R1 边界固定样本已 deterministic 完成**；T4.2 profiler 继续暂缓。
- T4.2a-O1 observability audit 合同（**closed / read-only**）：
  - 必须解释 Sample A grammar 0-token、`latency_ms` 缺失、cache hit/miss 字段分散、可靠账单与用户感知时间不可得；只陈述代码/DB 可证明事实，未知根因保持 unresolved。
  - 区分 provider effective call 与 job claim attempt；区分 estimated/billed cost；区分 provider latency、worker duration、pipeline-root duration、first-layer/per-layer/coverage-ready 和 browser-perceived latency。
  - 交付字段 lineage、可计算性矩阵、四个 V1 records 只读 evidence、最小 instrumentation slices 与 deterministic test 建议；不得在审计任务中顺手实施。
- **T4.2a-O3 已代码级完成**（deterministic）：duration provenance metadata schema v1；无 provider timing 时 `provider_request_duration_status=unavailable`。后续可选 cache pricing / estimated cost；Progressive UX 已由 **PUX-R1/R2** 分 fixture 与 runtime 闭合；不做 profiler/planner 混装，不因 O3 宣称 latency 已可靠。
- **T4.2a-V2-R1 已代码级完成**（deterministic）：`tests/test_reader_orchestration_v2_boundary_samples.py` 5 passed；碎段新闻 SHORT_BATCH + Translation Group/anchor；STRUCTURED 独立 fingerprint/policy；>4000 words GROUPED multi-window + reading-order；no-op grammar window 终态/预算/无重复 LLM。无生产代码改动、无真实 LLM；过程 TMP 已在 DOC-R3 删除。
- **T4.2a-PUX-R1 = fixture contract closed**（deterministic pure）：`progressive-transition.ts` + 21 tests；canonical replay 与 stale/layer 单调 helpers。**不单独作为 runtime 验收**。
- **T4.2a-PUX-R2 = runtime integration gate closed**：`reader-record` page `reloadSnapshot` 经 progressive 校验才应用 snapshot；stale/layer regression 不覆盖 UI 且 cursor hold；底部 progressive status strip；Plate generation-scoped clear + scroll restore；4 page integration tests。无 LLM、无后端 orchestration。过程 TMP 已在 DOC-R3 删除。

## 2026-07-13 阶段检查点

原 T4.2a 实现阶段约 **85%**：实现与合同层已闭合；Sample A grammar attribution 仍 UNRESOLVED，Cost/Latency 仍 PARTIAL，rejected-snapshot retry/backoff 待设计。**T4.2a-PUX-R3** 已关闭 Reader Plate 测试卫生（仅测试读取的 CRLF/LF 归一化；全套 Web Vitest 67 files / 958 tests 通过）。**T4.2a-LP-R1** 长内容 snapshot 传输研究已闭合；下一步唯一预批准动作是 **T4.2a-LP-R2 Phase 0 payload profiling**，先测 payload/编码/时序/reload，再决定是否进入 ETag、压缩、fragment 或前端渲染实验。旧的 bounded-LLM profiler 继续暂缓。
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
