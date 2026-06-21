# D2-S1 Reading Unit Builder Result

> 日期：2026-06-18
> 状态：`accepted_with_changes`
> 输入文档：`modules/reading-base-and-units.md`

## Verdict

Reading Unit Builder 方向成立，可以进入 D3/D4。

必须修正两点后实施：

- Reading Unit 不等于用户选区锚点坐标。新增 sentence-like Anchor Segment，保留现有 `sentence_id` 兼容字段。D5-V2 实现已把 span anchor offset 固化为 unit-local UTF-16 offsets，并用 `anchor_segment_id` 约束到具体 segment。
- 旧 `prepare_input` 可以拆件复用，但不能原样作为 Stable Base builder。Stable Base canonicalizer 必须更保守，避免默认改写 smart quotes、dash、大小写等可见作者文本。

## Checked Evidence

本 spike 读取了以下当前实现：

- `services/api/app/contracts/annotation.py`
- `packages/contracts/index.js`
- `packages/contracts/README.md`
- `services/api/app/services/text_anchors.py`
- `services/api/app/services/analysis/postprocess/utf16_offsets.py`
- `services/api/app/services/analysis/preprocess/input_preparation.py`
- `services/api/app/services/analysis/postprocess/projection.py`
- `services/api/app/services/reader_scene.py`
- `apps/web/src/lib/reader-plate/projection/render-scene-to-plate-document.ts`
- 相关 tests：`test_utf16_offsets.py`、`test_user_annotations.py`、`test_reader_notes.py`、`test_projection.py`

## Key Findings

1. Claread 已有稳定的跨端 anchor 合同：offset unit 是 JavaScript UTF-16 code units，hash 是 `fnv1a32-utf16`，前后端实现一致。
2. 当前旧实现的用户高亮、笔记、Ask anchor 主要依赖 `sentence_id` 和句内局部 offsets，不是全文 offset；新合同以 `anchor_segment_id` 保留句/segment 归属，并使用 unit-local offsets 作为持久 span anchor 坐标。
3. 当前 projection 已经有 fail-closed 的 UTF-16 range validation，可以复用思路。
4. 旧 `prepare_input` 包含有价值资产：HTML/Markdown/URL 清理、Unicode space/invisible character 处理、PDF 软换行、段落/句子 span、spaCy/regex 降级、质量信号。
5. 旧 `prepare_input` 也有不适合作为 Stable Base 默认行为的部分：会把 smart quotes 变 straight quotes、dash 变 hyphen、删除 URL/email/code/chinese parenthetical。它们适合作为 extraction/sanitization policy，不适合作为低风险 Stable Base 的无条件文本改写。
6. 旧实现存在 ID 口径不一致：主要 analysis path 使用 `p1`/`s1` 起始，`reader_scene.py` fallback 使用 `p0`/`s0`。新 builder 必须统一 1-based ID。

## Accepted Contract

D3/D4 采用三层文本坐标：

| 层 | 用途 | 坐标 |
|---|---|---|
| Stable Base | record 内唯一正文事实源 | `reading_bases.text` |
| Reading Unit | 编排、translation、parsed coverage、navigation | base-absolute UTF-16 offsets |
| Anchor Segment | 用户选区、span layer、笔记/高亮/Ask anchor | `anchor_segment_id` + unit-local UTF-16 offsets，通常 sentence；必要时 clause/fallback window |

D5-V2 implementation note：segment-local offsets 仍会出现在 Plate source leaf projection metadata 中，例如 `segment_start_utf16` / `segment_end_utf16`，但它们是从 unit-local anchor offsets 派生的渲染坐标，不是持久 domain anchor。

Reading Unit Builder 输出：

- `reading_bases.text`
- `reading_bases.content_sha256`
- `reading_units`：`unit_id`、`order_index`、`unit_type`、`base_start_utf16`、`base_end_utf16`、`text_hash`
- `anchor_segments`：`anchor_segment_id`、兼容 `sentence_id`、`segment_type`、`unit_id`、`paragraph_id`、`order_index`、`base_start_utf16`、`base_end_utf16`、`text_hash`
- Navigation Skeleton：title、unit order、optional heading metadata

Hash 存储 raw 8-char hex；算法通过 `hash_algorithm = fnv1a32-utf16` 或全局常量表达，不把 `fnv1a32-utf16:` prefix 混入 `text_hash` 字段。

## Builder Policy

D4 builder：

- 使用 deterministic builder，不调用 LLM。
- Stable Base 默认只做低影响规范化：line endings、Unicode space/invisible character、首尾空白、重复空行。
- 不默认改写 smart quotes、dash、ellipsis、大小写。
- 不默认删除 URL/email/code/chinese parenthetical；这类内容应进入 source risk / Candidate Base 或被明确 policy 处理。
- unit 默认以段落为主；过长段落按 sentence boundary 合并成 sentence groups。
- 长文本缺少可靠句子边界时，按 clause boundary 降级；仍不可用时使用 word window，并标记 `segment_type = fallback_window` 与低 boundary quality。
- sentence segmenter D4 默认使用 deterministic regex fallback；如果后续使用 spaCy，必须 pin model version 并记录 `segmenter_version`。
- ID 采用 1-based：`u1`、`p1`、`s1`；排序依赖 `order_index`，不依赖字符串排序。
- Unit 覆盖非空正文；unit 间未覆盖字符只能是 whitespace separators。
- D4 不启用 LLM Unit Builder；D5+ Unit Boundary Refiner 只能建议既有 Anchor Segments 的 split/merge，不能改写文本、生成 offsets 或绕过 validator。

## Reuse Matrix

| 旧实现 | 结论 |
|---|---|
| `app/contracts/annotation.py` | 直接复用为 anchor/hash 合同来源。 |
| `packages/contracts/index.js` | 直接复用；Web 新 selection bridge 继续调用同一 hash 逻辑。 |
| `text_anchors.py` | 复用 validation 思路，改为从新 `anchor_segments` 读取，不再读取 `render_scene_json`。 |
| `input_preparation.py` | 拆件复用；不能整体作为 Stable Base builder。 |
| `postprocess/utf16_offsets.py` | 复用 Python offset -> UTF-16 offset 转换；invalid slice 语义要统一为 fail-closed。 |
| `postprocess/projection.py` | 复用 canonical span -> UTF-16 range 的 fail-closed projection 思路；不复用旧 RenderScene 输出。 |
| Web `render-scene-to-plate-document.ts` | 复用 UTF-16 range validation 与 diagnostics 思路；projection contract 会重写。 |

## Focused Tests For D3

- `StableBaseCanonicalizer` preserves smart quotes, em dash, ellipsis, emoji, and case.
- Line ending and blank-line normalization produce deterministic `content_sha256`.
- Builder emits 1-based `u1`/`p1`/`s1` and monotonic `order_index`.
- Unit absolute UTF-16 offsets slice back to exact unit text.
- Anchor Segment absolute UTF-16 offsets slice back to exact segment text.
- Span anchor with emoji uses `anchor_segment_id` + unit-local UTF-16 offsets and validates selected text/hash.
- Cross-segment selection is represented as `multi_text` over ordered Anchor Segments.
- Long paragraph split never cuts inside a word and leaves only whitespace gaps between units.
- No-newline long text produces sentence/clause/fallback Anchor Segments with explicit `segment_type` and valid coverage.
- Python `compute_text_range_hash` equals JS `computeUtf16FNV1a` on ASCII, CJK, emoji, smart quotes, and dash samples.
- Worker/publisher rejects span anchors whose unit-local offsets fall outside the target Anchor Segment range, or whose selected text/hash fails source grounding.

## D3/D4 Impact

- D3 schema should include `anchor_segments` or equivalent table/view. Do not rely on `reading_units` alone for user text selection.
- D4 translation can publish unit-level layer without span anchors.
- D5 vocabulary/grammar_note/sentence_analysis layers must target Anchor Segments for precise spans.
- Old `render_scene_json` stays out of the new path.

## Review Result

The D1 design direction is correct after these changes. The main risk was coordinate ambiguity. With Anchor Segments and explicit coordinate scopes, the design aligns with the existing Claread anchor contract and avoids making paragraph-sized units carry span-local selection semantics.
