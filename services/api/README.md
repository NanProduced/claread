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
- `MODEL_PROFILES_JSON`
- `LANGSMITH_*`
- `ZILLIZ_*`
- `BAILIAN_*`

## 数据库

PostgreSQL 是主数据源。

本地开发的 migration 位于：

```text
infra/migrations/
```

当前数据库基线是 pre-release squashed `0001_initial_schema.sql`。

词典表是高成本数据资产，保护和恢复策略见 `services/api/docs/database.md`。

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
