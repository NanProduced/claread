# 技术栈决策与评估

本文记录 Claread Web 端技术栈决策。当前结论：**Web 端采用 Next.js App Router，不采用纯 SPA 作为主应用框架**。

## 决策摘要

Claread Web 是“内容站 + 分享页 + 阅读应用”的混合产品，不只是登录后工具页。因此首期直接使用 Next.js App Router。

核心理由：

1. `claread.com` 需要 SEO。Landing、帮助、博客、产品说明和后续公开内容页需要服务端可索引 HTML。
2. 解读结果页是天然分享单元。分享卡片的 title、description、Open Graph image 需要服务端 metadata。
3. 产品形态混合。静态内容页、公开分享页、登录后高交互应用页需要不同渲染策略，Next.js 可以在同一应用内处理。

## 开发优先级

虽然技术栈选择 Next.js，但当前阶段不优先打磨 landing 页。

优先顺序：

1. 功能页面：输入、分析提交、任务状态、Reader、历史、词典、登录/调试态。
2. Reader 和批注体验：文章渲染、inline marks、选区批注、词典浮层、侧栏、快捷键。
3. 分享结果页基础能力：公开 snapshot、metadata、OG image 占位。
4. Landing / 关于 / 帮助 / 博客：先做入口和占位，等多端产品形态稳定后再精细设计。

## 推荐技术栈

```text
apps/web/
├── Next.js App Router        # Web 主框架，支持 SSG/ISR/SSR/RSC/Client Components
├── React                     # UI 框架
├── TypeScript                # 类型系统
├── Tailwind CSS              # 样式基础和设计 token 消费
├── shadcn/ui                 # 选择性复制源码组件，不套模板
├── Radix UI Primitives       # 可访问、无样式的基础交互组件
├── Floating UI               # 精确定位词典浮层、选区工具栏、hover card
├── Motion                    # Reader 面板、状态切换、局部过渡动画
├── TanStack Query            # 客户端服务端状态、轮询、缓存和 mutation
├── Zustand                   # Reader UI 状态：模式、侧栏、选区、快捷键状态
├── next/font/local           # 自托管字体：Inter / Newsreader / 思源
├── next-themes               # 暗色模式和系统主题
├── openapi-typescript        # 从 FastAPI OpenAPI 生成 contracts
├── Vitest + Testing Library  # adapter、组件和交互单测
├── MSW                       # API mock 集成测试
└── Playwright                # E2E、截图、SEO/share smoke test
```

## 框架评估

### Next.js App Router

采用。

适用点：

- Landing / 帮助 / 博客可做 SSG/ISR。
- `/share/[shareId]` 可做 SSR 或可缓存动态渲染，并生成动态 metadata / OG image。
- `/app/*` 登录后页面可用 Server Components 加 Client Components 混合实现。
- Route Handlers 可做很薄的 Web BFF：设置 httpOnly cookie、代理 FastAPI、保护服务端 token。
- 文件路由、metadata、sitemap、robots、OG image、font/image 优化都在同一框架内。

约束：

- 不把 Next.js Route Handler 写成新业务后端。
- 业务事实仍在 `services/api/` 和 PostgreSQL。
- 需要严格控制 Server Component / Client Component 边界，Reader 强交互区域下沉到 client 组件。

### Vite + React SPA

不作为主应用框架。

适用点：

- 内部纯工具页、实验 demo、独立可嵌入组件 playground。

排除原因：

- 不满足 `claread.com` SEO。
- 不适合公开分享页 metadata 和 link preview。
- 后续仍会迁移到 SSR/SSG 框架，早期节省的复杂度会变成返工。

### Remix / React Router Framework

暂不采用。

优点：

- Web 标准和表单模型清晰。
- 数据加载和 mutation 模型成熟。

不采用原因：

- Claread 更需要内容站、OG image、ISR/SSG、RSC 和生态部署能力的组合。
- Next.js 在 SEO、metadata、部署和混合渲染上的直接路径更短。

### Astro + React Islands

暂不采用。

优点：

- 内容站和静态页面强。
- landing / blog 性能很好。

不采用原因：

- Claread 的主战场是高交互 Reader 和登录后应用，不只是内容站。
- Reader、批注、词典侧栏和任务状态会让 React island 边界复杂化。

## UI 与视觉技术栈

### 基础 UI

- Tailwind CSS：用于快速实现精细布局、响应式规则和设计 token 映射。
- shadcn/ui：选择性复制 Button、Dialog、Popover、Tooltip、Tabs、Sheet、ScrollArea 等组件源码，作为可改造基础，不引入完整模板。
- Radix UI Primitives：用于 Dialog、Popover、Tabs、Tooltip、Dropdown、Scroll Area 等可访问基础组件。
- lucide-react：用于常见工具图标。

原则：

- 不直接套现成 dashboard 模板。
- 不共享小程序 UI。
- 不在 Reader 内堆卡片式营销布局。
- 工具按钮优先图标 + tooltip，复杂设置使用菜单、分段控件、滑块、切换按钮。

### 动效

默认使用 Motion：

- 面板展开/折叠。
- Reader mode 切换。
- 词典浮层出现/消失。
- 批注列表定位和滚动反馈。
- 任务状态过渡。

暂不引入 GSAP / Lenis 作为基础依赖。它们更适合后续 landing 页的大型叙事动效、滚动编排或复杂时间线；当前功能页不应为未来 landing 提前承担体积和复杂度。

### Landing 页视觉

先做占位：

- `/`：品牌、核心入口、等待列表或登录入口。
- `/about`：占位说明。
- `/help`：占位帮助。
- `/blog`：占位列表。

后续多端产品形态稳定后，再单独做 landing 视觉方案。届时可评估：

- Motion / GSAP / Lenis：复杂滚动叙事和平滑滚动。
- Three.js / React Three Fiber：如果需要沉浸式阅读场景。
- Rive / Lottie：轻量品牌动效。
- 动态 OG image：分享传播素材。

## Reader 与批注技术栈

Reader 首期不使用富文本编辑器作为主渲染引擎。

推荐方案：

- 使用后端 `AnyRenderSceneModel` 渲染只读文章结构。
- 自建 `RenderScene -> ReaderVm -> DOM` adapter。
- 使用浏览器 Selection / Range API 做选区。
- 使用 `text_range`、`sentence_id`、`paragraph_id` 和 selected text 记录用户批注。
- 使用 Floating UI 定位选区工具栏、词典浮层、hover card。
- 使用 Radix Dialog/Popover/Tooltip 处理可访问交互。

理由：

- Claread Reader 是“分析结果阅读器”，不是自由编辑器。
- 后端 canonical result 已经有段落、句子、inline marks、sentence entries。
- 富文本编辑器会引入文档模型转换、selection mapping 和 schema 维护成本。

可选增强：

- Tiptap / ProseMirror：后续如果要做用户长笔记、富文本摘录、协作批注或可编辑文档，再引入。
- CSS Custom Highlight API：可作为增强层评估，但不能作为首期唯一高亮机制。
- TanStack Virtual：长文性能出现问题后再评估，不提前复杂化 selection 和 anchor 映射。

## 数据与状态

### 服务端数据

- Next Server Components：用于公开页、分享页、首屏读取和 metadata。
- TanStack Query：用于登录后客户端交互、轮询任务、词典缓存、mutation 和乐观更新。
- `fetch` 优先；只有出现明确需求时再引入 Axios。

### 客户端状态

- Zustand：Reader UI 状态。
- next-themes：系统主题、明暗主题和阅读主题入口。
- URL search params：阅读模式、筛选、历史搜索等可分享/可恢复状态。
- localStorage：字体、阅读模式、面板布局等非敏感偏好。
- httpOnly cookie：Web session token。

## 字体策略

阅读体验优先使用自托管字体，不运行时依赖 Google Fonts。

首期占位：

- UI 英文：Inter。
- 英文长文阅读：Newsreader 或同类 editorial serif。
- 中文 UI / 解释：思源黑体或思源宋体。

实现方式：

- 用 `next/font/local` 接入本地字体文件。
- 字体文件放入 `apps/web/src/assets/fonts/` 或后续 `packages/design-tokens` 约定位置。
- 首期如果字体文件尚未入库，先使用系统 fallback，保留 font token 和接入位置。

## API Contracts

OpenAPI 类型生成仍然是 Phase 0。

```text
services/api/openapi.json
  -> packages/contracts/types.ts
  -> apps/web/src/services/api/*
```

规则：

- `types.ts` 自动生成，不手改。
- Web 自己保留 DTO -> VM adapter。
- 小程序当前 DTO 不强制迁移，后续再逐步对齐。
- contracts 先解决类型边界，不生成重型 SDK。

## Next.js 路由草案

```text
app/
├── (marketing)/
│   ├── page.tsx                 # /，landing 占位
│   ├── about/page.tsx           # 占位
│   ├── help/page.tsx            # 占位
│   └── blog/page.tsx            # 占位
├── share/[shareId]/
│   ├── page.tsx                 # 公开分享结果页
│   └── opengraph-image.tsx      # 动态 OG 图
├── (app)/
│   ├── app/page.tsx             # 输入 / 工作台
│   ├── app/reader/[recordId]/page.tsx
│   ├── app/history/page.tsx
│   └── app/login/page.tsx
├── api/
│   └── auth/*/route.ts          # 仅 Web BFF / cookie 设置，不写业务后端
├── layout.tsx
├── robots.ts
└── sitemap.ts
```

## 后端适配影响

Next.js 选择会新增三个后端方向：

1. Web auth adapter：手机号 + 短信验证码优先，写入 `user_identities(provider='phone')` 和 `user_sessions(client_platform='web')`；后续支持微信开放平台登录/绑定。
2. Next.js BFF：浏览器持 httpOnly cookie，BFF 持内部 session token 调 FastAPI；BFF 做聚合和投影，但不复制 workflow、词典、记录、配额等核心后端业务。
3. 分享 snapshot：支持公开分享页 SSR/metadata，不直接暴露私有 records。

Reader 首期仍直接消费现有 `AnyRenderSceneModel`，不新增 Web render profile。只有当 Web 需要不同字段组合或公开分享脱敏投影时，再定义 `render_target=web_rich` 或 `share_snapshot`。

## 初始化命令占位

实际版本以初始化当天 `create-next-app` 稳定版本为准。

```powershell
pnpm create next-app apps/web --ts --app --src-dir --eslint --tailwind --use-pnpm
pnpm --dir apps/web add @tanstack/react-query zustand @floating-ui/react motion lucide-react next-themes clsx tailwind-merge
pnpm --dir apps/web add @radix-ui/react-dialog @radix-ui/react-popover @radix-ui/react-tooltip @radix-ui/react-tabs
pnpm --dir apps/web add -D vitest @testing-library/react @testing-library/user-event msw @playwright/test
pnpm add -D openapi-typescript --filter @claread/contracts
```

## 验证入口

Web 初始化后补齐：

```powershell
pnpm --dir apps/web run typecheck
pnpm --dir apps/web run lint
pnpm --dir apps/web run build
pnpm --dir apps/web run test
pnpm --dir apps/web run test:e2e
```

Playwright smoke test 需要覆盖：

- `/` landing 占位可访问，metadata 正确。
- `/app` 功能入口可渲染。
- 输入文本并提交分析。
- Reader 渲染正文、翻译、inline marks、sentence entries。
- 选区工具栏出现并能创建批注。
- 点击词显示词典浮层。
- 历史列表进入 Reader。
- `/share/[shareId]` 返回服务端 HTML 和 Open Graph metadata。
