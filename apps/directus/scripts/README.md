# Directus Scripts

本目录包含 Directus 本地开发与 metadata 同步脚本。

## 脚本清单

### watch-extensions.mjs

扩展源码热构建 watcher。监听 4 个 bundle 的 `src/` 目录变更，自动触发 `pnpm --filter <bundle> run build`。

| 项目 | 说明 |
|------|------|
| 入口 | `pnpm directus:extensions:watch` |
| 监听范围 | `modules-bundle/src`、`panels-bundle/src`、`endpoints-bundle/src`、`hooks-bundle/src` |
| 写入 | 仅构建产物，不写 metadata / SQL / 容器 |
| 典型场景 | 扩展开发期间保持运行，浏览器刷新验证 |

### directus-mcp-headers-helper.mjs

为 Claude Code MCP 连接提供动态鉴权 header。输出 JSON 格式的 `Authorization` header。

| 项目 | 说明 |
|------|------|
| 入口 | 由 `.mcp.json` 的 `headersHelper` 自动调用 |
| 优先级 | `DIRECTUS_MCP_ACCESS_TOKEN` > 登录换取短期 token |
| 登录凭据来源 | `DIRECTUS_MCP_EMAIL/PASSWORD` > `DIRECTUS_EMAIL/PASSWORD` > `ADMIN_EMAIL/PASSWORD` > 容器环境变量 |
| 写入 | 无（只输出 JSON 到 stdout） |
| 典型场景 | Claude Code 连接本地 Directus MCP 时自动鉴权 |

### sync-parse-run-observability-metadata.mjs

同步 Parse Run Observability 的 Directus metadata：collection 定义、字段元数据、关系、presets、dashboards。

| 项目 | 说明 |
|------|------|
| 入口 | `pnpm directus:parse-run:sync-metadata` |
| 会写 SQL | 是 — 通过 `docker exec` 在 PostgreSQL 中执行 migration SQL |
| 会写 Directus metadata | 是 — 通过 Directus API 创建/更新 collection / field / relation / preset / dashboard |
| 会重启容器 | 是 — SQL bootstrap 后重启 Directus 使 schema 变更生效 |
| 关键环境变量 | `DIRECTUS_URL`、`DIRECTUS_CONTAINER`、`POSTGRES_CONTAINER`、`RESET_PARSE_RUN_DASHBOARD` |
| 典型场景 | 业务表 schema 变更后、首次部署时、dashboard 需要重置时 |

### sync-eval-center-metadata.mjs

同步 Eval Center / Example Lab 的 Directus metadata：collection 定义、字段元数据、module bar 入口。同时清理已弃用字段。

| 项目 | 说明 |
|------|------|
| 入口 | `pnpm directus:eval-center:sync-metadata` |
| 会写 SQL | 视环境变量而定；默认会执行 Eval Center baseline bootstrap SQL |
| 会写 Directus metadata | 是 — 通过 Directus API 创建/更新 collection / field / module bar |
| 会重启容器 | 视环境变量而定；默认 SQL 执行后重启 Directus |
| 会清理弃用字段 | 是 — 当前清理 `eval_example_lab_entries.rag_eligible` |
| 关键环境变量 | `DIRECTUS_URL`、`DIRECTUS_CONTAINER`、`POSTGRES_CONTAINER`、`DIRECTUS_SKIP_SQL_BOOTSTRAP`、`DIRECTUS_SKIP_RESTART` |
| 典型场景 | Eval Center collection 字段变更后、首次部署时、弃用字段需要清理时 |

补充说明：

- 该脚本会自动读取 `apps/directus/.env`。
- baseline bootstrap 读取的是 `infra/migrations/eval-center/0001_eval_center_control_plane.sql`。
- 后续增量 migration 仍应按正常数据库迁移流程执行；本脚本不替代所有后续 migration。

## 注意事项

- 两个 sync 脚本都会直接操作本地 PostgreSQL 和 Directus metadata，不要在生产环境运行。
- sync 脚本依赖 Directus 容器和 PostgreSQL 容器正在运行。
- `DIRECTUS_SKIP_SQL_BOOTSTRAP=true` 可跳过 SQL 执行（仅同步 Directus metadata）。
- `DIRECTUS_SKIP_RESTART=true` 可跳过容器重启。
