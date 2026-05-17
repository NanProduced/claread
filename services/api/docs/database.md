# 数据库

Claread 后端使用 PostgreSQL 作为业务事实源。Redis 用于缓存和任务辅助能力。

## Migration Baseline

当前 schema 基线由 `0001` 加后续增量 migration 共同构成：

```text
infra/migrations/0001_initial_schema.sql
infra/migrations/0002_add_text_range_favorites_target_type.sql
infra/migrations/0003_normalize_payload_jsonb_objects.sql
infra/migrations/0004_add_multi_text_anchor_types.sql
infra/migrations/0005_add_ai_usage_events.sql
infra/migrations/0006_excerpt_assets_constraints_and_indexes.sql
infra/migrations/0007_add_dict_ai_candidates_and_credit_ledger_entry_type.sql
```

其中：

- `0001` 是可重建业务表的初始基线
- `0002` - `0007` 是当前开发阶段必须继续顺序应用的增量变更

`0001` 必须包含：

- `daily_readers.paragraph_notes_json`
- `daily_readers.takeaways_json`
- 词典三表 schema
- 用户资产、分析记录、任务、配额、反馈、Daily Reader 核心表

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

PostgreSQL Docker init 脚本只在 volume 首次创建时执行。已有 volume 不会因为修改 `0001` 自动升级；现有开发库如果已经创建过，后续表结构变更必须继续顺序执行 `0002` - `0007`。

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
