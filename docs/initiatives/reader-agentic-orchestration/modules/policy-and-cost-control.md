# Policy 与 Cost Control

> 状态：`D5 完成；T4.2a-R2 durable ExecutionBudget 已代码级 review 通过（real-LLM validation pending）`
> 最后更新：2026-07-13（DOC-R2：补 T4.2a-R2 durable ExecutionBudget / publish fence / route flip fencing 交叉引用；详细约束不再本地复制）
> 范围：Planner 最小化、Skip Gate、Prompt Cache、Model Profile、Usage Bucket 和 token economy。

## 目标

Orchestration 的控制面必须尽量 deterministic。LLM token 应花在真正需要语义生成或判断的 worker 上，而不是花在调度和路由上。

D4 默认：

- Policy Planner 是 deterministic function。
- PydanticAI 只用于 translation 等 LLM-backed workers。
- LangGraph 不进入 D4/D5 主路径；D6+ 如需要复杂 branching / interrupt / repair flow，再做隔离 spike。
- 不启用 LLM Planner。

D3-P0 依赖策略：

- 主动升级 PydanticAI 到当前最新稳定 1.x，并验证 typed output、usage、retry、FunctionModel / provider adapter 行为。
- DashScope SDK 跟随最新 patch；asyncpg 升级到 0.31.x 并验证 transaction counter / lease tests。
- LangGraph 不为 D4/D5 主动升级；现有旧 workflow 可继续保持当前锁定版本直到 cutover。LangGraph v1+ 只作为 D6+ 复杂 repair / branching / interrupt 隔离 spike 候选，具体能力和版本风险必须在 spike 时实测确认。
- FastAPI、LangSmith 只在 focused tests 暴露缺口时升级，不作为 D4 主路径前置。

## Planner 角色拆分

| 角色 | 类型 | 何时使用 | D4 是否实现 |
|---|---|---|---|
| Policy Planner | deterministic code | 根据 record state、coverage、failure class、Authorization Envelope 输出 typed plan | 是 |
| Semantic Reviewer | PydanticAI typed worker | 判断复杂文档结构、layer 语义价值、Candidate Base 高风险修复建议 | 否，D5+ |
| LLM Planner | LLM-driven planner | 开放式多步探索、难以用规则表达的高层策略 | 否，默认不进入本轮 |

Policy Planner 的输入：

- Reading Record product state
- Stable Base / Reading Units
- published layers
- Parsed Decisions
- failure classes
- Authorization Envelope
- policy tables
- usage counters

Plate projection state 不是 Policy Planner 的决策事实源。Planner 可以读取 projection health，例如 `projection_lagging`、`projection_reset_required`，用于决定是否触发 snapshot rebuild；但不能根据前端 Plate path 或 Plate value 反推出业务状态。

Policy Planner 的输出：

- run / job plan
- skip decision
- pause / action_required decision
- retry / repair decision

Policy Planner 不调用模型。

## Skip Gate 合同

Skip Gate 在 job 入队、claim 和 publish 前运行，避免无意义 LLM 调用，并防止 stale worker result 写入 domain truth。

D4 translation gate：

```text
record state == readable_enhancing
AND target unit exists
AND unit hash valid
AND translation layer not already published
AND previous attempt not too recent
AND envelope has enough token/cost budget
AND record not paused/cancelled/superseded
=> enqueue or claim translation job
```

Skip Gate 返回：

| 字段 | 含义 |
|---|---|
| `decision` | `run`、`skip`、`pause`、`retry_later`、`reject` |
| `rationale_code` | 结构化原因 |
| `policy_version` | 使用的 policy table 版本 |
| `next_retry_at` | `retry_later` 时使用 |

推荐 `rationale_code`：

- `already_published`
- `record_not_readable`
- `record_paused`
- `record_cancelled`
- `record_superseded`
- `unit_missing`
- `unit_hash_mismatch`
- `budget_exhausted`
- `retry_too_recent`
- `not_applicable_to_goal`
- `unit_too_short`
- `layer_disabled`

Event 规则：

- 纯幂等 skip，例如 `already_published`，不写 UI event。
- 会影响用户可见 coverage 或 action 的 skip，写内部 decision 记录；只有需要前端知道时才写 `reader_events`。
- worker heartbeat、claim、attempt 不进入 `reader_events`。
- pre-publish 的 Skip Gate 实际由 Layer Publisher / Publish Guard 承担；它可以复用同一套 `rationale_code`，但最终写入权仍属于 deterministic domain service。

## Layer Applicability Table

D5+ 扩展 layer 时，优先扩展静态 policy table，不新增运行时 LLM routing。

| layer_type | min_unit_chars | reading_goal_applicability | min_tokens | D4 |
|---|---:|---|---:|---|
| translation | 0 | all learning records | 200 | yes |
| vocabulary | 30 | learning_english | 400 | no |
| grammar_note | 60 | learning_english | 350 | no |
| sentence_analysis | 200 | learning_english | 600 | no |
| summary | 0 | learning_english, academic future | 800 | no |
| semantic_outline | 0 | academic future, learning_english optional | 1000 | no |

这些数值是 D1 seed values，D2 Length Class 与成本 spike 后可调整。

`grammar_note` 与 `sentence_analysis` 可以由同一个 `grammar_bundle_worker` / `reader_layer_grammar_bundle` job 生成；Policy、Layer Publisher、RAG、Projection 和 Eval 必须按 subtype 独立处理。`long_sentence` 不作为权威 layer type，只作为触发 `sentence_analysis` 的适用场景或 rationale。

## Prompt Cache / Context Economy

目标：

- 静态 prompt 放在前面。
- unit text 等动态内容放在后面。
- translation worker 不携带 Ask history。
- provider 支持 cache 时记录 cache hit/miss。

Provider posture：

- OpenAI prompt caching 使用 exact prefix match，静态内容应放在 prompt 前缀。
- DeepSeek pricing 区分 cache hit / cache miss，应记录 cache 状态。
- DashScope / 百炼是否支持等价 cache 能力由 D2 provider spike 验证。
- Anthropic `cache_control` 可作为设计参考，但本轮不默认使用 Anthropic。
- Prompt cache 是成本优化和观测信号，不是正确性、budget safety 或 replay 的前提。

D4 prompt 结构：

```text
static system instruction
-> stable layer policy fragment
-> reading goal / variant
-> dynamic unit text
```

禁止：

- 每次 worker 拼接整篇文章。
- 把 Ask 多轮历史传给 translation worker。
- 把 planner trace 放进 worker prompt。
- 让 Plate AI / editor plugin 绕过 Claread worker、Model Profile、Authorization Envelope 或 usage audit。
- 为生成前端 Plate fragment 额外调用 LLM；fragment conversion 默认是 deterministic projection。

## Model Profile 合同

模型选择不是运行时 LLM 决策，而是 deterministic route lookup。

`model_profiles` 或等价配置至少包含：

| 字段 | 含义 |
|---|---|
| `profile_id` | Claread 内部 profile id |
| `provider` | `dashscope`、`deepseek` 等 |
| `model_id` | provider 官方 model id |
| `route` | `reader_layer_translation`、`reader_layer_vocabulary`、`reader_layer_grammar_bundle` 等 |
| `layer_type` | 适用 layer |
| `context_limit` | 上下文窗口 |
| `max_output_tokens` | 默认输出上限 |
| `supports_json_schema` | 是否支持 strict structured output |
| `supports_thinking` | 是否支持 thinking |
| `supports_prompt_cache` | 是否支持 prompt cache 或等价缓存 |
| `input_cost_per_1m` | cache miss 或普通 input 成本 |
| `cached_input_cost_per_1m` | cache hit 成本，可为空 |
| `output_cost_per_1m` | output 成本 |
| `fallback_profile_ids` | deterministic fallback 链 |
| `benchmark_status` | `unverified`、`spike_passed`、`rejected` |

D2 要用当前官方 model id 校准。不要把 TMP research 中的具体 model id 直接写进 production contract。

具体 provider model id、pricing、cache 字段和 structured output 能力属于高变更外部事实。D3-P0 必须重新查官方文档和实际调用结果，更新 model catalog / profile config；正式架构只约束 route/profile contract，不把某个 TMP 调研中的 model id 当长期事实。

Fallback 规则：

- Planner 和 job 只引用 `model_route`。
- Resolver 根据 route 找 primary profile 和 deterministic fallback chain。
- Fallback 不能绕过 route required capabilities、Authorization Envelope 或 usage audit。
- `operation_fingerprint` 表示 business intent，包含 route / prompt / layer / unit / input hash 等稳定字段，不包含临时 actual provider/model。
- Usage event 记录 primary profile、actual profile、fallback attempt index 和 fallback reason。

Display title generation 使用独立的 `reader_title_generation` route，必须显式配置 `reader_title_model_profile`。它不能静默回退到 `reader_layer_translation`：title worker 是短 bounded context + 极小 structured output，translation profile 通常按更长 unit translation 的上下文、延迟和额度配置。独立 worker 会为每个 record 增加一次短模型调用，但换来独立 retry、usage attribution 和 quota control，不把标题失败或重试耦合到逐 unit 翻译。

## Usage Bucket 合同

`ai_usage_events` 需要从“记录一次模型调用”升级为可做成本归因的审计表。

新增或等价 metadata 至少覆盖：

| 字段 | 含义 |
|---|---|
| `reader_run_id` | bounded run |
| `reader_job_id` | typed job |
| `enhancement_layer_id` | published layer |
| `planner_kind` | `deterministic_policy`、`semantic_reviewer`、`llm_worker`、`repair`、`batch` |
| `policy_version` | policy table 版本 |
| `model_profile_id` | 实际使用的 profile |
| `cache_hit` | provider 报告或 adapter 推断 |
| `cache_class` | `none`、`provider_default`、`5m`、`1h`、`24h` 等 |
| `token_budget_before` | 调用前预算 |
| `token_budget_after` | 调用后预算 |
| `operation_fingerprint` | 幂等关联 |

D2 cost baseline 至少输出：

- `usage_by_record`
- `usage_by_layer`
- `usage_by_model_profile`
- `usage_by_cache_status`
- `usage_by_planner_kind`

Provider cache usage 必须 adapter 化为 normalized fields，例如 `cached_input_tokens`、`cache_miss_input_tokens`、`cache_creation_input_tokens`、`cache_status` 和 `cache_class`。若 provider 不返回 cache 字段，按 unknown 或 all miss 记录，不得估算成 hit。

## Batch / 非实时工作

D4 不使用 batch。

D5+ 可把非实时工作迁入 batch：

- vocabulary / grammar bundle 后台补全
- RAG substrate rebuild
- eval dataset rerun
- repair sampling

不适用 batch：

- `article_ready` 前链路
- 当前 unit translation
- Ask sidecar 实时回答

## D2 Spike

D2 必须验证：

- Skip Gate 是否能在 0 token 下正确跳过重复/无效 jobs。
- Prompt cache 或 provider cache 状态是否能被 adapter 记录。
- Model Profile route lookup 是否能覆盖 translation worker、grammar bundle worker 和 fallback。
- PydanticAI structured output + usage limits 是否适合 translation。
- `ai_usage_events` 扩展字段是否足以支撑 cost baseline。

## D3-P0 Backend Dependency Alignment

D3-P0 已于 2026-06-18 完成 closeout。

正式实现 D3 runtime skeleton 前必须完成后端依赖对齐：

- PydanticAI：已升级到 `1.107.0`，现有 structured completion、Reader Ask agent 和旧 workflow focused tests 通过。
- DashScope SDK：已升级到 `1.25.23`，现有 native provider 与 stream tests 通过。
- asyncpg：已升级到 `0.31.0`，作为 D3 job/event transaction tests 的实现基线。
- LangGraph：保持 `0.6.11`；D4/D5 不主动升级、不引入主路径。LangGraph v1+ 只作为 D6+ complex repair / branching / interrupt 隔离 spike 候选。
- LangSmith / tracing：保持当前锁定版本；现有 tracing isolation tests 通过。
- Provider SDK / adapters：现有 focused tests 未暴露必须升级 OpenAI SDK 或 FastAPI 的缺口。
- FastAPI SSE 与 asyncpg transaction semantics：在 D3-P4 新 schema/runtime skeleton 中补专门 tests，不能仅依赖 D3-P0 closeout。

D3-P0 输出已包含 lockfile 更新、focused tests、rollback plan，以及 deferred runtime checks 清单。

## T4.2a-R2 Durable ExecutionBudget / Publish Fence / Route Flip Fencing

T4.2a-R2 在本模块的 Policy / Cost Control 基础上引入跨 `runner.run()` 持久化的 per-layer `ExecutionBudget`、publish fence 与 route flip fencing。详细约束不再本地复制，权威归宿：

- 决策记录：[`../target-architecture.md`](../target-architecture.md#决策记录) `T4.2a-R2` 行。
- 不可违反决策与 R2-R1 / R2-R2 / R2-R3 / R2-R3a 修复明细：[`../agent-brief.md`](../agent-brief.md)「不可违反决策」T4.2a-R2 系列。
- 任务状态、测试计数与 deterministic acceptance：[`../implementation-plan.md`](../implementation-plan.md) T4.2a-R2 章节。
- 观测性字段（`budget_denied`、`exhausted_layers`、`budget_diagnostics`、`stopped_reason` 持久化到 `reader_runtime_spans.metadata_json`）：[`./orchestration-runtime.md`](./orchestration-runtime.md#observability)。

简要口径（详细约束以权威归宿为准）：

- per-layer `ExecutionBudget.load_durable()` 从 `reader_jobs` 聚合 `SUM(attempt_count)` / `MAX(max_attempts)` per `(record, base, generation, layer)`，跨 run 持久化。
- `max_effective_calls = planned_calls * max_multiplier`，默认 `max_multiplier=3` 与生产 `max_attempts=3` 对齐。
- `BUDGET_CONSUMING_OUTCOMES = {succeeded, retry_later, failed_terminal}`；`superseded / no_job / skipped / budget_denied` 不消耗预算。
- 预算耗尽时 `stopped_reason = budget_exhausted`（全 layer）或 `partial_budget_exhausted`（部分 layer）。
- Route flip fencing = bootstrap supersede + claim-time `_validate_fence` → `_check_route_consistency` + publish-time 同一 `_validate_fence`（6 个 publisher 方法）；mismatch 返回 `stale_route_fingerprint`。
- 状态：代码级 review 通过 / deterministic acceptance complete / real LLM validation pending；不新增 migration。

## 参考资料

- LangGraph overview: https://docs.langchain.com/oss/python/langgraph/overview
- LangGraph streaming: https://docs.langchain.com/oss/python/langgraph/streaming
- PydanticAI agents: https://ai.pydantic.dev/agents/
- PydanticAI usage limits: https://ai.pydantic.dev/api/usage/
- LangGraph v1 release notes: https://docs.langchain.com/oss/python/releases/langgraph-v1
- LangGraph changelog: https://docs.langchain.com/oss/python/releases/changelog
- LangGraph v1 migration guide: https://docs.langchain.com/oss/python/migrate/langgraph-v1
- Temporal workflows: https://docs.temporal.io/workflows
- Temporal activities: https://docs.temporal.io/activities
- OpenAI structured outputs: https://platform.openai.com/docs/guides/structured-outputs
- OpenAI prompt caching: https://platform.openai.com/docs/guides/prompt-caching
- 阿里云百炼模型列表: https://help.aliyun.com/zh/model-studio/getting-started/models
- DeepSeek pricing: https://api-docs.deepseek.com/quick_start/pricing
