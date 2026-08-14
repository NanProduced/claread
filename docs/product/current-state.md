# 当前状态

> **状态**: `CURRENT` | **最后验证**: 2026-08-03（Architectural Cutover Complete；旧 Learning Workflow / Analysis Ask / 旧 Web/Mini 页面 / 旧 Directus Eval Center / Workflow Lab / Node Lab 已物理删除）

本文给新会话 agent 提供 Claread 当前事实。它不是迁移日志。

## 当前可运行基线

- 后端：`services/api/`，FastAPI，通用 Claread API。
- 客户端：`apps/miniprogram/` 微信小程序和 `apps/web/` Web 产品客户端。
- 数据库：`infra/docker/` 启动 PostgreSQL / Redis。
- schema：单一 `0001` 初始基线 SQL `infra/migrations/0001_initial.sql`，覆盖当前业务表；旧 Eval Center 控制面表不在 baseline 中。
- 词典：`dict_entries`、`dict_lookup_targets`、`dict_redirects` 已恢复到 `claread_postgres_data`。
- 控制面：`apps/directus/` Claread Console 当前只保留 enum-label-display / enum-label-interface 等通用 metadata 展示 module；旧 Eval Center、Workflow Lab、Node Lab、Render Scene Inspector、Parse Run Observability module 已在 cutover 中物理删除，按新 orchestration 重建属于 post-cutover backlog。

当前基线已推进到 Architectural Cutover Complete：Web 是新用户提交 Reader orchestration 的唯一客户端（不是 Claread 唯一用户客户端；小程序仍是稳定客户端），通过 Next.js BFF `/api/web/reader/records/*` 接入新 Reader orchestration 主链；旧 Learning Workflow、Analysis Ask、Ask legacy lane、旧 Web Reader 产品页实现（`ReaderWorkbench` / `ReaderRecordWorkbenchSurface` / `ReaderPlateSnapshotSurface`）、旧 Directus Eval Center / Workflow Lab / Node Lab 已物理删除。Operational Readiness（计费、统一监测、Console/Eval 按新 orchestration 重建等）属于 post-cutover backlog。

## 已验证事实

- 2026-05-21 验证：Reader 标注体系的数据层已收口为"文章收藏 + 用户高亮 + 用户笔记 + Ask Claread 显式引用"；数据库基线已压回单一 `0001_initial.sql`，Web 与小程序构建通过。
- 2026-05-16 验证：Web typecheck / build 通过；本轮通过本地浏览器回归核对 Reader 的 selection toolbar、lookup preview 和 `multi_text` 交互表现；`services/api/tests/test_user_assets.py` 和 `services/api/tests/test_user_annotations.py` 通过。
- Web 已接入手机号登录、Reader 提交、Reader、历史记录、生词本、复习、文章收藏、用户高亮、用户笔记、Ask Claread、反馈和设置/配额；设置页已补齐昵称编辑、积分明细、默认透读和 Web 偏好云端同步，Library 已形成搜索/收藏筛选/排序的基础管理体验；公共区已覆盖首页、每日精读、示例文章和分享页；command palette 已实现。旧"分析任务"流程已在 cutover 中物理删除。
- `text_range` / `multi_text` 已稳定到同一套数据契约：Web 通过 `@claread/contracts` 常量对齐，后端按 UTF-16 offset、`fnv1a32-utf16` hash、Anchor Segment / Reading Unit 切片和 unit/segment 顺序校验局部/多段选区。
- AI 使用审计与结算底座已完成第一轮加固：`ai_usage_events`、capability code、usage scope 和 billing mode 已可承接后续词典 AI 与 Reader AI 能力。
- `Ask Claread` 已完成 Reader 2.0 底座上的 agent-loop-only 重构主线，并在 cutover 中成为唯一 Ask 生产链：旧 Analysis Ask、Ask legacy lane 已物理删除，`reader_record_ask` agentic v2 是唯一 Ask 执行链（article-bound、可回源、turn run 持久化、统一 SSE 事件合同）。当前正式事实以 `docs/product/ask-claread.md` 与 `docs/architecture/ask-claread.md` 为准；已实现 turn run 持久化、citation 回源导航、客户端提交幂等 reconcile、thread memory compaction、learner reasoning 投影和文章 RAG / Web search 受控工具。
- Reader 主链已完成从旧 AI Workflow 到 bounded agentic orchestration 的硬切换：旧 `learning_workflow.py`、Analysis service 写入路径（`services/api/app/services/analysis/` 整目录 `.py` 源文件已删除）、`analysis_results.render_scene_json` 作为事实源、旧 `/reader/records/{id}/scene`、旧 Web Reader 产品页实现（`ReaderWorkbench` / `ReaderRecordWorkbenchSurface` / `ReaderPlateSnapshotSurface`）已物理删除；新链以 Reading Record、Stable Document、Reading Units、Anchor Segments、Enhancement Layers、`reader_events` 为事实源，Web 通过 `/app/read` 与 `/app/reader/[recordId]` + BFF `/api/web/reader/records/*` 接入。后端代码对旧 `analysis_*` 业务表的引用已全部迁移到 Reading Record 事实，旧表已不在 baseline migration 中；`analysis_windows` 与 `layer_analysis_plans` 是新链在用表，残留本地库旧表清理属于 post-cutover 数据清理 backlog。Academic workflow 在 cutover 中下线，后续按新 contract 单独评估；Daily Reader 保持固定 workflow 不进入本轮 runtime conversion。Reader orchestration 专项权威上下文在 `docs/architecture/reader-orchestration.md`。
- Example Lab 按 Directus 原生 Collection `eval_example_lab_entries` 实现（Collection 仍保留）；旧 Eval Center module、Node Lab、Workflow Lab、Run History、Render Scene Inspector、Parse Run Observability 已在 cutover 中物理删除，按新 orchestration 重建属于 post-cutover backlog。grammar RAG / Example Lab 契约已收口：无 `teaching_goal`、无 `structure_signals`、无 `retrieval_version`；`variant` 是硬边界。
- Web Reader 产品页实现为 `apps/web/src/app/(private)/app/reader/[recordId]/plate-page.tsx` + `ReaderRecordPlateSurface`，基于 Plate.js projection；旧 `ReaderWorkbench` / `ReaderRecordWorkbenchSurface` / `ReaderPlateSnapshotSurface` 已物理删除。后续 Reader UI 迭代应沿 Plate.js projection 边界推进。
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
- Reader orchestration runtime 与 Ask agentic v2 执行链。
- prompt / model 配置机制。
- LangSmith trace、Directus 控制面和后续评测数据。

客户端差异通过以下方式处理：

- auth adapter。
- render profile / render snapshot。
- capability profile。
- 客户端 UI 和平台 API adapter。

## 当前主要方向

### 主线：Reader agentic orchestration post-cutover backlog

Reader agentic orchestration 的 Architectural Cutover 已完成：用户提交内容的 `learning` 主链已经从旧固定 AI Workflow 切换到 bounded agentic orchestration，旧 Learning Workflow、Analysis Ask、Ask legacy lane、旧 Web/Mini 页面、旧 Directus Eval Center / Workflow Lab / Node Lab 已物理删除。专项权威上下文位于 `docs/architecture/reader-orchestration.md`；该目录的目标架构与模块合同同时是当前生产架构的事实源。

当前主线推进重点为 post-cutover backlog：旧 Eval 控制面表与 legacy `analysis_*` 残留本地库清理（范围见 `docs/architecture/workflow-history.md`，保护 `analysis_windows` 与 `layer_analysis_plans`）、Console / Eval 按新 orchestration 重建（治理化控制面）、统一监测与计费适配、测试治理与代码架构优化。`academic workflow` 的 agentic orchestration 重构待 learning workflow 在 post-cutover 稳定后再单独设计。

`daily_reader_workflow` 不进入本轮 runtime conversion，保持固定 workflow 形态，与旧 Learning Workflow 已解耦。

新架构的产品原则继续生效：LLM / planner 承担更多策略判断，代码负责红线边界、结构契约、安全校验、审计、计费、限流和回退。高影响输入适配必须先给用户 Candidate Reading Base 预览、修改和确认；确认后才生成稳定阅读基座与稳定阅读单元，一经确认不可被后续增强改写。

### 副线：Ask Claread post-cutover 维护

Ask Claread 已完成 Reader 2.0 底座上的 agent-loop-only 重构主线，并在 cutover 中成为唯一 Ask 生产链：旧 Analysis Ask、Ask legacy lane 已物理删除，`reader_record_ask` agentic v2 是唯一 Ask 执行链。后续按需优化回答质量、correctness、真实 LLM smoke/eval 和用户体验，但保持 article-bound、可回源、可确认写入、统一审计/结算的冻结边界。

Ask Claread 作为 consumer / sidecar integration 接入 Stable Reading Base 和 Reading Units，不是 Reader orchestration 控制中心。

### 副线：Web 次要功能补齐与页面设计收口

Web 主产品链路已形成可用基线，公共区、认证区和私有区路由已完整覆盖。后续重点是次要功能补齐、页面设计收口和体验打磨，而不是继续搭建基础框架。

### 副线：Claread Console 控制面治理化重建

Claread Console 当前只保留 enum-label-display / enum-label-interface 等通用 metadata 展示 module；旧 Eval Center、Workflow Lab、Node Lab、Render Scene Inspector、Parse Run Observability module 已在 cutover 中物理删除。后续重点是按新 orchestration 重建治理化控制面——按治理价值排序推进，而不是泛化铺开后台功能。

### 维护线：小程序与多端稳定性维护

小程序是稳定客户端，保持回归约束。Reader 2.0 与 Ask 重构都不应破坏当前小程序主链路。多端共享后端和数据库的稳定性是持续维护项。

## 已知边界

- 真实 `.env`、模型 key、微信 secret、Zilliz token 不提交。
- `packages/shared-utils/` 仍为预留位置（目录尚未创建）；`apps/directus/` 只承担控制面，不承担核心执行面。
- 小程序 UI/UX 是当前实现，不代表 Web 端体验上限。
- 模型输出质量和结构化输出稳定性依赖 `services/api/.env` 中的模型 profile；更换模型后需要重新跑解析链路。
- 旧脚本式 regression suite 不进入新仓库主线；旧 Eval Center module 已在 cutover 中物理删除，后续评测控制面按新 orchestration 重建，仍走 Directus + 自建 eval harness + LLM-as-a-Judge 的路线。
- Ask Claread 当前以当前文章为绑定上下文，通过受控工具（evidence 展开、文章 RAG 检索、显式授权的 Web search）取证；用户高亮与用户笔记只通过显式引用进入 Ask，不存在独立"用户学习资产自由查询"产品面。
- Reader orchestration 当前只面向用户提交内容的 `learning workflow`。`academic workflow` 暂缓重构；`daily_reader_workflow` 保持固定 workflow；全局用户资产整理、跨记录语义 RAG、知识库化不进入本轮范围。
- Reader orchestration 的 Architectural Cutover 已完成，目标架构与模块合同已是当前生产架构事实源；post-cutover backlog 见 `docs/development/mainline.md`。
- 旧 Eval Center、Workflow Lab、Node Lab、Render Scene Inspector、Parse Run Observability module 已在 cutover 中物理删除，按新 orchestration 重建属于 post-cutover backlog；不把 Directus 变成执行面。`eval_example_lab_entries` 作为 Directus Collection 保留，不属于已删除的 Eval Center module。
- Example Lab 是 Directus Collection，不是 Eval Center 独立 mode；grammar RAG / Example Lab 契约已收口（无 teaching_goal、无 structure_signals、无 retrieval_version；variant 是硬边界）。

## 文档使用规则

新会话 agent 应先读：

1. `AGENTS.md`
2. `docs/README.md`
3. `docs/product/current-state.md`
4. 最近目录的 `AGENTS.md`

如果文档和代码冲突，以当前代码、数据库和测试结果为准，并补充文档或建立后续任务。
