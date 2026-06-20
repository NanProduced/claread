# Cutover 与旧 AI Workflow 处理

> 状态：`D1 草案`
> 最后更新：2026-06-18
> 范围：停服重构、旧 workflow 替换、旧表/旧 UI 清理边界。

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
