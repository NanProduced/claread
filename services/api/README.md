# Claread API 服务

`services/api/` 是 Claread 的通用后端服务，不是某个客户端的专属后端。

当前第一个客户端是微信小程序，后续 Web、内部工具、评测和 RAG 流程也会复用这套后端业务内核。

## 技术栈

- Python 3.11+
- FastAPI
- LangGraph（workflow 编排相关）
- PydanticAI（agent / model 调用相关）
- Pydantic v2
- asyncpg
- PostgreSQL
- Redis
- LangSmith

## 职责

- 认证与 session。
- 用户资料和配额。
- 分析任务提交、排队、轮询。
- Workflow 编排和模型调用。
- 结构化分析结果生成。
- 历史记录、生词本、收藏、用户批注。
- 词典查询和缓存。
- 每日精读 pipeline。
- 反馈与奖励。

## 本地启动

安装依赖：

```powershell
uv sync
```

启动服务：

```powershell
uv run uvicorn app.main:app --reload
```

启动 Reader enhancement worker（独立进程，不挂到 FastAPI lifespan）：

```powershell
uv run reader-enhancement-worker --once
uv run reader-enhancement-worker
```

新 Reading Record / agentic orchestration 本地页面验证至少需要三个进程同时运行：

- API：`uv run uvicorn app.main:app --reload`
- Web：仓库根目录运行 `pnpm web:dev` 或 `pnpm --dir apps/web run dev`
- Worker：`uv run reader-enhancement-worker`；只想手动消费一次队列时用 `uv run reader-enhancement-worker --once`

如果 `/app/read` 或 `/app/reader-plate` 提交后只看到 `article_ready`，而 `translate_unit` 等 `reader_jobs` 长时间停在 `queued`，页面持续显示“批注生成中”，通常不是解析失败，而是 reader enhancement worker 没有运行或没有消费队列。完整本地链路和诊断 SQL 见 `docs/initiatives/reader-agentic-orchestration/modules/local-real-chain-runbook.md`。

运行测试：

```powershell
uv run pytest
```

静态检查：

```powershell
uv run python -m compileall app tests
uv run ruff check
uv run mypy app
```

`compileall` 和核心测试是当前最低验证入口。`ruff` / `mypy` 需要按后续质量门槛继续校准。

## 环境变量

复制 `.env.example` 后配置本地 `.env`。

不要把真实 API key、模型 key、数据库密码提交到仓库。

关键配置：

- `DATABASE_URL`
- `REDIS_URL`
- `WECHAT_APP_ID`
- `WECHAT_APP_SECRET`
- `PHONE_AUTH_PROVIDER`（本地默认 `mock`；真实短信使用 `aliyun_dypnsapi`）
- `ALIYUN_DYPNSAPI_SIGN_NAME`
- `ALIYUN_DYPNSAPI_LOGIN_TEMPLATE_CODE`（赠送登录/注册模板默认 `100001`）
- `ALIYUN_DYPNSAPI_*` 或本地已有的 `ALIBABA_CLOUD_ACCESS_KEY_ID` / `ALIBABA_CLOUD_ACCESS_KEY_SECRET`
- Reader 文件上传 OSS：
  - `ALIYUN_OSS_PRESIGN_ENABLED=true`
  - `ALIYUN_OSS_BUCKET` / `ALIYUN_OSS_ENDPOINT`
  - 凭证优先读取成对的 `ALIYUN_OSS_ACCESS_KEY_ID` / `ALIYUN_OSS_ACCESS_KEY_SECRET`；两者都为空时 fallback 到本地已有的 `ALIBABA_CLOUD_ACCESS_KEY_ID` / `ALIBABA_CLOUD_ACCESS_KEY_SECRET`
  - 不允许混用半组 OSS 专用凭证和半组通用阿里云凭证；如果配置了任意一个 `ALIYUN_OSS_ACCESS_KEY_*`，必须同时配置另一项
  - 启动 API 和 `reader-artifact-pipeline-worker` 的 Python 环境必须安装 OSS extra：`uv sync --extra oss`
- Reader OCR：
  - 本地启用 qwen3.5-ocr：`READER_OCR_PROVIDER_ENABLED=true`、`READER_OCR_PROVIDER_NAME=qwen`、`READER_OCR_QWEN_MODEL=qwen3.5-ocr`
  - Qwen OCR 凭证读取 `DASHSCOPE_API_KEY`，可以来自进程环境或 `services/api/.env`
- Article RAG：
  - 本地启用：`READER_ARTICLE_RAG_ENABLED=true`、`READER_ARTICLE_RAG_EMBEDDING_PROVIDER=dashscope`、`READER_ARTICLE_RAG_VECTOR_PROVIDER=zilliz`
  - Embedding 路由使用 `RAG_EMBEDDING_MODEL_PROFILE=rag-embedding-v4`，该 profile 在 `config/model-profiles.json` 中解析到 `text-embedding-v4` / `dashscope_embedding`
  - Zilliz 优先读取 `READER_ARTICLE_RAG_ZILLIZ_URI/TOKEN`；二者为空时 fallback 到 few-shot/Grammar RAG 的 `ZILLIZ_URI/TOKEN`
  - `READER_ARTICLE_RAG_ZILLIZ_COLLECTION` 仍必须保持 Article RAG 专用，例如 `article_rag_index_v1`，不要复用 `grammar_note_examples` / `sentence_analysis_examples`
- `DEFAULT_MODEL_PROFILE` / `ANNOTATION_MODEL_PROFILE` / `ASK_CLAREAD_PROFILE`
- `READER_ASK_REPLAN_MODEL_PROFILE`
- `RAG_EMBEDDING_MODEL_PROFILE` / `RAG_RERANK_MODEL_PROFILE`
- `MODEL_PROFILES_JSON`
- `MODEL_PRESETS_JSON`
- `READER_ASK_MODEL_OPTIONS_JSON`
- `DASHSCOPE_API_KEY` / `DEEPSEEK_API_KEY` / `MINIMAX_API_KEY` / `MOONSHOT_API_KEY`
- `LANGSMITH_*`
- `ZILLIZ_*`
- `BAILIAN_*`（仅作为 embedding/rerank 的 deprecated fallback）

## 数据库

PostgreSQL 是主数据源。

本地开发的 migration 位于：

```text
infra/migrations/
```

当前数据库基线是 pre-release squashed `0001_initial_schema.sql`。

如果本地库早于最新 `0001` 启动，新增字段需要手动补齐；例如 Ask Claread 线程模型选择字段：

```sql
ALTER TABLE reader_ask_threads ADD COLUMN IF NOT EXISTS selected_model_key TEXT;
```

词典表是高成本数据资产，保护和恢复策略见 `services/api/docs/database.md`。

Daily Reader 的 workflow、reading unit 语义和后续收口项见 `services/api/docs/daily-reader.md`。

## API 契约

关键 API：

- `POST /analysis-tasks`
- `GET /analysis-tasks/{task_id}`
- `GET /analysis-tasks/current`
- `POST /analyze`
- `POST /auth/wechat/login`
- `GET /auth/session/me`
- `PATCH /auth/profile`
- `GET /records`
- `GET /records/{record_id}`
- `GET /records/by-client-id/{client_record_id}`
- `GET /vocabulary`
- `POST /vocabulary`
- `POST /vocabulary/highlights`
- `GET /me/quota/anonymous`
- `GET /me/credit/ledger`
- `GET /dict`
- `GET /dict/entry`
- `GET /daily-reader/today`
- `GET /daily-reader`

所有对外 API 应声明 `response_model`，便于未来生成 `packages/contracts`。

## 多端规则

- 不把小程序限制写进后端 canonical 模型。
- 不用云端 UUID 替代客户端稳定 ID。
- 不为 Web 复制一套业务后端。
- 客户端差异通过 adapter、render profile、capability profile 处理。
- Web 登录是新的 auth adapter，不替换小程序登录流程。
- `users.id` 是 Claread 内部用户主键；手机号、微信小程序 openid、未来 Web 微信 openid 都写入 `user_identities`，不直挂到 `users`。
- 微信 `unionid` 可空；同一非空 `unionid` 的多个微信 identity 必须归属同一 `user_id`，跨用户冲突进入显式账号合并流程。
