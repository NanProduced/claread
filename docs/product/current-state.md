# 当前状态

本文给新会话 agent 提供 Claread 当前事实。它不是迁移日志。

## 当前可运行基线

- 后端：`services/api/`，FastAPI，通用 Claread API。
- 客户端：`apps/miniprogram/`，微信小程序，当前已可连接本地后端运行。
- 数据库：`infra/docker/` 启动 PostgreSQL / Redis。
- schema：`infra/migrations/0001_initial_schema.sql`。
- 词典：`dict_entries`、`dict_lookup_targets`、`dict_redirects` 已恢复到 `claread_postgres_data`。

当前小程序和后端是可运行起点，不是 Claread 的完整功能上限。

## 已验证事实

- Docker Compose project 使用 `claread`。
- 本地 PostgreSQL volume 使用 `claread_postgres_data`。
- 本地 Redis volume 使用 `claread_redis_data`。
- PostgreSQL 使用普通 `postgres:16-alpine`，当前不依赖 pgvector。
- 词典三表恢复基线：
  - `dict_entries`: 253300
  - `dict_lookup_targets`: 1014676
  - `dict_redirects`: 848873
  - `exam_tags` 非空词条：20239
- 微信小程序本地调试需要在微信开发者工具中关闭本地域名校验，或使用已配置的合法域名。

## 多端决策

Claread 已从单一微信小程序开发转为多端产品开发。

后端不为 Web 单独复制一套。Web、小程序、Directus 和后续 App 应共享：

- PostgreSQL 数据。
- 用户、身份、记录、词典、用户资产。
- 分析任务和 workflow。
- prompt / model 配置机制。
- LangSmith trace 和后续评测数据。

客户端差异通过以下方式处理：

- auth adapter。
- render profile / render snapshot。
- capability profile。
- 客户端 UI 和平台 API adapter。

## 当前主要方向

1. 稳定微信小程序当前基线。
2. 校准新仓库文档、AGENTS 指令和本地开发流程。
3. 逐步抽出 API contracts。
4. 开始 Web 端设计和开发。
5. 规划 Directus 内部面板、评测样本管理和 few-shot RAG 数据流。

## 已知边界

- 真实 `.env`、模型 key、微信 secret、Zilliz token 不提交。
- `apps/web/`、`apps/directus/`、`evals/` 和 `packages/` 当前是规划位置，具体实现后续补齐。
- 小程序 UI/UX 是当前实现，不代表 Web 端体验上限。
- 模型输出质量和结构化输出稳定性依赖 `services/api/.env` 中的模型 profile；更换模型后需要重新跑解析链路。
- 旧脚本式 regression suite 不进入新仓库主线；评测系统后续基于 Directus + LLM-as-a-Judge 重新设计。

## 文档使用规则

新会话 agent 应先读：

1. `AGENTS.md`
2. `docs/README.md`
3. `docs/product/current-state.md`
4. 最近目录的 `AGENTS.md`

如果文档和代码冲突，以当前代码、数据库和测试结果为准，并补充文档或建立后续任务。
