# Cutover 与旧 AI Workflow 处理

> 状态：`D5-W3 planning`
> 最后更新：2026-06-22
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
| 新提交产品入口 | `/app/read` | `/api/web/analysis/*` -> 旧 `/analysis-tasks` | 旧 `cloud_record_id` / analysis record id | `AnalyzeSubmitForm.tsx`、`services/bff/analysis.ts`；成功后仍跳旧 `/app/reader/{recordId}` |
| 新 Reader Plate 验证页 | `/app/reader-plate` + `?record_id=` | `/api/web/reader-plate/*` -> 新 `/reader/records/plain-text|snapshot|events` | 新 `Reading Record.record_id` | `reader-plate/page.tsx`；当前是 read-only validation surface，不是最终产品 UI |
| 旧 Reader 产品页 | `/app/reader/{recordId}` | `getReaderRecord()` -> 旧 `/reader/records/{id}/scene` 或 `by-client-id/.../scene` | 旧 analysis record id 或 client record id | `reader/[recordId]/page.tsx`、`services/bff/reader.ts`、`services/api/reader-scene.ts`；仍承载 ReaderWorkbench、Ask、点词、笔记、高亮 |
| Library record links | `appReaderRoute(record.id)` | `/records` -> `RecordResponseDto[]` | 旧 `RecordResponseDto.id` | `LibraryClient.tsx`、`services/bff/records.ts`；Library 当前拿到的是旧 record list，不是新 Reading Record list |
| Vocabulary source links | `appReaderRoute(recordId)` / `appReaderRoute(item.sourceRecordId)` | vocabulary item source refs -> 旧 source record contract | 旧 source record id / `cloud_record_id` | `app/vocabulary/VocabularyClient.tsx`；点回原文仍跳旧 ReaderWorkbench，不能把 source record id 当新 `Reading Record.record_id` |
| Command palette 最近记录 | `appReaderRoute(record.id)` / hardcoded `/app/reader/...` | recent/search record list | 旧 record id | `CommandPaletteDialog.tsx`、`command-palette-items.ts` |
| Active analysis task indicator | toast action -> `appReaderRoute(recordId)` | `/api/web/analysis/current` + `/api/web/analysis/tasks/{taskId}` | 旧 `cloud_record_id` | `active-analysis-task-indicator.tsx`、`analysis-task-client.ts` |
| `services/bff/analysis.ts` `readerUrl` | hardcoded `/app/reader/${recordId}` | 旧 analysis task submit/status projection | 旧 `cloud_record_id` | 是当前 cutover 最显式的旧产品路径投射点 |
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

- `/app/read`、active task、command palette、Library、Vocabulary source links 仍主要围绕旧 `analysis task / source record id -> /app/reader/{recordId}` 工作。
- `/app/reader/{recordId}` 仍走旧 scene adapter，把 `ReaderSceneResponseDto` 适配成 ReaderWorkbench VM。
- `/app/reader-plate` 独立消费新 `ReaderPlateSnapshot`，其 `record_id` 是新 Reading Record id，不应回灌给旧 `/app/reader/{recordId}` helper。
- Library 当前列表来自旧 `/records`；即使页面本身不直接渲染 `render_scene_json`，它拿到的数据对象仍属于旧 record contract。

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

Touched files：

- `apps/web/src/lib/routes.ts`
- `apps/web/src/app/(private)/app/reader-plate/page.tsx`
- `apps/web/src/app/(private)/app/reader-plate/page.test.tsx`
- `apps/web/src/components/layout/app-shell/index.tsx`
- `apps/web/src/app/(private)/app/vocabulary/VocabularyClient.tsx`

Done criteria：

- legacy reader route helper 只接受旧 record id 语义。
- new reader-plate validation route 有独立 helper，不再散落 hardcoded `?record_id=...`。
- Vocabulary source links 显式选择 legacy reader helper，而不是复用 future reading-record helper。
- 没有任何入口因为 helper 改名而被动切到新产品页。

### Phase W3-C: 确定新产品 Reader route

推荐方向：

- 在 Ask、dictionary click lookup、user notes/highlights 仍留在旧 ReaderWorkbench 的前提下，不要立刻把 `/app/reader/{recordId}` 切到新 Reader Plate。
- 先保留旧 `/app/reader/{recordId}` 给 legacy record contract。
- 为新 Reading Record 新增明确产品 route，例如 `/app/reader-record/{recordId}`，或等价的新命名。

不推荐当前直接做的事：

- 直接把 `/app/reader/{recordId}` 改成读取 `ReaderPlateSnapshot`。
- 用 query-param 技巧让旧/new 两类 id 共享一个模糊 helper。

Touched files：

- `apps/web/src/lib/routes.ts`
- `apps/web/src/app/(private)/app/reader-plate/page.tsx` 或提炼后的新 reader-record page
- `apps/web/src/components/layout/app-shell/index.tsx`
- 相关 auth/BFF wiring 与 page-level tests

Done criteria：

- 新 Reading Record 有明确产品路由，不再依赖验证页 query path 作为唯一入口。
- 新 route 仍只消费 snapshot/events，不读取旧 `/scene`。
- 旧 ReaderWorkbench 仍保留给未迁移能力，直到产品级替换完成。

### Phase W3-D: 逐步改线 submit / Library / command palette / active task / Vocabulary

目标：

- 按入口逐块改线，避免一次性大迁移。
- 每次只迁一个“产生 readerUrl 或 record link 的 surface”。

建议顺序：

1. 先改新 submit 产品入口及其 route helper。
2. 再改 active task indicator / command palette。
3. 再单独处理 Vocabulary source links，前提是 source refs 已能区分 legacy source record 与 new Reading Record。
4. 最后改 Library links，前提是 Library 能识别并展示新 Reading Record 来源。

Touched files：

- `apps/web/src/app/(private)/app/read/AnalyzeSubmitForm.tsx`
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

## 下一轮最小编码任务建议

下一轮最小可执行切片建议是 W3-B，而不是直接切产品路由：

- 在 `src/lib/routes.ts` 引入显式 legacy/new route helpers。
- 把 `/app/reader-plate?record_id=...` 封装为明确 helper。
- 更新 `reader-plate/page.tsx` 与静态 guard test 使用新 helper。
- 保持 `/app/read`、Library、command palette、active task 的现有行为不变。

这样可以先把“route 语义分叉”编码化，降低后续把旧/new record id 混用的风险，而不提前切走旧 ReaderWorkbench 的产品能力。

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
