# 当前状态

> **状态**: `CURRENT` | **最后验证**: 2026-08-19

本文给新会话 agent 提供 Claread 当前事实。它不是迁移日志。

## 当前可运行基线

- 后端：`services/api/`，FastAPI，通用 Claread API。
- 客户端：`apps/miniprogram/` 微信小程序和 `apps/web/` Web 产品客户端。
- 数据库：`infra/docker/` 启动 PostgreSQL / Redis。
- schema：单一 `0001` 初始基线 SQL `infra/migrations/0001_initial.sql`，覆盖当前业务表；旧 Eval Center 控制面表不在 baseline 中。
- 词典：`dict_entries`、`dict_lookup_targets`、`dict_redirects` 已恢复到 `claread_postgres_data`。
- 控制面：`apps/directus/` Claread Console 当前只保留 enum-label-display / enum-label-interface 等通用 metadata 展示 module；旧 Eval Center、Workflow Lab、Node Lab、Render Scene Inspector、Parse Run Observability module 已物理删除，Console / Eval 治理化控制面尚未实现。

当前 Web 是新用户提交 Reader orchestration 的唯一客户端（不是 Claread 唯一用户客户端；小程序仍是稳定客户端），通过 Next.js BFF `/api/web/reader/records/*` 接入 Reader orchestration 主链；旧 Learning Workflow、Analysis Ask、Ask legacy lane、旧 Web Reader 产品页实现（`ReaderWorkbench` / `ReaderRecordWorkbenchSurface` / `ReaderPlateSnapshotSurface`）、旧 Directus Eval Center / Workflow Lab / Node Lab 已物理删除。计费写账闭环、统一监测和 Console / Eval 治理化控制面尚未实现。

## 已验证事实

- Reader 标注体系的数据层已收口为"文章收藏 + 用户高亮 + 用户笔记 + Ask Claread 显式引用"；数据库基线为单一 `0001_initial.sql`。当前验证入口见 `docs/operations/testing.md`。
- Web 已接入手机号登录、Reader 提交、Reader、历史记录、生词本、复习、文章收藏、用户高亮、用户笔记、Ask Claread、反馈和设置/配额；设置页已补齐昵称编辑、积分明细、默认透读和 Web 偏好云端同步，Library 已形成搜索/收藏筛选/排序的基础管理体验；公共区已覆盖首页、每日精读、示例文章和分享页；command palette 已实现。旧"分析任务"流程已物理删除。
- Daily Reader 已形成公开刊物基线：候选按内容分优先、成功产出绑定来源/话题多样性，正文按 reading unit 生成难度自适应精读；Web 提供独立刊物 surface、“浏览 / 学习”切换与登录返回自动收藏。当前事实见 `daily-reader.md`、`../../services/api/docs/daily-reader.md` 和 `../../apps/web/docs/design/surface-daily-reader.md`；真实 provider 成本/质量、连续 3 天自动生产和量化 Nielsen 复评仍是上线门禁。
- Reading Record 生命周期：最近阅读支持"从最近阅读中移除"（只隐藏最近阅读入口，全部阅读记录中仍存在；用户再次打开记录后 `recent_hidden_at` 被清除并重新进入最近阅读）；用户删除记录为产品层不可恢复操作——PostgreSQL 软删除（`deleted_at` / `lifecycle_status='deleted'` / `product_state='deleted'`），解析数据、原始输入、Stable Document、Base、Units、Anchors、Enhancement Layers、Ask 历史、批注与审计行全部保留，不物理删除 PostgreSQL 数据；删除后所有用户入口 fail closed；同事务收敛任务/运行并写入 Vector GC intent，向量由后台异步精确删除。Web 提供行级操作菜单（Sidebar 最近阅读与 Library 全部记录共享同一菜单组件）与删除前危险操作确认。
- `text_range` / `multi_text` 已稳定到同一套数据契约：Web 通过 `@claread/contracts` 常量对齐，后端按 UTF-16 offset、`fnv1a32-utf16` hash、Anchor Segment / Reading Unit 切片和 unit/segment 顺序校验局部/多段选区。
- AI 使用审计与结算底座已完成第一轮加固：`ai_usage_events`、capability code、usage scope 和 billing mode 已可承接后续词典 AI 与 Reader AI 能力。
- `Ask Claread` 当前以 `reader_record_ask` agentic v2 为唯一 Ask 执行链（article-bound、可回源、turn run 持久化、统一 SSE 事件合同）；旧 Analysis Ask 和 Ask legacy lane 已物理删除。当前正式事实以 `docs/product/ask-claread.md` 与 `docs/architecture/ask-claread.md` 为准；已实现 turn run 持久化、citation 回源导航、客户端提交幂等 reconcile、thread memory compaction、learner reasoning 投影和文章 RAG / Web search 受控工具。
- Reader 当前生产链以 Reading Record、Stable Document、Reading Units、Anchor Segments、Enhancement Layers、`reader_events` 为事实源，Web 通过 `/app/read` 与 `/app/reader/[recordId]` + BFF `/api/web/reader/records/*` 接入。旧 `learning_workflow.py`、Analysis service 写入路径（`services/api/app/services/analysis/` 整目录 `.py` 源文件已删除）、`analysis_results.render_scene_json` 事实源、旧 `/reader/records/{id}/scene` 和旧 Web Reader 产品页实现（`ReaderWorkbench` / `ReaderRecordWorkbenchSurface` / `ReaderPlateSnapshotSurface`）已物理删除。后端代码对旧 `analysis_*` 业务表的引用已全部迁移到 Reading Record 事实，旧表已不在 baseline migration 中；`analysis_windows` 与 `layer_analysis_plans` 是新链在用表，残留本地库旧表清理尚未完成。Academic workflow 尚未实现，后续按新 contract 单独评估；Daily Reader 保持固定 workflow。Reader orchestration 当前架构权威上下文在 `docs/architecture/reader-orchestration.md`。
- Reader 可恢复解析已入基线：解析失败（`failed`）的正文和已完成内容仍可阅读；Web 在解析失败时提供友好提示与手动恢复；恢复保留 Record、URL、原始输入与历史，只创建新的 successor 任务，恢复不重复计费（successor 执行按 `internal_only` 计费）；纯 provider_timeout 失败支持 bounded automatic recovery（冷却与次数上限内自动重建）。后端已有结构化恢复告警兼容面，Console / 外部投递尚未实现。可恢复解析已完成离线验收；真实 provider、真实浏览器与生产部署验收未执行。
- Article RAG：`READER_ARTICLE_RAG_ENABLED` 默认 `false`，开启时由 index worker 构建单路径索引并通过 Ask 受控工具消费；真实 provider 的 acceptance 尚未作为本轮验证执行（本地与 CI 均为 offline 测试，offline 测试不是 production acceptance 的证据）；运维 reindex 入口为显式 CLI（默认 dry-run，`--execute` 才写入）。
- Example Lab 按 Directus 原生 Collection `eval_example_lab_entries` 实现（Collection 仍保留）；旧 Eval Center module、Node Lab、Workflow Lab、Run History、Render Scene Inspector、Parse Run Observability 已物理删除，Console / Eval 治理化控制面尚未实现。grammar RAG / Example Lab 契约已收口：无 `teaching_goal`、无 `structure_signals`、无 `retrieval_version`；`variant` 是硬边界。
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

### 主线：Reader orchestration 稳定推进与治理化控制面规划

用户提交内容的 `learning` 主链当前使用 bounded agentic orchestration；旧 Learning Workflow、Analysis Ask、Ask legacy lane、旧 Web/Mini 页面和旧 Directus Eval Center / Workflow Lab / Node Lab 已物理删除。当前架构权威上下文位于 `docs/architecture/reader-orchestration.md`。

当前主线推进重点为：旧 Eval 控制面表与 legacy `analysis_*` 残留本地库清理（范围见 `docs/architecture/workflow-history.md`，保护 `analysis_windows` 与 `layer_analysis_plans`）、尚未实现的 Console / Eval 治理化控制面、统一监测与计费适配、测试治理与代码架构优化。`academic workflow` 尚未实现，需单独设计。

`daily_reader_workflow` 保持固定 workflow 形态，与旧 Learning Workflow 已解耦。

新架构的产品原则继续生效：LLM / planner 承担更多策略判断，代码负责红线边界、结构契约、安全校验、审计、计费、限流和回退。高影响输入适配必须先给用户 Candidate Reading Base 预览、修改和确认；确认后才生成稳定阅读基座与稳定阅读单元，一经确认不可被后续增强改写。

### 副线：Ask Claread 维护

Ask Claread 当前以 `reader_record_ask` agentic v2 为唯一 Ask 执行链；旧 Analysis Ask 和 Ask legacy lane 已物理删除。后续按需优化回答质量、correctness、真实 LLM smoke/eval 和用户体验，但保持 article-bound、可回源、可确认写入、统一审计/结算的冻结边界。

Ask Claread 作为 consumer / sidecar integration 接入 Stable Reading Base 和 Reading Units，不是 Reader orchestration 控制中心。

### 副线：Web 次要功能补齐与页面设计收口

Web 主产品链路已形成可用基线，公共区、认证区和私有区路由已完整覆盖。后续重点是次要功能补齐、页面设计收口和体验打磨，而不是继续搭建基础框架。

Daily Reader 的代码与离线/本地浏览器收口已完成；后续只在真实生产验收发现问题时做针对性修正，不再扩展第二套 surface 或解析框架。

### 副线：Claread Console 控制面治理化建设

Claread Console 当前只保留 enum-label-display / enum-label-interface 等通用 metadata 展示 module；旧 Eval Center、Workflow Lab、Node Lab、Render Scene Inspector、Parse Run Observability module 已物理删除。治理化控制面尚未实现，后续应按治理价值排序推进，而不是泛化铺开后台功能。

### 维护线：小程序与多端稳定性维护

小程序是稳定客户端，保持回归约束。Reader 与 Ask 的后续变更都不应破坏当前小程序主链路。多端共享后端和数据库的稳定性是持续维护项。

## 已知边界

- 真实 `.env`、模型 key、微信 secret、Zilliz token 不提交。
- `packages/shared-utils/` 仍为预留位置（目录尚未创建）；`apps/directus/` 只承担控制面，不承担核心执行面。
- 小程序 UI/UX 是当前实现，不代表 Web 端体验上限。
- 模型输出质量和结构化输出稳定性依赖 `services/api/.env` 中的模型 profile；更换模型后需要重新跑解析链路。
- 旧脚本式 regression suite 不属于当前仓库验证入口；旧 Eval Center module 已物理删除，评测控制面尚未实现，规划仍走 Directus + 自建 eval harness + LLM-as-a-Judge 路线。
- Ask Claread 当前以当前文章为绑定上下文，通过受控工具（evidence 展开、文章 RAG 检索、显式授权的 Web search）取证；用户高亮与用户笔记只通过显式引用进入 Ask，不存在独立"用户学习资产自由查询"产品面。
- Reader orchestration 当前只面向用户提交内容的 `learning workflow`。`academic workflow` 尚未实现；`daily_reader_workflow` 保持固定 workflow；当前产品范围不包括全局用户资产整理、跨记录语义 RAG 和知识库化。
- Reader orchestration 目标架构与模块合同已是当前生产架构事实源；尚未完成事项见 `docs/development/mainline.md`。
- 旧 Eval Center、Workflow Lab、Node Lab、Render Scene Inspector、Parse Run Observability module 已物理删除，Console / Eval 治理化控制面尚未实现；不把 Directus 变成执行面。`eval_example_lab_entries` 作为 Directus Collection 保留，不属于已删除的 Eval Center module。
- Example Lab 是 Directus Collection，不是 Eval Center 独立 mode；grammar RAG / Example Lab 契约已收口（无 teaching_goal、无 structure_signals、无 retrieval_version；variant 是硬边界）。

## 文档使用规则

新会话 agent 应先读：

1. `AGENTS.md`
2. `docs/README.md`
3. `docs/product/current-state.md`
4. 最近目录的 `AGENTS.md`

如果文档和代码冲突，以当前代码、数据库和测试结果为准，并补充文档或建立后续任务。
