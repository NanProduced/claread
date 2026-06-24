# Cutover 与旧 AI Workflow 处理

> 状态：`D6-U4 V1c single-range persistence 已完成；UI-D6C Plate surface polish 已完成；新旧双轨收敛待推进`
> 最后更新：2026-06-24
> 范围：停服重构、旧 workflow 替换、旧表/旧 UI 清理边界，以及 Web cutover 迁移顺序。

## 基本立场

Claread 尚未上线，本轮不要求旧开发数据兼容或迁移。

允许：

- 重构期间解析功能不可用。
- 停止服务后重构 backend。
- Web Reader UI 跟随新 contract 改写。
- 新流程验证走通后，再做其他适配。
- 最后移除旧 AI Workflow 代码和旧表。

不做：

- 旧 `render_scene_json` 到新 Reader projection 的兼容映射。
- 旧 `analysis_tasks` 到新 `reader_runs` 的数据迁移。
- 为旧 Web Reader contract 保持双轨长期兼容。

## 推荐流程

```text
service stop / parsing disabled
-> rewrite learning AI Workflow to Reader orchestration
-> reset schema baseline, preserve dictionary tables
-> update Web Reader UI to new Reading Record API
-> validate text parsing vertical slice
-> add Candidate Base / RAG / advanced layers
-> adapt or remove remaining old consumers
-> delete old workflow code and tables
```

## 必须保护

数据库重置时必须保护：

- `dict_entries`
- `dict_lookup_targets`
- `dict_redirects`

D3 schema reset 不要求迁移旧开发数据，但不能把仍保留的产品能力误删。除词典三表外，以下能力必须在删除旧 workflow 前有明确处理：

- Daily Reader 和 `pipeline_runs`：本轮不做 runtime conversion，不能被 learning reset 隐式删除。
- Ask conversation / Ask Supplement：重写到新 Reading Record 和 document tools，不再 merge 到旧 render scene。
- User Editorial Assets：用户高亮、笔记、收藏、词汇本 source refs 需要新 record/anchor 写入路径。
- Usage audit / credit ledger：新增 reader run/job/layer attribution，ledger 不再依赖旧 task join 才能显示标题。
- Feedback / dictionary AI candidates：旧 `analysis_record_id` 依赖需要重写、置空或显式归档。
- Directus / Eval Center 观察面：旧 parse-run、render scene inspector 和 workflow lab 必须隐藏、禁用或重写。

## 旧实现复用

可复用：

- `input_preparation` 的语言检测、标题/文本规范化经验。
- `app/contracts/annotation.py` 的 UTF-16 offset 和 `fnv1a32-utf16` hash。
- `text_anchors.py` 的 anchor validation 思路。
- `analysis/task_executor.py` 的 DB claim、heartbeat、stale recovery 经验。
- `ai_usage_events` 的 usage audit 基础。
- Ask Claread 的“用户确认后写资产”产品约束。
- `user_annotations` / `reader_notes` 的 anchor 和 `target_key` 思路。
- `reader_ask_supplements` 的来源标记思路；新实现必须继续区分 Ask Supplement 与系统批注层。

不复用：

- `learning_workflow.py` 固定全量 graph 作为产品生命周期。
- `analysis_tasks` 的 coarse active task 语义。
- `analysis_results.render_scene_json` 作为事实源。
- `task succeeded` 作为文章可读或 parsed 的判断。
- 固定批注数量作为 parsed 门槛。

## 旧依赖审计

虽然不做兼容迁移，删除旧代码前仍需做依赖审计，防止误删词典、Daily Reader 或 usage audit。

审计对象至少包括：

- `analysis_records`
- `analysis_tasks`
- `analysis_results`
- `analysis_task_events`
- `analysis_debug_snapshots`
- `analysis_overview_tasks`
- `analysis_overview_task_events`
- `ai_usage_events`
- `reader_ask_threads`
- `reader_ask_turn_runs`
- `reader_ask_supplements`
- `user_annotations`
- `reader_notes`
- `favorite_records`
- `vocabulary_book`

`user_annotations`、`reader_notes` 和未来 User Editorial Assets 不应被 Enhancement Layer retry 或 System Annotation Layer replacement 误删。

处理方式只需标记：

- delete
- rewrite to Reading Record
- keep for Daily Reader / unrelated feature
- keep until later cleanup

不需要为旧数据编写迁移脚本。

删除旧 learning workflow 的 blocking gates：

- Web submit 不再依赖 `/analysis-tasks` 作为唯一入口。
- Web Reader 不再依赖 `/reader/records/{id}/scene` 或 `analysis_results.render_scene_json`。
- Ask context loader 不再读取旧 render scene / page state 作为事实源。
- user annotations / reader notes 的 anchor validation 已改为 Stable Base / Reading Units / Anchor Segments。
- usage、ledger、feedback、vocabulary source refs 有新 Reading Record attribution。
- reset script 明确保留词典三表，并明确 Daily Reader、user assets、Ask、usage/ledger 的保留或清空策略。
- Directus/Eval 旧视图已禁用、隐藏或改为新 artifact contract。

## Web Reader 切换

Web Reader 不再依赖旧 `render_scene_json`。Reader Article Body 的新路径是 Base Plate Snapshot + Projection Operations。

允许短期复用旧前端投影代码中的局部算法，例如 UTF-16 range validation、diagnostics 和 selection bridge。禁止把旧 `render_scene_json` 作为新 D4 API contract 或后端 truth。

新 UI 直接消费：

- `ReaderRecordSnapshot`
- `ReadingUnits`
- `layers_by_unit`
- `actions`
- `coverage`
- `reader_events`

旧 reader adapters、旧 scene inspector、旧 Directus inspector 可以在重构期间失效，后续按需要重写。

## D5-W3 当前 Web 路由矩阵

当前 Web 入口仍处于双轨状态，且旧 record id / 新 Reading Record id 不能混用：

| Surface | 当前入口 / route | 当前 source of truth | 当前 id 语义 | 代码落点 / 说明 |
|---|---|---|---|---|
| 新提交产品入口 | `/app/read` | 默认 `/api/web/reading-record/submit` -> 新 `/reader/records/plain-text`；legacy mode 仍可回退到 `/api/web/analysis/submit` | 默认新 `Reading Record.record_id`；legacy mode 使用旧 `cloud_record_id` | `AnalyzeSubmitForm.tsx`、`submit-mode.ts`、`recent-reading-record.ts`、`services/bff/reader-plate.ts`；W3-D1 起默认成功后跳 `/app/reader-record/{readingRecordId}`，W3-D2 起提供 Web-only 最近 Reading Record 继续入口 |
| 新 Reader Plate 验证页 | `/app/reader-plate` + `?record_id=` | `/api/web/reader-plate/*` -> 新 `/reader/records/plain-text|snapshot|events` | 新 `Reading Record.record_id` | `reader-plate/page.tsx`；当前是 read-only validation surface，不是最终产品 UI |
| 新 Reading Record 产品 route shell | `/app/reader-record/{recordId}` | `/api/web/reader-plate/{recordId}/snapshot` | 新 `Reading Record.record_id` | `reader-record/[recordId]/page.tsx`；W3-D1 起承接 `/app/read` 成功 landing；D6-E3 起 `local-real-chain-runbook.md` 固化 `/app/read -> /api/web/reading-record/submit -> /app/reader-record/{recordId}` 的本地真实链路验证 checklist；UI-D5 起 loaded state 默认渲染 `ReaderRecordPlateSurface` read-only，用 `ReaderPlateSnapshotDto -> ReaderRecordPlateDocument` 直接 projection，并保留 `reader-record-surface-mode.ts` 受控 fallback 到 Workbench；UI-D6A 已锁定默认 mode 是 Plate、Workbench fallback 需显式选择、progressive loading 使用 compact chip / slim strip 且不替换正文；Ask / notes / highlights / feedback 写入口仍未启用；W3-D5/D6/D7 起可由 Library 新 section、command palette 新分组和新 Reading Record indicator 发现；旧 active analysis task 流量仍未切入 |
| 旧 Reader 产品页 | `/app/reader/{recordId}` | `getReaderRecord()` -> 旧 `/reader/records/{id}/scene` 或 `by-client-id/.../scene` | 旧 analysis record id 或 client record id | `reader/[recordId]/page.tsx`、`services/bff/reader.ts`、`services/api/reader-scene.ts`；仍承载 ReaderWorkbench、Ask、点词、笔记、高亮 |
| Library record links | `legacyAppReaderRoute(record.id)` | `/records` -> `RecordResponseDto[]` | 旧 `RecordResponseDto.id` | `LibraryClient.tsx`、`services/bff/records.ts`；Library 当前拿到的是旧 record list，不是新 Reading Record list |
| Vocabulary source links | `legacyAppReaderRoute(recordId)` / `legacyAppReaderRoute(item.sourceRecordId)` | vocabulary item source refs -> 旧 source record contract | 旧 source record id / `cloud_record_id` / `client_record_id` | `app/vocabulary/VocabularyClient.tsx`；点回原文仍跳旧 ReaderWorkbench；W3-D9 已用 guard 锁定不能把 `sourceRecordId` 当新 `Reading Record.record_id`，后续必须等 BFF 提供 `sourceReadingRecordId` 或 `sourceReaderUrl` |
| Command palette 最近记录 | `legacyAppReaderRoute(record.id)` / `legacyAppReaderRoute(lastRecordId)` | recent/search record list | 旧 record id | `CommandPaletteDialog.tsx`、`command-palette-items.ts` |
| Command palette 新阅读记录分组 | BFF 返回的 `readerUrl` | 新 `Reading Record.record_id`，但前端只消费 `readerUrl` | `/api/web/reading-records` -> `/app/reader-record/{recordId}` | `ReadingRecordCommandGroup.tsx`；W3-D6 起独立新增，不替换旧 command palette recent/search records |
| Active analysis task indicator | toast action -> `legacyAppReaderRoute(recordId)` | `/api/web/analysis/current` + `/api/web/analysis/tasks/{taskId}` | 旧 `cloud_record_id` | `active-analysis-task-indicator.tsx`、`analysis-task-client.ts` |
| Reading Record activity indicator | BFF 返回的 `readerUrl` | 新 `Reading Record.record_id`，但前端只消费 `readerUrl` | `/api/web/reading-records` -> `/app/reader-record/{recordId}` | `reading-record-activity-indicator.tsx`；W3-D7 起独立新增，并列于旧 active indicator；W3-D8 起在 `/app/reader-record/*`、`/app/reader-plate*`、`/app/read` 隐藏且不请求列表，其他 app shell 页面展示 |
| `services/bff/analysis.ts` `readerUrl` | `legacyAppReaderRoute(recordId)` | 旧 analysis task submit/status projection | 旧 `cloud_record_id` | 是当前 cutover 最显式的旧产品路径投射点 |
| App shell route heuristics | `pathname.startsWith("/app/reader/") || pathname === "/app/read"` | 纯前端 route heuristic | 无 | `components/layout/app-shell/index.tsx`；未来新产品 route 进入后也要同步调整 sidebar collapse / active-task hiding 逻辑 |

## D5-W3 当前数据对象关系

当前旧 / 新对象边界如下：

- 旧产品面：
  - `TaskSubmitResponseDto` / `TaskStatusResponseDto`
  - `RecordResponseDto`
  - `ReaderSceneResponseDto`
  - `analysis_results.render_scene_json`
- 新 Reader orchestration 面：
  - `Reading Record`
  - `Stable Reading Base`
  - `ReaderPlateSnapshot`
  - `reader_events`

当前 Web 代码的现实情况：

- `/app/read` 默认 submit 已围绕新 `Reading Record.record_id -> /app/reader-record/{recordId}` 工作；W3-D2 增加的 `claread:web:recent-reading-record` localStorage 只保存最近一次新 Reading Record 的最小恢复字段，不是长期事实源；Library 新 Reading Record section、command palette 新分组和 Reading Record activity indicator 已经可发现新 Reading Record；W3-D8 起 indicator 不覆盖 `/app/read`，因为 read 页已有 recent recovery；旧 active task、Vocabulary source links、command palette legacy recent/search records 仍主要围绕旧 `analysis task / source record id -> /app/reader/{recordId}` 工作。
- `/app/reader/{recordId}` 仍走旧 scene adapter，把 `ReaderSceneResponseDto` 适配成 ReaderWorkbench VM。
- `/app/reader-plate` 独立消费新 `ReaderPlateSnapshot`，其 `record_id` 是新 Reading Record id，不应回灌给旧 `/app/reader/{recordId}` helper。
- `/app/reader-record/{recordId}` 现在提供新的 Reading Record product route；UI-D6A 验收后默认 surface mode 保持为 `plate`，loaded state 渲染 `ReaderRecordPlateSurface` read-only，直接消费 `ReaderPlateSnapshotDto` 并投影 stable source、unit translation、system marks/cues、user asset read projection 和 compact progress。
- Workbench-backed surface 仍可受控回退：设置 `NEXT_PUBLIC_READER_RECORD_SURFACE_MODE=workbench`，或在浏览器 localStorage 写入 `claread:reader-record-surface-mode=workbench`。该 fallback 用于比较/排查，不是默认产品 surface。
- UI-D5 继承原有 events polling / snapshot reload：`layer_published`、`record_product_state_updated`、`projection_reset_required` 触发后重新拉取 snapshot，并由 Plate surface 重新 projection。
- UI-D6A characterization tests 锁定默认 Plate 路径的渐进状态：`processing`、`readable_enhancing`、`failed`、`action_required` 都用轻量 progress chip / strip 表达，不显示旧 Workbench 状态卡，也不替换正文。
- UI-D4 阅读态打磨只改 `ReaderRecordPlateSurface` 的前端呈现：reading header、selection-context action strip、低干扰 cue marker / compact panel、辅助译文样式；不改 `/app/reader-record/{recordId}` 默认 route，不改后端 API，不启用 Ask 或 Feedback。
- UI-D6B 对默认 Plate surface 做端到端真实流程验收与 UI/UX 收敛评估：对比旧 `ReaderWorkbench.tsx` 基线，修复 `ReaderRecordPlateSurface.tsx` 中英文混排状态文案错误（操作按钮、写入状态、复制状态、section 标签、状态文本、词典面板、按钮标签、aria-label、disabledReason 全部收敛为中文），新增 smoke guard 防止英文 UI 文案回归；三进程真实联调验证 `/app/read -> /api/web/reading-record/submit -> /app/reader-record/{recordId}` 端到端流程（真实 record_id `a6621b52-3f51-49b0-9a5f-f630a6c5818a`），4 个增强层全部 succeeded，highlight/note 写入通过 Web BFF 成功持久化并出现在 snapshot `user_assets` projection（`analysis_record_id: null`，未映射到 legacy），修复文章标题缺失（`CompactProgress` 新增 `title` prop 渲染 `<h1>`）；未接旧 `/scene`、`render_scene_json`、`analysis-tasks`；未修复但记录的后续 UI/UX 项见 `reader-record-plate-surface-ui.md` UI-D6B 节。
- D6-E3 起本地真实链路验证入口收口为 `/app/read` 默认提交到 `/api/web/reading-record/submit`，成功 landing 到 `/app/reader-record/{recordId}` 后用 events polling / snapshot reload 观察增强内容；诊断以 `reader_jobs`、`reader_events`、`enhancement_layers` 和 snapshot `enhancement_progress` 为准，worker 仍是独立进程，不进入 FastAPI lifespan。
- Ask、Feedback 仍未接通；Plate mode 下 Ask / Feedback 保持 disabled / coming soon，不调用 `/api/web/reader-ask`、`/api/web/reader-notes` 或 `/api/web/reader-annotations`。Highlight / Note 仅在 D6-U7 已支持的 stable-source single-range selection 上走新 `/api/web/reading-record/highlights` / `/api/web/reading-record/notes`，多段选区、非 stable-source selection 和 `multi_text` 仍 disabled。
- Library 当前列表来自旧 `/records`；即使页面本身不直接渲染 `render_scene_json`，它拿到的数据对象仍属于旧 record contract。

UI / UX 方向：

- W3-C1 的 `/app/reader-record/{recordId}` 是新 Reading Record 的产品 route shell；UI-D6A 起默认中心 surface 已作为 read-only `ReaderRecordPlateSurface` 的真实流程验收对象，但写入口仍保持关闭。
- Legacy `/app/reader/{recordId}` 继续保留 ReaderWorkbench、Ask、点词、笔记、高亮和浮层能力，直到对应新 Reading Record contract 和 UI 切线完成。
- 后续产品化改线的核心是补齐 `unit / anchor_segment / text_range` 到 Plate selection、查词、笔记、Ask 附件和跳转桥的兼容层；不能把写入口提前接回旧 render scene。
- `/app/reader-plate` 仍是验证入口；不要把它的单列 read-only surface 当作最终产品页视觉基线。

因此，D5-W3 不允许：

- 把旧 `record.id` / `cloud_record_id` 当成新 `Reading Record.record_id` 直接跳转。
- 为了 cutover 把旧 `render_scene_json` 或 `/scene` 映射成新 snapshot 路径。
- 把 `/app/reader-plate` 当前 read-only validation surface 伪装成已完成产品页。

## 推荐 Cutover Phases

各阶段详细状态见 `implementation-plan.md` D6 子阶段索引表。当前 cutover 边界结论：

| Phase | 内容 | 状态 |
|---|---|---|
| W3-A | 文档与 guard 保持 | ✅ 完成 |
| W3-B | Route helper split（legacyAppReaderRoute / appReadingRecordRoute / appReaderPlateRoute） | ✅ 完成 |
| W3-C1/C3 | 新增 `/app/reader-record/{recordId}` product route + Workbench-backed read-only surface | ✅ 完成 |
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

## 当前下一步建议

W3-D1-D9 已完成 `/app/read` submit landing、Web-only 最近记录恢复、Reading Record list source、Library 新 section、command palette 新分组、Reading Record activity indicator 及其 route gating，并收口 Vocabulary source links 的 legacy route-source 边界；W3-D3 起用 static guard tests 锁定当前入口来源矩阵，后续不要一次性迁所有 consumer：

- 先观察 `/app/read -> /app/reader-record/{recordId}` 的新 Reading Record landing 稳定性。
- 继续把 recent localStorage 当临时恢复入口；正式最近记录列表应等 new Reading Record Library source 单独实现。
- 再逐个评估 active task、Vocabulary source links，以及是否要替换 command palette legacy recent/search records；只有能区分旧/new record id 后再改线。
- 不做旧 `render_scene_json` / `/scene` 到新 snapshot path 的兼容映射。
- 迁移某个入口时，必须同步更新 `apps/web/src/lib/entry-source-matrix.test.ts` 和本矩阵表，避免 guard 与实际代码漂移。

这样可以把第一次真实产品 cutover 限制在 submit 入口，先验证新 product route 的记录 id、BFF 和页面装配，再决定是否逐步迁移其他旧入口。

### 推荐 W3-D4 最小实现切片

当前最安全的下一步是**先实现 new Reading Record list source**，而不是先改 command palette recent record。理由：

1. command palette、Library、active task 当前都依赖旧 `/records` 或 `/api/web/command-palette/records` 返回的旧 record id。在没有 new Reading Record list source 之前，这些入口即使改了 route helper 也拿不到新 `Reading Record.record_id`，改线没有意义。
2. new Reading Record list source 实现后，command palette recent / search 和 Library 才有可消费的新 id 数据面；届时再逐个入口改线，每次只改一个 surface 并同步更新 static guard。
3. active task 和 Vocabulary source links 的 id 来源更深（active task 来自 `/api/web/analysis/*`，Vocabulary source refs 来自 vocabulary item `sourceRecordId`），应等 new Reading Record list source 稳定后再单独评估。

W3-D4 最小切片建议范围：

- 新增 `services/bff/reading-records.ts`（或等价 BFF），调用后端 `GET /reader/records`（或等价 list endpoint）返回新 `Reading Record.record_id` 列表。
- 不改 Library / command palette / active task / Vocabulary 运行时逻辑；只新增 BFF 和对应 focused tests。
- 不新增 public Library UI 切换；先让 BFF contract 可用，再在 W3-D5+ 逐个入口改线。
- 改线某个入口时，同步更新 `entry-source-matrix.test.ts` 的对应 guard 和本矩阵表。

## 不允许事项

- 不做旧 `render_scene_json` 到新 snapshot / Plate path 的兼容映射。
- 不把旧 `analysis_tasks` / `RecordResponseDto.id` / `cloud_record_id` 直接当新 `Reading Record.record_id` 使用。
- 不把 `/app/reader-plate` 当前 validation surface 当最终产品 UI 交付。
- 不在 cutover planning 阶段修改后端 Reader orchestration worker。
- 不通过“保留旧 route、换内部数据源”的方式静默绕过缺失的 Ask / dictionary / notes / highlights 能力。

## Daily Reader 边界

`daily_reader_workflow` 不进入本轮 runtime conversion。

如果旧表删除影响 Daily Reader，需要先标记为 keep 或重写 Daily Reader 依赖，不允许把 Daily Reader 语义混入 learning Reader orchestration。

## D6-A0 Ask / Notes / Highlights Dependency Audit 结论

> 本节是 D6 product hardening 进入 Ask / notes / highlights / user asset 写入前的迁移边界结论。完整依赖矩阵、最小分层、最小实现顺序、暂不切能力与原因已写入 `modules/schema-and-domain-contract.md` 的 `D6-A0 Ask / Notes / Highlights Dependency Audit` 子节；本节只收口 cutover 视角的结论与未切入口状态。

### 当前 Ask / notes / highlights / user asset 写入入口状态

| 入口 | 当前 route / surface | 当前数据源 / id 语义 | 当前 status | D6-A0 切线结论 |
|---|---|---|---|---|
| Ask Claread | `/app/reader/{recordId}` 内 `AiWorkspacePanel` + `ask-chat/*` | 旧 `analysis_record_id` + `target_key` + `render_scene_json`；`reader_ask_threads` / `reader_ask_supplements` 表 | legacy（runtime 不变） | D6-A1 read-only 接入 anchor_segment_id；D6-A3 已完成 write-proposal anchor contract（不写 DB、不启用 UI）；D6-A4 切 supplement 写；D6-A6 切 Web route；UI 切线必须等 Plate Surface |
| Reading Record Ask probe | FastAPI `/reader/records/{reading_record_id}/ask/messages` + `/reader/records/{reading_record_id}/ask/actions/{action_id}/confirm` | 新 `reading_record_id` + optional `anchor: UserEditorialAssetAnchor`；facts 只来自 Reading Record snapshot / anchor gate | backend 最小切片，execution disabled | D6-A6 当前只做 route contract + typed pending；不创建 legacy ask thread / turn run / supplement，不新增 Web BFF，不启用 `/app/reader-record/{recordId}` UI |
| Reader notes | `/app/reader/{recordId}` 内 `ReaderNotePanel` + `/api/web/reader-notes` | 旧 `analysis_record_id` + `anchor_sentence_id` + `target_key`；`reader_notes` 表 | legacy（runtime 不变） | D6-A5 双轨：request body 引入 `UserEditorialAssetAnchor` 可选 anchor，旧 `target_key` deprecated optional |
| Reader highlights | `/app/reader/{recordId}` 内 `SelectionToolbar` + `AnnotationGutter` + `/api/web/reader-annotations` | 旧 `analysis_record_id` + `sentence_id` / `target_key`；`user_annotations` 表 | legacy（runtime 不变） | D6-A5 双轨：与 notes 同样引入 anchor_segment_id 可选 anchor |
| Ask action confirm (`save_note` / `save_highlight`) | `/api/web/reader-ask/threads/{threadId}/actions/{actionId}/confirm` | 旧 `analysis_record_id` + `record_id` 两种，按 action 类型 | legacy（runtime 不变） | 旧 confirm 路径保留；D6-A6 的 Reading Record confirm 另走 `/reader/records/{reading_record_id}/ask/actions/{action_id}/confirm`，当前稳定返回 `pending`，不回退 legacy `confirm_action` |
| Selection → Ask attachment | `apps/web/src/lib/reader-plate/bridges/ask/adapters.ts` + `primitives/selection-targets.ts` | 旧 `targetKey` / `sentence_id` | legacy（runtime 不变） | D6-A1 read-only 投影新 anchor；D6-A6 与新 BFF / route 同步切换 |
| Dictionary / user asset 写入 | `/app/reader/{recordId}` 内 `DictionaryPopover` + 旧 asset 写入 | 旧 `analysis_record_id` | legacy（runtime 不变） | 不在 D6-A0 范围；`/app/reader-record/{recordId}` 已 read-only dictionary lookup 已恢复 |
| Ask cross-record citation (`known_reference_resolver`) | `services/api/app/services/reader_ask/known_reference_resolver.py` | 旧 `render_scene` dict | legacy（runtime 不变） | D6-A0 暂不切；等 candidate base / RAG substrate 决策 |
| `reader_scene.py` 作为 authoritative service | `/app/reader/{recordId}` 主路径 | 旧 `client_record_id` / UUID + `render_scene_json` | legacy（runtime 不变） | D6 不替换；`merge_record_with_reader_ask_supplements` 在 D6-A4 后不再承担 Ask supplement 写入 |
| `daily_vocab_agent.py` | 旧 daily vocab path | 旧 `paragraph_id` | legacy，不在 D6 切线范围 | daily_reader_workflow 不进入 runtime conversion |

### Cutover 边界声明

- Ask / notes / highlights / user asset 的**用户可见写入入口**仍由 `/app/reader/{recordId}` 承载；本轮不切 `/app/reader-record/{recordId}` 的 UI 写入路径。唯一新增的是 D6-A6 FastAPI-only Reading Record Ask probe route，它只做 snapshot/anchor 校验 + typed pending，不构成 UI enablement。
- `/app/reader-record/{recordId}` 的 Ask / notes / highlights UI 切线**必须等 Plate Surface 视觉方案**落地；本轮不做。
- UI-D5 已把 `/app/reader-record/{recordId}` 默认 surface mode 切到新的 Plate read-only surface；这是读投影切换，不是写入口切换。Ask / notes / highlights 仍未启用。
- 旧 `reader_ask.service` / `user_annotations` / `reader_notes` / `reader_scene` runtime 行为**完全保持不变**；D6-A0 不引入兼容性修改、不引入字段别名、不引入双轨长期兼容。
- D6-A1 起新写入切线以 `schema-and-domain-contract.md` 的 `D6-A0 Ask / Notes / Highlights Dependency Audit` 子节为起点；D6-A0 不在本轮做实现。
- 旧 `reader_ask_threads` / `reader_ask_supplements` / `user_annotations` / `reader_notes` 表**保留至旧 data 清空**；cutover 不在本轮删表。
- D6-A0 静态 guard 已落地：Web `apps/web/src/lib/reader-record-boundary.test.ts` 锁定 `/app/reader-record/{recordId}` 不引用 legacy route / scene / write route / write surface，API `services/api/tests/test_d6_a0_static_boundary.py` 锁定 `user_editorial_assets` schema-only、`reader_orchestration` 不 import `reader_ask`、新 Reader Record 路径不读 `render_scene_json` 作为 fact source。

### D6 子阶段结论摘要

各子阶段详细状态见 `implementation-plan.md` D6 子阶段索引表。关键 cutover 边界结论：

- D6-A1：`/app/reader-record/{recordId}` 内 selection 可投影为新 Reading Record anchor draft，但 UI 写入口仍 disabled。
- D6-A3：Ask `save_note` / `save_highlight` proposal 可携带 `UserEditorialAssetAnchor`，但只生成 proposal 不写 DB。
- D6-A6：新增 FastAPI `/reader/records/{reading_record_id}/ask/*` route，只做 snapshot/anchor 校验 + typed pending，不启用 Web UI。
- D6-A5：`user_annotations` / `reader_notes` 新增 optional `anchor` 分支，gate 成功后返回 409 deferred（D6-U4 已推进到真实持久化）。
- D6-U2：single-range first 策略；`multi_text` 后续走 `UserEditorialAssetAnchorSet`。
- D6-U3：V1c 扩展 legacy `user_annotations` / `reader_notes` 表，不新增统一 `user_editorial_assets` 表。
- D6-U4：migration `0002_reader_record_anchor_columns.sql` 已落地，runtime 写入真实持久化，`analysis_record_id = NULL`，不调用 `load_render_scene`。

### 暂不切的旧能力与原因（cutover 视角）

- `reader_ask_threads` 主键不重写：跨 Reading Record 的 Ask thread 合并 / 迁移策略未确定。
- `reader_scene.py` 不被替换为新 service：直到 Plate Surface 决定 `/app/reader-record/{recordId}` 与 `/app/reader/{recordId}` 是否合并，两个 service 并存。
- `daily_vocab_agent.py` 不切：与 Daily Reader 边界对齐，本轮 daily_reader_workflow 不进入 runtime conversion。
- `services/api/app/schemas/{user_annotations,reader_notes,reader_ask,reader_scene,analysis}.py` 旧 DTO 字段不直接删除：D6 schema 演进必须保留 deprecated optional 字段，避免破坏 library / command palette / Vocabulary source links 等 legacy consumer。
- Ask cross-record citation (`known_reference_resolver`) 不切：依赖 candidate base / RAG substrate，不属于 D6 product hardening 主路径。
- 旧 Directus / Eval 观察面不切：观察面切换属于隔离 spike，不在 D6-A0 cutover 范围。
- `@target_sentence_id` 在 agent tool 内部允许保留为 alias，但禁止出现在对外 DTO / persistence；这是为避免一次大改引入回归。

### UI-D6C UI/UX polish 结论

ReaderRecordPlateSurface 已完成小步 UI/UX polish（inspector 文案、translation block 样式、write state 自动清除、面板 accent border）。仍 deliberately deferred：阅读模式切换器、词典 rail、Ask rail、floating toolbar、收藏按钮、阅读设置、cue 折叠。详细结论见 `modules/reader-record-plate-surface-ui.md`。

## D2 / D3 要求

D2 前：

- 完成旧依赖矩阵。
- 确认 schema reset 脚本如何保留词典三表。

D3：

- 新 schema baseline 以 Reading Record 为中心。
- learning Web path 不再走旧 `/analysis-tasks`。
- 旧路径可以被 feature flag 禁用或直接移除。
- Academic workflow 保持暂缓，需要 feature flag 或隔离，不能因删除 learning workflow 旧模块而意外破坏。
