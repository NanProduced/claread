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
services/api/config/reader-ask-model-options.example.json
```

## pnpm workspace

JS/TS 侧使用 pnpm workspace，范围由根目录 `pnpm-workspace.yaml` 管理：

```text
apps/*
apps/directus/extensions/*
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
| `pnpm directus:up` | 启动 Directus overlay |
| `pnpm directus:down` | 停止 Directus overlay |
| `pnpm directus:logs` | 查看 Directus 日志 |
| `pnpm directus:extensions:build` | 构建 Directus 本地扩展 |
| `pnpm directus:extensions:watch` | 监听构建 Directus 本地扩展 |

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

Directus 使用独立 overlay：

```text
infra/docker/docker-compose.directus.yml
```

启动方式固定为两个终端：

```powershell
pnpm directus:up
pnpm directus:extensions:watch
```

Directus overlay 读取：

- `infra/docker/.env`
- `apps/directus/.env.example`

本地默认管理员：

- 登录邮箱：`admin@claread.dev`
- 显示名：`claread admin`
- 密码：由本地 `apps/directus/.env` 或启动环境中的 `ADMIN_PASSWORD` 配置；仓库示例只保留占位值。

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

真实模型配置不提交。通过 `services/api/config/model-profiles.example.json`、`services/api/config/model-presets.example.json`、`services/api/config/reader-ask-model-options.example.json` 和环境变量注入模型配置。

### 三层结构

当前模型配置采用三层结构：

- **providers**：transport/protocol 能力和鉴权信息。adapter 决定协议类型（`openai_compatible` / `dashscope_native`），不是 vendor 名。同一 vendor（如 DashScope）允许按 transport 差异拆成两个 provider。
- **models**：某个 provider 下的远端模型名。同一远端模型名可以定义两个 model entry，分别引用不同 adapter 的 provider。
- **profiles**：场景级配置（Ask / workflow / planner / replan）。thinking 是否开启应该在 profile 或 route override 配置，不写死在代码里。

### Adapter 语义

| Adapter | Transport | 要求 | 适用场景 |
|---------|-----------|------|----------|
| `openai_compatible` | OpenAI 兼容 HTTP（SSE） | `base_url` 必填 | workflow / planner / eval / structured completion |
| `dashscope_native` | DashScope 原生 SDK | `api_key` 或 `api_key_env`（不需要 `base_url`） | Ask 主回答 / replan（需要可见 reasoning 流） |

同一 vendor 拆成两个 provider 是允许且推荐的。例如：

- `dashscope`（adapter = `dashscope_native`）— Ask 路由走原生协议
- `dashscope_compat`（adapter = `openai_compatible`）— workflow / planner 走兼容层

### 推荐实践

- Ask 主回答如需可见 reasoning 流，可走 native provider。
- workflow / planner / structured completion 在 native 路径未完全验证前可继续 compat。
- `reader-ask-model-options` 负责运营侧 Ask 模型选项，不等于 profiles 全量暴露。

### 计费与运行预算解耦

- `reserved_points` 不参与 prompt token 上限推导，只负责预扣/风控。
- compaction / runtime budget 应只看 `runtime_defaults` / `runtime_budget` 字段。

### Route 建议

| Route | 推荐 profile 类型 | 推荐 adapter | 建议 thinking | 备注 |
|-------|-------------------|-------------|--------------|------|
| `reader_ask` | ask-main-* | dashscope_native | 开启 | 主回答走原生，支持可见 reasoning |
| `reader_ask_planner` | ask-planner-* | openai_compatible | 关闭 | planner 先固定 compat |
| `reader_ask_replan` | ask-replan-* | dashscope_native | 开启 | replan 走原生 |
| `annotation_generation` | workflow-* | openai_compatible | 关闭 | 结构化输出，compat 已验证 |
| `dict_ai` | workflow-* | openai_compatible | 关闭 | 词典 AI |
| `daily_annotation` | workflow-* | openai_compatible | 关闭 | 批量标注 |
| `daily_analysis` | workflow-* | openai_compatible | 关闭 | 批量分析 |
| `daily_review` | workflow-* | openai_compatible | 关闭 | 批量复查 |

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

Directus scaffold 验证入口：

```powershell
pnpm directus:extensions:build
docker compose --env-file infra/docker/.env --env-file apps/directus/.env.example -f infra/docker/docker-compose.local.yml -f infra/docker/docker-compose.directus.yml config
```
