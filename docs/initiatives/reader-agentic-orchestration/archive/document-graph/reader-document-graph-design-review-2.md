# ReaderDocumentGraph 方案评审
## 总体判断：有条件接受
核心方向正确 ：把 Plate.js 降为 renderer、补一层产品语义模型、不让 raw Plate JSON 进后端 —— 这与正式文档已有的 "Plate 是 projection" 一致。

但有重要前置条件 ：当前 TMP 提出的 ReaderDocumentGraph / ProjectionAnchor / TranslationDisplayGroup 三件事，与正式文档已落地的 ReaderPlateSnapshot + UserEditorialAssetAnchor.scope + projection_ops 合同存在 重复定义、口径漂移和 schema 冲突 。在按下"接受 ReaderDocumentGraph 作为新术语"之前，必须先做收敛，否则会造成双事实源和术语膨胀。不能按 TMP 现状直接落地。

## 最大风险 TOP 5
### 风险 1：ReaderDocumentGraph 与 ReaderPlateSnapshot 重复定义（双事实源）
正式文档 plate-reader-projection.md 已定义 ReaderPlateSnapshot 作为 BFF/snapshot 层，其 value 字段已是 Plate-consumable document， enhancement_layers / ask_supplements / user_assets / parsed_decisions 都已是 wrapper DTO 字段。TMP 的 ReaderDocumentGraph 在职责上与之 90% 重叠，只是换了 node-based 命名。

风险 ：两套"产品语义层"并存，每次 truth 变化要同步两个模型；snapshot serializer 和 Graph builder 容易漂移；测试矩阵翻倍。

### 风险 2：ProjectionAnchor 与已有 UserEditorialAssetAnchor.scope 合同冲突
reader-record-plate-surface-ui.md#L887-L901 已定义 UserEditorialAssetAnchor 携带 scope?: "stable_source" | "translation" | "system_ai_layer" | "ask_supplement" ，且 V1c 写入约束明确是 stable-source single-range only 。

TMP 的 ProjectionAnchor 新增 user_note scope、 graph_version 、 node_id 字段，并提议 "V1 Note 支持所有 scope"。这会：

- 引入 node_id 作为新寻址维度（接近 Plate path 的脆弱性）
- graph_version 与已有 last_event_sequence 形成双版本号
- 让 Note 写入绕过 V1c single-range stable-source 限制
### 风险 3：Translation V2 的 display_groups 把渲染策略泄漏进 layer truth
enhancement-layers-and-parsed.md 已明确："Layer Worker 不直接输出 raw Plate path operation"、"Projection 不是 layer truth"。

TMP 的 display_groups （含 placement_reason: paragraph_flow | grammar_break | sentence_analysis | long_sentence | quote_boundary ）是 渲染排版策略 ，让 worker 输出它等于：

- Worker 必须知道渲染策略（哪些句有 grammar_note、哪些是长句）
- 同一 segment 在不同 mode（沉浸/精读）下应有不同分组，但 layer output 是单份
- 违反 "Plate projection op 必须指向稳定 domain target" 的原则
### 风险 4：Translation V2 schema 与正式文档已有 V2 草案不一致
reader-record-plate-surface-ui.md#L595-L608 已定义 Translation V2 schema：

TMP 的 schema 用 segment_items + display_groups 两个数组，字段名不同、缺 full_translation / confidence / notes 、且 translated_text 在两处重复。两份 V2 草案并存会导致 worker、publisher、projection、eval 全部要写两套。

### 风险 5：Phase 0 "口径收敛" 实际是大规模术语迁移
TMP Phase 0 写："更新正式 docs 中'Plate 是 projection'的说法，使其指向 ReaderDocumentGraph，而不是直接指向 ReaderPlateSnapshot.value "。

这是 危险操作 ：

- ReaderPlateSnapshot 已是 BFF 合同 + DTO seed + 测试基线
- 正式文档明确 "开发期不创建 V1/V2 类型"、"用 schema_kind 不用 schema_version "
- 把 "projection" 重新指向 Graph 需要迁移 snapshot serializer、Web slice、tests、API docs
- 这是 N 周的术语迁移工程，不解决"像卡片不像文档"的实际问题
## 需要补充或改写的设计点
### 1. ReaderDocumentGraph 必须明确定位为 ReaderPlateSnapshot 的内部 builder，而非新事实层
问题 ：TMP 说 "ReaderDocumentGraph 不是 source truth，也不是 Plate truth，而是可重建的产品语义层" —— 但 ReaderPlateSnapshot 已经是这个角色。

改写方向 ：

- 不引入 ReaderDocumentGraph 作为顶层新术语
- 把 TMP 提出的 ReaderDocumentNode 模型定位为 snapshot serializer 内部的中间表示 ： ReaderPlateSnapshot.value 的 builder
- 正式文档补一段 "Snapshot Value Builder" 章节描述 node tree → Plate value 的映射，但不新增 truth layer
- 保留 TMP 的 node_type 枚举作为 builder 内部归类用，不入持久化 schema
### 2. ProjectionAnchor 改写为 UserEditorialAssetAnchor 的扩展，不新立合同
问题 ：TMP 的 ProjectionAnchor 字段（ graph_version 、 node_id 、独立 scope 枚举）与已有 anchor 合同冲突。

改写方向 ：

- 继续使用 UserEditorialAssetAnchor ，复用已有 scope 字段
- node_id 不进 anchor （Node 是 builder 内部概念，不是 durable target）；Ask / Note 用 anchor_segment_id + layer_id + item_id 寻址，已有合同已支持
- graph_version 删除，统一用 last_event_sequence
- V1c 仍只允许 stable_source single-range 写入；非 source scope 的 Note/Ask 作为 V2 capability，单独走 gate
- user_note scope 不引入 —— user note 通过 asset_id + owner='user' 表达，scope 仍是 stable_source / translation / system_ai_layer 之一
### 3. Translation V2 改写：display_groups 移出 layer output，放到 projection 层
问题 ： display_groups 是渲染策略，不应进 worker output。

改写方向 ：

- Translation V2 layer output 采用正式文档已有 schema： { schema_version: 2, target_language, items: [{anchor_segment_id, source_text, source_text_hash, translated_text}], full_translation?, confidence, notes }
- 删除 segment_items 命名（与正式文档 items 冲突），统一用 items
- display_groups 作为 projection 层 derived data ：snapshot serializer 或 Web projection 从 items + Stable Document Blocks + 其他 layer presence（grammar_note / sentence_analysis）派生
- TMP 的 Display Group 规则（1-3 segments、不跨 block、长句单独成组）作为 projection policy 写入 plate-reader-projection.md，不进 enhancement-layers-and-parsed.md
- 这样 worker 可以按 unit 批量调 LLM 但只输出 per-segment items；不同 mode（沉浸/精读）可以有不同的 display_groups 派生
### 4. Ask context resolver 必须显式区分 read vs write，且 user note 读取需用户感知
问题 ：TMP 说 Ask resolver 返回 "如果 node 是 ask_supplement 或 user note, 则返回 supplement/note 内容 + origin source"，但未讨论 user note 被 Ask 默认读取的隐私边界。

改写方向 ：

- Ask resolver 只读 stable_source / translation / system_ai_layer / ask_supplement 四类（与 owner policy 的非 user 部分一致）
- 读取 user owner 资产（highlight / note）必须显式用户授权或当前 selection 命中的 asset；不能默认把全部 user note 喂给 Ask
- resolver 返回值结构改为： { visible_text, owning_node_origin, source_grounding, adjacent_visible_nodes, mode } ，不直接返回 user note body 除非用户选中
- 与 plate-reader-projection.md 的 Ask Document Tools 表合并，不另起 resolver 合同
### 5. 节点模型必须与 projection_ops 合同对齐
问题 ：TMP 的 ReaderDocumentNode 没有说如何映射到已有 projection_ops 的 op_type seed list（upsert_translation_node / add_ai_mark / upsert_ai_note_node / upsert_ask_supplement_node / upsert_user_highlight / upsert_user_note / remove_projection_node）。

改写方向 ：

- 每个 node_type 必须能 round-trip 映射到一个 op_type
- origin_ref 字段必须与 projection_ops 的 target 字段对齐（ unit_id / anchor_segment_id + layer_id / supplement_id / asset_id ）
- 删除 display_policy: Record<string, unknown> 这种 escape hatch —— display policy 必须是 typed schema，否则无法测试和跨端复用
- children 嵌套关系必须与 Plate element children 一致，避免另一套树结构
### 6. 长文 / 渐进式 orchestration 必须补 viewport 与 Ask 全文上下文的冲突
问题 ：TMP 把 "长文性能 → viewport-aware load" 作为风险缓解，但 Ask 需要全文上下文，viewport 内的 Graph 不够。

改写方向 ：

- 区分两种 Graph 消费： rendering graph （viewport-aware, 可 lazy）vs Ask context graph （full document, 可裁剪但不可 viewport-only）
- Rendering graph 可缓存、可分段加载
- Ask context graph 必须能从 truth（Stable Document Blocks + layers + assets）独立重建，不依赖 rendering graph 的 viewport state
- 渐进式 orchestration：当 translation layer 按 unit 发布时，Graph 的对应 node 必须 lazy attach，不阻塞首屏；这与已有 layer_published event + snapshot reload 机制一致，不引入新 event type
### 7. 跨端渲染必须显式覆盖小程序
问题 ：AGENTS.md 明确 "小程序、Web、未来 App 共享后端业务核心"、"客户端差异通过 render profile 处理"。TMP 说 "Plate.js 负责渲染和交互" —— 但小程序没有 Plate.js。

改写方向 ：

- 明确 ReaderDocumentGraph（或 Snapshot Value Builder）的输出是 renderer-agnostic node tree
- Web renderer = Plate.js，把 node tree 转 Plate value
- 小程序 renderer = 自有组件，消费同一 node tree（或 ReaderPlateSnapshot 的精简子集）
- 不在 Graph 层假设 Plate-specific 概念（如 leaf / mark / decoration）
### 8. 存储策略必须明确 "Graph 永远可丢弃" 的不变量
问题 ：TMP 中期方案说 "如生成成本过高，可引入 materialized reader_document_graph_nodes " —— 但未定义何时丢弃、如何与 event sequence 对齐。

改写方向 ：

- 不变量：任何 materialized graph cache 必须带 {record_id, base_id, generation, last_event_sequence}
- cache 命中条件： record_id + base_id + generation 匹配，且 last_event_sequence >= snapshot.last_event_sequence （不允许 stale read）
- cache miss 时从 truth 重建，重建路径必须与首次生成一致（deterministic）
- cache 不写入 reader_events ，不参与 anchor validation
- 长期如提升为后端 read model，必须新增独立 migration 和合同，不与 enhancement_layers / user_annotations / reader_notes 混表
## 建议保留的设计点
1. 核心洞察 ：unit 级整段译文是当前体验主要瓶颈 —— 正确，应作为 Translation V2 的驱动力
2. Display Group 规则 （1-3 segments、不跨 block、短句合并、长句单独成组、grammar_note 句后断开）—— 作为 projection policy 保留，只是不进 layer output
3. grammar_note 与 sentence_analysis 投影形态区分 （cue + compact note vs Structure Lens cue）—— 与正式文档 reader-record-plate-surface-ui.md 一致
4. V1 Highlight 只允许 stable_source —— 正确，与 V1c single-range stable-source 约束一致
5. Vocabulary mark visual resolver 统一 —— 正式文档已有 Marks / Cues Conflict Resolver （优先级 1-7 + 合并规则），TMP 应引用而非另立
6. 不保存 raw Plate JSON、Graph 可重建 —— 与正式文档 "Plate document 是 Web projection" 一致
7. 自定义 block component 必须渲染 Plate children —— 正式文档已有此约束，TMP 重申有意义
8. Plate comment/discussion 持久化仍是 Claread user assets —— 与已落地 CommentKit 改造一致
## 下一轮 grill 决策问题
### Q1（最关键）：是否真的需要 ReaderDocumentGraph 作为新术语？
- 选项 A：不引入新术语，把 TMP 的 node tree 定位为 ReaderPlateSnapshot.value 的 internal builder，正式文档补 "Snapshot Value Builder" 章节
- 选项 B：引入 ReaderDocumentGraph 作为 ReaderPlateSnapshot.value 的别名/超集，但明确两者不并存（Graph 是 value 的 superset，含 origin_ref）
- 选项 C：完全替换 ReaderPlateSnapshot 为 ReaderDocumentGraph （高风险，需迁移全量合同）
我的推荐 ：选项 A。能解决"像卡片不像文档"的问题（通过 node_type 枚举和 builder 规则），又不引入新事实层。

### Q2：Translation V2 schema 用哪一份？
- 选项 A：用 reader-record-plate-surface-ui.md 已有的 {schema_version:2, items, full_translation, confidence, notes} ，display_groups 作为 projection 派生
- 选项 B：用 TMP 的 {segment_items, display_groups} ，display_groups 进 layer output
- 选项 C：混合（items 进 layer，display_groups 进 layer quality metadata）
我的推荐 ：选项 A。worker 只负责对齐，projection 负责排版。

### Q3：非 source scope 的 Note 写入何时启用？
- 选项 A：V1c 不启用（保持 stable-source single-range only），V2 引入 UserEditorialAssetAnchorSet + 非 source scope 一起
- 选项 B：V1c 立即允许 translation scope Note，但 Highlight 仍只 stable_source
- 选项 C：全部 V2 再说，V1c 严守 stable-source
我的推荐 ：选项 C。与现有 V1c 约束一致，避免 layer regenerate 后的 rebase 问题在 V1c 就爆发。

### Q4：Ask resolver 读取 user note 的边界？
- 选项 A：Ask 默认不读 user note，除非用户当前选中该 note
- 选项 B：Ask 默认读当前 unit 内所有 user note
- 选项 C：Ask 读 user note 但不读 user highlight
我的推荐 ：选项 A。隐私最小化，与 "One Selection Source" 原则一致。

### Q5：Graph cache 何时引入？
- 选项 A：Phase 1 不引入，全程从 truth 重建
- 选项 B：Phase 1 引入 client-side cache，server 仍无 cache
- 选项 C：Phase 2 直接引入 server-side materialized view
我的推荐 ：选项 A。先验证体验闭环，再谈性能。与 TMP "先作为 snapshot/BFF projection" 一致，但明确 client-side cache 也不在 Phase 1。

### Q6：跨端 node tree 何时定型？
- 选项 A：Phase 1 就把 node tree 设计成 renderer-agnostic（含小程序消费约束）
- 选项 B：Phase 1 只考虑 Web/Plate，小程序后续再说
- 选项 C：在 packages/contracts 定义 shared node tree schema
我的推荐 ：选项 A。AGENTS.md 已明确多端共享后端核心，node tree 如果 Plate-specific 后续难改。

## 关键问题与反例汇总（5-10 个）
1. 重复事实源 ：ReaderDocumentGraph 与 ReaderPlateSnapshot 职责 90% 重叠 → 改为 snapshot 内部 builder
2. Anchor 合同冲突 ：ProjectionAnchor 的 node_id / graph_version 与已有 anchor 合同冲突 → 复用 UserEditorialAssetAnchor
3. Layer 渲染泄漏 ：display_groups 不应在 worker output → 移到 projection 层
4. V2 schema 双份 ：TMP 与正式文档已有 V2 草案字段名/结构不同 → 统一到正式文档版本
5. Note scope 越界 ：TMP 提议 V1 Note 支持所有 scope，但 V1c 已锁 stable-source → 推迟到 V2
6. Ask 读 user note 隐私 ：未定义 user note 被 Ask 默认读取的边界 → selection-driven only
7. projection_ops 映射缺失 ：ReaderDocumentNode 与已有 op_type seed list 未对齐 → 补映射表
8. viewport 与 Ask 全文冲突 ：长文 lazy load 与 Ask 全文上下文矛盾 → 分 rendering graph / Ask context graph
9. 跨端假设 Plate ：TMP 说 "Plate.js 负责渲染"，但小程序无 Plate.js → node tree 必须 renderer-agnostic
10. Phase 0 术语迁移风险 ：替换 ReaderPlateSnapshot 为 Graph 是 N 周工程且不解决 UX 问题 → 不做术语替换
结论 ：方案抓住了正确的问题（译文整段、callout 打断流、Ask 上下文不一致），但提出的 ReaderDocumentGraph / ProjectionAnchor / TranslationDisplayGroup 三件事需要先与正式文档的 ReaderPlateSnapshot / UserEditorialAssetAnchor.scope / TranslationLayerOutputV2 收敛，再进入实施。建议下一轮 grill 聚焦 Q1-Q6，先解决术语和合同对齐，再讨论 Phase 切分。