# Claread Web

`apps/web/` 是 Claread 的 Web 产品客户端。Web 共享 `services/api/`、PostgreSQL 数据、API contracts、纯业务 utils 和 design tokens，但不复用小程序 UI，也不复制一套后端。

当前 Web 已形成可用产品基线，采用三段式路由边界：

- 公共区：`/`、`/about`、`/help`、`/blog`、`/daily`、`/daily/:articleId`、`/share/:shareId`
- 认证区：`/login`、`/signup`
- 私有应用区：`/app/read`、`/app/library`、`/app/vocabulary`、`/app/review`、`/app/settings`、`/app/settings/feedback`、`/app/settings/ledger`、`/app/reader/:recordId`

`/app` 只作为私有入口并立即落到 `/app/read`。

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
$env:CLAREAD_WEB_DEBUG_SESSION_TOKEN="<dev session token>"
```

`/login` 与 `/signup` 分别提供单一的邮箱登录和注册入口，并通过底部文字链接互相切换。密码登录直接调用同源 BFF `/api/web/auth/email/password/login`，不会创建 OTP challenge；注册通过 `/api/web/auth/email/start` 发送邮箱 OTP，密码重置使用独立的 request / complete 流程。OTP 验证成功后，BFF 才把流程投影为设置密码或重置密码。

浏览器不直接调用 FastAPI 邮箱端点。challenge、ticket 和 session token 只在 Next.js BFF 服务端处理并写入 HttpOnly Cookie，不进入普通浏览器 JSON 或认证日志。`CLAREAD_WEB_DEBUG_SESSION_TOKEN` 只在非生产环境显式注入独立的 `limited_debug` 受限 session；它与任何登录 provider 无关，未设置时 `signed_out` 不会自动获得调试访问。

未设置调试 session 或上游不可用时，功能页显示明确错误态或空态，不再在产品路径回落到 mock demo。

上线前仍有以下 `OWNER_DECISION_REQUIRED`：生产 HTTPS、同源 BFF 与可信反向代理/IP 边界；Resend 发信域名的 DKIM/SPF/DMARC 线上事实；HIBP 不可用时是否 fail-open。未确认前不得把这些事项写成已完成。

`/app/read` 的真实解析提交已通过 BFF 接入：浏览器提交到 `/api/web/reader/records/input`，Next.js BFF 携带 Web session 调 FastAPI `POST /reader/records/input`；浏览器随后轮询 `/api/web/reader/records/[recordId]/events` 与快照端点，记录可用后进入 `/app/reader/[recordId]`。文件上传链路走 `/api/web/reader/source-artifacts/*`。

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

- `implementation-plan.md` — 当前产品边界、路由与页面 IA
- `api-contract-audit.md` — **HISTORICAL**：cutover 前旧 API 面审计，仅供回看
- `reader-ia.md` — Reader 信息架构
- `tech-stack-options.md` — 技术栈选型
- `auth-routing.md` — 认证路由规则
- `design/component-system.md` — 组件系统
