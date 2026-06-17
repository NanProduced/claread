# 测试与验证

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
uv run pytest tests/test_reader_ask_real_llm_smoke.py -m real_llm -v
```

测试会先校验当前 `reader_ask` route 解析出的 `model_name` 与
`CLAREAD_REAL_LLM_MODEL` 完全一致；不一致时跳过，避免误用本地默认模型。

## Web 验证

工作目录：仓库根目录。

```powershell
pnpm --filter @claread/web typecheck
pnpm --filter @claread/web lint
pnpm --filter @claread/web build
```

Web smoke 应覆盖手机号登录、分析提交、Reader、历史记录、生词本、复习、收藏、批注、反馈和设置/配额。

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

如触达 metadata 结构，还应补：

```powershell
pnpm directus:parse-run:sync-metadata
pnpm directus:eval-center:sync-metadata
```

当前建议的人工 smoke 包括：

- Parse Run Observability 列表 / 详情 / saved views 可打开
- Render Scene Inspector 能读取 learning / academic 样本
- Eval Center 的 `node-lab` / `workflow-lab` / `run-history` 可进入
- Example Lab 条目可保存，`output_fragment` 契约校验有效
- Example Lab 的 AI RAG Generator 可生成 `grammar_tags` / `retrieval_text` / `derived_*`

## Eval / Grammar RAG 验证

工作目录：仓库根目录。

当前与 Example Lab / grammar RAG 收口最相关的测试为：

```powershell
uv run pytest services/api/tests/test_example_lab.py -q
uv run pytest services/api/tests/test_grammar_retrieval_hints.py -q
uv run pytest services/api/tests/test_rag_infra.py -q
uv run pytest services/api/tests/test_rag_integration.py -q
uv run pytest services/api/tests/test_rag_readiness.py -q
```

如变更了 grammar seed / Zilliz schema，还应重建并重新 ingest 测试 collection，再做一次 workflow 侧 RAG smoke。

## Eval Center 运维脚本

| 脚本 | 用途 |
|------|------|
| `infra/scripts/reset-eval-center-data.ps1` | 清空 eval 控制面表 + 删除 runtime artifact（不清理业务表、Directus 配置、rubrics） |
| `infra/scripts/init-eval-center-dev.ps1` | 重新应用 migration + 重置数据 + 同步 metadata + 执行 smoke checks |

init 脚本 smoke checks：

- `eval_example_lab_entries` collection metadata 存在
- `directus_fields` 不残留 `rag_eligible`
- Node Lab 默认值/constraint 不残留 `judge_compare`/`judge_compare_result`
- `experiment_fingerprint` 列存在
- eval-only 表 init 后为空
- runtime artifact 目录已清空

重置后人工验收：

1. Directus 可正常登录
2. Eval Center module 可进入
3. Node Lab 可创建 session
4. Workflow Lab 可发起 compare
5. Example Lab 条目可保存
6. Run History 页面可加载
7. Parse Run Observability 列表可显示
