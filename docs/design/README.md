# 设计文档入口

本目录用于承载 Claread 的跨端设计原则。它不直接复制旧仓库 `docs/uiux/` 或 `.impeccable.md`。

## 分层

| 层级 | 位置 | 内容 |
|------|------|------|
| 全局产品气质 | `docs/product/design-context.md` | Claread 的阅读体验、语气、视觉方向和反模式 |
| 全局设计规则 | `docs/design/` | 跨端可复用的排版、信息密度、状态、动效原则 |
| 小程序专属 | `apps/miniprogram/docs/` | 微信小程序 / Taro 约束、当前冻结 UI、包体和平台能力 |
| Web 专属 | `apps/web/docs/` | Web 端布局、交互、浏览器测试、桌面和移动 Web 体验 |

## 迁移规则

- 旧 `docs/uiux/` 是小程序阶段的设计过程材料，不原样迁移。
- 已实现的业务逻辑进入产品、后端或小程序基线文档。
- `.impeccable.md` 的阅读气质、克制、精确、编辑感等原则可以吸收；rpx、ScrollView、包体、微信环境等限制只能进入小程序专属文档。
- Web 端开始开发前，需要单独建立 Web UI 指南，不能用小程序 handoff 包替代。

## 给 Agent 的规则

- 修改全局设计文档时，不要写某个客户端的实现限制。
- 修改小程序时，先看 `apps/miniprogram/AGENTS.md` 和小程序 docs。
- 修改 Web 时，先看 `apps/web/AGENTS.md` 和 Web docs。
- 不确定某条 UI 规则是否跨端适用时，默认放到对应客户端文档，而不是全局文档。

更具体的设计决策规则见 `docs/design/AGENTS.md`。
