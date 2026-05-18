# 技术栈

> **状态**: `CURRENT` | **最后更新**: 2026-05-18

本文记录 Claread Web 端当前使用的技术栈。决策过程和备选评估已归档；路由结构见 `implementation-plan.md`；后端接口契约见 `api-contract-audit.md`；验证命令见 `AGENTS.md` 和 `README.md`。

## 主框架

```text
Next.js App Router    # Web 主框架，支持 SSG/ISR/SSR/RSC/Client Components
React                 # UI 框架
TypeScript            # 类型系统
```

Next.js 约束：

- Route Handler 只做 Web BFF（httpOnly cookie、FastAPI 代理、session 投影），不写业务后端。
- 业务事实仍在 `services/api/` 和 PostgreSQL。
- Server Component / Client Component 边界需严格控制；Reader 强交互区域下沉到 client 组件。

## 样式与 UI 组件

```text
Tailwind CSS v4              # 样式基础和设计 token 消费
Radix UI Primitives          # 可访问、无样式的基础交互组件
  ├── @radix-ui/react-dialog
  ├── @radix-ui/react-popover
  ├── @radix-ui/react-tabs
  └── @radix-ui/react-tooltip
Floating UI                  # 精确定位词典浮层、选区工具栏、hover card
lucide-react                 # 常见工具图标
clsx + tailwind-merge        # 条件样式合并
```

shadcn/ui 使用规则：

- 选择性复制 Button、Dialog、Popover、Tooltip、Tabs、Sheet、ScrollArea 等组件源码，不引入完整模板。
- 不直接套第三方 shadcn theme。Claread Web 使用 **Claread Paper theme**：暖纸背景、墨色文字、品牌 `lens-blue` 焦点色和语义标注色。
- 正式初始化前，先在 `globals.css` 维护 shadcn-compatible semantic token aliases。
- 如果需要正式 shadcn 组件，先补 `components.json` 决策，再通过 CLI 添加，不手抄 registry 文件。

## 动效

```text
Motion    # 面板展开/折叠、Reader mode 切换、词典浮层出现/消失、批注定位反馈、任务状态过渡
```

暂不引入 GSAP / Lenis。它们更适合后续 landing 页的大型叙事动效和滚动编排；当前功能页不应提前承担体积和复杂度。

## 数据与状态

### 服务端数据

```text
Next.js Server Components    # 公开页、分享页、首屏读取和 metadata
TanStack Query               # 登录后客户端交互、轮询任务、词典缓存、mutation 和乐观更新
fetch                        # 优先使用原生 fetch；只有出现明确需求时再引入 Axios
```

### 客户端状态

```text
Zustand           # Reader UI 状态：阅读模式、面板开关、字体大小、当前焦点
next-themes       # 系统主题、明暗主题和阅读主题入口
URL search params # 阅读模式、筛选、历史搜索等可分享/可恢复状态
localStorage      # 字体、阅读模式、面板布局等非敏感偏好
httpOnly cookie   # Web session token
```

## Reader 与批注

Reader 使用只读渲染，不使用富文本编辑器作为主渲染引擎：

- 后端 `AnyRenderSceneModel` 渲染只读文章结构。
- 自建 `RenderScene -> ReaderVm -> DOM` adapter。
- 浏览器 Selection / Range API 做选区。
- `text_range`、`sentence_id`、`paragraph_id` 和 selected text 记录用户批注。
- Reader Floating Layer 封装 Floating UI，定位选区工具栏、词典浮层、hover card。
- Radix Dialog/Popover/Tooltip 处理可访问交互。

三层基础设施：

1. **Claread Paper theme**：设计 token 是品牌事实，shadcn theme 只能承接这些 token。
2. **Reader Floating Layer**：正文锚点浮层统一用 Floating UI，按钮菜单继续用 Radix/shadcn primitives。
3. **Annotation Anchor Model**：句子级批注和单句内 `text_range` 已进入首期闭环；跨句/跨段 `multi_text` 已落地；Reader DOM 持续输出 `data-paragraph-id`、`data-sentence-id`、句内 offset 和 anchor text。

可选增强（后续按需引入）：

- Tiptap / ProseMirror：用户长笔记、富文本摘录、协作批注或可编辑文档。
- CSS Custom Highlight API：增强层评估，不作为唯一高亮机制。
- TanStack Virtual：长文性能问题出现后再评估。

## 字体

阅读体验优先使用自托管字体，不运行时依赖 Google Fonts。

- UI 英文：Inter。
- 英文长文阅读：Newsreader 或同类 editorial serif。
- 中文 UI / 解释：思源黑体或思源宋体。

实现方式：

- `next/font/local` 接入本地字体文件。
- 字体文件放入 `apps/web/src/assets/fonts/` 或后续 `packages/design-tokens` 约定位置。
- 首期如果字体文件尚未入库，先使用系统 fallback，保留 font token 和接入位置。

## API Contracts

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

当前 `@claread/contracts` 已先承载批注/收藏/text range 常量，后续应评估 OpenAPI -> `packages/contracts` 生成完整 DTO 的方式。

## 测试

```text
Vitest + Testing Library    # adapter、组件和交互单测
MSW                         # API mock 集成测试
Playwright                  # E2E、截图、SEO/share smoke test
```
