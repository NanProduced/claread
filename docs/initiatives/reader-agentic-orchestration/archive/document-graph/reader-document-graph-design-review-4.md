# ReaderDocumentGraph 方案评审
## 总体判断：有条件接受
方案方向正确：它准确命中了当前页面“卡片化”的体验问题，并试图在 Stable Source Truth 与 Plate Renderer 之间补一层产品语义层。这一层与现有 ReaderPlateSnapshot 的“wrapper + value”思路相容，且坚持 不存 raw Plate JSON、truth 仍在稳定层 的原则，与正式文档一致。

但当前草案在概念边界、anchor 作用域、rebase 策略、长文策略和与现有 DTO 的关系上都不够紧。建议 先作为 snapshot/BFF projection 落地 V1，明确与 ReaderPlateSnapshot 的包含关系，再决定是否提升为后端 read model 。不要同时引入新术语层和新的持久表。

## 最大风险 TOP 5
### 1. ReaderDocumentGraph 与 ReaderPlateSnapshot 边界重叠，易导致双轨投影
正式文档已经定义了 ReaderPlateSnapshot 作为“snapshot wrapper + Plate value”。TMP 草案的 Graph 节点模型（ node_id 、 node_type 、 origin_ref 、 anchor_refs 、 display_policy ）与 snapshot wrapper 中的 enhancement_layers 、 ask_supplements 、 user_assets 、 value 高度重叠。如果不明确 Graph 是 snapshot 内部的一个字段还是替代 snapshot，会出现“BFF 拼一次 Graph、前端再拼一次 snapshot”的重复劳动。

关键问题 ： ReaderDocumentGraph 是 ReaderPlateSnapshot.value 的上游，还是 ReaderPlateSnapshot 的同义重写？

### 2. ProjectionAnchor.scope 混合了“所有权”和“内容类型”，写入策略不一致
草案中 scope 包括 stable_source | translation | system_ai_layer | ask_supplement | user_note 。但 user_note 既是用户资产又是一种 scope，与 owner 字段（ stable_source | system_ai | ask_supplement | user ）正交混乱。更麻烦的是：V1 允许 note 写入所有 scope，却 只允许 highlight 写 stable source 。这会造成“同一选区可以记笔记但不能高亮”的反直觉 UX，且没有给出稳定的技术理由。

### 3. 非 source scope 的 rebase/retry 策略缺失
当 translation 或 system_ai_layer 因 layer regenerate/supersede 而失效时，挂在它们上面的 user note / Ask context 如何 rebase？草案只提到 highlight 限制在 stable source 以避免 rebase 问题，但 note 却允许跨 scope——这个风险没有闭环。正式文档中 Enhancement Layer 是可再生、可局部重试的，User Editorial Assets 不应被改写，但 跨 scope 的 note 会引用可再生内容，需要 rebase 或失效策略 。

### 4. Translation V2 的 display_groups 与 Stable Document Block 边界、fallback、hash 校验未闭环
display_groups.placement_reason 和分组规则（1-3 个连续 segments、不跨 block、引用边界等）方向对，但缺少：

- display_groups 变化时是否需要版本化 schema/hash？
- 当 LLM 输出的 segment_items 与 anchor_segment 的 selected_text hash 不一致时，是 fail-closed 还是允许展示未对齐译文？
- 当前 D4 translation layer 是 unit-level，V2 如何向后兼容？正式文档要求 schema_version 字段。
### 5. 长文与渐进式 orchestration 的 windowing/lazy 策略只有口号
草案只提到“按 block/window lazy projection，snapshot cache，viewport-aware load”，但没有具体策略。当前正式文档要求 article_ready 时 Base Plate Snapshot 必须可生成，且 D4 用 full snapshot reload。如果 Graph 要支持长文，必须定义：

- window API 的边界（按 unit？按 block？按 viewport？）
- Ask context resolver 在 partial window 下如何补全前后文
- cache invalidation 的精确条件（ last_event_sequence 足够吗？layer published 是否触发整图重建？）
## 需要补充或改写的设计点
### 必须明确 Graph 在现有架构中的位置
建议改写为：

ReaderDocumentGraph 先作为 snapshot 的 graph 字段存在，不单独建表。Ask Context Resolver 消费 graph ，Plate Surface 消费 value ，两者从同一份 snapshot 派生。

### 收紧 ProjectionAnchor 的作用域模型
建议把 scope 拆成两个字段：

system_ai_layer 太粗，无法区分 vocabulary/grammar/analysis 的不同上下文策略。同时，V1 写入策略建议统一为： notes 和 highlights 都只写 stable_source ；非 source scope 的 note 在 layer regenerate 时无法稳定 rebase，应降级为“Ask 会话内临时引用”或“保存时强制回落到对应 source anchor”。

### 补充 rebase / invalidation 规则
明确写入非 source scope 时的两条路径：

1. 持久化资产（user note/highlight） ：必须锚定到 stable source；保存时把 translation / system_ai scope 解析为对应的 source anchor segment 范围。
2. 临时 Ask 上下文 ：可以引用 translation / grammar_note / sentence_analysis node，但 Ask resolver 必须返回对应 source segments + layer item，不持久化为 user asset。
当 layer regenerate 时：

- source scope 资产：不变
- 临时 Ask 上下文：失效，下次请求需重新解析
- 非 source scope 持久化资产：不允许在 V1 出现
### Translation V2 schema 需要 source hash 和 fallback 规则
建议 segment_items 增加：

后端 projection 必须校验 source_text_hash 与 anchor_segments.text_hash 一致，不一致时 fail-closed 或标记为 alignment_failed ，不能展示错位译文。 display_groups 也应携带 group_source_hash 用于快速校验完整性。

### 长文策略需要具体 window API 草案
至少补充：

- /api/web/reader-records/{id}/graph?window=unit:u1..u10 或 ?block=b1..b5
- Ask context resolver 的 context_window_policy ：向前/向后各取 N 个 segments 或到 block 边界
- cache key 包含 (record_id, base_id, generation, last_event_sequence, window)
- viewport-aware load 只在客户端做，不增加 API 复杂度
### 与当前代码矛盾的 sentence_analysis 投影必须做决策
正式 UI 文档明确 sentence_analysis V1 应是 Structure Lens cue-only （ reader-record-plate-surface-ui.md ），但当前代码仍投影为 document-flow callout（ reader-plate-component-integration.md ）。ReaderDocumentGraph 方案必须对此表态，不能两边都保留。

建议 ：采纳 cue-only 作为 V1 默认；callout 仅在用户主动展开或复杂句自动展开 compact structure block。Graph node 中 sentence_analysis 节点默认 display_policy.expanded = false 。

### display_policy: Record<string, unknown> 必须类型化
当前是类型安全的反模式。建议至少：

## 建议保留的设计点
1. “不保存 raw Plate JSON，truth 仍在 stable blocks + canonical text + layers” ：与正式文档完全一致，必须保留。
2. Translation V2 的 segment_items + display_groups 分层 ：这是解决 unit 太粗、sentence 太碎的正确方向，保留。
3. ReaderDocumentGraph 可重建、cache 可丢弃 ：缓存策略与 last_event_sequence 绑定符合事件驱动架构。
4. Plate 只渲染、不保存业务事实 ：与正式文档的 Domain-first 原则一致。
5. 渐进迁移路线分 5 个 phase ：合理，但 Phase 0 应该增加“明确 Graph 与 ReaderPlateSnapshot 的关系”。
6. Grammar / Sentence Analysis 默认低干扰（cue/inline mark） ：方向正确，符合“文档优先”目标。
## 下一轮 grill 决策问题
1. ReaderDocumentGraph 是内嵌进 ReaderPlateSnapshot.graph 字段，还是替代现有 snapshot wrapper？ 这决定是否需要重写 BFF DTO。
2. V1 是否允许 user note/highlight 写入非 stable_source scope？ 如果允许，rebase 策略是什么？如果不允许，Ask 如何引用 translation/grammar 文本？
3. Translation V2 的 display_groups 是否必须后端持久化？ 还是允许前端从 segment_items 按固定规则动态分组？
4. sentence_analysis V1 默认形态：cue-only / compact callout / 两者按规则切换？ 需要立即淘汰当前代码中的 document-flow callout。
5. 长文 windowing 的边界单位是什么？unit、block 还是 viewport？ Ask context resolver 的补全策略如何配套？
6. ProjectionAnchor 中的 text_start_offset / text_end_offset 是基于哪一层文本？ 是 canonical text、unit text、还是 node-local text？需要统一，否则 Ask resolver 无法切片。
7. 当 segment_items.source_text_hash 与 anchor segment hash 不一致时，是 fail-closed、降级显示 source text，还是允许展示并标记 alignment_failed ？
建议先做一轮针对上述 7 个问题的决策，再把结论压缩回正式文档，然后删除本 TMP。