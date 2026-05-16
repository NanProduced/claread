# 开发主线

> **状态**: `CURRENT` | **最后验证**: 2026-05-16

本文说明 Claread 当前主线方向。它不是任务流水账；已完成的阶段只保留结论，具体实现细节回到代码、测试和对应目录文档。

## 当前基线

Claread 已完成从单一小程序基线到多端产品基线的第一步：

- 微信小程序仍是稳定客户端，继续作为回归约束。
- Web baseline 已接入真实 FastAPI BFF/API 链路，不再依赖产品路径 mock/demo fixture。
- Web 已开始 Web 端能力增强：SelectionToolbar、单句内 `text_range`、跨句/跨段 `multi_text` 批注/收藏和学习资产聚合页已落地；Reader 自动化回归仍待补齐。
- FastAPI 后端是通用 Claread API，承载小程序、Web 和后续客户端共享的用户、记录、任务、词典、用户资产、配额和反馈能力。
- `@claread/contracts` 已先承载批注/收藏/text range 常量，后续再评估完整 OpenAPI DTO 生成。
- 本地开发基线使用 PostgreSQL、Redis、词典数据和受控测试手机号链路。

当前基线验证命令见 `docs/operations/testing.md`。

## 当前主线

下一阶段不是继续做迁移收尾，而是进入真实产品迭代准备。主线分三条并行轨道推进，具体优先级和产品取舍需要后续评估：

| 轨道 | 方向 | 当前边界 |
| --- | --- | --- |
| Web 产品体验 | 稳定 Reader 交互、资产管理和下一批 Web 增强能力 | 先基于真实接口和稳定 BFF，不回到 mock/demo |
| 后端多端化 | 收紧 contracts、ID 语义、用户资产模型、Daily Reader schema、错误态和 render profile 边界 | 兼容小程序，不复制 Web 后端 |
| 质量与运营底座 | 建立回归验证、文档治理、Directus/evals/RAG 的边界方案 | 先评估边界，再决定实现顺序 |

## 近期工作顺序

1. 补足 Reader 真实数据自动化：SelectionToolbar 创建/取消高亮、笔记、局部收藏、旧高亮兼容和资产跳转强调。
2. 确认 Web 增强能力优先级：Ask Claread、Reader 反馈、查词增强、资产管理、分享/导出等。
3. 补强长期数据约束：`text_range` / `multi_text` 数据库 CHECK、局部索引、annotations/favorites 分页和完整 contracts 生成策略。
4. 持续做多端文档治理：任何 Web 专属增强都要标明小程序是“可操作”“仅展示”还是“不适用”。

## 暂不拍板

以下事项仍需产品、业务和技术评估，不在本文做决定性描述：

- Grammar X-Ray、分享页、导出、AI 侧栏等 Web 增强能力的顺序。
- Directus 内部工具和 eval/RAG 系统的落地节奏。
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
