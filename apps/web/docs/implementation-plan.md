# Web 实施计划

> **状态**: `CURRENT` | **最后更新**: 2026-08-14

本文记录 Claread Web 当前稳定实施边界，只保留已确认的产品与技术合同。

## 当前产品边界

Claread Web 现在采用三段式路由结构：

- 公共区：`/`、`/about`、`/help`、`/blog`、`/daily`、`/daily/:articleId`、`/share/:shareId`
- 认证区：`/login`
- 私有应用区：`/app/read`、`/app/library`、`/app/vocabulary`、`/app/review`、`/app/settings`、`/app/settings/feedback`、`/app/settings/ledger`、`/app/reader/:recordId`

`/app` 只作为私有入口并立即落到 `/app/read`。旧私有路径 `/read`、`/library`、`/vocabulary`、`/review`、`/settings`、`/reader/:id` 已从产品合同中删除，不保留兼容层。

## 当前实现基线

- Web 基于 Next.js App Router，使用单一顶层 root layout。
- 公共区、认证区、私有区通过 route groups 组织代码，但真实边界由 URL 分区定义。
- 私有区统一挂载 `AppShell`；`/login` 不再通过 app shell 特判逃逸。
- 路由 contract 已集中到 `src/lib/routes.ts`，页面、导航、登录回跳和 BFF 页面 URL 均从这里取值。
- 登录态投影统一为 `signed_out`、`signed_in`、`limited_debug` 三态。
- 邮箱入口要求显式选择“登录 / 注册”：密码登录不创建 OTP challenge，注册才发送邮箱 OTP；密码重置使用独立流程。
- challenge、ticket、purpose 与 session token 只由同源 Next.js BFF 服务端处理并写入 HttpOnly Cookie，不进入普通浏览器 JSON 或认证日志。
- 显式注入的开发期 debug session 只表现为 `limited_debug`，与登录 provider 无关，也不伪装成正式已登录。
- `proxy.ts` 只拦截 `/app/*`，同时保留 BFF/server-side session 校验。

## 全局入口与快捷键基线

- Web 已提供统一全局入口：`Cmd/Ctrl + K` 打开 command palette。
- command palette 只承载 `App Global` 层能力：页面跳转、最近文章、文章搜索和少量全局命令。
- Library / Vocabulary 页内搜索、Reader 词典查词、Ask 上下文文章搜索仍然是局部搜索，不并入全局 palette。
- 快捷键按作用域区分：`App Global / Page Global / Surface Local / Ephemeral State`。
- 冲突优先级固定为：`Ephemeral State > Surface Local > Page Global > App Global`。
- 当前快捷键提示 UI 规则：
  - 菜单项右侧显示 shortcut suffix。
  - icon 按钮在 tooltip 中显示快捷键。
  - 输入框、浮层、面板使用 helper text 或 footer 就近提示。
  - 只给已真实实现的键位做提示，不给规划中的键位做假提示。

## 首期能力地图

浏览器不直连 FastAPI；私有区数据一律经过同源 Web BFF（`/api/web/*`）再到 FastAPI 上游。公共区页面由 Server Component 服务端调用上游。

| 模块 | 页面 | Web BFF / 上游依赖 | 当前状态 |
| --- | --- | --- | --- |
| 输入提交 | `/app/read` | `POST /api/web/reader/records/input`；文件走 `/api/web/reader/source-artifacts/*`（init-upload → complete-upload → submit-input） | 已接入 |
| 解析进度 | `/app/reader/:recordId` | `GET /api/web/reader/records/[recordId]/snapshot` + `/events` 轮询推进 | 已接入 |
| 解析失败恢复 | `/app/reader/:recordId` | BFF `POST /api/web/reader/records/[recordId]/recovery` → 上游 `POST /reader/records/{record_id}/recovery`；无请求 body（trigger 服务端固定 manual）；pending 期间禁止重复提交；`recovery_started` / `nothing_to_recover` 均触发 Snapshot reload；section-only failure 继续使用 section retry，不显示 record-level 恢复；401/404/409/503、网络失败、未知 outcome 与畸形上游响应一律使用固定安全文案，敏感上游内容不进 DTO/DOM | 已接入 |
| 结果阅读 | `/app/reader/:recordId` | snapshot / stable-document / candidate-document(s) / section-translation / confirmed-source BFF | 已接入 |
| 原件预览 | `/app/read`（Content Check） | BFF `GET /api/web/reader/records/[recordId]/source-preview` → 上游 record-scoped preview 元数据；以受控同源二进制流代理交付，presigned URL 不直接进普通 DOM | 已接入 |
| 历史记录 | `/app/library` | `GET /api/web/reader/records` | 已接入 |
| Ask Claread | `/app/reader/:recordId` | `ask/threads`、`ask/threads/[threadId]/messages/stream`、citations navigate、model-options BFF | 已接入 |
| 词典查词 | Reader | `/api/web/dict/lookup` / `/dict/entry` / `/dict/ai` | 已接入 |
| 登录与配额 | `/login`、`/app/settings` | `/api/web/session`、`/api/web/auth/email/*`、`/api/web/profile`；`/app/settings/ledger` 展示积分流水 | 已接入 |
| 收藏 | Reader | `/api/web/reader/records/[recordId]/favorite` | 已接入 |
| 生词本 | `/app/vocabulary` | `/api/web/vocabulary`、`/api/web/vocabulary/[id]` | 已接入 |
| 生词复习 | `/app/review` | `/api/web/review/items`、`/api/web/review/items/[id]/submit` | 已接入 |
| 批注与笔记 | Reader | `/api/web/reader/records/[recordId]/highlights`、`/notes`（含 `[id]` 更新/删除） | 已接入 |
| 反馈 | `/app/settings/feedback` | `/api/web/feedback`、`/api/web/feedback/[id]` | 已接入 |
| 公开内容 | `/daily`、`/daily/:articleId` | Server Component 经 `services/api/daily-reader.ts` 调 FastAPI daily-reader 上游 | 已接入基础形态 |

## 页面 IA

| 路由 | 优先级 | 说明 |
| --- | --- | --- |
| `/` | P1 | 正式公共首页与产品入口 |
| `/daily` | P1 | 每日精读入口和列表 |
| `/daily/:articleId` | P1 | 公开每日精读详情 |
| `/login` | P1 | 显式邮箱登录、注册与密码重置入口 |
| `/app/read` | P0 | 粘贴即解读与最近记录入口 |
| `/app/reader/:recordId` | P0 | 核心 Reader |
| `/app/library` | P0 | 阅读记录 |
| `/app/vocabulary` | P1 | 生词本 |
| `/app/review` | P1 | 生词复习 |
| `/app/settings` | P1 | 账户、配额、主题；`/app/settings/feedback` 反馈、`/app/settings/ledger` 积分流水 |
| `/share/:shareId` | P2 | 分享页 |
| `/about`、`/help`、`/blog` | P3 | 公共内容占位 |

## 路由与导航规则

- 公共区统一使用公共 header，品牌入口恒定回 `/`。
- 公共区主 CTA 根据 session 显示“登录”“打开 Claread”或“打开调试工作区”。
- 私有区 rail 只负责 `/app/*` 导航。
- 私有区必须提供返回公共区入口。
- `review` 不是一级 rail 项，只从生词本进入。

## 会话规则

Web 只对外暴露三种 session 状态：

- `signed_out`：未登录
- `signed_in`：真实可用账户
- `limited_debug`：开发期受限工作区

`limited_debug` 仅由非生产环境显式注入的 debug session 创建，可以进入 `/app/*`，但相关文案、能力和错误处理必须明确说明受限，不得伪装为正式账户。`signed_out` 不得回落为 `limited_debug`。

## 验证入口

每次 Web 路由或页面结构改动后至少运行：

```powershell
pnpm --filter=@claread/web lint
pnpm --filter=@claread/web typecheck
pnpm --filter=@claread/web build
pnpm --filter=@claread/web test:e2e
```

真实页面 smoke 至少覆盖：

- 公共区页面可访问
- 未登录访问 `/app/*` 会跳到 `/login?next=...`
- 登录后可进入私有区新路径
- 私有区 rail 激活正常
- 私有区可回到公共区
- 公开区已登录 CTA 不再固定显示“登录”
