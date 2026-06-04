# 数据库

Claread 后端使用 PostgreSQL 作为业务事实源。Redis 用于缓存和任务辅助能力。

## Migration Baseline

当前开发库 schema 基线由下列 migration 顺序构成：

```text
infra/migrations/0001_initial_schema.sql
infra/migrations/0002_reader_scene_and_debug_snapshots.sql
infra/migrations/0003_eval_control_tables.sql
```

`0001` 必须包含：

- `daily_readers.paragraph_notes_json`
- `daily_readers.takeaways_json`
- 词典三表 schema
- 用户资产、分析记录、任务、配额、反馈、Daily Reader 核心表
- `ai_usage_events`
- `dict_ai_candidate_entries`
- `reader_ask_threads` / `reader_ask_messages` / `reader_ask_supplements`
- `reader_ask_turn_runs` / `reader_ask_eval_traces`
- `analysis_overview_tasks` / `analysis_overview_task_events`

`0002` 当前必须包含：

- `analysis_records.request_payload_json`
- `analysis_debug_snapshots`
- 与 reader scene view / debug snapshot 相关的结构补充

`0003` 当前必须包含：

- Eval Center Node Probe 手动保存记录所需索引

## 词典数据

词典三表：

```text
dict_entries
dict_lookup_targets
dict_redirects
```

当前本地开发 volume：

```text
claread_postgres_data
```

当前恢复基线：

```text
dict_entries: 253300
dict_lookup_targets: 1014676
dict_redirects: 848873
entries_with_exam_tags: 20239
```

## reset 脚本

当前脚本：

```text
infra/scripts/reset_dev_keep_dict.sql
infra/scripts/reset_full_keep_dict.sql
```

要求：

- reset 开发数据时保留 `dict_*`。
- `dict_*` 相关索引和 `idx_vocabulary_book_dict_entry_id` 必须使用 `CREATE INDEX IF NOT EXISTS` / `CREATE UNIQUE INDEX IF NOT EXISTS`。
- `vocabulary_book.dict_entry_id` 外键使用 `DO` block 处理重复约束，避免 keep-dict 场景重复执行失败。
- `analysis_debug_snapshots` 属于业务表，必须纳入 reset 范围。

PostgreSQL Docker init 脚本只在 volume 首次创建时执行。已有 volume 不会因为修改 `0001/0002/0003` 自动升级；现有开发库如果已经创建过，应先执行 `reset_full_keep_dict.sql`，再依次执行新的 `0001_initial_schema.sql`、`0002_reader_scene_and_debug_snapshots.sql` 与 `0003_eval_control_tables.sql` 重建业务表，最后执行 `check_schema_baseline.sql` 验证基线完整性。

## Directus 本地配置与业务表 reset

当前开发库允许重置 `dict_*` 之外的业务表，但要区分两类数据：

- 业务表
  - 如 `analysis_records`、`analysis_results`、`analysis_tasks`、`analysis_debug_snapshots`
- Directus system tables
  - 如 `directus_collections`、`directus_fields`、`directus_relations`、`directus_presets`

当前 reset 脚本只针对业务表，不会动 `directus_*` system tables。

因此：

- `reset_dev_keep_dict.sql`
  - 只会清空业务数据
  - Directus 的 collection / fields / relations / presets 配置会保留
  - Directus 页面仍然可打开，只是业务数据为空
- `reset_full_keep_dict.sql`
  - 会删除并重建业务表
  - 只要后续 migration 重新建回相同业务表，Directus 的 metadata 仍然可以继续复用
  - 如果业务表结构发生新增/删减，重建后应再执行一次：
    - `pnpm directus:parse-run:sync-metadata`

也就是说，开发阶段重置业务表本身不会清掉 Directus 平台配置；真正需要注意的是：

- 业务表重建后，Directus 页面里的数据会消失
- 如果 schema 有变化，需要重新同步 Directus metadata

## 验收 SQL

```sql
SELECT COUNT(*) FROM dict_entries;
SELECT COUNT(*) FROM dict_lookup_targets;
SELECT COUNT(*) FROM dict_redirects;

SELECT COUNT(*)
FROM dict_entries
WHERE exam_tags IS NOT NULL AND cardinality(exam_tags) > 0;

SELECT vb.id, vb.lemma, vb.dict_entry_id, de.display_headword
FROM vocabulary_book vb
LEFT JOIN dict_entries de ON de.id = vb.dict_entry_id
WHERE vb.dict_entry_id IS NOT NULL
LIMIT 20;
```

验收时还需要记录旧库和新库的三表行数、`exam_tags` 非空数量，并抽样验证 `/dict` 与生词本 `dict_entry_id` 关联。当前仓库缺少独立 `exam_tags` 标注脚本；如果旧库本身没有标签，dump/restore 也无法生成标签。

`vocabulary_book.dict_entry_id` 依赖 `dict_entries.id`。词典重导导致 ID 变化时，生词详情可能无法加载完整词条；长期应评估稳定 key 方案。

## 后续可考虑

- 增加 `dump_dict_tables` 脚本，和现有 `restore_dict_tables.ps1` 配套。
- 设计 `dict_entry_id` 长期稳定引用策略。
- 重新评估词典数据清洗、`exam_tags` 和重跑脚本的时机。
