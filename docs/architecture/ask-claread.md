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

- `reader_ask_turn_runs`：run 身份、`run_attempt` / `supersedes_run_id`、`status`（`streaming|completed|failed|interrupted|cancelled|stale`）、`final_status`（`ok|context_stale|invalid_citations|failed|cancelled`）、`user_visible_output_json`、`resolved_evidence_json`、`reasoning_projection_json`、envelope fingerprint/snapshot；终态写入经 CAS 幂等。
- `reader_ask_client_submissions`：`(thread_id, client_submission_id)` 主键去重，`claim_generation` CAS；重复提交不重复调模型。
- 中断收敛：路由 `finally` 把仍 `streaming` 的行 reconcile 为 `cancelled`；后台 `StaleStreamSweeper`（启动 sweep + 60s 周期）只向 cancelled/failed 收敛，绝不提交半成品。
- 历史投影（`history_projection.py`）：DB `final_status` 列是唯一状态权威；不投影受限证据；`current_eval_trace` 恒为 `None`（`reader_ask_eval_traces` 不在 baseline schema，评测走 `evals/` artifact 文件式 harness）。

## Thread memory

`thread_memory/`：LLM compactor 只产出窄化 `CompactionDraft`（事实 + 来源 ID），存储、CAS（`version`）、fence 与置信度由服务端负责；快照可从 canonical 消息完全重建，CAS 不匹配时走确定性无 LLM emergency 重建。压缩生命周期发 `context.compaction.*` SSE。

## Learner reasoning 投影

`learner_reasoning/projector.py` 是独立小 agent（无工具、`instrument=False`、不可见 reasoning 内容经 scrub 后输入），产出 ≤80 字中性中文摘要（策略 `learner_reasoning_v1`）。功能开关 `reader_record_ask_learner_reasoning_enabled`（默认关）；快照经 `agentic.learner_reasoning.snapshot` 流式下发并随 turn run 持久化。

## 计费与用量

- capability 常量 `reader_ask`；加权计费配置（`analysis_weighted_tokens_v1` 公式 + 每档位 `price_multiplier`）定义在 `app/services/ai_usage/billing.py` 并挂载到 model option。
- turn run 已预留 `usage_summary_json` / `usage_event_id` 列；实际写账接入（预扣/结算/退款闭环）属于 post-cutover backlog。

## 评测

Ask 质量评测不依赖在线表：`evals/claread_eval/reader_record_ask/` 提供 artifact 文件式 harness（11 个评估维度 + aggregator），入口 `evals/scripts/run_reader_record_ask_eval.py`；真实 LLM 阶段经 env gate 调用 `services/api/tests/test_reader_record_ask_real_llm_eval.py`。

## 当前明确不做

- 跨文章检索、跨文章引用入口与历史资产自由查询。
- agent 写动作（保存笔记/高亮、生成 supplement）与 proposal-confirm 写生命周期。
- 多线程列表、独立 AI 工作台、对话级人格记忆。
- PydanticAI `Tool(requires_approval=True)` / 跨 HTTP roundtrip 保活 agent run。
- 把 LangSmith trace 当作 runtime 状态权威（runtime 事实源是 PostgreSQL turn run 与 `reader_runtime_spans`）。
