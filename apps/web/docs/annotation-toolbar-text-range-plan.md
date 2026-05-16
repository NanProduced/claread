# Annotation Toolbar And Text Range Plan

> **状态**: `CURRENT` | **最后更新**: 2026-05-16
> 本文记录 Web Reader 批注 toolbar 第一版范围，以及 `text_range` / `multi_text` anchor 从现状到完整闭环的后端/API/客户端评估。

## Toolbar v1

第一版 SelectionToolbar 的信息架构固定为：

```text
Ask Claread | 高亮颜色 | 笔记 | 收藏 | 查词 | 反馈 | ...
```

范围说明：

- `Ask Claread`：先占位，视觉上保留主入口，但 disabled / coming soon，不触发真实 AI 对话。
- 高亮颜色：使用用户批注专属 marker 视觉，不复用机器标注的 vocab / phrase / context / grammar 色块语义。toolbar v1 只暴露 3 个不与机器标注冲突的用户色，底层继续兼容现有 `warm_yellow / soft_green / soft_blue / soft_purple / sage_green`。
- `笔记`：打开轻量 note popover 或移动端 bottom sheet，保存为 user annotation。
- `收藏`：按当前 toolbar 选区保存。局部选区写入 `target_type='text_range'`，跨句/跨段选区写入 `target_type='multi_text'`，整句模式写入 `target_type='sentence'`。
- `查词`：仅当选区像单词或短语时可用；长选区优先引导到 Ask / 解释。
- `反馈`：针对当前句子或选区上下文打开 feedback 入口。
- `...`：后续容纳复制、删除批注、复制原文+译文、展开句子解析等低频操作。

当前实现已经推进到 Phase 4：Web toolbar 可从单句内 DOM selection 创建 `text_range`，也可从跨句/跨段 DOM selection 创建 `multi_text` 高亮/笔记/收藏，并能反显已有状态、取消标注、选择当前句子和触发查词；`Ask Claread`、反馈和更多操作仍是占位/待接入。

## Current Implementation State

已完成：

- Reader 原文使用 Floating UI 显示 SelectionToolbar，第一版结构为 `Ask Claread | 高亮颜色 | 笔记 | 收藏 | 查词 | 反馈 | ...`。
- Web 高亮/笔记可以写入 `anchor_type='text_range'`，并携带 `start_offset/end_offset/text_hash`。
- Web Reader 会按句内 offsets 渲染局部用户高亮；整句批注仍保留整句 marker 视觉。
- Web Reader SelectionToolbar 改为 icon-first 浮动工具条，不再横向滚动；笔记改为工具条下方轻量输入，不再打开原来的大弹窗。
- Web Reader 支持已存在选区批注的状态反显，可更新高亮色、删除笔记、取消标注。
- Web toolbar 已接入 `text_range` / `sentence` 收藏 BFF。
- Web toolbar 当前会显式标记 `整句 / 局部选区 / 跨句选区` 模式，避免手动整句选择、局部选择和跨句选择共用一套模糊显示。
- Web Reader 普通正文不再逐词渲染为可点击节点，避免浏览器原生选区被切碎；点词查询改为句子级点击定位。
- Web Reader SelectionToolbar 使用 live DOM Range 作为 Floating UI reference，滚动时跟随选区。
- 机器标注和用户标注已分层：固定搭配改为紫色高亮，语法保持细线标注；用户高亮使用独立的纸面 marker 视觉。
- Web、微信小程序已通过 `@claread/contracts` 共享批注/收藏 target、颜色、offset/hash 常量；`text_range` 坐标统一为 UTF-16 code unit。
- 后端已按 render scene 校验 `selected_text`、`start_offset/end_offset` 和 `text_hash`，避免 Web 写入漂移锚点。
- Web 已新增 `/library/assets` 学习资产页，按解析文章聚合句子与 `text_range` 高亮、笔记和收藏。
- Web `/library/assets` 与小程序摘录页都已接入 `targetKey` 回跳；跨句资产使用 `multi_text` target key 和 segment payload 保持一致。
- 小程序摘录跳回结果页时可携带 `anchorType=text_range`、offset 和 hash，用于复现 Web 局部资产。
- 小程序结果页和摘录页已兼容显示 Web 创建的 `multi_text` 资产；当前仍不主动创建跨句选区。
- `grammar_note` / `sentence_analysis` 采用两层状态：
  - 默认态：正文只显示轻量机器标注。
  - 激活态：点击解析 chip/card 后，卡片 active，关联原文加重显示。
- 小程序摘录页已允许 `anchor_type='text_range'` 的批注进入学习资产列表，并标记为“局部高亮”。
- 小程序摘录页已允许 `target_type='text_range'` / `target_type='multi_text'` 的收藏进入学习资产列表，作为局部摘录或跨句摘录展示。

仍未启用：

- `Ask Claread` 实际 AI 操作。
- 小程序主动创建 `text_range` / `multi_text`。

## Current Backend State

当前 FastAPI 与数据库已经完成 `text_range` + `multi_text` 闭环：

- `user_annotations.anchor_type` 已允许 `sentence / paragraph / text_range / multi_text`。
- `user_annotations` 已有 `start_offset`、`end_offset`、`text_hash`。
- `UserAnnotationCreateRequest` / `UserAnnotationResponse` 已暴露 `segments[]`，作为 `multi_text` 的 canonical payload。
- `UserAnnotationCreateRequest` 已按 `anchor_type` 做条件校验：sentence 要有 `sentence_id`，paragraph 要有 `paragraph_id`，text_range 要有 `analysis_record_id/sentence_id/start_offset/end_offset/text_hash/selected_text`，multi_text 要有 `analysis_record_id` 和至少两个 segments。
- `text_range` 的 `selected_text` 长度按 UTF-16 code unit 与 offset span 比对，并使用 `fnv1a32-utf16` 校验 hash。
- 创建批注时，服务层会校验 record 所属用户、render scene 中的 sentence 是否存在，以及 UTF-16 切片是否与 selected text 一致；`multi_text` 还要求 segments 按文章 sentence 顺序递增，且不重复 sentence_id。
- `_build_target_key()` 已能生成 `record:{id}:range:{sentence_id}:{start}:{end}:{text_hash}`。
- `_build_target_key()` 已能生成 `record:{id}:multi_text:{segment_count}:{signature_hash}`。
- 后端测试覆盖 schema 条件校验和 target key。
- `favorite_records.target_type` 已通过 migration 扩展 `text_range / multi_text`，favorites schema/API/service 和测试已覆盖局部/跨句收藏，并校验 payload 中的 selected text、offset、hash 和 render scene segment 顺序。

缺口：

- 数据库层还没有条件 CHECK；约束目前在服务层。
- Web Reader text range overlay 已按连续 range 分段渲染；与机器 inline mark 重叠时，机器标注优先保持自己的语义形态，用户高亮作为附加 marker 层显示。

## Current Learning Asset Model

当前“学习资产”不是独立顶级对象表，而是由文章记录下的收藏、批注和解析要点聚合出来：

- 顶级父级是 `analysis_record` / 解析文章。
- 资产子项当前是 anchor asset，而不再默认等同于句子：`sentence`、`text_range`、`multi_text` 都是一级资产子项。
- `user_annotations` 承载高亮和笔记，`favorite_records` 承载收藏。
- 小程序 `packageA/excerpts` 会把收藏和批注合并成 `SentenceAsset`，再按文章分组展示“学习摘录”。

当前关键实现状态：

- 小程序 Result 页的 `SelectionContext`、`buildTargetKey()` 和 API DTO 已有 `text_range` 字段形态，但 `ParagraphBlock` 长按实际仍创建整句选区：`startOffset=0`、`endOffset=sentence.text.length`、`anchorType='sentence'`。
- 小程序 `ParagraphBlock` 已能渲染 Web 写入的 `anchor_type='text_range'` 批注：当 offsets 有效时，会在句内生成 user highlight overlay；整句批注仍走句子背景。
- 小程序“学习摘录”页已允许：
  - 收藏接收 `target_type === 'sentence' | 'text_range' | 'multi_text'`。
  - 批注接收 `anchor_type === 'sentence' | 'text_range' | 'multi_text'`。
  因此 Web 局部/跨句高亮、笔记和收藏都可以作为同一篇文章下的 anchor asset 展示。
- Web `/library` 是阅读记录索引；`/library/assets` 是学习资产聚合页，按文章合并句子级、`text_range` 和 `multi_text` 的收藏、highlight、note。
- `favorite_records.target_type` 与 `user_annotations.anchor_type` 已通过 `0004_add_multi_text_anchor_types.sql` 扩展 `multi_text`。

因此，`text_range` 不是只改 Reader 的锚点问题。完整闭环必须同时覆盖：

1. Reader 创建：Web DOM selection -> sentence local offsets / multi-segment payload -> hash -> `/user-annotations`。
2. Reader 展示：Web 和小程序都能在句内显示局部高亮；跨句资产按 segment 分发到涉及的句子。
3. 资产聚合：摘录页从 `SentenceAsset` 泛化为 anchor asset，支持句子、局部文本和跨句文本共存。
4. 收藏模型：收藏选区使用同一套 `text_range` / `multi_text` target key，与 annotations 保持一致。

## Database Impact

短期不需要新增表。现有 `user_annotations` / `favorite_records` 可以承载 `multi_text`，方式是：

- top-level `sentence_id` / `paragraph_id` 继续存首个 segment，保证粗粒度回跳和旧端兼容；
- canonical 多段 anchor 存在 `payload_json.segments[]`；
- `target_key` 作为 anchor identity，确保资产列表和回跳都按 anchor 而不是按 sentence 混合。

`favorite_records.target_type` 和 `user_annotations.anchor_type` 已通过 schema 级 migration 扩展，新增 `infra/migrations/0004_add_multi_text_anchor_types.sql`。原因：

- 当前 `0001_initial_schema.sql` 已包含 text range 字段。
- 现有环境可能已有 sentence 级数据，数据库级 CHECK 需要先清洗历史数据。
- 文本 offset 坐标系已固定为 UTF-16 code unit，与 Web/miniprogram JavaScript 字符串 offset 一致。

中期可以考虑：

- 给 `user_annotations` 增加条件 CHECK：
  - `sentence/paragraph` anchor 不要求 offsets。
  - `text_range` anchor 要求 `sentence_id IS NOT NULL`、`start_offset IS NOT NULL`、`end_offset IS NOT NULL`、`start_offset < end_offset`。
- 增加局部索引：
  - `(user_id, analysis_record_id, sentence_id, start_offset, end_offset)` where `anchor_type='text_range' and deleted_at is null`。

## API And Service Changes

已按以下顺序实现：

1. 后端 schema validator：
   - `sentence`：必须有 `sentence_id`。
   - `paragraph`：必须有 `paragraph_id`。
   - `text_range`：必须有 `analysis_record_id`、`sentence_id`、`start_offset`、`end_offset`、`text_hash`。
   - `selected_text` 不能为空，`start_offset < end_offset`。
2. 后端 target key：
   - 保持当前结构，避免破坏已创建数据。
   - 对 text range 要求 `text_hash` 非空，避免同句同 offset 但文本漂移时冲突。
   - 创建时校验 render scene sentence 切片，防止仅靠 target key 写入无效资产。
3. Web BFF：
   - `WebAnnotationCreateRequest` 增加 `anchorType`、`startOffset`、`endOffset`、`textHash`。
   - `multi_text` 透传 `segments[]`，不再把跨句选择压扁成单句。
   - 透传 text range 字段到 `/user-annotations`。
4. Web VM：
   - `WebAnnotationVm` 增加 `startOffset`、`endOffset`、`textHash`。
5. Web Reader：
   - 用 `data-sentence-id` 和原文 DOM Range 反算 selection。
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

当前 `payload_json.anchor` 应同时保存：

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

小程序端当前可以不创建 `text_range` / `multi_text`，但必须能显示 Web 创建的这些资产：

- 当前 `apps/miniprogram/src/services/api/user-annotations.client.ts` 已有 `anchor_type / start_offset / end_offset / text_hash / segments` 字段。
- `ParagraphBlock` 已有 range 渲染逻辑：当 annotation 是 `text_range` 时直接按 offsets 渲染；当 annotation 是 `multi_text` 时按 segments 分发到对应句子渲染。
- 小程序长按仍创建 `anchorType: "sentence"`，这是合理降级。
- 小程序结果页与摘录页跳回 Web 创建的局部/跨句资产时，若该 target 只有 favorite、没有 annotation，会短时强调对应 sentence / range / multi_text segment，而不是伪装成当前用户选区。

仍需补齐：

- 小程序创建 text range 的代码路径当前没有传 `start_offset/end_offset/text_hash`；如果未来小程序也支持局部选择，需要补齐。
- 小程序摘录页已经能展示 Web 局部/跨句高亮、笔记和收藏，跳回原文时可携带 `targetKey`，并按首段 sentence 定位。
- 小程序 `listUserAnnotations()` 已提高默认请求量；Web text range 增多后，仍建议补完整分页。

## Favorites Impact

Toolbar v1 包含“收藏”，但 favorites 当前不同于 annotations：

- `favorite_records.target_type` DB CHECK 已允许 `analysis_record / sentence / paragraph / phrase / vocab / text_range / multi_text`。
- Web BFF 使用 `/api/web/favorites/target` 操作局部收藏。

因此：

- 局部选区收藏保存为 `target_type='text_range'`。
- 跨句/跨段选区收藏保存为 `target_type='multi_text'`。
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

- 当前已做兼容：小程序摘录页允许 `anchor_type='text_range'` 的 user annotations 进入 `SentenceAsset`，使用 `selected_text` 展示，并保留 `startOffset/endOffset/textHash`。
- Web 已新增 `/library/assets`，以解析文章为父级聚合 sentence 和 text range anchor。
- 后续如果资产模型继续扩展，可将小程序 `SentenceAsset` 改名或泛化为 `AnchorAsset`：
  - `anchorType: 'sentence' | 'text_range'`
  - `sentenceId`
  - `text` 使用 `selected_text`
  - 可选 `parentSentenceText`
  - `startOffset/endOffset/textHash`
- 摘录列表仍按文章分组，局部批注显示为句子下的一条局部摘录，点击仍跳回原句并可复现局部 range。
- `listUserAnnotations()` 已增加默认 `limit=200`；更完整的分页可以后续补。

### Phase 3: Text Range Favorite

目标：收藏选区成为局部资产。

- 已完成 migration 扩展 `favorite_records.target_type` / `user_annotations.anchor_type` CHECK，加入 `multi_text`。
- 已完成 FastAPI favorites schema/API/service 和测试覆盖。
- 已完成 Web BFF `/api/web/favorites/target`。
- 已完成 Web toolbar 局部收藏操作。
- 已完成小程序摘录页接收 `target_type='text_range'` / `target_type='multi_text'` favorites。

target type、anchor type、颜色、offset unit 和 hash algorithm 已先沉淀到 `@claread/contracts`，后续仍需评估是否从 OpenAPI 生成完整 DTO。

### Delivered Stabilization

本轮已完成的稳定化项：

- 后端按 render scene 校验 `selected_text`、`start_offset/end_offset` 和 `text_hash` 的一致性。
- Web 端拆出 `ReaderCanvas`、`ReaderSentenceRow`、`ReaderAnnotationOverlay` 和 `reader-selection`，降低 `ReaderWorkbench` 后续迭代风险。
- Web 本轮已通过本地浏览器回归核对 SelectionToolbar 滚动跟随、lookup preview 锚定和 `multi_text` 选区识别；提交到仓库的真实记录自动化仍需要稳定登录态或 debug session。
- Web `/library/assets` 现会明确展示 `multi_text` 的 segment 数和句子/offset 信息，避免整句与跨句资产混在一个模糊文案里。
- Web 与小程序回跳 favorite-only `text_range` / `multi_text` 时，都会使用独立 route focus 状态做短时强调，不再复用 `selectionRange`。
- Web 手动 selection 若刚好覆盖整句，现已归一化为 `anchorType='sentence'`；与 toolbar“选择当前句子”保持同一底层语义和同一显示状态。
- Web 当前选区命中已改为 exact anchor 优先，不再用“任意重叠 range”复用旧批注。
- note 与 highlight 现按同一 anchor 独立维护：已有高亮上补 note 会保留 highlight；note-only 删除时直接删除该 anchor；highlight+note 清除 note 时只移除 note。
- 普通单词点击的轻量词卡已改为浮层定位，不再通过占位块撑开句子行高。

### Remaining Stabilization

后续不应立刻扩大 selection 范围，优先补：

- 完整分页：annotations/favorites 在资产页和小程序摘录页仍不应长期依赖较大 limit。
- 数据库条件 CHECK 和局部索引：在历史数据清理后补强。
- Playwright 真实数据 smoke：覆盖创建/取消高亮、保存/删除笔记、局部收藏、点词查词和旧高亮兼容。

## Product Decision

建议产品节奏：

1. Toolbar 文案和状态按当前 anchor 变化：局部选区就是“选区”，选择当前句子后就是“当前句”。
2. Web selection 支持单句内局部高亮、笔记、收藏、查词。
3. 同一句内允许“整句 note / highlight”与更小的 `text_range` note / highlight 并存，但不同 anchor 必须分别保存、分别展示、分别删除。
4. 小程序不复刻局部选择操作，但展示 Web 写入的局部资产。
5. 小程序当前不复刻跨句/跨段创建交互，但要完整显示并回跳 Web 侧 `multi_text` 资产。

这样能先提升 UI/UX，又不制造跨端数据不一致。
