# Ask Claread 上下文记忆与同步 Compaction — 运行 Runbook

**效力**：本 runbook 记录已落地代码事实与已验收门禁，是上下文压缩链路的运行/回滚权威摘要。代码与测试优先于本稿；如有冲突以代码为准。

## 1. 参数矩阵（代码权威值，单位 chars）

| 参数 | 值 | 代码位置 |
|---|---|---|
| 总字符账 `MODEL_VISIBLE_TURN_PAYLOAD_CAP` | 128,000 | `model_view_budget.py:58` |
| recent history 账户 `RESERVE_RECENT_HISTORY` | 40,000 | `model_view_budget.py:73` |
| compacted memory 账户 `RESERVE_MEMORY` | 8,000 | `model_view_budget.py:72` |
| recent pair 上限 `_RECENT_PAIRS` | 20 | `thread_memory/manager.py:60` |
| 账户数 | 9（七原 + memory + recent_history），`sum == CAP` 不变量 | `model_view_budget.py:99` |
| Compactor 模型 | 固定 `ask-main-deepseek-v4-flash`（不继承主答档位） | `compactor.py` `resolve_compactor_model` |
| Compactor thinking | 强制 disabled（`extra_body.thinking.type=disabled` + `enable_thinking=False`，覆盖 profile） | `compactor.py` `build_compactor_model_settings` |
| Compactor 工具 | 无（结构化 `CompactionDraft` 输出，`parallel_tool_calls=False`，`fallback_profiles=[]`） | `compactor.py` |
| 压缩触发 | recent history 超 40K 字符或超 20 对 | `manager.py` `partition_recent_history` |

> 研究稿的 96K/6 对基线已被代码超越；上表为实际值。

## 2. Feature flag 与启用方式

- `reader_record_ask_memory_enabled`（`settings.py:82`）**默认 False**。
- 启用：`services/api/.env` 设 `READER_RECORD_ASK_MEMORY_ENABLED=true` 后重启 API。
- 即使开启，短线程（≤20 对且 recent ≤40K）**不触发**压缩（`not_needed`）；仅在触发时调用 Flash compactor。
- `reader_record_ask_agentic_enabled`、`reader_article_rag_enabled` 独立，本地已置 true（与压缩无耦合）。

## 3. 已验收证据登记（按层）

**证据分层（不得混称）**：下表的每一行属于且仅属于一种证据类型；只有“backend production-core integration”行能支撑“真实 DB→Runtime→SSE→下一轮 prompt 消费”的结论，其余行各自只证明其层。表中结果是对应门禁最近一次已记录证据，不表示每一行都在同一轮复跑。

- **manager unit/integration**：`test_manager.py` 等，内存 fake repo，证明 prepare_context / CAS / fallback / 非重叠逻辑。
- **real DB component test**：`test_db_integration.py`，真实 PostgreSQL，但直接调 repository，不经 runtime/production_stream。
- **real Flash component smoke**：`test_compactor_real_llm.py`，单次真实 provider，仅证 compactor 本身。
- **scripted UI**：`reader-record-ask-process-target-r0.spec.ts`，harness 脚本喂 SSE，证真实 `TurnProcessDisclosure` 组件对 compaction 事件的渲染（非 BFF、非生产后端集成）。
- **backend production-core integration**：`test_context_compaction_integrated_chain.py`，真实 PostgreSQL + 真实 snapshot-facts loader/runtime/manager/production_stream + 确定性 fake compactor/answer model（零 provider），证 production stream 首次写入前 memory row 不存在，随后压缩触发→真实 compaction SSE→`reader_ask_thread_memory` CAS v1 写入→下一轮 model-visible prompt 含 compacted memory + recent history 且 turn 覆盖不重叠→compactor 失败 Host fallback 主答仍完成→隐私。该层不经过 FastAPI route、Next BFF 或浏览器。

| 门禁 | 证据类型 | 命令 | 结果 |
|---|---|---|---|
| 离线 thread_memory 套件 | manager unit/integration | `pytest tests/services/reader_record_ask/thread_memory/ -m "not real_llm"` | 230 passed / 1 skipped / 1 deselected |
| 真实 PostgreSQL gate（LATERAL canonical + CAS applied/conflict + 真实写入 + finally 清理 disposable thread） | real DB component | `CLAREAD_RUN_THREAD_MEMORY_DB_TESTS=1 pytest .../test_db_integration.py` | 1 passed |
| Flash compactor 真实单次 smoke（三重门 `CLAREAD_ALLOW_REAL_LLM_TESTS=1` + `CLAREAD_REAL_LLM_MODEL=deepseek-v4-flash` + `-m real_llm`；retry=0；不记 transcript） | real Flash component | 同上条件 `pytest .../test_compactor_real_llm.py` | 1 passed，call=2.16s，typed ok，无 fallback |
| 后端生产核心集成链（真实 PG canonical history→真实 runtime/manager/production_stream→确定性 fake compactor→memory v1 写入→真实 compaction SSE→下一轮 prompt 消费 memory+recent 且不重叠；+fallback；+隐私） | backend production-core integration | `CLAREAD_RUN_THREAD_MEMORY_DB_TESTS=1 pytest .../test_context_compaction_integrated_chain.py` | 2 passed |
| 确定性全链路 SSE 顺序/身份/隐私 | production_stream unit | `pytest tests/test_reader_record_ask_production_stream.py`（含 `test_context_compaction_sse_precedes_agentic_work_and_is_safe`） | green（reader_record_ask 套件内） |
| UI compaction 生命周期（同 disclosure、冷加载不恢复过程卡） | scripted UI | `playwright ... reader-record-ask-process-target-r0.spec.ts` | 此前 22 passed；本轮按 Owner 决策未复跑 |

> **未验证边界**：当前没有一条测试把同一次真实 compaction 从 FastAPI route 连续穿过 Next BFF 再送到浏览器；backend production-core 与 scripted UI 是两层独立证据，不能组合冒充端到端证明。Owner 已决定在旧代码/逻辑 cutover 后统一整理这些带任务号的阶段性 Playwright 规格，因此本轮不新增高成本浏览器 compaction harness，也不宣称现代化后的整套 Playwright 已重新转绿。cutover 验收必须重新建立并执行 Browser→Next BFF→FastAPI 的权威 Ask 门禁。

硬门（确定性层，离线套件锁定）：fabricated citation=0、foreign binding 被 allowlist 剥离、fence 失效不渲染原 article fact、prompt 注入不持久化（redaction + allowlist + CAS）、`sum==CAP` 不变量、fail-soft 时主答仍可继续。

## 4. 时序与隐私

- 事件顺序（生产流）：`agentic.run_started` → `context.compaction.started` → `context.compaction.completed|fallback|failed` → 首个 `agentic.progress` → `message.delta` → `message.completed`。
- 压缩 payload 仅携 `execution_version/message_id/thread_id/turn_run_id` + `detail_code`（白名单值）；provider 异常文本、transcript、query、URL 一律不出现（`detail_code` 非白名单值被擦除）。
- recent history 与 compacted memory 分属独立账户，不重复计费；memory 块位于 handles 之后、selection 之前。

## 5. 回滚

- 关闭记忆：`READER_RECORD_ASK_MEMORY_ENABLED=false`（或不设）重启 API 即回到零 memory 注入；assembly 路径与无记忆时零差异（flag-off 回归保护已测）。
- migration 0028（`reader_ask_thread_memory` 列/表）为加性可空，关闭 flag 后保留无副作用，无需回滚 DDL。
- 单轮压缩失败 → deterministic emergency fallback，主答不中断；CAS 冲突 → 重建而非脏写。
