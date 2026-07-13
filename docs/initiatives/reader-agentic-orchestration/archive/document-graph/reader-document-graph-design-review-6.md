# Claread `ReaderDocumentGraph` 方案评审

> 评审日期：2026-06-27
> 评审对象：`docs/tmp/reader-orchestration/TMP-reader-document-graph-design-2026-06-27.md`
> 参照基线：`reading-base-and-units.md` / `plate-reader-projection.md` / `enhancement-layers-and-parsed.md` / `reader-record-plate-surface-ui.md` / `reader-plate-component-integration.md`
> 评审者立场：产品语义一致性 + 后端可控性 + 工程最小化

---

## 总体判断：**有条件接受**

整体方向正确（后端 truth 不变、Plate 仅是渲染、projection 必须落到稳定 anchor），但方案本身是 **"症状层重述 + 三个新术语"**，还不是落地级设计。它直接解决 ①（行间卡片感）和 ⑤（不存 raw Plate）两点；对 ③ / ④ / ⑥ 的处理停留在"我们有方案但还没具体"。**需要在合并到正式模块前补齐 4 处实质性收紧**（见下），否则这套 Graph 会变成第二个 `render_scene_json` 的替身。

最大问题：方案把"渲染体验不够好"打包成"新增中间层"，但其中 **2/3 的体验改进根本不需要新中间层**——收敛旧 `SelectionActionStrip`、改 `CalloutMarkdownRenderer` 走 `PlateStatic`、把 sentence_analysis 切到 cue-only 这三件事，在不引入 `ReaderDocumentGraph` 的前提下就能完成 80% 的可见效果。

---

## 最大风险 TOP 5

### 1. Graph 引入早于 V1a 验收，已有路径足够解症状 ⚠️ P0

方案的核心动机是"页面像行间卡片"。但 `reader-record-plate-surface-ui.md`（V1a 验收段）已经把收敛方案拆得很清楚：

- 旧 `SelectionActionStrip` 删除 → 收敛到 `FloatingToolbarKit`
- callout 改走 `PlateStatic`/`@platejs/basic-*` 替代 `CalloutMarkdownRenderer` 自定义递归
- sentence_analysis 从 callout 改 cue-only（`reader_record_sentence_analysis_cue`）
- unit translation "本段译文"过渡展示

**这四项都在 V1a 范围内，都不需要 `ReaderDocumentGraph`。** 在 V1a 还没完成时就引入 Graph，会和现有 `projectReaderPlateSnapshotToReaderRecordPlateDocument` projection 形成两条并行投影链（snapshot → ReaderRecordPlateDocument 一条；snapshot → Graph → Plate value 另一条）。

**推荐**：把 Graph 推到 **V2+**，先让 V1a 用现有 `ReaderRecordPlateDocument` projection 跑通，再评估是否需要 Graph。

### 2. Translation V2 schema 与现有 translation worker / Layer Publisher 契约未对齐 ⚠️ P0

方案提出 `TranslationLayerOutputV2 { segment_items, display_groups }`，但：

- 现有 `enhancement-layers-and-parsed.md` 定义的 `TranslationLayerOutput` 是 `{ schema_version: 1, target_language, translated_text, notes, confidence }`。
- 现有 Layer Publisher 的发布门禁（content schema valid / anchor schema valid / CAS winner / grounded in target unit）都是按"整段 translated_text + unit-level anchor"设计的。
- `plate-reader-projection.md` 的 `upsert_translation_node` projection op 也是 `unit_id` 或 `anchor_segment_id` + `layer_id` 维度，**没有 display_group 概念**。
- `display_groups.placement_reason` 是纯展示决策，**与 grounding 无关**，但被放在 schema 顶层会污染 domain schema。

**推荐**：V2 schema 拆成两部分：

- `TranslationLayerOutputV2.items`（domain truth：每 segment 的译文 + source text + hash）进入 `enhancement_layers.output`，由 Layer Publisher 校验。
- `display_groups` **不进 layer schema**，改为前端 BFF 在 snapshot 中派生的 `ReaderPlateSnapshot.translation_display_groups`，或作为独立前端 projection 输入。这样 translation worker 仍按 unit 批量调用（成本不变），但 ① 不污染 domain schema ② group 策略可单独演进 ③ projection op 仍按 segment + layer_id 写入。

### 3. `ProjectionAnchor.scope` 风险：Note 高亮 AI 文本的 rebase 黑洞 ⚠️ P0

方案说"Highlight 仅 stable_source，Note 支持所有 scope"。但 `reader-record-plate-surface-ui.md` 已明确：

> `UserEditorialAssetAnchor` 的 `anchor_segment_id` + `unit-local UTF-16 offsets` 是 **强约束**，span 必须落在对应 Anchor Segment 的 unit range 内。

如果 Note 落在 `translation` / `system_ai_layer` scope：

- 翻译文本不在 Canonical Text Layer 上，**无 UTF-16 offset、无 `fnv1a32-utf16` hash 校验基础**——`hash_algorithm` 字段在非 source scope 下无意义。
- translation layer 是可重生成的（layer retry），Note 高亮在重生成后 **range 失效**，没有 stable 锚可 rebase。
- sentence_analysis cue 也类似：`anchor` 是 `Anchor Segment` 或 `Unit`，cue 本身的文本不是 source。
- ask_supplement：用户笔记挂在 AI 文本上后，AI 修订会让笔记漂移。

**推荐**：V1 把 Note 也只允许 `stable_source` scope（与 Highlight 一致）。`translation` / `system_ai_layer` / `ask_supplement` scope 的 "Note on AI text" 推迟到 **Ask Supplement 完整 projection 设计**完成之后（已在正式文档列为后续需求）。`ProjectionAnchor.scope` 枚举先保留，但 `WritePayload` 校验只放行 `stable_source`。

### 4. Graph 排序与渐进式 orchestration 冲突 ⚠️ P1

方案说 Graph 由 Stable Document Blocks + Anchor Segments + Enhancement Layers + User Editorial Assets + Ask Supplements 汇成。但：

- enhancement layer 是 **异步渐进发布**的（translation / vocab / grammar / sentence_analysis 都按 unit 调度）。
- Graph 必须支持"图层陆续出现"的中间态——即 Graph 不是一次性生成物。
- 方案 `ReaderDocumentNode.order: string` 没有明确定义排序规则，只在 Open Question #6 提到。
- 如果 Graph 排序依赖 layer placement policy 动态计算，**每次 layer 发布都要 O(n) 重排**；如果依赖 source block order + 静态 order string，**新 layer 必须插队时无法自然放置**。

**推荐**：明确 Graph 是 **不可变快照**（每个 snapshot / last_event_sequence 对应一个 Graph 视图），每次 layer 发布走 snapshot reload 而不是 Graph 增量更新。这与 `plate-reader-projection.md` 的 snapshot 恢复契约一致（"reload snapshot on gap, unresolved target, hash mismatch"），且 Graph 节点 `graph_version` 字段已隐含这个语义——但方案正文没说清楚，Open Question #6 也没收敛。

### 5. `ReaderDocumentNode` 形态重复造 `ReaderPlateSnapshot` ⚠️ P1

对比 `ReaderDocumentNode` 与现有 `ReaderPlateSnapshot.navigation.units` + `enhancement_layers` + `ask_supplements` + `user_assets`：

| `ReaderDocumentNode` 字段 | 现有 snapshot 等价 |
|---|---|
| `node_id` | `layer_id` / `supplement_id` / `asset_id` / `unit_id` / `block_id` |
| `node_type: source_paragraph` 等 | `reader_unit.unit_type` + `reader_source_block` |
| `owner: stable_source / system_ai / ask_supplement / user` | snapshot layer `owner` 已有 `stable / system_ai / ask_supplement / user / ephemeral` |
| `origin_ref` | snapshot 中 `base_id / unit_id / anchor_segment_id / layer_id` 已分散在各数组里 |
| `anchor_refs: ProjectionAnchor[]` | 无直接对应，但这是 Graph 的新抽象 |
| `display_policy` | 无 |

`anchor_refs` 之外的所有字段都能从现有 snapshot 派生。方案把现有 snapshot wrapper 的字段重组了一遍，**没有新增任何 anchoring / context 解析能力**——这部分能力被推到 `ProjectionAnchor` 和 Ask resolver 上，但两者定义都还停留在草案阶段。

**推荐**：把 `ReaderDocumentGraph` 当作 **snapshot 的视图层（View Model）**，不要新建 `ReaderDocumentNode` 类型；而是在 `ReaderPlateSnapshot` 之上定义 `readerDocumentGraphView(snapshot): ReaderDocumentGraphView` 纯函数，把 Graph 节点当作前端 view-only 数据。这样：

- 不增加后端表 / 不增加 snapshot wire format 字段。
- Graph 重建逻辑就是 snapshot 解析，自然支持"cache 可丢弃、sequence gap 重建"。
- 与 `plate-reader-projection.md` 已声明的 "snapshot wrapper DTO，不把 Claread metadata 强塞进 Plate root" 一致。

---

## 需要补充或改写的设计点

### A. Translation V2 schema 拆分（domain vs display）

当前 `TranslationLayerOutputV2` 把 `display_groups` 放进了 layer schema。建议：

```ts
// domain truth（持久化在 enhancement_layers.output）
type TranslationLayerOutputV2 = {
  schema_version: 2;
  target_language: string;
  segment_items: Array<{
    anchor_segment_id: string;
    source_text: string;
    source_text_hash: string;
    translated_text: string;
  }>;
  full_translation?: string;        // 兼容 fallback
  confidence: "low" | "normal" | "high";
  notes: string[];
};

// 前端 projection 派生（不进入 layer schema）
type TranslationDisplayGroups = Array<{
  group_id: string;
  anchor_segment_ids: string[];
  translated_text: string;
  placement_reason: ...;
}>;
```

后者由 BFF 在 snapshot reload 阶段派生；V1 可用纯前端规则，V2+ 可引入 worker 生成的 placement_hint 但仍不进 layer schema。

### B. ProjectionAnchor.write_scope 收紧

新增 `ProjectionAnchor.writable_scopes` 字段（或者直接在 resolver 层 hardcode），明确：

- `stable_source`：可写 highlight + note
- `translation`：**V1 只读**（Ask 读，但不写）
- `system_ai_layer`：**V1 只读**
- `ask_supplement`：只读
- `user_note`：只读

这把 Open Question #3 答案收敛成"先全只读，等 Ask Supplement projection 设计落地后再说"。

### C. Graph 版本与 cache 失效契约

明确写出：

- `ReaderDocumentGraph` 是 `ReaderPlateSnapshot` 的视图函数，**不缓存任何 backend 状态**。
- 如有性能需求，cache key = `(record_id, base_id, generation, last_event_sequence, graph_version)`。
- `graph_version` 由 Graph 函数版本号决定（不是 layer 发布次数）；schema 改动 +1。
- snapshot reload 时 Graph 自动重建（开销可接受是另一回事，但路径不依赖 cache）。

### D. Ask Context Resolver 边界

方案说"Ask Claread 不应绕过 Graph 单独拼上下文"，但 Ask resolver 的输入只有 `ProjectionAnchor` 和 graph_version。这意味着：

- 必须先点中 Graph 中的某个 node 才能 Ask —— 与"selection 是最高优先级 active source"是否冲突？
- 如果用户选了一段普通原文但想 Ask 整段 unit 的翻译，resolver 是否允许 `ProjectionAnchor` → `unit_id` 升级？
- `ask_supplement` scope 的 Ask（即"基于我之前的 AI 回答继续问"）如何用同一 resolver 表达？

**推荐**：把 `AskContextResolver` 输入从 `ProjectionAnchor` 单参数扩展为 `{ anchor: ProjectionAnchor, explicit_scope_upgrade?: "unit" | "record", related_node_ids?: string[] }`。`anchor` 是用户点选入口，`explicit_scope_upgrade` 是 rail / pin 行为提供的上下文。

### E. Node 排序的明确定义

Open Question #6 必须收敛成：

- `ReaderDocumentNode.order` 是 **base offsets 范围内的浮点字符串**（"0.5", "1.0", "1.5"），用于在同 source block 内插入 AI 节点。
- 同 source block 内的 stable nodes 顺序由 `base_start_utf16` 决定。
- 跨 block 不允许插入 AI 节点（Open Question #6 → "1 不跨 Stable Document Block" 的更严格版本）。

这避免了"动态计算 order" 带来的 layer 发布期 O(n) 重排，也让 Ask citation 能稳定指向"第 N 段第 M 句"。

---

## 建议保留的设计点

1. **`ReaderDocumentGraph` 作为术语（哪怕实现是 view function）**——它给团队一个共同心智模型，明确"Plate 之下、Snapshot 之上还有一个产品语义层"。只要不把它实体化成后端表，术语保留无害。

2. **`ProjectionAnchor` 多 scope 枚举**——未来 Ask 引用 translation / grammar 是合理的，只是 V1 写入路径先收紧。

3. **`TranslationDisplayGroup` 作为前端概念**——Display group 解决了"unit 太粗 / sentence 太碎"的真实痛点（Open Question #6），关键是不要把它污染到 layer schema。

4. **Phase 0–5 渐进迁移路线**——大体合理，但需要把 Phase 1（Graph Projection V1）和 Phase 2（Translation V2）的顺序调换：先 Phase 2 schema 拆分（domain vs display），再做 Phase 1 Graph。

5. **"cache 可丢弃、不作为 truth" 风险表**——这条原则与现有 snapshot + event-driven projection 完全一致，应该保留并强化到正式模块。

6. **`ReaderDocumentNode.children` 用于 sub-callout / 嵌套**——比把所有内容塞进单个 node 更易处理 grammar cue 编号、sentence_analysis chunk 等子结构。

7. **"V1 Note 不放行非 source scope" 风险缓解**——只要 V1 不开 AI text note，rebase 黑洞不会出现，Ask Supplement projection 设计可以独立演进。

---

## 下一轮 Grill 决策问题

> 这些是必须在合并到正式模块前做出"是 / 否"二选一的问题，多选一就合并不下去。

1. **Graph 是不是 V2 引入？** V1a 完成后看现有 `ReaderRecordPlateDocument` projection 是否足以解决"卡片感"，是 → Graph 推迟到 V2；否 → 现在合并但同步修订 V1a 验收。

2. **`TranslationLayerOutputV2` schema 拆分？** 是 → `display_groups` 不进 layer schema；否 → 接受 schema 污染并补 Layer Publisher 发布门禁对 display_group 的校验。

3. **Note 在 V1 是否允许非 source scope？** 否 → `ProjectionAnchor` writable_scopes V1 仅 `stable_source`；是 → 给出 translation / system_ai_layer scope 的 rebase 策略（必须包括 layer retry 后的 anchor recovery）。

4. **Graph 排序规则？** 选 (a) 浮点 order string (b) base_start_utf16 + 静态 placement policy (c) 仅依赖 source block order，AI 节点只能塞进 source block 内空隙。

5. **Graph 是否进入后端 read model？** 否 → 保持前端 view function + 可丢弃 cache；是 → 必须先定义后端 Graph 表 schema（节点 + 边 + scope），并给出 cache vs truth 边界的失效契约。

6. **`AskContextResolver` 输入形态？** 单 `ProjectionAnchor` / `{ anchor + scope_upgrade + related_node_ids }` / 完全重写为 `{ node_id + intent }` 三选一。

7. **`sentence_analysis` 默认形态？** cue-only / 短句自动 compact structure block / 用户设置切换。V1d 是 best-effort chunk underline 还是 V2 offset schema 必选？

---

## 一句话总结

**接受方向，不接受当前形态**——Graph 作为术语和心智模型值得保留，但作为新中间层过早；Translation V2 的 display_groups 必须从 layer schema 剥离到前端 projection；ProjectionAnchor V1 写入路径收紧到 stable_source only；Graph 节点排序必须给出可实现的算法。三处收紧后，方案可以从 TMP 沉淀到正式模块。