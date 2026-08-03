# Directus 本地开发

> **状态**: `CURRENT` | **最后验证**: 2026-08-03（CUTOVER-DOC-TRUTH-CLOSEOUT-R1：Architectural Cutover Complete；旧 Eval Center / Parse Run / Render Scene Inspector module 与 `directus:parse-run:sync-metadata` / `directus:eval-center:sync-metadata` 命令已物理删除）

本文描述 `Claread Console` 的本地 Directus runtime、metadata sync 和当前扩展开发方式。

## 本地地址

- Directus 登录: `http://127.0.0.1:8055/admin`
- 默认首页: `http://127.0.0.1:8055/admin/content`
- MCP 入口: `http://127.0.0.1:8055/mcp`

## 默认管理员

- 登录邮箱: `admin@claread.dev`
- 显示名: `claread admin`
- 密码: 由本地 `apps/directus/.env` 或启动环境中的 `ADMIN_PASSWORD` 配置；仓库示例只保留占位值。

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
pnpm directus:llm-config:sync-metadata   # LLM Config metadata sync（当前唯一保留的 metadata sync 命令）
pnpm directus:llm-config:export-bundle
pnpm directus:llm-config:import-bundle
```

旧 `directus:parse-run:sync-metadata` 与 `directus:eval-center:sync-metadata` 已在 cutover 中从 root 与 `apps/directus` `package.json` 移除，`apps/directus/scripts/check-logical-registration.mjs` 强制禁止回潮；不要在文档或脚本中再写成可用命令。

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

仓库根目录的 `.mcp.json` 是 **gitignored 本地文件**（见 `.gitignore`），不进入版本控制，也不保证每个开发者本地都存在。需要本地接入 Claude Code Directus MCP 时，按下面配置自行创建。

`.mcp.json`（仓库根目录，本地创建，不提交）：

- server name: `directus-local`
- type: `http`
- URL 默认值: `http://127.0.0.1:8055/mcp`
- 认证方式: `headersHelper`

认证 helper 脚本（已纳入版本控制）：`apps/directus/scripts/directus-mcp-headers-helper.mjs`

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

## MCP 适用边界

MCP 适合辅助 Directus schema / collection / relation / flow 开发，但不适合：

- 替代 SQL migration 设计
- 替代 Displays / Panels / Layouts / Modules 代码实现
- 替代复杂业务逻辑设计
- 替代 Git 驱动的正式变更评审

安全注意事项：

- 不对 MCP 开启自动审批
- 不把生产敏感数据带入 AI 会话
- 不混用不可信 MCP Server
- 长期应改用专用 MCP 用户和最小权限角色

## 开发坑点

- `module_bar` 配置必须写成对象数组 `[{ type: "module", id: "<module-id>", enabled: true }]`，不能写成字符串数组
- `ai_usage_events` 需按 capability scope 观察（同一 record_id 下可能同时有 Reader orchestration 各层 / `reader_ask`）
- JSONB 字段可能存在双重编码（字符串化 JSON 被存入 JSONB），防御性编程时仍需注意

## 当前约束

- Directus 只承担控制面。
- 当前已存在真实扩展与 metadata sync 链路，不再只是空骨架。
- 业务核心表是否只读保护、如何做原生展示，后续单独设计。
- 旧 Parse Run Observability / Eval Center module 已在 cutover 中物理删除，相关 metadata sync 命令（`directus:parse-run:sync-metadata` / `directus:eval-center:sync-metadata`）已移除；Console / Eval 按新 orchestration 重建属于 post-cutover backlog。
- LLM Config metadata 通过 `pnpm directus:llm-config:sync-metadata` 同步，不直接手改 live Directus metadata。
- Example Lab 作为 Directus Collection 保留，不属于已删除的 Eval Center module。

## 业务表 reset 与 Directus 配置

开发阶段允许按 `services/api/docs/database.md` 中的说明重置 `dict_*` 之外的业务表。

需要区分：

- 业务表
  - `reader_*`、`ai_usage_events`、`reader_ask_*` 等（旧 `analysis_*` 数据层清理属于 DATA-AUDIT post-cutover backlog）
- Directus system tables
  - `directus_collections`
  - `directus_fields`
  - `directus_relations`
  - `directus_presets`

当前 reset 脚本只处理业务表，不会删除 `directus_*` system tables。

因此：

- 重置业务表后，Directus 的 collection / fields / relations / presets 配置默认仍在
- 页面可视化不会因为 reset 直接消失，只是业务数据被清空
- 如果业务 schema 有新增或删减，重建业务表后应再执行一次 LLM Config metadata sync：

```powershell
pnpm directus:llm-config:sync-metadata
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
- `hooks-bundle`
- `endpoints-bundle`

当前这些 bundle 已承载真实能力，包括：

- 通用 metadata 展示 module（enum-label / event-type / json-summary / status-badge / usage-summary / record-context / relational-events / text-preview）
- LLM Config module（含 Advanced / AskClaread / Catalog / Overview / Validation 五种 mode）
- reader-orch endpoints bundle（Reader orchestration 诊断只读 endpoint）
- Example Lab AI RAG Generator interface（作为 Directus Collection 保留）
- hooks-bundle 与 panels-bundle 基础能力

旧 Parse Run Observability、Render Scene Inspector、Eval Center module 已在 cutover 中物理删除，按新 orchestration 重建属于 post-cutover backlog。
