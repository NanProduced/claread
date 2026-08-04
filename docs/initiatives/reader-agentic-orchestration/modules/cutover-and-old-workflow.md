# Cutover 与旧 AI Workflow 处理

> 状态：`Frozen closeout record（Architectural Cutover Complete；DOC-R2 代码现场核验与 D6-A0 依赖审计作为历史证据保留；本文不再作为活跃任务入口）`
> 最后更新：2026-08-03（DOC-TRUTH-LIFECYCLE-R2：冻结为精确 closeout 记录；CUTOVER-DOC-TRUTH-CLOSEOUT-R1 已完成单轨化重写）
> 范围：cutover 落地结论、当前单轨架构入口、必须保护的数据、post-cutover backlog，以及历史 cutover 过程证据。本文是历史 closeout，新任务入口走 `implementation-plan.md` post-cutover backlog。

## Architectural Cutover Complete（当前事实）

Reader 与 Ask 主链已经单轨化，旧生产链已物理删除，且 Markdown Reading Record 到 Ask evidence/citation 的联合产品路径已经通过验收。Operational Readiness（计费、统一监测、Console/Eval 按新 orchestration 重建等）属于 post-cutover backlog，不在此处写成已完成。

### 当前单轨架构入口

以下入口是 cutover 后的稳定 Reader / Ask 用户路径。旧 URL、旧页面、旧 BFF route、旧 worker、旧 reset 命令均已注销并物理删除，不再保留 fallback 或 alias 重定向。

| Surface | 当前入口 / route | 当前 source of truth | id 语义 |
|---|---|---|---|
| Web 提交产品入口 | `/app/read` | `POST /api/web/reader/records/input` -> FastAPI `/reader/records/input` | 新 `Reading Record.record_id` |
| Web Reader 产品页 | `/app/reader/[recordId]` | `GET /api/web/reader/records/[recordId]/snapshot` 等 record-nested BFF | 新 `Reading Record.record_id` |
| Web BFF Reader records | `/api/web/reader/records/*` | 转发到 FastAPI `/reader/records/*` | 新 `Reading Record.record_id` |
| Web BFF source artifacts | `/api/web/reader/source-artifacts/*` | 转发到 FastAPI `/reader/source-artifacts/*` | `artifact_id` |
| Web BFF record-nested Ask | `/api/web/reader/records/[recordId]/ask/*` | 转发到 FastAPI `/reader/records/{record_id}/ask/*` v2 | `reading_record_id` + `reading_record_anchor` |
| FastAPI Reader records | `/reader/records/*` | PostgreSQL Reading Record / Stable Document / Reading Units / Anchor Segments / Enhancement Layers / reader_events | 新 `Reading Record.record_id` |
| FastAPI record-nested Ask v2 | `/reader/records/{reading_record_id}/ask/*` | `reader_ask_*` 共享表 + Reading Record snapshot / anchor gate | `reading_record_id` + anchor |

### 已物理删除的旧生产链（不再可达）

以下旧入口、旧页面、旧 BFF route、旧 worker、旧 reset 命令在 cutover 中已注销并物理删除。它们仅作为历史路径在本节及历史段落中提及，不再作为当前入口、fallback 或 alias 重定向：

- 旧 Learning Workflow（`learning_workflow.py` 固定全量 graph）与对应 Analysis service 写入路径（`services/api/app/services/analysis/` 整目录 `.py` 源文件已删除，仅留 `__pycache__/*.pyc` 缓存）。
- 旧 Analysis Ask、Ask legacy lane 与 `analysis_record_id` 写入路径。
- 旧 Web Reader 产品页实现：`/app/reader/{recordId}` 与 `/app/reader/[recordId]` 是同一运行时动态路由（Next.js `[recordId]` 渲染为 `{recordId}`），cutover 替换的是该路由的页面实现——旧 scene adapter + `ReaderSceneResponseDto` + `ReaderRecordWorkbenchSurface` / `ReaderWorkbench.tsx` 已物理删除，替换为 `plate-page.tsx` + `ReaderRecordPlateSurface`。URL 本身不变。
- 临时验证页 `/app/reader-plate` 与 `/app/reader-record/{recordId}`（已物理删除，仅在 e2e 404 断言与 source-guard 测试中保留为负向断言）。
- 旧 Web 组件 `ReadingRecordCommandGroup.tsx`、`active-analysis-task-indicator.tsx`、`reading-record-activity-indicator.tsx`、`analysis-task-client.ts`、`ReaderNotePanel.tsx`、`ReaderRecordWorkbenchSurface.tsx`、`ReaderPlateSnapshotSurface.tsx`、`snapshot-to-reader-workbench.ts` 及其测试已物理删除（仅在 `reader-orchestration-source-guard.test.ts` 的 `PHYSICAL_DELETED_PATHS` 列表中保留为防回潮断言）。
- 旧 Web BFF route：`/api/web/analysis/*`、`/api/web/reading-record/*`、`/api/web/reader-plate/*`、`/api/web/reader-ask/*`、`/api/web/command-palette/records`、`/api/web/reader-annotations`、`/api/web/reader-notes` 的 legacy 写入分支。
- 旧 FastAPI `/reader/records/{id}/scene` 与 `analysis_results.render_scene_json` 作为事实源。
- 旧 `reader_scene.py` 作为 authoritative service。
- 旧 Directus Eval Center、Workflow Lab、Node Lab、Render Scene Inspector、Parse Run Observability module。
- 旧 reset 命令 `infra/scripts/reset-eval-center-data.ps1`（已删除；`init-eval-center-dev.ps1` 保留为 `[retired]` fail-closed tombstone，不属于当前生产控制面）。
- 旧 pnpm scripts `directus:parse-run:sync-metadata` 与 `directus:eval-center:sync-metadata`（已从 root 与 `apps/directus` `package.json` 移除；`apps/directus/scripts/check-logical-registration.mjs` 强制禁止回潮）。

#### 旧 `analysis_*` 表的精确状态

cutover 删除的是 Analysis service **写入路径**，不是表本身。表 DROP 属于 DATA-AUDIT post-cutover backlog。当前数据库中 9 张 `analysis_*` / `layer_analysis_*` 表分三类：

**Legacy 孤儿表（无当前 `.py` 源码引用，待 DATA-AUDIT 清理）**：

- `analysis_debug_snapshots`
- `analysis_task_events`
- `analysis_overview_tasks`
- `analysis_overview_task_events`

**Legacy 仍被只读引用表（写入路径已删除；当前 `.py` 仍只读引用以服务用户资产 CRUD 与 quota 历史，DROP 前必须先迁移引用）**：

- `analysis_records` — 被 `services/api/app/services/user_assets/records.py`（CRUD）、`services/api/app/services/text_anchors.py`（LEFT JOIN）、`services/api/app/services/quota/ledger.py`（LEFT JOIN）引用。
- `analysis_results` — 被 `services/api/app/services/user_assets/records.py`（CRUD）、`services/api/app/services/text_anchors.py`（LEFT JOIN）引用。
- `analysis_tasks` — 被 `services/api/app/services/quota/ledger.py`（LEFT JOIN 解析 legacy `analysis_deduct` ledger 条目的文章标题）引用。

**新链在用表（保护，不属于清理范围）**：

- `layer_analysis_plans` — 由 `infra/migrations/0015_layer_analysis_plans.sql` 创建，被 `services/api/app/services/reader_orchestration/` 下 `zplus_bootstrap.py`、`grammar_window_publisher.py`、`pipeline_runner.py`、`repository.py`、`job_bootstrap.py` 读写。
- `analysis_windows` — 由 `infra/migrations/0015_layer_analysis_plans.sql` 创建，被 `services/api/app/services/reader_orchestration/` 下 `grammar_window_worker.py`、`grammar_window_publisher.py`、`pipeline_runner.py`、`repository.py`、`completion_finalizer.py`、`zplus_bootstrap.py` 读写。

DATA-AUDIT 必须显式保护 `analysis_windows` 与 `layer_analysis_plans`；对仍被只读引用的 3 张 legacy 表必须先迁移 `user_assets/records.py`、`text_anchors.py`、`quota/ledger.py` 中的引用，再 DROP。**禁止使用 "删除 analysis_*" 这类 wildcard 指令。**

**DATA-LEGACY-IDENTITY-EXIT 状态（2026-08-04）**：上述代码引用已全部退出并物理删除 — `user_assets/records.py`、`text_anchors.py`、`schemas/user_assets/records.py` 已删除（L1 logical exit + P1 physical deletion，commit `0670c197` / `7ec356ca`），`quota/ledger.py` 不再 JOIN legacy 表，annotations/notes/favorites/feedback/usage 不再有 analysis identity。app 代码对 7 张 legacy 表零消费，由 `services/api/tests/test_data_legacy_identity_exit_guard.py` 静态锁定。剩余工作属于 D2 schema baseline：7 张 legacy 表的 DDL DROP 与共享表 legacy 列删除。

### 必须保护的数据（post-cutover 数据清理仍须遵守）

Claread 尚未上线，受控验证中可重置本地业务数据，但 cutover 后的数据清理仍须保护以下共享产品表与已冻结能力，不得误删：

- 词典三表：`dict_entries`、`dict_lookup_targets`、`dict_redirects`。
- `reader_ask_*` 共享表（Ask 在 cutover 后唯一生产链）。
- `eval_example_lab_entries`（Directus Collection，不属于已删除的 Eval Center module）。
- Reader user assets（`user_annotations`、`reader_notes`、用户高亮、笔记、收藏、生词本 source refs）。
- usage/ledger（`ai_usage_events`、credit ledger、capability code、usage scope、billing mode）。
- Daily Reader 与 `pipeline_runs`（与旧 Learning Workflow 已解耦，不进入本轮 runtime conversion）。
- Dictionary、Vocabulary（词典查询与生词本主链）。

### 保留的产品合同（cutover 后继续生效）

- `message.completed` 与 typed terminal events。
- Citation / provenance（Ask evidence/citation 必须可回源到 Reading Record / Stable Document / Anchor Segment）。
- Stable Document / Anchor Segment 作为新链事实源。
- `anchor_segment_id` 是权威锚点；`sentence_id` 仅作兼容 alias，不出现在对外 DTO / persistence。
- Ask 是侧边助手，不是 orchestration 控制面；Ask supplement 必须标记来源，不伪装成系统层。

## Post-cutover backlog（不在本文展开任务细节）

以下事项已登记为 post-cutover backlog，由后续任务单独推进；不在本文写成已完成：

- 旧 Eval 表与 legacy `analysis_*` 表清理（DATA-AUDIT）。范围见上方「旧 `analysis_*` 表的精确状态」小节：4 张 legacy 孤儿表可直接 DROP；3 张 legacy 仍被只读引用表需先迁移 `user_assets/records.py`、`text_anchors.py`、`quota/ledger.py` 引用；`analysis_windows` 与 `layer_analysis_plans` 必须保护。
- Console / Eval 按新 orchestration 重建（治理化控制面）。
- 统一监测、计费适配、usage/ledger 与新 Reader run/job/layer attribution 闭环。
- Test Governance 与代码架构优化（TEST-GOVERNANCE、ARCH-OPT-AUDIT）。

## 历史过程证据（仅供回看，不作为当前事实来源）

### DOC-R2 代码现场核验（2026-07-13，历史）

DOC-R2 阶段曾对当时路由矩阵中以下文件 / route 的存在性做现场核验，结果如下。cutover 落地后这些核验对象的"仍存在"项也已物理删除；该表只保留作历史证据，不再代表当前状态：

| 引用 | 矩阵行 | DOC-R2 核验结果 | 当前状态 |
|---|---|---|---|
| `ReadingRecordCommandGroup.tsx` | Command palette 新阅读记录分组 | ❌ 当时已删除 | 已删除（历史一致） |
| `active-analysis-task-indicator.tsx` | Active analysis task indicator | ❌ 当时已删除 | 已删除（历史一致） |
| `/api/web/command-palette/records` | 推荐 W3-D4 最小实现切片 | ❌ 当时已删除 | 已删除（历史一致） |
| `/api/web/analysis/*` | Active analysis task indicator | ✅ 当时仍存在 | cutover 后已物理删除 |
| `reading-record-activity-indicator.tsx` | Reading Record activity indicator | ✅ 当时仍存在 | cutover 后随旧 route 一起退役 |
| `analysis-task-client.ts` | Active analysis task indicator | ✅ 当时仍存在 | cutover 后已物理删除 |

DOC-R2 不修改代码、不重写矩阵行，仅在原行后追加「已删除」标注。Cutover 落地后该边界已无回退路径，但 DOC-R2 的"代码事实优先于 TMP verdict"原则继续生效。

### 历史 cutover 推荐流程

cutover 前的推荐流程如下，目前已完整执行完毕：

```text
service stop / parsing disabled
-> rewrite learning AI Workflow to Reader orchestration
-> reset schema baseline, preserve dictionary tables
-> update Web Reader UI to new Reading Record API
-> validate text parsing vertical slice
-> add Candidate Document / RAG / advanced layers
-> adapt or remove remaining old consumers
-> delete old workflow code and tables
```

### 历史 W3 cutover phases

cutover 推进期间的 W3 阶段已全部完成；下列阶段表只保留作历史证据，不再代表当前任务：

| Phase | 内容 | 历史状态 |
|---|---|---|
| W3-A | 文档与 guard 保持 | ✅ 完成 |
| W3-B | Route helper split（legacyAppReaderRoute / appReadingRecordRoute / appReaderPlateRoute） | ✅ 完成 |
| W3-C1/C3 | 新增 `/app/reader-record/{recordId}` product route + Workbench-backed read-only surface | ✅ 完成（cutover 后被 `/app/reader/[recordId]` 取代） |
| W3-D0 | 拆清 legacy / new submit BFF contract | ✅ 完成 |
| W3-D1 | `/app/read` 默认 submit 切到新 Reading Record | ✅ 完成 |
| W3-D2 | Web-only 最近 Reading Record 恢复入口（localStorage） | ✅ 完成 |
| W3-D3 | Static guard tests 锁定入口来源矩阵 | ✅ 完成 |
| W3-D4 | New Reading Record list source BFF（`GET /reader/records`） | ✅ 完成 |
| W3-D5 | Library 新 Reading Record section | ✅ 完成 |
| W3-D6 | Command palette 新 Reading Record 分组 | ✅ 完成 |
| W3-D7 | Reading Record activity indicator | ✅ 完成 |
| W3-D8 | Activity indicator route gating | ✅ 完成 |
| W3-D9 | Vocabulary source links legacy 边界 guard | ✅ 完成 |

### 历史 D6-A0 Ask / Notes / Highlights Dependency Audit（2026-07，历史）

D6-A0 是 cutover 前对 Ask / notes / highlights / user asset 写入入口的迁移边界审计。cutover 落地后，所列 legacy 入口（`/app/reader/{recordId}` 内 `AiWorkspacePanel`、`ReaderNotePanel`、`SelectionToolbar` + `/api/web/reader-notes` / `/api/web/reader-annotations` 旧分支、`reader_scene.py` 作为 authoritative service 等）已物理删除。完整依赖矩阵见 `modules/schema-and-domain-contract.md` 的 `D6-A0 Ask / Notes / Highlights Dependency Audit` 子节；本节不复制。

历史 D6 子阶段结论摘要（仅供回看）：

- D6-A1：`/app/reader-record/{recordId}` 内 selection 可投影为新 Reading Record anchor draft，但 UI 写入口仍 disabled。
- D6-A3：Ask `save_note` / `save_highlight` proposal 可携带 `UserEditorialAssetAnchor`，但只生成 proposal 不写 DB。
- D6-A6：新增 FastAPI `/reader/records/{reading_record_id}/ask/*` route，只做 snapshot/anchor 校验 + typed pending。
- D6-A5：`user_annotations` / `reader_notes` 新增 optional `anchor` 分支，gate 成功后返回 409 deferred（D6-U4 已推进到真实持久化）。
- D6-U2：single-range first 策略；`multi_text` 后续走 `UserEditorialAssetAnchorSet`。
- D6-U3：V1c 扩展 legacy `user_annotations` / `reader_notes` 表，不新增统一 `user_editorial_assets` 表。
- D6-U4：migration `0002_reader_record_anchor_columns.sql` 已落地，runtime 写入真实持久化，`analysis_record_id = NULL`，不调用 `load_render_scene`。
- UI-D6C：ReaderRecordPlateSurface 完成 UI/UX polish；详细结论见 `modules/reader-record-plate-surface-ui.md`。

## Daily Reader 边界

`daily_reader_workflow` 不进入本轮 runtime conversion。Cutover 后 Daily Reader 与旧 Learning Workflow 已解耦，但 `daily_reader_workflow` 仍保持固定 workflow 形态，不混入 learning Reader orchestration。如果后续数据清理影响 Daily Reader，需要先标记为 keep 或重写 Daily Reader 依赖，不允许把 Daily Reader 语义混入 learning Reader orchestration。

## 旧实现复用经验（历史，仅供回看）

cutover 前曾审计旧实现的局部复用价值。cutover 落地后这些复用经验已沉淀到新链实现或归档；本节保留作历史证据，不代表当前仍需复用：

- `input_preparation` 的语言检测、标题/文本规范化经验。
- `app/contracts/annotation.py` 的 UTF-16 offset 和 `fnv1a32-utf16` hash。
- `text_anchors.py` 的 anchor validation 思路。
- `analysis/task_executor.py` 的 DB claim、heartbeat、stale recovery 经验。
- `ai_usage_events` 的 usage audit 基础。
- Ask Claread 的"用户确认后写资产"产品约束。
- `user_annotations` / `reader_notes` 的 anchor 和 `target_key` 思路。
- `reader_ask_supplements` 的来源标记思路。

不复用（cutover 后已物理删除，不再保留为产品形态）：

- `learning_workflow.py` 固定全量 graph 作为产品生命周期。
- `analysis_tasks` 的 coarse active task 语义。
- `analysis_results.render_scene_json` 作为事实源。
- `task succeeded` 作为文章可读或 parsed 的判断。
- 固定批注数量作为 parsed 门槛。

## 不允许事项（cutover 后继续生效）

- 不做旧 `render_scene_json` 到新 snapshot / Plate path 的兼容映射。
- 不把旧 `analysis_tasks` / `RecordResponseDto.id` / `cloud_record_id` 直接当新 `Reading Record.record_id` 使用。
- 不通过"保留旧 route、换内部数据源"的方式静默绕过缺失的 Ask / dictionary / notes / highlights 能力。
- 不在正式文档中把已删除的旧 URL、旧页面、旧 worker、旧 reset 命令描述为当前入口或当前能力；旧 token 只允许出现在历史段落、archive 或负向 404 断言中。
- 不在正式文档中把 Operational Readiness（计费、统一监测、Console/Eval 重建等）写成已完成。
