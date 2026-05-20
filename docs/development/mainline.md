# 开发主线

> **状态**: `CURRENT` | **最后验证**: 2026-05-20

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

当前主线已经从 `Web Reader 2.0（Plate 底座接入）` 进入到其后续阶段：`Ask Claread 冻结收尾与后续产品调整评估`。

Web Reader 2.0（Plate 底座接入）这一前置任务已经完成。当前工作不再是决定 Reader 是否切到 Plate，而是在该底座上把 Ask Claread 冻结到一个可用、正确、便于后续需求调整评估的状态。

当前主线分三条并行轨道推进：

| 轨道 | 方向 | 当前边界 |
| --- | --- | --- |
| Web Reader 2.0 | Plate Reader 底座、对象模型、selection/jump、词典/资产/Ask bridge 已完成并作为当前基础设施冻结 | 后端 canonical render scene 与小程序消费链路保持不变；后续只做增量优化，不再回到 Reader 2.0 方案评审 |
| Ask Claread | 在 Reader 2.0 底座上完成 AI 助手的冻结收尾，校正 correctness、文档口径和评估边界 | 当前目标是冻结可用正确状态，而不是继续无界扩功能；必须保持 article-bound、可回源、可确认写入、统一审计/结算 |
| 阅读器与多端稳定性 | 持续补 Reader 自动化、摘录回跳、移动 Web 和小程序 DevTools 人工回归 | Reader 2.0 与 Ask 重构都不应破坏当前小程序和 Web 主链路 |
| 质量与内部运营底座 | Ask 冻结后同步明确 evals、RAG、Directus 与后续产品调整的边界 | 不提前铺开泛内部工具，只为 Ask 评估和后续需求调整提供必要底座 |

## 近期工作顺序

1. 修正 Ask Claread 冻结前的 correctness 问题，确保 regenerate、supplement lifecycle、known reference resolution 和写动作边界符合当前规范。
2. 统一 Ask Claread 正式文档口径，以 `docs/product/ask-claread.md` 和 `docs/architecture/ask-claread.md` 作为当前真相源。
3. 保持 Reader 2.0 底座稳定，继续补真实数据自动化和小程序 DevTools 人工回归，避免 Ask 收尾影响阅读主链路。
4. 在冻结完成后，评估后续产品调整，尤其是“用户学习资产”相关范围是否保留、缩减或移除。
5. Directus 当前只进入边界设计和 schema 准备；等 Ask Claread 冻结评估完成后，再决定是否启动正式内部工具开发。

## 暂不拍板

以下事项仍需产品、业务和技术评估，不在本文做决定性描述：

- Ask Claread 冻结后是否继续保留“用户学习资产”相关范围，以及缩减后 planner / resolver / product contract 应如何收口。
- 多解析页 / 跨文章检索何时从当前受控扩展升级到 hybrid retrieval / RAG。
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
