# 本地开发环境

本文描述 Claread 本地开发环境。

## 配置原则

- 不提交真实 `.env`。
- 不提交模型 API key、微信 secret、Zilliz token。
- 不写个人局域网 IP。
- 不把真实 DB / Redis 密码提交到仓库。
- 本地和生产配置通过 `.env.example` 区分。

## 推荐配置文件

```text
.env.example
services/api/.env.example
apps/miniprogram/.env.example
infra/docker/.env.example
services/api/config/model-profiles.example.json
services/api/config/model-presets.example.json
```

## pnpm workspace

JS/TS 侧使用 pnpm workspace，范围由根目录 `pnpm-workspace.yaml` 管理：

```text
apps/*
packages/*
```

依赖安装和刷新必须优先在仓库根目录执行：

```powershell
pnpm install
```

不要在 Web、小程序 watch 进程运行时安装依赖。安装过程中如果被中断，workspace 的 `.bin` 链接可能处于半完成状态，表现为 `taro`、`next` 或 `tsc` 无法识别。这通常不是 Web 和小程序冲突，处理方式是：

1. 停止所有 `pnpm ... dev` / `taro ... --watch` / `next dev` 进程。
2. 回到仓库根目录执行 `pnpm install`。
3. 再用根目录脚本启动或验证。

常用根目录脚本：

| 命令 | 用途 |
|------|------|
| `pnpm miniprogram:dev` | 小程序 Taro watch 构建 |
| `pnpm miniprogram:build` | 小程序一次性 weapp 构建 |
| `pnpm miniprogram:typecheck` | 小程序 TypeScript 检查 |
| `pnpm web:dev` | Web Next.js dev server |
| `pnpm web:build` | Web 生产构建 |
| `pnpm web:typecheck` | Web TypeScript 检查 |
| `pnpm web:lint` | Web ESLint 检查 |

需要直接定位 workspace 包时，也可以使用：

```powershell
pnpm --filter claread-miniprogram run build:weapp
pnpm --filter @claread/web run build
```

## 数据库

本地 Docker Compose 位于：

```text
infra/docker/docker-compose.local.yml
```

启动：

```powershell
cd infra/docker
docker compose -f docker-compose.local.yml up -d
```

当前使用 Claread 命名的 project 和 volume：

```text
claread
claread_postgres_data
claread_redis_data
```

词典三表已恢复到 `claread_postgres_data`。短期连接其他 Postgres 只作为本地 fallback，并且只写在本地 `.env`，不进入默认 compose。

compose 中的 DB / Redis 用户名和密码必须通过 `infra/docker/.env` 注入。

## 小程序 API 地址

`apps/miniprogram` 使用：

```text
TARO_APP_API_BASE_URL=http://localhost:8000
```

dev/staging/prod 由构建环境注入。

微信开发者工具本地调试 `http://localhost:8000` 时，需要关闭本地域名校验，或使用已经配置到小程序后台的合法 request 域名。

## Redis

本地可以默认关闭或按需开启。生产环境如有多 worker、缓存和任务能力，应显式启用 Redis。

## 模型配置

真实模型配置不提交。通过 `services/api/config/model-profiles.example.json`、`services/api/config/model-presets.example.json` 和环境变量注入模型配置。

结构化输出链路对模型能力敏感。更换 `DEFAULT_MODEL_PROFILE` 或 `ANNOTATION_MODEL_PROFILE` 后，需要重新验证解析结果是否包含词汇、语法、句式和翻译字段。

## 验证入口

后端最小健康测试：

```powershell
cd services/api
uv run pytest tests/test_health.py -q
```

后端当前核心回归入口：

```powershell
cd services/api
uv run pytest tests/test_analyze_workflow.py tests/test_academic_workflow.py tests/test_task_center.py tests/test_quota_credits.py tests/test_user_assets.py tests/test_vocabulary_review.py -q
```

小程序和 Web 的构建/类型检查优先使用根目录脚本，见上方 `pnpm workspace`。
