# Cutover 与旧 AI Workflow 处理

> 状态：`D5-W3 D4 done`
> 最后更新：2026-06-23
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
| 新 Reading Record 产品 route shell | `/app/reader-record/{recordId}` | `/api/web/reader-plate/{recordId}/snapshot` | 新 `Reading Record.record_id` | `reader-record/[recordId]/page.tsx`；W3-C3 起通过 snapshot adapter 渲染旧 Workbench 风格只读中心 Plate 区，W3-D1 起承接 `/app/read` 成功 landing；Library / command palette / active task 流量仍未切入 |
| 旧 Reader 产品页 | `/app/reader/{recordId}` | `getReaderRecord()` -> 旧 `/reader/records/{id}/scene` 或 `by-client-id/.../scene` | 旧 analysis record id 或 client record id | `reader/[recordId]/page.tsx`、`services/bff/reader.ts`、`services/api/reader-scene.ts`；仍承载 ReaderWorkbench、Ask、点词、笔记、高亮 |
| Library record links | `legacyAppReaderRoute(record.id)` | `/records` -> `RecordResponseDto[]` | 旧 `RecordResponseDto.id` | `LibraryClient.tsx`、`services/bff/records.ts`；Library 当前拿到的是旧 record list，不是新 Reading Record list |
| Vocabulary source links | `legacyAppReaderRoute(recordId)` / `legacyAppReaderRoute(item.sourceRecordId)` | vocabulary item source refs -> 旧 source record contract | 旧 source record id / `cloud_record_id` | `app/vocabulary/VocabularyClient.tsx`；点回原文仍跳旧 ReaderWorkbench，不能把 source record id 当新 `Reading Record.record_id` |
| Command palette 最近记录 | `legacyAppReaderRoute(record.id)` / `legacyAppReaderRoute(lastRecordId)` | recent/search record list | 旧 record id | `CommandPaletteDialog.tsx`、`command-palette-items.ts` |
| Active analysis task indicator | toast action -> `legacyAppReaderRoute(recordId)` | `/api/web/analysis/current` + `/api/web/analysis/tasks/{taskId}` | 旧 `cloud_record_id` | `active-analysis-task-indicator.tsx`、`analysis-task-client.ts` |
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

- `/app/read` 默认 submit 已围绕新 `Reading Record.record_id -> /app/reader-record/{recordId}` 工作；W3-D2 增加的 `claread:web:recent-reading-record` localStorage 只保存最近一次新 Reading Record 的最小恢复字段，不是长期事实源；active task、command palette、Library、Vocabulary source links 仍主要围绕旧 `analysis task / source record id -> /app/reader/{recordId}` 工作。
- `/app/reader/{recordId}` 仍走旧 scene adapter，把 `ReaderSceneResponseDto` 适配成 ReaderWorkbench VM。
- `/app/reader-plate` 独立消费新 `ReaderPlateSnapshot`，其 `record_id` 是新 Reading Record id，不应回灌给旧 `/app/reader/{recordId}` helper。
- `/app/reader-record/{recordId}` 现在提供新的 Reading Record product route，并复用 `IntensiveReaderSurface` / `ImmersiveReaderSurface` 渲染 Workbench-backed read-only 中心 Plate 区；Ask、notes/highlights、dictionary/user asset 写入仍未接通。
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

- 在 Ask、dictionary click lookup、user notes/highlights 仍留在旧 ReaderWorkbench 的前提下，不要立刻把 `/app/reader/{recordId}` 切到新 Reader Plate。
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
- 新 route 当前明确是 read-only product surface：Ask persistence、notes/highlights persistence、dictionary/user asset 写入禁用；没有接旧 `/scene`、旧 record adapter 或 `/analysis-tasks`。
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
| command palette recent / search | `/api/web/command-palette/records` | 旧 record id | `legacyAppReaderRoute(record.id)` -> `/app/reader/{recordId}` | legacy |
| Library 列表 | `/records` -> `RecordResponseDto[]` | 旧 `RecordResponseDto.id` | `legacyAppReaderRoute(record.id)` -> `/app/reader/{recordId}` | legacy |
| Vocabulary source links | vocabulary item `sourceRecordId` | 旧 source record id | `legacyAppReaderRoute(item.sourceRecordId)` -> `/app/reader/{recordId}` | legacy |
| `services/bff/analysis.ts` `readerUrl` | 旧 analysis task submit/status projection | 旧 `cloud_record_id` | `legacyAppReaderRoute(recordId)` -> `/app/reader/{recordId}` | legacy |
| `services/bff/records.ts` record list | `/records` upstream list | 旧 `RecordResponseDto.id` | 不直接产出 reader route；由 consumer `LibraryClient` 选择 `legacyAppReaderRoute` | legacy |

- Static guards 覆盖：
  - `active-analysis-task-indicator.tsx` 不引用 `appReadingRecordRoute`。
  - `command-palette/CommandPaletteDialog.tsx` 和 `command-palette/command-palette-items.ts` 不引用 `appReadingRecordRoute`。
  - `LibraryClient.tsx` 不引用 `appReadingRecordRoute`。
  - `VocabularyClient.tsx` 不引用 `appReadingRecordRoute`。
  - `services/bff/analysis.ts` 不引用 `appReadingRecordRoute`。
  - `services/bff/records.ts` 不引用 `appReadingRecordRoute` 或 `/app/reader-record/`。
  - `recent-reading-record.ts` 不引用 `legacyAppReaderRoute`、`/app/reader/` 或 `analysis-tasks`。
- 已切到 new Reading Record 的入口：仅 `/app/read` submit landing + `/app/read` recent recovery。
- 仍 legacy 的入口：active task、command palette、Library、Vocabulary source links、`services/bff/analysis.ts` `readerUrl`、`services/bff/records.ts` record list。

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

W3-D1/D2 已完成第一个产品入口的最小切换和 Web-only 最近记录恢复入口；W3-D3 已用 static guard tests 锁定当前入口来源矩阵，后续不要一次性迁所有 consumer：

- 先观察 `/app/read -> /app/reader-record/{recordId}` 的新 Reading Record landing 稳定性。
- 继续把 recent localStorage 当临时恢复入口；正式最近记录列表应等 new Reading Record Library source 单独实现。
- 再逐个评估 active task、command palette、Vocabulary source links 和 Library 的 id 来源，只有能区分旧/new record id 后再改线。
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
- 后续 W3-D5+ 可逐个入口改线：先 command palette recent / search，再 Library，最后 active task 和 Vocabulary source links。

## 不允许事项

- 不做旧 `render_scene_json` 到新 snapshot / Plate path 的兼容映射。
- 不把旧 `analysis_tasks` / `RecordResponseDto.id` / `cloud_record_id` 直接当新 `Reading Record.record_id` 使用。
- 不把 `/app/reader-plate` 当前 validation surface 当最终产品 UI 交付。
- 不在 cutover planning 阶段修改后端 Reader orchestration worker。
- 不通过“保留旧 route、换内部数据源”的方式静默绕过缺失的 Ask / dictionary / notes / highlights 能力。

## Daily Reader 边界

`daily_reader_workflow` 不进入本轮 runtime conversion。

如果旧表删除影响 Daily Reader，需要先标记为 keep 或重写 Daily Reader 依赖，不允许把 Daily Reader 语义混入 learning Reader orchestration。

## D2 / D3 要求

D2 前：

- 完成旧依赖矩阵。
- 确认 schema reset 脚本如何保留词典三表。

D3：

- 新 schema baseline 以 Reading Record 为中心。
- learning Web path 不再走旧 `/analysis-tasks`。
- 旧路径可以被 feature flag 禁用或直接移除。
- Academic workflow 保持暂缓，需要 feature flag 或隔离，不能因删除 learning workflow 旧模块而意外破坏。
