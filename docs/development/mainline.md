# 开发主线

> **状态**: `CURRENT` | **最后验证**: 2026-06-17

本文说明 Claread 当前主线方向。它不是任务流水账；已完成的阶段只保留结论，具体实现细节回到代码、测试和对应目录文档。

## 当前基线

Claread 已完成从单一小程序基线到多端产品基线的推进：

- 微信小程序仍是稳定客户端，继续作为回归约束。
- Web 已形成可用产品基线，通过 Next.js BFF 接入真实 FastAPI 链路，不再依赖产品路径 mock/demo fixture。公共区、认证区和私有区路由已完整覆盖，command palette 已实现。
- Web Reader 标注体系已收口：SelectionToolbar、单句内 `text_range`、跨句/跨段 `multi_text` 高亮/笔记和 Ask Claread 显式引用已接入；高亮冲突已统一走后端 resolver 合并，SelectionToolbar 已收口为"一级高亮 + inline 颜色条"的单层工具条。
- Reader 词典 AI 已收口为 article-scoped 的前端缓存能力，不改变后端词典 truth layer。
- AI 使用审计与结算底座已正式化：`ai_usage_events`、capability code、usage scope 与 billing mode 已可承接后续词典 AI、Ask Claread 和其他 Web AI 能力。
- FastAPI 后端是通用 Claread API，承载小程序、Web 和后续客户端共享的用户、记录、任务、词典、用户资产、配额和反馈能力。
- workflow 解析主链路可跑通：learning / academic 双模式、grammar RAG 检索、prompt 策略和 canonical result 生成已形成完整链路；但当前 AI Workflow 形态已被判定不足以承接后续 Reader 产品目标，下一阶段主线转向 bounded agentic Reader orchestration。
- Claread Console 已进入可用控制面阶段：Eval Center（node-lab / workflow-lab / run-history）、Render Scene Inspector、Parse Run Observability 和 Example Lab 已有可用能力。
- `@claread/contracts` 已先承载批注/收藏/text range 常量，后续再评估完整 OpenAPI DTO 生成。
- 本地开发基线使用 PostgreSQL、Redis、词典数据和受控测试手机号链路。

当前基线验证命令见 `docs/operations/testing.md`。

## 当前主线

### 主线：Reader agentic orchestration 调研与方案设计

当前准备把用户提交内容的 `learning workflow` 与 `academic workflow` 从固定 AI Workflow 重构为 bounded agentic orchestration。重构目标不是把 Reader 页变成常驻 LLM 线程，而是以可审计、可中断、可确认、可回退的 event-triggered run / job 形态生成稳定阅读基座、稳定阅读单元和增量增强层。

近期重点：
- 先完成 R0-R11 调研：产品参考、成本/速度/负载、Length Class、Parsed Decision、流式输出、Ask/RAG 边界、计费审计、运行时框架和迁移 rollout
- 建立当前 workflow 的 token、latency、retry、服务器负载基线，再设计新 orchestration 成本模型
- 验证 LangGraph、PydanticAI、自建 DB 状态机、worker、SSE / polling 和 observability 的职责边界
- 明确 Stable Reading Base、Reading Units、Navigation Skeleton、Enhancement Layer 和用户确认流程
- 用 Eval Center / Directus / trace 体系支撑 planner decision、parsed decision、coverage 和质量评测

范围边界：

- In scope：用户提交内容的 `learning workflow` 与 `academic workflow`
- Out of scope：`daily_reader_workflow` 及 Daily Reader 的文章发现、抽取、评分、定时生产和公开页面生成模式
- 兼容对象：旧 `render_scene_json` 记录、Daily Reader 公共页面、Reader API、Library、Ask Claread 和 Eval Center

设计原则：

- LLM / planner 承担更多策略判断，代码负责红线边界、结构契约、安全校验、审计、计费、限流和回退。
- 译文是最基础增强，但词汇、语法、长难句、outline 等是否生成以及生成到什么程度，应由 planner 基于 goal / variant / reading unit 价值评估决定，避免机械阈值。
- 高影响输入适配必须先给用户 Candidate Reading Base 预览、修改和确认；确认后才生成稳定阅读基座和稳定阅读单元，一经确认不可被后续增强改写。

### 副线：Ask Claread baseline 维护与接入准备

Ask Claread 已完成从三段式 planner 编排到 agent-loop-only baseline 的重构。live service 不再调用 planner route resolver 或 semantic planner LLM；`planner_first`、`planning_snapshot_json` 等名称只作为历史 trace / DB 兼容面保留。当前稳定边界是 article-bound、可回源、可确认写入、统一审计/结算。

近期重点：
- 作为稳定 baseline 合回主分支，后续不再扩大 Ask 自身架构面
- 保持 article-bound、可回源、可确认写入、统一审计/结算边界
- 后续 Reader orchestration 需要把 Ask 作为 consumer / sidecar integration 重新接入 Stable Reading Base 和 Reading Units
- 按需补充真实 LLM smoke、小型 eval dataset 和 correctness 修正，但不恢复 planner-first live path

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

- Reader orchestration 是否用一个共享 runtime 承载 learning / academic，再由 policy 区分，还是保留两个 orchestrator。
- LangGraph、PydanticAI、自建 DB 状态机和 worker / SSE broker 的最终职责划分，以及是否升级或替换底层框架。
- Stable Reading Base、Reading Units、Navigation Skeleton、Semantic Outline、Enhancement Layer、Parsed Decision 的最终 schema 和 API 版本化方式。
- Article Ready、Initial Enhancement Ready、100% Parse Coverage、长文渐进 coverage 的计费、速度、默认推进和用户授权策略。
- Ask Claread 接入新 Stable Reading Base 后，是否允许低频 sidecar action、如何授权、如何保存为用户资产。
- 多解析页 / 跨文章检索何时从当前受控扩展升级到 hybrid retrieval / RAG；本轮不做全局用户资产整理或跨记录语义 RAG 产品化。
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
