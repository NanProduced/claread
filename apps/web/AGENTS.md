# Web Agent 指令

`apps/web/` 是 Claread Web 客户端。迁移第一阶段可以为空，后续开发时按本文件扩展。

## Web 定位

- Web 端应追求比小程序更完整的阅读、解析、批注和管理体验。
- Web 端共享 `services/api/`，不单独复制一套后端。
- Web 可以拥有更丰富的 render profile，但 canonical result 仍由后端统一产生。

## 开发原则

- 不被小程序 UI 限制反向约束。
- 桌面和移动 Web 都要考虑，但第一阶段可以先定义 MVP 范围。
- 设计规则放在 `docs/design/` 和 `apps/web/docs/`，不要复用旧小程序 handoff 文档作为 Web 真相源。
- 新增跨端类型时优先放入 `packages/contracts/`。
- Web Reader 不存在 demo/mock 记录页。验证 Reader 时必须使用真实记录，或先走真实创建流程拿到可用 `recordId`，不要假设 `/app/reader/demo-record` 之类的占位路径成立。

## 品牌上下文与资产

- 做 Web 端 UI、视觉、品牌页、分享页或导出页前，必须先读 `apps/web/DESIGN.md`、`apps/web/docs/design/README.md` 和 `packages/design-tokens/assets/brand/README.md`。
- Claread 品牌源资产放在 `packages/design-tokens/assets/brand/`，这是 logo、图标和品牌探索图的唯一源目录。
- Web 运行时只能使用复制或导出到 `apps/web/public/brand/` 的资产，不要从运行时代码直接 import `packages/design-tokens/assets/brand/`。
- 当前 Web 品牌组件集中在 `apps/web/src/components/brand/BrandMarks.tsx`。需要展示 Logo、横版标识、水印或小印章时，优先复用 `BrandLockup`、`ApertureWatermark`、`ClareadStamp`，不要临时用文字或手写 SVG 代替品牌资产。
- 新页面如果需要品牌识别，先检查 `packages/design-tokens/assets/brand/logos/`、`icons/`、`design/` 中的现有素材，再决定是否复制到 `apps/web/public/brand/`。不要凭印象重画 Logo、重配色或发明新的品牌符号。
- 公开产品页、分享页、导出页和登录页必须让 Claread 品牌成为首屏可见信号；功能页可以更克制，但仍应保留 Claread 的阅读镜头、纸面和批注语言。

## 验证

Web 开发开始后，应补齐：

- typecheck
- build
- browser smoke test
- 关键阅读/解析页面截图验证

浏览器端显示效果需要用真实页面验证，不能只依赖静态代码审查。
