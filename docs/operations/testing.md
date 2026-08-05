# 测试与验证

> **状态**: `CURRENT` | **最后验证**: 2026-08-05（TEST-GOVERNANCE-API-EVALS-P2-CLOSEOUT，验收名 `API/Evals test file-name + marker governance`：77 个 API 测试文件改业务名并补 chain/seam/life marker，test-file allowlist 收缩至 0；清除 2 个陈旧 migration 测试、source-artifact schema 合同改读 `0001_initial.sql`；docstring / 旧 migration 引用与 real-LLM 文件收口；evals 非持久化任务码测试函数改业务名；清理 4 条 B905。后续 TEST-GOVERNANCE-API-IDENTIFIERS-P3 已完成测试标识符层任务码改名：377 项任务码标识符改为业务名，guard 新增 tests/** 标识符 AST 扫描，identifier allowlist 收口至 14（全部附外部合同证据），test-file allowlist 维持 0、production-symbol allowlist 维持 24）

先验证当前后端、小程序和 Web，再进入大范围产品体验或架构改动。

## 后端核心测试

工作目录：仓库根目录。

当前稳定基线使用全量后端测试：

```powershell
uv run pytest services/api/tests -q
```

如果在 `services/api/` 目录内执行，可改为 `uv run pytest tests -q`。

默认命令即 **offline 门禁**：全部 real-LLM / real-provider 测试被 conftest 三重门禁跳过（见下文），不需要额外断网参数。

## pytest marker 语义

`services/api/pyproject.toml` 启用 `--strict-markers`：未声明 marker 直接报错。已声明 marker 分四类：

- 门禁类（既有）：`real_llm` / `article_rag_smoke` / `no_network_default`。
- 业务链路 `chain_*`（一个测试文件打一个）：`chain_reader_parse` / `chain_reader_orchestration` / `chain_reading_record` / `chain_reader_ask` / `chain_article_rag` / `chain_markdown_input` / `chain_vocabulary` / `chain_console_eval` / `chain_auth` / `chain_infra`。
- seam `seam_*`（一个测试文件打一个）：`seam_pure_unit` / `seam_service_integration` / `seam_api_contract` / `seam_cross_app_contract` / `seam_real_llm`。Playwright/Ladle 维度属于 Web runner，不在 API pytest 声明；真正需要时再添加。
- 生命周期 `life_*`（可多个）：`life_permanent_regression` / `life_migration_guard` / `life_characterization` / `life_spike` / `life_temporary_compatibility` / `life_external_smoke`。

**当前态 / 规划态**（渐进 taxonomy）：

| 状态 | marker |
|---|---|
| 门禁类，已有消费者 | `real_llm` / `article_rag_smoke` / `no_network_default` |
| 治理 marker，已有消费者（naming guard 测试） | `chain_infra` / `seam_pure_unit` / `life_permanent_regression` |
| 渐进 taxonomy，尚无消费者，随存量补标逐步启用 | 其余全部 `chain_*` / `seam_*` / `life_*` |

尚无消费者的 marker 只保证“已声明、可用于 `-m`”，不保证当前能选中任何测试；不要把零消费者组合写成当前可用命令。任务编号（`T5.6b`、`D6-I4b`、`round20` 等）**不设 marker**，只放文件顶部注释 `# task-history: ...`，不进入 `-m` 运行选择。

当前可用的 marker 选择命令（有实际消费者）：

```powershell
cd services/api
uv run pytest -m chain_infra -q
uv run pytest -m "real_llm" -v   # 需同时满足三重门禁环境变量
```

规划态示例（存量补标完成后才有意义，当前会选中 0 个测试）：

```powershell
uv run pytest -m "chain_reader_ask and seam_api_contract and not real_llm" -q
```

## 任务编号 naming guard 与 allowlist ratchet

任务编号是历史追踪信息，不是业务身份。两条 guard 阻止其回流：

- **API**：`services/api/tests/test_task_number_naming_guard.py`。检查三层目标中的任务编号：新测试文件名、`app/` 生产符号（AST 标识符）、`tests/**` 测试标识符（FunctionDef / AsyncFunctionDef / ClassDef 与 module-level 赋值名，P3 新增），同时覆盖 snake_case（`d5_`/`d6_i4b` / `t58a` / `round20`）与 CamelCase / UPPER_SNAKE 任务代号（`ReaderD5SchemaHealthReport` / `READER_D5_*` / `READER_D6_*` / `ZPlus*`）。存量进入精确 allowlist，**相等 ratchet**：allowlist 数量必须恰好等于上限（test-file 0、production-symbol 24、test-identifier 14；API 测试文件名与任务码标识符存量已分别在 P2 / P3 全部改名，identifier allowlist 仅保留附外部合同证据的 KEEP 项），收缩后不得回升；改名/删除必须在同一变更中移除对应条目并同步降低上限；新文件/新符号禁止带任务编号。字符串字面量与注释豁免——协议值、fixture 载荷、migration 版本、`execution_version`、artifact version、workflow version、dataset/env 身份等持久化协议值不属于命名漂移，不在 guard 扫描范围；guard 只治理测试**标识符**命名，测试标识符与持久化协议值严格区分。
- **Web**：`apps/web/src/lib/reader-orchestration/task-number-naming-guard.test.ts`。复用 reader-orchestration source guard 的 node:fs 扫描模式，覆盖 `src/**` vitest 与 `tests/**` Playwright 测试文件名，test-file allowlist 已清空、**相等 ratchet 上限 0**；同时对测试源码逐行 fail-closed 扫描大写任务码形态（`B/R/P/S/T+数字`、`D5`/`D6`、`A3`–`A5`、`ROUND+数字`、`LP-R+数字`、`ASK-` epic 前缀），`task-history:` 行豁免，code-identity allowlist 采用 strip-then-scan 且相等 ratchet（上限 9），豁免身份不会掩护同行真实任务码。产品版本号（`-v2`）、文章等级（`-g5-`）、领域词（`l1-heading`）、表示事件合同名（`G1`/`G2`/`G3`）属持久化/业务身份，不视为任务编号。

改名既有任务编号文件时：只改文件名与顶部 `# task-history:` 注释，不改断言、不合并测试、不迁目录，并同步收缩 guard allowlist 与其 ceiling。

**标识符层治理（TEST-GOVERNANCE-API-IDENTIFIERS-P3）已收口**：C1 删除落地后重新 stdlib AST 盘点（不沿用合并前静态数字），tests/** 内任务码标识符按业务链分四批改名（Reader Parse / Artifact pipeline、Reader Orchestration / Semantic outline、Reader Ask retry / lifecycle / real-LLM harness、Infra / closeout），新名直接表达业务行为；断言、marker、fixture 值、协议字符串零变化，collection 总数保持 6139。最终 allowlist：test-file 0、production-symbol 24、test-identifier 14（8 个 `R4_A3_*_ENV` evals dataset/run 合同常量 + 5 个 READER_D5/D6 schema-health 测试 + 1 个 reasoning round0 领域语义测试，均附外部合同证据）。guard 模式族不含 `zplus`/`ZPlus`（生产域词）；字符串字面量与注释不在扫描范围。当前可用 guard 命令（已验证可跑通）：

```powershell
cd services/api
uv run pytest tests/test_task_number_naming_guard.py -q
```

至此“任务历史仅存顶部 `# task-history:` 注释与持久化协议值”对 API **文件名与测试标识符**均成立。

## 后续 API/Web 并行治理边界

- **API/Evals ownership**：`services/api` marker 补标、allowlist 收缩、剩余任务编号文件改名归 API 治理任务。`evals/` 是独立 pytest 项目，**不继承** API conftest，但已有自己的 `evals/tests/conftest.py`：声明 `real_llm` marker、镜像同一三重门禁并 monkeypatch 相同 provider 边界，fail-closed regression 已落地；后续 evals 治理（naming guard / 补标）独立排期，不与 API 治理任务混批。
- **Web ownership**：vitest guard、命名治理、`// task-history:` 注释归 Web 治理任务；不拆分 ReaderRecordPlateSurface mega-suite、不改 Web 生产逻辑，除非另行审批。
- 两侧 guard 均为纯文件系统/AST 检查，无 DB、无网络、无 LLM，可在任何 PR 门禁运行。

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

这是 **real-LLM 三重门禁（triple gate）**，由 `services/api/tests/conftest.py` 的 autouse fixture 实现：门禁关闭时 monkeypatch 所有 provider 客户端（structured completion / dashscope stream / bailian embedding / rerank），任何绕过尝试会被记录并在测试后硬失败（fail-closed）。所有会触达真实 provider 的测试必须打 `real_llm` marker 走同一门禁（包括 thinking probe scaffold），禁止自设环境变量旁路。

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
