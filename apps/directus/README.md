# Directus Scaffold

`apps/directus/` 承载 `Claread Console` 的本地 Directus runtime、扩展 workspace 骨架和后续原生化开发入口。

## 目标

- 接入 monorepo workspace
- 提供本地 Directus runtime
- 固定 `watch + auto reload` 开发闭环
- 保留 `module`、`panel`、`endpoint` 扩展包骨架
- 为后续基于 Directus 原生能力的数据展示开发提供干净起点

## 目录

```text
apps/directus/
  AGENTS.md
  .env.example
  extensions/
    modules-bundle/
    panels-bundle/
    endpoints-bundle/
  scripts/
    watch-extensions.mjs
```

## 本地开发

启动 Directus runtime：

```powershell
pnpm directus:up
```

启动扩展 watch：

```powershell
pnpm directus:extensions:watch
```

常用补充命令：

```powershell
pnpm directus:down
pnpm directus:logs
pnpm directus:extensions:build
pnpm directus:parse-run:sync-metadata
```

默认访问：

- 登录入口：`http://127.0.0.1:8055/admin`
- 默认首页：`http://127.0.0.1:8055/admin/content`
- MCP 入口：`http://127.0.0.1:8055/mcp`

默认本地管理员：

- 登录邮箱：`admin@claread.dev`
- 显示名：`claread admin`
- 密码：`Nan12091209`

Directus 登录使用邮箱，不使用独立用户名。

## MCP

当前本地 Directus 已启用原生 MCP。

- 适用版本要求：`v11.12+`
- 当前本地容器版本：`11.17.4`
- 当前设置：
  - `mcp_enabled=true`
  - `mcp_allow_deletes=false`
  - `mcp_system_prompt_enabled=true`

本地连接 URL 形式：

```text
http://127.0.0.1:8055/mcp?access_token=<DIRECTUS_ACCESS_TOKEN>
```

MCP 在 Claread 中的定位是辅助 Directus schema / collection / relation / flow 开发，不替代仓库里的 SQL migration、扩展源码开发或代码评审。

Claude Code 项目级接入已配置在仓库根目录 [`.mcp.json`](/C:/Users/nanpr/claread/claread/.mcp.json)。

- server name: `directus-local`
- transport: `http`
- auth helper: [apps/directus/scripts/directus-mcp-headers-helper.mjs](/C:/Users/nanpr/claread/claread/apps/directus/scripts/directus-mcp-headers-helper.mjs)

该 helper 会在连接时登录本地 Directus，并输出 `Authorization` header，供 Claude Code 动态鉴权。

## 当前状态

- Directus overlay 仍连接 Claread 本地 PostgreSQL。
- 扩展 workspace 仍保留为可构建、可 watch 的空骨架。
- 当前没有启用任何自定义 `Claread Console` 业务壳。
- parse-run observability 的当前正式方向是：
  - 以原始业务表作为 Directus 数据模型主体
  - 以 `analysis_records` 为中心配置关系
  - 优先用原生 `Collections / Fields / Relationships / Displays / Panels`
  - 只有原生关系无法表达的读取需求，才考虑轻量自定义 endpoint
- Task 1 / Task 2 的 collection / field / relation metadata 通过
  [apps/directus/scripts/sync-parse-run-observability-metadata.mjs](/C:/Users/nanpr/claread/claread/apps/directus/scripts/sync-parse-run-observability-metadata.mjs)
  同步，不直接手改 live Directus metadata。

## 约束

- Bootstrap 不承载业务执行逻辑。
- Directus 只做控制面和可视化入口。
- 重逻辑仍保留给 Claread API / worker。
