# 数据库

Claread 后端使用 PostgreSQL 作为业务事实源。Redis 用于缓存和任务辅助能力。

## Migration Baseline

clean import 第一阶段只保留一个 pre-release baseline：

```text
infra/migrations/0001_initial_schema.sql
```

旧 `0002_add_paragraph_notes_and_takeaways.sql` 已压回 `0001`，新仓库不迁移独立 `0002`。

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

默认通过 dump/restore 迁移到新 Claread volume。

## reset 脚本

建议迁移或重写：

```text
infra/scripts/reset_dev_keep_dict.sql
infra/scripts/reset_full_keep_dict.sql
```

要求：

- reset 开发数据时保留 `dict_*`。
- `dict_*` 相关索引和 `idx_vocabulary_book_dict_entry_id` 必须使用 `CREATE INDEX IF NOT EXISTS` / `CREATE UNIQUE INDEX IF NOT EXISTS`。
- `vocabulary_book.dict_entry_id` 外键使用 `DO` block 处理重复约束，避免 keep-dict 场景重复执行失败。

PostgreSQL Docker init 脚本只在 volume 首次创建时执行。已有 volume 不会因为修改 `0001` 自动升级。

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

- 补 `check_schema_baseline.sql`。
- 补 `check_dict_integrity.sql`。
- 将 `_migration_prep/DICT_DUMP_RESTORE_COMMANDS.md` 的草稿转为 `dump_dict_tables` / `restore_dict_tables` 脚本。
- 设计 `dict_entry_id` 长期稳定引用策略。
