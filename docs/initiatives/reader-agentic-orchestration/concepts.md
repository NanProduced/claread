# Reader Agentic Orchestration 概念定义

> 状态：`D6 输入与文档型 Reader 术语修订`
> 最后更新：2026-06-25
> 用途：统一 Reader agentic orchestration 重构中的术语、边界和文档口径。

## 使用规则

- 本文件是 `docs/initiatives/reader-agentic-orchestration/` 下的术语事实源。
- 新文档、任务说明和代码命名应优先引用本表中的 Term。
- 如果概念发生变化，先更新本文件，再同步目标架构和模块文档。
- 不使用旧 AI Workflow 的 `task succeeded`、`render_scene_json truth`、`analysis result` 等说法描述新 Reader。
- 代码映射中的表名、字段名在 D3 schema 前是目标合同；实现时可以调整命名，但不能改变生命周期和所有权边界。
- 开发期核心类型、DTO 和 service 名不加 `V1` / `V2` 后缀。Reader snapshot wrapper 使用 `schema_kind`。版本只允许作为 layer output、fragment 等 serialized boundary payload 的 `schema_version` 字段出现，且不得泄漏到 orchestration 核心逻辑。

## 产品对象与输入适配

| 术语名称 (Term) | 中文名称 & 别名 | 业务定义 (Definition) | 生命周期 & 所有权 | 代码映射 (Code Mapping) | 易混淆对比 (Distinct From) |
|---|---|---|---|---|---|
| Reading Record | 阅读记录；文章记录 | 用户面对的长期阅读对象，聚合输入、稳定正文、阅读单位、增强层、用户编辑资产和运行历史。 | 由 Reader domain service 创建和持有；可长期存在，可被 supersede/cancel。 | 目标：`reading_records`；旧实现近似：`analysis_records`，但不做迁移兼容。 | 不是 workflow run；不是单次后台任务。 |
| Original Input | 原始输入 | 用户提交的原始内容，可为文本、URL、文件引用或图片引用。 | 由 Input Adapter 保存；用于审计、恢复和重新适配，默认不作为 Reader/Ask truth。 | 目标：`original_inputs` 或 `reading_records.original_input_ref`。 | 不等于 Stable Reading Base。 |
| Source Artifact | 来源产物；输入产物 | 文件、网页快照、PDF 页、OCR 图片等可审计输入产物及 metadata。 | 由 Input Adapter / provider adapter 追加；外部存储对象不能成为业务事实源。 | 目标：`source_artifacts`，OSS object metadata/checksum。 | 不等于正文；上传文件不是可直接阅读文本。 |
| Extraction Result | 抽取结果 | parser/OCR/VL/网页抽取后的文本、结构、置信度和 source loss 风险。 | 由 extractor 生成，可重算；进入后续适配决策。 | 目标：`extraction_results`。 | 不等于 Stable Reading Base；抽取成功也可能需要用户确认。 |
| Source Loss Risk | 来源损失风险；source loss flags | 输入抽取可能损失正文、顺序或结构的风险信号。 | 由 Input Adapter / extractor 产生；用于路由 low/high-impact adaptation。 | 目标：`extraction_results.source_loss_risk`。 | 不是 worker failure；不是 LLM confidence。 |
| Low-impact Input Adaptation | 低影响输入适配；低风险清洗 | 不改变作者可见语义的确定性规范化，如 line endings、Unicode space/invisible character、首尾空白、重复空行。低影响路径可直接生成 Stable Reading Base。 | 由 Input Adapter / Reading Base Builder 执行；必须记录 policy/version。 | 目标：`canonicalizer_version`、`input_adaptation_policy.impact_level = low`。 | 不包含 OCR 修复、正文重写、boilerplate 删除、表格/代码丢弃。 |
| High-impact Input Adaptation | 高影响输入适配；高风险正文适配 | 可能改变内容边界、阅读顺序或作者可见文本的处理，如 OCR 修复、多栏 PDF 顺序修复、网页正文抽取低置信、删除 boilerplate、表格/代码降级。必须先产出 Candidate Reading Base。 | 由 Input Adapter 触发；用户确认前不得冻结 Stable Reading Base。 | 目标：`candidate_reading_bases`、`reading_records.product_state = needs_confirmation`。 | 不等于 enhancement；不是后台 worker 可自动写入 Stable Base 的步骤。 |
| Candidate Reading Document | 候选阅读文档；Candidate Document；旧称 Candidate Base | 高影响适配或 suitability gate 需要确认时产生的候选文档，包含标题、blocks、source refs、canonical text mapping、warnings、table/image/footnote 等特殊块和解析策略。 | 由 Input Adapter / Document Parser 生成；用户确认或编辑后转为 Stable Reading Document。 | 目标：`candidate_reading_documents` 或当前过渡名 `candidate_reading_bases`。 | 不是临时 Stable Reading Document；未确认时不进入 RAG truth layer。 |
| Candidate Document Preview | 候选文档预览；确认预览 | 用户查看、编辑并确认 Candidate Reading Document 的产品步骤。确认的是“这个阅读文档是否可冻结”，不是只确认纯文本。 | Web Reader / Library UI 拥有交互；domain service 负责确认写入。 | 目标：`candidate_document_ready` / 过渡 `candidate_base_ready` milestone、confirm API。 | 不是 AI 批注预览；不是增强层发布。 |
| Stable Reading Document | 稳定阅读文档；Stable Document | 已确认、可阅读、适合 Claread 英语阅读提升的稳定文档，是同一 Reading Record 内的文档事实源，包含 stable blocks、source refs 和 canonical text mapping。 | 由 Reading Document Builder / Base Composer 冻结；同一 record 内不可变。 | 目标：后续 `reading_documents` / `reading_document_blocks`；当前过渡实现仍以 `reading_bases` 承载 canonical text。 | 不被 Enhancement Layer、Ask 或 Unit Builder 改写；不是 Plate document。 |
| Stable Document Block | 稳定文档块；Document Block | Stable Reading Document 内不可变的结构块，如 heading、paragraph、list item、blockquote、code block、table、image、caption、footnote。 | 由 Input Adapter / Base Composer 冻结；同一 document 内不可变。 | 目标：`reading_document_blocks`；当前 D5 仅有 heuristic unit metadata。 | 不等于 Plate node；不等于 Reading Unit。 |
| Canonical Text Layer | 规范文本层；Canonical Text | 从 Stable Reading Document 派生的线性文本层，用于 UTF-16 offsets、Reading Units、Anchor Segments、主解析 worker 和 text-range hash。 | 由 Reading Document Builder 派生并冻结；同一 document 内不可变。 | 当前：`reading_bases.text`；后续可作为 document-derived text layer。 | 不等于完整文档结构；table/image/footnote 可能以独立 block 进入 RAG/Ask，但不都进入主解析。 |
| Supersede | 替代；重建记录 | 用新 Reading Record 替代旧记录的产品动作。 | 由用户或 domain service 发起；旧 record 标记 superseded。 | 目标：`reading_records.superseded_by_record_id`。 | 不是在原 record 内修改 Stable Base。 |

## 文本坐标与阅读单位

| 术语名称 (Term) | 中文名称 & 别名 | 业务定义 (Definition) | 生命周期 & 所有权 | 代码映射 (Code Mapping) | 易混淆对比 (Distinct From) |
|---|---|---|---|---|---|
| Reading Document Builder | 阅读文档构建器；Base Composer；过渡名 Reading Base Builder | 把低影响输入或已确认 Candidate Document 冻结为 Stable Reading Document，并派生 Canonical Text Layer、Reading Units、Anchor Segments 和 Navigation Skeleton。 | Reader domain service 拥有；文本坐标派生必须 deterministic。 | 目标 service：`reading_document_builder` / 当前 `reading_base_builder`。 | 不是 OCR/parser；不负责高影响清洗；不把 Plate JSON 当 truth。 |
| Reading Unit | 阅读单元；Unit | Canonical Text Layer 上不可变、连续、非重叠的编排和调度单位，用于 translation、parsed coverage、navigation 和渐进增强调度。 | 由 Reading Document Builder 从 Canonical Text Layer 生成；同一 base/document 内不可变。 | 目标：`reading_units`，字段含 `unit_id`、`order_index`、base absolute UTF-16 offsets、`text_hash`。 | 不等于自然段；不等于一句话；不等于 Stable Document Block；不是用户选区最小坐标单位。 |
| Anchor Segment | 锚点片段；sentence-like segment | Reading Unit 内稳定的 span 锚点单位，通常是句子；在无可靠句子边界时可为 clause 或 fallback window。span-bound anchor 必须引用 `anchor_segment_id`，并且 unit-local UTF-16 offset 必须落在该 segment 范围内。 | 由 Reading Base Builder 生成；同一 base 内不可变。 | 目标：`anchor_segments`，字段含 `anchor_segment_id`/兼容 `sentence_id`、`segment_type`、base absolute offsets、unit offsets、`text_hash`。 | 不等于 Reading Unit；`segment_type = clause/fallback_window` 时不应称为真实句子。 |
| Text Coordinate Scope | 文本坐标范围；坐标作用域 | 文本坐标的参照范围：Reading Unit/Anchor Segment 持久事实记录 Stable Base absolute UTF-16 offsets；`ReaderTextRangeAnchor.start_offset/end_offset` 使用 unit-local UTF-16 offsets，并由 `anchor_segment_id` 约束。Plate leaf `segment_start_utf16/segment_end_utf16` 是派生的 segment-local projection metadata。 | 由 contracts 强制；Publisher 和 User Editing Surface 必须校验。 | 现有：`services/api/app/contracts/annotation.py`、`packages/contracts`；算法 `fnv1a32-utf16`。 | 不允许把 Plate path 或全文 absolute offset 当作 span anchor 字段。 |
| Navigation Skeleton | 基础导航骨架 | `article_ready` 所需的基础导航结构，基于 Reading Units 和可选 heading metadata 生成。 | 由 Reading Base Builder 生成；可由后续 Semantic Outline 增强。 | 目标：`navigation_skeleton` / Reader snapshot fragment。 | 不等于 Semantic Outline；不阻塞于 LLM summary。 |
| Boundary Quality | 切分质量；boundary quality | 对 deterministic Unit/Anchor Segment 边界自然度和风险的结构化评估。 | 由 builder 生成；可触发 D5+ refiner 或 action signal。 | 目标字段：`reading_units.boundary_quality`、builder diagnostics。 | 不是 parsed quality；不是 LLM layer confidence。 |
| Unit Boundary Refiner | Unit 边界改良器；受控 LLM refiner | D5+ 可选 LLM worker，只能基于既有 Anchor Segments 输出 split/merge 边界建议，不能改写文本或生成坐标。 | 由 Policy Planner 按质量信号触发；结果必须经 builder validator 接受。 | 目标：`planner_suggestion_json` / `boundary_refiner_suggestion`。 | 不是 LLM Unit Builder；不是 Stable Base 修复器。 |

## Reader Plate Projection

| 术语名称 (Term) | 中文名称 & 别名 | 业务定义 (Definition) | 生命周期 & 所有权 | 代码映射 (Code Mapping) | 易混淆对比 (Distinct From) |
|---|---|---|---|---|---|
| Reader Plate Document | Reader Plate 文档；Plate Article Body | Web Reader Article Body 的 Plate.js read-only 文档投影，承载 Stable Reading Document、译文、AI 批注、Ask Supplement 和用户资产的富文本呈现。 | Projection 层生成；可由后端 domain facts 重建；前端临时持有。 | 目标：`apps/web/src/lib/reader-plate` 下的新 Plate value/schema；`platejs/react` 渲染。 | 不是后端 truth；不是旧 `render_scene_json`；不是 Stable Reading Document。 |
| Base Plate Snapshot | 基础 Plate 快照 | `article_ready` 时由 Stable Reading Document、Stable Document Blocks、Canonical Text Layer、Reading Units 和 Anchor Segments 生成的基础 Plate document。 | Reader projection service 生成；刷新恢复时可重建。 | 目标：`ReaderPlateSnapshot` 中的 `value`。 | 不等于完整增强结果；不等待 RAG 或所有 layers。 |
| Plate Projection | Plate 投影 | 将 domain facts 转为 Reader Plate Document、projection operations 和前端可应用 fragment 的过程。 | Projection 层拥有；不得改变 domain truth。 | 目标：`plate_projection` / `projection_operations` service。 | 不等于 Layer Publisher 的业务发布。 |
| Projection Operation | 投影操作；projection op | 指向稳定 domain target 的增量投影操作，如对某 unit 插入译文、对某 Anchor Segment 添加 AI mark。 | 由 projection emitter 产生；可由 domain facts 重建；前端按事件顺序应用。 | 目标：`reader_events.event_type = projection_ops`。 | 不等于 raw Slate operation；不得持久化 Plate path。 |
| Plate Fragment | Plate 片段 | 通过 allowlist 和 sanitize 后可插入 Plate Document 的局部富文本 fragment。 | 由 projection layer 或 frontend converter 生成；用于渲染，不作为业务事实。 | 目标：`fragment.format = plate_fragment`。 | 不等于 LLM 任意 JSON；不等于 Enhancement Layer truth。 |
| Canonical Text Mapping | 规范文本映射 | Plate text leaves / Anchor Segment nodes 到 Stable Base UTF-16 offsets 的映射。 | Reading Base Builder / projection layer 生成；anchor validation 使用。 | 目标：`anchor_segment_id`、`base_start_utf16`、`base_end_utf16`、text hash metadata。 | 不等于 Plate node path。 |
| Plate Path Adapter | Plate 路径桥接器 | 在当前前端 Plate tree 中把 `unit_id` / `anchor_segment_id` 与临时 Plate path 互转。 | 前端 projection runtime 拥有；patch 后可失效重建。 | 目标：`apps/web/src/lib/reader-plate/adapters/*`。 | 不持久化；不是 domain anchor。 |
| Document Tool | 文档工具 | Ask 或 UI 调用的受控工具，如 read range、propose note、write supplement。 | Ask Sidecar Bridge / User Editorial API 拥有；必须走 Authorization Envelope。 | 目标：`document_tools` service / API。 | 不是让 LLM 直接改 DOM 或直接写 Plate JSON。 |
| Content Owner | 内容所有权；owner | Plate projection 中节点或 mark 的权限类别：`stable`、`system_ai`、`ask_supplement`、`user`、`ephemeral`。 | 后端 policy 是权威；前端 Plate plugin 做 UX 镜像。 | 目标：projection node metadata `owner`。 | 不等于数据库 owner/user_id；用于编辑权限。 |

## 编排运行时与策略

| 术语名称 (Term) | 中文名称 & 别名 | 业务定义 (Definition) | 生命周期 & 所有权 | 代码映射 (Code Mapping) | 易混淆对比 (Distinct From) |
|---|---|---|---|---|---|
| Reader Run | 阅读运行；run | 由用户或系统触发的 bounded background run，用于一批可审计 orchestration 工作。 | Orchestration Runtime 创建；可 cancel、expire、complete。 | 目标：`reader_runs`。 | 不是长期产品对象；不是 Reader 页面会话。 |
| Reader Job | 阅读任务；job | run 内可 claim、heartbeat、retry 的 typed execution unit，如 translation job、rag indexing job。 | Guarded Executor 拥有执行状态；worker claim 后处理。 | 目标：`reader_jobs`。 | 不是 Product State。 |
| Authorization Envelope | 授权信封；预算与权限边界 | 代码强制的 cost、steps、unit range、retry、concurrency、context、asset write 边界。 | 由 domain/policy 层创建；planner 和 sidecar action 必须遵守。 | 目标：`authorization_envelopes` 或 run/job metadata。 | 不是 prompt 里的提醒。 |
| Policy Planner | 策略规划器 | Deterministic 决策层，根据持久状态、policy table 和 envelope 输出 typed plan。 | D4 实现；不调用模型。 | 目标 service：`policy_planner`。 | 不是 LLM Planner。 |
| Semantic Reviewer | 语义审查器 | 受控 LLM worker，用于复杂文档结构审查、layer 语义价值判断或 Candidate Base 高风险修复建议。 | D5+ 可选；输出结构化建议。 | 目标：PydanticAI typed worker。 | 不是默认 planner；不拥有最终状态写入权。 |
| Grammar Bundle Worker | 语法组合 worker；grammar worker | 可一次 LLM 调用生成 `grammar_note` 与 `sentence_analysis` 两类 layer subtype 的 typed worker。 | D5 初版可合并执行；发布时必须拆成独立 subtype 校验和投影。 | 目标：`reader_layer_grammar_bundle` route / `GrammarDraft(grammar_notes, sentence_analyses)`。 | 不是单一 layer type；不等于把两类批注混存。 |
| Skip Gate | 跳过门禁 | job 入队或 claim 前的 deterministic skip/pause/reject/retry_later 检查。 | Policy / Executor 拥有；避免无效 LLM 调用。 | 目标 service：`skip_gate`，字段 `rationale_code`。 | 不是让模型判断要不要跑。 |
| Model Profile | 模型配置档 | provider model 的配置化路由与成本描述。 | Policy / Cost Control 拥有；通过 route lookup 使用。 | 目标：`model_profiles`。 | 不是 planner 即兴选模型。 |
| Usage Bucket | 用量归因桶 | usage audit 的成本归因维度，如 record、job、layer、model profile、cache status、planner kind。 | Usage audit 拥有；用于成本基线和回归。 | 目标：扩展 `ai_usage_events`。 | 不是只记录总 token。 |
| Prompt Cache | Prompt 缓存；provider cache | provider 对重复 prompt prefix 的缓存能力或等价成本优化。 | LLM adapter 记录 cache hit/miss；prompt 结构需配合。 | 目标字段：`cache_hit`、`cache_class`。 | 不是缓存模型回答。 |

## 增强层与用户编辑

| 术语名称 (Term) | 中文名称 & 别名 | 业务定义 (Definition) | 生命周期 & 所有权 | 代码映射 (Code Mapping) | 易混淆对比 (Distinct From) |
|---|---|---|---|---|---|
| Enhancement Layer | 增强层 | 建立在 Reading Units / Anchor Segments 上的可再生系统增强，如 translation、AI annotation、summary。 | Layer Workers 生成，Layer Publisher 发布；可局部重试。 | 目标：`enhancement_layers`。 | 不修改 Stable Base；不拥有用户编辑资产。 |
| System Annotation Layer | 系统 AI 批注层；AI Annotation Layer | Enhancement Layer 中由系统 worker 生成的 AI 批注，如 vocabulary items、grammar_note、sentence_analysis。 | 系统拥有；可再生、可替换、不可直接编辑。 | 目标：`enhancement_layers.layer_type in (...)`。 | 不等于用户笔记、高亮或 Ask 保存内容。 |
| Vocabulary Layer | 词汇批注层 | 一个 Enhancement Layer，内部 item 可为 `vocab_highlight`、`phrase_gloss`、`context_gloss`。 | Vocabulary Worker 生成，Layer Publisher 校验 anchor/source grounding 后发布。 | 目标：`enhancement_layers.layer_type = vocabulary`，`output_json.items[].item_type`。 | 不是三个顶层 layer type。 |
| Vocab Highlight | 词汇高亮；`vocab_highlight` | 对有学习价值的词做 AI 高亮，可只有高亮，也可带简短解释。 | 属于 Vocabulary Layer item；可再生、可替换。 | 目标：`output_json.items[].item_type = vocab_highlight`。 | 不等于用户高亮；不是必须有完整释义的 gloss。 |
| Phrase Gloss | 短语释义；`phrase_gloss` | 对短语、搭配、习语、专名或复合表达做解释。 | 属于 Vocabulary Layer item；span anchor 必须通过 `anchor_segment_id` 校验。 | 目标：`output_json.items[].item_type = phrase_gloss`。 | 不等于单词高亮；不作为独立 layer type。 |
| Context Gloss | 上下文释义；`context_gloss` | 对依赖当前语境才能正确理解的词义或表达做解释。 | 属于 Vocabulary Layer item；优先级高于 phrase / vocab highlight。 | 目标：`output_json.items[].item_type = context_gloss`。 | 不等于词典通用释义；必须绑定当前文本上下文。 |
| Grammar Note | 语法旁注；grammar_note | Span-bound 语法点说明，必须锚定到一个或多个 `anchor_segment_id` + unit-local ranges。 | Grammar Bundle Worker 或专用 worker 生成；Layer Publisher 独立校验。 | 目标：`enhancement_layers.layer_type = grammar_note`。 | 不等于 sentence_analysis；不是整句结构拆解。 |
| Sentence Analysis | 句子结构分析；sentence_analysis；长难句拆解 | Sentence/unit-bound 结构解析，可包含 chunks，用于解释句子主干、层次和理解难点。 | Grammar Bundle Worker 或专用 worker 生成；只对适用句子发布。 | 目标：`enhancement_layers.layer_type = sentence_analysis`。 | 不等于 grammar_note；`long_sentence` 只是适用场景描述，不作为权威 layer 名称。 |
| Layer Worker | 增强层 worker | 生成 candidate layer output 的 typed worker。 | Worker runtime 执行；不得直接写 UI projection。 | 目标：translation/vocabulary/grammar bundle worker。 | 不是 Publisher；不决定 parsed milestone。 |
| Layer Publisher | 增强层发布器 | 校验并原子发布 Enhancement Layer 的模块，负责 schema、anchor、source grounding、CAS。 | Domain service 拥有；发布后写 Reader Event。 | 目标 service：`layer_publisher`。 | 不是 LLM worker；不得写 User Editorial Assets。 |
| Parsed Decision | 解析完成判断；单元 parsed 判断 | 对单个 Reading Unit 是否达到当前策略下 parsed 的审计判断。 | Publisher / policy aggregate 写入；可抽样 eval。 | 目标：`parsed_decisions`。 | 不是批注数量；不是 worker success。 |
| Parse Coverage | 解析覆盖率 | 已达到 parsed 的 Reading Units 覆盖比例。 | Aggregate 推导；应单调增加，除非 supersede/cancel。 | 目标：coverage view / Reader snapshot。 | 不是 task progress 百分比。 |
| User Editing Surface | 用户编辑区 | Reader 中承载用户高亮、笔记、保存 Ask 建议等可编辑内容的界面区域。 | 前端 UI 和 User Editorial Asset API 拥有。 | 目标：Web Reader editing surface。 | 不写入 Enhancement Layer。 |
| User Editorial Asset | 用户编辑资产 | 用户拥有和可编辑的阅读资产，如 highlight、reader note、保存的 Ask note/highlight、生词动作。 | 用户控制生命周期；系统层 retry 不得删除或覆盖。 | 目标：`user_editorial_assets` 或现有 note/highlight 表重构。 | 不等于 System Annotation Layer。 |
| Ask Sidecar | Ask 侧边助手 | 绑定当前 Reading Record 的侧边问答助手。 | Ask Bridge 进入同一 Authorization Envelope；不控制 orchestration。 | 目标：Ask sidecar API / bridge。 | 不是 orchestrator；不是全局知识库入口。 |
| Ask Supplement | Ask 补充内容 | Ask Claread 生成、用户确认后追加到当前阅读页的 AI 补充入口，如补充 grammar note。 | 用户可删除；来源必须标记 `ask_supplement` / `assistant_supplement`。 | 目标：`ask_supplements`。 | 不等于系统 AI 批注层；不等于用户笔记。 |

## 状态、事件与投影

| 术语名称 (Term) | 中文名称 & 别名 | 业务定义 (Definition) | 生命周期 & 所有权 | 代码映射 (Code Mapping) | 易混淆对比 (Distinct From) |
|---|---|---|---|---|---|
| Product State | 产品状态 | Reader / Library 可见的业务状态，如 `needs_confirmation`、`readable_enhancing`。 | Domain service 写入；前端直接消费。 | 目标：`reading_records.product_state`。 | 不是 worker execution state。 |
| Run / Job State | 运行状态；执行状态 | worker 执行状态，如 `queued`、`claimed`、`retry_later`、`succeeded`。 | Orchestration Runtime 拥有。 | 目标：`reader_runs.status`、`reader_jobs.status`。 | 不表达用户产品语义。 |
| Reader Event | Reader 领域事件 | 面向 UI projection 的持久领域事件，如 layer published、coverage changed、asset changed。 | Domain/Publisher 写入；SSE/polling 消费。 | 目标：`reader_events`。 | 不是 LLM token stream；不是 worker diagnostic。 |
| Projection Event | 投影事件 | `reader_events` 中面向 Web Plate projection 的事件，如 `projection_ops`。 | Projection emitter 写入；可由 domain facts 重建。 | 目标：`reader_events.event_type = projection_ops`。 | 不等于业务事实本身；不等于 raw Slate operation。 |
| Reader Job Event | Reader job 诊断事件 | worker 诊断事件，如 claim、heartbeat、attempt、requeue。 | Executor / Observability 拥有。 | 目标：`reader_job_events` 或 logs。 | 不进入 SSE 主流。 |
| Reader Snapshot | Reader 快照；projection | 当前 Reader projection 的可恢复快照或 GET 聚合结果。 | Projection 层生成；可由业务事实重建。 | 目标：Reader BFF snapshot response。 | 不是唯一事实源。 |
| `candidate_document_ready` / 过渡 `candidate_base_ready` | 候选文档可确认 | 高影响适配产生可预览 Candidate Document，等待用户确认或编辑。 | Product milestone；由 Input Adapter/domain gate 写入。 | 目标：product state / reader event。 | 不是 `article_ready`。 |
| `article_ready` | 文章可读 | Stable Reading Document、Stable Document Blocks、Canonical Text Layer、Reading Units、Anchor Segments 和基础导航已可用，用户可以开始阅读。 | Product milestone；由 domain gate 写入。 | 目标：product state / reader event。 | 不等全文解析完成；不等待 RAG 或增强层。 |
| `initial_enhancement_ready` | 初始增强可用 | 第一批可见增强层已发布，通常是当前或起始 units 的译文。 | Product milestone；由 layer aggregate 推导。 | 目标：product state / reader event。 | 不是 coverage complete。 |
| `coverage_complete` | 解析覆盖完成 | 当前策略下所有目标 units 都有 parsed decision。 | Aggregate 推导；planner 不直接写。 | 目标：product state / coverage view。 | 不是 worker run success。 |
| `action_required` | 需要用户处理 | 需要确认、配额、继续、重试或修复的可见状态。 | Domain service 写入；Reader/Library 必须可发现。 | 目标：`reading_records.product_state`。 | 不是 terminal failure 的唯一表达。 |
| Cancel | 取消 | 取消当前 run 或未发布 jobs。 | 用户或系统触发；不回滚已发布 layer。 | 目标：`reader_runs.status = cancelled`、job cancel flags。 | 不是删除 Reading Record。 |

## RAG 与引用

| 术语名称 (Term) | 中文名称 & 别名 | 业务定义 (Definition) | 生命周期 & 所有权 | 代码映射 (Code Mapping) | 易混淆对比 (Distinct From) |
|---|---|---|---|---|---|
| RAG Substrate | RAG 检索底座 | 当前 Reading Record 内的检索底座，基于 Stable Reading Document、Stable Document Blocks、Canonical Text Layer、Reading Units、Anchor Segments 和已发布增强层构建。 | RAG worker 异步构建；`article_ready` 不等待。 | 目标：`rag_substrates`、`rag_chunks`、vector metadata filter。 | 不是全局知识库；不是 Original Input 默认上下文。 |
| RAG Citation | RAG 引用 | Ask 或解释结果返回的可校验引用，包含 substrate、stable document/base、block、source scope、hash、snippet，必要时包含 unit / Anchor Segment / offsets。 | RAG adapter 返回，Ask 接受前校验。 | 目标：citation DTO。 | 不是模型口头声称的引用；不是只回到线性文本 offset。 |
| Source Scope | 来源范围；allowed source scope | RAG/Ask 查询允许使用的来源边界，如 current unit、viewport units、published layers。 | Authorization Envelope 控制；query 必须显式携带。 | 目标：RAG query DTO `allowed_source_scope`。 | 不是 provider filter 的全部；业务层先约束。 |

## 状态口径

| 表述 | 统一口径 |
|---|---|
| “文章解析完成了” | 必须说明是 `article_ready`、`initial_enhancement_ready` 还是 `coverage_complete`。 |
| “任务失败了” | 区分 run/job 执行失败，或 Product State 进入 `action_required` / terminal failure。 |
| “RAG 已完成” | 说“当前 record 的 `rag_substrate` 达到 `substrate_ready`”，并说明覆盖的 `source_scope`。 |
| “Ask 写入批注” | Ask 请求 sidecar action，用户确认后写 User Editorial Asset 或 Ask Supplement。 |
| “重新解析这篇文章” | 如果 Stable Reading Document / Canonical Text 已冻结，创建新 Reading Record 或 supersede 旧 record。 |
| “LLM 切正文” | LLM 不生成 Stable Reading Document / Canonical Text 坐标；如启用 Unit Boundary Refiner，也只能建议既有 Anchor Segments 的 split/merge。 |

## 与旧实现的关系

| 旧说法 | 新说法 |
|---|---|
| `analysis_records` | `reading_records`，但本轮不做数据迁移兼容。 |
| `analysis_tasks` | `reader_runs` + `reader_jobs`。 |
| `analysis_results.render_scene_json` | Stable Reading Document -> Reader Plate projection / snapshot，不做旧 scene 映射。 |
| `learning_workflow` | bounded planner + typed jobs + layer publisher。 |
| `parallel_agents_node` | layer workers。 |
| `task succeeded` | 明确的 Reader milestone 或 Parsed coverage。 |
