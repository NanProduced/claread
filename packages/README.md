# packages

`packages/` 用于放置 Claread 跨端非 UI 共享包。

当前已落地 `design-tokens/` 和第一版 `contracts/`。其他目录按需求逐步建立，不为了填目录而提前抽象。

## contracts

`packages/contracts/` 用于 API 契约、跨端枚举和生成类型。

当前已包含：

- 用户批注类型、anchor type、颜色常量。
- 收藏 target type 常量。
- `text_range` offset unit 与 hash algorithm 常量。
- Web / 小程序复用的 TypeScript 字面量类型。

未来可能继续包含：

- OpenAPI 生成的 TypeScript 类型。
- 后端响应 DTO。
- 跨端请求/响应类型。
- 错误码、状态码和枚举。

它解决的问题是：小程序、Web、Directus 或未来 App 调用同一套后端时，不需要各自手写容易漂移的接口结构和字符串常量。当前版本仍是轻量共享常量包，不是完整 SDK。

## shared-utils

`packages/shared-utils/` 用于纯业务工具函数。

适合放：

- ID 语义校验。
- reading goal / variant 映射。
- source type 判断。
- 文本 hash。
- API 错误码归一化。
- 与平台无关的数据转换。

不适合放：

- 小程序 storage。
- Web localStorage wrapper。
- Taro 请求封装。
- React hooks。
- UI 组件。
- 路由逻辑。

## design-tokens

`packages/design-tokens/` 用于 Claread 跨端设计 token。

设计 token 是语义值，不是 UI 组件。

例如：

- 阅读背景色。
- 正文字色。
- 批注语义色。
- 字体层级。
- 间距。
- 圆角。

各端可以把 token 转换为自己的实现：

- 小程序：SCSS / rpx 变量。
- Web：CSS variables。
- 未来 App：native theme。

## 不共享 UI

Claread 三端不共享 UI。

小程序、Web 和未来 App 应分别使用最适合自身平台的技术栈和交互方式。`packages/` 只承载契约、纯业务逻辑和设计语义，不承载跨端页面组件。
