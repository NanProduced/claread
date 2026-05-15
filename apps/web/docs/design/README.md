# Claread Web Design

> **状态**: `CURRENT` | **最后更新**: 2026-05-15

本目录记录 Claread Web 第一版 UI/UX 的设计方向。这里的图片是页面形态和视觉语言参考，不是 mock 数据页、demo fixture 或可直接照抄的像素稿。Web 开发仍必须接入真实 Next.js BFF / FastAPI 链路。

## 当前设计方向

Claread Web 采用“阅读镜头 + 原文画布工作台”方向：

- 左侧可折叠 rail 是轻量阅读仪器条，不是 SaaS sidebar。
- `/login` 升级为 Start/Login：报纸头版式 hero + 今日精读 + 3 个公开示例 + 登录面板。
- `/read` 是已登录文章画布，不再承载未登录空态或右侧状态卡片。
- `/daily` 是公开每日精读入口，像“放在门口的报纸”，不是内容 Feed 或打卡入口。
- Reader 是品牌主场：中心原文画布承载主要解析展示，左侧常驻词典和动态操作区，右侧预留 AI 工作区。
- Library / Vocabulary 是资产和恢复入口，保持高密度但不做后台仪表盘；Review 从生词本进入，不作为一级入口。
- Share / Export 后续作为可传播阅读产物，不阻塞第一版 Reader 主链路。

## 设计图

| 文件 | 用途 |
| --- | --- |
| `directions/web-v1-login-start-direction.png` | `/login` Start/Login 报纸头版式方向 |
| `directions/web-v1-daily-reader-direction.png` | `/daily` 每日精读入口方向 |
| `directions/web-v1-reader-permanent-dictionary-direction.png` | Reader 桌面工作台：左上常驻词典，左下句子操作，中心原文画布，右侧 AI 收起 |
| `directions/web-v1-reader-settings-dynamic-panel-direction.png` | Reader 桌面工作台：左上常驻词典，左下阅读设置动态面板 |
| `directions/web-v1-reader-ai-workspace-direction.png` | Reader 桌面工作台：右侧 Ask Claread 展开，围绕当前原文上下文对话 |

旧 `mockups/` 目录已移除。已废弃的 Reader 右侧集中说明方向图也不再保留，避免后续实现时把解析内容误放进右侧列表。

## 使用规则

- 设计图只用于结构、气质、层级和组件角色讨论。
- 具体实现以 `apps/web/PRODUCT.md`、`apps/web/DESIGN.md`、`apps/web/docs/reader-ia.md` 和真实页面验证为准。
- 不新增 `/reader/demo`、mock fixture 或用户可见示例数据回退。
- 公开示例只走 `/daily/:date` 与 `/examples/:slug`，不回到受保护功能页的匿名空态。
- 如果设计图与真实后端能力冲突，记录为后端/架构待评审项，不在 UI 阶段擅自拍板。
- 关键 UI 开发后必须用浏览器截图验证，不只依赖静态代码审查。
