# Annotation Toolbar 与 Text Range 锚点模型

> **状态**: `CURRENT` | **最后更新**: 2026-05-20
> 本文记录 Web Reader 批注 toolbar 第一版范围、`text_range` / `multi_text` anchor 的当前实现状态和数据契约。**注意：这份文档同时记录“现有实现事实”和“已暴露但尚未收口的产品问题”**；文本收藏、`multi_text` 作为用户学习资产、以及 `/library/assets` 的长期定位当前都处于重审阶段，不再视为既定方向。

## Toolbar v1

SelectionToolbar 信息架构：

```text
Ask Claread | 高亮颜色 | 笔记 | 收藏 | 查词 | 反馈 | ...
```

范围说明：

- `Ask Claread`：占位入口，待接入 AI 对话。
- 高亮颜色：用户批注专属 marker 视觉，不复用机器标注语义色。toolbar v1 暴露 3 个用户色，底层兼容 `warm_yellow / soft_green / soft_blue / soft_purple / sage_green`。
- `笔记`：轻量 note popover 或移动端 bottom sheet，保存为 user annotation。
- `收藏`：当前实现仍支持局部选区 → `target_type='text_range'`，跨句/跨段 → `target_type='multi_text'`，整句 → `target_type='sentence'`；但这条产品线已进入重审，不应继续扩展。
- `查词`：仅当选区像单词或短语时可用；长选区优先引导到 Ask / 解释。
- `反馈`：针对当前句子或选区上下文打开 feedback 入口。
- `...`：后续容纳复制、删除批注、复制原文+译文、展开句子解析等低频操作。

当前实现状态：Web toolbar 可从单句内 DOM selection 创建 `text_range`，也可从跨句/跨段 DOM selection 创建 `multi_text` 高亮/笔记/收藏，并能反显已有状态、取消标注、选择当前句子和触发查词；`Ask Claread`、反馈和更多操作仍是占位/待接入。**但这代表“技术能力已经打通”，不代表“用户学习资产模型已经成立”。**

## Web 实现状态

已完成：

- SelectionToolbar 使用 Floating UI + live DOM Range 定位，滚动时跟随选区；icon-first 浮动工具条，笔记改为工具条下方轻量输入。
- 高亮/笔记写入 `anchor_type='text_range'`，携带 `start_offset/end_offset/text_hash`；按句内 offsets 分段渲染局部用户高亮。
- 支持已存在选区批注的状态反显，可更新高亮色、删除笔记、取消标注。
- 显式标记 `整句 / 局部选区 / 跨句选区` 模式。
- 普通正文不再逐词渲染为可点击节点；点词查询改为句子级点击定位。
- 机器标注和用户标注分层：固定搭配紫色高亮，语法细线标注；用户高亮使用独立纸面 marker 视觉。
- Web、小程序通过 `@claread/contracts` 共享批注/收藏 target、颜色、offset/hash 常量；`text_range` 坐标统一为 UTF-16 code unit。
- `/library/assets` 摘录与批注页按文章聚合 sentence、`text_range` 和 `multi_text` 的高亮、笔记和收藏；已接入 `targetKey` 回跳。该页面当前更像实验性的摘录索引，而不是稳定“学习资产中心”。
- 手动 selection 覆盖整句时归一化为 `anchorType='sentence'`。
- 选区命中改为 exact anchor 优先。
- note 与 highlight 按同一 anchor 独立维护：已有高亮上补 note 保留 highlight；note-only 删除时直接删除该 anchor；highlight+note 清除 note 时只移除 note。
- 普通单词点击的轻量词卡改为浮层定位，不撑开句子行高。

仍未启用：

- `Ask Claread` 实际 AI 操作。
- 小程序主动创建 `text_range` / `multi_text`。

## 后端实现状态

FastAPI 与数据库已完成 `text_range` + `multi_text` 闭环：

- `user_annotations.anchor_type` 允许 `sentence / paragraph / text_range / multi_text`。
- `user_annotations` 已有 `start_offset`、`end_offset`、`text_hash`。
- `UserAnnotationCreateRequest` / `UserAnnotationResponse` 已暴露 `segments[]`。
- 按 `anchor_type` 条件校验：sentence 要有 `sentence_id`，paragraph 要有 `paragraph_id`，text_range 要有 `analysis_record_id/sentence_id/start_offset/end_offset/text_hash/selected_text`，multi_text 要有 `analysis_record_id` 和至少两个 segments。
- `selected_text` 长度按 UTF-16 code unit 与 offset span 比对，`fnv1a32-utf16` 校验 hash。
- 创建批注时校验 record 所属用户、render scene sentence 存在性和 UTF-16 切片一致性；`multi_text` 要求 segments 按 sentence 顺序递增且不重复。
- `_build_target_key()` 生成 `record:{id}:range:{sentence_id}:{start}:{end}:{text_hash}` 和 `record:{id}:multi_text:{segment_count}:{signature_hash}`。
- `favorite_records.target_type` 已扩展 `text_range / multi_text`，favorites schema/API/service 和测试已覆盖。

缺口：

- 数据库层还没有条件 CHECK；约束目前在服务层。
- 与机器 inline mark 重叠时，机器标注优先保持语义形态，用户高亮作为附加 marker 层。

## 摘录资产模型（现有实现，非长期结论）

“学习资产”目前还不是稳定产品结论，当前正式化的只有“摘录资产”这一层现有实现：

- 顶级父级是 `analysis_record` / 解析文章。
- 资产子项是 anchor asset：`sentence`、`text_range`、`multi_text` 都是一级资产子项。
- `user_annotations` 承载高亮和笔记，`favorite_records` 承载收藏。
- `GET /excerpt-assets` 按 canonical `target_key` 合并 favorites 和 annotations，按文章分组返回。
- `vocabulary_book` 不并入摘录聚合；词汇资产走独立入口。

闭环覆盖：

1. Reader 创建：Web DOM selection → sentence local offsets / multi-segment payload → hash → `/user-annotations`。
2. Reader 展示：Web 和小程序都能在句内显示局部高亮；跨句资产按 segment 分发到涉及的句子。
3. 资产聚合：摘录页支持句子、局部文本和跨句文本共存。
4. 收藏模型：收藏选区使用同一套 `text_range` / `multi_text` target key。

这一层实现已经足以支撑当前代码，但**并不意味着**：

- 文本收藏一定保留为长期产品能力；
- `multi_text` 一定继续作为用户学习资产的主要 authoring 目标；
- `/library/assets` 一定继续存在并对用户可见。

## 数据库

现有 `user_annotations` / `favorite_records` 承载 `multi_text`：

- top-level `sentence_id` / `paragraph_id` 存首个 segment，保证粗粒度回跳和旧端兼容。
- canonical 多段 anchor 存在 `payload_json.segments[]`。
- `target_key` 作为 anchor identity。
- `favorite_records.target_type` 和 `user_annotations.anchor_type` 已通过 `0004_add_multi_text_anchor_types.sql` 扩展。
- offset 坐标系已固定为 UTF-16 code unit。

中期可补：

- `user_annotations` 条件 CHECK：`text_range` 要求 `sentence_id IS NOT NULL`、`start_offset IS NOT NULL`、`end_offset IS NOT NULL`、`start_offset < end_offset`。
- 局部索引：`(user_id, analysis_record_id, sentence_id, start_offset, end_offset)` where `anchor_type='text_range' and deleted_at is null`。

## Offset 坐标系

```text
offset = sentence.text 的 JavaScript UTF-16 code unit offset
```

`payload_json.anchor` 同时保存：

```json
{
  "offset_unit": "utf16",
  "selected_text_hash": "...",
  "sentence_text_hash": "...",
  "prefix": "前 16-32 字符",
  "suffix": "后 16-32 字符",
  "created_client": "web"
}
```

后续即使 offset 失效，可用 TextQuoteSelector 思路做 fuzzy repair。

## 小程序兼容

小程序不创建 `text_range` / `multi_text`，但必须能显示 Web 创建的这些资产：

- `ParagraphBlock` 已有 range 渲染逻辑：`text_range` 按 offsets 渲染，`multi_text` 按 segments 分发。
- 长按仍创建 `anchorType: "sentence"`，这是合理降级。
- 摘录页已允许 `anchor_type='text_range'` / `anchor_type='multi_text'` 的批注和收藏进入资产列表。
- 跳回 Web 创建的局部/跨句资产时，使用独立 route focus 状态做短时强调。

仍需补齐：

- 小程序创建 text range 的代码路径当前没有传 `start_offset/end_offset/text_hash`。
- `listUserAnnotations()` 已提高默认请求量；仍建议补完整分页。

## 收藏模型

- 局部选区收藏 → `target_type='text_range'`（现有实现，进入重审）。
- 跨句/跨段选区收藏 → `target_type='multi_text'`（现有实现，进入重审）。
- 整句收藏 → `target_type='sentence'`（现有实现，进入重审）。
- Web BFF 使用 `/api/web/favorites/target` 操作局部收藏。

## 待稳定项

后续不应立刻扩大 selection 范围，优先补：

- 完整分页：annotations/favorites 在资产页和小程序摘录页不应长期依赖较大 limit。
- 数据库条件 CHECK 和局部索引：在历史数据清理后补强。
- Playwright 真实数据 smoke：覆盖创建/取消高亮、保存/删除笔记、局部收藏、点词查词和旧高亮兼容。
- 用户资产冲突模型：当前 exact-match / overlap / stacked 只是实现现状，产品规则仍未闭环。
- 产品定位重审：文本收藏、`multi_text` 用户资产、`/library/assets` 是否继续保留，需要先完成 Reader 主场与“学习资产”价值重审。

## 产品决策

1. Toolbar 文案和状态按当前 anchor 变化：局部选区就是"选区"，选择当前句子后就是"当前句"。
2. 同一句内允许"整句 note / highlight"与更小的 `text_range` note / highlight 并存，但不同 anchor 必须分别保存、分别展示、分别删除。
3. 小程序不复刻局部选择操作，但展示 Web 写入的局部资产。
4. 小程序当前不复刻跨句/跨段创建交互，但要完整显示并回跳 Web 侧 `multi_text` 资产。
