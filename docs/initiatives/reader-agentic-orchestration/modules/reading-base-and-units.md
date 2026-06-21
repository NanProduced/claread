# Stable Reading Base 与 Reading Units

> 状态：`D2-S1 修订`
> 最后更新：2026-06-18
> 范围：稳定正文、不可变阅读单位、文本坐标和 `article_ready` gate。

## 核心不变量

- Stable Reading Base 是同一 Reading Record 内不可变的正文。
- Reading Units 是 Stable Base 上不可变的编排、渲染和 parsed coverage 单位。
- Anchor Segments 是 Reading Unit 内 sentence-like 的文本锚点单位，用于用户选区、笔记、高亮和 span-bound layers；通常是句子，必要时可为 clause 或 fallback window。
- 文本坐标分两层：unit/segment facts 使用 Stable Base 绝对 UTF-16 offsets；span anchor 使用 `anchor_segment_id` + unit-local UTF-16 offsets，并由 Anchor Segment range 约束。
- Enhancement Layers 和 User Editorial Assets 只能引用 Stable Base / Reading Units / Anchor Segments，不能改写正文。
- Planner 可以提供切分建议，但 deterministic builder 拥有最终 offsets/hash/order。
- Base Plate Snapshot 只能从 Stable Base / Reading Units / Anchor Segments 投影生成，不是新的正文 truth。

## Stable Base Canonical Text

`reading_bases.text` 是唯一坐标来源。

生成 Stable Base 前可以做规范化；一旦写入，后续渲染、切分、anchor validation 都必须使用完全相同的字符串。

Stable Base 是输入适配后的可读英文正文。复杂输入处理发生在 Stable Base 之前：

- 低影响输入适配可直接生成 Stable Base。
- 高影响输入适配必须先生成 Candidate Reading Base，经用户 preview / edit / confirm 后再冻结 Stable Base。
- Unit Builder 不负责 OCR 修复、网页正文抽取、boilerplate 删除、多栏顺序修复、表格/代码降级或正文重写。

D2-S1 采用以下 canonical 口径：

- 输入解码为 Unicode 字符串。
- 行尾统一为 `\n`。
- 可以移除明显的首尾空白，`3+` 个连续空行可压缩为 `2` 个。
- 可以把常见 Unicode space 转为 ASCII space，并移除 zero-width / control characters。
- 不默认改写 smart quote、dash、ellipsis、大小写等可见作者文本。
- 不默认删除 URL、email、code fence、中文括注等内容；如果这些内容影响可读性，Input Adapter 应把记录路由到 Candidate Base 或 action-required policy。
- 如需 NFC/NFKC 等 Unicode normalization，必须作为显式 builder policy，并记录 `canonicalizer_version`；D4 默认不引入会改变 code unit 的额外归一化。

`reading_bases` 建议同时保存：

- `text`
- `content_sha256`：对持久化 `text` 的 UTF-8 bytes 计算，用于 base identity。
- `canonicalizer_version`
- `builder_version`
- `source_refs`

`content_sha256` 不用于 span anchor validation。

## 文本坐标

Claread 已有前后端共享的 anchor 合同：

- offset unit：JavaScript UTF-16 code units。
- text range hash：`fnv1a32-utf16`。
- 后端实现：`services/api/app/contracts/annotation.py`。
- 前端 contract：`packages/contracts`。

新 Reading Units 和 Enhancement Layer anchors 必须复用这一合同。

坐标分层：

| 坐标 | 字段 | 用途 |
|---|---|---|
| Stable Base absolute | `base_start_utf16` / `base_end_utf16` | Reading Unit、Anchor Segment 回源校验 |
| Unit local constrained by Anchor Segment | `start_offset` / `end_offset` + `anchor_segment_id` | 用户选区、笔记、高亮、span-bound layers |

约束：

- Unit / Anchor Segment 的 absolute offsets 基于 `reading_bases.text`。
- Span-bound layer 的 `start_offset` / `end_offset` 基于 `reading_units.text`，并且必须落在 `anchor_segment_id` 对应 Anchor Segment 的 unit range 内；多数 segment 是 sentence，少数可为 clause 或 fallback window。
- Segment-local offsets 只作为 projection 阶段从 unit-local offsets 派生的 metadata，不作为持久 domain anchor。
- Span-bound layer 必须保存 `selected_text` 和 raw 8-char `text_hash`。
- `text_hash` 字段不带 `fnv1a32-utf16:` prefix；算法通过常量或 `hash_algorithm` 字段表达。
- 发布前必须用 `slice_by_utf16_offsets` 和 `compute_text_range_hash` 重新校验。
- 如果需要更强内容身份校验，新增 `content_sha256` 字段，不替代 `fnv1a32-utf16` anchor hash。

## Reading Unit Builder

D4 默认 builder：

1. 输入 `reading_bases.text`。
2. 按结构块优先切分：显式标题、空行段落、列表、引用等 metadata 优先；D4 纯文本路径先使用空行段落。
3. 在结构块内生成 Anchor Segments：优先英文句子边界；句子边界不可靠时使用 clause；仍不可用时使用 fallback window。
4. 按目标长度把相邻 Anchor Segments 聚合为 Reading Units；不在单词内部切分。
5. 为每个 unit 写入 `unit_id`、`order_index`、`unit_type`、base absolute UTF-16 offsets、unit text hash、boundary quality。
6. 为每个 Anchor Segment 写入 `anchor_segment_id`、兼容 `sentence_id`、`segment_type`、`unit_id`、`paragraph_id`、`order_index`、base absolute UTF-16 offsets、segment text hash。
7. 校验 units 覆盖非空正文，unit 间未覆盖字符只能是 whitespace separators，且 order 单调。

默认 unit 是阅读和增强的最小稳定单位，不保证等于一句话。

默认 Unit 也不保证等于自然语义段落。它是 Claread 在 Stable Base 上用于渲染、translation scheduling、parsed coverage、progressive events 和成本控制的稳定工作单元。

D4 ID 口径：

- 采用 1-based IDs：`u1`、`p1`、`s1`。
- 排序只依赖 `order_index`，不依赖字符串排序。
- 不继承旧 fallback 中的 `p0` / `s0` 口径。

Sentence segmentation：

- D4 默认使用 deterministic regex fallback。
- 如果后续改用 spaCy，必须 pin model/version，并保存 `segmenter_version`。
- 同一 Stable Base 冻结后，不允许因 segmenter 升级改写既有 units 或 segments；需要重新适配时创建新 Reading Record 或 supersede。
- `segment_type = sentence` 时可以暴露为句子；`segment_type = clause` 或 `fallback_window` 时，UI 和 layer 不应假设它是真实句子。

长文本无换行时的处理：

1. 将整段视为一个结构块。
2. 优先按英文句末标点生成 sentence Anchor Segments。
3. 若没有可靠句末标点，按分号、冒号、破折号、逗号连接词等 clause 边界降级。
4. 若 clause 边界仍不可用，使用 word window 生成 `fallback_window` Anchor Segments，并标记 `boundary_quality = low`。
5. 聚合相邻 Anchor Segments 形成 Reading Units，保证 coverage、order、offset/hash 正确。

Planner suggestion：

- Planner 可输出 `planner_suggestion_json`，例如建议某些段落合并或拆分。
- Builder 可以读取 suggestion 作为启发，但最终必须 deterministic。
- 如果 suggestion 与 builder invariant 冲突，builder 优先。
- suggestion 只作为 audit trace 或后续 Candidate Base 改进信号。

LLM 介入边界：

- D4 不启用 LLM Unit Builder。
- D5+ 可以引入 Unit Boundary Refiner，但它只能建议既有 Anchor Segments 的 split/merge 组合。
- Refiner 不得改写 Stable Base 文本，不得生成新 offsets，不得绕过 hash/coverage/order validator。
- Refiner 输出不通过校验时直接丢弃，保留 deterministic baseline。
- 触发条件来自 builder quality signals，例如无换行长文本、PDF/OCR 残留结构异常、过多 `fallback_window`、Unit 长度异常或括号/引号边界明显不自然。

## Navigation Skeleton

`article_ready` 需要基础 Navigation Skeleton。

D4 只需要最小导航：

- record title
- unit order
- optional paragraph/section heading

Semantic Outline 是 optional Enhancement Layer，不能阻塞 `article_ready`。

## Base Plate Snapshot

Web Reader Article Body 使用 Plate.js 渲染。`article_ready` 前，Reading Base Builder 或 Projection 层必须能生成 Base Plate Snapshot。

Base Plate Snapshot 约束：

- source text 来自 `reading_bases.text`。
- 每个 Reading Unit / Anchor Segment node 必须携带稳定 ids 和 UTF-16/hash metadata。
- Plate node path 不持久化；只在前端作为当前 tree 的临时地址。
- Snapshot 可以包含标题、段落、列表等低风险结构 metadata，但不得改写 Stable Base 文本。
- 如果未来 `base_document` 保存 Markdown/HTML 结构，它仍必须可映射回 `canonical_text` 的 UTF-16 offsets。

最小 node metadata：

```json
{
  "type": "reader_anchor_segment",
  "owner": "stable",
  "base_id": "base_...",
  "unit_id": "u1",
  "anchor_segment_id": "s3",
  "segment_type": "sentence",
  "base_start_utf16": 120,
  "base_end_utf16": 220,
  "text_hash": "1a2b3c4d",
  "hash_algorithm": "fnv1a32-utf16"
}
```

## `article_ready` Gate

`article_ready` 只能由 domain milestone gate 写入。

前置条件：

- `reading_records` 存在且未 cancelled / superseded。
- Stable Reading Base 已写入。
- Reading Units 已生成并通过 offsets/hash 校验。
- Base Plate Snapshot 已生成，并能从 Anchor Segment node 回源校验。
- Navigation Skeleton 已生成。
- 基础 metadata 已存在：title、language、source metadata。

禁止依赖：

- translation layer
- vocabulary / grammar_note / sentence_analysis layer
- summary / Semantic Outline
- RAG substrate
- Ask sidecar
- full parse coverage

Focused test 必须覆盖：

- 非空低风险文本输入可以在没有任何 enhancement layer 时达到 `article_ready`。
- enhancement worker 不允许在 `article_ready` 前发布 layer。
- Stable Base 冻结后正文不可改写。
- Unit absolute offsets 能 slice 回 unit text。
- `anchor_segment_id` + unit-local span 能使用现有 UTF-16/hash contract 校验。

## D2-S1 结论

D2-S1 已验证：

- 英文 paragraph + sentence fallback 可以生成稳定 units，但必须记录 builder/segmenter version。
- Web selection / anchor bridge 应输出 `anchor_segment_id` + unit-local offsets；多数 segment 是 sentence，fallback segment 必须显式标记类型。
- `fnv1a32-utf16` 可复用为 unit、segment 和 selected text hash。
- 旧 `prepare_input` 可拆件复用，但不能原样作为 Stable Base builder。

详细结果见 `spikes/D2-S1-reading-unit-builder-result.md`。
