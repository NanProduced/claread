# LangSmith Tracing

> **状态**: `CURRENT` | **最后验证**: 2026-08-14

本文记录 Claread 通用后端的 LangSmith 使用规范。它服务本地调试、Reader orchestration 质量分析和后续 LLM-as-a-Judge 数据回看。

## 当前结论

- LangSmith 用于 trace，不作为线上业务链路依赖。
- 单一 project `claread-dev`（或操作者自配的同义命名），不支持"每请求切 project"。所有写 trace 的链路落在同一个 project。
- 当前有两条 trace 来源：
  - **LangGraph callback + `@traceable`**：覆盖 Daily Reader 固定 workflow 的 LLM 调用。
  - **PydanticAI 1.x OpenTelemetry instrumentation**：覆盖在已初始化进程中运行的 PydanticAI agent 模型调用（当前即 API 进程内的 Ask Claread agent），由 `LANGSMITH_OTEL_ENABLED=true` 开启；生产应开启，测试默认关闭（`tests/conftest.py`）。
- PydanticAI agent 在未执行 `Agent.instrument_all()` 的进程里**不产生** trace，不要依赖 PydanticAI 自动产出。`Agent.instrument_all()` 只经 `setup_langsmith()` 注册，而 `setup_langsmith()` 只在 API 进程 bootstrap（`app/main.py`）调用一次。**standalone Reader enhancement worker（`scripts/run_reader_enhancement_worker.py`）当前不调用 `setup_langsmith()`，因此其 PydanticAI agent 调用当前不写入 LangSmith**；不能把 standalone worker 描述为必然产生 trace。Reader 侧的运行时观测以 PG `reader_runtime_spans` 为准。
- Reader orchestration 的运行时事实源是 PostgreSQL `reader_runtime_spans`；LangSmith run 通过 `langsmith_run_id` 回填与 PG span 行关联，LangSmith 不是 runtime 状态权威。

## 环境变量

本地启用时配置：

```bash
LANGSMITH_ENABLED=true
LANGSMITH_API_KEY=<your-key>
LANGSMITH_PROJECT=claread-dev
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_TRACING=true
LANGSMITH_OTEL_ENABLED=true
```

默认值见 `services/api/app/config/settings.py`：`langsmith_enabled=false`、`langsmith_tracing=true`、`langsmith_project=claread-dev`、`langsmith_otel_enabled=false`。`setup_langsmith()` 只在 API 进程 bootstrap（`app/main.py`）调用一次；worker 脚本入口不调用它。真实 key 不进入仓库。

## 真正会写 LangSmith 的链路

| 链路 | 入口 | span / run 名 | 备注 |
|------|------|---------------|------|
| Daily Reader 自动管线 | `services/api/app/services/daily_reader/pipeline.py` 选稿+workflow | 根 run `daily_reader`，tag `surface:daily_reader_pipeline` | LangGraph `graph.ainvoke` config 显式传入 |
| Daily Reader LLM 子调用 | `services/api/app/services/daily_reader/workflow.py` | `daily_highlight_llm_call`、`daily_paragraph_notes_llm_call`、`daily_takeaways_llm_call`、`daily_review_llm_call`、`daily_refinement_llm_call` | `@traceable(run_type="llm")` |
| Daily Reader 选稿评分 | `services/api/app/services/daily_reader/scoring.py` | `daily_scoring_llm_call`（orphan，无根） | `@traceable(run_type="llm")` |
| Reader orchestration agent | enhancement worker 内 PydanticAI agent | `reader_layer_translation_agent`、`reader_layer_vocabulary_agent`、`reader_layer_grammar_bundle_agent`、`reader_layer_grammar_window_agent`、`reader_title_generation_agent`、`reader_semantic_outline_agent` 及对应 batch agent | OTEL span，仅当所在进程经 `setup_langsmith()` 初始化且 `LANGSMITH_OTEL_ENABLED=true`；standalone enhancement worker 当前无此初始化路径，不产生这些 span |
| Ask Claread 主 agent | `services/api/app/services/reader_record_ask/agent.py` | PydanticAI agent 默认 span | 随 API 进程的 `instrument_all()` 继承 |

**不产生 LangSmith trace 的路径**：

- learner reasoning projector（`services/api/app/services/reader_record_ask/learner_reasoning/projector.py` 显式 `instrument=False`）。
- 未开启 OTEL 的进程中的任何 PydanticAI 调用。
- standalone Reader enhancement worker：入口脚本不调用 `setup_langsmith()`，未注册 `Agent.instrument_all()`。
- 旧 `/analyze` 主链、旧 eval-center 子路径（`/eval/article-analysis/*`）：已物理删除，不再有对应 trace 行为。

## `surface` 标签现状

`app/observability/tracing_context.py` 定义了 canonical surface 集合：

| 值 | 现状 |
|----|------|
| `daily_reader_pipeline` | 实际使用：Daily Reader 根 run 的 tag 与 metadata（`pipeline.py` 字面传入） |
| `reader_orchestration` | 常量与 ContextVar 通道已预留，当前生产路径未绑定；Reader 侧以 PG `reader_runtime_spans` 与 OTEL agent span 名为主要观测信号 |

`set_trace_surface(...)` / `get_trace_surface(...)` 是 ContextVar 实现，并发安全；新增链路绑定新 surface 时先扩 `KNOWN_SURFACES` 再更新本表。

## `reader_runtime_spans` 与 LangSmith 关联

Reader orchestration 的 runtime span 落 PostgreSQL `reader_runtime_spans`，span kind 为 `pipeline_root` / `worker_tick` / `publish_fence` / `claim`：

- `pipeline_root`：worker_loop 每 claim 一条 record 开启，trace_id 取自 `reader_runs.envelope_json`。
- `worker_tick`：pipeline_runner 按 worker 类型（`display_title` / `semantic_outline` / `translation` / `vocabulary` / `grammar_bundle` 及 batch/window 变体）开启与收尾。
- OTEL 开启时，`LangSmithIdBridgeProcessor` 把 PydanticAI LLM span 上的 `langsmith.trace.id` / `langsmith.span.id` 捕获进 ContextVar，`ReaderSpanRecorder.end_span` 自动回填 `reader_runtime_spans.langsmith_run_id`（格式 `<trace_id>/<span_id>`），把 PG span 行与 LangSmith run 关联。

排查 Reader 问题时先看 PG span（Directus `reader-orch` 只读 endpoint：`/reader-orch/trace/:trace_id`、`/reader-orch/run/:run_id`、`/reader-orch/record/:record_id/summary`、`/reader-orch/dashboard`），再按 `langsmith_run_id` 跳 LangSmith 看模型细节。

## 单调用禁用 trace

`app.observability.disabled_tracing()` 包装 `langsmith.run_helpers.tracing_context(enabled=False)`，用于单次调用显式 opt-out，ContextVar 实现、并发安全，当前主要由测试使用。需要硬隔离某类实验 trace 时，用独立进程 + 独立 `LANGSMITH_PROJECT` 部署，不要 per-request 改环境变量。

旧 per-request 切 project 的实现（改 `os.environ["LANGSMITH_PROJECT"]`）已移除：同进程并发下会污染同时刻的非实验请求。旧 `trace_scope` / `trace_project` 字段随旧 eval adapter 物理删除，不再是任何代码的输入。

## 调试方式

人工分析优先按 tag / metadata 过滤：

- `surface:daily_reader_pipeline` — Daily Reader 后台管线。
- run name `daily_reader` / `daily_scoring_llm_call` — Daily Reader workflow 与选稿评分。
- agent span 名 `reader_layer_*` / Ask 默认 agent span — Reader orchestration 与 Ask 模型调用。

查看异常时优先检查：

1. PG `reader_runtime_spans` 是否定位到失败 worker / fence 违规。
2. OTEL LLM span 是否记录 provider、model 和 token usage（GenAI 语义约定字段）。
3. worker 输出与 publisher fence 日志是否能解释降级、丢弃或跳过的增强层。

## 注意事项

- 不提交本地 `.env` 中的 LangSmith key。
- 评测不依赖 LangSmith 在线数据：Ask 评测走 `evals/` 下 artifact 文件式 harness，Daily/Reader 质量回看以 PG span + LangSmith trace 为辅。
- LangSmith 相关测试覆盖在 `services/api/tests/test_tracing_isolation.py`、`test_langsmith_observability.py`、`test_reader_runtime_spans.py` 和 `test_grammar_window_observability.py`。
