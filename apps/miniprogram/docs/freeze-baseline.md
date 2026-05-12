# 小程序冻结基线

本文记录 clean import 时微信小程序客户端的冻结范围。

这里的冻结不是产品终局，只是迁移节点的稳定基线。迁移后，小程序仍会继续迭代。

## 当前范围

| 模块 | 状态 |
|------|------|
| 输入与分析提交 | 可用 |
| 解析阅读页 | 可用，小程序端存在平台降级 |
| 历史记录 | 可用 |
| 生词本 | 可用 |
| 生词复习 | 可用 |
| 收藏 | 可用 |
| 反馈 | 可用 |
| 每日精读 | 可用 |
| 微信登录 / 配额 | 可用 |

## 小程序专属边界

这些能力是小程序客户端实现，不应污染全局后端架构：

- `Taro.login()` 微信登录。
- 小程序分享。
- Taro storage。
- 小程序分包和页面路由。
- 小程序音频 API。
- 微信头像 / 昵称能力。
- 小程序弹窗、toast、导航栏安全区。

## 必须保持的契约

- `client_record_id`、`cloud_record_id`、`task_id` 语义。
- 分析任务状态机。
- records / vocabulary / favorites / feedback / user-annotations API 字段。
- `/dict` 和 `/dict/entry` 响应结构。
- Daily Reader `body/highlights/paragraph_notes/takeaways` 当前兼容结构。
- 本地 storage key 和 sync queue 语义。

## clean import 资产

迁移：

- `client/src/`
- `client/config/`
- `client/babel.config.js`
- `client/project.config.json`
- `client/package.json`
- `client/tsconfig.json`

不迁移：

- `dist/`
- `node_modules/`
- `.pnpm-store/`
- `.swc/`
- `scratch/`
- `src/dev-fixtures/`
- `project.private.config.json`
- 空占位或历史多平台目录

## 已知非阻塞项

- 微信分享能力可后续完善。
- 小程序包体积和分包优化后续处理。
- 阅读页高保真交互主要放到 Web 阶段。
- 小程序当前 render scene 不是 Web 端体验上限。
