# 测试与验证

先验证当前后端和小程序基线，再开发 Web。

## 后端核心测试

工作目录：`services/api/`。

核心测试套件：

```powershell
rtk test uv run pytest tests/test_analyze_workflow.py tests/test_academic_workflow.py tests/test_task_center.py tests/test_quota_credits.py tests/test_user_assets.py tests/test_vocabulary_review.py -q
```

当前基线应在 `services/api/` 下执行。

## 后端静态检查

建议执行：

```powershell
rtk err uv run python -m compileall app tests
rtk err uv run ruff check app tests
rtk err uv run mypy app
```

`compileall` 是当前最低静态验证。`ruff` 和 `mypy` 需要按后续质量门槛继续校准。

## 小程序验证

工作目录：`apps/miniprogram/`。

建议执行：

```powershell
rtk err pnpm run build:weapp
rtk err pnpm exec tsc -p tsconfig.json --noEmit
```

已知非阻塞 warning：

- webpack asset size limit。
- no async chunks。
- `images/share/` 包体积后续作为 P2 优化。

## 旧 Regression Suite

旧仓库曾有脚本式 regression suite，但这条路线不迁移到新仓库。

后续评测应重新设计为：

- Directus 可视化管理样本、标注和人工审核状态。
- LLM-as-a-Judge workflow 读取结构化样本并写回评测结果。
- few-shot RAG 从已审核样本中选择高质量案例。
- LangSmith trace 负责观察单次运行过程，Directus/eval 负责沉淀结果和对比。

## 当前数据库基线

当前本地 Claread 数据库使用：

```text
claread_postgres_data
```

词典三表恢复基线：

```text
dict_entries: 253300
dict_lookup_targets: 1014676
dict_redirects: 848873
entries_with_exam_tags: 20239
```

PostgreSQL 扩展：

```text
pgcrypto
plpgsql
```

当前不依赖 pgvector。

## 数据库验证

当前至少检查：

- `0001` 可在空库执行。
- `daily_readers.paragraph_notes_json` 存在。
- `daily_readers.takeaways_json` 存在。
- `dict_*` 三表存在且可查询。
- `dict_*` 相关索引和 `idx_vocabulary_book_dict_entry_id` 使用 `IF NOT EXISTS`。
- `exam_tags` 覆盖未丢失。
- 生词本 `dict_entry_id` 仍可查到词条。

词典验收应记录旧库和新库的三表行数、`exam_tags` 非空数量，并抽样调用 `/dict` 和 `/dict/entry`。

## 后端验收顺序

1. 后端依赖安装。
2. 后端核心测试。
3. 后端启动和 health check。
4. 数据库 baseline 和词典校验。

## 小程序验收顺序

1. 小程序依赖安装。
2. 小程序构建。
3. TypeScript 检查。
4. 微信开发者工具打开并验证主链路。
