# Daily Reader 后端说明

> 状态: `CURRENT` | 最后验证: 2026-05-30

本文记录 Daily Reader 当前后端事实，包括 workflow 结构、API/数据契约、已收口的优化，以及后续仍需处理的工程债。

## 模块边界

Daily Reader 属于 `services/api/` 通用后端能力，不是 Web 或小程序的专属逻辑。Web、小程序和后续客户端共享同一套 `daily_readers` 数据、公开 API 和 workflow 输出。

核心入口：

- 公开读取：`GET /daily-reader/today`、`GET /daily-reader`、`GET /daily-reader/{article_id}`
- 管理触发：`POST /daily-reader/admin/generate`
- 管理重跑：`POST /daily-reader/admin/retry`

公开详情接口只返回 `status='published'` 的文章；管理端重跑使用 any-status 查询以保留草稿修复能力。

## 当前数据契约

`daily_readers` 当前主要保存：

- `body_json`: 面向客户端展示的正文结构，正文数组继续使用 `paragraphs` 字段名；当前语义已经是 reading unit。
- `highlights_json`: 词汇/表达高亮，`paragraph_id` 指向 reading unit id。
- `paragraph_notes_json`: 段落透读与译文结构，`paragraph_id` 指向 reading unit id。
- `takeaways_json`: 文末收束，包括讨论问题和写作借鉴。

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

## 验证入口

核心测试：

```powershell
rtk test uv run pytest services/api/tests/test_daily_reader_structure.py services/api/tests/test_daily_reader.py services/api/tests/test_daily_takeaways_schema.py -q
```

验收以上述命令的当前运行结果为准。

真实数据验证时，`run_workflow_only` 会对现有文章原地 `UPDATE`。重跑前应先导出目标文章的 `body_json`、`paragraph_notes_json`、`highlights_json` 和 `takeaways_json` 快照，避免覆盖旧输出后无法比较。

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

5. 内容安全与封面策略
   - `content_security.py` 存在，但 pipeline 仍有占位式安全结果。
   - cover 当前是本地下载到 `static/covers` 的开发方案，没有正式尺寸校验、多尺寸派生或 CDN 策略。

6. 长文 takeaways 上下文
   - close reading takeaways 仍依赖 notes summary 截断策略。
   - 后续可改为结构化抽样，避免长文前半部分过度占据上下文。
