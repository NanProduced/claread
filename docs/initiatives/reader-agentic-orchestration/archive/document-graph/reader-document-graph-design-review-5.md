# ReaderDocumentGraph 方案评审
## 总体判断：有条件接受
方案方向正确，核心分层（Stable Truth → Graph → Plate）与现有架构一致，能解决"行间卡片"问题。但关键设计点（display_groups 生成策略、非 source scope anchor 生命周期、Graph 生成成本）需要补充明确后才能进入实施。

## 最大风险 TOP 5
### 1. display_groups 所有权和确定性未定
display_groups 是 Translation V2 的核心，但设计没有回答：

- 谁生成 display_groups？ LLM 输出还是确定性规则？
- 如果 LLM 生成，同一 unit 多次 regenerate 会产生不同分组，导致页面布局不稳定。
- 如果确定性规则，需要明确定义输入（segment 长度、grammar_note 存在性、sentence_analysis 存在性、blockquote 边界等）和算法。
当前代码事实 ： reader-record-plate-document.ts 仍把 sentence_analysis 投影为文档流 callout block，与方案"cue-only"目标冲突。

建议 ：display_groups 应由后端 deterministic rule engine 生成，输入是 anchor segments + layer items，输出是 stable group_id + placement_reason。LLM 只负责 segment_items 翻译质量。

### 2. 非 source scope anchor 的 layer regenerate 生命周期
方案允许 Note 锚定到 translation / system_ai_layer / ask_supplement scope，但没有回答：

- 当 translation layer 被 regenerate 时，锚定到旧 translation 的 user note 怎么处理？
- 当 grammar_note layer 被替换时，锚定到旧 grammar_note 的 note 怎么处理？
风险 ：user note 变成 orphan，或者 silently 指向错误的 layer version。

建议 ：

- V1 Note 只允许 stable_source scope，与 Highlight 一致。
- 如果允许非 source scope，必须定义 rebase policy：layer regenerate 时，检查 anchor_segment_id + text_hash 是否仍能匹配新 layer item；不能匹配则标记 orphan，不静默迁移。
- 或者：note 的 origin_ref 记录 layer_id + item_id，layer regenerate 后旧 note 显示为"based on previous version"，用户可手动 re-anchor。
### 3. Graph 生成成本和缓存策略不清晰
方案提到"可先由 snapshot/BFF 层重建"，但没有回答：

- Graph 生成是 server-side (Python) 还是 client-side (TypeScript)?
- 长文档（100+ units, 500+ segments, 多个 enhancement layers）的生成成本是多少？
- 缓存 invalidation 策略是什么？ last_event_sequence gap 时全量重建的成本？
风险 ：如果每次 snapshot reload 都全量生成 Graph，长文档首屏会慢。

建议 ：

- 明确 Graph 生成是 server-side responsibility，作为 /api/web/reader-plate/snapshot 的一部分返回。
- 定义 cache key: (record_id, base_id, generation, last_event_sequence) 。
- 定义 viewport-aware loading: V1 返回 full graph，V2 引入 windowed API（按 unit range 或 visible viewport）。
- 如果 Graph 生成成本 > 100ms，必须引入 materialized cache table。
### 4. ProjectionAnchor offset 语义对非 source scope 不明确
ProjectionAnchor 定义：

对于 stable_source scope，offsets 是 canonical text UTF-16 offsets，清晰。

但对于 translation scope：

- offsets 是 display_group 内的 offsets？还是 segment_item 内的 offsets？
- text_hash 是对 display_group.translated_text 计算？还是对 segment_item.translated_text 计算？
对于 system_ai_layer scope (grammar_note / sentence_analysis)：

- offsets 是 layer item 内的 offsets？还是 anchor segment 内的 offsets？
风险 ：offset 语义不清会导致 Ask context resolver 无法准确提取用户选中的文本。

建议 ：

- 明确定义： text_start_offset / text_end_offset 是 origin_ref.item_id 对应的 layer item text 内的 UTF-16 offsets。
- text_hash 是对 selected_text 计算的 fnv1a32-utf16 。
- 对于 translation scope， item_id 是 display_group.group_id ，text 是 display_group.translated_text 。
### 5. Ask context resolver 规格不足
方案列出 resolver 需要返回的内容，但没有回答：

- 如何 grounding 到 LLM 可理解的 context？
- 对于 translation scope，是否包含对应的 source segments？包含多少上下文？
- 对于 grammar/sentence_analysis scope，是否包含 neighboring source context？
风险 ：Ask 回答质量依赖 context 质量。如果 context 不足或过多，LLM 回答会偏题。

建议 ：

- 定义 context template per scope：
  - stable_source : source sentence + 前后各 1 segment
  - translation : display_group.translated_text + 对应 source segments + unit context
  - system_ai_layer : layer item text + anchor segment text + 前后各 1 segment
  - ask_supplement : supplement text + origin source context
  - user_note : note text + anchor source context
- 定义 context size limit (e.g., max 2000 tokens)，超出时 truncate by relevance。
## 需要补充或改写的设计点
### 1. display_groups 生成算法
需要补充：

- 输入：anchor_segments + grammar_note items + sentence_analysis items + stable_document_blocks
- 输出：display_groups with group_id, anchor_segment_ids, translated_text, placement_reason
- 算法：
- 需要 characterization test 覆盖：短段落合并、长句独立成组、grammar_note 断点、sentence_analysis 独立成组。
### 2. Graph node ordering algorithm
需要补充：

- 输入：stable_document_blocks + anchor_segments + enhancement_layers + user_assets + ask_supplements
- 输出：ReaderDocumentNode[] with stable order field
- 算法：
- order 字段使用 stable string like "block_001:source:0" , "block_001:translation:1" , "block_001:grammar_cue:2" 。
### 3. Graph generation responsibility
需要补充：

- Graph 生成是 server-side responsibility，作为 ReaderPlateSnapshot 的一部分。
- API endpoint: GET /api/web/reader-plate/snapshot/{record_id} returns ReaderPlateSnapshot with graph: ReaderDocumentGraph field。
- ReaderPlateSnapshot.value 由 Graph 生成，不是直接由 domain facts 生成。
- Migration path: 现有 projectReaderPlateSnapshotToReaderRecordPlateDocument 改为消费 snapshot.graph ，不是 snapshot.value 。
### 4. Non-source scope anchor lifecycle policy
需要补充：

- V1: Note 只允许 stable_source scope，与 Highlight 一致。
- V2: 如果允许非 source scope，需要定义 rebase policy：
  - layer regenerate 时，检查 anchor_segment_id + text_hash 是否匹配新 layer item。
  - 匹配：自动 rebase。
  - 不匹配：标记 orphaned_at timestamp，显示为"based on previous version"，用户可手动 re-anchor。
  - 不静默迁移，不删除。
### 5. Vocabulary mark conflict resolution
需要补充：

- 引用 reader-record-plate-surface-ui.md 的 "Marks / Cues Conflict Resolver" section。
- Graph 中 vocabulary_mark node 的 display_policy 需要包含 collision_priority： context_gloss > phrase_gloss > vocab_highlight 。
- 同一 anchor_segment 多个 vocabulary marks 时，只允许一个背景层，其余降级为 underline/cue。
### 6. Backward compatibility with current ReaderPlateSnapshot
需要补充：

- Graph 是 ReaderPlateSnapshot 的新字段，不是 replacement。
- V1: snapshot.value 仍由 domain facts 直接生成， snapshot.graph 可选。
- V2: snapshot.value 由 snapshot.graph 生成，旧路径 deprecated。
- V3: 删除旧路径，只保留 Graph → Plate 路径。
## 建议保留的设计点
### 1. 不保存 raw Plate JSON，Graph 可重建/可缓存
这是正确的。Graph 是 read model，不是 truth。truth 仍在 stable blocks + canonical text + layers。

### 2. Translation V2 的 segment_items + display_groups 分离
segment_items 提供 grounding 和 Ask 精度，display_groups 提供阅读排版。分离是合理的。

### 3. ProjectionAnchor scope 扩展
允许 Ask/Note 锚定到 translation/grammar/analysis 是必要的，能支持"这段译文为什么这么翻"等问题。

### 4. 渐进式 orchestration 不被破坏
方案明确"正文先可读，translation / vocabulary / grammar / analysis 逐步出现"，与现有 architecture 一致。

### 5. Plate.js 不成为业务事实源
方案明确"Plate 负责渲染和交互，Ask Claread 也从同一份文档图解析上下文"，与现有 principle 一致。

### 6. Grammar/sentence_analysis 默认 cue-only
方案明确"不应默认把每条 analysis 都投成文档流大卡片"，与 UI 目标一致。

## 下一轮 grill 决策问题
1. display_groups 生成策略 ：LLM 输出还是 deterministic rules？如果 LLM，如何保证跨 regenerate 的布局稳定性？如果 rules，算法是否足够覆盖所有 edge case（短段落、长句、grammar_note、sentence_analysis、blockquote）？
2. 非 source scope note 的 V1 策略 ：V1 是否只允许 stable_source scope？如果允许非 source scope，rebase policy 是什么？orphan note 如何显示？
3. Graph 生成责任 ：Graph 生成是 server-side (Python) 还是 client-side (TypeScript)？如果 server-side，是否需要新增 API endpoint？如果 client-side，长文档性能如何保证？
4. Graph 缓存策略 ：是否需要 materialized cache table？cache key 是什么？invalidation 策略是什么？如果 last_event_sequence gap，是否全量重建？
5. Ask context resolver 规格 ：每个 scope 的 context template 是什么？context size limit 是多少？超出时如何 truncate？
6. Graph node ordering ： order 字段使用 stable string 还是 integer？ordering algorithm 是否足够覆盖所有 node type（source/translation/grammar/sentence_analysis/vocabulary/user_highlight/user_note/ask_supplement）？
7. Backward compatibility ：Graph 是 ReaderPlateSnapshot 的新字段还是 replacement？migration path 是什么？旧 projectReaderPlateSnapshotToReaderRecordPlateDocument 何时 deprecated？
8. 长文档 viewport-aware loading ：V1 是否返回 full graph？V2 是否引入 windowed API？window size 是什么（unit range / visible viewport / fixed segment count）？
9. Testing strategy ：如何验证 Graph → Plate projection 的正确性？是否需要 characterization tests 覆盖 source text preservation、anchor validity、visual layout、Ask context accuracy？
10. Phase 1 scope ：Phase 1 是否包含 Translation V2？如果不包含，Translation V1 unit-level 如何与 Graph 共存？是否需要 fallback adapter？