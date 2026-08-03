# 测试与验证

> **状态**: `CURRENT` | **最后验证**: 2026-08-03（CUTOVER-DOC-TRUTH-CLOSEOUT-R1：Architectural Cutover Complete；旧 Eval Center / Parse Run / Render Scene Inspector module 与 `reset-eval-center-data.ps1` 已物理删除，相关验证步骤同步移除）

先验证当前后端、小程序和 Web，再进入大范围产品体验或架构改动。

## 后端核心测试

工作目录：仓库根目录。

当前稳定基线使用全量后端测试：

```powershell
uv run pytest services/api/tests -q
```

如果在 `services/api/` 目录内执行，可改为 `uv run pytest tests -q`。

## 后端静态检查

建议执行：

```powershell
uv run ruff check app tests
```

`ruff` 和 `mypy` 的全量门槛仍需继续校准；改动后至少对触达文件运行 targeted ruff。

## Ask Claread 真实 LLM Smoke

Ask Claread 的真实 LLM smoke 默认跳过。只有同时满足以下三项才允许执行：

- `CLAREAD_ALLOW_REAL_LLM_TESTS=1`
- `CLAREAD_REAL_LLM_MODEL=<已授权模型名>`
- pytest 显式传入 `-m real_llm`

运行示例：

```powershell
cd services/api
$env:CLAREAD_ALLOW_REAL_LLM_TESTS = "1"
$env:CLAREAD_REAL_LLM_MODEL = "glm-5.1"
uv run pytest tests/test_reader_record_ask_real_llm_eval.py -m real_llm -v
```

测试会先校验当前 `reader_record_ask` route 解析出的 `model_name` 与
`CLAREAD_REAL_LLM_MODEL` 完全一致；不一致时跳过，避免误用本地默认模型。

## Web 验证

工作目录：仓库根目录。

```powershell
pnpm --filter @claread/web typecheck
pnpm --filter @claread/web lint
pnpm --filter @claread/web build
```

Web smoke 应覆盖手机号登录、Reader 提交（`/app/read`）、Reader 产品页（`/app/reader/[recordId]`）、历史记录、生词本、复习、收藏、批注、反馈和设置/配额。

当前仓库未提交稳定的 Reader Playwright 用例。涉及 SelectionToolbar、lookup preview、route focus 和 `multi_text` 的 UI 改动，需在本地浏览器做交互回归；等 committed e2e 恢复后，再把命令补回本文。

## 小程序验证

工作目录：仓库根目录。

建议执行：

```powershell
pnpm miniprogram:typecheck
pnpm miniprogram:build
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

## Directus / Claread Console 验证

工作目录：仓库根目录。

当前 Directus 控制面至少执行：

```powershell
pnpm directus:extensions:build
pnpm --filter @claread/directus-endpoints test
```

如触达 LLM config metadata 结构，还应补：

```powershell
pnpm directus:llm-config:sync-metadata
```

当前 Directus 只保留通用 metadata 展示 module（enum-label-display / enum-label-interface / event-type-display / json-summary / llm-config / record-context-display / status-badge / usage-summary 等）和 reader-orch endpoints bundle。旧 Eval Center、Workflow Lab、Node Lab、Render Scene Inspector、Parse Run Observability module 已在 cutover 中物理删除，按新 orchestration 重建属于 post-cutover backlog。

当前建议的人工 smoke 包括：

- Directus 可正常登录
- 通用 metadata 展示 module（enum-label / event-type / json-summary / status-badge / usage-summary）可正常加载
- LLM Config module 可进入并查看 / 导出 / 导入配置 bundle
- `eval_example_lab_entries` collection 仍可访问（作为 Directus Collection 保留，不属于已删除的 Eval Center module）

## Eval / Grammar RAG 验证

工作目录：仓库根目录。

当前与 grammar RAG 收口最相关的测试为：

```powershell
uv run pytest services/api/tests/test_grammar_retrieval_hints.py -q
uv run pytest services/api/tests/test_rag_infra.py -q
uv run pytest services/api/tests/test_rag_integration.py -q
uv run pytest services/api/tests/test_rag_readiness.py -q
```

如变更了 grammar seed / Zilliz schema，还应重建并重新 ingest 测试 collection，再做一次 Reader orchestration 侧 RAG smoke。

## Eval Center 运维脚本（历史）

旧 Eval Center module 已在 cutover 中物理删除，`infra/scripts/reset-eval-center-data.ps1` 已删除。`infra/scripts/init-eval-center-dev.ps1` 仍保留但不属于当前生产控制面；Console / Eval 按新 orchestration 重建属于 post-cutover backlog，重建前不应依赖该脚本作为当前控制面入口。
