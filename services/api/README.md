# Claread API 服务

`services/api/` 是 Claread 的通用后端服务，不是某个客户端的专属后端。

当前客户端是微信小程序和 Web（Web 是 Reader 提交主链的唯一客户端），内部工具、评测和 RAG 流程也复用这套后端业务内核。

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
- Reader orchestration：Reading Record 提交、候选确认、稳定基座冻结、增强层生成与快照投影。
- Ask Claread agentic 执行链。
- 结构化阅读事实生成（Stable Document / Reading Units / Anchor Segments / Enhancement Layers）。
- 生词本、收藏、用户批注、用户笔记。
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

Reader orchestration 目前有 3 个进程级 worker entrypoint：

- `reader-enhancement-worker`：生成 translation / vocabulary / grammar 等增强层；默认必启。
- `reader-artifact-pipeline-worker`：处理 PDF、Markdown、图片 OCR 等 artifact-backed input 的提取与 materialization；文件上传链路必启。
- `reader-article-rag-index-worker`：构建文章 RAG 索引；仅在 `READER_ARTICLE_RAG_ENABLED=true` 时启用。

本地完整文件上传链路默认需要 API、Web、enhancement worker 和 artifact worker。推荐从仓库根目录一键启动：

```powershell
pnpm reader:dev
```

日志会保留在同一个终端，并带有 `api`、`web`、`enhancement`、`artifact` 彩色前缀。需要单独观察某个进程时，使用以下独立命令分别开终端：

```powershell
pnpm reader:api
pnpm reader:web
pnpm reader:worker:enhancement
pnpm reader:worker:artifact
```

只聚合启动两个默认 worker：

```powershell
pnpm reader:workers
```

启用 Article RAG 时使用 `pnpm reader:dev:rag` 或 `pnpm reader:workers:rag`。单次诊断消费仍可在 `services/api` 下运行：

```powershell
uv run reader-enhancement-worker --once
uv run reader-artifact-pipeline-worker --once
```

如果上传文件已经写入 OSS，但 `input_artifact_extraction` 长时间保持 `queued`、`attempt_count = 0`，且 Reading Record 没有 active base，通常是 artifact worker 未启动或未消费队列。如果纯文本已进入 `article_ready`，但 `translate_unit` 等任务持续排队，则检查 enhancement worker。完整本地链路和诊断 SQL 见 `docs/initiatives/reader-agentic-orchestration/modules/local-real-chain-runbook.md`。

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
  - `READER_ARTICLE_RAG_ZILLIZ_COLLECTION` 仍必须保持 Article RAG 专用，例如 `article_rag_chunks`，不要复用 `grammar_note_examples` / `sentence_analysis_examples`
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

当前数据库基线是 pre-release squashed `0001_initial.sql`。

如果本地库早于最新 `0001` 启动，新增字段需要手动补齐；例如 Ask Claread 线程模型选择字段：

```sql
ALTER TABLE reader_ask_threads ADD COLUMN IF NOT EXISTS selected_model_key TEXT;
```

词典表是高成本数据资产，保护和恢复策略见 `services/api/docs/database.md`。

Daily Reader 的 workflow、reading unit 语义和后续收口项见 `services/api/docs/daily-reader.md`。

## API 契约

关键 API：

- `POST /reader/records/input`
- `GET /reader/records`
- `GET /reader/records/{record_id}/snapshot`
- `GET /reader/records/{record_id}/events`
- `POST /reader/records/{record_id}/candidate-documents/{candidate_document_id}/confirm`
- `POST /reader/records/{reading_record_id}/ask/threads/{thread_id}/messages/stream`
- `POST /auth/wechat/login`
- `GET /auth/session/me`
- `PATCH /auth/profile`
- `GET /vocabulary`
- `POST /vocabulary`
- `POST /vocabulary/highlights`
- `GET /me/quota`
- `GET /me/quota/anonymous`
- `GET /me/credit/ledger`
- `GET /dict`
- `GET /dict/entry`
- `POST /dict/ai`
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
