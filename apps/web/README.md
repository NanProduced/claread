# Claread Web

`apps/web/` 是 Claread 的 Web 客户端。Web 共享 `services/api/`、PostgreSQL 数据、API contracts、纯业务 utils 和 design tokens，但不复用小程序 UI，也不复制一套后端。

当前 Web 已初始化为 Next.js App Router 项目。首期重点是功能页面、Reader、批注、词典、历史和分享页骨架；landing、关于、帮助、博客等内容页先保留占位入口。

## 技术栈

- Next.js App Router
- React
- TypeScript
- Tailwind CSS
- Radix Primitives / shadcn/ui selective copy
- TanStack Query
- Zustand
- Floating UI
- Motion

## 启动

安装依赖应在仓库根目录执行：

```powershell
pnpm install
```

启动开发服务器：

```powershell
pnpm web:dev
```

默认访问：

```text
http://127.0.0.1:3000
```

## BFF / API 接入

Web 浏览器不直接消费 FastAPI 原始端点。Next.js Server Components / Route Handlers 通过 `apps/web/src/services/api/` 的 server-only upstream client 调用 FastAPI，再由 `apps/web/src/adapters/` 投影为 Web view model。

开发期可选环境变量：

```powershell
$env:CLAREAD_FASTAPI_BASE_URL="http://127.0.0.1:8000"
$env:CLAREAD_PHONE_AUTH_PROVIDER="mock"
$env:CLAREAD_WEB_DEBUG_SESSION_TOKEN="<dev session token>"
$env:CLAREAD_WEB_DEMO_RECORD_ID="<optional upstream record id for /app/reader/demo-record>"
```

本地手机号登录默认使用 mock provider，验证码固定为 `888888`。如需让 Web 走 FastAPI 手机号登录，将 `CLAREAD_PHONE_AUTH_PROVIDER` 设置为 `fastapi`（或兼容值 `aliyun_dypnsapi`），并在 `services/api/.env` 中启用 `PHONE_AUTH_PROVIDER="aliyun_dypnsapi"`。阿里云云通信号码认证服务 Dypnsapi 的模板、签名、AK/SK 都只配置在 FastAPI 侧，Web BFF 只负责 httpOnly cookie 与 session 投影。

未设置调试 session、上游不可用或记录缺少完整 `render_scene_json` 时，Reader 会明确回落到 mock demo，保持现有页面可运行。

`/app` 的真实解析提交已通过 BFF 接入：浏览器提交到 `/api/web/analysis/submit`，Next.js BFF 携带 Web session 调 FastAPI `/analysis-tasks`；同步等待超时时，浏览器继续轮询 `/api/web/analysis/tasks/[taskId]`，任务成功后跳转到 `/app/reader/[cloudRecordId]`。

验证：

```powershell
pnpm web:typecheck
pnpm web:lint
pnpm web:build
```

也可以直接定位 Web workspace：

```powershell
pnpm --filter @claread/web run dev
pnpm --filter @claread/web run build
```

如果出现 `next` 或 `tsc` 无法识别，优先停止所有 dev/watch 进程，然后回到仓库根目录重新执行 `pnpm install`。

## 文档

Web 专项文档位于 `apps/web/docs/`：

- `development-tracker.md`
- `tech-stack-options.md`
- `api-contract-audit.md`
- `reader-ia.md`
