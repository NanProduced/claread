# D6 Article RAG Operational Runbook

> 状态：D6 文档型 Reader 修订（Article RAG operational readiness）
> 最后更新：2026-07-01
> 范围：当前 Reading Record 内 Article RAG substrate 的本地启动、状态查询、排障、可交接的运维手册。
> 与 `rag-substrate.md` 的关系：substrate 文档讲 truth / contract / citation；本文讲**怎么把这条链跑起来、怎么定位常见 fail-soft、怎么安全交接**。

## 0. 范围与不做什么

本 runbook 只覆盖 Article RAG 索引与检索链路：

```text
article_ready
  -> ArticleRagAutoEnsureService.ensure_in_transaction   (I4V)
  -> ArticleRagIndexLifecycleService.ensure_*             (I4S)
  -> ArticleRagIndexBootstrapService.bootstrap_*         (I4B)
  -> reader_jobs.article_rag_index_build                  (I4B)
  -> reader-article-rag-index-worker                     (I4U / I4C)
  -> DashScope embed + Zilliz upsert                      (I4D)
  -> reader_article_rag_index_runs.status = 'indexed'    (I4C)
  -> ArticleRagRetrievalService.retrieve_for_record      (I4E)
  -> ArticleRagContextService.build_context_pack_*        (I4F)
  -> ArticleRagAskContextProvider.build_for_ask           (I4N / I4O)
```

不覆盖：

- 通用 enhancement worker（translation / vocabulary / grammar bundle）—— 见 `local-real-chain-runbook.md`
- grammar RAG（`ZILLIZ_COLLECTION_GRAMMAR_NOTE` / `ZILLIZ_COLLECTION_SENTENCE_ANALYSIS`）—— 是旧的 1.x 链路
- 跨记录 / 全局 User Editorial Assets RAG
- 把 Original Input / 未确认 Candidate Document 纳入检索

## 1. 关键配置

Article RAG 的所有开关集中在 `services/api/app/config/settings.py` 的 `reader_article_rag_*` 字段。下面是 `services/api/.env.example` 的标准模板（参考值，**不要**直接把真实 key 写进 `.env.example`）：

```text
# Article RAG feature flag. 关闭时 auto-ensure hook 是 no-op；
# 关闭不影响 article_ready 主流程。手动 GET status / POST ensure
# 仍走 lifecycle service 语义，不被该 flag gate。
READER_ARTICLE_RAG_ENABLED=false

# Embedding provider 选择与模型。空字符串 / 未配置 = 走 Unconfigured 兜底，
# worker 会在第一个 job 上以 embedding_provider_unconfigured 失败。
READER_ARTICLE_RAG_EMBEDDING_PROVIDER=""
READER_ARTICLE_RAG_EMBEDDING_MODEL=""

# Vector store provider 选择。Zilliz 要求四件套同时非空：uri + token + collection + dim > 0。
READER_ARTICLE_RAG_VECTOR_PROVIDER=""
READER_ARTICLE_RAG_ZILLIZ_URI=""
READER_ARTICLE_RAG_ZILLIZ_TOKEN=""
READER_ARTICLE_RAG_ZILLIZ_COLLECTION="article_rag_index_v1"
READER_ARTICLE_RAG_VECTOR_DIM=1024

# Worker loop 调优（默认即可；只在排查 stale lease 或想减少 DB poll 频率时调）
READER_ARTICLE_RAG_WORKER_POLL_INTERVAL_SECONDS=5
READER_ARTICLE_RAG_WORKER_LEASE_DURATION_SECONDS=120
READER_ARTICLE_RAG_WORKER_LEASE_OWNER_PREFIX="reader-article-rag-index-worker"
READER_ARTICLE_RAG_WORKER_MAX_TICKS=100

# 真实 provider 烟囱测试 opt-in gate。CI 默认 skip；本地要跑真实链路时
# 显式打开。生产部署**不要**打开。
READER_ARTICLE_RAG_SMOKE=false
```

实际 key 放在 `services/api/.env`（不进 git），或者由外部 secret manager 注入。`DASHSCOPE_API_KEY` / `BAILIAN_API_KEY` 的语义与 Article RAG 嵌入 provider 解耦 —— 当且仅当 `READER_ARTICLE_RAG_EMBEDDING_PROVIDER=dashscope` 且 Bailian key 解析得到非空值时，I4D 工厂才会构造真实 `DashScopeArticleRagEmbeddingProvider`。

### Secret 红线

下面这些值在所有日志、HTTP 响应 detail、prompt sidecar、metadata repr 中都**不得**出现：

- `DASHSCOPE_API_KEY`
- `BAILIAN_API_KEY`
- `ZILLIZ_TOKEN`
- `READER_ARTICLE_RAG_ZILLIZ_TOKEN`

具体落地点：

- Article RAG index / retrieval / context / Ask facade 的 `repr()` / `str()` 仅暴露稳定 reason_code / status，不含任何 token。
- 409 异常路径的 HTTP detail 是固定字符串 `article_rag_index_status_unexpected_error` / `article_rag_index_ensure_unexpected_error`，不 echo `str(exc)`。
- 路由层（I4T）的 404 / 200 typed response 也不携带任何 token。
- `reader_article_rag_zilliz_token` 在 settings 加载阶段进入内存，但只通过 `build_default_article_rag_vector_writer` 工厂注入到 `ZillizArticleRagVectorWriter` 实例，从来不打 log / repr。

## 2. 启动 worker

### 路径 A：使用 pyproject 注册的 entry-point（推荐）

```powershell
cd services/api
uv sync
uv run reader-article-rag-index-worker
```

注册位置：`pyproject.toml` 的 `[project.scripts]`：

```text
reader-article-rag-index-worker = "scripts.run_reader_article_rag_index_worker:main"
```

### 路径 B：直接调模块

```powershell
cd services/api
uv run python -m scripts.run_reader_article_rag_index_worker
```

### 常用参数

```powershell
uv run reader-article-rag-index-worker `
  --poll-interval-seconds 5 `
  --lease-duration-seconds 120 `
  --lease-owner-prefix reader-article-rag-index-worker `
  --max-ticks 100 `
  --once
```

- `--once`：单次扫描并退出，常用于本地 dry-run / CI diagnostic。退出码 0 表示至少一个 tick 跑过；`stopped_reason` 在结果 JSON 中。
- 不传 `--once`：持续 loop，按 `--poll-interval-seconds` 间隔轮询 `reader_jobs` 表。

### 没有 DashScope / Zilliz 配置时的行为

`build_worker_service` 不会崩，**永远**能起来：

- `READER_ARTICLE_RAG_EMBEDDING_PROVIDER` 为空 或 解析不到合法 API key → 注入 `UnconfiguredArticleRagEmbeddingProvider`
- `READER_ARTICLE_RAG_VECTOR_PROVIDER=zilliz` 但 uri / token / collection / dim 任一缺失 → 注入 `UnconfiguredArticleRagVectorWriter`
- 任一 provider 处于 unconfigured 态 → worker 拿到 job 后立刻返回 `failed_terminal`，index_run 写 `error_json` 含 `embedding_provider_unconfigured` / `vector_writer_unconfigured`，**不会**静默写入 fake / 测试数据
- index_run 失败**不会**回滚 `reading_records.readiness_state` —— article_ready 主流程完全不受影响

## 3. lifecycle 状态查询与 ensure

### 手动 GET status

```powershell
$headers = @{ Authorization = "Bearer $session.session_token" }

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/reader/records/$recordId/article-rag-index/status" `
  -Headers $headers
```

返回的 `status` 字段（Article RAG lifecycle status）取值：

| status | 含义 | 典型 reason_code | 客户端下一步 |
|---|---|---|---|
| `indexed` | 已有可检索的 index_run，retrieval 可用 | `indexed` | Ask 走 RAG 上下文 |
| `indexing` | index_run.status='indexing'，worker 正在跑 | `index_run_indexing` | 等待 worker，Ask 走 no-attach 兜底 |
| `queued` | index_run 已 enqueued / planned | `index_run_queued` | 启动 worker |
| `failed` | 最近一次 run 失败 | `index_run_failed` | 看 `failure_code`，常见 unconfigured |
| `superseded_or_stale` | run 存在但 base/generation 已不一致 | `index_run_base_or_generation_mismatch` / `index_run_superseded` | 重新 POST ensure |
| `not_indexed` | record 状态 OK，但从未 enqueue 过 index_run | `no_index_run` | POST ensure 一次 |
| `not_ready` | record 还在 processing / 缺 active_base / 缺 stable_document | `record_not_article_ready` / `active_base_id_is_null` / `no_active_stable_document` / `stable_generation_mismatch` | 等到 record 转 article_ready 再来 |
| `unavailable` | record 不存在 / 软删 / 跨用户 | `record_not_found` | 404 |

`index_version` query 参数可指定版本（默认 `article_rag_index_v1`）。**注意**：GET 是只读路径，不开事务，不锁行，不读 chunk text / vector payload / Plate JSON。

### 手动 POST ensure

```powershell
$body = @{ expected_generation = 3 } | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/reader/records/$recordId/article-rag-index/ensure" `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $body
```

`expected_generation` 必填（ge=1），body 中**不能**带 `user_id` / `chunker_version` / `canonicalizer_version` / `builder_version` / `segmenter_version` 等任何版本覆盖字段，schema 走 `extra="forbid"`，未知字段返回 422。

返回的 `status` 取值（Article RAG ensure status）：

| status | 含义 | 客户端下一步 |
|---|---|---|
| `enqueued` | 成功 enqueue 了一个新 index_run + reader_job | 等 worker 处理 |
| `idempotent_noop` | 当前 active index_run 已存在，fingerprint 匹配 | 不重复 enqueue |
| `not_ready` | record 还没到 article_ready | 等 |
| `no_active_base` | record 缺 active_base_id | 检查 article_ready flow |
| `generation_mismatch` | 客户端 expected_generation 不匹配 record 实际 generation | 重新读 status / record |
| `plan_hash_mismatch` | I4B bootstrap 内部一致性失败（极少见） | 报 ops |
| `bootstrap_inconsistent` | bootstrap 内部报错，但被服务翻译为 typed status | 报 ops |
| `record_not_found` | 跨用户 / 软删 / 不存在 | 404 |
| `error` | 内部 guard 失败（不是用户能解决的） | 报 ops |

意外（非 typed）异常 → 409 + 固定 leak-safe detail，不 echo 异常消息、不携带 token / URI / chunk text / query text。

## 4. 失败码 / 状态 → 下一步 速查

| 来源 | 失败码 / status | 是否可重试 | 下一步 |
|---|---|---|---|
| 路由 GET 409 | `article_rag_index_status_unexpected_error` | 视情况 | 看 server log，check schema health |
| 路由 POST 409 | `article_rag_index_ensure_unexpected_error` | 视情况 | 同上 |
| worker job `embedding_provider_unconfigured` | 配置缺失 | 不可重试 | 配 `READER_ARTICLE_RAG_EMBEDDING_PROVIDER` + key，重 POST ensure |
| worker job `vector_writer_unconfigured` | 配置缺失 | 不可重试 | 配齐 Zilliz 四件套，重 POST ensure |
| worker job `retry_later` | provider 暂时性失败 | 可重试 | 尊重 `available_at`；检查网络 / provider 配额 |
| worker job `failed_terminal` 其他 | schema fence / plan_hash 不一致 | 不可重试 | 报 ops |
| lifecycle `not_ready` | record 未 article_ready | 不可重试 | 走 article_ready 流程 |
| lifecycle `superseded_or_stale` | index_run 与当前 base/generation 不一致 | 不可重试 | 重新 POST ensure |
| retrieval `retrieval_no_indexed_run` | record 没有 indexed 状态 | 不可重试 | POST ensure，等 worker |
| retrieval `retrieval_embedding_failed` | embedding provider 抛错 | 视情况 | 报 ops |
| Ask facade `article_rag_context_unexpected_error` | 内部组件抛错 | 不可重试 | 看 server log |

## 5. 本地诊断

### 5.1 字段确认（record 卡在 `not_indexed` / `superseded_or_stale`）

```powershell
psql "$env:DATABASE_URL" -c "
SELECT id, status, plan_content_sha256, chunk_count, error_json,
       embedding_model, vector_store_provider, vector_collection,
       completed_at
FROM reader_article_rag_index_runs
WHERE reading_record_id = '$recordId'::uuid
ORDER BY created_at DESC
LIMIT 5;
"
```

判断表：

| 现象 | 含义 | 下一步 |
|---|---|---|
| 没有任何 row | 从未 enqueue 过 | POST ensure |
| 有 row 但 `status='failed'`，`error_json` 含 `embedding_provider_unconfigured` | 配置缺失 | 配 `READER_ARTICLE_RAG_EMBEDDING_PROVIDER` + key，重 POST ensure |
| 有 row 但 `status='failed'`，`error_json` 含 `vector_writer_unconfigured` | Zilliz 配置缺 | 配齐 Zilliz 四件套 |
| 有 row 且 `status='indexed'`，但 lifecycle 返回 `superseded_or_stale` | 旧 run 的 base / generation 已不一致 | 重 POST ensure，会自动建新 run |
| 有 row 且 `status='indexing'`，`completed_at` 为 NULL | worker 正在跑 | 看 worker 终端日志 |
| 有 row 且 `status='indexed'`，`embedding_model` / `vector_store_provider` / `vector_collection` 异常 | 用了 fake provider 落库 | 配齐真实 provider 后重 POST ensure |

### 5.2 worker 进程在不在

```powershell
ps -ef | Select-String "reader-article-rag-index-worker"
```

如果不存在：

```powershell
cd services/api
uv run reader-article-rag-index-worker
```

### 5.3 DB 是否有未消费的 job

```powershell
psql "$env:DATABASE_URL" -c "
SELECT id, job_type, status, attempt_count, failure_class, failure_code,
       lease_owner, lease_expires_at, available_at
FROM reader_jobs
WHERE reading_record_id = '$recordId'::uuid
  AND job_type = 'article_rag_index_build'
ORDER BY created_at DESC
LIMIT 5;
"
```

判断：

| status | 含义 | 下一步 |
|---|---|---|
| `queued` | 等 worker claim | 启动 worker |
| `claimed` 且 `lease_expires_at` 未过期 | worker 持有，正在跑 | 等待 |
| `claimed` 且 `lease_expires_at` 已过期 | 旧 worker 退出 | 重启 worker，会做 stale lease recovery |
| `succeeded` | 已完成 | 走 status 查 lifecycle |
| `failed_terminal` | 终态失败 | 查 `failure_code` / `failure_message` |
| `retry_later` 且 `available_at` 在未来 | 暂时失败 | 等 |

## 6. 安全 / 边界

- **不**把任何 token / API key 写进代码、`.env.example`、日志、HTTP 响应 detail、prompt sidecar、metadata repr。
- **不**让 Article RAG worker 阻塞 `article_ready`：即使 worker 全挂，`reading_records.readiness_state` 也保持 `article_ready`。
- **不**让 vector payload 成为 citation truth —— retrieval 始终从 Postgres plan rebuild。
- **不**把 Plate JSON / Markdown / DOM / Slate 投影纳入 RAG 索引或检索。
- **不**用 `READER_ARTICLE_RAG_SMOKE=true` 进生产部署：它是测试 opt-in gate。
- **不**在没有 worker 进程的情况下假设 `indexed` 状态会自然出现 —— 那是 RAG 索引进度，不是 article_ready 进度。

## 7. 本地 dry-run + 真实 smoke 命令

本节把第 1 节（配置）和第 2 节（启动 worker）落地为可验证命令。所有命令都基于 `services/api/` 工作目录。

### 7.1 默认 no-network dry-run（CI 必跑）

`tests/test_d6_i4z_article_rag_local_dry_run.py` 默认会跑、且不触网。它覆盖：

- 启动/构造 worker 不需要 provider 配置（注入 `Unconfigured*` 兜底）
- 用真实 test-Postgres schema（per-test 临时 schema，自动清理）seed 一个最小 article_ready record
- 调 lifecycle `ensure` 创建 `index_run` + `reader_job`
- 调 `worker.process_next` 一次：因为 provider unconfigured，job / index_run 走 fail-closed
- 断言：
  - `index_run.status` ≠ `indexed`
  - `index_run.error_json.failure_code` ∈ `{embedding_provider_unconfigured, vector_writer_unconfigured}`
  - `reader_jobs.status` = `failed_terminal` 且 `failure_code` 是同一个 unconfigured sentinel
  - `reading_records.readiness_state` 保持 `article_ready` —— **worker 失败不级联回 article_ready 主流程**
  - `lifecycle.load_article_rag_index_lifecycle_status` 返回 `status=failed` + `reason_code=index_run_failed`
  - `error_json` / `reason_code` / `failure_code` 都不含 token / URI / chunk text / query text
- 二次 `ensure` 同 generation 必须返回 `idempotent_noop`（不重复 enqueue）

跑：

```powershell
cd services/api

# 只跑 I4Z dry-run
uv run pytest -q tests/test_d6_i4z_article_rag_local_dry_run.py

# 跑 I4Y + I4Z（readiness + dry-run），都不触网
uv run pytest -q tests/test_d6_i4y_article_rag_operational_readiness.py tests/test_d6_i4z_article_rag_local_dry_run.py

# 跑除 article_rag_smoke 之外的所有 I4 测试（显式排除真实 smoke）
uv run pytest -q -m "not article_rag_smoke" tests/test_d6_i4z_article_rag_local_dry_run.py
```

预期结果：所有 default-passes 测试 `passed`，真实 smoke 标记的测试 `skipped`，**无** `failed`。

### 7.2 真实 provider 烟囱测试（opt-in，生产禁止）

`tests/test_d6_i4z_article_rag_local_dry_run.py::test_real_provider_end_to_end_indexed_and_retrievable` 是真实 DashScope (Bailian) + Zilliz 链路 smoke，**默认 skip**。要 opt-in 跑，必须同时满足以下条件：

```text
READER_ARTICLE_RAG_SMOKE=1
READER_ARTICLE_RAG_EMBEDDING_PROVIDER=dashscope
# 二选一（任一非空即可）：
BAILIAN_API_KEY=<real key>
# 或者：
RAG_EMBEDDING_MODEL_PROFILE=<profile that resolves to dashscope_embedding>
READER_ARTICLE_RAG_VECTOR_PROVIDER=zilliz
READER_ARTICLE_RAG_ZILLIZ_URI=<real URI>
READER_ARTICLE_RAG_ZILLIZ_TOKEN=<real token>
# 强制 namespace 隔离：collection 名必须以 article_rag_index_smoke_ 开头
# （防止 env-typo 把 smoke 数据写到生产 collection）
READER_ARTICLE_RAG_ZILLIZ_COLLECTION=article_rag_index_smoke_<8-hex>
READER_ARTICLE_RAG_VECTOR_DIM=<1024 或按真实 provider 配>
```

**关键合同**（与 I4D 工厂 `build_default_article_rag_embedding_provider` 一致）：

- `DASHSCOPE_API_KEY` **不**是 Article RAG 嵌入 provider 的 key。`DASHSCOPE_API_KEY` 是 OCR adapter 用的 env；Article RAG 走 registry 路由（`RAG_EMBEDDING_MODEL_PROFILE` 必须解析到 `dashscope_embedding` adapter）或 legacy fallback（`BAILIAN_API_KEY` + `BAILIAN_EMBEDDING_MODEL`）。
- 凭据 env **任一非空**即可：smoke gate 接受 `BAILIAN_API_KEY` 或 `RAG_EMBEDDING_MODEL_PROFILE` 二选一。只设其中一个（team 走哪条路径）都行。
- 只设 `DASHSCOPE_API_KEY` + `READER_ARTICLE_RAG_EMBEDDING_PROVIDER=dashscope` 仍然会让工厂返回 `UnconfiguredArticleRagEmbeddingProvider` —— 这是历史 bug 修复后的预期行为，不是配置错误。
- 任一 env 缺失 → test `skip`，**不** `fail`（保护 CI 不会因为缺凭据变红）。
- **Collection 隔离硬规则**：`READER_ARTICLE_RAG_ZILLIZ_COLLECTION` 必须以 `article_rag_index_smoke_` 开头，否则 smoke 在进入测试体前就 SKIP，不会向 Zilliz 发起任何写入。生产 collection 名（如 `article_rag_index_v1`）会让 smoke 静默 skip，不会误写。

opt-in 命令：

```powershell
cd services/api

$env:READER_ARTICLE_RAG_SMOKE = "1"
$env:READER_ARTICLE_RAG_EMBEDDING_PROVIDER = "dashscope"
# 二选一（凭据 env，team 走哪条路径都行）：
$env:BAILIAN_API_KEY = "<real key>"
# 或者用 registry 路由（如果已配 model-profiles.json）：
# $env:RAG_EMBEDDING_MODEL_PROFILE = "<profile-name>"
$env:READER_ARTICLE_RAG_VECTOR_PROVIDER = "zilliz"
$env:READER_ARTICLE_RAG_ZILLIZ_URI = "<real URI>"
$env:READER_ARTICLE_RAG_ZILLIZ_TOKEN = "<real token>"
# 强制 collection 隔离：必须以 article_rag_index_smoke_ 开头
$env:READER_ARTICLE_RAG_ZILLIZ_COLLECTION = "article_rag_index_smoke_<8-hex>"
$env:READER_ARTICLE_RAG_VECTOR_DIM = "1024"

uv run pytest -q -m article_rag_smoke tests/test_d6_i4z_article_rag_local_dry_run.py
```

真实 smoke 行为（实现细节）：

1. 用真实 `Settings()` 构造 `DashScopeArticleRagEmbeddingProvider` + `ZillizArticleRagVectorWriter` + `ZillizArticleRagVectorSearcher`（**不** fake）
2. seed 一个最小 article_ready record（短英文，无敏感内容）
3. `lifecycle.ensure` → `worker.process_next` → 断言 `index_run.status='indexed'`，`embedding_model` / `vector_store_provider` / `vector_collection` 都是真实 provider 名（**不** 是 `fake-*`）
4. `retrieval.retrieve_for_record` 用同句短 query 返回 typed `ArticleRagRetrievalResult`（hits 可能为 0，因为 collection 是干净的；smoke 只断言响应 shape，不强求 hit 数）
5. `ArticleRagAskContextProvider.build_for_ask` 返回 valid `ArticleRagAskPromptAssembly`（`should_attach` 取决于 hit 数；hit 数 > 0 → True，hit 数 = 0 → False，状态 `not_indexed_or_unavailable`）
6. 残留策略：测试用 deterministic collection prefix `article_rag_index_smoke_<8-hex>`，**不**自动删除 Zilliz 数据；需要 ops 在测试结束后手动清。日志 / 失败消息都 scrub 过 token / URI。

### 7.3 真实 smoke gate 严禁进入生产

`READER_ARTICLE_RAG_SMOKE=1` 是个测试 opt-in gate。生产部署 / staging 长期运行**绝对不要**设这个变量。CI 也不会设。如果生产环境的 `.env` / secret manager 里看到这个变量，**请删掉**——它会打开真实 DashScope + Zilliz 流量，与生产 RAG 流量混在同一个 Zilliz collection namespace 下，会污染 / 干扰正式数据。
