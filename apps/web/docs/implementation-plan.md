# Web 实施计划

> **状态**: `CURRENT` | **最后更新**: 2026-05-25

本文记录 Claread Web 当前稳定实施边界，只保留已确认的产品与技术合同。

## 当前产品边界

Claread Web 现在采用三段式路由结构：

- 公共区：`/`、`/about`、`/help`、`/blog`、`/daily`、`/daily/:articleId`、`/examples/:slug`、`/share/:shareId`
- 认证区：`/login`
- 私有应用区：`/app/read`、`/app/library`、`/app/vocabulary`、`/app/review`、`/app/settings`、`/app/reader/:recordId`

`/app` 只作为私有入口并立即落到 `/app/read`。旧私有路径 `/read`、`/library`、`/vocabulary`、`/review`、`/settings`、`/reader/:id` 已从产品合同中删除，不保留兼容层。

## 当前实现基线

- Web 基于 Next.js App Router，使用单一顶层 root layout。
- 公共区、认证区、私有区通过 route groups 组织代码，但真实边界由 URL 分区定义。
- 私有区统一挂载 `AppShell`；`/login` 不再通过 app shell 特判逃逸。
- 路由 contract 已集中到 `src/lib/routes.ts`，页面、导航、登录回跳和 BFF 页面 URL 均从这里取值。
- 登录态投影统一为 `signed_out`、`signed_in`、`limited_debug` 三态。
- 调试手机号会话只表现为 `limited_debug`，不再伪装成正式已登录。
- `proxy.ts` 只拦截 `/app/*`，同时保留 BFF/server-side session 校验。

## 首期能力地图

| 模块 | 页面 | 后端依赖 | 当前状态 |
| --- | --- | --- | --- |
| 输入与分析提交 | `/app/read` | `POST /analysis-tasks` | 已接入 |
| 任务状态轮询 | `/app/read` | `GET /analysis-tasks/{id}` | 已接入 |
| 结果阅读 | `/app/reader/:recordId` | `GET /records/{id}` | 已接入 |
| 历史记录 | `/app/library` | `GET /records` | 已接入 |
| 词典查词 | Reader | `GET /dict` / `GET /dict/entry` | 已接入 |
| 登录与配额 | `/login`、`/app/settings` | Web BFF + session / quota APIs | 已接入 |
| 收藏 | Reader / Library | favorites APIs | 已接入 |
| 生词本 | `/app/vocabulary` | vocabulary APIs | 已接入 |
| 生词复习 | `/app/review` | review APIs | 已接入 |
| 批注与笔记 | Reader | annotations / reader-notes APIs | 已接入 |
| 公开内容 | `/daily`、`/daily/:articleId`、`/examples/:slug` | Daily / public content APIs | 已接入基础形态 |

## 页面 IA

| 路由 | 优先级 | 说明 |
| --- | --- | --- |
| `/` | P1 | 正式公共首页与产品入口 |
| `/daily` | P1 | 每日精读入口和列表 |
| `/daily/:articleId` | P1 | 公开每日精读详情 |
| `/examples/:slug` | P1 | 公开示例详情 |
| `/login` | P1 | 手机号登录入口 |
| `/app/read` | P0 | 粘贴即解读与最近记录入口 |
| `/app/reader/:recordId` | P0 | 核心 Reader |
| `/app/library` | P0 | 阅读记录 |
| `/app/vocabulary` | P1 | 生词本 |
| `/app/review` | P1 | 生词复习 |
| `/app/settings` | P1 | 账户、配额、主题与反馈 |
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

`limited_debug` 可以进入 `/app/*`，但相关文案、能力和错误处理必须明确说明受限，不得伪装为正式账户。

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
