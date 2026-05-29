# Directus 本地开发

本文描述 `Claread Console` 的本地 Directus runtime 与扩展 scaffold 用法。

## 本地地址

- Directus 登录: `http://127.0.0.1:8055/admin`
- 默认首页: `http://127.0.0.1:8055/admin/content`
- MCP 入口: `http://127.0.0.1:8055/mcp`

## 默认管理员

- 登录邮箱: `admin@claread.dev`
- 显示名: `claread admin`
- 密码: `Nan12091209`

说明:

- 登录使用邮箱，不使用独立用户名。
- 本地开发不要求真实可收信邮箱，只要求格式合法。

## 启动命令

```powershell
pnpm directus:up
pnpm directus:extensions:watch
```

常用补充命令:

```powershell
pnpm directus:down
pnpm directus:logs
pnpm directus:extensions:build
pnpm directus:parse-run:sync-metadata
```

## MCP

当前本地 Directus 已启用原生 MCP。

- 当前容器版本: `11.17.4`
- 当前设置:
  - `mcp_enabled=true`
  - `mcp_allow_deletes=false`
  - `mcp_system_prompt_enabled=true`

本地连接格式:

```text
http://127.0.0.1:8055/mcp?access_token=<DIRECTUS_ACCESS_TOKEN>
```

建议:

- 本地先禁用 delete
- 不要对 MCP 开启自动审批
- 后续若长期使用，再单独创建专用 MCP 用户和最小权限角色

## Claude Code 项目接入

当前仓库根目录已提供项目级 [`.mcp.json`](/C:/Users/nanpr/claread/claread/.mcp.json)。

- server name: `directus-local`
- type: `http`
- URL 默认值: `http://127.0.0.1:8055/mcp`
- 认证方式: `headersHelper`

认证 helper:

- [apps/directus/scripts/directus-mcp-headers-helper.mjs](/C:/Users/nanpr/claread/claread/apps/directus/scripts/directus-mcp-headers-helper.mjs)

helper 逻辑：

- 若存在 `DIRECTUS_MCP_ACCESS_TOKEN`，直接用静态 token
- 否则使用本地 Directus 登录接口换取短期 access token

建议验证方式：

```powershell
claude mcp list
```

在本仓库目录启动 Claude Code 后，也可以直接执行 `/mcp` 查看 `directus-local` 状态。

## 热更新规则

| 改动类型 | 是否热更新 | 操作 |
|------|------|------|
| `apps/directus/extensions/**/src` | 是 | 保持 `pnpm directus:extensions:watch` 运行，浏览器刷新验证 |
| 扩展 `package.json` / manifest | 否 | `pnpm install`，再执行 `pnpm directus:extensions:build` |
| `apps/directus/.env.example` | 否 | 重启 Directus 容器 |
| `infra/docker/docker-compose.directus.yml` | 否 | 重建 Directus 容器 |

推荐重启方式:

```powershell
docker compose --env-file infra/docker/.env --env-file apps/directus/.env.example -f infra/docker/docker-compose.local.yml -f infra/docker/docker-compose.directus.yml up -d --force-recreate directus
```

## 当前约束

- Directus 只承担控制面。
- 当前扩展包保留为空骨架，不预置业务壳。
- 业务核心表是否只读保护、如何做原生展示，后续单独设计。
- parse-run observability 当前优先走原始业务表 + 关系建模，复杂跨表摘要按需补轻量只读接口。
- Parse run observability 的 Task 1 / Task 2 metadata 通过 repo 内脚本同步，不直接手改 live Directus metadata。

## 业务表 reset 与 Directus 配置

开发阶段允许按 `services/api/docs/database.md` 中的说明重置 `dict_*` 之外的业务表。

需要区分：

- 业务表
  - `analysis_*`、`ai_usage_events`、`reader_ask_*` 等
- Directus system tables
  - `directus_collections`
  - `directus_fields`
  - `directus_relations`
  - `directus_presets`

当前 reset 脚本只处理业务表，不会删除 `directus_*` system tables。

因此：

- 重置业务表后，Directus 的 collection / fields / relations / presets 配置默认仍在
- 页面可视化不会因为 reset 直接消失，只是业务数据被清空
- 如果业务 schema 有新增或删减，重建业务表后应再执行一次：

```powershell
pnpm directus:parse-run:sync-metadata
```

建议：

- `reset_dev_keep_dict.sql`
  - 适合清空业务数据但保留现有 Directus 配置
- `reset_full_keep_dict.sql`
  - 适合表结构升级后重建业务表
  - 跑完 migration 后，再补一次 Directus metadata sync

## 当前扩展状态

- `modules-bundle`
- `panels-bundle`
- `endpoints-bundle`

以上 bundle 当前仅作为 workspace scaffold 保留，用于后续 `display`、`layout`、`panel`、`module`、`endpoint` 开发。
