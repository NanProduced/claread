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

## 阿里云 OSS dev bucket

Reader 输入适配的文件类 Source Artifact 在开发测试阶段使用独立 OSS bucket。当前 dev bucket：

```text
bucket: claread-dev
endpoint: https://oss-cn-shenzhen.aliyuncs.com
bucket domain: claread-dev.oss-cn-shenzhen.aliyuncs.com
```

本地访问使用环境变量注入凭证，不提交真实 key：

```text
ALIBABA_CLOUD_ACCESS_KEY_ID
ALIBABA_CLOUD_ACCESS_KEY_SECRET
ALIYUN_OSS_BUCKET=claread-dev
ALIYUN_OSS_ENDPOINT=https://oss-cn-shenzhen.aliyuncs.com
```

约束：

- dev bucket 只用于开发测试，不存放生产用户数据。
- bucket 必须保持 private；前端直传应走后端签发的临时凭证，不暴露长期 AK/SK。
- OSS object 只作为 Source Artifact / derived artifact 的外部存储，不成为 Reader 业务事实源；事实仍落 PostgreSQL。
- 后续正式上线单独创建 prod bucket，并重新评估 lifecycle、KMS、CDN、跨区域容灾和最小权限 RAM policy。

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
- `apps/directus/.env`（本地文件，不提交；仓库只提供 `apps/directus/.env.example` 占位）

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

### OpenAI profile 解析优先级

`openai_compatible` adapter 的模型 profile（`OpenAIModelProfile`）按以下优先级解析：

1. **显式 `openai_profile`**：provider 或 model 上声明的 `openai_profile` 字段。这是推荐方式，配置完全显式，无隐式推断。
2. **`provider_options.profile` hint**：当 `openai_profile` 未声明时，`provider_options.profile` 的值会映射到内置 profile builder。支持的 hint 值：
   - `"deepseek_v4"` — DeepSeek V4 reasoning + prompted JSON output
   - `"reasoning_content"` — 通用 reasoning_content 字段支持（DashScope compat、Zhipu 等）
   - `"moonshot"` — Moonshot AI provider 特殊 profile
3. **无 profile**：以上均未配置时，`OpenAIChatModel` 使用 pydantic-ai 自身默认值。

旧版 URL / model name 启发式（如 `"deepseek.com" in base_url`、`model_name.startswith("qwen")`）已移除。新配置应通过 `openai_profile` 或 `provider_options.profile` 显式声明。

### 推荐实践

- Ask 主回答如需可见 reasoning 流，可走 native provider。
- workflow / planner / structured completion 在 native 路径未完全验证前可继续 compat。
- `reader-ask-model-options` 负责运营侧 Ask 模型选项，不等于 profiles 全量暴露。
- `reader-ask-model-options` 中 `enabled=true` 的选项应视为“可实际运行承诺”：三个 Ask route 不仅要能 resolve，还要能在当前后端构建出对应 model adapter。

### DashScope native adapter

适用于 Ask 主回答 / replan 需要验证 DashScope 原生 reasoning 路径的场景，走 `dashscope.AioGeneration.call(stream=True, result_format="message", incremental_output=True)`。

- provider entry: `adapter: "dashscope_native"`，**不需要 `base_url`**
- 鉴权: `api_key_env: "DASHSCOPE_API_KEY"`
- model entry 仍需声明 `model_name`（如 `qwen3.7-max`）
- 业务层 `prepare_stream_model_settings` 已收口到 `model_config.adapter` 判断，不使用 URL 启发式
- planner / workflow / annotation / structured completion **保持 `openai_compatible`**

同一远端模型可同时存在 `qwen37-max` (compat) 和 `qwen37-max-native` (native) 两个 model entry；profile 按场景引用。

**为什么需要 native**：pydantic-ai 的 OpenAI 兼容适配层在解析 DashScope 流式 chunk 时，会丢失 `reasoning_content` 字段（chunk schema 不含此字段），导致 Ask 主回答在 `qwen3.7-max` / `glm-5.1` 下看不到「思考过程」展开。`dashscope_native` adapter 绕过 OpenAI 兼容层，直接读取 DashScope native `Message` 协议里的 `reasoning_content`。

**当前限制**：

- native reasoning 事件链仍在持续联调，不能把它视为所有 Ask route 都已完全收口。
- 当前 native path 的 tool bridge / non-stream replan 语义仍有已知限制；在正式收口前，不应把 `dashscope_native` 当成 workflow / planner / structured completion 的通用 adapter。

**fallback**：若 `dashscope_native` 路径出现 tool 调用或流式兼容问题，只需在 `services/api/config/model-profiles.json` 把对应 ask-* profile 的 `model` 字段切回 compat entry（`qwen37-max` / `glm51`），adapter 自动回退到 `openai_compatible`。

### 计费与运行预算解耦

- `reserved_points` 不参与 prompt token 上限推导，只负责预扣/风控。
- compaction / runtime budget 应只看 `runtime_defaults` / `runtime_budget` 字段。

### Route 建议

| Route | 推荐 profile 类型 | 推荐 adapter | 建议 thinking | 备注 |
|-------|-------------------|-------------|--------------|------|
| `reader_ask` | ask-main-* | dashscope_native | 开启 | 主回答走原生，支持可见 reasoning |
| `reader_ask_replan` | ask-replan-* | dashscope_native | 开启 | replan 走原生 |
| `annotation_generation` | workflow-* | openai_compatible | 关闭 | 结构化输出，compat 已验证 |
| `dict_ai` | workflow-* | openai_compatible | 关闭 | 词典 AI |
| `daily_annotation` | workflow-* | openai_compatible | 关闭 | 批量标注 |
| `daily_analysis` | workflow-* | openai_compatible | 关闭 | 批量分析 |
| `daily_review` | workflow-* | openai_compatible | off | batch review | <!-- reader-runtime --> |
Reader worker / Article RAG / compaction 的环境变量与运行入口见 `docs/operations/reader-runtime.md`。单文件边界、pending/available 可达性与 provider=0 fail-closed 见该文档 Artifact Input Operations Contract。

结构化输出链路对模型能力敏感。更换 `DEFAULT_MODEL_PROFILE` 或 `ANNOTATION_MODEL_PROFILE` 后，需要重新验证解析结果是否包含词汇、语法、句式和翻译字段。

### Resolve vs Buildable

配置层有两个层次的可用性判断：

- **Resolve-only**：`resolve_model_config()` 成功返回 `ResolvedModelConfig`。表示 profile → model → provider 链路完整，可以拿到模型身份信息。适用于静态目录展示、trace 元数据、eval 标记等不需要实际构建模型实例的场景。
- **Buildable**：在 resolve 基础上，`build_model_instance()` 还能成功构建 `Model` 实例。表示该配置可以实际用于 LLM 调用。适用于 Ask 面板可选项、runtime 调用等对用户承诺可用的场景。

当前 buildability 校验点：

| 场景 | 校验层次 | 说明 |
|------|---------|------|
| `validate_model_selection(buildable=False)` | resolve-only | 默认只校验链路完整性 |
| `validate_model_selection(buildable=True)` | buildable | 显式要求主 profile 与声明的 fallback profiles 都可 build |
| Ask model option catalog (`_validate_catalog`) | buildable | 每个 enabled option 必须可 build |
| Ask fallback option (无 enabled option 时) | buildable | 路由默认必须可 build |
| `build_model_for_route` | buildable | runtime 实际构建 |

## 验证入口

后端最小健康测试：

```powershell
cd services/api
uv run pytest tests/test_health.py -q
```

后端当前核心回归入口：

```powershell
cd services/api
uv run pytest tests/test_quota_credits.py tests/test_user_assets.py tests/test_vocabulary_review.py -q
```

Reader orchestration 与 Ask 的完整验证入口见 `docs/operations/testing.md`。

小程序和 Web 的构建/类型检查优先使用根目录脚本，见上方 `pnpm workspace`。

Directus scaffold 验证入口：

```powershell
pnpm directus:extensions:build
docker compose --env-file infra/docker/.env --env-file apps/directus/.env -f infra/docker/docker-compose.local.yml -f infra/docker/docker-compose.directus.yml config
```
