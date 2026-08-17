# Reader 运行时操作

> **状态**: `CURRENT` | **最后验证**: 2026-08-17
>
> 符号与代码路径优先；过期行号不是当前 authority。
>
> 本文记录 Reader orchestration 主链路的本地真实运行方式：

```text
plain_text / artifact-backed input -> active base -> enhancement worker loop -> snapshot reload
```

范围约束：

- 只使用现有 API、Web BFF 和公开的 worker CLI entrypoint。
- 不新增 public worker-control endpoint；不把 runner 挂到 Web submit 或 FastAPI lifespan。
- 不使用 smoke harness / fake executor 作为产品路径。
- snapshot 是从 domain facts 重建的 projection，不读取旧 `render_scene_json`。
- 后端 smoke 只证明 API / worker / snapshot / events 数据链路成立，不等价于浏览器端 E2E 验证。

## 本地进程与页面边界

Reader orchestration 当前共有 3 个进程级 worker entrypoint：

| 进程 | 作用 | 常用命令 |
|---|---|---|
| API | 接收 submit、写入 durable facts、提供 snapshot/events API | `pnpm reader:api` |
| Web | 提供 `/app/read` 与 `/app/reader/[recordId]` 页面和 BFF `/api/web/reader/records/*` | `pnpm reader:web` |
| Reader enhancement worker（默认必启） | 消费 active-base enhancement jobs，发布 translation / vocabulary / grammar layers | `pnpm reader:worker:enhancement` |
| Artifact pipeline worker（文件上传必启） | 消费 `input_artifact_extraction` / `extracted_artifact_materialization`，建立 candidate 或 stable base | `pnpm reader:worker:artifact` |
| Article RAG index worker（可选） | 在 `READER_ARTICLE_RAG_ENABLED=true` 时构建文章索引 | `pnpm reader:worker:rag` |

从仓库根目录快速启动默认完整链路：`pnpm reader:dev`（`concurrently` 给每行日志加 `api` / `web` / `enhancement` / `artifact` 前缀）。只启动两个默认 worker 用 `pnpm reader:workers`；开启 RAG 时用 `pnpm reader:dev:rag` 或 `pnpm reader:workers:rag`。需要纯净日志时在独立终端运行单进程命令。

页面边界：

- `/app/read` 是用户提交入口，对应 BFF `POST /api/web/reader/records/input` -> FastAPI `/reader/records/input`。
- `/app/reader/[recordId]` 是 Reading Record 产品页，对应 BFF record-nested 路由（snapshot / events 等）；页面实现为 `apps/web/src/app/(private)/app/reader/[recordId]/plate-page.tsx` + `ReaderRecordPlateSurface`。运行时 URL 是 `/app/reader/{recordId}`。
- 旧 `/app/reader-plate`、`/app/reader-record/{recordId}`、`/api/web/reader-plate/*`、`/api/web/reading-record/submit` 已物理删除，只在 e2e 404 断言与 source-guard 测试中保留为负向断言。
- Web 只通过 pipeline status、events polling 和 snapshot reload 观察结果，不消费队列。缺少 artifact worker 时上传文件停在预 base 阶段；缺少 enhancement worker 时已可读文章停在"批注生成中"。

## 关键 wiring

| 能力 | Model route | Prompt agent | 推荐 env | 当前 fail-closed 行为 |
|---|---|---|---|---|
| translation | `reader_layer_translation` | `reader_layer_translation` | `READER_TRANSLATION_MODEL_PROFILE` | route/profile 不可用时 terminal failure，不发布空 layer |
| vocabulary | `reader_layer_vocabulary` | `reader_layer_vocabulary` | `READER_VOCABULARY_MODEL_PROFILE` | 未显式配置时 `vocabulary_executor_unconfigured` |
| grammar bundle | `reader_layer_grammar_bundle` | `reader_layer_grammar_bundle` | `READER_GRAMMAR_BUNDLE_MODEL_PROFILE` | 未显式配置时 `grammar_bundle_executor_unconfigured` |

- `MODEL_PROFILES_JSON` 与 `MODEL_PRESETS_JSON` 决定 route 可解析到哪些真实模型。
- translation route 可 fallback 到 `ANNOTATION_MODEL_PROFILE`，但本地真实链路建议显式设置 `READER_TRANSLATION_MODEL_PROFILE`。
- grammar bundle worker 成功后发布 `grammar_note` 与 `sentence_analysis` 两个 layer。

建议先准备的本地环境变量：

```powershell
$env:MODEL_PROFILES_JSON = "config/model-profiles.json"
$env:MODEL_PRESETS_JSON = "config/model-presets.json"
$env:READER_TRANSLATION_MODEL_PROFILE = "<实际存在的 profile 名>"
$env:READER_VOCABULARY_MODEL_PROFILE = "<实际存在的 profile 名>"
$env:READER_GRAMMAR_BUNDLE_MODEL_PROFILE = "<实际存在的 profile 名>"
$env:DASHSCOPE_API_KEY = "<real provider key>"
```

可选 worker loop 配置（默认值：scan interval 5s、batch size 10、max ticks 24、max jobs 24、lease duration 120s）：

```powershell
$env:READER_WORKER_SCAN_INTERVAL_SECONDS = "5"
$env:READER_WORKER_BATCH_SIZE = "10"
$env:READER_WORKER_MAX_TICKS = "24"
$env:READER_WORKER_MAX_JOBS = "24"
$env:READER_WORKER_LEASE_DURATION_SECONDS = "120"
$env:READER_WORKER_LEASE_OWNER_PREFIX = "reader-enhancement-worker"
```

默认 lease duration 120 秒，比早期 30 秒更保守，降低长文本真实 LLM 处理中途 `LeaseExpiredError` 概率；代价是 worker 异常退出后 stale lease 最长多保留约 2 分钟，由 stale lease recovery 回收。

## 启动步骤

### 1. 启动 API

```powershell
cd services/api
uv sync
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

本地手机号登录默认 mock provider（`PHONE_AUTH_PROVIDER="mock"`、`PHONE_MOCK_VERIFICATION_CODE="888888"`），可拿到真实 Claread session token。

### 2. 获取本地 session token

```powershell
$phone = "13800138000"
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/auth/phone/request-code" -ContentType "application/json" -Body (@{ phone = $phone } | ConvertTo-Json)
$session = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/auth/phone/verify-code" -ContentType "application/json" -Body (@{ phone = $phone; code = "888888" } | ConvertTo-Json)
```

返回 `user_id` / `session_token` / `expires_at`。注入 Web dev 进程：`$env:CLAREAD_WEB_DEBUG_SESSION_TOKEN = $session.session_token`。

### 3. 启动 Web

```powershell
pnpm install
pnpm reader:web   # 或 pnpm --dir apps/web run dev
```

Web dev server 默认 `http://127.0.0.1:3000`。常用环境变量：`CLAREAD_FASTAPI_BASE_URL`、`CLAREAD_PHONE_AUTH_PROVIDER=mock`、`CLAREAD_WEB_DEBUG_SESSION_TOKEN`。注意：Reader records BFF 拒绝 `anonymous` 和 `mock_phone` 会话；允许完整 Web session 或 debug session。

### 4. 启动 worker

```powershell
pnpm reader:workers                        # 两个默认 worker，合并日志
pnpm reader:worker:enhancement             # 隔离日志
pnpm reader:worker:artifact
uv run reader-enhancement-worker --once    # services/api 下单次扫描诊断
uv run reader-artifact-pipeline-worker --once
```

`--once` 输出 JSON summary，有用字段：`scanned_candidate_count`、`processed_count`、`lock_skipped_count`、`results[].record_id`、`results[].pipeline_summary.bootstrapped_job_counts` / `worker_tick_counts` / `outcome_counts` / `last_event_sequence` / `snapshot_reload_recommended` / `stopped_reason`。

## Artifact 输入与恢复操作

> **Immutable evidence**: `services/api/app/api/routes/reader_orchestration.py` §`/source-artifacts/init-upload` (line 489), §`/source-artifacts/{artifact_id}/complete-upload` (line 569), §`/source-artifacts/{artifact_id}/submit-input` (line 598); `services/api/app/services/reader_orchestration/artifact_input_application_service.py` §`submit_available_artifact_as_input` (lines 84-180); `services/api/app/services/reader_orchestration/artifact_input_status_query_service.py` §`PipelineOutcome` / §`PipelineNextAction` (lines 41-65), §status mapping (lines 225-300); `services/api/app/services/reader_orchestration/artifact_pipeline_worker_service.py` §module docstring (lines 1-50); `infra/migrations/0001_initial.sql` §`source_artifacts_status_check` (line 1445).

### 调用顺序（single-file current boundary）

当前上传链是 **single-file**：客户端先 `init-upload`，对象上传完成后调用 `complete-upload`，再以 `submit-input` 将 available artifact 绑定到 Reading Record 并入队。

```text
(1) POST /source-artifacts/init-upload
        ↓ client uploads object to OSS
(2) POST /source-artifacts/{artifact_id}/complete-upload
        ↓ artifact.status: pending -> available
(3) POST /source-artifacts/{artifact_id}/submit-input
        ↓ same-transaction: insert reading_record + original_input + bind artifact + enqueue extraction job
(4) reader-artifact-pipeline-worker (separate process)
        ↓ claim input_artifact_extraction job
(5) extraction worker: provider router (text / PDF / OCR adapter)
        ↓ on success: write original_inputs.source_text + enqueue extracted_artifact_materialization
(6) materialization worker: Input Suitability Gate
        ↓ low-impact: freeze -> article_ready + stable doc + base
        ↓ high-impact: candidate_base_ready + candidate row (needs user confirmation)
        ↓ rejected: action_required
(7) (future, NOT implemented) pending metadata TTL cleanup
```

**Single-file 当前边界**：

- 客户端每次 `init-upload` 只能上传一个对象；不支持 multi-file batch、zip 解压、并发多 artifact 合并到同一 record。
- `submit-input` 只接受 `available` 状态的 artifact；`pending` 状态会返回 `upload_pending` / `complete_upload` 提示。
- API submit 不同步解析文件：`submit_available_artifact_as_input` 在同一事务内只做 durable fact 写入（reading_record / original_input / bind / enqueue），不调用 provider。
- plain-text / Markdown 主链不经过此链路：直接走 `ArticleReadyPersistenceService` 形成 stable base。

### 状态机：pending / available 当前可达；failed / deleted schema-allowed 但无生产 writer

`source_artifacts.status` schema 允许四种值（migration 0001 §`source_artifacts_status_check` line 1445）：

```sql
CONSTRAINT source_artifacts_status_check CHECK ((status = ANY (ARRAY['pending'::text, 'available'::text, 'failed'::text, 'deleted'::text])))
```

| status | 当前生产 writer | 当前消费方 | 语义 |
|---|---|---|---|
| `pending` | `init-upload` 创建后默认值 | `artifact_input_status_query_service` 返回 `upload_pending` / `complete_upload` | 对象未确认上传完成 |
| `available` | `complete-upload` 原子推进 | `submit-input` 检查后绑定 | 对象已确认上传，可被 submit |
| `failed` | **无生产 writer**（schema-allowed） | `artifact_input_status_query_service` 返回 `extraction_failed` / `show_error` | 预留：extraction/materialization terminal failure 回写（当前未接通） |
| `deleted` | **无生产 writer**（schema-allowed，软删 `deleted_at IS NULL` 过滤） | `artifact_input_status_query_service` 提前过滤，不返回状态 | 预留：用户或 cleanup 软删（当前未接通） |

**重要事实**：`failed` 与 `deleted` 是 schema-allowed 但当前无生产 writer 的状态。`artifact_input_status_query_service.py` line 573 注释明确：`artifact.status == "deleted" cannot reach here because of the deleted_at IS NULL filter`。当前若出现 extraction/materialization terminal failure，失败事实记录在 `reader_jobs.failure_class` / `failure_code` / `failure_message`，**不**回写 `source_artifacts.status = 'failed'`；当前若出现孤立 pending row，按后台诊断处理，不手工伪造 `failed` / `deleted`。

### 重复 submit 锁与绑定语义（concurrency/duplicate fence，非 idempotent-success API）

`submit-input` 是 **concurrency/duplicate fence**，不是 idempotent-success API。首次 submit 成功后，重复 submit 不会返回原 record，而是返回 409。

- `_load_available_source_artifact_for_binding` 在事务内 `SELECT ... FOR UPDATE` 锁定 artifact 行（`source_artifacts` WHERE id=$1 AND user_id=$2 AND deleted_at IS NULL）。
- 锁定后检查 `status = 'available'`；再检查 `reading_record_id IS NOT NULL OR original_input_id IS NOT NULL`——**首次绑定不会把 status 从 available 改走**，只 SET `reading_record_id` / `original_input_id`；重复 submit 因已绑定字段返回 `ArtifactInputApplicationConflictError` -> HTTP 409（"source artifact is already bound to a reading_record/original_input"）。
- 同一事务内：插入 `reading_records` + `original_inputs` + `_bind_source_artifact_to_input`（UPDATE SET reading_record_id/original_input_id）+ `_enqueue_artifact_extraction_job`。
- **`reader_jobs.idempotency_key` 是独立列**；UNIQUE 约束 `uq_reader_jobs_run_idempotency UNIQUE (run_id, idempotency_key)`（migration 0001 line 2069）只在**单 run 内**生效，**不提供跨 run 去重**。
- **active fingerprint index** `uq_reader_jobs_active_fingerprint`（migration 0001 line 2384）只阻止活动语义 job 冲突（WHERE status IN ('queued','claimed','retry_later','paused')）；正常重复 submit 在 enqueue 前已被绑定检查阻断，不会到达 fingerprint index。
- 客户端重试 `submit-input` 必须处理 409，不能假设幂等返回原 record。

### TTL cleanup 与 complete-upload 原子竞争

`pending` metadata 的 TTL cleanup **尚未成为生产 writer**：

- 当前没有后台 worker 定期扫描 `status = 'pending'` 且 `created_at < now() - TTL` 的 artifact 行并改写为 `failed` / `deleted`。
- 未来引入 cleanup 时，**默认接入既有 artifact worker `_run_drain_cycle`**（`services/api/scripts/run_reader_artifact_pipeline_worker.py:272`），不新增独立 cleanup worker 进程。cleanup 逻辑作为 `_run_drain_cycle` 的一个 drain phase 或 pre-pipeline step 接入，复用既有 lease / heartbeat / retry 合同。
- cleanup 必须在**同一锁/事务边界**与 `complete-upload` 竞争验证：cleanup 持有 `SELECT FOR UPDATE` 时，并发 `complete-upload` 必须等待或返回 409，不能让 cleanup 把即将变成 `available` 的 artifact 错误标记为 `failed`。
- 当前若出现孤立 pending row（例如 OSS 上传中断、客户端崩溃），先保留证据并按后台诊断处理；不手工伪造 `failed` / `deleted`，不删除 OSS 对象。

### MIME / size boundary 的 Web/backend 差异

- Web 端 image MIME allowlist 与后端 `source_artifacts` schema **并不完全一致**：Web 校验在 `apps/web/src/app/(private)/app/read/use-content-check.ts` 周边，后端校验在 `submit-input` 路由的 `content_type` 解析。
- 25 MB 是 **Web 校验**而非端到端服务端合同：后端 `source_artifacts.byte_size` schema 允许 NULL，`complete-upload` 接受客户端报告的 `byte_size`，不做服务端 OSS HEAD 校验。
- 在 allowlist / 服务端 size boundary 收口前，**以实际入口校验为准**；不要把 Web 25 MB 写成后端硬上限。
- `submit-input` 期间 `_source_type_and_input_type_from_content_type` 根据 `content_type` 推断 `source_type`（`file` / `pdf` / `image`）和 `input_type`（`file_ref` / `image_ref`）；未知 MIME 走 `file` 兜底。

### PDF OCR rasterizer 仍为 UNKNOWN / Owner 决策

`artifact_pipeline_worker_service.py` §module docstring 明确 Deferred 项：

- **Real Aliyun OSS SDK network reads**：`AliyunOssObjectReader` 仍是 stub，不是生产路径。
- **PDF extraction**：当前 PDF text-layer 可用（如有），但 PDF→逐页 raster→OCR fallback 的 rasterizer（poppler / pdftoppm / mupdf / pdfjs）、页序合同、临时页对象清理、错误恢复策略**仍未选定**。
- **OCR / qwen-ocr**：image extraction 在 OCR 未配置时 terminal fail closed；provider router (`ArtifactExtractionProviderRouter`) 是 adapter，缺配置时走 `UnconfiguredArtifactExtractionProvider`，不写 fake 数据。
- 这些是 **UNKNOWN / OWNER 决策**，不阻塞 plain-text/Markdown 主链；本文不替 Owner 选定 rasterizer 或 OCR provider。

### provider=0 与 conditional launch scope

`artifact_pipeline_worker_service.py` 明确：

- 如果不注入 `extraction_provider=` / `storage_reader=` / `extraction_worker=` / `materialization_worker=`，extraction worker 使用 `UnconfiguredArtifactExtractionProvider`，**fail closed**：无网络、无 OCR、无 PDF 解析。
- `process_once` 每次只处理 **一个 job**（extraction OR materialization），不并发；调用方需反复调用 `process_once` drain pipeline。
- 服务**不直接写业务表**：所有写入委托给 existing workers；不新增 routes、不新增 queue tables、不新增 scheduler。
- conditional launch scope：artifact worker 是**条件必启**——只在上传文件时需要；plain-text / Markdown 主链不需要此 worker。`pnpm reader:workers` 默认启动 enhancement + artifact 两个 worker；`pnpm reader:worker:artifact` 单独启动 artifact worker。

### 排障顺序

1. 确认 artifact 状态与 owner/checksum：`SELECT id, status, user_id, content_sha256, source_filename, deleted_at FROM source_artifacts WHERE id = $1`。
2. 查 extraction / materialization jobs 的 claim、lease、failure code：`SELECT id, job_type, status, attempt_count, lease_owner, lease_expires_at, failure_class, failure_code, failure_message FROM reader_jobs WHERE target_key = $1 ORDER BY created_at`。
3. 检查 Candidate / Stable base 是否建立：`SELECT id, record_generation, status FROM stable_reading_documents WHERE reading_record_id = $1` + `SELECT id, status FROM candidate_reading_documents WHERE reading_record_id = $1`。
4. 对象存储、OCR/provider 缺配置只阻断 artifact 条件链，**不影响 plain-text/Markdown 主链**。

### 5. Schema health

worker / 真实 provider 链路 / snapshot 查询报 schema 缺失时：

```powershell
uv run python scripts/check_reader_schema_health.py          # 或 --json
```

显式验证 `ai_usage_events` / `user_credit_ledger` 的 D5 attribution columns 与 FK/index、`user_annotations` / `reader_notes` 的 D6 Reading Record anchor columns 与索引。脚本只诊断不自动改库。检查失败时，先执行 `infra/scripts/reset_full_keep_dict.sql` DROP 业务表，再重新执行当前唯一基线 `infra/migrations/0001_initial.sql`。详细操作与受保护数据边界见 `services/api/docs/database.md`。

## 提交与验证

### 路径 A：`/app/read` 产品链路

1. 启动 API、Web、enhancement worker 三个进程。
2. 打开 `http://127.0.0.1:3000/app/read`，粘贴英文文本提交。
3. 确认提交请求是 `POST /api/web/reader/records/input`；不应出现已删除的 `/api/web/analysis/submit` 或 `/api/web/reading-record/submit`。
4. 成功后跳转 `http://127.0.0.1:3000/app/reader/<recordId>`。
5. 期望：初始 `enhancement_progress` 显示 queued / processing；worker 发布 layer 后，Network 出现 `GET /api/web/reader/records/[recordId]/events` 返回 reload-required event，随后重新请求 snapshot；Plate 区出现译文、词汇 mark、语法 mark / sentence analysis。

### 路径 B：直接调后端 API

```powershell
$headers = @{ Authorization = "Bearer $($session.session_token)" }
$submit = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/reader/records/input" -Headers $headers -ContentType "application/json" -Body (@{
  source_type = "plain_text"
  text = "Claread lets developers inspect one reading record at a time."
} | ConvertTo-Json)
```

请求体对应 `ReaderUnifiedInputSubmitRequest`：必填 `source_type` / `text`，可选 `filename` / `source_metadata` / `client_record_id` / `language` / `reading_goal` / `reading_variant`。响应按 `outcome` 判别：

- `stable_document_ready`：返回 `reading_record_id` / `stable_document_id` / `base_id` / `record_generation` / `document_version` / `title` / `content_sha256` / `canonical_text_sha256` / `block_count` / `article_ready_event_id` / `article_ready_sequence` / `suitability` / `snapshot`。
- `candidate_document_required`：返回 `reading_record_id` / `candidate_document_id` / `record_generation` / `status` / `title` / `block_count` / `source_type` / `filename` / `original_input_id` / `suitability`。
- `input_rejected_or_action_required`：仅返回 `outcome` / `suitability`。

只有 `stable_document_ready` 分支才能使用 `article_ready_sequence` 作为观察 `reader_events` 的起点。

### 观察 events / snapshot

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/reader/records/$recordId/events?after_sequence=$after&limit=100" -Headers $headers
Invoke-RestMethod -Uri "http://127.0.0.1:8000/reader/records/$recordId/snapshot" -Headers $headers
```

关注：`last_event_sequence` / `reload_required` / `reload_reason` / `events[]`；snapshot 中 `enhancement_progress.overall_status` 与 `layers[]` 的 translation / vocabulary / grammar 状态。snapshot 从 DB facts 重建，不依赖旧 `render_scene_json`。

## 本地 DB 诊断：record 卡在"批注生成中"

先查四张 runtime truth 表：`reading_records`（product_state / readiness_state / generation / active_base_id）、`reader_jobs`（status / attempt_count / available_at / lease_owner / lease_expires_at / failure_class / failure_code / failure_message）、`reader_events`（sequence / event_type / payload_json）、`enhancement_layers`（layer_type / status / source_job_id）。

判断表：

| 现象 | 含义 | 下一步 |
|---|---|---|
| record 查不到 | record id、数据库或环境变量不一致 | 确认 Web/API 指向同一 `DATABASE_URL` |
| `input_artifact_extraction` 长期 `queued`、attempt 0、无 active_base | artifact worker 未运行 | `pnpm reader:worker:artifact`；页面可点"重新检查" |
| `article_ready` 后只有 `article_ready` event，jobs 停在 `queued`，layers 空 | enhancement worker 未运行或未消费队列 | `uv run reader-enhancement-worker --once` |
| jobs `claimed` 且 lease 未过期 | 正在真实 LLM 调用 | 看 worker 日志；长文本需要等待 |
| jobs `claimed` 但 lease 已过期 | 之前 worker 退出/卡死 | 再跑 `--once`，会先做 stale lease recovery |
| jobs `failed_terminal` | terminal fail-closed | 查 `failure_code` / `failure_message` |
| `failed_terminal` 且 code 是 `model_route_unavailable` / `vocabulary_executor_unconfigured` / `grammar_bundle_executor_unconfigured` | route/profile 缺失 | 检查 model profiles / presets / reader 三 profile env / provider key |
| jobs `retry_later` 且 `available_at` 在未来 | 暂时性失败 | 等 `available_at` 后再消费 |
| layer_published + layers `published` 但 Web 不更新 | Web/BFF polling 或 session 问题 | 刷新；查 BFF events/snapshot 响应 |
| `/app/reader/[recordId]` polling error 但 FastAPI `/events` 正常 | Web BFF session/base URL 问题 | 检查 `CLAREAD_FASTAPI_BASE_URL`、`CLAREAD_WEB_DEBUG_SESSION_TOKEN` |

最常见的本地误判：`article_ready` 已成功、`translate_unit` 已 queued，但缺 reader enhancement worker 进程——页面"批注生成中"只是 polling 等不到后续 events。

Events 诊断还需遵守 cursor 合同：`after_sequence=N` 只消费 `sequence>N` 的 committed events；响应被 limit 截断时 cursor 只推进到已处理的最后一条。gap、未知 payload、generation/base/source identity 不一致或 target 无法解析时，放弃局部状态并 reload snapshot。相同 snapshot 的 early return 不是 rejection；rejected snapshot 不得推进 accepted identity/cursor。当前没有 SSE/WebSocket 或可靠的 `projection_ops` 增量 applier。

## 常见 fail-closed / attention 情况

- missing profile / route：`vocabulary_executor_unconfigured` / `grammar_bundle_executor_unconfigured` / `model_route_unavailable`，表现为 `failed_terminal`，不会静默发布空 layer。
- schema validation failure：真实模型 structured output 不符合 schema 时按 terminal failure 处理，停在 `failed_terminal`。
- `retry_later`：暂时性失败，worker loop 尊重 `available_at`，不会热循环。
- `all_workers_no_job`：当前 record/base/generation 已无 claim 的 enhancement job，是正常停止态。
- `failed_terminal` 的产品状态：默认映射 `failed`；`action_required` 只允许来自显式 user-action allowlist；`publish_fence_failed`、profile/route 缺失等 system/config failure 不映射成 `action_required`；`retry_later`、`all_workers_no_job`、`max_ticks_reached`、`max_jobs_reached` 不改 `product_state`。

## Article RAG 运行

范围：`article_ready -> ArticleRagAutoEnsureService.ensure_in_transaction -> lifecycle ensure -> bootstrap -> reader_jobs.article_rag_index_build -> reader-article-rag-index-worker -> DashScope embed + Zilliz upsert -> index_run indexed -> retrieval -> Ask port -> production_stream`。不覆盖 grammar RAG（旧 1.x 链路）、跨记录资产 RAG、未确认 Candidate Document。

关键配置（`services/api/.env.example` 标准模板，真实 key 放 `.env`）：

```text
RAG_EMBEDDING_MODEL_PROFILE="rag-embedding-v4"
READER_ARTICLE_RAG_ENABLED=false
READER_ARTICLE_RAG_EMBEDDING_PROVIDER="dashscope"
READER_ARTICLE_RAG_EMBEDDING_MODEL=""
READER_ARTICLE_RAG_VECTOR_PROVIDER="zilliz"
READER_ARTICLE_RAG_ZILLIZ_URI=""
READER_ARTICLE_RAG_ZILLIZ_TOKEN=""
READER_ARTICLE_RAG_ZILLIZ_COLLECTION="article_rag_chunks"
READER_ARTICLE_RAG_VECTOR_DIM=1024
READER_ARTICLE_RAG_WORKER_POLL_INTERVAL_SECONDS=5
READER_ARTICLE_RAG_WORKER_LEASE_DURATION_SECONDS=120
READER_ARTICLE_RAG_SMOKE=false
```

- flag 关闭时 auto-ensure hook 是 no-op；不影响 `article_ready` 主流程。手动 GET status / POST ensure 不被 flag gate。
- Zilliz URI/token 可复用 `ZILLIZ_URI/TOKEN` fallback，但 collection 必须保持 Article RAG 专用，不能复用 few-shot collection。
- worker 无配置也永远能起来：provider 处于 unconfigured 时拿到 job 立刻 `failed_terminal`（`embedding_provider_unconfigured` / `vector_writer_unconfigured`），不写 fake 数据；index_run 失败不回滚 `readiness_state`。
- **Secret 红线**：`DASHSCOPE_API_KEY`、`BAILIAN_API_KEY`、`ZILLIZ_TOKEN`、`READER_ARTICLE_RAG_ZILLIZ_TOKEN` 不得出现在任何日志、HTTP detail、prompt sidecar、metadata repr；409 detail 为固定字符串，不 echo 异常。

本地默认是 **no-network dry-run**：`READER_ARTICLE_RAG_SMOKE` 是 opt-in，默认 `false`，不得把真实 provider smoke 写成默认路径。真实链路的唯一 acceptance 入口是 `test_article_rag_single_path_real_acceptance`；不要再用已退役的 smoke-collection namespace。

启动：`uv run reader-article-rag-index-worker`（或 `python -m scripts.run_reader_article_rag_index_worker`）；常用参数 `--poll-interval-seconds 5 --lease-duration-seconds 120 --lease-owner-prefix reader-article-rag-index-worker --max-ticks 100 --once`。

状态查询：

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/reader/records/$recordId/article-rag-index/status" -Headers $headers
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/reader/records/$recordId/article-rag-index/ensure" -Headers $headers -ContentType "application/json" -Body (@{ expected_generation = 3 } | ConvertTo-Json)
```

- GET status 取值：`indexed` / `indexing` / `queued` / `failed` / `superseded_or_stale` / `not_indexed` / `not_ready` / `unavailable`。`indexed` 时 Ask 可用 RAG 上下文；`not_ready` / `not_indexed` / `superseded_or_stale` / `unavailable` 时隐藏 RAG affordance，不阻塞阅读。
- POST ensure 取值：`enqueued` / `idempotent_noop` / `record_not_found` / `generation_mismatch` / `not_ready` / `no_active_base` / `plan_hash_mismatch` / `bootstrap_inconsistent` / `error`。body 不能带 `user_id` / `chunker_version` / provider / vector 配置（`extra="forbid"`，未知字段 422）。
- 失败码速查：`embedding_provider_unconfigured` / `vector_writer_unconfigured`（配置缺失，配好后重 POST ensure）；`retry_later`（可重试，尊重 `available_at`）；`superseded_or_stale`（重 POST ensure）；`retrieval_no_indexed_run`（POST ensure 等 worker）；其余 `failed_terminal` / `bootstrap_inconsistent` 报 ops。

### Reindex（运维显式入口）

`run_reader_article_rag_reindex.py` 是唯一的 reindex 入口：**默认 dry-run**（只读分类、零写入、零服务调用），写操作必须显式 `--execute`。

- `--record-id <uuid>` 与 `--all` 互斥且必选其一；`--all` 按「当前 active stable document 的**最新** Article RAG run（`created_at` 尝试顺序，同刻以 `id` 兜底）」三态分类：最新 `queued` / `indexing` / `planned` → 不进入候选；最新 `failed` / `superseded` → recovery 候选；最新 `indexed` → reindex 候选（dry-run 与 execute 使用同一 latest-run 候选集合，旧 run 后续的状态触碰不改变"最新尝试"判定）。
- `--limit N` 只允许与 `--all` 组合，在稳定排序后截断候选。
- `--rate-limit-per-second`（默认 1.0，`0` 关闭）只作用于 execute 迭代间隔。
- execute 每个 record 独立事务：单条失败不中断批次，summary 稳定输出 `scanned / eligible / enqueued / in_progress / skipped / failed`；恢复路径（最新 run 为 failed/superseded）计入 `eligible` 并产出 `recovery_enqueued`（服务返回 `recovery_enqueued` 状态，summary 计入 `enqueued`）。
- **无自动 rollback**：reindex 只翻转 PostgreSQL 状态（supersede + 重新 bootstrap）；新 build 失败后的恢复方式是**重跑 reindex**。
- 不提供 public route / scheduler / 自动触发；worker 不消费 reindex 意图。

## Ask 上下文压缩（Compaction）运行

- Feature flag：`READER_RECORD_ASK_MEMORY_ENABLED`（默认 false）；`services/api/.env` 设 `READER_RECORD_ASK_MEMORY_ENABLED=true` 后重启 API。
- 即使开启，短线程（≤20 对且 recent ≤40K 字符）不触发压缩（`not_needed`）；触发时才调用 compactor（固定 `ask-main-deepseek-v4-flash`、thinking 强制 disabled、无工具、结构化 `CompactionDraft` 输出）。
- 参数矩阵（代码权威值）：`MODEL_VISIBLE_TURN_PAYLOAD_CAP=128,000`、`RESERVE_RECENT_HISTORY=40,000`、`RESERVE_MEMORY=8,000`、`_RECENT_PAIRS=20`；9 个账户 `sum == CAP` 不变量。
- 事件顺序（生产流）：`agentic.run_started` -> `context.compaction.started` -> `context.compaction.completed|fallback|failed` -> 首个 `agentic.progress` -> `message.delta` -> `message.completed`。压缩 payload 只携带白名单 `detail_code`，不携带 provider 异常文本、transcript、query、URL。
- 回滚：关闭 flag 重启即回到零 memory 注入；migration 0028 为加性可空，无需回滚 DDL；单轮压缩失败走 deterministic emergency fallback，主答不中断。

## 边界

- 本文不是生产部署手册；生产 worker service / container 使用同一 entrypoint，但监控、扩缩容、密钥管理在部署层。
- 不新增 public worker-control endpoint；不把 worker 挂进 FastAPI lifespan；不把 Web submit 改成同步跑 LLM；不做旧本地 DB 自动兼容迁移；不把 fake executor 变成产品默认路径。
- D5-R4/R6 历史验证记录（真实 DashScope 短文本与 250+ 词长文本链路）只作为回看证据，不再作为当前可执行步骤。
- PydanticAI deprecation warnings 不阻塞链路但需单独清理；Boundary / Unit Builder v2（更细粒度逐句覆盖）仍是未收口工作。

## 相关文档

- `docs/architecture/reader-orchestration.md` — 架构、run/job、策略与失败语义。
- `docs/architecture/reader-rag.md` — Article RAG / grammar few-shot RAG 契约。
- `docs/architecture/ask-claread.md` / `docs/product/ask-claread.md` — Ask 运行时与产品边界。
- `docs/operations/langsmith.md` — LangSmith trace 规范与 `reader_runtime_spans` 双轨。
- `docs/operations/model-config.md` — model profile / preset 配置。
