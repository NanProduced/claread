# Claread Web Design

> **状态**: `CURRENT` | **最后更新**: 2026-07-13

本目录记录 Claread Web 第一版 UI/UX 的设计方向。这里的图片是页面形态和视觉语言参考，不是 mock 数据页、demo fixture 或可直接照抄的像素稿。Web 开发仍必须接入真实 Next.js BFF / FastAPI 链路。

当前文档层级应按下面顺序理解：

1. `../DESIGN.md` 是 Web 设计系统、token、组件契约与页面模式的唯一治理入口。
2. `component-system.md` 是 Reader 专项补充规范，只处理 Reader 画布、锚点、组件与交互特例。

当前组件库实施状态：

- 第三方 primitive 底座评审已锁定
- 本轮优先落地 `primitives/` 包装层和 `Ladle` stories
- 自研组件仍按后续设计图评审推进

## 当前设计方向

Claread Web 当前稳定方向已经沉淀到：

- `../PRODUCT.md`
- `../DESIGN.md`
- `../reader-ia.md`
- `component-system.md`

## 品牌资产入口

Web 端设计不能只凭文字描述 Claread 品牌。做 UI、产品页、分享页、导出页、登录页或任何含品牌露出的页面前，必须检查：

- `../../../../packages/design-tokens/assets/brand/README.md`
- `../../../../packages/design-tokens/assets/brand/logos/`
- `../../../../packages/design-tokens/assets/brand/icons/`
- `../../../../packages/design-tokens/assets/brand/design/`

这些是跨端品牌源资产。Web 运行时代码只引用复制或导出到 `../../public/brand/` 的文件。当前 Web 侧品牌组件在 `../../src/components/brand/BrandMarks.tsx`，页面中需要 Logo、横版标识、光圈水印或小印章时，优先复用这里的 `BrandLockup`、`ApertureWatermark`、`ClareadStamp`。

如果设计需要新增品牌图片，先从 `packages/design-tokens/assets/brand/` 选择源资产，再导出到 `apps/web/public/brand/`；不要在页面内重画 Logo、发明新品牌图形或用纯文字临时代替。

## 设计参考管理

PNG 方向图和截图只作为本地评审参考，不作为长期事实来源，也不再进入 Git。当前 `.gitignore` 已忽略 `apps/web/docs/design/**/*.png`；如果本地仍有 `directions/` 或 `component-previews/` 图片，它们只用于临时视觉对齐。

旧 `mockups/` 目录已移除。已废弃的 Reader 右侧集中说明、固定三栏、左下常驻动态面板等方向不再保留为正式设计方案，避免后续实现时把解析内容误放进右侧列表或把正文压成后台栏。

## 使用规则

- 设计图只用于结构、气质、层级和组件角色讨论。
- 具体实现以 `apps/web/PRODUCT.md`、`apps/web/DESIGN.md`、`apps/web/docs/reader-ia.md` 和真实页面验证为准。
- 不新增 `/app/reader/demo`、mock fixture 或用户可见示例数据回退。
- 公开示例只走 `/daily/:articleId` 与 `/examples/:slug`，不回到受保护功能页的匿名空态。
- 如果设计图与真实后端能力冲突，记录为后端/架构待评审项，不在 UI 阶段擅自拍板。
- 关键 UI 开发后必须用浏览器截图验证，不只依赖静态代码审查。

## 组件规范

| 文件 | 用途 |
| --- | --- |
| `../DESIGN.md` | 全站设计系统、token、组件契约、页面模式与迁移边界 |
| `component-system.md` | Reader UI/UX 组件使用规范、画布/锚点特例和验证要求 |

组件预览图可以在本地生成或保留，但最终结论必须压缩回 `../DESIGN.md`、`component-system.md`、`reader-ia.md` 与 `../PRODUCT.md`。方向图不构成当前规范。
