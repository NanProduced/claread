# Cutover 与旧 AI Workflow 处理

> 状态：`D6-U4 V1c single-range persistence` + `UI-D3 Reader Record Plate Surface read-only scaffold`
> 最后更新：2026-06-24
> 范围：停服重构、旧 workflow 替换、旧表/旧 UI 清理边界，以及 Web cutover 迁移顺序；本轮 D6-A0 增加 Ask / notes / highlights / user asset 写入路径的依赖审计与迁移边界收口，D6-A5 在不切 UI / 不增 DB migration 的前提下完成 notes / highlights 双合同 schema + service 验证 spike，D6-U4 把 D6-A5 的 409 deferred 路径推进到真实 single-range persistence（新增 migration + runtime 写入），但仍不启用 `/app/reader-record` UI 写入口；UI-D3 只新增 Reader Record Plate Surface read-only scaffold，不切默认产品 route，不启用 Ask / notes / highlights 写入。

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
| 新 Reading Record 产品 route shell | `/app/reader-record/{recordId}` | `/api/web/reader-plate/{recordId}/snapshot` | 新 `Reading Record.record_id` | `reader-record/[recordId]/page.tsx`；W3-C3 起通过 snapshot adapter 渲染旧 Workbench 风格只读中心 Plate 区，W3-D1 起承接 `/app/read` 成功 landing；D6-P7B 起在旧 Workbench header 内展示 snapshot `enhancement_progress` capability 级轻量进度条；D6-E2B 起用 Web smoke test 锁定初始 snapshot -> reader_events polling -> snapshot reload 后，Plate 中心区可更新译文、词汇/语法标注和 progress；D6-E3 起 `local-real-chain-runbook.md` 固化 `/app/read -> /api/web/reading-record/submit -> /app/reader-record/{recordId}` 的本地真实链路验证 checklist；UI-D3 起新增 `ReaderRecordPlateSurface` read-only scaffold，但未替换默认 route surface、未启用 Ask / notes / highlights 写入；W3-D5/D6/D7 起可由 Library 新 section、command palette 新分组和新 Reading Record indicator 发现；旧 active analysis task 流量仍未切入 |
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
- `/app/reader-record/{recordId}` 现在提供新的 Reading Record product route，并复用 `IntensiveReaderSurface` / `ImmersiveReaderSurface` 渲染 Workbench-backed read-only 中心 Plate 区；D6-P3 起只读 dictionary lookup 已恢复，Ask、notes/highlights、dictionary/user asset 写入仍未接通。
- D6-P7B 起 `/app/reader-record/{recordId}` 会在旧 Workbench header 内紧凑展示 optional `snapshot.enhancement_progress` 的 capability 级摘要；缺失该字段时继续使用既有 `product_state` / `readiness_state` 提示，不影响正文 Plate read-only 渲染。
- D6-E2B 起 Web focused smoke 覆盖 `/app/reader-record/{recordId}` 从 `readable_enhancing` 初始 snapshot，经 `layer_published` / `record_product_state_updated` events polling reload 到新 snapshot，并验证 Workbench-backed Plate 中心区的译文、词汇/语法 mark、句子分析和 progress 摘要同步更新；这是前端集成测试合同，不新增后端 schema 或运行时入口流量。
- D6-E3 起本地真实链路验证入口收口为 `/app/read` 默认提交到 `/api/web/reading-record/submit`，成功 landing 到 `/app/reader-record/{recordId}` 后用 events polling / snapshot reload 观察增强内容；诊断以 `reader_jobs`、`reader_events`、`enhancement_layers` 和 snapshot `enhancement_progress` 为准，worker 仍是独立进程，不进入 FastAPI lifespan。
- UI-D3 起新增 `apps/web/src/components/reader/plate/ReaderRecordPlateSurface.tsx`，它直接消费 `ReaderPlateSnapshotDto -> ReaderRecordPlateDocument`，作为 Plate read-only scaffold 渲染 stable source text、unit translation block、system marks/cues 和 compact progress；该组件当前不接 `/app/reader-record/{recordId}` 默认 route，不引用旧 Workbench / `ReaderVm` / scene adapter，不调用 Ask / notes / highlights 写 routes。
- Library 当前列表来自旧 `/records`；即使页面本身不直接渲染 `render_scene_json`，它拿到的数据对象仍属于旧 record contract。

UI / UX 方向：

- W3-C1 的 `/app/reader-record/{recordId}` 是新 Reading Record 的产品 route shell，不是最终 Reader UI 重做方案。
- 最终 cutover 不应另起一套解析页框架；应保留当前 `/app/reader/{recordId}` ReaderWorkbench 的页面框架、标题区、阅读设置、查词、笔记、高亮、Ask Claread 和浮层能力。
- 后续产品化改线的核心是把中心文章正文与标注显示区域从旧 `renderSceneToPlateDocument(reader)` 数据面迁到新 `ReaderPlateSnapshot` / Plate projection，并补齐 `unit / anchor_segment / text_range` 到现有选择、查词、笔记、Ask 附件和跳转桥的兼容层。
- `/app/reader-plate` 仍是验证入口；不要把它的单列 read-only surface 当作最终产品页视觉基线。

因此，D5-W3 不允许：

- 把旧 `record.id` / `cloud_record_id` 当成新 `Reading Record.record_id` 直接跳转。
- 为了 cutover 把旧 `render_scene_json` 或 `/scene` 映射成新 snapshot 路径。
- 把 `/app/reader-plate` 当前 read-only validation surface 伪装成已完成产品页。

## 推荐 Cutover Phases

### Phase W3-A: 文档与 guard 保持

目标：

- 保留 `/app/reader-plate` 作为验证入口。
- 明确当前 route / source-of-truth matrix。
- 保持“不回退 `/scene` / `render_scene_json`”的静态 guard。

Touched files：

- `docs/initiatives/reader-agentic-orchestration/modules/cutover-and-old-workflow.md`
- `docs/initiatives/reader-agentic-orchestration/implementation-plan.md`
- 可选：`apps/web/src/app/(private)/app/reader-plate/page.test.tsx`

Done criteria：

- 现状矩阵、风险和禁止事项进入 tracked 正式文档。
- 新 Reader Plate 路径仍只走 `/api/web/reader-plate/*`。
- 不发生任何产品路由切换。

### Phase W3-B: Route helper split，不做产品切换

目标：

- 在前端代码中显式区分 legacy reader route 与 new reading-record route。
- 先消除“一个 helper 同时承载旧/new record 语义”的歧义。
- 继续保留 `/app/reader-plate` 作为验证页，但为 query-string 直达补明确 helper。
- 2026-06-22 已落地 `legacyAppReaderRoute()` / `appReaderPlateRoute()` helper split；本轮只做命名与调用点显式化，不切任何产品流量。

Touched files：

- `apps/web/src/lib/routes.ts`
- `apps/web/src/app/(private)/app/read/AnalyzeSubmitForm.tsx`
- `apps/web/src/app/(private)/app/library/LibraryClient.tsx`
- `apps/web/src/app/(private)/app/vocabulary/VocabularyClient.tsx`
- `apps/web/src/components/layout/active-analysis-task-indicator.tsx`
- `apps/web/src/components/layout/command-palette/CommandPaletteDialog.tsx`
- `apps/web/src/components/layout/command-palette/command-palette-items.ts`
- `apps/web/src/services/bff/analysis.ts`
- `apps/web/src/app/(private)/app/reader-plate/page.tsx`
- `apps/web/src/app/(private)/app/reader-plate/page.test.tsx`
- `apps/web/src/lib/routes.test.ts`

Done criteria：

- legacy reader route helper 只接受旧 record id 语义。
- new reader-plate validation route 有独立 helper，不再散落 hardcoded `?record_id=...`。
- Vocabulary source links 显式选择 legacy reader helper，而不是复用 future reading-record helper。
- 没有任何入口因为 helper 改名而被动切到新产品页。
- `/app/read`、Library、Vocabulary、command palette、active task 与 `services/bff/analysis.ts` 仍输出旧 `/app/reader/{recordId}` URL。

### Phase W3-C: 确定新产品 Reader route

推荐方向：

- 在 Ask、user notes/highlights、dictionary/user asset 写入仍留在旧 ReaderWorkbench 的前提下，不要立刻把 `/app/reader/{recordId}` 切到新 Reader Plate；`/app/reader-record/{recordId}` 当前只恢复只读 dictionary lookup。
- 先保留旧 `/app/reader/{recordId}` 给 legacy record contract。
- 为新 Reading Record 新增明确产品 route，例如 `/app/reader-record/{recordId}`，或等价的新命名。
- 2026-06-22 已落地 W3-C1：新增 `/app/reader-record/{recordId}` route shell、`appReadingRecordRoute()` helper 和 direct-load page test，但没有切 `/app/read`、Library、Vocabulary、active task 或 command palette 流量。

不推荐当前直接做的事：

- 直接把 `/app/reader/{recordId}` 改成读取 `ReaderPlateSnapshot`。
- 用 query-param 技巧让旧/new 两类 id 共享一个模糊 helper。

Touched files：

- `apps/web/src/lib/routes.ts`
- `apps/web/src/app/(private)/app/reader-record/[recordId]/page.tsx`
- `apps/web/src/app/(private)/app/reader-record/[recordId]/page.test.tsx`
- `apps/web/src/components/reader/ReaderRecordWorkbenchSurface.tsx`
- `apps/web/src/components/layout/app-shell/index.tsx`
- `apps/web/src/lib/routes.test.ts`

Done criteria：

- 新 Reading Record 有明确产品路由，不再依赖验证页 query path 作为唯一入口。
- 新 route 当前复用 `/api/web/reader-plate/{recordId}/snapshot`，不读取旧 `/scene`。
- 旧 ReaderWorkbench 仍保留给未迁移能力，直到产品级替换完成。
- `/app/read`、Library、Vocabulary、active task 与 command palette 本轮不改线。

W3-C2 结论：

- 新 `ReaderPlateSnapshot` 可以先投影成旧 `ReaderMockVm` / `ReaderPlateDocument`，复用 `IntensiveReaderSurface` / `ImmersiveReaderSurface` 的中心正文、译文、inline mark、句式拆解和 Workbench DOM anchor contract。
- 最小 adapter 以 `snapshot.value` 为输入，把 `reader_unit` 当段落、`reader_anchor_segment` 当 sentence-like anchor、translation / vocabulary / grammar / sentence_analysis 投影为旧 surface 已支持的节点和 mark；Plate path 仍只作瞬时渲染地址。
- 本阶段不切产品流量，不改旧 `/app/reader/{recordId}` 数据源，也不承诺新 Reading Record 的 notes / highlights / Ask supplements persistence 已完成。

W3-C3 结论：

- `/app/reader-record/{recordId}` loaded 状态已从 `ReaderPlateSnapshotSurface` 验证视图切到 `ReaderRecordWorkbenchSurface`，输入仍只来自 `/api/web/reader-plate/{recordId}/snapshot`。
- `ReaderRecordWorkbenchSurface` 通过 snapshot-to-reader-workbench adapter 生成旧 `ReaderPlateDocument`，复用 `IntensiveReaderSurface` / `ImmersiveReaderSurface`、阅读模式切换、阅读设置、本地分析展开、译文、inline marks、句式拆解和 DOM anchor contract。
- 新 route 当前明确是 read-only product surface：token click、标注点击和正文选择触发的只读 dictionary lookup 可用；Ask persistence、notes/highlights persistence、dictionary/user asset 写入禁用；没有接旧 `/scene`、旧 record adapter 或 `/analysis-tasks`。
- W3-C3 当时 `/app/read`、Library、Vocabulary、command palette、active task 仍未切到 `/app/reader-record/{recordId}`；W3-D1 只切了 `/app/read` submit landing。

### Phase W3-D: 逐步改线 submit / Library / command palette / active task / Vocabulary

目标：

- 按入口逐块改线，避免一次性大迁移。
- 每次只迁一个“产生 readerUrl 或 record link 的 surface”。

W3-D0 结论：

- Web BFF submit contract 已拆清：`submitAnalysisFromWeb()` 仍是 legacy analysis task submit，成功结果中的 `recordId` 表示旧 `cloud_record_id`，`readerUrl` 继续是 `legacyAppReaderRoute(recordId)`。
- 新 Reading Record product submit adapter 是 `submitReadingRecordPlainTextFromWeb()`，成功结果使用 `readingRecordId`、`readerUrl = appReadingRecordRoute(readingRecordId)`、`baseId`、`articleReadySequence` 和 `snapshot`，不复用 legacy `recordId` 命名。
- `/app/read` 在 W3-D0 时默认提交路径仍是 `/api/web/analysis/submit`；该前置阶段未切到新 Reading Record adapter。
- Library、Vocabulary、command palette、active task 本轮未改线；新 reader submit BFF 不依赖 `/analysis-tasks`、`legacyAppReaderRoute` 或旧 `/app/reader/{recordId}`。

W3-D1 结论：

- `/app/read` 已通过 `READ_PAGE_SUBMIT_MODE = "reading-record"` 和 `submit-mode.ts` 形成明确切换点，默认调用 `/api/web/reading-record/submit`。
- 新 route handler 调用 `submitReadingRecordPlainTextFromWeb()`，成功响应使用 `readingRecordId` 和 `readerUrl = appReadingRecordRoute(readingRecordId)`，不会把新 Reading Record id 写入 legacy `recordId` 或 active analysis task payload。
- 成功 landing 已切到 `/app/reader-record/{readingRecordId}`，例如 `/app/reader-record/reading_record_1`。
- legacy `/api/web/analysis/submit` 路径与 `submitAnalysisFromWeb()` 仍保留为显式回滚路径；active task polling 仍只服务 legacy analysis task。
- Library、Vocabulary、command palette、active task 本轮未改线；这些入口仍保持旧 id / `legacyAppReaderRoute(...)` 边界。

W3-D2 结论：

- `/app/read` 新 Reading Record submit 成功后，会把最近记录最小字段写入 `localStorage["claread:web:recent-reading-record"]`：`readingRecordId`、`readerUrl`、`title`、`createdAt`。
- `title` 优先来自 `payload.snapshot.record.title`，否则用输入首行兜底；不会把 snapshot 大对象写入 localStorage。
- `/app/read` 加载时只读取通过 schema guard 的 recent payload；缺字段、非 string、非 `/app/reader-record/` URL 或非法日期都会忽略。
- 页面展示的是轻量"继续阅读"入口，点击只跳 `readerUrl`；它不是 Library，不参与 command palette / active task / Vocabulary source links，也不接旧 `/scene`。
- legacy submit mode 不写 recent Reading Record 缓存。

W3-D3 结论：

- 已用 static guard tests 锁定当前 Web 入口来源矩阵，避免旧 record id 和新 Reading Record id 在改线前被混用。guard 文件：`apps/web/src/lib/entry-source-matrix.test.ts`。
- 本轮没有切任何入口流量，没有新增 Reading Record list API，没有改后端，也没有改 Library / Vocabulary / command palette / active task 的运行时逻辑。
- 当前每个入口的数据源、id 语义和 route 边界如下：

| Surface | 当前数据源 | 当前 id 语义 | 当前 route | 当前状态 |
|---|---|---|---|---|
| `/app/read` recent recovery | Web-only `localStorage["claread:web:recent-reading-record"]` | 新 `Reading Record.record_id` | `appReadingRecordRoute(readingRecordId)` -> `/app/reader-record/{recordId}` | new（W3-D2 落地） |
| active-analysis-task-indicator | `/api/web/analysis/current` + `/api/web/analysis/tasks/{taskId}` | 旧 `cloud_record_id` | `legacyAppReaderRoute(recordId)` -> `/app/reader/{recordId}` | legacy |
| Reading Record activity indicator | `/api/web/reading-records` | 新 `Reading Record.record_id` hidden behind BFF `readerUrl` | BFF `readerUrl` -> `/app/reader-record/{recordId}` | new（W3-D7 落地，W3-D8 在阅读页/验证页/read 页隐藏） |
| command palette recent / search | `/api/web/command-palette/records` | 旧 record id | `legacyAppReaderRoute(record.id)` -> `/app/reader/{recordId}` | legacy |
| command palette new Reading Records | `/api/web/reading-records` | 新 `Reading Record.record_id` hidden behind BFF `readerUrl` | BFF `readerUrl` -> `/app/reader-record/{recordId}` | new（W3-D6 落地） |
| Library 列表 | `/records` -> `RecordResponseDto[]` | 旧 `RecordResponseDto.id` | `legacyAppReaderRoute(record.id)` -> `/app/reader/{recordId}` | legacy |
| Vocabulary source links | vocabulary item `sourceRecordId` | 旧 source record id | `legacyAppReaderRoute(item.sourceRecordId)` -> `/app/reader/{recordId}` | legacy |
| `services/bff/analysis.ts` `readerUrl` | 旧 analysis task submit/status projection | 旧 `cloud_record_id` | `legacyAppReaderRoute(recordId)` -> `/app/reader/{recordId}` | legacy |
| `services/bff/records.ts` record list | `/records` upstream list | 旧 `RecordResponseDto.id` | 不直接产出 reader route；由 consumer `LibraryClient` 选择 `legacyAppReaderRoute` | legacy |

- Static guards 覆盖：
  - `active-analysis-task-indicator.tsx` 不引用 `appReadingRecordRoute`。
  - `command-palette/CommandPaletteDialog.tsx` 和 `command-palette/command-palette-items.ts` 不引用 `appReadingRecordRoute`。
  - `LibraryClient.tsx` 不引用 `appReadingRecordRoute`。
  - `VocabularyClient.tsx` 不引用 `appReadingRecordRoute` 或 `/app/reader-record/`，并明确用 `legacyAppReaderRoute(item.sourceRecordId)` 处理旧 vocabulary source refs。
  - `services/bff/analysis.ts` 不引用 `appReadingRecordRoute`。
  - `services/bff/records.ts` 不引用 `appReadingRecordRoute` 或 `/app/reader-record/`。
  - `recent-reading-record.ts` 不引用 `legacyAppReaderRoute`、`/app/reader/` 或 `analysis-tasks`。
- 已切到 new Reading Record 的入口：`/app/read` submit landing、`/app/read` recent recovery、Library 新 section、command palette 新分组、Reading Record activity indicator（仅非阅读/验证/read app shell 页面展示）。
- 仍 legacy 的入口：active task、command palette legacy recent/search records、Vocabulary source links、`services/bff/analysis.ts` `readerUrl`、`services/bff/records.ts` record list。Vocabulary source links 的切换条件不是改 route helper，而是先让 BFF 返回新 source truth（`sourceReadingRecordId` 或 `sourceReaderUrl`）。
- 仍 legacy 且 D6-A0 标记为"先 read-only、后写切换"的入口：旧 ReaderWorkbench 的 Ask / notes / highlights / user asset 写入（仍由 `/app/reader/{recordId}` 承载）；`/app/reader-record/{recordId}` 当前只恢复 read-only dictionary lookup 和只读 snapshot 渲染。Ask / notes / highlights 的写入路径切线依赖 `schema-and-domain-contract.md` 中 `D6-A0 Ask / notes / highlights dependency audit` 的 D6 最小实现顺序，且 UI 切线依赖 Plate Surface 视觉方案，本轮不切。

建议顺序：

1. 先改新 submit 产品入口及其 route helper。
2. 再改 active task indicator / command palette。
3. 再单独处理 Vocabulary source links，前提是 source refs 已能区分 legacy source record 与 new Reading Record。
4. 最后改 Library links，前提是 Library 能识别并展示新 Reading Record 来源。

Touched files：

- W3-D0：
  - `apps/web/src/services/bff/reader-plate.ts`
  - `apps/web/src/services/bff/reader-plate.test.ts`
  - `apps/web/src/services/bff/analysis.test.ts`
  - `apps/web/src/lib/routes.test.ts`
- W3-D1+：
  - `apps/web/src/app/(private)/app/read/AnalyzeSubmitForm.tsx`
  - `apps/web/src/app/(private)/app/read/recent-reading-record.ts`
  - `apps/web/src/services/bff/analysis.ts` 或其替代新 BFF
  - `apps/web/src/components/layout/active-analysis-task-indicator.tsx`
  - `apps/web/src/components/layout/command-palette/command-palette-items.ts`
  - `apps/web/src/components/layout/command-palette/CommandPaletteDialog.tsx`
  - `apps/web/src/app/(private)/app/vocabulary/VocabularyClient.tsx`
  - `apps/web/src/app/(private)/app/library/LibraryClient.tsx`
  - `apps/web/src/services/bff/records.ts` 及后续 new record list source

Done criteria：

- 每个入口都显式知道自己跳的是 legacy reader 还是 new Reading Record route。
- 不再依赖 `services/bff/analysis.ts` 产出旧 `/app/reader/${recordId}` 作为默认产品 landing。
- Library / command palette / active task / Vocabulary source links 不再隐式混用旧/new record id。

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

W3-D4 结论：

- 后端新增 `GET /reader/records` 只读 list endpoint，返回 user-scoped Reading Record 列表，字段包含 `record_id`、`title`、`created_at`、`source_type`、`source_metadata`、`product_state`、`readiness_state`、`last_event_sequence`；支持 `limit`（默认 20，max 100），按 `created_at DESC` 排序，不返回 snapshot 全量内容。
- Web 新增 `services/api/reading-records.ts` 上游客户端、`services/bff/reading-records.ts` BFF source、`/api/web/reading-records` Web API route；BFF 输出 `readingRecordId` 和 `readerUrl = appReadingRecordRoute(readingRecordId)`，不暴露 `recordId` / `record_id` 命名。
- 新 BFF source 不引用 `legacyAppReaderRoute`、`/app/reader/` 或 `analysis-tasks`；`entry-source-matrix.test.ts` 已新增 guard 锁定此边界。
- 本轮没有切任何入口流量：Library、command palette、active task、Vocabulary source links 仍保持旧 id / `legacyAppReaderRoute(...)` 边界。
- 后端 focused tests 覆盖 user scope、字段 shape、排序、limit 防御；Web BFF tests 覆盖 readerUrl、新 id 命名、无 legacy route。
- 后续 W3-D5+ 可逐个入口改线；Library 已在 W3-D5 新增 Reading Record 发现 section，剩余入口按 command palette recent / search、active task、Vocabulary source links 继续迁移。

W3-D5 结论：

- Library 页面已新增 `ReadingRecordSection` 客户端组件，在现有 Library 页面框架内（Archive Header 上方）渲染一个明确的新 Reading Record 发现 section。
- 新 section 从 `/api/web/reading-records` 获取数据，覆盖 loading、empty、error 三种状态；list 状态展示 title、createdAt、productState、readinessState 最小状态信息。
- 新 section 使用 BFF 返回的 `readerUrl` 跳转，不在 Library 里手写 `/app/reader-record/` 路径，也不引用 `appReadingRecordRoute` / `legacyAppReaderRoute` route helper。
- 旧 Library records 列表行为不变：仍使用 `legacyAppReaderRoute(record.id)` 跳转旧 `/app/reader/{recordId}`，仍从旧 `/records` BFF 获取数据。
- `LibraryClient.tsx` 仍只引用 `legacyAppReaderRoute`，不引用 `appReadingRecordRoute`；新 section 是独立客户端组件 `ReadingRecordSection.tsx`，不引用任何 route helper。
- `entry-source-matrix.test.ts` 已新增 guard：`ReadingRecordSection.tsx` 不引用 `legacyAppReaderRoute`、`/app/reader/` 或 `analysis-tasks`；`LibraryClient.tsx` 旧 record list 仍使用 `legacyAppReaderRoute`。
- command palette、active task、Vocabulary source links 仍保持 legacy 边界，本轮未改线。
- focused tests 覆盖 loading/empty/error/list 状态、readerUrl 使用、无 legacy route 引用。

W3-D6 结论：

- Command palette 新增独立 `ReadingRecordCommandGroup`，从 `/api/web/reading-records` 获取新 Reading Record 列表。
- 新分组只使用 BFF 返回的 `readerUrl` 跳转，不在 command palette 中手写 `/app/reader-record/`，也不引用 `appReadingRecordRoute` / `legacyAppReaderRoute`。
- 旧 command palette recent/search records 行为不变：仍从 `/api/web/command-palette/records` 获取旧 record id，并使用 `legacyAppReaderRoute(record.id)` 打开 `/app/reader/{recordId}`。
- 本轮未改 active-analysis-task-indicator、VocabularyClient 或 Library；Library W3-D5 新 section 保持原样。
- `entry-source-matrix.test.ts` 已新增 W3-D6 guard：`ReadingRecordCommandGroup.tsx` 不引用 `legacyAppReaderRoute`、`appReadingRecordRoute`、`/app/reader/`、`/app/reader-record/` 或 `analysis-tasks`，并明确消费 `readerUrl`。

W3-D7 结论：

- 新增独立 `ReadingRecordActivityIndicator`，挂载在 `AppShell` 中，与旧 `ActiveAnalysisTaskIndicator` 并列存在，不替换旧 analysis task indicator。
- 新 indicator 从 `/api/web/reading-records` 读取最近 Reading Records，优先展示 `processing`、`readable_enhancing`、`action_required`、`failed` 状态的最近项；没有这些状态时回退到最近一条 Reading Record。
- 点击新 indicator 只使用 BFF 返回的 `readerUrl`，不在组件中手写 `/app/reader-record/`，也不引用 `appReadingRecordRoute` / `legacyAppReaderRoute`。
- `action_required` 和 `failed` 状态分别显示“需要处理”“处理失败”文案，作为轻量 active/recent 提示；不做复杂任务进度或 toast 替换。
- 本轮未改 `active-analysis-task-indicator.tsx`、VocabularyClient、Library 或 command palette legacy recent/search records；旧 active task 仍绑定 `/api/web/analysis/*` 和 `legacyAppReaderRoute(...)`。
- `entry-source-matrix.test.ts` 已新增 W3-D7 guard：`reading-record-activity-indicator.tsx` 不引用 legacy/new route helpers、`/app/reader/`、`/app/reader-record/` 或 `analysis-tasks`，并明确消费 `readerUrl`。

W3-D8 结论：

- `ReadingRecordActivityIndicator` 增加 pathname gating：在 `/app/reader-record/*`、`/app/reader-plate*`、`/app/read` 隐藏，且隐藏时不发起 `/api/web/reading-records` 请求。
- `/app/read` 隐藏的原因是该页已有 W3-D2 recent recovery，避免同屏出现两个恢复入口；其他 app shell 页面继续展示轻量 active/recent indicator。
- indicator 仍只使用 BFF 返回的 `readerUrl` 导航；组件源码不手写 `/app/reader-record/`，不引用 `legacyAppReaderRoute` 或 `appReadingRecordRoute`，不触碰 `analysis-tasks`。
- 本轮未改旧 `ActiveAnalysisTaskIndicator`，也未改 Library、Vocabulary、command palette 或 submit 流量。

W3-D9 结论：

- Vocabulary source links 仍是 legacy 入口：`services/bff/vocabulary.ts` 当前从 `payload_json.source_refs[0].cloud_record_id ?? client_record_id` 投出 `sourceRecordId`，该字段不是新 Reading Record id。
- `VocabularyClient.tsx` 继续用 `legacyAppReaderRoute(item.sourceRecordId)` 跳旧 `/app/reader/{recordId}`；本轮不改 Vocabulary 运行时数据源，不新增假 `readingRecordId`，不把 `sourceRecordId` 传给 `/app/reader-record`。
- 后续切换条件是 Vocabulary BFF 拿到明确的新 source truth，例如 `sourceReadingRecordId` 或更直接的 `sourceReaderUrl`；只有到那时才能逐步把 source link 切到新 Reading Record route。
- `entry-source-matrix.test.ts` 已新增 W3-D9 guard：Vocabulary client 不引用 `appReadingRecordRoute` 或 `/app/reader-record/`，BFF 不暴露 `sourceReadingRecordId` / `sourceReaderUrl`，并明确 `sourceRecordId` 来自旧 `cloud_record_id` / `client_record_id`。

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
| Reader notes | `/app/reader/{recordId}` 内 `ReaderNotePanel` + `/api/web/reader-notes` | 旧 `analysis_record_id` + `anchor_sentence_id` + `target_key`；`reader_notes` 表 | legacy（runtime 不变） | D6-A5 双轨：request body 引入 `UserEditorialAssetAnchor` 可选 anchor，旧 `target_key` deprecated optional |
| Reader highlights | `/app/reader/{recordId}` 内 `SelectionToolbar` + `AnnotationGutter` + `/api/web/reader-annotations` | 旧 `analysis_record_id` + `sentence_id` / `target_key`；`user_annotations` 表 | legacy（runtime 不变） | D6-A5 双轨：与 notes 同样引入 anchor_segment_id 可选 anchor |
| Ask action confirm (`save_note` / `save_highlight`) | `/api/web/reader-ask/threads/{threadId}/actions/{actionId}/confirm` | 旧 `analysis_record_id` + `record_id` 两种，按 action 类型 | legacy（runtime 不变） | D6-A6 新增 Reading Record id 入口；旧 confirm 路径保留 |
| Selection → Ask attachment | `apps/web/src/lib/reader-plate/bridges/ask/adapters.ts` + `primitives/selection-targets.ts` | 旧 `targetKey` / `sentence_id` | legacy（runtime 不变） | D6-A1 read-only 投影新 anchor；D6-A6 与新 BFF / route 同步切换 |
| Dictionary / user asset 写入 | `/app/reader/{recordId}` 内 `DictionaryPopover` + 旧 asset 写入 | 旧 `analysis_record_id` | legacy（runtime 不变） | 不在 D6-A0 范围；`/app/reader-record/{recordId}` 已 read-only dictionary lookup 已恢复 |
| Ask cross-record citation (`known_reference_resolver`) | `services/api/app/services/reader_ask/known_reference_resolver.py` | 旧 `render_scene` dict | legacy（runtime 不变） | D6-A0 暂不切；等 candidate base / RAG substrate 决策 |
| `reader_scene.py` 作为 authoritative service | `/app/reader/{recordId}` 主路径 | 旧 `client_record_id` / UUID + `render_scene_json` | legacy（runtime 不变） | D6 不替换；`merge_record_with_reader_ask_supplements` 在 D6-A4 后不再承担 Ask supplement 写入 |
| `daily_vocab_agent.py` | 旧 daily vocab path | 旧 `paragraph_id` | legacy，不在 D6 切线范围 | daily_reader_workflow 不进入 runtime conversion |

### Cutover 边界声明

- Ask / notes / highlights / user asset 的写入入口**仍由 `/app/reader/{recordId}` 承载**；本轮 D6-A0 不切 `/app/reader-record/{recordId}` 的写入路径。
- `/app/reader-record/{recordId}` 的 Ask / notes / highlights UI 切线**必须等 Plate Surface 视觉方案**落地；本轮不做。
- UI-D3 已提供新的 Plate read-only scaffold 组件，但只是切线基础；默认产品 route 仍未替换，Ask / notes / highlights 仍未启用。
- 旧 `reader_ask.service` / `user_annotations` / `reader_notes` / `reader_scene` runtime 行为**完全保持不变**；D6-A0 不引入兼容性修改、不引入字段别名、不引入双轨长期兼容。
- D6-A1 起新写入切线以 `schema-and-domain-contract.md` 的 `D6-A0 Ask / Notes / Highlights Dependency Audit` 子节为起点；D6-A0 不在本轮做实现。
- 旧 `reader_ask_threads` / `reader_ask_supplements` / `user_annotations` / `reader_notes` 表**保留至旧 data 清空**；cutover 不在本轮删表。
- D6-A0 静态 guard 已落地：Web `apps/web/src/lib/reader-record-boundary.test.ts` 锁定 `/app/reader-record/{recordId}` 不引用 legacy route / scene / write route / write surface，API `services/api/tests/test_d6_a0_static_boundary.py` 锁定 `user_editorial_assets` schema-only、`reader_orchestration` 不 import `reader_ask`、新 Reader Record 路径不读 `render_scene_json` 作为 fact source。

### D6-A1 Web read-only anchor 结论

- 已落地纯前端 helper `apps/web/src/lib/reader-plate/projection/reader-record-anchor-draft.ts`：把 `/app/reader-record/{recordId}` 内的 `ReaderTextSelection` 投影为新 Reading Record anchor draft shape（`record_id` + `base_id` + `generation` + `unit_id` + `anchor_segment_id` + unit-local UTF-16 offsets + `selected_text` + `text_hash` + `scope`），供后续 Ask / notes / highlights 接入使用。
- 关键 fix：同一 unit 内第二个 anchor segment 的 unit-local offset 通过 `anchor_segment.unit_start_utf16` baseline 加上 segment-local offset 得出；不接受 segment-local offset 直接当 unit-local offset。
- helper 只产出 draft shape（`ReaderRecordAnchorDraft`），不调用任何写 API；UI 写入口（`AiWorkspacePanel` / `ReaderNotePanel` / `AnnotationGutter` / `SelectionToolbar` 的 ask/highlight/note/feedback）保持 disabled；`/app/reader-record/{recordId}` 仍未启用 Ask / notes / highlights / user asset 写入。
- D6-A0 boundary guard 已扩展覆盖该 helper 不引入 legacy 字符串或 legacy route helper；snapshot DTO 的 `record.generation` 已暴露 `reading_records.generation`，helper 输出真实 generation fence，后续可直接交给后端 anchor gate 校验。
- D6-A1 未触及 `/app/reader-record/{recordId}` 的 UI、未接新 API、未改 runtime；下一步 D6-A5（user_annotations / reader_notes 双轨写入切线）和 D6-A6（Web BFF / route 切线）才会启用 helper 与写入路径的真实连接。

### D6-A3 Ask tool signature / write-proposal anchor 结论

- 已落地后端 proposal contract：`save_note` / `save_highlight` action proposal 的 `payload_json.anchor` 可携带 `UserEditorialAssetAnchor` 同形 Reading Record anchor payload。
- legacy proposal payload 仍可用：旧 `ReaderAskAnchorRef`、`target_key`、`target_sentence_id` 不删除；未传入新 anchor 时 Ask agent 继续用 legacy `primary_anchor` 生成 proposal。
- D6-A3 只生成 write proposal，不调用 `load_validated_reading_record_anchor(...)`，不写 `user_annotations` / `reader_notes` / `reader_ask_supplements`，不改变旧 `/api/web/reader-ask/*` route / action confirm 行为。
- `/app/reader-record/{recordId}` 的 Ask 入口仍保持 disabled；启用 UI / BFF / route 仍归 D6-A6 与 Plate Surface UI 切线。

### D6-A5 Notes / Highlights dual-contract spike 结论

- 已为 `UserAnnotationCreateRequest` 与 `ReaderNoteCreateRequest` 增加 optional `anchor: UserEditorialAssetAnchor | None` 字段；当 `anchor` 存在时，legacy 必填字段（`analysis_record_id`、`sentence_id`、`start_offset`、`end_offset`、`text_hash` 等）放宽为可选，但外层 `selected_text` 必须与 `anchor.selected_text` 一致，避免两个文本事实源分叉；同时**绝不**从 `anchor.record_id` 自动回填 `analysis_record_id`，也不允许 schema 静默 remap —— `analysis_record_id` 与 anchor 是两条独立的 id 语义。
- `create_user_annotation` / `create_reader_note` 新增显式分支：当 `req.anchor is not None` 时走 Reading Record anchor gate，绕过 legacy `target_key` / render scene / DB write。gate 失败 → HTTP 400，detail 中带 `code` = 锚定错误码（如 `unit_not_found`、`reading_record_not_found`、`anchor_segment_not_found`、`outside_anchor_segment_range`）；gate 成功 → HTTP 409，detail 中带 `code = "user_editorial_asset_write_pending"` 与 `validated: True` 摘要，明确表示写入已校验但落表仍需后续。
- 本轮未新增 DB migration，未触碰 `user_annotations` / `reader_notes` / `analysis_records` 表；新分支下 legacy 表无任何 INSERT/UPDATE（通过 mock 断言 `mock_conn.fetchrow.assert_not_called()` / `mock_conn.execute.assert_not_called()` 锁定）。
- legacy `analysis_record_id` 写入路径**完全保持现状**：未携带 `anchor` 的请求仍走 legacy `target_key` / render scene / INSERT 流程，已有的 41 个 legacy 测试全部仍 pass。
- UI 写入口仍 disabled：`/app/reader-record/{recordId}` 未启用 Ask / notes / highlights / user asset 按钮；SelectionToolbar 的 `disabled.ask / .highlight / .note / .feedback = true` 契约不变。
- 新增 narrow allowlist 静态 guard：`user_annotations.py` 与 `reader_notes.py` 只能 import `app.services.reader_orchestration.anchor_gate` 与 `app.services.reader_orchestration.repository`（用于 gate 调用与 lazy `ReaderOrchestrationRepository()`），不允许 import `reader_orchestration` 包内任何其他模块，避免跨包耦合静默扩散。Allowlist 在 `tests/test_d6_a0_static_boundary.py::test_legacy_services_only_import_allowlisted_reader_orchestration_modules` 锁定。
- 同一 guard 文件修复了路径常量（`REPO_ROOT = parents[1]`，对应 `services/api/`），原先的 `parents[2]` 会落到 `services/`，导致 `_python_files` 走不存在的 `services/app/services/...`，原 4 个 guard 等于空跑；本次修复后所有 5 个 guard 真正扫到目标文件。
- 下一步 D6-A5 follow-up：把 409 deferred 路径接到真正的 persistence。D6-U3 design 结论是 V1c 先扩展 legacy `user_annotations` / `reader_notes` 表，增加 `reading_record_id` + `base_id` + `generation` + `unit_id` + `anchor_segment_id` + unit-local offsets；统一 `user_editorial_assets` 表推迟到 multi-range 或跨 scope asset 收敛时再评估。切线必须先有 schema migration + focused tests 才能做。

### D6-U2 Multi-anchor contract decision 结论

- 审计结论：当前 `UserEditorialAssetAnchor` / `anchor_gate` 只支持 single range，不支持 `multi_text`。D6-A5 optional `anchor` 分支因此只能验证一个 `anchor_segment_id` + unit-local UTF-16 range。
- V1c 最小写入策略定为 **single-range first**：`/app/reader-record` 新写入必须携带 optional `anchor: UserEditorialAssetAnchor`，服务端走 Reading Record anchor gate；没有 `anchor` 的 legacy payload 不属于新写入路径。
- D6-U2 只新增 schema-only `UserEditorialAssetAnchorSet` / range DTO 草案和 contract tests；不新增 DB migration，不接生产 persistence，不启用 `/app/reader-record` Web 写入口。
- `multi_text` 后续必须走 `UserEditorialAssetAnchorSet` / multi-range gate / persistence contract；不得把新 Reading Record id 塞回旧 `render_scene` 校验路径，也不得复用旧 `target_key` 作为 `/app/reader-record` 主锚点。
- 旧 `/app/reader/{recordId}` 行为保持不变：未携带新 `anchor` 的 notes / highlights 仍走 legacy `analysis_record_id` + `render_scene` 路径。

### D6-U3 V1c single-range persistence design 结论

- 本轮是 design / contract only：不新增 DB migration，不改 runtime 写入，不启用 `/app/reader-record` Web 写入口。
- 对比结论：V1c 推荐扩展 legacy `user_annotations` / `reader_notes` 表；不推荐立即新增统一 `user_editorial_assets` 表。
- 推荐路径的原因：两张 legacy 表已经分别承载 quick highlight 与 note body/color/update/delete/list 语义；增加 nullable Reading Record anchor columns 的 blast radius 最小，能让旧 `/app/reader` 与新 `/app/reader-record` 按 id family 隔离共存。
- 最小列 contract：`reading_record_id`、`base_id`、`generation`、`unit_id`、`anchor_segment_id`、`unit_start_utf16`、`unit_end_utf16`，继续使用 `selected_text` / `text_hash` 做内容身份；`target_key` 可保留为 deterministic compatibility key，但不再是 `/app/reader-record` authority。
- 迁移风险：`reader_notes.analysis_record_id` 当前非空，V1c 写新 Reading Record note 前必须迁移为 nullable 或建立新的 partial uniqueness path；两张表都需要 Reading Record family 的 partial indexes / constraints，且要避免把新 rows 暴露给 legacy `analysis_record_id` 查询。
- Reload projection：`/app/reader-record` 按 `reading_record_id` 查询，按当前 active `base_id` + `generation` 正常展示，使用 `unit_id` / `anchor_segment_id` / unit-local offsets 投影到 `ReaderPlateSnapshot`。stale base rows 默认隐藏或返回 typed stale state，不能回退到旧 render scene 重新校验。
- Legacy 隔离：旧 `/app/reader/{recordId}`、旧 BFF、旧 routes 继续只处理未携带 `anchor` 的 legacy payload；新 `/app/reader-record` 写 routes 必须独立、必须要求 `anchor`，且禁止 `anchor.record_id` 与 `analysis_record_id` 互相静默映射。
- 统一 `user_editorial_assets` 表的取舍：长期更干净，适合 multi-range、Ask save、translation-bound note 和跨 asset type 统一；但 V1c 会引入第二读源、新 service/route/permission/update/delete/projection 体系和 backfill 策略，因此推迟。

### D6-U4 V1c single-range persistence 实现结论

- 已新增 migration `infra/migrations/0002_reader_record_anchor_columns.sql`：`user_annotations` 和 `reader_notes` 两表对称增加 7 个 nullable Reading Record anchor columns（`reading_record_id`、`base_id`、`generation`、`unit_id`、`anchor_segment_id`、`unit_start_utf16`、`unit_end_utf16`）；`reader_notes.analysis_record_id` 和 `anchor_sentence_id` 安全迁移为 nullable；`user_annotations_text_anchor_payload_check` CHECK 被替换为接受 legacy 或 Reading Record 两条 path；两表新增 lookup index 和 partial unique index。
- `reader_notes.analysis_record_id` nullable 迁移取舍：现有 `UNIQUE (user_id, analysis_record_id, target_key)` 在 `analysis_record_id = NULL` 时不会冲突（PostgreSQL NULLs are distinct），因此不需要删除现有约束或新增并行列；新 Reading Record rows 的 dedup 依赖新增 partial unique index。这比"并行列 + 新 constraint"方案风险更小。
- `hash_algorithm` 不作为列新增：它是 code-level constant `fnv1a32-utf16`，不是 per-row data。
- runtime 写入已落地：`create_user_annotation` / `create_reader_note` 的 `req.anchor is not None` 分支在 gate 成功后真实 INSERT，`analysis_record_id = NULL`，anchor columns 填充。D6-A5 spike 的 409 `write_pending` 已移除。
- Reading Record path 不调用 `load_render_scene` / `validate_text_range_against_render_scene` / `validate_multi_text_against_render_scene`；Reading Record id 不会被静默映射到 `analysis_record_id`（INSERT SQL 硬编码 `NULL`）。
- `user_annotations` 使用 `ON CONFLICT (user_id, target_key) DO UPDATE`（复用现有 UNIQUE 约束）；`reader_notes` 使用 `ON CONFLICT` on 新增 partial unique index。
- legacy path 完全不变：未携带 `anchor` 的请求仍走 `analysis_record_id` + `load_render_scene` + `validate_*_against_render_scene`；legacy list/update/delete 按 `analysis_record_id` 查询，新 Reading Record rows（`analysis_record_id IS NULL`）对 legacy route 不可见。
- `list_user_annotations` 的 list-all 分支（`record_id is None`）显式过滤 `AND analysis_record_id IS NOT NULL`，防止新 Reading Record rows 泄漏到 legacy 全量列表。focused test 锁定该 SQL filter。
- `/app/reader-record` UI 写入口仍未启用；`/app/reader/{recordId}` legacy path 完全不变。
- FK 约束决策：V1c 不为 `reading_record_id` / `base_id` / `generation` 新增 FK 到 `reading_bases`。原因：anchor gate 已在 runtime 校验；`reading_bases` 的 `ON DELETE CASCADE` 会强制提前决定 user assets 的 cascade 语义；删除/归档语义尚未确定。Follow-up：删除/归档语义确定后 revisit FK。
- focused tests 已更新：gate success 不再返回 409，而是返回带 anchor columns 的 response；gate failure 仍返回 typed 400；render_scene non-invocation 断言不变；`analysis_record_id` non-remap 断言改为验证 INSERT SQL 硬编码 NULL 且 smuggled UUID 不出现在参数中；legacy list-all 不泄漏 Reading Record rows；legacy tests 全部仍 pass。

### 暂不切的旧能力与原因（cutover 视角）

- `reader_ask_threads` 主键不重写：跨 Reading Record 的 Ask thread 合并 / 迁移策略未确定。
- `reader_scene.py` 不被替换为新 service：直到 Plate Surface 决定 `/app/reader-record/{recordId}` 与 `/app/reader/{recordId}` 是否合并，两个 service 并存。
- `daily_vocab_agent.py` 不切：与 Daily Reader 边界对齐，本轮 daily_reader_workflow 不进入 runtime conversion。
- `services/api/app/schemas/{user_annotations,reader_notes,reader_ask,reader_scene,analysis}.py` 旧 DTO 字段不直接删除：D6 schema 演进必须保留 deprecated optional 字段，避免破坏 library / command palette / Vocabulary source links 等 legacy consumer。
- Ask cross-record citation (`known_reference_resolver`) 不切：依赖 candidate base / RAG substrate，不属于 D6 product hardening 主路径。
- 旧 Directus / Eval 观察面不切：观察面切换属于隔离 spike，不在 D6-A0 cutover 范围。
- `@target_sentence_id` 在 agent tool 内部允许保留为 alias，但禁止出现在对外 DTO / persistence；这是为避免一次大改引入回归。

## D2 / D3 要求

D2 前：

- 完成旧依赖矩阵。
- 确认 schema reset 脚本如何保留词典三表。

D3：

- 新 schema baseline 以 Reading Record 为中心。
- learning Web path 不再走旧 `/analysis-tasks`。
- 旧路径可以被 feature flag 禁用或直接移除。
- Academic workflow 保持暂缓，需要 feature flag 或隔离，不能因删除 learning workflow 旧模块而意外破坏。
