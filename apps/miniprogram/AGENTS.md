# Miniprogram Agent 指令

`apps/miniprogram/` 是 Claread 微信小程序客户端。它是当前迁移基线，但不是 Claread 多端产品的功能上限。

## 平台身份

- 保留微信小程序 / Taro 语境，不要把它改写成通用 Web 客户端。
- 微信登录、分享、分包、storage、rpx、包体积和 DevTools 行为都属于小程序专属约束。
- 小程序无法承载的 UI/UX 能力不代表后端或 Web 不能支持。

## 迁移基线

- 迁移前主功能已通过微信开发者工具人工验证。
- clean import 后先稳定当前功能，再逐步适配新后端目录和多端契约。
- 不迁移 `dist/`、`node_modules/`、缓存、private config、scratch、dev fixtures。
- 本地 API 地址不得硬编码个人局域网 IP，应走环境变量或本地默认。

## 开发规则

- 变更 API 请求前先确认 `services/api/docs/api-contracts.md`。
- 小程序 local-first、同步队列、record identity map、storage key 不能随意改名。
- UI 改动先以冻结基线为准，Web 端增强不要直接反向套到小程序。
- 包体积 warning 可以作为 P2 优化，但不能在迁移期打断 clean import。

## 验证

```powershell
rtk err pnpm run build:weapp
rtk err pnpm exec tsc -p tsconfig.json --noEmit
```

最终仍需在微信开发者工具中验证登录、解析、历史、词典、生词本、每日阅读等主链路。
