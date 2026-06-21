# Services/API Agent 指令

`services/api/` 是 Claread 通用后端服务。

## 后端边界

- 不写“小程序专用后端”逻辑。
- 新客户端差异应通过 request metadata、auth adapter、source type、render profile 或客户端自行渲染处理。
- PostgreSQL 是用户资产、分析记录、任务、词典和未来评测数据的事实来源。
- Redis、队列、worker、Directus、RAG 都是扩展层，不应破坏当前 API 基线。

## Serena

- 在 `services/api/` 内做代码任务时，优先使用 Serena 做符号级阅读、声明跳转、引用分析和重构，不要先整文件通读。
- 需要 Serena 做符号级检索、引用分析或重构时，只激活当前子项目 `claread-api`。
- 已知目标文件时，优先 `get_symbols_overview` -> `find_declaration` / `find_referencing_symbols` / `find_implementations`；需要修改整个符号时，优先 `rename_symbol`、`replace_symbol_body`、`safe_delete_symbol`。
- 目标落点不明确时，先用 Serena `search_for_pattern` 在本项目内粗搜；只有跨子项目排查、非代码文件、或 Serena 当前不支持的文件类型，才优先用 RTK / shell / git diff。
- 不要从本目录向上激活仓库根目录为 Serena 项目；跨子项目检索优先用 RTK / shell / git diff。
- 未经用户明确要求，不写 Serena memory；长期事实应更新正式文档或本 `AGENTS.md`。如果 memory 与代码、测试或正式文档冲突，以后者为准，并删除或覆盖过期 memory。

## Workflow

- 当前基线是 v3 思路：preprocess、reading goal strategy、multi-agent generation、normalize/ground/repair、canonical result、render projection。
- 修改输出结构时同步更新 Pydantic schema、数据库字段、API 文档、前端消费和测试。
- 保留 `workflow_version`、`schema_version`、prompt version 和 source metadata，方便回看和 eval。

## LLM / Prompt / Trace

- prompt 继续使用 `prompts/registry.yaml` 管理版本。
- 模型配置继续使用 profile / preset / request-level `model_selection`。
- `MODEL_PROFILES_JSON` 当前固定为 `providers / models / profiles` 三层；provider 负责兼容性与鉴权，model 负责远端模型名，profile 负责场景设置。
- Ask Claread 的 thinking 开关必须由 profile 或 route settings 决定，不能在业务代码里强制改写。
- LangSmith 主要依赖 `@traceable(run_type="llm")` 子 span；PydanticAI agent 当前为 `instrument=False`。
- 新增 LLM 节点时必须补 provider/model metadata 和 usage metadata。

## 数据库

- `0001_initial_schema.sql` 是 fresh init baseline。
- 词典三表 `dict_entries`、`dict_lookup_targets`、`dict_redirects` 迁移时优先 dump/restore，不轻易重导。
- 如果重导词典，必须处理 `vocabulary_book.dict_entry_id` 稳定性和 `exam_tags` 覆盖问题。

## 验证

优先跑：

```powershell
rtk test uv run pytest tests/test_analyze_workflow.py tests/test_academic_workflow.py tests/test_task_center.py tests/test_quota_credits.py tests/test_user_assets.py tests/test_vocabulary_review.py -q
```

按需补 `compileall`、ruff、mypy 和具体模块测试。
