# Ask Claread 架构说明

## 文档状态

- 状态：current implementation architecture
- 日期：2026-08-08
- 适用范围：Claread Web Reader 内当前 Ask Claread 模块（`services/api/app/services/reader_record_ask/`）
- 文档关系：
  - 当前产品边界见 `docs/product/ask-claread.md`
  - 当前主线与后续评估见 `docs/development/mainline.md`

## 架构目标

当前 Ask Claread 已冻结为 `article-bound, agent-loop-first, turn-run-backed` 的 Reader 内阅读助手：主回答 agent 直接消费服务端装配的 ContextEnvelope，按需调用受控工具取证，产出带来源标注的回答。旧 planner-first 链路、旧 9-tool registry、旧 supplement/写动作生命周期已物理删除，不在当前架构面内。

## Runtime chain and orchestration links与编排交叉引用（含编排交叉引用）
交叉引用：Ask sidecar action 与 Reader enhancement 共用同一用户级 concurrency / cost envelope（见 `docs/architecture/reader-orchestration.md` 的 Run/Job 模型）。上下文压缩运行参数与回滚入口见 `docs/operations/reader-runtime.md`。Ask 是侧车，不是 orchestration 控制面。

```text
route (reader_record_ask.py)
  -> service.py（preflight：anchor 校验、model option 解析、执行配置编译、提交幂等 claim）
  -> production_stream.py（SSE + 持久化 adapter）
  -> runtime.py / turn_coordinator.py（单轮状态中心：ContextEnvelope、预算、工具分发、fence）
  -> agent.py（PydanticAI 主 agent + grounding output validator）
  -> finalizer.py（fence 复核、证据解析、公开 citation）
  -> repository.py（turn run CAS 落库）
```

关键约束：

- 发送 preflight 在 `StreamingResponse` 之前完成提交幂等 claim（`ensure_submission_for_send` 单事务）；preflight 失败返回真实 HTTP 4xx/503，不用 SSE error frame 兜底。
- 上下文由 `context_envelope.py` / `envelope_builder.py` 从当前文章快照事实装配，带 fingerprint；model view 经 9 账户预算（`model_view_budget.py`）裁剪后以 untrusted block 渲染进 prompt。
- Turn 状态机冻结为 `idle -> running -> finalizing -> committed|failed|cancelled`（`turn_lifecycle.py`）。

## 主 agent 与工具

`create_reading_record_ask_agent`（`agent.py`）：

- 模型解析链：`reader-ask-model-options.json` -> `model_options.py` -> `execution_config.py` -> `build_model_for_route(reader_ask)`；无启用档位时回落路由默认。
- 输出契约：`AgentAnswerDraftOutput`（`response_kind ∈ grounded_answer / clarification / source_unavailable` + `answer_blocks`），由 `grounding_validator.py` 作为 output validator 强制（`retries={"tools": 1, "output": 2}`）。
- 结构化输出流式合同：三个生产 option 的最终答案均以 TextPart 内容流式返回（DeepSeek 经 provider 级、qwen37-max 经 model 级 `default_structured_output_mode: "prompted"`），thinking transport 只从 TextPart 生成 answer delta 事件；reasoning 仍来自 `reasoning_content` 字段流，工具轮走 tool call lane——三个事件面互相隔离。该合同由离线测试锁定（配置解析合同 + MockTransport 两轮 SDK 形状行为测试）；真实 provider probe（qwen3.7-max-2026-05-17）已验证 reasoning → tool_round → answer 流式序列与 canonical output（请求数 = 2，输出 token 在预算内），DeepSeek 两个 option 的真实 probe 亦通过功能验收（FUNCTIONAL_PROVIDER_REASONING_PASS）；浏览器真实产品验收未运行。
- 工具按条件挂载，全部经 `TurnCoordinator` 分发并受预算约束：

| 工具 | 挂载条件 | 约束 |
|------|----------|------|
| `expand_evidence` | 本轮存在可展开证据指针 | opaque `cur_` 指针状态机，进程级 pointer ledger |
| `search_current_article` | 文章 RAG port 可执行 | 每轮最多 1 次调用；不可用时模型看不到该工具 |
| `search_web` | 请求 `web_search_mode="allowed"` 且 capability 可解析 | registry `max_calls=2`、`max_results_per_call=5`；决定性结果后经 prepare hook 退役 |

旧 `read_range` 仅保留为离线契约，不在生产挂载。预算耗尽抛 `HostBudgetExhausted`。

## 文章 RAG

- `build_production_article_rag_port`（`production_wiring.py`）：仅在 `READER_ARTICLE_RAG_ENABLED=true` 且 embedding provider 与 vector provider 均非 Unconfigured 时返回 `RetrievalBackedArticleRagPort`，否则返回 `None`（零 RAG I/O）。
- 索引生命周期存于 `reader_article_rag_index_runs`（`planned|queued|indexing|indexed|failed|superseded`，只存 truth-layer hash 与 chunk_count，不存 chunk 文本；每文档唯一 active 索引）。
- 路由：`GET /reader/records/{record_id}/article-rag-index/status`、`POST .../ensure`。
- 检索结果为 typed 状态（`not_ready / not_indexed / indexing / unavailable / empty`），经 `article_rag_model_view.py` 清洗后才进入 model view。

## Web search

- Adapter registry 按精确模型名 fail-closed：Qwen（DashScope Responses 兼容端点）与 DeepSeek（Anthropic 兼容端点）两个 backend；构造前做 HTTPS origin 校验。
- 策略常量：`reader_record_ask_web_search_v1`，超时 18s，`max_calls=2`、`max_results_per_call=5`。
- `web_search_mode="allowed"` 但 capability/backend 无法解析时，请求返回 503 `web_search_unavailable`。

## 证据、citation 与回源

- 证据以 `evh_` 句柄注册（`evidence.py` / `evidence_registry.py`），展开走 `EvidenceExpansionSession` 状态机；web 证据有独立的镜像 registry。
- `finalizer.py` 复核 generation fence 后把句柄解析为公开 `PublicCitation`；公开面是 no-evh（不暴露句柄、fingerprint、原始证据），受限证据只存 `resolved_evidence_json`。
- 回源导航 `POST .../citations/{citation_id}/navigate`：客户端只提交 citation_id，服务端基于权威快照加载 `LiveDocumentFence`，要求消息 `final_status=ok`，返回 typed 定位（`unit_id` / `anchor_segment_id` / UTF-16 区间）或状态原因。

## 持久化

- `reader_ask_turn_runs`：run 身份、`run_attempt` / `supersedes_run_id`、`status`（`streaming|completed|failed|interrupted|cancelled|stale`）、`final_status`（`ok|context_stale|invalid_citations|failed|cancelled`）、`user_visible_output_json`、`resolved_evidence_json`、`reasoning_projection_json`、`usage_summary_json` / `usage_event_id`（attempt 级 usage 权威）、envelope fingerprint/snapshot；终态写入经 CAS 幂等。
- `reader_ask_client_submissions`：`(thread_id, client_submission_id)` 主键去重，`claim_generation` CAS；重复提交不重复调模型。
- 中断收敛：路由 `finally` 把仍 `streaming` 的行 reconcile 为 `cancelled`；后台 `StaleStreamSweeper`（启动 sweep + 60s 周期）只向 cancelled/failed 收敛，绝不提交半成品。
- 历史投影（`history_projection.py`）：DB `final_status` 列是唯一状态权威；不投影受限证据；`current_eval_trace` 恒为 `None`（`reader_ask_eval_traces` 不在 baseline schema，评测走 `evals/` artifact 文件式 harness）。

## Thread memory

`thread_memory/`：LLM compactor 只产出窄化 `CompactionDraft`（事实 + 来源 ID），存储、CAS（`version`）、fence 与置信度由服务端负责；快照可从 canonical 消息完全重建，CAS 不匹配时走确定性无 LLM emergency 重建。压缩生命周期发 `context.compaction.*` SSE。

## Provider reasoning

主 agent 的 `ThinkingPart` / `ThinkingPartDelta` 只进入 `ProviderReasoningObserver`。Host 以确定性、跨 chunk 的安全闸处理后，按严格递增 `seq` 发布 `agentic.reasoning.started|delta|completed`；不使用 LLM projector，不产生第二次 provider 调用。内部 evidence handle 会替换为中性的 `〔引用〕` 并继续输出；只有凭据、私钥、连接串或非法控制字符等真正的信任边界失败才永久停止本轮后续 reasoning，但不影响答案链。

`reasoning_projection_json` 保存用户实际看到的 `provider_reasoning_v1` 文本与 `complete|truncated|blocked` 状态；成功与非成功正常终态都保存，冷历史只经同一 snapshot validator 恢复，且不进入 thread memory。`reader_record_ask_provider_reasoning_enabled=true` 为默认，`false` 仅作紧急 kill switch。旧 `learner_reasoning_v1` 只保留历史读取兼容，不再接入生产执行流。

### Reasoning 观测（非敏感审计事实）

每轮终态都会产出一份有限枚举的 reasoning 观测（`projection_disabled` / `not_requested` / `provider_empty` / `complete` / `truncated` / `blocked`），字段固定为 `reasoning_requested`（来自 resolved profile 的 `model_settings.thinking_enabled()`，写入 execution snapshot 的 `thinking_requested`，绝不按模型名猜测）、`reasoning_projection_enabled`（host kill switch）、`reasoning_observed`、`reasoning_outcome`、`reasoning_char_count`、`projection_policy_version`。首字符即被安全闸封锁的 turn（`has_content=False` 且 `visibility_status=blocked`）记为 `blocked`，不误记为 `provider_empty`。该观测优先写入当轮 usage event 的 `metadata_json`；provider 未报告 usage、因此没有 usage event 时，通过一条固定结构的终态脱敏日志留下相同事实。观测只含布尔/枚举/计数，绝不包含 reasoning 文本、prompt、答案、provider payload、密钥或异常文本；观测失败不影响答案、终态与 usage accounting。三个生产 option（deepseek-v4-flash / qwen-max / deepseek-pro）的 reader_ask 路由 thinking 合同（可解析、profile 一致、thinking 请求开启）有离线合同测试锁定。真实 provider 验收 probe 位于 `services/api/tests/test_reader_record_ask_thinking_real_llm_probe.py`（real_llm triple gate + ≤2 请求硬上限 + 隐私安全报告；发送 resolved 生产 model settings 仅覆盖每请求 `max_tokens=246`，总输出预算 512 tokens = (246+10)×2（Qwen 官方 10-token 输出容差）由累计 `output_tokens_limit` 事后防线 + 报告断言兜底，per-call `disabled_tracing()` + `instrument=False` 隔离 tracing，`WrapperModel` 计数）。真实 probe 已运行并通过：qwen3.7-max-2026-05-17 实测 reasoning → tool_round → answer、流式 answer delta 存在、canonical output 存在、request_count = 2、output_tokens 在预算内；DeepSeek 两个 option 的功能性 probe 亦通过（FUNCTIONAL_PROVIDER_REASONING_PASS）。Qwen 运行期遗留一个已知上游 teardown warning（见 `docs/operations/model-config.md` 的 KNOWN_UPSTREAM_OPENAI_STREAM_FINALIZER_WARNING 定性，不阻止浏览器验收）。注意项目设置名与 provider wire 参数名的区分：DeepSeek 官方 wire 只确认 `max_tokens`（`DirectDeepSeekChatModel` 每请求转换并阻止 `max_completion_tokens` 同发），Qwen 沿 OpenAI 兼容默认映射发送 `max_completion_tokens`。

## 计费与用量

- capability 常量 `reader_ask`；加权计费配置（`analysis_weighted_tokens_v1` 公式 + 每档位 `price_multiplier`）定义在 `app/services/ai_usage/billing.py` 并挂载到 model option。
- Provider usage 审计已接入生产链：usage 只来自同一次主 agent run 的 PydanticAI 公开 API（两个 agent 入口共享同一个 `RunUsage` accumulator，含 tool round / output-validator retry 的 run 级聚合），经共享 `build_usage_metadata` 规范化后写入 `reader_ask_turn_runs.usage_summary_json`，并以 invocation key `reader_ask:turn:<turn_run_id>` 幂等落一条 `ai_usage_events` 记录（`usage_scope=user_billed`、`billing_mode=user_points`），turn run 的 `usage_event_id` 指向该记录；regenerate 产生新 turn run 与新事件，同一 turn 重放收敛到同一事件，同 key 不同 observation 判 `conflict`——只保留 summary、不链接旧 event id。终态失败/取消的 turn 仍保留 provider 已确认的部分 usage（event `metadata.usage_completeness=partial`），typed 终态（context_stale / invalid_citations / cancelled 等）原样进入 event `metadata.final_status` 与 `error_code`。usage 来自 provider 未报告时保持 `NULL`，不伪造零值；usage 落账失败不影响答案与终态，也不触发第二次 provider 调用。
- 成本测算为审计口径：`compute_reader_ask_cost_points` 结果记为 event `metadata_json.computed_cost_points`，`billing_policy_version` 与 `price_multiplier` 同时写入列与 metadata。`billed_points` 保持 `NULL`（不代表 token usage 缺失）；用户积分预扣、差额结算与退款尚未实现，属独立后续任务。

## 评测

Ask 质量评测不依赖在线表：`evals/claread_eval/reader_record_ask/` 提供 artifact 文件式 harness（11 个评估维度 + aggregator），入口 `evals/scripts/run_reader_record_ask_eval.py`；真实 LLM 阶段经 env gate 调用 `services/api/tests/test_reader_record_ask_real_llm_eval.py`。

## 当前明确不做

- 跨文章检索、跨文章引用入口与历史资产自由查询。
- agent 写动作（保存笔记/高亮、生成 supplement）与 proposal-confirm 写生命周期。
- 多线程列表、独立 AI 工作台、对话级人格记忆。
- PydanticAI `Tool(requires_approval=True)` / 跨 HTTP roundtrip 保活 agent run。
- 把 LangSmith trace 当作 runtime 状态权威（runtime 事实源是 PostgreSQL turn run 与 `reader_runtime_spans`）。
