# Orchestration Runtime

> 状态：`D1 草案`
> 最后更新：2026-06-18
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

## D4 Framework Posture

D4 默认收窄：

- Planner 先用 deterministic policy function：`plan(state, envelope) -> typed plan`。
- PydanticAI 用于 LLM-backed workers，例如 translator、annotator、judge。
- LangGraph 不作为 D4 默认依赖；D5+ 如需要 branching、interrupt 或复杂 repair flow，再做单独引入和版本评估。

这样避免 Planner、LangGraph、PydanticAI 三个控制面重叠。

Planner、Skip Gate、Model Profile、Prompt Cache 和 Usage Bucket 的细节见 `policy-and-cost-control.md`。本文件只定义 runtime 执行边界。

后端依赖版本必须在 D3 runtime skeleton 前完成 alignment spike。D4 不应在实现 Translation Worker 时临时升级 PydanticAI、LangGraph、LangSmith 或 provider SDK。

## LangGraph 1.x 评估结论

当前 D4 不以 LangGraph 作为主控 runtime。LangGraph 1.0 官方定位是稳定性发布，核心 graph primitives 和 execution model 基本不变；主要迁移点是 prebuilt agent 相关 deprecation。1.1 / 1.2 新增的 typed streaming、typed invoke、per-node timeout、node-level error handler、graceful shutdown、DeltaChannel 等能力，对 D5+ 的复杂 repair、branching、interrupt 或长会话 checkpoint 优化有价值，但不是 D4 的最小硬依赖。

D4 仍以 PostgreSQL tables 持久化 business state 和 worker state：

- `reader_runs` / `reader_jobs` 是 run、lease、retry 和 budget 的权威。
- `reader_events` / `reader_event_sequences` 是 UI catch-up 的权威。
- PydanticAI 只负责单个 LLM-backed typed worker 调用。

D3-P0 不主动升级 LangGraph。若 D5+ 决定使用 LangGraph 1.x，必须单独 spike：

- checkpoint 与 Claread `reader_runs` / `reader_jobs` 的边界。
- interrupted / resumed graph state 与 record generation fence 的兼容。
- node rename / state schema 变更的 backward compatibility。
- graph streaming 与 `reader_events` / Plate projection event 的关系。

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

Base-scoped jobs 必须携带 `base_id`。只有 `build_base` 这类 base 尚未存在的 record-level job 可以 `base_id = null`。`operation_fingerprint` 和 active job unique key 必须包含 `base_id + expected_generation`，避免 supersede 或重新冻结 base 后 late worker result 被误发布。

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

- `ReaderOrchestrator.submit_plain_text_and_bootstrap_translation()` 是后端 D4 submit facade：先复用 `ArticleReadyPersistenceService` 创建 record/base/unit/anchor/event facts，再复用 translation bootstrap 创建第一条 translation run/job。
- `ReaderOrchestrator.tick_translation_worker()` 是 D4 最小 worker tick：复用 `TranslationWorkerService` claim/process/publish，成功后写最小 `parsed_decisions` 并发布 `parsed_decision_updated` event。
- `TranslationWorkerRunner` 是 D4 内部 callable runner：封装 single tick 与 bounded drain，用 `WorkerDrainResult` 汇总 success / retry / terminal failure / fence rejection。
- D4 tick 是 service/testable entry，不是公开 HTTP endpoint。若后续暴露内部 route，必须补 worker auth、权限边界和 focused tests。
- D4 parsed decision 写入暂在 layer publish 后的独立事务。单线程 tick 下可接受；`diagnose_orphaned_translation_decisions()` 可检测 published translation layer 缺失 parsed decision 的异常状态。若未来要求 layer 与 parsed decision 强一致，应把 decision 写入收敛到 publisher transaction 或补 repair/diagnostic 策略。
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
