# LangSmith Tracing

本文记录 Claread 通用后端的 LangSmith 使用规范。它服务本地调试、workflow 质量分析和后续 LLM-as-a-Judge 数据回看。

## 当前结论

- LangSmith 用于 trace，不作为线上业务链路依赖。
- 当前主入口是 `POST /analyze`，后续 daily reader、academic、eval workflow 也应沿用同一套追踪规则。
- 每次完整 workflow 请求应尽量形成 1 条顶层 trace，内部节点和模型调用作为子 span。
- PydanticAI agent 当前配置为 `instrument=False`，不要依赖全局 instrumentation。
- 真实模型调用通过 `@traceable(run_type="llm")` 包装，并在子 span 上回填 usage metadata。

## 环境变量

本地启用时配置：

```bash
LANGSMITH_ENABLED=true
LANGSMITH_API_KEY=<your-key>
LANGSMITH_PROJECT=claread-dev
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
```

迁移后默认项目名应改为 `claread-dev` 或其他 Claread 命名，不沿用旧仓库项目名。真实 key 不进入仓库。

## Metadata 规则

顶层 trace 只保留稳定、可过滤的字段：

- `workflow_name`
- `workflow_version`
- `schema_version`
- `request_id`
- `profile_id`
- `source_type`
- `reading_goal`
- `reading_variant`
- `trace_scope`

模型子 span 应包含：

- `ls_provider`
- `ls_model_name`
- `usage_metadata`

## 关键 Span

当前主分析链路重点关注：

- `vocabulary_llm_call`
- `grammar_llm_call`
- `translation_llm_call`
- `repair_llm_call`

daily reader 和 academic workflow 的模型调用也已使用 `@traceable` 包装。新增 LLM 节点时必须补齐稳定 span name、provider/model metadata 和 token usage。

## 调试方式

人工分析优先按以下字段过滤：

- `workflow_name = article_analysis`
- `workflow_version = 3.0.0`
- `trace_scope = analyze_local_debug`

查看异常时优先检查：

1. 顶层 metadata 是否能定位请求、reading goal 和 schema version。
2. LLM 子 span 是否记录 provider、model 和 token usage。
3. normalize / repair 节点 outputs 是否能解释降级、丢弃或修复行为。
4. 输出 schema 变化是否同步更新 eval 和 prompt version。

## 迁移注意

- 不迁移旧 `.env` 中的 LangSmith key。
- 不迁移旧脚本式 regression suite；后续评测入口改由 Directus + eval workflow 重新设计。
- 新仓库导入后，先跑 `server/tests/test_langsmith_observability.py` 等同类测试，确认 env 注入和 usage metadata 行为没有退化。
