# D5/D6 Real Local Chain Runbook

本文记录 Reader enhancement 主链路的本地真实运行方式：

`plain_text -> article_ready -> worker loop -> snapshot reload`

范围约束：

- 只使用现有 API、Web BFF 和 `reader-enhancement-worker` CLI entrypoint（底层复用 `scripts/run_reader_enhancement_worker.py`）
- 不新增 public worker-control endpoint
- 不把 runner 挂到 Web submit 或 FastAPI lifespan
- 不使用 smoke harness / fake executor 作为产品路径
- `ReaderPlateSnapshot` 是 projection，不读取旧 `render_scene_json`

截至 D5-R6，本文操作路径已用真实 DashScope provider 跑通短文本与 250+ 词长文本主链路；验证没有使用 smoke harness 或 fake executor。

补充：后端 smoke 只证明 API / worker / snapshot / events 的数据链路成立，不等价于浏览器端页面轮询与渲染层面的 E2E 验证。

## 0. 本地进程与页面边界

新 Reading Record / agentic orchestration 的页面内验证至少需要三个进程同时运行：

| 进程 | 作用 | 常用命令 |
| --- | --- | --- |
| API | 接收 submit、写入 `reading_records` / `reader_jobs` / `reader_events`，提供 snapshot/events API | `uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000` |
| Web | 提供 `/app/read`、`/app/reader-plate`、`/app/reader-record/{recordId}` 页面和 BFF | `pnpm web:dev` 或 `pnpm --dir apps/web run dev` |
| Reader enhancement worker | 消费 `reader_jobs`，发布 translation / vocabulary / grammar layers 和后续 reader events | `uv run reader-enhancement-worker` |

页面边界：

- `/app/reader-plate` 是验证页，用于直接观察 `ReaderPlateSnapshot`、events polling 和 snapshot reload。
- `/app/reader-record/{recordId}` 是新 Reading Record 产品页，使用 snapshot + Workbench-backed read-only center surface。
- `/app/reader/{recordId}` 是 legacy ReaderWorkbench，仍服务旧 scene / legacy record contract。

`/app/read` 或 `/app/reader-plate` 提交成功后，后端会先创建 durable `article_ready` facts；Web 后续只通过 events polling 和 snapshot reload 观察增强层。Web 不会自己消费 `reader_jobs`。如果没有启动 reader enhancement worker，页面会停留在“批注生成中”，这表示队列无人消费，不等于文章结构解析失败。

## 1. 关键 wiring

| 能力 | Model route | Prompt agent | 推荐 env | 当前 fail-closed 行为 |
| --- | --- | --- | --- | --- |
| translation | `reader_layer_translation` | `reader_layer_translation` | `READER_TRANSLATION_MODEL_PROFILE` | route/profile 不可用时，worker 以 terminal failure 结束，不发布空 layer |
| vocabulary | `reader_layer_vocabulary` | `reader_layer_vocabulary` | `READER_VOCABULARY_MODEL_PROFILE` | 未显式配置 profile 时抛出 `vocabulary_executor_unconfigured` |
| grammar bundle | `reader_layer_grammar_bundle` | `reader_layer_grammar_bundle` | `READER_GRAMMAR_BUNDLE_MODEL_PROFILE` | 未显式配置 profile 时抛出 `grammar_bundle_executor_unconfigured` |

补充说明：

- `MODEL_PROFILES_JSON` 与 `MODEL_PRESETS_JSON` 决定 route 可解析到哪些真实模型。
- translation route 在 registry 中可以 fallback 到 `ANNOTATION_MODEL_PROFILE`，但本地真实链路建议显式设置 `READER_TRANSLATION_MODEL_PROFILE`，避免与旧 annotation 配置混淆。
- grammar bundle worker 只走 `reader_layer_grammar_bundle` route，成功后发布 `grammar_note` 与 `sentence_analysis` 两个 layer。

建议先在 `services/api/.env` 或当前 shell 中准备下面这些变量：

```powershell
$env:MODEL_PROFILES_JSON = "config/model-profiles.json"
$env:MODEL_PRESETS_JSON = "config/model-presets.json"

$env:READER_TRANSLATION_MODEL_PROFILE = "workflow-qwen37-max"
$env:READER_VOCABULARY_MODEL_PROFILE = "workflow-qwen37-max"
$env:READER_GRAMMAR_BUNDLE_MODEL_PROFILE = "workflow-qwen37-max"

$env:DASHSCOPE_API_KEY = "<real provider key>"
```

如果你使用别的 provider profile，改成 `services/api/config/model-profiles.json` 中实际存在的 profile 名，并提供对应 API key。

可选 worker loop 配置：

```powershell
$env:READER_WORKER_SCAN_INTERVAL_SECONDS = "5"
$env:READER_WORKER_BATCH_SIZE = "10"
$env:READER_WORKER_MAX_TICKS = "24"
$env:READER_WORKER_MAX_JOBS = "24"
$env:READER_WORKER_LEASE_DURATION_SECONDS = "120"
$env:READER_WORKER_LEASE_OWNER_PREFIX = "reader-enhancement-worker"
```

当前默认 lease duration 是 120 秒。这个值比最早的 30 秒更保守，目的是降低长文本真实 LLM 处理中途触发 `LeaseExpiredError` 的概率；代价是 worker 异常退出后的 stale lease 最长可能多保留约 2 分钟，之后仍由现有 stale lease recovery 回收。

D5-R6 的 250+ 词真实 provider 验证显式使用了 `--lease-duration-seconds 240`。这证明长文本链路可跑通，但不代表 120 秒默认值已完成生产级 latency/cost 调参。

## 2. 启动 API

在 `services/api` 下：

```powershell
uv sync
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

本地手机号登录默认使用 mock provider，`services/api/.env.example` 中的默认值是：

```text
PHONE_AUTH_PROVIDER="mock"
PHONE_MOCK_VERIFICATION_CODE="888888"
```

这意味着本地可以先拿到真实 Claread session token，而不需要真实短信服务。

## 3. 获取本地 session token

### 路径 A：直接调用 FastAPI mock phone auth

```powershell
$phone = "13800138000"

Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/auth/phone/request-code" `
  -ContentType "application/json" `
  -Body (@{ phone = $phone } | ConvertTo-Json)

$session = Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/auth/phone/verify-code" `
  -ContentType "application/json" `
  -Body (@{ phone = $phone; code = "888888" } | ConvertTo-Json)
```

返回结果里包含：

- `user_id`
- `session_token`
- `expires_at`

### 路径 B：给 Web 注入 debug session

如果你想直接从 Web 页面提交，可以把上一步的 `session_token` 注入 Web dev 进程：

```powershell
$env:CLAREAD_WEB_DEBUG_SESSION_TOKEN = $session.session_token
```

这不是 fake submit。它只是把一个真实 FastAPI session token 写进 Web dev 环境变量，供 BFF 透传给后端。

## 4. 启动 Web

在仓库根目录：

```powershell
pnpm install
pnpm web:dev
```

或只启动 Web workspace：

```powershell
pnpm --dir apps/web run dev
```

常用 Web dev 环境变量：

```powershell
$env:CLAREAD_FASTAPI_BASE_URL = "http://127.0.0.1:8000"
$env:CLAREAD_PHONE_AUTH_PROVIDER = "mock"
$env:CLAREAD_WEB_DEBUG_SESSION_TOKEN = "<session_token>"
```

注意：

- Reader Plate BFF 拒绝 `anonymous` 和 `mock_phone` 会话。
- 允许的是完整 Web session，或开发期通过 `CLAREAD_WEB_DEBUG_SESSION_TOKEN` 注入的 debug session。

## 5. 启动 worker

在 `services/api` 下：

单次扫描并退出：

```powershell
uv run reader-enhancement-worker --once
```

持续 loop：

```powershell
uv run reader-enhancement-worker
```

常用参数：

```powershell
uv run reader-enhancement-worker `
  --scan-interval-seconds 5 `
  --batch-size 10 `
  --max-ticks 24 `
  --max-jobs 24 `
  --lease-duration-seconds 120 `
  --lease-owner-prefix reader-enhancement-worker
```

该 CLI entrypoint 由 `services/api/pyproject.toml` 注册到 `scripts.run_reader_enhancement_worker:main`。如果需要核对底层实现或直接执行模块，仍然可以查看 `services/api/scripts/run_reader_enhancement_worker.py`，但本地/部署运行命令以 `uv run reader-enhancement-worker ...` 为准。

`--once` 会输出 JSON summary。当前最有用的字段是：

- `scanned_candidate_count`
- `processed_count`
- `lock_skipped_count`
- `results[].record_id`
- `results[].pipeline_summary.bootstrapped_job_counts`
- `results[].pipeline_summary.worker_tick_counts`
- `results[].pipeline_summary.outcome_counts`
- `results[].pipeline_summary.last_event_sequence`
- `results[].pipeline_summary.snapshot_reload_recommended`
- `results[].pipeline_summary.stopped_reason`

## 5.1 检查本地 D5/D6 schema health

如果 worker、真实 provider 链路，或 `/app/reader-record/{recordId}` snapshot 查询报 schema 缺失，先跑：

```powershell
uv run python scripts/check_reader_schema_health.py
```

只想看机器可读输出时：

```powershell
uv run python scripts/check_reader_schema_health.py --json
```

这个检查会显式验证：

- `ai_usage_events` 的 D5 attribution columns
- `ai_usage_events` 的 reader attribution FK / index
- `user_credit_ledger` 的 D5 attribution columns
- `user_credit_ledger` 的 reader attribution FK / index
- `user_annotations` 的 D6 Reading Record anchor columns：
  `reading_record_id`、`base_id`、`generation`、`unit_id`、`anchor_segment_id`、`unit_start_utf16`、`unit_end_utf16`
- `reader_notes` 的同一组 D6 Reading Record anchor columns
- `reader_notes.analysis_record_id` 和 `reader_notes.anchor_sentence_id` 必须允许 `NULL`
- `user_annotations` 的 `idx_user_annotations_reading_record` / `uq_user_annotations_reading_record_anchor`
- `reader_notes` 的 `idx_reader_notes_reading_record` / `uq_reader_notes_reading_record_anchor`
- `user_annotations_text_anchor_payload_check` 必须包含 Reading Record path，即允许 `anchor_type = 'text_range'` 时使用 `reading_record_id IS NOT NULL` 且 `analysis_record_id IS NULL`

如果 D5 attribution 检查失败，当前正确处理方式是重置或重建本地 DB，然后重新应用 `infra/migrations/0001_initial_schema.sql`。D5-R5 没有增加“旧库自动迁移兼容层”。

如果 D6 user asset schema 检查失败，脚本会明确提示需要执行 `infra/migrations/0002_reader_record_anchor_columns.sql`。脚本只做诊断，不会自动修改数据库。

## 5.2 旧 volume 缺 D6 0002 migration 的处理方式

D6-U4 新增了 `infra/migrations/0002_reader_record_anchor_columns.sql`，用于给 `user_annotations` 和 `reader_notes` 增加 Reading Record anchor columns。`infra/docker/docker-compose.local.yml` 已把该 migration 挂进 `/docker-entrypoint-initdb.d/`，因此新建 Postgres volume 时会自动执行。

如果你复用的是旧的 `claread_postgres_data` volume，Docker entrypoint 不会重新执行新增 migration。此时打开 `/app/reader-record/{recordId}` 可能在 snapshot 查询阶段报：

```text
asyncpg.exceptions.UndefinedColumnError: column ua.reading_record_id does not exist
```

保留现有数据并补一次 migration：

```powershell
docker cp .\infra\migrations\0002_reader_record_anchor_columns.sql claread-postgres:/tmp/0002_reader_record_anchor_columns.sql
docker exec claread-postgres psql -v ON_ERROR_STOP=1 -U claread -d claread -f /tmp/0002_reader_record_anchor_columns.sql
```

如果本地业务数据可丢弃，也可以重建 Postgres volume；重建后 compose 会按顺序执行 `0001_initial_schema.sql`、`0002_reader_record_anchor_columns.sql` 和 Eval Center `0001`。

## 6. 页面内提交、recordId 获取与产品页验证

### 路径 A：从 `/app/read` 验证新产品链路

D6-E3 的推荐本地验证路径是产品路径，不是验证页：

1. 确保 API、Web、reader enhancement worker 三个进程都已启动：
   - API：`uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`
   - Web：`pnpm web:dev` 或 `pnpm --dir apps/web run dev`
   - Worker：`uv run reader-enhancement-worker`，或先用 `uv run reader-enhancement-worker --once` 做单次诊断
2. 打开 `http://127.0.0.1:3000/app/read`。
3. 粘贴英文文本并提交。
4. 用浏览器 DevTools Network 或 Web dev server log 确认提交请求是 `POST /api/web/reading-record/submit`。
5. 不应出现默认产品提交到 `POST /api/web/analysis/submit`；如果出现，说明 submit mode 或环境不是当前 new Reading Record 路径。
6. 成功后页面应跳转到：

```text
http://127.0.0.1:3000/app/reader-record/<recordId>
```

URL path 最后一段就是新 `Reading Record.record_id`。在 PowerShell 中可用：

```powershell
$readerUrl = "http://127.0.0.1:3000/app/reader-record/<recordId>"
$recordId = ([uri]$readerUrl).Segments[-1].TrimEnd("/")
```

页面期望：

- 初始进入 `/app/reader-record/{recordId}` 时，旧 Workbench shell 与 Plate read-only 中心正文可见。
- header 内的 `enhancement_progress` 状态条可能先显示 queued / processing。
- Worker 发布 layer 后，浏览器 Network 应看到 `/api/web/reader-plate/{recordId}/events` 返回 reload-required event，随后重新请求 `/api/web/reader-plate/{recordId}/snapshot`。
- 新 snapshot 到达后，中心 Plate 区应出现已发布的译文、词汇 mark、语法 mark / sentence analysis，progress 摘要随 snapshot 更新。

如果浏览器跳转失败或你稍后回到 `/app/read`，Web-only recent recovery 会提供最近 Reading Record 的“继续阅读”入口。它只是本地/前端恢复入口，不是长期 domain truth。

### 路径 B：从 `/app/reader-plate` 验证 snapshot

`/app/reader-plate` 仍是验证页，适合直接观察 raw-ish `ReaderPlateSnapshot`、events polling 和 snapshot reload。

从验证页提交：

1. 确保 Web 进程已经拿到完整 session 或 `CLAREAD_WEB_DEBUG_SESSION_TOKEN`
2. 打开 `http://127.0.0.1:3000/app/reader-plate`
3. 粘贴英文文本，填写可选标题后提交
4. 成功后页面 URL 会切到 `?record_id=<uuid>`

也可以直接打开已有 record：

```text
http://127.0.0.1:3000/app/reader-plate?record_id=<record_id>
```

### 路径 C：直接调后端 API

```powershell
$headers = @{
  Authorization = "Bearer $($session.session_token)"
}

$submit = Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8000/reader/records/plain-text" `
  -Headers $headers `
  -ContentType "application/json" `
  -Body (@{
    plain_text = "Claread lets developers inspect one reading record at a time."
    title = "D5 Local Chain Demo"
    language = "en"
    client_record_id = "local-runbook-demo-001"
  } | ConvertTo-Json)
```

成功返回：

- `record_id`
- `base_id`
- `article_ready_sequence`
- `snapshot`

`article_ready_sequence` 是后续观察 `reader_events` 的起点。

## 7. 观察 reader_events、snapshot reload 和 worker summary

### 轮询 reader events

```powershell
$recordId = $submit.record_id
$after = $submit.article_ready_sequence

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/reader/records/$recordId/events?after_sequence=$after&limit=100" `
  -Headers $headers
```

关注这些字段：

- `last_event_sequence`
- `reload_required`
- `reload_reason`
- `events[]`

当 enhancement worker 发布 layer 后，`last_event_sequence` 应继续前进。

### 重新加载 snapshot

```powershell
$snapshot = Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/reader/records/$recordId/snapshot" `
  -Headers $headers
```

期待看到：

- source/base projection
- translation layer
- vocabulary layer
- grammar bundle 对应的 `grammar_note` 与 `sentence_analysis`
- `enhancement_progress.overall_status`
- `enhancement_progress.layers[]` 中 translation / vocabulary / grammar 的 job/layer 状态

这个 snapshot 是从 DB facts 重建的 projection，不依赖旧 `render_scene_json`。

只看 progress：

```powershell
$snapshot.enhancement_progress | ConvertTo-Json -Depth 8
$snapshot.enhancement_progress.layers |
  Format-Table capability, layer_type, status, job_status, job_id, layer_id, target_key
```

### 从 Web 观察 reload

- `/app/reader-record/{recordId}` 和 `/app/reader-plate` 都会通过现有 BFF 调 `/api/web/reader-plate/{recordId}/snapshot` 和 `/api/web/reader-plate/{recordId}/events`
- worker loop 推进后，页面应通过已有 polling/reload 看到新 layers
- 不存在单独的 public worker-control route

### 本地 DB 诊断：record 卡在“批注生成中”

给定一个新 Reading Record id，先查四张 runtime truth 表：

```powershell
$recordId = "<record_id>"

psql "$env:DATABASE_URL" -c "
SELECT
  id,
  title,
  source_type,
  product_state,
  readiness_state,
  generation,
  active_base_id,
  created_at,
  updated_at
FROM reading_records
WHERE id = '$recordId'::uuid;
"

psql "$env:DATABASE_URL" -c "
SELECT
  job_type,
  target_type,
  target_key,
  status,
  attempt_count,
  available_at,
  lease_owner,
  lease_expires_at,
  failure_class,
  failure_code,
  failure_message,
  updated_at
FROM reader_jobs
WHERE reading_record_id = '$recordId'::uuid
ORDER BY created_at ASC;
"

psql "$env:DATABASE_URL" -c "
SELECT
  sequence,
  event_type,
  source_job_id,
  source_layer_id,
  created_at,
  payload_json
FROM reader_events
WHERE reading_record_id = '$recordId'::uuid
ORDER BY sequence ASC;
"

psql "$env:DATABASE_URL" -c "
SELECT
  layer_type,
  layer_subtype,
  target_scope,
  target_key,
  status,
  source_job_id,
  published_at,
  created_at
FROM enhancement_layers
WHERE reading_record_id = '$recordId'::uuid
ORDER BY created_at ASC;
"
```

判断表：

| 现象 | 含义 | 下一步 |
| --- | --- | --- |
| `reading_records` 查不到 record | record id、数据库或环境变量不一致 | 确认 Web/API 指向同一个 `DATABASE_URL`，并确认使用的是新 Reading Record id |
| `readiness_state = article_ready`，`reader_events` 只有 `article_ready`，`reader_jobs` 有 `translate_unit` 或后续 jobs 停在 `queued`，`enhancement_layers` 为空 | 文章结构已经落库；worker 未运行或未消费队列 | 在 `services/api` 运行 `uv run reader-enhancement-worker --once` 验证一次，或运行 `uv run reader-enhancement-worker` 持续消费 |
| jobs 处于 `claimed` 且 `lease_expires_at` 未过期 | worker 已 claim，可能正在真实 LLM 调用中 | 看 worker 终端日志；长文本可能需要等待 |
| jobs 处于 `claimed` 但 `lease_expires_at` 已过期 | 之前的 worker 退出或卡死，lease 等待恢复 | 再运行 `uv run reader-enhancement-worker --once`，worker 会先做 stale lease recovery |
| jobs 为 `failed_terminal` | worker 已消费但 terminal fail-closed | 查 `failure_code` / `failure_message`，常见为 profile/route/schema/publish fence 问题 |
| jobs 为 `failed_terminal` 且 `failure_code` 是 `model_route_unavailable`、`vocabulary_executor_unconfigured` 或 `grammar_bundle_executor_unconfigured` | model route / profile 缺失或未配置 | 检查 `MODEL_PROFILES_JSON`、`MODEL_PRESETS_JSON`、`READER_TRANSLATION_MODEL_PROFILE`、`READER_VOCABULARY_MODEL_PROFILE`、`READER_GRAMMAR_BUNDLE_MODEL_PROFILE` 和 provider API key |
| jobs 为 `retry_later` 且 `available_at` 在未来 | 暂时性失败，当前不应热循环 | 等到 `available_at` 后再让 worker 消费，或检查 provider/network 问题 |
| `layer_published` events 和 `enhancement_layers.status = published` 已存在，但 Web 仍不更新 | worker 已发布，问题转向 Web/BFF polling 或 session | 刷新页面；再查 `/api/web/reader-plate/{recordId}/events` 和 `/snapshot` BFF 响应 |
| `/app/reader-record/{recordId}` 出现 Web polling error，但 FastAPI `/events` 正常 | Web BFF session、base URL 或 dev server 问题 | 检查 `CLAREAD_FASTAPI_BASE_URL`、`CLAREAD_WEB_DEBUG_SESSION_TOKEN`、浏览器 cookie/session，并确认 Web dev server 指向同一个 API |

最常见的本地误判是第二行：`article_ready` 已经成功，`translate_unit` job 也已 queued，但没有 reader enhancement worker 进程。此时页面“批注生成中”只是 polling 等不到后续 events。

## 8. 常见 fail-closed / attention 情况

### missing profile / route

- vocabulary 未配置 `READER_VOCABULARY_MODEL_PROFILE` 时，会得到 `vocabulary_executor_unconfigured`
- grammar bundle 未配置 `READER_GRAMMAR_BUNDLE_MODEL_PROFILE` 时，会得到 `grammar_bundle_executor_unconfigured`
- translation route/profile 不可解析时，会得到 `model_route_unavailable`

这些都会表现为 worker summary 中的 `failed_terminal`，不会静默发布空 layer。

### schema validation failure

如果真实模型返回的 structured output 不符合 schema，worker 会按 terminal failure 处理。当前正确行为是停在 `failed_terminal`，而不是发布半成品 layer。

### retry_later

`retry_later` 表示暂时性失败。worker loop 会尊重 job `available_at`，不会为同一条记录热循环重试。

### all_workers_no_job

`stopped_reason = "all_workers_no_job"` 通常表示当前 record/base/generation 下已经没有可 claim 的 enhancement job。这是正常停止态，不代表错误。

### failed_terminal 的产品状态

D6-P0 起，worker loop 会通过保守 classifier 更新 `reading_records.product_state`：

- `failed_terminal` 默认映射到 `failed`
- `action_required` 只允许来自显式 user-action allowlist
- `publish_fence_failed`、`model_route_unavailable`、`vocabulary_executor_unconfigured`、`grammar_bundle_executor_unconfigured` 等 system/config failure 不会映射成 `action_required`
- `retry_later`、`all_workers_no_job`、`max_ticks_reached`、`max_jobs_reached` 继续不改 `product_state`

## 9. D5-R4 / D5-R6 实际验证到哪里

已验证：

- `settings.py` 中 reader worker / model profile settings 存在
- LLM routes / registry / prompts 的 translation、vocabulary、grammar bundle wiring 存在
- `uv run reader-enhancement-worker --help` 可以正常输出 CLI help
- 真实 DashScope `workflow-qwen37-max` provider 下，短文本 `plain_text -> article_ready -> worker loop -> snapshot reload` 端到端跑通。
- Worker 自动 bootstrap vocabulary / grammar bundle jobs，并发布 translation、vocabulary、grammar_note 三类 layer。
- Snapshot projection 当前出现 group-native `reader_translation_group` node、`reader_vocabulary_marks` 和 `reader_grammar_note_marks`。
- Reader events 从 `article_ready` 推进到 `layer_published` / `parsed_decision_updated`，sequence 严格递增。
- 第二次 worker `--once` 扫描为空，证明当前 record/base/generation 不重复 publish。
- 真实 DashScope `workflow-qwen37-max` provider 下，250+ 词长文本也已完成 `plain_text -> article_ready -> worker loop -> snapshot reload -> Web render`。
- 长文本验证 record `34476538-c091-43ef-a395-009de7633a68` 的 FastAPI snapshot 和 Web BFF snapshot 均包含 translation、vocabulary、grammar_note、sentence_analysis。
- 同一 snapshot 中 `parsed_decisions=1`，`snapshot.value` 出现 2 个 `reader_sentence_analysis` nodes，且不包含旧 `render_scene_json`。
- 浏览器实渲染确认 `/app/reader-plate?record_id=34476538-c091-43ef-a395-009de7633a68` 出现 2 张 sentence analysis 卡片。

尚未完整收口：

- PydanticAI deprecation warnings：当前不阻塞链路，但需要单独清理，避免后续依赖升级时变成 breakage。
- Boundary / Unit Builder v2：R6 长文本仍只切出 1 个 `reader_unit`，`boundary_quality=low`；如果产品需要更细粒度逐句覆盖，需独立评估 unit aggregation、boundary refiner 和 sentence_analysis coverage policy。
- 本地旧数据库：如果出现 `ai_usage_events.reading_record_id` 等列缺失，通常是本地 DB schema 早于当前 fresh baseline，需要刷新/重建本地 DB；当前仓库 `0001_initial_schema.sql` 已包含这些列和 FK。

因此，本文既是 D5 本地真实链路的正式操作手册，也是 D5-R4/R6 已实跑路径的配置对照；它不代表生产部署、输出质量调优、Boundary / Unit Builder v2、Ask/toolbar/点词查询或旧本地 DB 自动迁移已经完成。

## 10. D5-R5 运行态硬化补充

已补充：

- `reader_worker_lease_duration_seconds` setting 和 worker CLI `--lease-duration-seconds`
- dev/admin schema check：`scripts/check_reader_schema_health.py`
- `infra/scripts/check_schema_baseline.sql` 对 `ai_usage_events` / `user_credit_ledger` 的 D5 attribution columns、FK 和 index 额外校验

仍未扩张：

- 不新增 public endpoint
- 不把 worker 放进 FastAPI lifespan
- 不把 Web submit 同步改成跑 LLM
- 不做旧本地 DB 的自动兼容迁移
- 不把 fake executor 变成产品默认路径
