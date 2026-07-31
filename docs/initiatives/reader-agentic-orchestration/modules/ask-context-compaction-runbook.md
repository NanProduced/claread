# Ask Claread 上下文记忆与同步 Compaction — 运行 Runbook

**效力**：本 runbook 记录已落地代码事实与已验收门禁，是上下文压缩链路的运行/回滚权威摘要。
研究推导过程见已迁移至 `docs/tmp/reader-orchestration/` 的 R0 深研稿（TMP，不作长期事实来源）。代码与测试优先于本稿；如有冲突以代码为准。

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

## 3. 已验收门禁（本轮复测）

| 门禁 | 命令 | 结果 |
|---|---|---|
| 离线 thread_memory 套件 | `pytest tests/services/reader_record_ask/thread_memory/ -m "not real_llm"` | 230 passed / 1 skipped / 1 deselected |
| 真实 PostgreSQL gate（LATERAL canonical + CAS applied/conflict + 真实写入 + finally 清理 disposable thread） | `CLAREAD_RUN_THREAD_MEMORY_DB_TESTS=1 pytest .../test_db_integration.py` | 1 passed |
| Flash compactor 真实单次 smoke（三重门 `CLAREAD_ALLOW_REAL_LLM_TESTS=1` + `CLAREAD_REAL_LLM_MODEL=deepseek-v4-flash` + `-m real_llm`；retry=0；不记 transcript） | 同上条件 `pytest .../test_compactor_real_llm.py` | 1 passed，call=2.16s，typed ok，无 fallback |
| 确定性全链路 SSE 顺序/身份/隐私 | `pytest tests/test_reader_record_ask_production_stream.py`（含 `test_context_compaction_sse_precedes_agentic_work_and_is_safe`） | green（1143 套件内） |
| UI compaction 生命周期（同 disclosure、冷加载不恢复过程卡） | `playwright ... reader-record-ask-process-target-r0.spec.ts` | 22 passed |

硬门（确定性层，离线套件锁定）：fabricated citation=0、foreign binding 被 allowlist 剥离、fence 失效不渲染原 article fact、prompt 注入不持久化（redaction + allowlist + CAS）、`sum==CAP` 不变量、fail-soft 时主答仍可继续。

## 4. 时序与隐私

- 事件顺序（生产流）：`agentic.run_started` → `context.compaction.started` → `context.compaction.completed|fallback|failed` → 首个 `agentic.progress` → `message.delta` → `message.completed`。
- 压缩 payload 仅携 `execution_version/message_id/thread_id/turn_run_id` + `detail_code`（白名单值）；provider 异常文本、transcript、query、URL 一律不出现（`detail_code` 非白名单值被擦除）。
- recent history 与 compacted memory 分属独立账户，不重复计费；memory 块位于 handles 之后、selection 之前。

## 5. 回滚

- 关闭记忆：`READER_RECORD_ASK_MEMORY_ENABLED=false`（或不设）重启 API 即回到零 memory 注入；assembly 路径与无记忆时零差异（flag-off 回归保护已测）。
- migration 0028（`reader_ask_thread_memory` 列/表）为加性可空，关闭 flag 后保留无副作用，无需回滚 DDL。
- 单轮压缩失败 → deterministic emergency fallback，主答不中断；CAS 冲突 → 重建而非脏写。
