# Translation V2 / 双语文档显示方案 — 架构评估报告
*日期：2026-06-27 | 类型：架构评审 | 置信度：中高（基于代码现状 + 1 个外部参考）*

> 范围：为 Claread `/app/reader-record/{recordId}` 新版 Reader Record 页面的 **Translation V2** 与**双语文档显示**提出落地建议。聚焦 Reading Base 切分、Translation worker 输入输出、Translation domain truth 与 display grouping 的边界、页面视觉体验、候选方案比较以及推荐实施顺序。
>
> 约束：不修改代码；不把 Plate value / DOM selection / Slate path 当业务事实源；不把 Markdown 语法写入 `reading_bases.text`；legacy `/app/reader/{recordId}` 不动。

---

## 0. Executive Summary（TL;DR）

1. **不要扩展 unit translation 的窗口**。当前 `TranslationLayerOutput` 只有 `translated_text`，V1 是"覆盖整段"的退化形态（projection 里只能显示为 unit 边界内的"本段译文"块，参见 `reader-record-plate-document.ts:793-819` 和 `block-quote` 渲染），在第一句 anchor segment 后强行插入会破坏 anchor alignment，且 worker 拿不到任何 sentence-level grounding。
2. **推荐采用"中间方案 + 极小 worker 增量"**：保持 worker 的 unit 输入窗口，输出新增 `items: [{ anchor_segment_id, source_text, translated_text, confidence }]`，让 publisher 把 per-segment items 落库为 `enhancement_layers` 的子结构；display group 由前端 deterministic 派生（不再让 worker 提议 placement）。
3. **Reading Base 切分已经够用，不需要重做 builder**。当前 `_segment_sentence_spans` / `_segment_clause_spans` / `_segment_fallback_windows`（`base_builder.py:415-553`）已经能产出 sentence / clause / fallback 三类 segment；只要 projection 不再把 unit 译文塞到第一个 segment 后，V2 就能立刻拿到稳定 anchor。
4. **页面视觉**：translation 视觉应当**沿用 quote 形态 + 灰度降级 + 来源 lane 分离**；不要把译文塞进原文段落的 mark。推荐 grammar/sentence_analysis/vocabulary 都在"lane 隔离"而非"插行"模型下渲染。
5. **最大风险**：(a) LLM 输出 `items` 不对齐 / 漏 segment；(b) V1/V2 schema 并存导致 worker / publisher 分叉；(c) projection 把 unit 译文错挂到 anchor segment 后。本报告给出对应缓解策略与必做的 spike。
6. **第一阶段不做**：worker `placement_hints` / `display_groups`、group-level translation 的持久化、persistent AI-text note/highlight、sentence-analysis chunk offset V2。

---

## 1. 现状事实（不是抽象架构讨论）

### 1.1 Reading Base / Units / Anchor Segments 切分已经稳定

- `services/api/app/services/reader_orchestration/base_builder.py:177-376` 是 deterministic builder：
  - `_split_structure_blocks`（`base_builder.py:386-412`）按可见行做空行分段，产生 structure block；
  - 一个 structure block 在 D5 baseline 下**仍是一个 Reading Unit**（参见 `reading-base-and-units.md:117` "1 structure block -> 1 reading unit"）；
  - 在 unit 内再走 `_build_segment_spans` → `_segment_sentence_spans` / `_segment_clause_spans` / `_segment_fallback_windows`，把 unit 切成 sentence / clause / fallback_window 三类 anchor segment。
- offsets / hash 完全基于 Canonical Text Layer（当前是 `reading_bases.text` 的 plain text）的 UTF-16 + `fnv1a32-utf16` 合同（`base_builder.py:281-282`）。
- 因此：**"每段一句话"的"诗歌感"问题并不来自 anchor segment 本身，而来自前端把 translation 渲染时强行切到第一句后面**。修复点完全在 projection 与 worker schema。

### 1.2 Translation worker V1 是 unit 窗口、整段输出

- `translation_worker.py:113-163`：worker 当前读 unit 全文本（`source_text`），用 PydanticAI `Agent(output_type=TranslationLayerOutput, …)` 产出 `translated_text` 整段。
- `TranslationLayerOutput`（`reader_orchestration.py:135-142`）：只有 `translated_text / notes / confidence / target_language`，**没有任何 per-segment 对齐字段**。
- Prompt（`reader_layer_translation.yaml:1-18`）也只要求"翻译给定 `source_text`"，没有要求它做 alignment。
- `TranslationLayerPublisher.publish_unit_translation`（`layer_publisher.py:116-355`）把整段 `translated_text` 当作 layer payload 写入 `enhancement_layers.output_json`，并通过 `_validate_text_range_anchor` 类的强 anchor 校验，但**目前只对 vocabulary / grammar / sentence_analysis 校验，translation 自身没有 anchor 校验**（因为没有 anchor）。
- `target_scope` 在 publisher 里硬编码为 `'unit'`（`layer_publisher.py:221`），DB 上 `enhancement_layers.target_key = unit_id`。

### 1.3 Projection 当前把 unit 译文渲染为 blockquote

- `reader-record-plate-document.ts:793-819` 的 `buildBlockquoteBlock` 只接受 `node.target_scope === "unit"`，因此只产出 `reader_record_unit_translation`，映射为 `type: "blockquote"` block。
- `mapUnitToBlocks`（`reader-record-plate-document.ts:945-976`）在 iteration `unit.children` 时：
  - `reader_source_block` 内的每个 anchor segment 都被渲染为 `paragraph`；
  - 同一个 unit 的 translation node 被统一放在 source 块**之后**，作为一个 blockquote；
  - 视觉是"全部原文 → 全部译文"，符合 unit-level fallback 约束。
- 当前 `ReaderRecordPlateDocument` schema 在 README 注释里仍叫 V2（`reader-record-plate-document.ts:42`），但事实上仍是 V1 形态："unit 级译文显示为'本段译文'"。这是 V1c 的设计约束。

### 1.4 视觉实现已经留好了 lane 接口

- `reader-record-plate-document-ui.md:583-618` 的"译文 V1/V2"章节明确：
  > 后续 translation worker/schema 升级为：`TranslationLayerOutputV2 = { items: Array<{ anchor_segment_id, source_text, translated_text }>, full_translation?, confidence, notes }`。前端再把 1-3 个连续 anchor segments 合并为 translation pair group。
- 同时 Plate 视觉优先级的 Lane 规则（`reader-record-plate-document-ui.md:624-668` 的"Marks / Cues Conflict Resolver"）已经定义了 source / translation / inline_mark / system_cue / supplement_cue / user_cue 六类 lane；translation pair group 正好落在 `translation` lane，不会和 vocabulary / grammar mark 冲突。

### 1.5 Anchor / persistence 的硬约束（不可逾越）

- span anchor 必须满足 `ReaderTextRangeAnchor`（`reader_orchestration.py:108-132`）：`base_id / unit_id / anchor_segment_id / segment_type / start_offset / end_offset / selected_text / text_hash` 八件套，offsets 与 selected_text UTF-16 长度一致、`text_hash = fnv1a32-utf16(selected_text)`。
- enhancement layer publisher 已经强制这些约束（`layer_publisher.py:1025-1088` `_validate_text_range_anchor`），任何 worker 输出都会被拒。
- 因此，**translation items 如果要落地持久化，必须满足这套 anchor contract**，不能直接存一段"看起来对得上的中文"。

---

## 2. 问题 1 — Reading Base / Units / Anchor Segments 切分够用吗？

### 2.1 答案：当前 builder 够用，**不要为了 Translation V2 重新切**

| 维度 | 现状 | 评估 |
|---|---|---|
| structure block → unit | D5 baseline `1→1`（`reading-base-and-units.md:117`） | 单段长文会让 unit 包含多个 sentence anchor。当前 builder 在 `_classify_unit_type`（`base_builder.py:584-596`）把整段长文归为 `body`，单结构块就是单 unit。这会让 unit translation 退化到"段落级"。 |
| sentence / clause / fallback | `_build_segment_spans` 三层降级 | 满足对齐需求。 |
| Boundary quality | sentence > 280 字符 → `low`（`base_builder.py:554-557`） | 长难句会被标 `low`，projection 可以据此决定"是否仍做 sentence 级 translation"还是退化为 unit 级。 |
| unit text hash | `compute_text_range_hash(block_text)`（`base_builder.py:342`） | 可在 worker 端反查校验；现 publisher 没做 translation 的 hash 校验。 |

### 2.2 真正的痛点不是切分，是 worker / projection 的"每段一句"错觉

- 痛点来源：worker 拿整段 unit，输出整段 `translated_text`；projection 又把这段译文渲染成 blockquote 块，**这本身没有问题**——问题在于用户看到的是"原文 → 译文"，**没有 anchor-level 对应**，所以：
  - 选某一句原文时，不能自动 highlight 对应中文；
  - 译文可能与原文不一一对应（一句英文拆两句中文、两句英文合一句中文），但 UI 上无法表达。
- 修复路径：让 worker 把 `translated_text` 拆成 per-segment items；projection 把 items 重新组织成 1–3 段合并的 pair group，**而不是**把每个 sentence anchor 都单独对应一个 item 块。

### 2.3 关键约束（建议写进 builder 文档）

1. **anchor segment type 不能是 `unknown`**：`AnchorSegmentType = "sentence" | "clause" | "fallback_window"`（`reader_orchestration.py:90`）。projection 在 V2 应当对 `fallback_window` 显式降级（合并相邻为更大的 pair group，或回退到 unit-level 译文）。
2. **boundary_quality == "low" 的 segment 不强行做 per-segment 对齐**：`reader_orchestration.py:91` 的 `boundary_quality: Literal["normal", "low"]`。projection 应当把"low quality sentence"折叠进 unit-level fallback。
3. **anchor_segment_id 必须稳定**：`base_builder.py:296-299` 顺序生成 `f"s{len(anchor_segments) + 1}"`。`reader_orchestration.py:103-104` 要求 `text_hash` 是 8-hex chars，`anchor_segment_id` 至少 1 char。建议 V2 不要让 worker 重新发现 anchor_segment_id，**只接受 worker 引用现有 anchor_segment_id**，publisher 校验它属于该 unit / base。

---

## 3. 问题 2 — Translation worker 应该用什么输入上下文和输出粒度？

### 3.1 输入窗口：保持 unit，但 prompt 必须给 anchor segment ids

**推荐**：

```text
INPUT (worker context):
- reading_record_id / base_id / unit_id / expected_generation
- reading_goal / variant_id (作为 alignment hint，可选)
- source_language / target_language
- source_text: 整段 unit 文本
- anchor_segments: [{ anchor_segment_id, segment_type, boundary_quality, text }]
```

**为什么不放大到 record 上下文**：
- 上下文越长，token 成本和 hallucination 风险越高；
- Claread 已经有 unit-level 的 grammar/vocabulary/sentence_analysis 三层 worker，translation 用 unit 窗口与之一致；
- D5 worker lease + retry 已经按 unit 设计，窗口放大要重新设计 policy 和 budget。

**为什么不缩小到单句**：
- 单句没有上下文会导致代词、省略、修辞翻译错；
- translation worker 的 SLA 要稳定，跨 segment 的 idiom 处理需要 sentence-pair 视野；
- PydanticAI `Agent(retries={"output": 2})` 已经能容忍局部字段错误，但单句场景下整段失败重试的成本太高。

### 3.2 输出 schema：V2 必须支持 per-segment items

**推荐 V2 schema**（与已存在的 `reader-record-plate-surface-ui.md:596-608` 草案对齐，但显式收紧）：

```python
class TranslationItemV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchor_segment_id: str = Field(min_length=1)
    source_text: str = Field(min_length=1)
    translated_text: str = Field(min_length=1)
    confidence: Literal["low", "normal", "high"] = "normal"
    spans: list[tuple[int, int]] = Field(default_factory=list)  # debug only


class TranslationLayerOutputV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = 2
    target_language: str = Field(min_length=1)
    items: list[TranslationItemV2] = Field(min_length=1)
    full_translation: str | None = None      # optional, for unit-level fallback
    notes: list[str] = Field(default_factory=list)
    confidence: Literal["low", "normal", "high"] = "normal"
```

关键约束（要在 publisher / schema validation 强制）：

1. **`items[].anchor_segment_id` 必须属于本次 job 的 unit**。publisher 应在 `_load_unit_anchor_validation_context`（`layer_publisher.py:959-1022`）时已经拿到了 `segments_by_id`，逐项校验。
2. **`items[].source_text` 必须 == `slice_by_utf16_offsets(unit_text, segment.unit_start_utf16, segment.unit_end_utf16)`**。这是 anchor contract 的最小条件，对应 `ReaderTextRangeAnchor.selected_text`。
3. **`items[].source_text` 的 hash 必须等于对应 anchor segment 的 `text_hash`**。publisher 已有的 `_validate_text_range_anchor`（`layer_publisher.py:1025-1088`）已经把这条逻辑实现，直接复用。
4. **`items` 必须覆盖 unit 内所有 `sentence` 类型 anchor segments**；`clause` / `fallback_window` 可以合并或省略，但省略时 projection 必须 fallback 到 unit-level。
5. **多余 items 直接拒**。不允许 worker 出现"幻觉 segment id"。
6. **`full_translation` 是可选 fallback**：当 `items` 不完整或某些 segment `boundary_quality="low"` 时，projection 用 unit-level 兜底。

### 3.3 reading_goal / variant 怎么注入

- 当前 `translation_worker.py:439` 从 `job.input_json->>'target_language'` 取 target_language，但没看到 `reading_goal` / `variant` 字段。
- 建议把 reading_goal / variant 作为 **prompt 指令 / system prefix**，**不进入 schema**：
  - 例如 `reading_goal=explanation` → prompt 中追加"解释型翻译：补足省略主语、显化逻辑连接"；
  - 例如 `variant=academic` → prompt 追加"学术语气、保留术语"；
  - 例如 `variant=captions` → prompt 追加"字幕式短句"。
- **不要让 reading_goal / variant 改变 items 字段**。items 字段永远是 { anchor_segment_id, source_text, translated_text, confidence }；差异化翻译完全通过 prompt 控制。

### 3.4 Prompt 模板要点（建议草案）

```yaml
description: Reader orchestration translation V2 worker system instructions
content: |
  你是 Claread Reader Orchestration 的 translation V2 worker。
  任务只针对单个 reading unit，输出结构化 `TranslationLayerOutputV2` (schema_version=2)。

  输入字段：
    source_text: 整段 unit 原文。
    anchor_segments: 必填。每一项包含 anchor_segment_id, segment_type, boundary_quality, text。
                      必须使用 anchor_segments 给出的 anchor_segment_id，不要自创。
    reading_goal / variant: 可选。影响翻译风格（见下方要求）。
    target_language: 必须回填。

  输出要求：
    items 必须按 anchor_segments 顺序输出，覆盖所有 sentence 类型的 segment。
    clause / fallback_window 段可以合并到相邻 items 或省略；省略时由 unit-level fallback 兜底。
    items[].source_text 必须等于该 anchor_segment 的 text。
    items[].translated_text 可以是 0 到多句中文；不要求与原文一一对齐。
    full_translation 仅在 items 不完整时输出，做为 unit-level fallback。
    confidence 仅在翻译不确定或歧义保留时使用 low；正常 high-quality 翻译用 high。

  翻译风格（由 reading_goal / variant 决定）：
    - explanation: 保留原句顺序，必要时补足省略主语，显化逻辑连接。
    - academic: 学术语气，保留术语、避免口语化。
    - captions: 短句优先，避免长定语。

  不要输出 Markdown、不要输出解释段落、不要输出 schema 之外的字段。
```

### 3.5 Worker 健壮性建议

- 把 `output_type=TranslationLayerOutputV2`，并允许 V1 兼容：
  - 短期：worker 同时输出 V1 `translated_text` 和 V2 `items`，publisher 检测 `schema_version`；V2 路径成功后停发 V1；
  - 长期：把 V1 标 deprecated，所有 path 走 V2。
- `Agent(retries={"tools": 1, "output": 2})`（沿用 `translation_worker.py:144`）即可；不要把 retries 调到 3+，否则单 segment 失败会拖慢 unit。
- **不要做 streaming output**（`Pydantic AI run_stream`）。理由：translation items 是结构化列表，partial items 不能落库；落地必须等整段产出。streaming 反而会让 publisher 拿到残缺 schema。

---

## 4. 问题 3 — Translation domain truth 与 display grouping

### 4.1 答案：domain truth 是 per-segment items，display grouping 是 deterministic projection

**Domain truth 必须包含**：
- per-segment items（落库）
- 可选 unit-level `full_translation`（fallback 用途，**不作为主展示**）
- `target_language` / `confidence` / `notes`（与 V1 兼容）

**Domain truth 不包含**：
- display group id
- placement hint / after_anchor_segment_id
- 视觉密度 / "compact"/"detailed" 这种 UI 字段
- language-specific 字号、颜色

理由：
1. `enhancement_layers` 是 worker 输出的真值（`layer_publisher.py:196-247` 的 `enhancement_layers.output_json`），schema 加 display 字段会让 worker 输出变成"projection 的输入"，违反 `plate-reader-projection.md:30-42` 的"Plate document 是 Web projection"原则。
2. `display_group` 在 rebuild 时可以从 items 重新计算，没有保留价值。
3. 一旦让 worker 给 placement hint，worker 会因为 prompt 偏差产生与 projection 冲突的 placement，校验/降级/回退成本极高。

### 4.2 持久化方案（必须二选一，推荐方案 A）

#### 方案 A：per-segment items 落库为 enhancement layer 的 output_json 子结构（**推荐**）

- 不新增表，不新增 layer_type；
- `enhancement_layers.output_json` 直接存 V2 schema（`schema_version: 2`）；
- `target_scope` 仍为 `'unit'`；
- `coverage_json` 增加：
  ```json
  {
    "anchor_segment_ids": ["s1","s2",...],
    "items_count": 3,
    "fallback_segments": ["s4"],
    "alignment_failures": []
  }
  ```
- `quality_json` 记录 alignment 失败统计；
- pros：最小迁移；与 vocabulary / grammar / sentence_analysis layer shape 对齐；
- cons：`output_json` 较大（每个 unit 多出若干 items JSON），但相对于 vocabulary / grammar 已经存在的 mark 列表，体量在同一量级。

#### 方案 B：新增 `enhancement_layer_items` 表（schema 升级）

- 字段：`layer_id, anchor_segment_id, source_text, translated_text, confidence`；
- pros：查询 "哪些 segment 还没有 translation" 简单；与 vocabulary item 表 schema 接近；
- cons：需要 migration、新的 repository、新 publisher，**实现成本高、回归测试面广**；V2 阶段不值得。

**结论**：方案 A。

### 4.3 Display grouping 由 projection deterministic 生成

#### 推荐 grouping policy（前端 pure function）

输入：
- unit 的 anchor segments（sentence / clause / fallback_window）
- unit 的 translation V2 items（按 anchor_segment_id 索引）

输出：
- `TranslationPairGroup[]`，每个 group 包含：
  - `anchor_segment_ids: string[]`
  - `source_text: string`（按 anchor segment 顺序拼接，空格隔开）
  - `translated_text: string`（按 items 顺序拼接，空格隔开）
  - `placement_reason: enum`
  - `quality: "normal" | "fallback" | "low_boundary"`

#### 推荐规则

按以下优先级执行（伪代码）：

```ts
function groupSegmentsForTranslation(
  segments: AnchorSegment[],
  items: Map<string, TranslationItemV2>,
): TranslationPairGroup[] {
  const groups: TranslationPairGroup[] = [];
  let buffer: AnchorSegment[] = [];
  let flushBuffer = (reason) => { /* emit group using buffer + items, then clear */ };

  for (const seg of segments) {
    // 1. boundary_quality=low 的 segment: 合并到当前 group；不再细分
    // 2. clause / fallback_window: 不单独成组，合并到上一组
    // 3. sentence 且 items 中存在：buffer 累积；累计到 3 段或遇到 grammar cue 时 flush
    // 4. sentence 但 items 中缺失（fallback_segment）: flush 当前 buffer（使用 full_translation 兜底或显示 unit-level fallback），单独 fallback group
    // 5. quote / list / heading 边界: flush + new group
  }
  flushBuffer("end_of_unit");
  return groups;
}
```

核心规则：
1. **不跨 stable document block**（heading / list / blockquote / table 等另起 group）。
2. **每组 1–3 个 sentence**（与 `reader-record-plate-surface-ui.md:612` 一致）。
3. **遇到 grammar cue**（grammar_note.spans 命中当前 segment 时）flush 当前 group，让 grammar cue 与对应译文分开。
4. **遇到 sentence-analysis segment**（sentence_analysis layer 命中当前 segment 时）flush 当前 group，让 structure lens 与译文分开。
5. **fallback window** 始终落入 fallback group，不强求 per-segment items。

### 4.4 Worker 输出和 deterministic grouping 冲突怎么办

| 冲突 | 行为 |
|---|---|
| items 缺某 sentence segment | 该 segment 与相邻 sentence 合并成 fallback group；projection 用 unit-level fallback text。 |
| items 多出 anchor_segment_id（不在 unit 内） | publisher 在 validation 阶段直接拒，整层 `failed_terminal`，**不写入**。 |
| items[].source_text 与 segment.text 不一致 | publisher 校验失败，整层 `failed_terminal`；写入 `alignment_failed` diagnostic。 |
| items[].translated_text 为空 / 长度异常（>3 倍 source 或 <0.3 倍） | publisher 记 diagnostic，但不拒；projection 用 normal confidence 渲染，由 UI 决定显示降级（淡灰 + "机器翻译仅供参考" 文案）。 |
| full_translation 与 items 拼接不一致 | 不强制一致；full_translation 仅作为 unit-level fallback 文案，不参与 items 校验。 |

### 4.5 避免前端硬切分导致双语对照语义错误

- 前端**不允许**用 character offset 切中文；必须按 anchor_segment_id 对齐；
- projection 的 group 必须保留 `anchor_segment_ids: string[]`，group 删除 / 折叠必须能反向定位到原始 anchor；
- group 不能跨 unit；跨 unit 必须由 projection 重新生成。
- 测试要求（必须写进 V2 characterization test）：
  - 单元翻译：`items` 缺 segment → fallback group；
  - 单元翻译：`items` 全覆盖 → 1-3 sentence / group；
  - 边界：clause / fallback segment → 不入 per-segment group，合并到相邻 sentence group；
  - 边界：sentence-analysis cue 命中 segment → group 在该 segment 前 flush；
  - 边界：grammar cue 命中 segment → group 在该 segment 前 flush；
  - 错误：items 多出 segment id → publisher 拒收，整层 failed_terminal；
  - 错误：items[].source_text 与 segment.text 不一致 → publisher 拒收。

---

## 5. 问题 4 — 页面视觉体验

### 5.1 Lane 模型（与现状 `reader-record-plate-document-ui.md:624-668` 对齐）

| Lane | 内容 | 默认形态 |
|---|---|---|
| `source` | anchor segment 原文 | serif 正文，最大字号，最大字重 |
| `translation` | translation pair group | 中文 sans-serif，~0.92em，淡灰（text-ink-soft），左侧 2px emerald-300 border + 缩进 |
| `inline_mark` | vocab/grammar/user mark | 浅底色或下划线，user highlight 优先 |
| `system_cue` | grammar cue / sentence-analysis cue | 编号 / underline，不进文档流卡片 |
| `supplement_cue` | ask supplement | 与 system_cue 视觉一致但归属 `ask_supplement` owner |
| `user_cue` | user comment / highlight | user color |

### 5.2 译文视觉的具体建议（基于现状 `ReaderRecordPlateSurface.tsx:511-524` 已有 blockquote 渲染）

当前 blockquote 样式：
```tsx
className="reader-record-plate-blockquote mt-3 border-l-2 border-emerald-300/60 bg-emerald-50/40 py-2 pl-4 pr-3 font-sans text-[0.95rem] leading-7 text-ink-soft"
```

**推荐调整**（ponytail：尽量复用现有 CSS、只在 V2 需要的细节上调整）：

1. **字号**：保持 `text-[0.95rem]` 不动；保持 `leading-7` 不动；如果做 per-segment group，可以在每组之间用 `mb-2` / `last:mb-0`，避免长译文"压成一段"。
2. **lane 视觉**：保留 emerald-300 左边线 + emerald-50 底色 + `text-ink-soft`。**不要换主色**，避免与 grammar cue（emerald-600 underline）混淆。
3. **字号层级**：原文 `text-[1rem]` / 译文 `text-[0.92rem]` / cue `text-[0.75rem]`。现状已经是这个方向。
4. **不要给译文加 inline mark**（vocab/grammar/user highlight）。Mark 是原文的事实；译文是派生层。
5. **不要把译文放进 paragraph block**。它应当始终在独立 lane（blockquote / 自定义 `reader_translation_pair` block）。

### 5.3 多个 layer 同时出现的间距控制

参考现状 `reader-record-plate-document.ts:945-976` 的 `mapUnitToBlocks`：当前每个 anchor segment 后挂 paragraph + grammar + analysis + supplement callouts。V2 在此基础上，每 N 个 sentence 后挂一个 translation pair group。

间距建议：
- source paragraph：`mb-3`
- translation pair group：`mb-4`
- grammar callout：`mb-2`，左侧 1.5px emerald-600 underline + 0.7rem label（"语法 · …"）
- sentence-analysis callout：`mb-2`，左侧 1.5px sky-600 line，结构化 chunk list
- ask supplement callout：`mb-2`，左侧 1.5px violet-500 dotted line

ponytail：这些都是 `mt-/mb-` 调整，**不要重新发明 spacing 系统**。

### 5.4 沉浸 vs 精读模式下的密度切换

参考 `ReaderRecordPlateSurface.tsx:1019-1026`：
```tsx
if (surfaceMode === "intensive") {
  return plateDocument.children;
}
return plateDocument.children.filter((block) => block.type === "paragraph");
```

**V2 沉浸模式**：默认隐藏 translation pair group，保留 source paragraph + grammar cue + user cue；
**V2 精读模式**：默认显示 translation pair group + grammar callout + sentence-analysis callout + supplement；
**保留** `mode_visibility` 字段（与 `TMP-reader-document-graph-design-2026-06-27.md:262-269` 对齐）：每个 group 携带 `display.mode_visibility.immersive` / `intensive`，由前端根据 readerSettings.mode 决定渲染。

### 5.5 Plate.js 原生能力 vs 自定义 wrapper（基于 `reader-record-plate-document-ui.md:97-127` 矩阵）

| 需求 | 推荐 |
|---|---|
| 主文档只读交互 | `<Plate readOnly>`（官方） |
| 划词 toolbar | `@platejs/floating`（官方）；按钮定制为 Claread action |
| 选区保持 | `CursorOverlayPlugin`（官方） |
| 用户高亮 | `CommentLeaf` mark 改造 / `highlight-node`（已落地） |
| 用户评论 | `CommentLeaf` + `InlineCommentPanel`（已落地） |
| 系统 marks/cues | 自定义 leaf / decoration（必须 Claread 自管） |
| **翻译 pair group block** | **自定义 `reader_translation_pair` element + 自定义 wrapper**（不依赖官方 callout，因为语义不同） |
| **grammar callout** | 官方 `@platejs/callout` 或自定义 element；选官方 callout 更省事（已有 CustomWrapper 经验） |
| **sentence-analysis structure block** | **始终展开的 element + 嵌套 chunk list**（不能用 toggle / collapse） |

ponytail：选型要复用，**不要为翻译重做一套 leaf plugin**。

---

## 6. 问题 5 — 三个候选方案比较

### 方案 A：保守 — 保持 unit translation，前端优化视觉

| 维度 | 评估 |
|---|---|
| Correctness | ✅ V1 已经能跑。 |
| UX | ⚠️ "本段译文" 块体验差，无法做 anchor-level 对应；选某句原文查词时仍要找译文。 |
| 实现成本 | 最低。前端只改 blockquote 样式，不动后端。 |
| 回归测试 | 最小。已有 `ReaderRecordPlateSurface.test.tsx` 覆盖。 |
| Ask/RAG 接入 | 仍按 unit scope 检索，RAG citation 不能定位到 sentence。 |
| 长期可维护性 | ❌ 与 `enhancement-layers-and-parsed.md:23-30` "D5+ 升级到 per-segment items" 的方向不符；技术债累积。 |

### 方案 B（**中间方案，推荐**）：segment translation item + deterministic display grouping

| 维度 | 评估 |
|---|---|
| Correctness | ✅ Per-segment items 强制 anchor contract；publisher 在 `_validate_text_range_anchor` 框架上做最小扩展；deterministic projection 不依赖 worker 输出。 |
| UX | ✅ 用户能看到与原文自然对应的译文；选词查词时 RAG/Ask 也能定位。 |
| 实现成本 | 中等。后端：V2 schema + publisher 校验 + worker prompt 重写；前端：projection 函数 + pair group 渲染。预估 2–3 个 PR。 |
| 回归测试 | 中等。需要新增 `test_translation_layer_output_v2_validation`、`test_translation_publisher_v2_items`、`test_projection_group_segments`；现有 translation worker 测试需要 V1/V2 双跑。 |
| Ask/RAG 接入 | ✅ Ask 可以直接引用 `anchor_segment_id` 查到对应中文；RAG citation 在 per-segment 层。 |
| 长期可维护性 | ✅ 与"Reader Document View Model"（`TMP-reader-document-graph-design-2026-06-27.md:191-260`）方向一致；display grouping 是 projection 派生，不污染 worker 输出。 |

### 方案 C（激进）：translation worker 输出 semantic display groups / placement hints

| 维度 | 评估 |
|---|---|
| Correctness | ⚠️ Worker 输出 placement 是 LLM 行为，prompt 漂移会导致 placement 与 projection 冲突；publisher 需要在两层做 fallback，测试面翻倍。 |
| UX | ✅ 理论上更"自然"——LLM 可以判断"这两句英文合成一句中文"作为一对。 |
| 实现成本 | 高。worker prompt 复杂度上升；publisher 要支持 `display_groups[]` schema；projection 要做 trust-but-verify。 |
| 回归测试 | 高。worker 输出 schema 复杂，失败模式多；characterization test 必须覆盖所有 fallback 路径。 |
| Ask/RAG 接入 | ✅ 同一对 group 的 anchor 与翻译都在 worker 输出中，Ask 上下文更完整。 |
| 长期可维护性 | ❌ 把 UI 决策渗入 worker；`plate-reader-projection.md:30-42` 原则被破坏；后续任何 UI 调整都要重新发 worker。 |

### 决策：方案 B（中间）

理由：
1. 与现有架构方向一致（`enhancement-layers-and-parsed.md:23-30`、`reader-record-plate-surface-ui.md:594-608`、`TMP-reader-document-graph-design-2026-06-27.md:273-340` 都明确推荐 per-segment items）；
2. 与 Pydantic AI structured output 最佳实践一致（`output_type` + Pydantic model + `Field` 约束；不需要 LLM 做 placement）；
3. 实现成本可控、回归测试可行；
4. 把 UI 决策完全放在 projection 层，符合 domain-first 原则。

---

## 7. 问题 6 — 最终推荐方案

### 7.1 Phase 1（Translation V2 之前的清理工作）

> Phase 1 与 `reader-record-plate-document-ui.md:942-951` 的 V1a/V1b/V1c/V1d 切片对齐；本报告只关心 V2 相关。

1. **保持 V1 unit translation 行为不动**。
2. **不改 schema、不改 worker prompt**。
3. **前端视觉微调**（如有资源）：
   - blockquote 字号微调 `text-[0.92rem]`；
   - 增加 unit 级 fallback 文案 "本段译文"（已有）；
   - 让 translation blockquote 在 immersive 模式下默认隐藏（已有 `surfaceMode === "intensive"` filter）。
4. **写 characterization test**：固定 V1 行为，防止后续误改。

### 7.2 Translation V2 第一版（推荐实施）

#### Schema / 数据层
1. 新增 `TranslationLayerOutputV2`（`reader_orchestration.py`），不删除 V1。
2. 短期兼容：worker 输 V1 + V2（dual schema），publisher 按 `schema_version` 路由；老消费者继续读 V1，新消费者读 V2。
3. 中期：把 V1 标 deprecated，所有 worker / publisher 走 V2；保留 V1 deserializer 用于历史 layer 兼容。

#### Worker / Publisher
4. 重写 `reader_layer_translation.yaml` prompt：
   - 输入字段加 `anchor_segments: [{ anchor_segment_id, segment_type, boundary_quality, text }]`；
   - 输入字段加 `reading_goal / variant`（可选）；
   - 输出 schema 切到 V2。
5. 扩展 `TranslationLayerPublisher.publish_unit_translation`：
   - 检测 `output.schema_version`；
   - V2 路径：调用 `_validate_text_range_anchor` 校验 `items[].anchor_segment_id / source_text`；
   - 写入 `coverage_json`：`{ anchor_segment_ids, items_count, fallback_segments, alignment_failures }`；
   - 写入 `quality_json`：`{ alignment_failure_count, ... }`。
6. **不要修改 `target_scope`**，仍为 `'unit'`。

#### Frontend Projection
7. `reader-record-plate-document.ts`：
   - 替换 `buildBlockquoteBlock`：从 unit-level `reader_translation` node → `TranslationPairGroup[]` 派生；
   - 新增 `buildTranslationPairBlocks(unit, items)`：
     - 输入：`unit.children` 中所有 `reader_anchor_segment` + `reader_translation.output.items`；
     - 输出：`ReaderRecordPlateTranslationPairBlock[]`（自定义 block type）。
8. `reader-record-plate-to-plate-value.ts`：
   - 新增 `READER_TRANSLATION_PAIR_TYPE = "reader_translation_pair"`；
   - `paragraphBlockToElement` / `translationPairBlockToElement` 分流；
   - **不** 让 translation leaf 暴露 anchor_segment_id（anchor 已在 group 上）。
9. `ReaderRecordPlateSurface.tsx`：
   - 新增 `<TranslationPairBlock block />` 组件，复用 `text-ink-soft + emerald` lane 样式；
   - 在 immersive 模式下隐藏（沿用 `surfaceMode === "intensive"` filter）；
   - 视觉规则：
     - group 容器：`mt-3 mb-3 border-l-2 border-emerald-300/60 bg-emerald-50/40 py-2 pl-4 pr-3`
     - 中文：`font-sans text-[0.92rem] leading-7 text-ink-soft`
     - 顶部 label（极轻量）：`text-[0.65rem] uppercase tracking-[0.12em] text-emerald-700/70`，内容 "译文"（与现状 `ReaderRecordPlateSurface.tsx:516` 一致）
     - 段落间距：每个 group 之间 `mb-2`，最后一个 `last:mb-0`
10. 不动 grammar / sentence-analysis / supplement callout 的渲染规则（`reader-record-plate-document.ts:821-943`）；它们与 translation pair group 在同一 anchor segment 后顺序出现，互不干扰。

#### Ask / RAG 接入
11. Ask Claread 不变；anchor adapter 已能消费 per-segment anchor（`reader-record-plate-surface-ui.md:884-905`）。
12. RAG 索引：V2 之后，layer 索引可以补 `anchor_segment_id` 维度；V2 first 不强求。

#### 测试
13. 后端：
    - `test_translation_layer_output_v2_validation`：items 缺 segment、多 segment、source_text 不一致、hash 不匹配、anchor_segment_id 不存在。
    - `test_translation_layer_publisher_v2`：coverage_json 正确写入、alignment_failures 正确写入、quality_json 正确写入。
    - `test_translation_worker_v2_prompt`：fake executor 返回 V2，断言 prompt 包含 anchor_segments 列表。
14. 前端：
    - `test_projection_group_segments_v2`：sentence 累积 1-3 / fallback group / grammar cue flush / sentence-analysis flush / quote boundary。
    - `test_plate_value_translation_pair`：V2 items 渲染为 pair group blocks。
    - `test_reader_record_plate_surface_translation_v2`：immersive 隐藏、intensive 显示。

#### 迁移 / 文档
15. 在 `enhancement-layers-and-parsed.md` 增补 "D7 Translation V2" 章节。
16. 在 `reader-record-plate-surface-ui.md` 把 V1c "本段译文" 段落更新为 "Translation pair group"。
17. 在 `reading-base-and-units.md` 加注解：**V2 不需要 builder 改动**。

### 7.3 暂不做（明确划线）

1. ❌ Worker 输出 `placement_hints` / `display_groups`（方案 C）—— 与"Plate 是 projection"原则冲突。
2. ❌ Group-level translation 持久化（group 是 projection 派生）。
3. ❌ Persistent user note/highlight 在 AI 文本（translation / grammar / analysis / supplement）—— 已有 `reader-record-plate-surface-ui.md:1018-1024` 明示 disabled。
4. ❌ Sentence-analysis chunk offset V2（与 Translation V2 解耦）。
5. ❌ Ask Supplement 入文档（V1d 之后）。
6. ❌ Materialized backend view-model 表（Reader Document View Model 在 V2 阶段保持前端 pure function）。
7. ❌ Mini-program 同步消费 view model（先稳定 Web）。
8. ❌ reading_goal / variant 进入 schema 字段（保持 prompt 指令，不污染 items）。
9. ❌ Worker streaming 输出（schema 必须完整产出才能 publisher 校验）。

### 7.4 必须提前验证的 spike

#### Spike S1（必做）：prompt-only V2 alignment quality

- 目标：验证 prompt-only 引导 LLM 输出 `items[].source_text == segment.text` 的成功率。
- 方法：抽 10 段 unit，用 fake PydanticAI executor 跑 prompt，对齐 items 校验。
- 验收：items 全覆盖且 source_text 100% 一致 ≥ 80%；否则考虑"先让 worker 输出自由 text，publisher 用 difflib 对齐 segment"。
- 输出：1 份 prompt-eval 报告（不是代码），决定 V2 worker 是否走 prompt-only。

#### Spike S2（必做）：fallback / boundary 投影正确性

- 目标：验证 deterministic grouping 函数在 fallback segment、grammar cue flush、sentence-analysis flush 下的输出符合 UI 预期。
- 方法：写一个 vitest 单元测试，喂入 mock 数据，截图对比。
- 输出：projection 函数 reference impl + visual screenshot。

#### Spike S3（推荐）：PydanticAI V2 失败模式

- 目标：枚举 worker 输出 schema 错误的失败模式，验证 publisher 校验足以 fail-closed。
- 方法：故意构造缺失 / 多出 / hash 不一致的 V2 输出，验证 publisher 拒绝。
- 输出：失败模式列表 + publisher 拒绝覆盖率 ≥ 95%。

#### Spike S4（可选）：reading_goal 对译文质量的影响

- 目标：判断 reading_goal / variant 是否对用户感知翻译质量有显著影响。
- 方法：抽 5 段 unit，分别用 explanation / academic / captions prompt 跑，对比 3 个评审的盲评。
- 输出：决定 V2 first 是否带 reading_goal；不带不影响上线。

### 7.5 最大风险

| 风险 | 等级 | 缓解 |
|---|---|---|
| Worker 输出 items 与 anchor_segment_id / source_text 不一致 | **高** | 复用 `_validate_text_range_anchor`；V1/V2 dual schema 期间保留 V1 fallback；Spike S1 + S3 验证。 |
| Worker 漏 segment（items 不完整） | 高 | publisher 在 coverage_json 记 fallback_segments；projection 用 unit-level fallback 兜底；不在 V2 阶段强制 fail-closed。 |
| Projection deterministic grouping 与 sentence-analysis / grammar cue 冲突 | 中 | grouping 函数强制在 cue 命中的 segment 前 flush；characterization test 覆盖。 |
| V1 / V2 schema 并存导致老用户看 V1、新用户看 V2，体验分裂 | 中 | V2 first rollout：worker 切到 V2 后，所有新 unit 都用 V2；历史 unit 仍按 V1 渲染（projection 兼容）。 |
| Worker prompt 漂移导致 items 结构变化 | 中 | `output_type=TranslationLayerOutputV2` + `retries={"output": 2}` + Pydantic validation，结构性错误会自动 retry。 |
| Dual schema 期间 publisher 路由错误 | 中 | publisher 用 `if output.schema_version == 2:` 显式分支；写专门单元测试。 |
| Immersive 模式下 translation 隐藏后用户找不到译文 | 低 | header chip 显示 "翻译已隐藏 / 点击展开"；settings panel 持久化偏好。 |
| 用户对 group 边界不满意（觉得该合并 / 该分开） | 低 | 不提供 user-controlled grouping；只允许全局 on/off。V3 再考虑。 |
| RAG citation 不能引用 per-segment 中文 | 低 | RAG 索引暂用 unit-level；V2 之后补 anchor_segment_id 维度。 |

### 7.6 推荐的实施顺序（与 SP / PR 划分）

```
PR-1 (Backend, ~1 week):
  - 新增 TranslationLayerOutputV2 schema
  - TranslationLayerPublisher V2 路径（含 _validate_text_range_anchor）
  - reader_layer_translation.yaml V2 prompt（基于 Spike S1 调参）
  - test_translation_layer_output_v2_validation
  - test_translation_layer_publisher_v2
  - dual schema 兼容：worker 同时输出 V1+V2，publisher 按 schema_version 路由

PR-2 (Frontend projection, ~1 week):
  - reader-record-plate-document.ts: buildTranslationPairBlocks
  - reader-record-plate-to-plate-value.ts: READER_TRANSLATION_PAIR_TYPE
  - ReaderRecordPlateSurface.tsx: <TranslationPairBlock>
  - test_projection_group_segments_v2
  - test_plate_value_translation_pair
  - test_reader_record_plate_surface_translation_v2

PR-3 (Backend enablement, ~3 days):
  - worker 切换：production worker 仅输出 V2
  - publisher 切到 V2-only（保留 V1 deserializer for historical）
  - 监控：alignment_failure_count, fallback_segments rate

PR-4 (Docs + UX polish, ~2 days):
  - enhancement-layers-and-parsed.md: D7 Translation V2
  - reader-record-plate-surface-ui.md: 更新 V1c 段落
  - reading-base-and-units.md: 加 V2 注解
  - visual QA：immersive / intensive 截图对比
```

总工作量预估：约 3 周，1 名后端 + 1 名前端 + 半天 PM。

---

## 8. 不需要做的（明确否决）

1. **不要为 translation 重做 Reading Unit 切分**。当前 builder 的 sentence / clause / fallback 三层降级已经够用；重做会导致 anchor_segment_id 重新生成，所有历史 unit / segment / user asset 全部失效。
2. **不要让 worker 输出 placement hint**。违反 domain-first 原则。
3. **不要新增 `enhancement_layer_translation_items` 表**（方案 B vs 方案 A 的取舍）。schema 升级成本高，V2 阶段不值得。
4. **不要在 anchor_segment_id 内放中文**。anchor 是 id，不是 text。
5. **不要把译文塞进 paragraph block**。translation 必须独立 lane，避免与原文 mark 冲突。
6. **不要在 V2 first 阶段做 reading_goal / variant schema 字段**。保持 prompt 指令。
7. **不要在 projection 层 cache "deterministic grouping 结果"**。grouping 是 O(unit segments) 的纯函数，没必要 cache；cache 反而引入 stale risk。

---

## 9. 与现有架构方向的一致性

| 现有原则 | V2 是否一致 |
|---|---|
| `plate-reader-projection.md:30-42` Plate 是 Web projection，不是 truth | ✅ V2 把 grouping 放在 projection，worker 不输出 placement。 |
| `reading-base-and-units.md:14-16` Enhancement Layer 只能引用 stable | ✅ V2 items 强制 anchor_segment_id 校验。 |
| `reader-record-plate-document-ui.md:594-608` V2 应输出 per-segment items | ✅ 完全对齐。 |
| `TMP-reader-document-graph-design-2026-06-27.md:273-340` Translation V2 domain/display split | ✅ 完全对齐；本报告是这份文档的细化实施版。 |
| `enhancement-layers-and-parsed.md:120-134` Layer output 必须通过 schema + anchor validation | ✅ V2 schema 强制 anchor 校验。 |
| `reader-record-plate-surface-ui.md:1018-1024` V1c 不写 AI 文本 note/highlight | ✅ V2 不动这条边界。 |
| `plate-reader-projection.md:282-301` AI fragment allowlist | ✅ translation pair group 不是 fragment；是 derived layer projection。 |
| `reading-base-and-units.md:31-39` canonical text 不含 Markdown | ✅ translation items[].translated_text 是纯文本，不污染 canonical text。 |

---

## 10. 参考文献 / 仓库链接

- `services/api/app/services/reader_orchestration/base_builder.py` — D4/D5 baseline builder
- `services/api/app/services/reader_orchestration/translation_worker.py` — 当前 worker
- `services/api/app/services/reader_orchestration/layer_publisher.py` — publisher + `_validate_text_range_anchor`
- `services/api/app/schemas/reader_orchestration.py` — `TranslationLayerOutput`（V1）
- `services/api/prompts/agents/reader_layer_translation.yaml` — 当前 prompt
- `services/api/app/services/reader_orchestration/job_bootstrap.py` — `TRANSLATION_JOB_TYPE / TARGET_SCOPE`
- `apps/web/src/lib/reader-plate/projection/reader-record-plate-document.ts` — projection（V1 形态）
- `apps/web/src/lib/reader-plate/projection/reader-record-plate-to-plate-value.ts` — Plate value 投影
- `apps/web/src/components/reader/plate/ReaderRecordPlateSurface.tsx` — 主 surface（`surfaceMode` filter、`ReaderBlockquoteBlock` 渲染）
- `docs/initiatives/reader-agentic-orchestration/modules/reading-base-and-units.md` — Reading Base 切分口径
- `docs/initiatives/reader-agentic-orchestration/modules/plate-reader-projection.md` — projection 原则
- `docs/initiatives/reader-agentic-orchestration/modules/enhancement-layers-and-parsed.md` — layer schema + 校验
- `docs/initiatives/reader-agentic-orchestration/modules/reader-record-plate-surface-ui.md` — UI 形态 + V2 草案
- `docs/tmp/reader-orchestration/TMP-reader-document-graph-design-2026-06-27.md` — Reader Document View Model（V2 落点）
- `docs/tmp/reader-orchestration/review/reader-document-graph-design-review-{1..6}.md` — 历史评审
- Pydantic AI structured output 文档（ToolOutput / NativeOutput / PromptedOutput / retry / chunking 建议）
- Wikipedia "Bilingual text"（parallel text / sentence alignment / bitexts 概念参考）

---

## 11. 结论

**采用方案 B（中间方案）**：保持 unit worker 输入窗口，新增 V2 schema 强制 per-segment items + anchor contract，前端 deterministic grouping 生成 translation pair group。**不**采用 worker placement hints / group-level translation / 新增 layer items 表。

**关键工程要点**：
1. Worker 输入加 `anchor_segments`，prompt 严格对齐；
2. Publisher 复用 `_validate_text_range_anchor`，强制 source_text / hash / segment_id 三件套；
3. Projection deterministic grouping：1-3 sentence / group，遇到 grammar / sentence-analysis cue flush；
4. Translation lane 视觉保持现状 emerald-300 / emerald-50 / text-ink-soft；
5. V1/V2 dual schema 兼容期：worker 同时输出，publisher 按 schema_version 路由；
6. Characterization test 覆盖：fallback group / cue flush / quote boundary / alignment failure。

**最大风险**：worker 输出 items 与 anchor_segment 不一致 → 通过 `_validate_text_range_anchor` 复用 + Spike S1 prompt 验证 + Spike S3 失败模式覆盖来缓解。

**最小 spike**：必须做 S1（prompt-only alignment quality）和 S2（projection grouping correctness）；S3 推荐做；S4 可选。

**暂不做**：worker placement、group-level translation 持久化、AI 文本 note/highlight 持久化、sentence-analysis chunk V2、reading_goal schema 字段、worker streaming、materialized view model。

**Phase 1 不做 V2 schema 改动**；只清理 V1 视觉与 characterization test，确保 V2 切流时不回归。