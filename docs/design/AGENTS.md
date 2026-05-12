# Design Agent 指令

本目录只放跨端设计原则，不放某个客户端的实现限制。

## 决策规则

- 先判断设计规则是跨端事实还是客户端实现细节。
- 跨端事实写入 `docs/design/` 或 `docs/product/`。
- 微信小程序实现限制写入 `apps/miniprogram/docs/`。
- Web 实现规则写入 `apps/web/docs/`。
- 全局规则和客户端规则冲突时，以全局业务契约为准，客户端 UI 表现按自身能力分别定义。
- 小程序端降级 UI 不作为 Web 端设计上限。
- 设计 token、颜色、字体、间距等跨端复用内容，后续优先进入 `packages/design-tokens/`。

## 禁止事项

- 不把 rpx、微信分包、ScrollView、微信开发者工具等平台限制写入全局设计原则。
- 不直接复制旧仓库 `docs/uiux/` handoff 包。
- 不用旧小程序截图或草图作为 Web 端最终设计依据。
