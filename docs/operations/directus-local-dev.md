# Directus 本地开发

本文描述 `Claread Console` 的本地 Directus runtime 与扩展 scaffold 用法。

## 本地地址

- Directus 登录: `http://127.0.0.1:8055/admin`
- 默认首页: `http://127.0.0.1:8055/admin/content`

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
```

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

## 当前扩展状态

- `modules-bundle`
- `panels-bundle`
- `endpoints-bundle`

以上 bundle 当前仅作为 workspace scaffold 保留，用于后续 `display`、`layout`、`panel`、`module`、`endpoint` 开发。
