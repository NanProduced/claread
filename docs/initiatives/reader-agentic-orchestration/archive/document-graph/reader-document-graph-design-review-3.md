## 总体判断：有条件接受
方向正确，核心决策（Stable Source Truth + Graph + Plate Renderer，不持久化 raw Plate JSON）与正式文档一致。但当前草案在 Graph 与现有 Projection 层的关系、节点到 Plate 的映射、非 source scope 的 rebase 策略、以及 display_group 生成机制上存在关键缺口，必须先补上再进入实现。

## 最大风险 TOP 5
1. ReaderDocumentGraph 与现有 ReaderRecordPlateDocument 的职责重叠（高风险）

正式文档 reader-record-plate-surface-ui.md 已定义了 ReaderRecordPlateDocument 作为前端 projection schema，包含完整的 unit → source_block → anchor_segment 树结构、marks/cues 投影、progress 投影。草案提出的 ReaderDocumentGraph 本质上在做同一件事，但用了不同的术语和节点模型。两个"产品语义层"并存会导致：

- 开发者困惑哪个是真正的中间层
- 两套节点类型需要维护映射
- Ask context resolver 不知道该读哪个
推荐 ：将 ReaderDocumentGraph 定位为 ReaderRecordPlateDocument 的 后端/BFF 侧泛化版本 ，明确两者的继承/映射关系，而不是作为独立概念引入。或者直接扩展 ReaderRecordPlateDocument 承担 Graph 的职责。

2. Translation V2 display_group 生成机制未定义（高风险）

草案描述了 display_group 规则（1-3 个连续 Anchor Segments、不跨 Stable Document Block 等），但没有说明 谁 生成 display_groups。有两种可能：

- LLM 生成 ：可靠性风险，placement_reason 可能不稳定
- 确定性后处理 ：规则需要形式化，但草案的规则（"很短句合并"、"含重要转折的句子可单独成组"）是启发式的，难以形式化
草案说 "worker 仍可按 Reading Unit 批量调用 LLM"，暗示 LLM 输出 display_groups，但这与正式文档 enhancement-layers-and-parsed.md 的原则 "LLM 不输出 offsets、hash、raw Plate JSON 或 raw Slate ops" 不完全一致——display_groups 包含 anchor_segment_ids，本质上是一种结构化输出。

推荐 ：明确 display_groups 由后端确定性 postprocessing 生成（基于 segment_items + Anchor Segment 元数据），不依赖 LLM。placement_reason 改为 deterministic enum，不依赖 LLM 判断。

3. 非 source scope 的 ProjectionAnchor 缺少 rebase 策略（高风险）

草案允许 Note 支持所有 scope（translation、system_ai_layer、ask_supplement、user_note），但 enhancement-layers-and-parsed.md 明确 "Layer 可再生、可局部重试"。当 translation layer 重新生成后：

- 旧译文文本消失，用户基于旧译文的 note 锚点断裂
- grammar_note / sentence_analysis 重新生成后，文本内容可能变化
- origin_ref 中的 layer_id 可以追溯，但 text_hash 校验会失败
草案在风险表中提到 "rebase 问题" 但对非 source scope 的 highlight 说"避免用户高亮 AI 文本带来过多 rebase 问题"，对 note 却没有同样的谨慎。

推荐 ：V1 非 source scope 的 Note 写入必须附带降级策略：如果 layer 再生后 text_hash 不匹配，note 应显示为 "anchored to previous version" 并保留历史 layer 引用，或降级为仅关联 source anchor。不可静默丢失。

4. Graph 节点类型混淆了内容类型与显示状态（中风险）

草案的 node_type 枚举包含 grammar_cue 和 grammar_note 、 sentence_analysis_cue 和 sentence_analysis 。正式文档 reader-record-plate-surface-ui.md 明确 Sentence Analysis V1 是 "cue-only"，不进入文档流。草案把 cue 和展开后的 note 作为两种节点类型，但这是 显示状态 ，不是 语义类型 。

同样的问题存在于 display_policy: Record<string, unknown> ——这个字段是决定"文档感 vs 卡片感"的关键，但完全 opaque。

推荐 ： node_type 只表达语义类型（ source_paragraph 、 translation 、 grammar_note 、 sentence_analysis 、 vocabulary_mark 、 ask_supplement 、 user_note 、 user_highlight ）。显示状态（cue vs expanded）由 display_policy 控制，且 display_policy 必须有明确的 typed schema，不能是 Record<string, unknown> 。

5. 长文和渐进式 orchestration 缺少增量 Graph 更新策略（中风险）

草案提到 "按 block/window lazy projection" 和 "viewport-aware load"，但正式文档 plate-reader-projection.md 的恢复流程是 "reload snapshot on gap"。Graph 的增量更新策略未被定义：

- 新 layer 发布后，是 full graph rebuild 还是增量 patch？
- 如果 full rebuild，长文的性能如何保证？
- 如果增量 patch，Graph 的序列化格式是否支持 delta？
推荐 ：在 Phase 1 明确 Graph 采用 full rebuild + snapshot cache 策略。增量更新留到 Phase 3+，等 projection_ops incremental applier 验证后再考虑。同时为 Graph 定义 last_event_sequence 版本号，用于 cache 失效判断。

## 需要补充或改写的设计点
1. Graph 与 ReaderRecordPlateDocument 的关系 ：必须在文档中画一张映射表，说明 Graph 的每种 node_type 对应 ReaderRecordPlateDocument 的哪个节点/leaf/mark。不写清楚这张表，Graph 就是空中楼阁。
2. display_policy 的 typed schema ：代替 Record<string, unknown> ，至少定义：
3. display_group 生成算法 ：补充确定性后处理流程，明确输入（segment_items + Anchor Segment 元数据 + Stable Document Block 边界）、输出（display_groups）和边界条件。规则中的启发式部分（"很短句"、"重要转折"）需要量化阈值。
4. 非 source scope 锚点的 rebase 策略 ：补充一个 section 描述 layer regenerate 后，translation/system_ai_layer/ask_supplement scope 的 note 如何处理。至少区分三种情况：text_hash 匹配（保留）、text_hash 不匹配但 layer_id 存在（降级为 source-only）、layer 被删除（标记 orphaned）。
5. Graph 节点的排序机制 ：草案的 order: string 和待评审问题 6 都在问这个问题。必须明确：Graph 的节点顺序是 source block order + layer placement policy 的确定性派生，还是需要独立存储。推荐前者，因为 Graph 是可重建的。
6. Ask context resolver 的 scope 优先级 ：草案说 V1 覆盖三类 scope，但没有说明当用户选区跨越多个 scope 时（如同时选中 source 和 translation），resolver 如何处理。需要明确优先级和合并策略。
7. 与现有 ReaderPlateSnapshot 的集成点 ：草案说 Graph 由 snapshot/BFF 层生成，但没有说明 Graph 是 snapshot 的一部分（如 ReaderPlateSnapshot.graph ），还是独立 API。这直接影响 Phase 1 的实现路径。
8. RAG 索引策略 ：草案提到 Graph 可用于 RAG，但没有说明 RAG 索引的是 Graph nodes、canonical text、还是两者。如果 Graph 可重建，RAG 索引应该基于 canonical text + layer facts，而不是 Graph nodes。
## 建议保留的设计点
1. 不持久化 raw Plate JSON ：与正式文档完全一致，是正确的架构决策。
2. Translation V2 的 segment_items + display_groups 分层 ：segment_items 用于 grounding/对齐，display_groups 用于排版，这个分离是正确的。解决了 unit 太粗、sentence 太碎的核心矛盾。
3. ProjectionAnchor 的 origin_ref 设计 ：保留 source grounding 的同时扩展到非 source scope，方向正确。
4. 存储策略三层递进 ：短期 snapshot/BFF 重建 → 中期 materialized cache → 长期后端 read model，节奏合理。
5. Grammar/Sentence Analysis 的 differentiated projection ：grammar_note 用 inline cue + expandable note，sentence_analysis 用 Structure Lens cue，不回退到大卡片，与正式文档一致。
6. 渐进迁移路线 Phase 0-5 ：分阶段推进，不试图一步到位，务实。
7. Ask Claread 必须从 Graph 解析上下文，不绕过 Graph ：这个原则正确，确保了所见即所问。
## 下一轮 grill 决策问题
1. 如果 ReaderDocumentGraph 实质上是 ReaderRecordPlateDocument 的泛化版本，是否应该直接扩展 ReaderRecordPlateDocument 而不是引入新概念？如果保留两个概念，两者的边界在哪里？
2. TranslationDisplayGroup 的生成是确定性后处理还是 LLM 输出？如果是确定性，规则中的启发式（"很短句"、"重要转折"）如何量化？如果是 LLM，如何保证 placement_reason 的稳定性？
3. 非 source scope 的 Note 写入，在 layer regenerate 后，是接受 "orphaned note"（保留但标记为旧版本），还是强制降级为 source-only anchor？用户体验上哪种更合理？
4. Graph 的 node_type 是否应该合并 cue/expanded 的区分，改为由 display_policy 统一控制？ grammar_cue 和 grammar_note 本质上是同一个语义实体的两种显示状态，为什么要分成两个 node_type？
5. V1 Graph 是否采用 full rebuild 策略，放弃增量更新？如果 full rebuild，长文（10万+ 字符）的重建延迟是否可接受？是否需要先做 benchmark？
6. Graph 作为 BFF/snapshot projection 时，Ask context resolver 是前端从 Graph 解析，还是后端提供独立的 context API？如果前端解析，Graph 的序列化大小是否可接受？