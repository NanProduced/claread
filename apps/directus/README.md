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
```

默认访问：

- 登录入口：`http://127.0.0.1:8055/admin`
- 默认首页：`http://127.0.0.1:8055/admin/content`

默认本地管理员：

- 登录邮箱：`admin@claread.dev`
- 显示名：`claread admin`
- 密码：`Nan12091209`

Directus 登录使用邮箱，不使用独立用户名。

## 当前状态

- Directus overlay 仍连接 Claread 本地 PostgreSQL。
- 扩展 workspace 仍保留为可构建、可 watch 的空骨架。
- 当前没有启用任何自定义 `Claread Console` 业务壳。
- `infra/migrations/0002_console_parse_run_views.sql` 保留为后续数据展示设计的候选输入，但当前不作为活跃 UI / API 契约。

## 约束

- Bootstrap 不承载业务执行逻辑。
- Directus 只做控制面和可视化入口。
- 重逻辑仍保留给 Claread API / worker。
