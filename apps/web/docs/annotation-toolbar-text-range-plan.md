# Annotation Toolbar And Text Range Plan

> **状态**: `CURRENT` | **最后更新**: 2026-05-16
> 本文记录 Web Reader 批注 toolbar 第一版范围，以及 `text_range` anchor 从现状到完整闭环的后端/API/客户端评估。

## Toolbar v1

第一版 SelectionToolbar 的信息架构固定为：

```text
Ask Claread | 高亮颜色 | 笔记 | 收藏 | 查词 | 反馈 | ...
```

范围说明：

- `Ask Claread`：先占位，视觉上保留主入口，但 disabled / coming soon，不触发真实 AI 对话。
- 高亮颜色：使用用户批注专属 marker 视觉，不复用机器标注的 vocab / phrase / context / grammar 色块语义。toolbar v1 只暴露 3 个不与机器标注冲突的用户色，底层继续兼容现有 `warm_yellow / soft_green / soft_blue / soft_purple / sage_green`。
- `笔记`：打开轻量 note popover 或移动端 bottom sheet，保存为 user annotation。
- `收藏`：按当前 toolbar 选区保存。局部选区写入 `target_type='text_range'`，整句模式写入 `target_type='sentence'`。
- `查词`：仅当选区像单词或短语时可用；长选区优先引导到 Ask / 解释。
- `反馈`：针对当前句子或选区上下文打开 feedback 入口。
- `...`：后续容纳复制、删除批注、复制原文+译文、展开句子解析等低频操作。

当前实现已经推进到 Phase 3：Web toolbar 可从单句内 DOM selection 创建 `text_range` 高亮/笔记/收藏，并能反显已有状态、取消标注、选择当前句子和触发查词；`Ask Claread`、反馈和更多操作仍是占位/待接入。

## Current Implementation State

已完成：

- Reader 原文使用 Floating UI 显示 SelectionToolbar，第一版结构为 `Ask Claread | 高亮颜色 | 笔记 | 收藏 | 查词 | 反馈 | ...`。
- Web 高亮/笔记可以写入 `anchor_type='text_range'`，并携带 `start_offset/end_offset/text_hash`。
- Web Reader 会按句内 offsets 渲染局部用户高亮；整句批注仍保留整句 marker 视觉。
- Web Reader SelectionToolbar 改为 icon-first 浮动工具条，不再横向滚动；笔记改为工具条下方轻量输入，不再打开原来的大弹窗。
- Web Reader 支持已存在选区批注的状态反显，可更新高亮色、删除笔记、取消标注。
- Web toolbar 已接入 `text_range` / `sentence` 收藏 BFF。
- Web Reader 普通正文不再逐词渲染为可点击节点，避免浏览器原生选区被切碎；点词查询改为句子级点击定位。
- Web Reader SelectionToolbar 使用 live DOM Range 作为 Floating UI reference，滚动时跟随选区。
- 机器标注和用户标注已分层：固定搭配改为紫色高亮，语法保持细线标注；用户高亮使用独立的纸面 marker 视觉。
- `grammar_note` / `sentence_analysis` 采用两层状态：
  - 默认态：正文只显示轻量机器标注。
  - 激活态：点击解析 chip/card 后，卡片 active，关联原文加重显示。
- 小程序摘录页已允许 `anchor_type='text_range'` 的批注进入学习资产列表，并标记为“局部高亮”。
- 小程序摘录页已允许 `target_type='text_range'` 的收藏进入学习资产列表，作为局部摘录展示。

仍未启用：

- `Ask Claread` 实际 AI 操作。
- 后端按 render scene 严格校验 selected text 与 sentence text 匹配。
- 跨句 selection 持久化。

## Current Backend State

当前 FastAPI 与数据库不是完全不支持 `text_range`，而是“字段已预留，闭环未完成”：

- `user_annotations.anchor_type` 已允许 `sentence / paragraph / text_range`。
- `user_annotations` 已有 `start_offset`、`end_offset`、`text_hash`。
- `UserAnnotationCreateRequest` / `UserAnnotationResponse` 已暴露这些字段。
- `UserAnnotationCreateRequest` 已按 `anchor_type` 做条件校验：sentence 要有 `sentence_id`，paragraph 要有 `paragraph_id`，text_range 要有 `sentence_id/start_offset/end_offset/text_hash`。
- `_build_target_key()` 已能生成 `record:{id}:range:{sentence_id}:{start}:{end}:{text_hash}`。
- 后端测试覆盖 schema 条件校验和 target key。
- `favorite_records.target_type` 已通过 migration 扩展 `text_range`，favorites schema/API/service 和测试已覆盖局部收藏。

缺口：

- 后端没有校验 selected text 与 record render scene 中的 sentence text 是否匹配。
- 数据库层还没有条件 CHECK；约束目前在服务层。
- Web Reader text range overlay 已按连续 range 分段渲染；与机器 inline mark 重叠时，机器标注优先保持自己的语义形态，用户高亮作为附加 marker 层显示。

## Current Learning Asset Model

当前“学习资产”不是独立顶级对象表，而是由文章记录下的收藏、批注和解析要点聚合出来：

- 顶级父级是 `analysis_record` / 解析文章。
- 资产子项主要以句子为单位：`sentence_id`、`target_key`、`selected_text`、`translation`、`review_assets`。
- `user_annotations` 承载高亮和笔记，`favorite_records` 承载收藏。
- 小程序 `packageA/excerpts` 会把收藏和批注合并成 `SentenceAsset`，再按文章分组展示“学习摘录”。

当前关键实现状态：

- 小程序 Result 页的 `SelectionContext`、`buildTargetKey()` 和 API DTO 已有 `text_range` 字段形态，但 `ParagraphBlock` 长按实际仍创建整句选区：`startOffset=0`、`endOffset=sentence.text.length`、`anchorType='sentence'`。
- 小程序 `ParagraphBlock` 已能渲染 Web 写入的 `anchor_type='text_range'` 批注：当 offsets 有效时，会在句内生成 user highlight overlay；整句批注仍走句子背景。
- 小程序“学习摘录”页已允许：
  - 收藏接收 `target_type === 'sentence' | 'text_range'`。
  - 批注接收 `anchor_type === 'sentence' | 'text_range'`。
  因此 Web 局部高亮、笔记和收藏可以作为同一篇文章下的局部摘录展示。
- Web `/library` 当前只是阅读记录索引，不是摘录/学习资产聚合页；Web Reader 已支持文章收藏、句子级批注和单句内 `text_range` 批注/收藏创建。
- `favorite_records.target_type` 已通过 `0002_add_text_range_favorites_target_type.sql` 扩展 `text_range`。

因此，`text_range` 不是只改 Reader 的锚点问题。完整闭环必须同时覆盖：

1. Reader 创建：Web DOM selection -> sentence local offsets -> hash -> `/user-annotations`。
2. Reader 展示：Web 和小程序都能在句内显示局部高亮/笔记。
3. 资产聚合：摘录页从 `SentenceAsset` 泛化为 anchor asset，支持句子和局部文本共存。
4. 收藏模型：收藏选区使用同一套 range target key，与 annotations 保持一致。

## Database Impact

短期不需要新增列，现有表已经覆盖 text range 所需字段。

`favorite_records.target_type` 需要 schema 级 migration，已新增 `infra/migrations/0002_add_text_range_favorites_target_type.sql`。`user_annotations` 暂不新增列，先在服务层收紧校验。原因：

- 当前 `0001_initial_schema.sql` 已包含 text range 字段。
- 现有环境可能已有 sentence 级数据，数据库级 CHECK 需要先清洗历史数据。
- 文本 offset 的最终坐标系还需要和 Web/miniprogram 渲染一致后再固化。

中期可以考虑：

- 给 `user_annotations` 增加条件 CHECK：
  - `sentence/paragraph` anchor 不要求 offsets。
  - `text_range` anchor 要求 `sentence_id IS NOT NULL`、`start_offset IS NOT NULL`、`end_offset IS NOT NULL`、`start_offset < end_offset`。
- 增加局部索引：
  - `(user_id, analysis_record_id, sentence_id, start_offset, end_offset)` where `anchor_type='text_range' and deleted_at is null`。

## API And Service Changes

建议按顺序实现：

1. 后端 schema validator：
   - `sentence`：必须有 `sentence_id`。
   - `paragraph`：必须有 `paragraph_id`。
   - `text_range`：必须有 `sentence_id`、`start_offset`、`end_offset`、`text_hash`。
   - `selected_text` 不能为空，`start_offset < end_offset`。
2. 后端 target key：
   - 保持当前结构，避免破坏已创建数据。
   - 对 text range 要求 `text_hash` 非空，避免同句同 offset 但文本漂移时冲突。
3. Web BFF：
   - `WebAnnotationCreateRequest` 增加 `anchorType`、`startOffset`、`endOffset`、`textHash`。
   - 在 BFF 里阻止跨句/跨段 selection 直接写入。
   - 透传 text range 字段到 `/user-annotations`。
4. Web VM：
   - `WebAnnotationVm` 增加 `startOffset`、`endOffset`、`textHash`。
5. Web Reader：
   - 用 `data-sentence-id`、`data-start-offset`、`data-end-offset` 反算 selection。
   - text range 持久化后，用 DOM span 分段渲染用户高亮；不要只给整句加背景。

## Offset Coordinate

必须先定义 offset 坐标系。建议 v1 使用：

```text
offset = sentence.text 的 JavaScript UTF-16 code unit offset
```

理由：

- Web DOM Selection 和 JS string slice 默认都是 UTF-16 code unit。
- 小程序/Taro 也是 JS 运行时，前端显示 text range 时更容易复用。
- 后端可以先存储，不主动用 Python 字符串切片做强验证。

风险：

- 如果未来后端要严格校验 selected text，需要实现同一套 UTF-16 offset slicing，或改为 code point offset 并在 Web selection 层做转换。
- 英文阅读文本中 surrogate pair 较少，v1 风险可控，但文档必须写清楚。

建议 `payload_json.anchor` 同时保存：

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

这让后续即使 offset 失效，也可以用 TextQuoteSelector 思路做 fuzzy repair。

## Miniprogram Compatibility

小程序端可以不创建 text range，但必须能显示 Web 创建的 text range：

- 当前 `apps/miniprogram/src/services/api/user-annotations.client.ts` 已有 `anchor_type / start_offset / end_offset / text_hash` 字段。
- `ParagraphBlock` 已有 text range 渲染逻辑：当 annotation 是 `text_range` 且 offsets 有效时，会在句内渲染用户高亮 overlay。
- 小程序长按仍创建 `anchorType: "sentence"`，这是合理降级。

仍需补齐：

- 小程序创建 text range 的代码路径当前没有传 `start_offset/end_offset/text_hash`；如果未来小程序也支持局部选择，需要补齐。
- 小程序摘录页已经能展示 Web 局部高亮/笔记/收藏，但跳回原文后如何精准定位局部 range 仍可继续增强。
- 小程序 `listUserAnnotations()` 已提高默认请求量；Web text range 增多后，仍建议补完整分页。

## Favorites Impact

Toolbar v1 包含“收藏”，但 favorites 当前不同于 annotations：

- `favorite_records.target_type` DB CHECK 已允许 `analysis_record / sentence / paragraph / phrase / vocab / text_range`。
- Web BFF 使用 `/api/web/favorites/target` 操作局部收藏。

因此：

- 局部选区收藏保存为 `target_type='text_range'`。
- 整句模式收藏保存为 `target_type='sentence'`。
- 小程序不需要提供局部选择操作，但摘录页必须展示 Web 写入的局部收藏。

## Delivered Development Path

`text_range` 已按数据闭环分层交付，后续重点从“能否做”转为“稳定性、校验和资产管理”：

### Phase 1: Annotation Text Range Core

目标：打通局部高亮和局部笔记。

- 已完成后端 schema 条件校验和单测。
- 已保持现有 `user_annotations` DB 列，不新增 annotation migration。
- 已完成 Web BFF/VM 透传 `anchorType/startOffset/endOffset/textHash`。
- Web Reader 只允许单句内 selection 创建 `text_range`。
- Web Reader 按句内 offsets 分段渲染用户高亮和 note marker。
- 小程序无需创建局部选区，但已能显示 Web 创建的 `text_range`。

这一步可独立上线，因为不改变现有 sentence 资产语义，也不会破坏小程序创建路径。

### Phase 2: Learning Assets Compatibility

目标：让“学习摘录”能看见 Web 局部批注。

- 当前已做最小兼容：小程序摘录页允许 `anchor_type='text_range'` 的 user annotations 进入 `SentenceAsset`，使用 `selected_text` 展示，并保留 `startOffset/endOffset/textHash`。
- 后续如果资产模型继续扩展，可将小程序 `SentenceAsset` 改名或泛化为 `AnchorAsset`：
  - `anchorType: 'sentence' | 'text_range'`
  - `sentenceId`
  - `text` 使用 `selected_text`
  - 可选 `parentSentenceText`
  - `startOffset/endOffset/textHash`
- 摘录列表仍按文章分组，局部批注显示为句子下的一条局部摘录，点击仍跳回原句。
- `listUserAnnotations()` 已增加默认 `limit=200`；更完整的分页可以后续补。

### Phase 3: Text Range Favorite

目标：收藏选区成为局部资产。

- 已完成 migration 扩展 `favorite_records.target_type` CHECK，加入 `text_range`。
- 已完成 FastAPI favorites schema/API/service 和测试覆盖。
- 已完成 Web BFF `/api/web/favorites/target`。
- 已完成 Web toolbar 局部收藏操作。
- 已完成小程序摘录页接收 `target_type='text_range'` favorites。

后续仍需把 target type 常量沉淀到 generated contracts 或 shared package，减少跨端字符串漂移。

### Next Stabilization

下一阶段不应继续扩大 selection 能力范围，而应先补稳定性：

- 后端按 render scene 校验 `selected_text`、`start_offset/end_offset` 和 `text_hash` 的一致性。
- Web 端把 selection toolbar、dictionary lookup、annotation rendering 拆成更小组件，降低 `ReaderWorkbench` 复杂度。
- Web 增加 Playwright smoke，固定覆盖：选区工具栏出现、滚动跟随、创建/取消高亮、打开笔记、局部收藏、点词查词。
- 小程序摘录页增强跳转后对局部 range 的定位和强调。

## Product Decision

建议产品节奏：

1. Toolbar 文案和状态按当前 anchor 变化：局部选区就是“选区”，选择当前句子后就是“当前句”。
2. Web selection 支持单句内局部高亮、笔记、收藏、查词。
3. 小程序不复刻局部选择操作，但展示 Web 写入的局部资产。
4. 跨句选择后置。首个 text range 版本只支持单句内 selection，跨句选择提示“暂不支持跨句批注，可先保存整句”。

这样能先提升 UI/UX，又不制造跨端数据不一致。
