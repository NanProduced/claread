# 数据库

Claread 后端使用 PostgreSQL 作为业务事实源。Redis 用于缓存和任务辅助能力。

## Migration Baseline

DATA-SCHEMA-BASELINE D2 之后，schema 基线只有单一 fresh baseline：

```text
infra/migrations/0001_initial.sql
```

- 由旧 0001–0029 迁移、LLM Config 控制面与 Example Lab 最终 schema 压缩而成。
- 不包含 7 张 legacy analysis 表、12 张旧 Eval 控制面表、`reader_ask_eval_traces`，
  以及受保护共享表上已确认的 legacy identity 列
  （`user_annotations.analysis_record_id`、`reader_notes.analysis_record_id` /
  `anchor_sentence_id`、`favorite_records.analysis_record_id`、
  `feedback.analysis_record_id` / `annotation_type`、
  `dict_ai_candidate_entries.record_id`、`ai_usage_events.record_id` / `task_id`、
  `user_credit_ledger.task_id`、`reader_ask_threads` / `reader_ask_turn_runs` /
  `reader_ask_supplements` 的 `analysis_record_id`）。
- `analysis_windows` 与 `layer_analysis_plans`（新链在用）、
  `eval_example_lab_entries`（受保护 Directus Collection）保留。
- `directus_*` system tables 由 Directus 自身管理，不写入 baseline。
- `docker-compose.local.yml` 只挂载这一个文件进 `/docker-entrypoint-initdb.d/`；
  `apps/directus/scripts/check-logical-registration.mjs` 与
  `tests/test_local_compose_migration_coverage.py` 强制禁止回潮。
- `pnpm directus:llm-config:sync-metadata` 保持 metadata-only：
  `llm_*` 物理表来自 baseline，脚本只同步 Directus collection metadata。

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

- reset 开发数据时保留 `dict_*` 三表与 `eval_example_lab_entries`（受保护数据）。
- reset 表清单与 baseline 的 48 张非保护表精确对齐（无 legacy 表残留）。
- baseline 中 `dict_*` 与 `eval_example_lab_entries` 的 DDL 使用
  `IF NOT EXISTS` / guarded ALTER，keep-dict 场景重复应用不会失败。

PostgreSQL Docker init 脚本只在 volume 首次创建时执行。已有 volume 不会因为修改 bootstrap SQL 自动升级；现有开发库如果已经创建过，应先执行 `reset_full_keep_dict.sql`，再重新应用：

```text
infra/migrations/0001_initial.sql
```

最后执行 `check_schema_baseline.sql` 验证基线完整性（52 张表精确集合、legacy 表/列缺失断言、退出合同的 CHECK/索引断言）。

## Directus 本地配置与业务表 reset

当前开发库允许重置受保护数据之外的业务表，但要区分两类数据：

- 业务表
  - baseline 中除 `dict_*` 与 `eval_example_lab_entries` 之外的 48 张表
- Directus system tables
  - 如 `directus_collections`、`directus_fields`、`directus_relations`、`directus_presets`

当前 reset 脚本只针对业务表，不会动 `directus_*` system tables。

因此：

- `reset_dev_keep_dict.sql`
  - 只会清空业务数据
  - Directus 的 collection / fields / relations / presets 配置会保留
  - Directus 页面仍然可打开，只是业务数据为空
- `reset_full_keep_dict.sql`
  - 会删除并重建业务表（受保护表除外）
  - 只要后续重新应用 `0001_initial.sql` 建回相同业务表，Directus 的 metadata 仍然可以继续复用

也就是说，开发阶段重置业务表本身不会清掉 Directus 平台配置；真正需要注意的是：

- 业务表重建后，Directus 页面里的数据会消失
- 如果 schema 有变化，需要重新同步 Directus metadata（LLM Config 走 metadata-only sync）

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

## 词典恢复脚本与 canonical dump

- canonical dump：`infra/backups/d2_dict_backup.dump`（DATA-D2 期间生成，SHA256 `fbaf2455…8316`，已写入脚本默认值）。该文件被 `.gitignore` 排除，不进版本库，但永远不要删除——它是 `restore_dict_tables.ps1` 的回滚源。
- `infra/scripts/restore_dict_tables.ps1` 为加固版恢复脚本：先校验 dump SHA256 和 `pg_restore --list` 覆盖面（任何破坏性语句之前），再 TRUNCATE、`--single-transaction` 数据恢复、序列修复，最后跑 `check_dict_integrity.sql`。每个阶段独立原子、显式检查退出码；失败后重跑脚本即回滚路径。

## 后续可考虑

- 设计 `dict_entry_id` 长期稳定引用策略。
- 重新评估词典数据清洗、`exam_tags` 和重跑脚本的时机。
