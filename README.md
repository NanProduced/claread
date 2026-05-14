# Claread

Claread 是一个多端英文阅读辅助产品。当前稳定基线包含：

- `services/api/`：通用 FastAPI 后端。
- `apps/miniprogram/`：微信小程序客户端。
- `apps/web/`：Web baseline，通过 Next.js BFF 接入真实后端。
- `infra/docker/`：本地 PostgreSQL / Redis。
- `infra/migrations/0001_initial_schema.sql`：当前 pre-release 数据库基线。

微信小程序是第一个客户端，不是 Claread 的架构中心。Web、Directus 内部工具、评测系统和 few-shot RAG 都应复用同一套后端业务核心和 PostgreSQL 数据。

## 快速入口

```text
docs/README.md                       # 文档地图
docs/product/overview.md             # 产品定位
docs/product/current-state.md        # 当前状态和下一步
docs/development/mainline.md         # 当前开发主线
docs/architecture/overview.md        # 架构总览
docs/architecture/monorepo-boundaries.md # monorepo 边界
docs/architecture/multi-client.md    # 多端原则
docs/operations/local-dev.md         # 本地开发环境
services/api/README.md               # 后端服务
apps/miniprogram/README.md           # 微信小程序
apps/web/README.md                   # Web 客户端
```

## 安装依赖

Claread 使用 pnpm workspace 管理前端 app 和共享 package。新增或变更 JS 依赖后，在仓库根目录安装：

```powershell
pnpm install
```

如果遇到 `taro`、`next`、`tsc` 等命令无法识别，优先停止正在运行的 dev/watch 进程，然后在根目录重新执行 `pnpm install`，不要只在单个 app 目录做局部安装。

## 本地服务

启动 PostgreSQL / Redis：

```powershell
cd infra/docker
docker compose -f docker-compose.local.yml up -d
```

启动后端：

```powershell
cd services/api
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 客户端启动

启动微信小程序构建监听：

```powershell
pnpm miniprogram:dev
```

然后在微信开发者工具中打开 `apps/miniprogram`。

启动 Web 客户端：

```powershell
pnpm web:dev
```

默认访问 `http://127.0.0.1:3000`。

## 常用验证

```powershell
pnpm miniprogram:build
pnpm miniprogram:typecheck
pnpm web:build
pnpm web:typecheck
pnpm web:lint
```

后端验证在 `services/api/` 下运行：

```powershell
uv run pytest tests/test_health.py -q
```

当前稳定基线的完整验证入口见 `docs/operations/testing.md`。

## 开发原则

- 不为 Web 复制一套业务后端。
- 不把微信小程序限制写成全局产品限制。
- 客户端差异通过 auth adapter、render profile、capability profile 和客户端 UI 层处理。
- 真实密钥、模型配置、微信 secret、Zilliz token 和本地私有配置不提交。
- 开发前阅读当前目录最近的 `AGENTS.md`。
