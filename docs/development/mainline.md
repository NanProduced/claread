# 开发主线

> **状态**: `CURRENT` | **最后验证**: 2026-08-19

本文说明 Claread 当前主线方向。它不是任务流水账；已完成的阶段只保留结论，具体实现细节回到代码、测试和对应目录文档。

## 当前基线

Claread 已完成从单一小程序基线到多端产品基线的推进：

- 微信小程序仍是稳定客户端，继续作为回归约束。
- Web 已形成可用产品基线，通过 Next.js BFF 接入真实 FastAPI 链路，不再依赖产品路径 mock/demo fixture。公共区、认证区和私有区路由已完整覆盖，command palette 已实现。
- Web Reader 标注体系已收口：SelectionToolbar、单句内 `text_range`、跨句/跨段 `multi_text` 高亮/笔记和 Ask Claread 显式引用已接入；高亮冲突已统一走后端 resolver 合并，SelectionToolbar 已收口为"一级高亮 + inline 颜色条"的单层工具条。
- Reader 词典 AI 已收口为 article-scoped 的前端缓存能力，不改变后端词典 truth layer。
- AI 使用审计与结算底座已正式化：`ai_usage_events`、capability code、usage scope 与 billing mode 已可承接后续词典 AI、Ask Claread 和其他 Web AI 能力。
- Reader 可恢复解析已完成并入基线：placeholder-only 内容防线、`failed_terminal` 不可变 successor 恢复（manual + bounded automatic）、不重复计费、Web 友好提示与手动重试、worker 周期内自动恢复调度与脱敏结构化告警兼容面均已完成；验证入口见 `docs/operations/testing.md` §Reader 恢复离线验收。
- FastAPI 后端是通用 Claread API，承载小程序、Web 和后续客户端共享的用户、记录、任务、词典、用户资产、配额和反馈能力。
- Reader 当前生产链以 Reading Record、Stable Document、Reading Units、Anchor Segments、Enhancement Layers、`reader_events` 为事实源，Web 通过 `/app/read` 与 `/app/reader/[recordId]` + BFF `/api/web/reader/records/*` 接入。旧 `learning_workflow.py`、Analysis service 写入路径（`services/api/app/services/analysis/` 整目录 `.py` 源文件）、`analysis_results.render_scene_json` 事实源、旧 Reader 产品页与 BFF route、旧 Directus Eval Center / Workflow Lab / Node Lab / Render Scene Inspector / Parse Run Observability 已物理删除。旧 `analysis_*` 表的精确状态（legacy 孤儿表 / legacy 仍被只读引用表 / 新链在用表 `analysis_windows` 与 `layer_analysis_plans`）见 `docs/architecture/workflow-history.md`；残留本地库的旧表 DROP 尚未完成。计费写账闭环、统一监测和 Console / Eval 治理化控制面尚未实现。
- Claread Console 当前只保留 enum-label-display / enum-label-interface 等通用 metadata 展示 module；旧 Eval Center、Workflow Lab、Node Lab、Render Scene Inspector、Parse Run Observability module 已物理删除，Console / Eval 治理化控制面尚未实现。
- `@claread/contracts` 已先承载批注/收藏/text range 常量，后续再评估完整 OpenAPI DTO 生成。
- 本地开发基线使用 PostgreSQL、Redis、词典数据和受控测试手机号链路。

当前基线验证命令见 `docs/operations/testing.md`。

## 当前主线

### 主线：Reader orchestration 稳定推进与治理化控制面规划

用户提交内容的 `learning` 主链当前使用 bounded agentic orchestration，以 Reading Record、Stable Document、Reading Units、Anchor Segments、Enhancement Layers、`reader_events` 为事实源，Web 通过 `/app/read` 与 `/app/reader/[recordId]` + BFF `/api/web/reader/records/*` 接入。旧 Learning Workflow、Analysis Ask、Ask legacy lane、旧 Web/Mini 页面和旧 Directus Eval Center / Workflow Lab / Node Lab 已物理删除。

当前架构权威上下文在 `docs/architecture/reader-orchestration.md`。计费写账闭环、统一监测与 Console / Eval 治理化控制面尚未实现。

近期重点：
- 旧 Eval 控制面表与 `analysis_*` 残留本地库清理
- Console / Eval 治理化控制面建设（规划中）
- 统一监测：消费现有结构化恢复告警（`reader_automatic_recovery_alert`），建设 Console / Sentry / 外部投递；计费适配、usage/ledger 与新 Reader run/job/layer attribution 闭环
- 可恢复解析的真实 provider、真实浏览器与生产部署验收保留为上线门禁（离线验收已完成，不替代真实验收）
- 测试治理与代码架构优化

范围边界：

- 当前包含：用户提交内容的 `learning workflow` 已使用 bounded agentic orchestration 生产链
- 当前产品范围不包括：`academic workflow`、`daily_reader_workflow` 及 Daily Reader 的文章发现、抽取、评分、定时生产和公开页面生成模式；小程序 Reader orchestration 当前未实现
- 数据策略：项目未上线，不做旧开发记录迁移；本地数据可重置，但保留 `dict_entries`、`dict_lookup_targets`、`dict_redirects`、`reader_ask_*` 共享表、`eval_example_lab_entries`、Reader user assets、usage/ledger
- 兼容对象：Daily Reader 公共页面、Reader API、Library、Ask Claread；旧开发数据不作为迁移约束

设计原则（继续生效）：

- LLM / planner 承担更多策略判断，代码负责红线边界、结构契约、安全校验、审计、计费、限流和回退。
- 译文是最基础增强，但词汇、语法、长难句、outline 等是否生成以及生成到什么程度，应由 planner 基于 goal / variant / reading unit 价值评估决定，避免机械阈值。
- 高影响输入适配必须先给用户 Candidate Reading Base 预览、修改和确认；确认后才生成稳定阅读基座和稳定阅读单元，一经确认不可被后续增强改写。

### 副线：Ask Claread 维护

Ask Claread 当前以 `reader_record_ask` agentic v2 为唯一 Ask 执行链（article-bound、可回源、可确认写入、统一审计/结算；`planner_first` 仅作为历史 trace value 保留）；旧 Analysis Ask 和 Ask legacy lane 已物理删除。

近期重点：
- 保持 article-bound、可回源、可确认写入、统一审计/结算边界
- Ask Claread 作为 consumer / sidecar integration 接入 Stable Reading Base 和 Reading Units，不是 Reader orchestration 控制中心
- 按需补充真实 LLM smoke、小型 eval dataset 和 correctness 修正，但不恢复 planner-first live path

### 副线：Web 次要功能补齐与页面设计收口

Web 主产品链路已形成可用基线，后续重点是次要功能补齐、页面设计收口和体验打磨。

近期重点：
- Web Reader UI/UX 继续打磨：句侧 note marker、selection draft popover 和浮出式 note panel 的交互与视觉层级
- 公共区页面设计收口
- 移动 Web 适配

### 副线：Claread Console 控制面治理化建设

Claread Console 当前只保留通用 metadata 展示 module；旧 Eval Center、Workflow Lab、Node Lab、Render Scene Inspector、Parse Run Observability module 已物理删除。治理化控制面尚未实现，后续应按治理价值排序推进，而不是泛化铺开后台功能。

近期重点：
- Console / Eval 治理化控制面建设（规划中），明确与已删除旧 Eval Center 的边界
- 按治理优先级推进：解析治理、RAG promotion、运营工作台

### 维护线：小程序与多端稳定性维护

小程序是稳定客户端，保持回归约束。Reader 与 Ask 的后续变更都不应破坏当前小程序主链路。

近期重点：
- 小程序 Reader 结果页对 `reader_notes` 的本地优先回读
- 多端共享后端和数据库的稳定性维护
- Reader 自动化回归补齐

## 暂不拍板

以下事项仍需产品、业务和技术评估，不在本文做决定性描述：

- Academic workflow 后续是否复用 learning orchestration runtime，还是单独设计 academic orchestrator。
- LangGraph、PydanticAI、自建 DB 状态机和 worker / SSE broker 的最终职责划分，以及是否升级或替换底层框架。
- Stable Reading Base、Reading Units、Navigation Skeleton、Semantic Outline、Enhancement Layer、Parsed Decision 的最终 schema 和 API 版本化方式。
- Article Ready、Initial Enhancement Ready、100% Parse Coverage、长文渐进 coverage 的计费、速度、默认推进和用户授权策略。
- Ask Claread 接入新 Stable Reading Base 后，是否允许低频 sidecar action、如何授权、如何保存为用户资产。
- 多解析页 / 跨文章检索何时从当前受控扩展升级到 hybrid retrieval / RAG；全局用户资产整理或跨记录语义 RAG 当前不产品化。
- Grammar X-Ray、分享页、导出和其他 AI 能力的优先级。
- 是否在 Ask Claread 之外单独产品化"AI 整合总结用户历史数据"能力，以及是否做跨文章/跨资产的长期学习画像。
- Claread Console 下一优先项落哪条工作流：解析治理、RAG promotion、运营工作台，还是 feedback / usage 观察面板。
- render snapshot / render profile 是否立即建表（旧 `render_scene_json` 已物理删除，不再作为迁移源）。
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
