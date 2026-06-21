# Miniprogram Agent 指令

`apps/miniprogram/` 是 Claread 微信小程序客户端。它是当前稳定基线，但不是 Claread 多端产品的功能上限。

## 平台身份

- 保留微信小程序 / Taro 语境，不要把它改写成通用 Web 客户端。
- 微信登录、分享、分包、storage、rpx、包体积和 DevTools 行为都属于小程序专属约束。
- 小程序无法承载的 UI/UX 能力不代表后端或 Web 不能支持。

## Serena

- 在 `apps/miniprogram/` 内做代码任务时，优先使用 Serena 做符号级阅读、声明跳转、引用分析和重构，不要先整文件通读。
- 需要 Serena 做符号级检索、引用分析或重构时，只激活当前子项目 `claread-miniprogram`。
- 已知目标文件时，优先 `get_symbols_overview` -> `find_declaration` / `find_referencing_symbols` / `find_implementations`；需要修改整个符号时，优先 `rename_symbol`、`replace_symbol_body`、`safe_delete_symbol`。
- 目标落点不明确时，先用 Serena `search_for_pattern` 在本项目内粗搜；只有跨子项目排查、非代码文件、或 Serena 当前不支持的文件类型，才优先用 RTK / shell / git diff。
- 涉及小程序与 sibling packages 的 TypeScript 跨包引用时，优先考虑 Serena `additional_workspace_folders` 配置，而不是把仓库根目录整体激活成单一 Serena 项目。
- 不要从本目录向上激活仓库根目录为 Serena 项目；跨子项目检索优先用 RTK / shell / git diff。
- 未经用户明确要求，不写 Serena memory；长期事实应更新正式文档或本 `AGENTS.md`。如果 memory 与代码、测试或正式文档冲突，以后者为准，并删除或覆盖过期 memory。

## 当前基线

- 主功能应以微信开发者工具人工验证为准。
- 先稳定当前功能，再逐步适配多端契约。
- 不迁移 `dist/`、`node_modules/`、缓存、private config、scratch、dev fixtures。
- 本地 API 地址不得硬编码个人局域网 IP，应走环境变量或本地默认。
- 本地 `http://localhost:8000` 调试通常需要关闭微信开发者工具的本地域名校验。

## 开发规则

- 变更 API 请求前先确认 `services/api/docs/api-contracts.md`。
- 小程序 local-first、同步队列、record identity map、storage key 不能随意改名。
- UI 改动先以冻结基线为准，Web 端增强不要直接反向套到小程序。
- 包体积 warning 可以作为 P2 优化，但不能打断当前基线验证。

## 验证

```powershell
rtk err pnpm run build:weapp
rtk err pnpm exec tsc -p tsconfig.json --noEmit
```

最终仍需在微信开发者工具中验证登录、解析、历史、词典、生词本、每日阅读等主链路。
