# Directus Scripts

> CUTOVER-CONTROL-EVAL-LONG Logical tombstones:
> - `sync-parse-run-observability-metadata.mjs` / `sync-eval-center-metadata.mjs` 已**物理删除**（Physical cutover），不再存在于仓库；重新添加属于 cutover 回归。
> - 根目录与 `apps/directus/package.json` 已移除 `parse-run:sync-metadata` / `eval-center:sync-metadata` 脚本别名。
> - `infra/scripts/init-eval-center-dev.ps1` fail-closed：在任何 Docker/DDL 之前退出。
> - `check-logical-registration.mjs` 为 L-GATE 静态门禁。
> - Reader observability 唯一 endpoint：`reader-orch`（`/reader-orch/*`）。
> - Example Lab **数据校验 hook** 保留；Example Lab UI/module/endpoint 仍注销。
> - 静态注销 ≠ 运行态 Directus metadata 已清理；metadata cleanup 与旧入口不可达属于集成 L-ACCEPT gate。

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

### check-logical-registration.mjs

Logical cutover 静态门禁：校验旧 module/panel/endpoint 未注册、`reader-orch` 正向存在、retired sync 脚本已物理删除、init 脚本 fail-closed、Example Lab 校验 hook 保留。

| 项目 | 说明 |
|------|------|
| 入口 | `pnpm --dir apps/directus run registration:check` |
| 会写 SQL / metadata | 否 |
| 典型场景 | L-GATE / CI 静态检查 |

### sync-llm-config-metadata.mjs

同步 LLM Config 控制面的 Directus metadata：6 个 collection 定义、字段元数据、module bar 入口。

| 项目 | 说明 |
|------|------|
| 入口 | `pnpm directus:llm-config:sync-metadata` |
| 会写 SQL | 视环境变量而定；默认会执行 LLM Config baseline bootstrap SQL |
| 会写 Directus metadata | 是 — 通过 Directus API 创建/更新 collection / field / module bar |
| 会重启容器 | 视环境变量而定；默认 SQL 执行后重启 Directus |
| 会清理弃用字段 | 否 — 当前无弃用项 |
| 关键环境变量 | `DIRECTUS_URL`、`DIRECTUS_CONTAINER`、`POSTGRES_CONTAINER`、`DIRECTUS_SKIP_SQL_BOOTSTRAP`、`DIRECTUS_SKIP_RESTART` |
| 典型场景 | LLM Config collection 首次部署时、字段变更后 |

补充说明：

- 该脚本会自动读取 `apps/directus/.env`。
- baseline bootstrap 读取的是 `infra/migrations/llm-config/0001_llm_config_control_plane.sql`。
- 6 个 collection：`llm_providers`、`llm_models`、`llm_profiles`、`llm_presets`、`llm_ask_options`、`llm_ask_config`。

### export-llm-config-bundle.mjs

从 Directus 读取 active 的 LLM 配置记录，导出为 3 个 JSON 文件，与 services/api schema 对齐。

| 项目 | 说明 |
|------|------|
| 入口 | `pnpm directus:llm-config:export-bundle` |
| 会写 SQL | 否 |
| 会写 Directus metadata | 否 |
| 会读 Directus 数据 | 是 — 通过 Directus API 读取 5 个 collection 的 active 记录 |
| 输出文件 | `model-profiles.json`、`model-presets.json`、`reader-ask-model-options.json` |
| 默认输出目录 | `apps/directus/.runtime/llm-config-export/` |
| 自定义输出 | `--output DIR` 参数 |
| 校验 | 是 — 导出前校验 bundle，与后端 Pydantic schema 规则对齐 |
| 典型场景 | 配置变更后导出 bundle，复制到 services/api/config/ |

### import-llm-config-bundle.mjs

从 services/api/config/ 读取 3 个源 JSON 文件，幂等 upsert 到 Directus 的 6 个 llm_* collection。

| 项目 | 说明 |
|------|------|
| 入口 | `pnpm directus:llm-config:import-bundle` |
| 会写 SQL | 否 |
| 会写 Directus 数据 | 是 — 通过 Directus API upsert llm_* collections |

## 注意事项

- 退役 sync 脚本已物理删除；`init-eval-center-dev.ps1` 不是运维入口，调用必须失败。
- 仍活跃的 LLM Config sync 会直接操作本地 PostgreSQL 和 Directus metadata，不要在生产环境运行。
- `DIRECTUS_SKIP_SQL_BOOTSTRAP=true` 可跳过 SQL 执行（仅同步 Directus metadata）。
- `DIRECTUS_SKIP_RESTART=true` 可跳过容器重启。
- 静态注销不证明现有 Directus 实例中的旧 dashboard/module bar/collection metadata 已清理；那是集成 L-ACCEPT / Data owner 工作。
