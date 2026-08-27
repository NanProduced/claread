# Web Agent 指令

`apps/web/` 是 Claread Web 产品客户端，共享 `services/api/` 与跨端数据契约。

## Web 定位

- Web 端应追求比小程序更完整的阅读、解析、批注和管理体验。
- Web 端共享 `services/api/`，不单独复制一套后端。
- Web 可以拥有更丰富的 render profile，但 canonical result 仍由后端统一产生。

## Serena

- 在 `apps/web/` 内做代码任务时，优先使用 Serena 做符号级阅读、声明跳转、引用分析和重构，不要先整文件通读。
- 需要 Serena 做符号级检索、引用分析或重构时，只激活当前子项目 `claread-web`。
- 已知目标文件时，优先 `get_symbols_overview` -> `find_declaration` / `find_referencing_symbols` / `find_implementations`；需要修改整个符号时，优先 `rename_symbol`、`replace_symbol_body`、`safe_delete_symbol`。
- 目标落点不明确时，先用 Serena `search_for_pattern` 在本项目内粗搜；只有跨子项目排查、非代码文件、或 Serena 当前不支持的文件类型，才优先用 RTK / shell / git diff。
- 涉及 `apps/web` 与 sibling packages 的 TypeScript 跨包引用时，优先考虑 Serena `additional_workspace_folders` 配置，而不是把仓库根目录整体激活成单一 Serena 项目。
- 不要从本目录向上激活仓库根目录为 Serena 项目；跨子项目检索优先用 RTK / shell / git diff。
- 未经用户明确要求，不写 Serena memory；长期事实应更新正式文档或本 `AGENTS.md`。如果 memory 与代码、测试或正式文档冲突，以后者为准，并删除或覆盖过期 memory。

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

## 产品页设计协作工具选择

开发或评审 Claread Web 产品页时，按阶段选择工具，不要同时混用所有设计插件和 skill：

- `@product-design`：用于产品页方向图、原型图、UI 方案、从视觉稿到可交互原型。适合在需要比较 2-3 个视觉方向、首屏原型、Figma/截图式方案或可交互 prototype 时使用。当前产品页早期方向探索应优先由它主导。
- `@creative-production`：用于 moodboard、品牌视觉路线、hero 视觉资产、营销图、广告/海报/场景探索。只有当产品页需要扩展光圈视觉、纸面卡片、品牌 campaign language 或生成 hero 资产时再引入；不要用它直接替代完整 Web UI 信息架构设计。
- `$impeccable`：用于生产级前端 UI 设计、实现、审查和打磨。进入 `apps/web` 真实代码实现、responsive polish、visual critique、accessibility / performance audit 时使用。它应读取 `PRODUCT.md`、`DESIGN.md`、现有 token 和组件后再动手。
- `$ui-ux-pro-max`：用于 UI/UX 规则校验和专业规范检查。方案定稿或实现阶段，用它检查对比度、响应式、动效、可访问性、移动端断点、按钮尺寸、文字层级和交互状态；不要让它替代 Claread 的品牌方向判断。

推荐流程：

1. 方向探索：`@product-design` 主导，参考 `docs/product/product-page-direction.md` 和 `packages/design-tokens/assets/brand/`。
2. 品牌视觉资产探索：需要时引入 `@creative-production`。
3. 真实代码落地：使用 `$impeccable` 做 production-grade 实现和打磨。
4. 交付前检查：使用 `$ui-ux-pro-max` 做可用性、响应式和无障碍校验。

## Daily Reader 页

- `/daily`、`/daily/[articleId]` 消费 v2 教学 IA，不渲染 v1 逐段问答。ViewModel 顺序：阅读任务卡 → 正文流（译文按需，`details`）→ 结构提纲 → 语言精讲 → 证据自测 → 迁移任务 → 收束。落点：`src/types/view/DailyReaderVm.ts`、`src/app/(public)/daily/[articleId]/DailyArticleBody.tsx`。
- 载荷来自 API 三字段 `lesson_blueprint` / `learning_package` / `reading_units`，经 `src/adapters/daily-reader.adapter.ts` 投影。公开页只展示已发布文章。
- 架构见 `docs/architecture/daily-reader.md`。

## 验证

Web 开发开始后，应补齐：

- typecheck
- build
- browser smoke test
- 关键阅读/解析页面截图验证

浏览器端显示效果需要用真实页面验证，不能只依赖静态代码审查。
