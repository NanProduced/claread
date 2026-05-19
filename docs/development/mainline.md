# 开发主线

> **状态**: `CURRENT` | **最后验证**: 2026-05-19

本文说明 Claread 当前主线方向。它不是任务流水账；已完成的阶段只保留结论，具体实现细节回到代码、测试和对应目录文档。

## 当前基线

Claread 已完成从单一小程序基线到多端产品基线的第一步：

- 微信小程序仍是稳定客户端，继续作为回归约束。
- Web baseline 已接入真实 FastAPI BFF/API 链路，不再依赖产品路径 mock/demo fixture。
- Web 已开始 Web 端能力增强：SelectionToolbar、单句内 `text_range`、跨句/跨段 `multi_text` 批注/收藏，以及按文章聚合的“摘录与批注”页已落地；Reader 自动化回归仍待补齐。
- AI 使用审计与结算底座已正式化：`ai_usage_events`、capability code、usage scope 与 billing mode 已可承接后续词典 AI、Ask Claread 和其他 Web AI 能力。
- FastAPI 后端是通用 Claread API，承载小程序、Web 和后续客户端共享的用户、记录、任务、词典、用户资产、配额和反馈能力。
- `@claread/contracts` 已先承载批注/收藏/text range 常量，后续再评估完整 OpenAPI DTO 生成。
- 本地开发基线使用 PostgreSQL、Redis、词典数据和受控测试手机号链路。

当前基线验证命令见 `docs/operations/testing.md`。

## 当前主线

当前最主要任务已经切换为：`Web Reader 2.0（Plate 底座接入） -> Ask Claread AI 助手重构`。

这不是对现有 chatbox 的局部修补，而是一次围绕 Web 解析页底座与 AI 助手协同重做的主线项目。目标是先把 Web Reader 从“semantic DOM + 多套桥接层”演进为更适合长期扩展的 Plate-based 文档 runtime，再在这一底座上把 Ask Claread 从“解析页里的 AI 控制台”重构为“像 Notion AI 一样强、但更适合 Claread 阅读场景的 article-bound assistant harness”。

当前主线分三条并行轨道推进：

| 轨道 | 方向 | 当前边界 |
| --- | --- | --- |
| Web Reader 2.0 | 以 Plate 为 Web 文档 runtime 重构解析页底座，统一对象模型、selection、jump、词典与资产桥接 | 后端 canonical render scene 与小程序消费链路保持不变；首批尽量不改 shared 资产合同 |
| Ask Claread 重构 | 在 Reader 2.0 底座上重构 AI 助手的功能边界、交互形态、上下文规划、能力编排、retrieval/RAG 路线和前后端 contract | 允许推翻 V1 设计；不受现有 chatbox 形态约束；但必须保持 article-bound、可回源、可确认写入、统一审计/结算 |
| 阅读器与多端稳定性 | 持续补 Reader 自动化、摘录回跳、移动 Web 和小程序 DevTools 人工回归 | Reader 2.0 与 Ask 重构都不应破坏当前小程序和 Web 主链路 |
| 质量与内部运营底座 | Ask 重构过程中同步明确 evals、RAG、Directus 的后续边界 | 不在主线前期提前铺开泛内部工具，只为 Ask 重构提供必要底座 |

## 近期工作顺序

1. 统一 Ask Claread 相关文档口径，明确 V1 只作为第一版设计/实现对照，V2 作为当前重构目标规范。
2. 先完成 Web Reader 2.0 路线确认：采用 Plate 作为 Web projection/document runtime，并明确 `canonical render_scene -> Plate document`、selection/jump、词典、用户资产、Ask bridge 的边界。
3. 先推进 Reader 2.0 的前置重构，优先稳定对象模型、attachment/jump contract、词典与用户资产桥接，再进入 Ask Claread 正式开发。
4. 在 Reader 2.0 边界明确后，继续完成 Ask Claread 功能边界与 harness 架构评审：intent、context planning、capability orchestration、retrieval/RAG、action/audit/eval。
5. 在 Plate 底座上推进 Ask Claread surface、前后端 contract 与 supplement layer 的重构。
6. 持续补 Reader 真实数据自动化和小程序 DevTools 人工回归，避免 Reader 2.0 与 AI 重构把摘录、回跳、词典和批注主链路带崩。
7. Directus 当前只进入边界设计和 schema 准备；等 Reader 2.0 与 Ask Claread 主线跑通后，再启动正式内部工具开发。

当前 Ask Claread 的产品目标以 `docs/product/ask-claread-v2-product-spec.md` 为准；正式实施架构与迁移顺序以 `docs/architecture/ask-claread-v2-refactor-rfc.md` 为准。`docs/product/ask-claread-v1.md` 仅保留为第一版设计/实现对照，待重构完成并验证后删除。Web Reader 2.0 与 Ask Claread 的开发期进度、决策与评审材料统一放在 `docs/tmp/ask-claread-rebuild/` 与 `apps/web/docs/tmp/`，这些文档均为临时文档，功能完成后删除或压缩进正式文档。

## 暂不拍板

以下事项仍需产品、业务和技术评估，不在本文做决定性描述：

- Ask Claread V2 首批功能边界：哪些能力属于“阅读助手必做”，哪些应明确延后。
- 多解析页 / 跨文章检索何时从结构化召回升级到 hybrid retrieval / RAG。
- Grammar X-Ray、分享页、导出和其他 AI 能力的优先级，但它们都不应抢在 Ask Claread 主线前面。
- 是否在 Ask Claread 之外单独产品化“AI 整合总结用户历史数据”能力，以及是否做跨文章/跨资产的长期学习画像。
- Directus 内部工具的具体第一批模块：Daily Reader 运营、评测样本、prompt 审核，还是 usage/feedback 观察面板。
- render snapshot / render profile 是否立即建表，以及与现有 `render_scene_json` 的迁移方式。
- contracts 生成方式、共享包边界和 CI 门槛。

## 硬约束

- 不为 Web 复制业务后端。
- 不破坏微信小程序现有主链路和 API 契约。
- 不把小程序平台限制写成全局产品限制。
- 浏览器不直接消费 FastAPI 原始 DTO；Web 通过 Next.js BFF/RSC 做 session、聚合和投影。
- 临时任务、agent prompt 和执行跟踪只放 `tmp/`，完成后删除或压缩进正式文档。

## 新会话阅读顺序

1. `AGENTS.md`
2. `README.md`
3. `docs/README.md`
4. `docs/product/current-state.md`
5. 本文档
6. 目标目录最近的 `AGENTS.md`
