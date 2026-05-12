# 词典数据与服务

Claread 使用 PostgreSQL 中的词典表支撑查词、生词本和阅读页词义解释。

## 核心表

| 表 | 职责 |
|----|------|
| `dict_entries` | TECD3 词条主体 |
| `dict_lookup_targets` | 查词、lemma、短语等 lookup 目标 |
| `dict_redirects` | redirect 和 fragment fallback |

这些表是高成本本地资产，不应在迁移第一阶段随意重导。

## 数据迁移策略

迁移执行细节见 `services/api/docs/database.md`。本文件只记录架构结论：词典表是高成本资产，第一阶段优先保留现有数据和 ID，不默认重跑导入脚本。

## 已知风险

- `dict_entries.id` 是自增 ID，重导会改变 ID。
- `vocabulary_book.dict_entry_id` 依赖 `dict_entries.id`。
- `import_tecd3.py` 当前不写 `exam_tags`。
- `exam_tags` 标注脚本尚未完成。
- `dict_*` 相关索引和 `vocabulary_book.dict_entry_id` 外键需要支持 keep-dict 场景下重复执行 baseline。

## 后续专项

新仓库稳定后再处理：

- 补 `exam_tags` 标注脚本。
- 评估 `dict_entry_id` 向 `(source, source_entry_key)` 稳定引用过渡。
- 重新导入词典并修复数据质量问题。
