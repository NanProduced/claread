# Claread Web Design

本目录用于承载 Claread Web 端的 UI 方向探索和设计纲要。

Web 端设计必须服从根目录 `PRODUCT.md` / `DESIGN.md` 的跨端总纲，同时不能直接复制小程序冻结 UI。Web 可以拥有更完整的阅读、批注、语法可视化、导出和分享体验，但首期仍优先功能页面，不先做完整 landing / 产品介绍页。

## 当前工作流

1. 调研阅读、笔记、AI、annotation、editorial web design 的视觉模式。
2. 生成 Web 方向图，指导功能页 UI 开发。
3. 以 `apps/web/PRODUCT.md` 和 `apps/web/DESIGN.md` 作为正式设计上下文。
4. 再进入 Web 功能页 IA、组件和实现计划。

## 已确认原则

- Web 首期先做功能页面，不优先做完整 landing。
- Web Reader 默认方向是单栏阅读 + 轻旁注。
- 页面结构可以调整，Claread 品牌气质不能丢。
- 不做 SaaS dashboard、Word/WPS 编辑器、NotebookLM 资料工作台或 AI chat 中心。
- 核心差异化是语法、长难句、段落和篇章解读的可视化。

## 设计图

- `mockups/web-ui-direction-board-01.png`：Reader、Reading Studio、Grammar X-Ray、Artifact Studio 多方向设计板。
- `mockups/desktop-layout-directions-01.png`：Editorial Reader、Reading Studio、Artifact Studio 桌面布局对比设计板。

这些设计图是开发参考图，不是固定像素规范。真正实现时优先服从 `apps/web/DESIGN.md`，并用浏览器截图回看。
