# 开发主线

> **状态**: `CURRENT` | **最后验证**: 2026-05-17

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

下一阶段主线可以从“资产与多端基线收口”切换到“AI 能力接入”。但这里的 AI 主线不是先做通用 chatbox，而是沿着已落地的 Reader、词典、摘录资产和审计计费底座，先做能闭环的能力纵切。当前主线分三条并行轨道推进：

| 轨道 | 方向 | 当前边界 |
| --- | --- | --- |
| AI 能力接入 | 先做词典 AI，再做解析页内 grounded Ask Claread | 不先做泛聊天；用户资产上下文作为 Ask Claread 的 grounding 能力随同推进，能力必须走统一审计/结算与资产闭环 |
| 阅读器与多端稳定性 | 持续补 Reader 自动化、摘录回跳、移动 Web 和小程序 DevTools 人工回归 | AI 接入不应破坏当前小程序和 Web 主链路 |
| 质量与内部运营底座 | 明确 Directus/evals/RAG 的边界，并在首个 AI 能力纵切之后启动内部工具建设 | 先收口契约和真实需求，再开 Directus 正式开发 |

## 近期工作顺序

1. 接入第一个正式用户侧 AI 能力纵切：词典 AI。
2. 推进解析页内 grounded Ask Claread：以 Reader 右侧 AI 工作区为入口，围绕当前句子、选区、全文和按需获取的用户资产做对话与解释，用 tool-calls 编排能力，而不是先做通用聊天框。
3. 用户资产上下文作为 Ask Claread 的内建 grounding 能力一并建设：先服务当前阅读与摘录资产召回，不先独立产品化，也不先做 AI 整合总结用户历史数据。
4. 持续补 Reader 真实数据自动化和小程序 DevTools 人工回归，避免 AI 接入把摘录、回跳、词典和批注主链路带崩。
5. Directus 当前只进入边界设计和 schema 准备；等“词典 AI -> Reader 内 Ask Claread”这条用户侧 AI 纵切跑通后，再启动正式内部工具开发。

当前 Ask Claread 的稳定功能定义见 `docs/product/ask-claread-v1.md`；后续增强只在正式产品/架构文档里继续收敛，不再长期保留临时 tracker。

## 暂不拍板

以下事项仍需产品、业务和技术评估，不在本文做决定性描述：

- Grammar X-Ray、分享页、导出和 AI 侧栏的具体先后仍可微调，但不应早于词典 AI 和 grounded Ask Claread。
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
