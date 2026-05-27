# Directus Bootstrap

`apps/directus/` 承载 Claread Console 的本地 Directus bootstrap 与扩展开发骨架。

## 目标

- 接入 monorepo workspace
- 提供本地 Directus runtime
- 固定 `watch + auto reload` 开发闭环
- 预留 `Workflow Output Lab`、`RAG Workbench`、`Eval Center` 和 dashboard panel 壳子

## 目录

```text
apps/directus/
  AGENTS.md
  .env.example
  extensions/
    modules-bundle/
    panels-bundle/
    endpoints-bundle/
```

## 本地开发

先启动 Directus runtime：

```powershell
pnpm directus:up
```

再启动扩展 watch：

```powershell
pnpm directus:extensions:watch
```

默认访问 `http://127.0.0.1:8055`。

默认本地管理员：

- 登录邮箱：`admin@claread.dev`
- 显示名：`claread admin`
- 密码：`Nan12091209`

Directus 登录使用邮箱，不使用独立用户名。

## 约束

- Bootstrap 不做业务 schema、collection、API bridge、RAG / Eval 执行逻辑。
- Directus 只做控制面和可视化入口。
- 重逻辑仍保留给 Claread API / worker。
