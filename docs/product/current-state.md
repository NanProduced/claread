# 当前状态

> **状态**: `CURRENT` | **最后验证**: 2026-06-17

本文给新会话 agent 提供 Claread 当前事实。它不是迁移日志。

## 当前可运行基线

- 后端：`services/api/`，FastAPI，通用 Claread API。
- 客户端：`apps/miniprogram/` 微信小程序和 `apps/web/` Web 产品客户端。
- 数据库：`infra/docker/` 启动 PostgreSQL / Redis。
- schema：两份 `0001` 初始基线 SQL：`infra/migrations/0001_initial_schema.sql` 负责 Claread 业务表，`infra/migrations/eval-center/0001_eval_center_control_plane.sql` 负责 Eval Center 控制面表。两者都是各自边界内的 initial schema，不是增量 migration。
- 词典：`dict_entries`、`dict_lookup_targets`、`dict_redirects` 已恢复到 `claread_postgres_data`。
- 控制面：`apps/directus/` Claread Console，已进入可用控制面阶段。

当前基线已从"双端可回归基线"推进到"多端产品基线 + 可用控制面"。Web 已形成可用产品基线，不再只是 baseline / 早期探索。小程序是稳定客户端，Web 已通过 Next.js BFF 接入真实后端主链路，两端共享同一套后端业务核心和 PostgreSQL 数据。Claread Console 已承载 Eval Center、Render Scene Inspector、Parse Run Observability 和 Example Lab 等可用能力。

## 已验证事实

- 2026-05-21 验证：Reader 标注体系的数据层已收口为"文章收藏 + 用户高亮 + 用户笔记 + Ask Claread 显式引用"；数据库基线已压回单一 `0001_initial_schema.sql`，Web 与小程序构建通过。
- 2026-05-16 验证：Web typecheck / build 通过；本轮通过本地浏览器回归核对 Reader 的 selection toolbar、lookup preview 和 `multi_text` 交互表现；`services/api/tests/test_user_assets.py` 和 `services/api/tests/test_user_annotations.py` 通过。
- Web 已接入手机号登录、分析任务、Reader、历史记录、生词本、复习、文章收藏、用户高亮、用户笔记、Ask Claread、反馈和设置/配额；设置页已补齐昵称编辑、积分明细、默认透读和 Web 偏好云端同步，Library 已形成搜索/收藏筛选/排序的基础管理体验；公共区已覆盖首页、每日精读、示例文章和分享页；command palette 已实现。
- `text_range` / `multi_text` 已稳定到同一套数据契约：Web 和小程序共享 `@claread/contracts` 常量，后端按 UTF-16 offset、`fnv1a32-utf16` hash、render scene sentence 切片和 sentence 顺序校验局部/多段选区。
- AI 使用审计与结算底座已完成第一轮加固：`ai_usage_events`、capability code、usage scope 和 billing mode 已可承接后续词典 AI 与 Reader AI 能力。
- `Ask Claread` 已完成 Reader 2.0 底座上的 agent-loop-only 重构主线，当前处于可运行基线状态（live service 不再调用 planner route resolver 或 semantic planner LLM；`planner_first` 仅作为历史 trace value 保留）。当前正式事实以 `docs/product/ask-claread.md` 与 `docs/architecture/ask-claread.md` 为准；已实现 turn-run/eval-trace 持久化、record/asset disambiguation、grammar_note supplement 生命周期、current-run hydration、follow-up suggestions、tool trace/citation 展示和单次 agent-loop repair。
- workflow 解析主链路可跑通：learning / academic 双模式、grammar RAG 检索、prompt 策略和 canonical result 生成已形成完整链路；但当前 AI Workflow 形态已被判定不足以承接后续 Reader 产品目标，下一阶段主线是面向用户提交内容的 bounded agentic Reader orchestration。
- Eval Center 已落地三个公开 mode：`node-lab`、`workflow-lab`、`run-history`。Node Lab 承载单节点评测与 judge，Workflow Lab 承载候选版本双跑 compare 与 review，Run History 承载统一只读回看。
- Example Lab 按 Directus 原生 Collection `eval_example_lab_entries` 实现，不在 Eval Center module 导航内。grammar RAG / Example Lab 契约已收口：无 `teaching_goal`、无 `structure_signals`、无 `retrieval_version`；`variant` 是硬边界。
- ReaderWorkbench 已拆出 Reader canvas、sentence row、annotation overlay 和 selection helper，后续 Reader UI 迭代应优先沿这些边界推进。
- Docker Compose project 使用 `claread`。
- 本地 PostgreSQL volume 使用 `claread_postgres_data`。
- 本地 Redis volume 使用 `claread_redis_data`。
- PostgreSQL 使用普通 `postgres:16-alpine`，当前不依赖 pgvector。
- 词典三表恢复基线：
  - `dict_entries`: 253300
  - `dict_lookup_targets`: 1014676
  - `dict_redirects`: 848873
  - `exam_tags` 非空词条：20239
- 微信小程序本地调试需要在微信开发者工具中关闭本地域名校验，或使用已配置的合法域名。

## 多端决策

Claread 已从单一微信小程序开发转为多端产品开发。

后端不为 Web 单独复制一套。Web、小程序、Directus 和后续 App 应共享：

- PostgreSQL 数据。
- 用户、身份、记录、词典、用户资产。
- 分析任务和 workflow。
- prompt / model 配置机制。
- LangSmith trace、Directus 控制面和后续评测数据。

客户端差异通过以下方式处理：

- auth adapter。
- render profile / render snapshot。
- capability profile。
- 客户端 UI 和平台 API adapter。

## 当前主要方向

### 主线：Reader agentic orchestration 调研与方案设计

当前准备把用户提交内容的 `learning workflow` 与 `academic workflow` 从固定 AI Workflow 重构为 bounded agentic orchestration。正式实现前先完成产品与技术调研，重点回答成本/速度/负载、运行时框架、Stable Reading Base、Reading Units、Navigation Skeleton、Parsed Decision、增量渲染、Ask/RAG 接入、计费审计和 rollout / rollback。

本轮不重构 `daily_reader_workflow`。Daily Reader 的场景是定时、定量、后台生产稳定公开阅读页面，固定 workflow 仍是合适形态；本轮只考虑它与新 Reader 契约的兼容边界和可复用经验。

新架构的产品原则是：LLM / planner 承担更多策略判断，代码负责红线边界、结构契约、安全校验、审计、计费、限流和回退。高影响输入适配必须先给用户 Candidate Reading Base 预览、修改和确认；确认后才生成稳定阅读基座与稳定阅读单元，一经确认不可被后续增强改写。

### 副线：Ask Claread baseline 维护与接入准备

Ask Claread 已冻结到 agent-loop-only 可用 baseline，当前不再扩 planner/retrieval 架构面。后续按需优化回答质量、correctness、真实 LLM smoke/eval 和用户体验，但保持 article-bound、可回源、可确认写入、统一审计/结算的冻结边界。

Reader orchestration 方案设计时，Ask Claread 应作为 consumer / sidecar integration 重新接入 Stable Reading Base 和 Reading Units，而不是把 Ask agent loop 提升为 Reader orchestration 控制中心。

### 副线：Web 次要功能补齐与页面设计收口

Web 主产品链路已形成可用基线，公共区、认证区和私有区路由已完整覆盖。后续重点是次要功能补齐、页面设计收口和体验打磨，而不是继续搭建基础框架。

### 副线：Claread Console 控制面治理化

Claread Console 已进入可用控制面阶段，Eval Center、Render Scene Inspector、Parse Run Observability 和 Example Lab 已有可用能力。后续重点转向控制面治理化——按治理价值排序推进，而不是泛化铺开后台功能。

### 维护线：小程序与多端稳定性维护

小程序是稳定客户端，保持回归约束。Reader 2.0 与 Ask 重构都不应破坏当前小程序主链路。多端共享后端和数据库的稳定性是持续维护项。

## 已知边界

- 真实 `.env`、模型 key、微信 secret、Zilliz token 不提交。
- `packages/shared-utils/` 仍为预留位置；`apps/directus/` 只承担控制面，不承担核心执行面。
- 小程序 UI/UX 是当前实现，不代表 Web 端体验上限。
- 模型输出质量和结构化输出稳定性依赖 `services/api/.env` 中的模型 profile；更换模型后需要重新跑解析链路。
- 旧脚本式 regression suite 不进入新仓库主线；当前评测控制面已落到 Directus / Eval Center，后续仍按 Directus + 自建 eval harness + LLM-as-a-Judge 的路线继续演进。
- Ask Claread 当前的跨文章稳定主路径以 `record_ref / known title reference / external analysis/supplement asset` 为主；用户高亮与用户笔记只通过显式引用进入 Ask，不存在独立"用户学习资产自由查询"产品面。
- Reader orchestration 当前只面向用户提交内容的 `learning workflow` 与 `academic workflow`。`daily_reader_workflow` 保持固定 workflow；全局用户资产整理、跨记录语义 RAG、知识库化不进入本轮范围。
- Reader orchestration 的正式实现必须在 R0-R11 调研完成后再启动；调研前不拍板最终框架、schema、任务队列、迁移策略和 UI 流式事件模型。
- Eval Center 当前公开主路径只有 node-lab、workflow-lab、run-history；judge / review 继续锚定 compare 或 trial，不把 Directus 变成执行面。Eval Center v1 是 learning-only，所有 eval adapter 入口均在 schema 层拒绝 `reading_goal="academic"`；academic graph 在后端主 workflow `/analyze` 中继续保留，但不属于当前 eval-center 公开评测面。
- Example Lab 是 Directus Collection，不是 Eval Center 独立 mode；grammar RAG / Example Lab 契约已收口（无 teaching_goal、无 structure_signals、无 retrieval_version；variant 是硬边界）。

## 文档使用规则

新会话 agent 应先读：

1. `AGENTS.md`
2. `docs/README.md`
3. `docs/product/current-state.md`
4. 最近目录的 `AGENTS.md`

如果文档和代码冲突，以当前代码、数据库和测试结果为准，并补充文档或建立后续任务。
