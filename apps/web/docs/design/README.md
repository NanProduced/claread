# Claread Web Design

> **状态**: `CURRENT` | **最后更新**: 2026-05-15

本目录记录 Claread Web 第一版 UI/UX 的设计方向。这里的图片是页面形态和视觉语言参考，不是 mock 数据页、demo fixture 或可直接照抄的像素稿。Web 开发仍必须接入真实 Next.js BFF / FastAPI 链路。

当前文档层级应按下面顺序理解：

1. `component-library-v0.md` 是 Web 组件库主规范，也是功能页 token、theme、目录和通用组件的唯一真相源。
2. `component-system.md` 是 Reader 专项补充规范，只处理 Reader 组件、交互和视觉特例。
3. `direction-exploration.md` 只保留页面形态探索和上游 rationale，不再承担当前规范职责。

当前组件库实施状态：

- 第三方 primitive 底座评审已锁定
- 本轮优先落地 `primitives/` 包装层和 `Ladle` stories
- 自研组件仍按后续设计图评审推进

## 当前设计方向

Claread Web 采用“阅读镜头 + 原文画布工作台”方向：

- 左侧可折叠 rail 是轻量阅读仪器条，不是 SaaS sidebar。
- `/login` 升级为 Start/Login：报纸头版式 hero + 今日精读 + 3 个公开示例 + 登录面板。
- `/read` 是已登录文章画布，不再承载未登录空态或右侧状态卡片。
- `/daily` 是公开每日精读入口，像“放在门口的报纸”，不是内容 Feed 或打卡入口。
- Reader 是品牌主场：中心原文画布承载主要解析展示，画布左侧承载词典详情层，画布右侧预留 AI 工作区，句子操作和阅读设置使用短时浮层。
- Library / Vocabulary 是资产和恢复入口，保持高密度但不做后台仪表盘；Review 从生词本进入，不作为一级入口。
- Share / Export 后续作为可传播阅读产物，不阻塞第一版 Reader 主链路。

## 设计参考管理

PNG 方向图和截图只作为本地评审参考，不作为长期事实来源，也不再进入 Git。当前 `.gitignore` 已忽略 `apps/web/docs/design/**/*.png`；如果本地仍有 `directions/` 或 `component-previews/` 图片，它们只用于临时视觉对齐。

旧 `mockups/` 目录已移除。已废弃的 Reader 右侧集中说明、固定三栏、左下常驻动态面板等方向不再保留为正式设计方案，避免后续实现时把解析内容误放进右侧列表或把正文压成后台栏。

## 使用规则

- 设计图只用于结构、气质、层级和组件角色讨论。
- 具体实现以 `apps/web/PRODUCT.md`、`apps/web/DESIGN.md`、`apps/web/docs/reader-ia.md` 和真实页面验证为准。
- 不新增 `/reader/demo`、mock fixture 或用户可见示例数据回退。
- 公开示例只走 `/daily/:date` 与 `/examples/:slug`，不回到受保护功能页的匿名空态。
- 如果设计图与真实后端能力冲突，记录为后端/架构待评审项，不在 UI 阶段擅自拍板。
- 关键 UI 开发后必须用浏览器截图验证，不只依赖静态代码审查。

## 组件规范

| 文件 | 用途 |
| --- | --- |
| `component-system.md` | Reader UI/UX 组件使用规范、token 规则、组件分层和验证要求 |
| `component-library-v0.md` | Claread Web 功能页组件库统一规范，负责全站 token、theme、目录结构、通用组件和第三方准入流程 |
| `direction-exploration.md` | 页面结构和品牌气质的探索归档，作为上游 rationale 参考 |

组件预览图可以在本地生成或保留，但最终结论必须压缩回 `component-library-v0.md`、`component-system.md`、`reader-ia.md`、`PRODUCT.md` 和 `DESIGN.md`。
