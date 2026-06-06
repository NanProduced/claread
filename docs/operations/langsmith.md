# LangSmith Tracing

本文记录 Claread 通用后端的 LangSmith 使用规范。它服务本地调试、workflow 质量分析和后续 LLM-as-a-Judge 数据回看。

## 当前结论

- LangSmith 用于 trace,不作为线上业务链路依赖。
- 单一 project `claread-dev`(或操作者自配的同义命名),不再支持"每请求切 project"。所有真正会写 trace 的链路落在同一个 project,**通过 `surface` tag/metadata 区分**。
- 真实模型调用通过 `@traceable(run_type="llm")` 包装,并在子 span 上回填 usage metadata。
- PydanticAI agent 当前配置为 `instrument=False`,**不要依赖** PydanticAI 自动产出 trace。
- eval-center 默认 **不** 写 trace(`trace_scope="off"`),需要把某次 eval 跑回放到 LangSmith 时显式改成 `trace_scope="inherit"`。

## 环境变量

本地启用时配置:

```bash
LANGSMITH_ENABLED=true
LANGSMITH_API_KEY=<your-key>
LANGSMITH_PROJECT=claread-dev
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
```

默认项目名应使用 `claread-dev` 或其他 Claread 命名。真实 key 不进入仓库。

## 真正会写 LangSmith 的链路

只有以下链路会在 LangSmith 产生 trace:

| 链路 | 入口 | 根 span 名 | `surface` tag |
|------|------|-----------|---------------|
| 主产品 article analysis(learning topology) | `POST /analyze` | `article_analysis` | `analyze_direct` |
| 主产品 article analysis(academic topology) | `POST /analyze`(reading_goal=`academic`) | `article_analysis` | `analyze_direct` |
| Daily Reader 自动管线 | `services/daily_reader/pipeline.py` 选稿+workflow | `daily_reader` | `daily_reader_pipeline` |
| Daily Reader 选稿评分 | `services/daily_reader/scoring.py` | `daily_scoring_llm_call` (orphan,无根) | — |
| Overview hint worker | `OverviewTaskWorker` 后台常驻 | `learning_overview_hint` (chain) + `learning_overview_hint_llm_call` | `overview_worker` |
| Eval Workflow Lab compare(显式 inherit) | `POST /eval/article-analysis/workflow` 当 `trace_scope="inherit"` | `article_analysis` | `eval_workflow_lab` |

> learning 与 academic topology 共用同一段 `_invoke_article_analysis` 代码路径(`workflow/analyze.py`),共享根 `run_name="article_analysis"`(=`WORKFLOW_NAME`)。**不要靠根 span 名区分 learning vs academic**,要按下面这些信号:
>
> - `metadata.reading_goal` (`daily_reading` / `exam` 走 learning,`academic` 走 academic 拓扑)
> - `metadata.reading_variant`
> - 节点级 LLM span 名差异:learning 链路出现 `vocabulary_llm_call` / `grammar_llm_call` / `translation_llm_call` / `repair_llm_call`;academic 链路出现 `term_llm_call` / `academic_translation_llm_call` / `understanding_llm_call`
> - workflow 产物结构(render_scene schema 不同)

**eval-center 路径默认不产生 LangSmith trace。** 完整名单见下。

## eval-center 子路径 trace 行为

| 子路径 | 默认行为 | 备注 |
|--------|----------|------|
| `/eval/article-analysis/workflow` | 默认 `off` → 不写。设 `trace_scope="inherit"` 后会写并带 `surface:eval_workflow_lab` tag。 | 唯一会复用 `/analyze` LangGraph 主链的 eval 入口。 |
| `/eval/article-analysis/node-lab/run`、`/compare` | 不写。 | 路径走 PydanticAI(`instrument=False`),根本不发 LangSmith span。 |
| `/eval/article-analysis/node-lab/judge-execute`、`/judge-run` | 不写。 | 同上。 |
| `/eval/article-analysis/node-lab/baseline`、`/workflow-lab/baseline-bundle` | 不写。 | 纯 prompt 拼装,无 LLM 调用。 |
| `/eval/article-analysis/workflow-lab/compare-judge` | 不写。 | 走 `run_structured_completion`(httpx 直连),无 `@traceable`。 |
| `/eval/article-analysis/node-probe` | 不写。 | PydanticAI 直接调用,无 `@traceable`。 |
| `/eval/article-analysis/example-lab/generate-rag-fields` | 不写。 | 走 `run_structured_completion`,无 `@traceable`。 |

## `trace_scope` 语义

| 值 | 含义 |
|----|------|
| `"off"`(默认) | 把这次 eval 调用包在 `langsmith.run_helpers.tracing_context(enabled=False)` 里;即使全局 `LANGSMITH_TRACING=true`,这次调用不会写 LangSmith。**ContextVar 实现,并发安全**。 |
| `"inherit"` | 不做任何 wrap;按全局 `LANGSMITH_TRACING` / `LANGSMITH_PROJECT` 走。用于操作者明确希望把这次 eval 放到主 project 里看。 |

旧值 `"isolated"`(配合 `trace_project` 切 project)已被移除。原因:旧实现通过修改 `os.environ["LANGSMITH_PROJECT"]` 完成切换,在同进程并发下会污染同时刻发生的非 eval 请求(主产品 `/analyze`、Daily Reader pipeline、Overview Worker 等)。如果后续需要硬隔离 eval trace,应通过独立进程+独立 `LANGSMITH_PROJECT` 部署,而不是 per-request 切。

`trace_project` 字段在请求 schema 上保留为 *deprecated no-op*,只为兼容历史 Directus 持久化 payload,不再被任何代码读取。

## Metadata / Tags 规则

顶层 trace 包含以下字段(由 `app/workflow/tracing.py` 构造):

- tags: `["workflow", workflow_name, "surface:<surface>", <model_name>...]`
- metadata 关键字段:
  - `workflow_name`、`workflow_version`、`schema_version`
  - `request_id`、`profile_id`、`source_type`
  - `reading_goal`、`reading_variant`
  - **`surface`**(必填,见下方枚举)

模型子 span(`@traceable(run_type="llm")`)应包含:

- `ls_provider`、`ls_model_name`、`model_provider`、`model_name`
- `usage_metadata`(token 数)
- 通过父 span 继承 `surface` 等顶层字段

### `surface` 枚举

| 值 | 何时使用 |
|----|----------|
| `analyze_direct` | 默认。`POST /analyze` 直连(匿名 trial / debug)。 |
| `eval_workflow_lab` | 由 `eval_adapter/article_analysis.py` 在调用 `run_article_analysis_with_state` 之前通过 `set_trace_surface(...)` 绑定。 |
| `daily_reader_pipeline` | 由 `services/daily_reader/pipeline.py` 在 `graph.ainvoke` config 中显式传入。 |
| `overview_worker` | 由 `services/analysis/overview_task_executor.py` 在 `launch_task` / `_build_trace_metadata` 中显式传入。 |

`surface` 在 `app.observability` 暴露为常量(`SURFACE_*`)。新增链路时:
1. 在 `app/observability/tracing_context.py` 加常量。
2. 调用点用 `set_trace_surface(...)` 或直接把 `surface=` 传入 `build_workflow_root_*` builder。
3. 更新本表。

## 关键 Span

当前主分析链路重点关注:

- `vocabulary_llm_call`
- `grammar_llm_call`
- `translation_llm_call`
- `repair_llm_call`

daily reader、academic、overview hint workflow 的模型调用也已使用 `@traceable` 包装。新增 LLM 节点时必须补齐稳定 span name、provider/model metadata 和 token usage。

## 调试方式

人工分析优先按 tag 过滤:

- `surface:analyze_direct` — 真实用户/匿名 trial 的 `/analyze` 请求
- `surface:eval_workflow_lab` — Workflow Lab compare 显式 inherit 的 eval 跑
- `surface:daily_reader_pipeline` — Daily Reader 后台管线
- `surface:overview_worker` — Overview hint worker

或按 metadata 字段:

- `workflow_name = article_analysis`
- `workflow_version = 3.0.0`
- `surface = <一个上面的值>`

查看异常时优先检查:

1. 顶层 metadata 是否能定位 `surface`、`request_id`、`reading_goal`、`schema_version`。
2. LLM 子 span 是否记录 provider、model 和 token usage。
3. normalize / repair 节点 outputs 是否能解释降级、丢弃或修复行为。
4. 输出 schema 变化是否同步更新 eval 和 prompt version。

## 调试时把 eval 跑放进 LangSmith

默认 eval 请求 `trace_scope="off"`。需要某次 eval 跑出现在 LangSmith 时:

1. 后端 POST 请求 body 加 `"trace_scope": "inherit"`。
2. 该次跑会按 `surface:eval_workflow_lab` 出现在主 project(`LANGSMITH_PROJECT`)。
3. 排查完后不要把 `inherit` 设为默认 — 默认 `off` 是为了避免持续 eval 实验污染主产品观察口径。

## 注意事项

- 不提交本地 `.env` 中的 LangSmith key。
- 不再通过修改 `os.environ["LANGSMITH_PROJECT"]` 做 per-request project 切换;`eval_adapter.shared.trace_scope` 已改写为基于 `ContextVar` 的安全实现。
- 旧脚本式 regression suite 不作为当前评测入口;后续评测入口改由 Directus + eval workflow 重新设计。
- 如果需要把某类 eval 完全隔离到独立 project,部署一个第二实例(独立的 `LANGSMITH_PROJECT` env)即可,不要在单进程内动态切换。
- LangSmith 相关测试覆盖在 `services/api/tests/test_tracing_isolation.py` 和 `tests/test_langsmith_observability.py`。
