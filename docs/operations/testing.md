# 测试与验证

> **状态**: `CURRENT` | **最后验证**: 2026-08-31

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

尚无消费者的 marker 只保证“已声明、可用于 `-m`”，不保证当前能选中任何测试；不要把零消费者组合写成当前可用命令。任务编号（单字母+数字形态的历史追踪码）**不设 marker**，不进入 `-m` 运行选择。

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

## 任务编号 naming guard

测试治理当前合同：

- naming guards 保留（API 与 Web 两条静态 guard）
- offline gate 要求 provider attempts 必须为 `0`

任务编号是历史追踪信息，不是业务身份，不应出现在新测试文件名、测试标识符或生产符号中。仓库提供两条静态 guard 阻止其回流：

- **API**：`services/api/tests/test_task_number_naming_guard.py`，扫描 `app/` 生产符号与 `tests/**` 测试文件。
- **Web**：`apps/web/src/lib/reader-orchestration/task-number-naming-guard.test.ts`，覆盖 `src/**` vitest 与 `tests/**` Playwright 测试。

两条 guard 均为纯文件系统 / AST 检查，无 DB、无网络、无 LLM，可在任何 PR 门禁运行。具体扫描规则、豁免词表、allowlist 与 ceiling 以 guard 源码为准，本文不复制；改名或删除被扫描对象时，在同一变更中同步维护 guard 源码中的对应条目。

持久化协议值（env 字符串、failure_code、policy 字面量、migration 版本、`execution_version`、artifact / workflow / dataset 身份等）是持久化身份，不属于命名漂移。

运行方式：

```powershell
cd services/api
uv run pytest tests/test_task_number_naming_guard.py -q
```

Web guard 随 vitest 套件运行：

```powershell
pnpm --filter @claread/web test
```

## evals 离线门禁

`evals/` 是独立 pytest 项目，不继承 API conftest；`evals/tests/conftest.py` 声明 `real_llm` marker，真实 provider 调用必须显式 opt-in，离线门禁默认 fail-closed 不调用 provider。

## 测试套件规模与性能基线

- 当前不存在统一的全仓完整测试命令；后端、Web、evals、小程序是独立验证入口，均以各自当前运行结果为准。
- 历史测试性能任务线已以 `DEFERRED_NO_PRODUCTION_CHANGE` 关闭：旧 HEAD 的 API pytest collection 为 7418 items，这是 collection 规模事实，不是完整套件耗时，也不能解释任何完整 suite wall time。
- fixture-scope 性能假设没有可信的 measured paired A/B 支撑；不得写成“该 fixture 范围无收益”，也不得写成“耗时问题已解决”。
- 未来重启性能工作的前提：phase runner 对 PASS 和 FAIL 都输出可验证的 cleanup contract，并由单一 direct runner 按冻结 paired A/B 协议执行一次；任何协议漂移、异常、超时、清理失败或样本不等量都 fail-closed。

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

Web 工具链使用根 `package.json` 声明的 Node `24.x`；不要用 Node 25 运行 Next.js/Turbopack 门禁。

Web vitest 门禁以当前运行结果为准：

```powershell
cd apps/web
pnpm exec vitest run
```

Web smoke 应覆盖手机号登录、Reader 提交（`/app/read`）、Reader 产品页（`/app/reader/[recordId]`）、历史记录、生词本、复习、收藏、批注、反馈和设置/配额。

仓库已有 committed Playwright suite（当前 tracked E2E spec 集 `apps/web/tests/e2e/*.spec.ts`、`apps/web/playwright.config.ts`，`apps/web` 内 `pnpm test:e2e`）与 Vitest 稳定性专项。Vitest 与 Playwright 是独立验证入口，不得用其中一个替代另一个；Playwright 仍按自身环境、fixture、live/opt-in 条件单独验收，本仓库不宣称这些 E2E 当前全部稳定或全部默认可运行。涉及 SelectionToolbar、lookup preview、route focus 和 `multi_text` 的 UI 改动，仍建议在本地浏览器做交互回归。

Playwright 默认独占 `127.0.0.1:3200`，端口被其他工作树占用时可先设置 `$env:CLAREAD_E2E_PORT = '3210'`；runner 仍会拒绝复用或终止未知进程。

## Reader 恢复离线验收

可恢复解析（placeholder 防线、`failed_terminal` 不可变 successor 恢复、manual/automatic 门禁、不重复计费、Web 恢复链与告警脱敏）的定向验收命令：

后端（工作目录 `services/api/`）：

```powershell
uv run pytest tests/test_candidate_document_freeze_plan.py tests/test_reader_failed_terminal_recovery.py tests/test_reader_automatic_recovery.py tests/test_reader_recovery_route.py tests/test_reader_orchestration_worker_cli.py tests/test_reader_orchestration_runtime_wiring.py -q
```

Web（工作目录 `apps/web/`）：

```powershell
pnpm exec vitest run 'src/app/api/web/reader/records/[recordId]/recovery/route.test.ts' src/services/bff/reading-records.test.ts src/components/reader/plate/ReaderAnalysisProgressControl.test.tsx
pnpm typecheck
```

边界：

- 这些是 **offline integration acceptance**（后端使用隔离 throwaway schema，Web 为组件/route 层离线测试），不等于 real provider、真实浏览器或 production deployment acceptance。
- 不在本文硬编码历史 passed 数量或耗时；以当前运行结果为准。
- 涉及真实 provider 的测试仍需显式 owner 授权并满足上文 real-LLM 三重门禁。

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

旧脚本式 regression suite 不属于当前仓库验证入口。

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

当前 Directus 只保留通用 metadata 展示 module（enum-label-display / enum-label-interface / event-type-display / json-summary / llm-config / record-context-display / status-badge / usage-summary 等）和 reader-orch endpoints bundle。旧 Eval Center、Workflow Lab、Node Lab、Render Scene Inspector、Parse Run Observability module 已物理删除；Console / Eval 治理化控制面尚未实现。

当前建议的人工 smoke 包括：

- Directus 可正常登录
- 通用 metadata 展示 module（enum-label / event-type / json-summary / status-badge / usage-summary）可正常加载
- LLM Config module 可进入并查看 / 导出 / 导入配置 bundle
- `eval_example_lab_entries` collection 仍可访问（作为 Directus Collection 保留，不属于已删除的 Eval Center module）

## Eval / Grammar RAG 验证

工作目录：仓库根目录。

当前与 grammar RAG 最相关的测试为：

```powershell
uv run pytest services/api/tests/test_grammar_retrieval_hints.py -q
uv run pytest services/api/tests/test_rag_infra.py -q
uv run pytest services/api/tests/test_rag_integration.py -q
uv run pytest services/api/tests/test_rag_readiness.py -q
```

如变更了 grammar seed / Zilliz schema，还应重建并重新 ingest 测试 collection，再做一次 Reader orchestration 侧 RAG smoke。

## Eval Center 运维脚本（历史）

旧 Eval Center module 及其数据 reset / init 运维脚本已物理删除。Console / Eval 治理化控制面尚未实现；当前不存在任何 Eval Center 脚本入口。
