# 开发主线

> **状态**: `CURRENT` | **最后验证**: 2026-06-10

本文说明 Claread 当前主线方向。它不是任务流水账；已完成的阶段只保留结论，具体实现细节回到代码、测试和对应目录文档。

## 当前基线

Claread 已完成从单一小程序基线到多端产品基线的推进：

- 微信小程序仍是稳定客户端，继续作为回归约束。
- Web 已形成可用产品基线，通过 Next.js BFF 接入真实 FastAPI 链路，不再依赖产品路径 mock/demo fixture。公共区、认证区和私有区路由已完整覆盖，command palette 已实现。
- Web Reader 标注体系已收口：SelectionToolbar、单句内 `text_range`、跨句/跨段 `multi_text` 高亮/笔记和 Ask Claread 显式引用已接入；高亮冲突已统一走后端 resolver 合并，SelectionToolbar 已收口为"一级高亮 + inline 颜色条"的单层工具条。
- Reader 词典 AI 已收口为 article-scoped 的前端缓存能力，不改变后端词典 truth layer。
- AI 使用审计与结算底座已正式化：`ai_usage_events`、capability code、usage scope 与 billing mode 已可承接后续词典 AI、Ask Claread 和其他 Web AI 能力。
- FastAPI 后端是通用 Claread API，承载小程序、Web 和后续客户端共享的用户、记录、任务、词典、用户资产、配额和反馈能力。
- workflow 解析主链路可跑通：learning / academic 双模式、grammar RAG 检索、prompt 策略和 canonical result 生成已形成完整链路。
- Claread Console 已进入可用控制面阶段：Eval Center（node-lab / workflow-lab / run-history）、Render Scene Inspector、Parse Run Observability 和 Example Lab 已有可用能力。
- `@claread/contracts` 已先承载批注/收藏/text range 常量，后续再评估完整 OpenAPI DTO 生成。
- 本地开发基线使用 PostgreSQL、Redis、词典数据和受控测试手机号链路。

当前基线验证命令见 `docs/operations/testing.md`。

## 当前主线

### 主线：workflow 输出质量提升 / 评测治理

workflow 解析主链路已可跑通，当前重心转向输出质量提升和评测治理。Eval Center 已落地 node-lab / workflow-lab / run-history 三个 mode，后续重点是通过评测驱动 workflow 输出质量的持续改进，而不是继续扩展控制面功能。

近期重点：
- 利用 Eval Center 的 Node Lab 和 Workflow Lab 对 workflow 输出做系统性评测
- 基于评测结果驱动 prompt 策略和解析链路的质量改进
- grammar RAG 检索质量和 few-shot 样本质量的持续治理

### 副线：Ask Claread 架构与表现优化

Ask Claread 当前实现仍是可运行基线，但已确认下一轮重构方向不再继续加固默认 `planner -> 主回答 agent -> 可选 replan` 的三段式编排。现阶段先完成 LLM 统一配置与 provider 兼容问题收口；随后再进入 Ask Claread 的再次重构，目标方向是默认 single agent loop，并保留 article-bound、可回源、可确认写入、统一审计/结算边界。

近期重点：
- 收口 DashScope / DeepSeek / GLM 等 provider 在 Ask 路径上的配置、streaming 与 reasoning 兼容问题
- 修正 correctness 问题，确保 regenerate、supplement lifecycle、known reference resolution 和写动作边界符合当前规范
- 为下一轮 Ask Claread 从 planner-first 迁移到默认 agent loop 的重构准备边界与问题清单

### 副线：Web 次要功能补齐与页面设计收口

Web 主产品链路已形成可用基线，后续重点是次要功能补齐、页面设计收口和体验打磨。

近期重点：
- Web Reader UI/UX 继续打磨：句侧 note marker、selection draft popover 和浮出式 note panel 的交互与视觉层级
- 公共区页面设计收口
- 移动 Web 适配

### 副线：Claread Console 控制面治理化

Claread Console 已进入可用控制面阶段，后续重点转向控制面治理化——按治理价值排序推进，而不是泛化铺开后台功能。

近期重点：
- 把 Eval Center、Example Lab、解析观察台和 Inspector 的正式边界压回主线文档
- 按治理优先级推进：解析治理、RAG promotion、运营工作台

### 维护线：小程序与多端稳定性维护

小程序是稳定客户端，保持回归约束。Reader 2.0 与 Ask 重构都不应破坏当前小程序主链路。

近期重点：
- 小程序 Reader 结果页对 `reader_notes` 的本地优先回读
- 多端共享后端和数据库的稳定性维护
- Reader 自动化回归补齐

## 暂不拍板

以下事项仍需产品、业务和技术评估，不在本文做决定性描述：

- Ask Claread 在显式引用模型上是否还需要更强的跨文章扩展，以及 resolver / product contract 应如何继续演进。
- Ask Claread semantic resolver / retrieval rerank 是否启用真实 LLM 或 embedding rerank；启用前必须先评估 timeout、candidate limit、成本、trace/eval 样本和 fallback。
- Ask Claread 下一轮默认 agent loop 中，planner / resolver 是作为按需 tool、独立 sidecar route，还是其他受控能力接入。
- Ask Claread planner 的 `answer_policy` 是否仍保留，以及若保留应作为硬约束、软偏好还是可覆盖策略。
- Ask Claread 是否需要受限 multi-step reader loop；当前已不再假设长期维持 planner-first，但是否开放多步 loop、最大 step 数和 retry 策略仍需单独拍板。
- Ask Claread auto replan 是否保留为默认兜底，还是降级为显式 retry / 低频 fallback。
- 多解析页 / 跨文章检索何时从当前受控扩展升级到 hybrid retrieval / RAG。
- Grammar X-Ray、分享页、导出和其他 AI 能力的优先级。
- 是否在 Ask Claread 之外单独产品化"AI 整合总结用户历史数据"能力，以及是否做跨文章/跨资产的长期学习画像。
- Claread Console 下一阶段优先落哪条工作流：解析治理、RAG promotion、运营工作台，还是 feedback / usage 观察面板。
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
