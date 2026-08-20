# Daily Reader 后端说明

> 状态: `CURRENT` | 最后验证: 2026-08-20

本文记录 Daily Reader 当前后端事实，包括 workflow 结构、API/数据契约、已收口的优化，以及后续仍需处理的工程债。

## 模块边界

Daily Reader 属于 `services/api/` 通用后端能力，不是 Web 或小程序的专属逻辑。Web、小程序和后续客户端共享同一套 `daily_readers` 数据、公开 API 和 workflow 输出。

核心入口：

- 公开读取：`GET /daily-reader/today`、`GET /daily-reader`、`GET /daily-reader/{article_id}`
- 管理触发：`POST /daily-reader/admin/generate`
- 日审队列：`GET /daily-reader/admin/review-queue`（Claread console 预留）
- 草稿轻量编辑：`PATCH /daily-reader/admin/{article_id}`（Claread console 预留）
- 管理重跑：`POST /daily-reader/admin/retry`（完成后强制 `status='draft'`，已发布文章立即从读者端消失）
- 管理发布 / 下架：`POST /daily-reader/admin/publish`、`POST /daily-reader/admin/unpublish`（请求体必填 `operator`，缺省 422）

公开详情接口只返回 `status='published'` 的文章；管理端重跑使用 any-status 查询以保留草稿修复能力。

## Claread console 预留接口

本节仅定义后端契约，不包含 Console UI。所有接口继续使用
`x-admin-api-key`；缺少 header 返回 422，错误 key 返回 401。

### 日审队列

```http
GET /daily-reader/admin/review-queue?limit=20&offset=0
x-admin-api-key: ...
```

- `limit`: 1-100，默认 20。
- `offset`: 非负整数，默认 0。
- 只返回 `status='draft' AND review_status='pending'`，按
  `created_at DESC, id DESC` 排序；多取一行计算 `has_more`。
- 返回中文标题/副标题、英文原题/来源摘要、来源、难度、标签、候选评分、
  封面 URL/候选/已选信息及审核字段。
- `machine_flags.cover_quality`：
  - `qualified`: 当前封面 URL 与已持久化的像素校验后选中封面一致；
  - `missing`: 没有封面 URL，前端应展示 editorial fallback；
  - `unavailable`: 有 URL，但没有可与之对应的已存尺寸证据（旧行或人工 URL）。
- `machine_flags.boilerplate_*` 只扫描已存 `body/highlights/paragraph_notes/takeaways`
  JSON，复用 pipeline 的确定性 dirty-data 规则；GET 不调用 LLM 或网络。
- `selection_score` 来自 `daily_readers.score`。当前 workflow 的最终 review
  分数没有持久化，因此 `review_score=null` 且
  `review_score_available=false`，不得把候选评分冒充 review 分。

### 草稿轻量编辑

```http
PATCH /daily-reader/admin/{article_id}
x-admin-api-key: ...
Content-Type: application/json

{
  "title": "中文主标题",
  "subtitle_zh": "中文副标题",
  "cover_image_url": "https://cdn.example/cover.webp",
  "tags": ["科技", "社会"]
}
```

- 白名单仅为 `title`、`subtitle_zh`、`cover_image_url`、`tags`；未知字段、
  空 body、空标题、非法 URL 或非法标签返回 422。
- `subtitle_zh=null`、`cover_image_url=null` 可清空对应可空字段；`tags=[]`
  可清空标签。
- 只允许编辑 draft。不存在返回 404；published/archived 返回 409。
- 只有值实际变化时才写库，并原子重置
  `review_status='pending'`、`reviewed_by=NULL`、`reviewed_at=NULL`；无变化返回
  `status='unchanged'`，不会清除既有审核记录。

### 单篇触发

`POST /daily-reader/admin/generate` 请求体新增 `single`（默认 `false`）。
`single=true` 时只把现有 pipeline 的真实 `max_count` seam 强制为 1；不接受
source URL，也不承诺指定某一候选。原有 `{}`、`max_count` 和 `force` 调用保持兼容。

```json
{"single": true, "force": false}
```

publish/unpublish 契约不变：请求体必须有去除首尾空白后非空的 `operator`；
publish 在同一条 UPDATE 中写入 `approved/reviewed_by/reviewed_at`。B-4 复用已有
三列，没有新增迁移。

## 当前数据契约

`daily_readers` 当前主要保存：

- `body_json`: 面向客户端展示的正文结构，正文数组继续使用 `paragraphs` 字段名；当前语义已经是 reading unit。
- `highlights_json`: 词汇/表达高亮，`paragraph_id` 指向 reading unit id。
- `paragraph_notes_json`: 段落透读与译文结构，`paragraph_id` 指向 reading unit id。
- `takeaways_json`: 文末收束，包括中文编辑标题、副标题、中文标签、讨论问题和写作借鉴。
- `original_title` / `subtitle_zh`: 分别保存英文源标题与中文编辑副标题；对旧行保持可空兼容。
- `review_status` / `reviewed_by` / `reviewed_at`: 发布审核状态与操作审计；发布、下架请求的 `operator` 会去除首尾空白并拒绝空值。
- `cover_image_url`: 像素门槛与 LLM 候选选择后落地的本地或 OSS 封面 URL。

为保持兼容，外部字段名仍沿用 `paragraphs`、`paragraph_id`、`paragraph_notes`。代码和 prompt 中的新语义按 reading unit 处理。

## Reading Unit 结构

Daily Reader 不再把抓取文本的原始换行直接当成页面段落。当前 workflow 使用 `raw_blocks -> reading_units` 管道：

1. `_split_into_raw_blocks`
   - 双换行切 section。
   - section 内单换行切 raw block。
   - 超长无换行文本按句子兜底拆分。
2. `_classify_raw_blocks`
   - 与标题精确匹配的 block 标为 `title_duplicate`。
   - 独占 section、短于 `SECTION_HEADING_MAX_CHARS`、非句末标点结尾的 block 可标为 `section_heading`。
   - 其余为 `content`。
3. `_plan_reading_units`
   - 过滤 `title_duplicate`。
   - `section_heading` 作为分组边界，不作为正文输出。
   - 对短 group 做相邻合并。
4. `_merge_content_blocks_into_units`
   - 按 `READING_UNIT_TARGET_CHARS = 520` 形成展示级 reading unit。
   - 对低于 `READING_UNIT_MIN_CHARS = 260` 的短 unit 做二次合并。
   - 保持 `MAX_PARAGRAPH_CHARS` 作为上限兜底。

当前真实样本验证：

| 样本 | 优化前 | 优化后 | 结果 |
|------|--------|--------|------|
| Hurricanes | 18 units，7 个 < 220 chars | 9 units，0 个 < 220 chars | 达标 |
| Meta lawsuit | 14 units，3 个 < 220 chars | 11 units，0 个 < 220 chars | 达标 |

## Prompt 与质量规则

当前 prompt/review/refinement 已对齐 reading unit 语义：

- 短过渡 unit 不强制高亮或 note。
- `MIN_REQUIRED_HIGHLIGHT_CHARS = 120`。
- review 使用 `unit_coherence`、`heading_handling`、`note_density` 等维度，而不是逐段补齐。
- refinement 不再为覆盖率机械补高亮/补 note。

`writing_moves` 当前语义为“写作借鉴”：

- 数量允许 `0-2` 个。
- `move_type` 是面向用户的中文短标签，不是修辞术语。
- `reusable_pattern` 是“可借句式”，为空时客户端不应占位展示。
- Web 和小程序展示文案均使用“写作借鉴 / 可借句式”。

## 分页与日期语义

Daily Reader 列表使用复合 cursor，避免同一天多篇文章跳项：

- 排序：`publish_date DESC, id DESC`
- cursor 格式：`YYYY-MM-DD|article_id`
- 兼容旧版纯日期 cursor。

`publish_date` 使用 UTC+8 业务日期。今日文章查询和 workflow payload 组装均通过业务日期函数计算，不依赖服务器本地 `date.today()`。

## 运维：每日触发与告警

本节是运维步骤，不是产品功能。调度用系统 cron / schtasks，不引入调度框架。

### 触发脚本

`infra/scripts/trigger_daily_reader_generate.ps1` 调用 `POST /daily-reader/admin/generate`。

环境变量：

| 变量 | 必填 | 说明 |
|------|------|------|
| `DAILY_READER_ADMIN_API_KEY` | 是（非 dry-run） | 与 API `x-admin-api-key` 相同 |
| `CLAREAD_API_BASE_URL` | 否 | 默认 `http://127.0.0.1:8000` |

```powershell
pwsh -File infra/scripts/trigger_daily_reader_generate.ps1 -DryRun
pwsh -File infra/scripts/trigger_daily_reader_generate.ps1
```

`-DryRun` 只打印 URL / 是否已设置 key / 请求体，不发 HTTP。

### crontab（Linux / macOS，错峰 06:23）

```cron
23 6 * * * CLAREAD_API_BASE_URL=http://127.0.0.1:8000 DAILY_READER_ADMIN_API_KEY=... /usr/bin/pwsh -File /opt/claread/infra/scripts/trigger_daily_reader_generate.ps1
```

无 pwsh 时可用 curl 等价行：

```cron
23 6 * * * curl -fsS -X POST "$CLAREAD_API_BASE_URL/daily-reader/admin/generate" -H "x-admin-api-key: $DAILY_READER_ADMIN_API_KEY" -H "Content-Type: application/json" -d '{"force":false,"max_count":3}'
```

时区按宿主机本地时区配置；业务日是 UTC+8，建议把机器时区设为 `Asia/Shanghai`。

### Windows schtasks

```powershell
schtasks /Create /TN "ClareadDailyReaderGenerate" /SC DAILY /ST 06:23 /RU SYSTEM /TR "pwsh -NoProfile -File C:\claread\infra\scripts\trigger_daily_reader_generate.ps1"
```

计划任务进程需要能读到 `DAILY_READER_ADMIN_API_KEY` 与可选 `CLAREAD_API_BASE_URL`（系统环境变量或在 `/TR` 里用 `pwsh -Command` 先 `$env:...=`）。

### 告警

pipeline 正常结束后，若命中任一条件，写 ERROR 日志；若配置了 `DAILY_READER_ALERT_WEBHOOK_URL`，再 POST JSON（`run_id` + `reasons`）。未配置 webhook 时只打日志。

| `reasons` | 条件 |
|-----------|------|
| `zero_output` | 0 篇进入 draft |
| `workflow_failure` | 至少一篇 workflow 抛错 |
| `all_candidates_filtered` | 有候选，但评分通过数为 0（含提取拒收后无人可评） |

### 已有库升级 review 审计列

`infra/migrations/` 仍只有 `0001_initial.sql`（compose 单一基线）。新列已写入 0001。已有 volume 不要 reset 时可执行：

```powershell
psql -v ON_ERROR_STOP=1 -f infra/scripts/alter_daily_readers_review_audit.sql
```

回滚：

```powershell
psql -v ON_ERROR_STOP=1 -f infra/scripts/alter_daily_readers_review_audit_down.sql
```

旧已发布行回填 `review_status='approved'`、`reviewed_by='legacy'`。B-4 console 预留接口复用这三列，不要再写一份迁移。

## 验证入口

核心测试：

```powershell
rtk test uv run pytest services/api/tests -q
```

验收以上述命令的当前运行结果为准。

真实数据验证时，`run_workflow_only` 会 `UPDATE` 解析 JSON，并把 `status` 重置为 `draft`（同时 `published_at=NULL`、`review_status='pending'`）。重跑前应先导出目标文章的 `body_json`、`paragraph_notes_json`、`highlights_json` 和 `takeaways_json` 快照，避免覆盖旧输出后无法比较。已发布文章 retry 后需重新 publish。

## 后续收口清单

以下问题当前尚未解决：

1. 单一真源
   - 当前 `body_json.paragraphs[].reading_note` 与 `paragraph_notes_json.notes[]` 仍可能形成双源。
   - 后续应明确 `paragraph_notes_json` 为结构化真相源，`body_json` 只保留正文投影，或反向只保留一种来源。

2. note 与 translation 解耦
   - 当前 `ParagraphReadingNote` 同时承载透读问题、摘要和译文。
   - 如果未来要让部分 unit 没有透读 note，但仍保留译文，需要先拆出 translation 的稳定来源。

3. Web / 小程序 DTO 重复维护
   - 两端仍各自维护 Daily Reader DTO 和 adapter。
   - 后续应通过 `packages/contracts` 或 OpenAPI 生成收敛跨端契约。

4. `footer_analysis_json` 兼容影子
   - 表结构和部分客户端仍存在旧兼容字段。
   - 后续应确认无线上依赖后删除或降级为历史迁移字段。

5. 封面策略
   - 当前候选先做至少 1200 像素的尺寸门槛，再通过 `daily_cover` 模型路由选择；模型失败时回退到排序第一的合格候选。
   - 选中图片按 `DAILY_READER_COVER_STORAGE_BACKEND=local|oss` 落地。生产仍需验证多模态 profile、派生尺寸与 CDN 配置。
   - `content_security.py` 与 `content_sec_check` 占位写入已删除；列保留为 DEPRECATED，pipeline 不再写入。

6. 长文 takeaways 上下文
   - close reading takeaways 已接收完整段译，不再使用 1500 字符 notes summary 截断；句译严格复用对应段译片段。
   - 真实 provider 的长文上下文成本、质量与 route/profile 拆分仍需在 A-5 验收。
