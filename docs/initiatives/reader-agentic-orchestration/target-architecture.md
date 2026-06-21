# Reader Agentic Orchestration 目标架构

> 状态：`D3 active`
> 最后更新：2026-06-20
> 范围：用户提交内容的 `learning` Reader 解析。

## 目标

把当前固定 AI Workflow 解析链路，重构为围绕长期 Reading Record 的 bounded agentic orchestration。

新 Reader 的产品感知应是“有来源约束的稳定阅读产物 + 后台渐进增强”，不是 agent 工作台，也不是页面级常驻 LLM 线程。

## 文档结构

本文件只保留目标架构总览、硬约束和决策记录。模块细节拆到独立文档。

| 文档 | 内容 |
|---|---|
| `concepts.md` | 按模块分组的概念定义、生命周期、代码映射和易混淆对比 |
| `modules/input-adapter.md` | 输入适配、Source Artifact、Extraction Result、Candidate Base |
| `modules/reading-base-and-units.md` | Stable Base、Reading Units、Anchor Segments、UTF-16/hash、`article_ready` gate |
| `modules/orchestration-runtime.md` | run/job、worker lease、并发、Authorization Envelope、framework posture |
| `modules/policy-and-cost-control.md` | Policy Planner、Skip Gate、Prompt Cache、Model Profile、Usage Bucket |
| `modules/enhancement-layers-and-parsed.md` | Enhancement Layer、System Annotation Layer、User Editing Boundary、anchor、Parsed Decision |
| `modules/streaming-and-projection.md` | Reader Events、snapshot、SSE、polling fallback |
| `modules/plate-reader-projection.md` | Plate.js Article Body、projection operations、document tools、owner 权限、anchor bridge |
| `modules/rag-substrate.md` | record-scoped RAG、citation DTO、provider adapter |
| `modules/cutover-and-old-workflow.md` | 停服重构、旧 workflow 移除、旧依赖审计 |

## 产品形态

用户面对的对象是 `Reading Record`，不是 workflow run。

Reader 体验应符合：

- 先出现稳定可读文章。
- 有稳定 Reading Units、Anchor Segments 和基础导航。
- 译文和其他增强层渐进到达。
- Ask Claread 是绑定当前文章的侧边助手。
- 用户高亮、笔记和保存的 Ask 建议作为用户编辑资产叠加在稳定正文上。
- 只有在用户确认、配额、继续、失败修复等边界上才出现需要用户处理的状态。

默认 Reader UI 不把 planner trace、token stream 或任务看板作为主界面。

## 范围边界

本轮包含：

- 替换 `learning workflow`。
- Web Reader 首轮验证。
- 新后端数据契约与 orchestration runtime。
- Event log、SSE/polling projection、usage audit、eval hooks。
- 当前 Reading Record 内的 RAG substrate contract。
- 文本、URL、PDF、OCR、文件上传等输入适配 contract。

本轮不包含：

- Daily Reader runtime 重构。
- `academic workflow` 重构；待 learning workflow 验证稳定后再单独设计。
- 第一阶段小程序实现。
- 旧开发数据迁移。
- 旧 `render_scene_json` 兼容映射。
- 全局 User Editorial Assets RAG 和跨记录知识库化。

## Cutover 立场

Claread 尚未上线，本轮重构不要求旧数据兼容。

允许解析功能在重构期间不可用。推荐流程：

```text
service stop / parsing disabled
-> rewrite learning AI Workflow to Reader orchestration
-> reset schema baseline, preserve dictionary tables
-> update Web Reader UI to new Reading Record API
-> validate text parsing vertical slice
-> add Candidate Base / RAG / advanced layers
-> adapt or remove remaining old consumers
-> delete old workflow code and tables
```

数据库重置必须保护：

- `dict_entries`
- `dict_lookup_targets`
- `dict_redirects`

## 核心模块

| 模块 | 职责 | 主要输出 |
|---|---|---|
| Input Adapter | 接收文本、URL、文件、PDF、OCR 图片；保存输入产物；判断低/高影响适配 | Original Input、Source Artifact、Extraction Result、Candidate/Stable Base |
| Reading Base Builder | 生成、确认、冻结 Stable Reading Base；生成不可变 Reading Units、Anchor Segments 和基础导航 | Stable Base、Reading Units、Anchor Segments、Navigation Skeleton |
| Orchestration Planner | 基于 record state 和 envelope 规划下一批 bounded jobs | typed plan |
| Guarded Executor | claim jobs、heartbeat、retry、cancel/supersede、usage audit | Reader Runs / Jobs |
| Policy / Cost Control | Skip Gate、model routing、prompt cache、usage bucket、cost baseline | policy decisions、model profiles、usage aggregates |
| Layer Workers | 翻译、词汇、语法、长难句、summary 等 typed execution | candidate layer output |
| Layer Publisher | 做 schema、anchor、source grounding、generation guard 和 CAS 发布 | published Enhancement Layers、System Annotation Layers、Parsed Decisions、Reader Events |
| Event / Projection | 持久 reader events、snapshot、SSE、polling fallback、projection operations | Reader projection |
| Plate Reader Projection | 将 domain facts 投影为 Plate.js Article Body 文档和稳定目标的 projection operations | Base Plate Snapshot、Projection Operations、Document Tools |
| RAG Substrate | 基于 Stable Base / Units 构建当前记录内检索底座 | RAG chunks、citations |
| Ask Sidecar Bridge | Ask 的继续增强、保存笔记/高亮、追加补充、扩大上下文等动作接入 envelope | sidecar actions、Ask Supplements、User Editorial Assets |
| Eval / Observability | usage audit、trace correlation、eval sampling | usage events、eval samples |

## 核心领域对象

| 对象 | 职责 | 可变性 |
|---|---|---|
| Reading Record | 用户面对的长期阅读对象 | 长期存在 |
| Original Input | 用户提交的原始材料，用于审计、恢复和重新适配 | 保留；默认不作为 Reader/Ask truth |
| Source Artifact | 文件、网页、OCR 图片、PDF 页面等输入产物及 metadata | 可追加处理结果 |
| Extraction Result | 抽取文本、结构、置信度、source loss 风险 | 可重算 |
| Candidate Reading Base | 高影响适配后的候选正文 | 确认前可编辑 |
| Stable Reading Base | 已确认的稳定可读正文 | 同一 record 内不可变 |
| Reading Units | 带 base-absolute offsets/hash/order 的稳定阅读单位 | 同一 record 内不可变 |
| Anchor Segments | Reading Unit 内 sentence-like 的稳定锚点段，通常是句子；必要时可为 clause 或 fallback window | 同一 record 内不可变 |
| Navigation Skeleton | 基于稳定 units 的基础导航 | `article_ready` 必需 |
| Enhancement Layers | 译文、System Annotation Layers、summary、Semantic Outline 等 | 可再生、可局部重试 |
| System Annotation Layers / AI Annotation Layers | 系统 worker 生成的 AI 批注层，如词汇、语法、长难句 | 可再生、不可直接编辑 |
| Ask Supplements | Ask 经用户确认后追加到阅读页的 AI 补充入口 | 用户可删除；与系统层分源 |
| RAG Substrate | 当前 record 内检索底座 | 可异步构建 |
| Parsed Decisions | 单元级 parsed 判断与 rationale | 可审计 |
| User Editorial Assets | 高亮、笔记、保存的 Ask note/highlight、生词动作 | 用户控制；不被系统层重写 |
| Reader Runs / Jobs | bounded background execution | 执行事实，不是产品对象 |
| Reader Events / Snapshots | streaming 和恢复用 projection | 可由业务表重建 |
| Reader Plate Document | Web Reader Article Body 的 Plate.js 文档投影 | projection，不是 truth |
| Projection Operation | 指向 unit、Anchor Segment、layer 或 user asset 的稳定投影操作 | 可由业务事实重建 |

## Reader Milestones

| 里程碑 | 最小合同 | 用户含义 |
|---|---|---|
| `candidate_base_ready` | 高影响适配产生可预览候选正文 | 用户需要确认或编辑 |
| `article_ready` | Stable Base、Reading Units、Anchor Segments、unit offsets/hash、标题/语言/source metadata、基础 Navigation Skeleton | 用户可以开始阅读 |
| `substrate_ready` | 当前 record RAG / Ask 基础上下文可用 | Ask 和高级能力更可靠 |
| `initial_enhancement_ready` | 第一批有用可见增强可用，通常是当前或起始 unit 译文 | 阅读辅助已可用 |
| `coverage_complete` | 当前策略下应 parsed 的 units 都已有 Parsed Decisions | 普通文章进入完成态 |
| `action_required` | 需要确认、配额、继续、重试或修复 | Reader 和 Library 必须可发现动作 |

`article_ready` 必须轻量，不能等待全文译文、完整 Semantic Outline、完整向量索引或所有批注层。

## D4 最小纵切

D4 只做低风险纯文本路径：

```text
POST text input
-> Original Input
-> Stable Reading Base
-> Reading Units + Anchor Segments
-> Base Plate Snapshot
-> Navigation Skeleton
-> article_ready
-> translation layer for first/current units
-> Parsed Decision
-> Reader events / Plate progressive render
```

D4 不做：

- URL / PDF / OCR / 文件上传实现。
- Candidate Base preview UX。
- vocabulary / grammar_note / sentence_analysis / summary / Semantic Outline。
- RAG 阻塞阅读。
- 小程序适配。
- 旧 `render_scene_json` 兼容映射。

## Runtime 立场

使用 PostgreSQL-backed bounded run/job 模型。

```text
Web Reader
  -> Reader API / BFF
  -> PostgreSQL Reading Record + run/job state + event log
  -> worker abstraction
  -> typed execution units
  -> LangSmith + ai_usage_events
```

D4 默认：

- Planner 先用 deterministic policy function。
- PydanticAI 用于 LLM-backed workers。
- LangGraph 不作为 D4 默认依赖；D5+ 如需要 branching / interrupt / complex repair flow，再做单独引入。
- 外部 MQ / Temporal 不作为 D1-D4 默认依赖。

## 状态分层

| 层 | 负责 | 不负责 |
|---|---|---|
| Product State | Library / Reader 可见状态 | worker 细节 |
| Run / Job State | claim、heartbeat、retry、cancel、execution failure | 用户产品语义 |
| Event / Projection State | SSE、polling、snapshot、刷新恢复 | 业务事实源 |

禁止用一个 task status 表达所有状态。

## RAG 立场

本轮 RAG 只服务当前 Reading Record。

- 默认 truth layer 是 Stable Base / Reading Units。
- `article_ready` 不等待 `substrate_ready`。
- 默认 shared collection + metadata filter，不默认 collection-per-record。
- RAG provider 必须 adapter 化。
- Citation 必须可校验到 base、unit、可选 Anchor Segment、hash 和 snippet。

## 硬约束

- Reader 页面不是常驻 LLM 线程。
- Reading Record 是长期产品对象。
- Stable Reading Base 在同一 record 内不可变。
- Reading Units 在同一 record 内不可变。
- Span anchors 使用 `anchor_segment_id` + unit-local UTF-16 offsets；offset 必须落在目标 Anchor Segment 的 unit range 内。Segment-local offsets 只作为 Plate leaf projection metadata 派生，不作为 domain anchor 持久字段。
- Stable Reading Base 是输入适配和必要用户确认后的可读英文正文；Unit Builder 不负责 OCR 修复、boilerplate 删除、多栏顺序修复或正文重写。
- Unit Builder 默认 deterministic；D5+ LLM Unit Boundary Refiner 只能建议既有 Anchor Segments 的 split/merge，不能改写文本、生成坐标或绕过 validator。
- 高影响输入适配必须先用户确认。
- `article_ready` 不等待增强层或 RAG。
- 译文是 D4 parsed 的最低门槛。
- 禁止用批注数量作为 parsed 阈值。
- Enhancement Layers 和 System Annotation Layers 可再生，不得修改 User Editorial Assets。
- Ask 保存 note/highlight 必须经用户确认后写 User Editorial Assets；Ask Supplement 必须标记来源，不能伪装成系统层。
- Ask 是侧边助手，不是 orchestration 控制面。
- Reader Article Body 走 Plate.js；Plate document 是 projection，不是后端 truth。
- Projection operations 必须使用稳定 domain target，不得把 raw Plate path / raw Slate path ops 作为 durable API contract。
- D4 正式路径不得经过旧 `render_scene_json`；旧投影代码只能作为参考或临时 spike adapter。
- Plate.js 相关包必须保持同一稳定主线；不得混用不同 major 的 `platejs` 与 `@platejs/*`。
- AI / Markdown fragment 必须经过 typed schema、allowlist、length cap、source grounding 和 link protocol policy；LLM 不得直出 arbitrary Plate JSON 作为持久事实。
- `anchor_segment_id` 是新权威锚点；`sentence_id` 仅是兼容 alias。
- Original Input 默认低优先级，只用于审计、恢复或明确授权上下文。
- Daily Reader 不进入 runtime 重构。
- 外部 RAG/OCR/OSS 服务必须通过 adapter 接入，不能成为 Claread 业务事实源。

## Reader Projection 与 Plate Document

Reader Article Body 的渲染与交互走 Plate.js（`platejs/react`），D1-012 决策。本节定义 Plate 与后端 domain truth 之间的投影边界。详细合同见 `modules/plate-reader-projection.md`。

### 关键定位

- **Plate document 不是 truth**，是 `reading_bases` / `reading_units` / `enhancement_layers` / `user_editorial_assets` / `ask_supplements` 的 Web projection。
- **Domain truth 不绑定 Plate**。后端 facts 必须能独立支撑 RAG citation、eval、非 Web 客户端和重新投影。
- **Plate node path 只作前端临时缓存**。持久化合同不得保存 raw Plate path 或 raw Slate path operation。
- **Reader Projection 与 Domain Event 共存**。Domain event 表达业务事实变化；projection event 表达 Web Reader 如何增量更新 Article Body。
- **刷新恢复从 domain truth 重建 Plate snapshot**，不是从 Plate value 反推 domain。

### Plate 覆盖范围

Plate 只覆盖 Reader 内部 Article Body：

| 区域 | 技术边界 |
|---|---|
| Stable Base 原文、Reading Units、Anchor Segments | Plate base nodes / marks |
| Translation、vocabulary、grammar_note、sentence_analysis、Ask Supplement | Plate AI-owned projection nodes / marks |
| 用户高亮、评论、笔记 | Plate user-owned projection nodes / marks |
| Ask sidecar panel、Library、settings、quota、debug、navigation shell | 普通 React UI |

### Owner 与权限

| Owner | 内容 | 用户权限 | 系统/AI 权限 |
|---|---|---|---|
| `stable` | Stable Base、Reading Unit、Anchor Segment source text | 不可编辑、不可删除；可选取、查询、评论、高亮 | 不可改写；如需修正，创建新 record 或 supersede |
| `system_ai` | 系统 worker 生成的 translation、vocabulary、grammar_note、sentence_analysis | 不可直接编辑；可隐藏、折叠、反馈；是否允许 dismiss 由产品策略决定 | 可通过 Layer Publisher 版本化替换 |
| `ask_supplement` | Ask 经用户确认后追加的 AI 补充 | 可删除或撤销显示；保留审计 | 可由 Ask tool 追加或修订 |
| `user` | 用户高亮、评论、笔记、保存的 Ask note/highlight | 可编辑、可删除 | AI 不可覆盖，只能提出建议 |
| `ephemeral` | 选区焦点、Ask citation、临时 suggestion | 当前 session 可关闭 | 不持久化为业务事实 |

Owner 校验双层执行：后端 domain service / Layer Publisher 是权威；前端 Plate plugin 只做 UX 镜像和提前拦截。

### Projection Event 合同

`reader_events` 支持 `event_type = 'projection_ops'`。payload 使用稳定 domain target，不持久化 raw Slate path：

```json
{
  "base_id": "uuid",
  "projection_version": 7,
  "ops": [
    {
      "op_id": "op_...",
      "op_type": "upsert_translation_node",
      "target": {
        "unit_id": "u1",
        "anchor_segment_id": "s3",
        "layer_id": "layer_..."
      },
      "owner": "system_ai",
      "fragment": {
        "format": "plate_fragment",
        "schema_version": 1,
        "content": []
      }
    }
  ],
  "source_event_id": "uuid-of-layer_published",
  "source_layer_id": "uuid-of-enhancement_layer"
}
```

前端 `ProjectionOperationApplier` 通过 `anchor_segment_id` / `unit_id` / `layer_id` 解析当前 Plate path，再生成并执行 Slate/Plate transforms。raw Slate operations 只允许存在于前端内部或短期调试日志，不进入 durable API contract。

`projection_ops.payload.projection_version` 只允许作为 D5+ projection cache / applier 的内部一致性 metadata。D4 snapshot 不暴露 snapshot-level `projection_version`，恢复 cursor 只使用 `last_event_sequence`。

### Snapshot 与 D4 Path

D4 正式路径从新 domain facts 直接生成 Base Plate Snapshot：

```text
Stable Base + Reading Units + Anchor Segments
-> Base Plate Snapshot
-> Plate Reader Surface
```

旧 `renderSceneToPlateDocument` 只能作为迁移参考或 spike adapter，不是 D4 新 contract。D4 可以先用 full snapshot reload 承接 translation layer；D5 再引入 projection operations 的端到端增量应用。

### Anchor Segment ↔ Node Path Adapter

Web projection 需要稳定锚点到 Plate path 的桥接：

- `sentenceIdToPath(editor, sentenceId): Path | null`（兼容旧调用；内部必须映射到 Anchor Segment）
- `anchorSegmentIdToPath(editor, anchorSegmentId): Path | null`
- `pathToAnchorSegment(editor, path): { unit_id, anchor_segment_id, start_offset, end_offset } | null`

缓存可以用 WeakMap；patch apply 后失效重建。所有 domain 回写必须输出 domain anchor，不直接提交 Plate path。

### Ask Document Tools

D5+ Ask Sidecar 使用 document tools，但写入用户资产必须经过用户确认：

| 工具 | 入参 | Domain 写入 | Projection |
|---|---|---|---|
| `read_range` | `{anchor, scope}` | 无（只读） | 无 |
| `propose_highlight` | `{anchor, color, scope}` | 用户确认后写 User Editorial Asset | `projection_ops` |
| `propose_note` | `{anchor, body_markdown, scope}` | 用户确认后写 User Editorial Asset | `projection_ops` |
| `write_ai_supplement` | `{anchor, body_markdown, parent_layer_type}` | 用户确认或授权后写 Ask Supplement | domain event + `projection_ops` |
| `revise_ai_annotation` | `{target, revision_mode, body_markdown}` | 默认提出 System Annotation revision proposal；已发布 Ask Supplement 修订需确认 | domain event + `projection_ops` |

AI 不能直接修改 Stable Base 或 User Editorial Asset。Markdown / Plate fragment 必须经过 sanitize、schema allowlist、anchor validation 和 Authorization Envelope。

### Fragment Sanitize

Plate fragment 只能来自 typed layer result、document tool result 或已 sanitize 的 Markdown fragment。默认策略：

- 后端先用 typed schema 约束 LLM 输出，不接受 arbitrary Plate JSON。
- Markdown -> Plate 前使用 strict allowlist，D5 默认只允许 paragraph、heading、list、code block、inline code、blockquote、strong、em、text 和受控 link。
- Link protocol 只允许 `http:`、`https:`、`mailto:`，并拒绝 localhost、private IP 和 internal host。
- D5 默认禁止 image、table、inline HTML、math、frontmatter、definition 和 footnote。
- 每类 fragment 必须有 length cap 和 source grounding；grammar_note / sentence_analysis / Ask Supplement 必须能回源到 Anchor Segment。

## 决策记录

| ID | 日期 | 决策 |
|---|---|---|
| D0-001 | 2026-06-18 | 本目录是 Reader AI Workflow -> agentic orchestration 重构期间的目标上下文。 |
| D0-002 | 2026-06-18 | 不做旧开发记录迁移；本地数据可重置，但必须保留词典三表。 |
| D0-003 | 2026-06-18 | Web 是第一验证客户端；小程序实现暂缓。 |
| D0-004 | 2026-06-18 | `daily_reader_workflow` 保持固定 workflow，不做 runtime conversion。 |
| D0-005 | 2026-06-18 | 第一阶段 runtime 使用 PostgreSQL-backed job state；外部 MQ/Temporal 不作为默认依赖。 |
| D0-006 | 2026-06-18 | PostgreSQL 拥有业务事实；LLM framework 只作为执行工具。 |
| D0-007 | 2026-06-18 | TMP research 只作为证据库；coding agent 默认使用本目录作为事实源。 |
| D0-008 | 2026-06-18 | RAG、输入适配、orchestration 入口三类 D0.5 边界已进入正式架构。 |
| D0-009 | 2026-06-18 | 测试阶段 RAG 优先接入 Zilliz Cloud；上线前评估阿里云 RAG/向量检索服务；代码必须 adapter 化。 |
| D0-010 | 2026-06-18 | 文件上传测试阶段使用阿里云 OSS；上线目标为 OSS + CDN。 |
| D0-011 | 2026-06-18 | OCR/富文档解析优先评估阿里云百炼图像理解、Qwen OCR/VL 和文档解析能力，但不得绕过 Candidate Base。 |
| D0-012 | 2026-06-18 | 本轮重构收敛为 learning workflow only；academic workflow 暂缓。 |
| D1-001 | 2026-06-18 | 正式设计拆分为概念表、目标总览和模块文档，避免单文档承载全部细节。 |
| D1-002 | 2026-06-18 | 不做旧 `render_scene_json` 兼容映射；Web Reader UI 随新 contract 改写。 |
| D1-003 | 2026-06-18 | D4 最小纵切收窄为纯文本低风险路径和 translation layer。 |
| D1-004 | 2026-06-18 | 新 runtime 不继承每用户单 active task 产品约束；并发由 per-record/per-user/per-worker envelope 控制。 |
| D1-005 | 2026-06-18 | Text anchor 必须复用现有 UTF-16 offsets 和 `fnv1a32-utf16` hash contract。 |
| D1-006 | 2026-06-18 | `reader_events` 只承载 UI domain events；worker diagnostics 不进入 SSE 主流。 |
| D1-007 | 2026-06-18 | D4 Planner 默认 deterministic policy function；LangGraph 不进入 D4 主路径，D5+ 如需 branching / interrupt / complex repair flow 再单独评估。 |
| D1-008 | 2026-06-18 | RAG 默认 shared collection + metadata filter，不默认 collection-per-record。 |
| D1-009 | 2026-06-18 | D4 不使用 LLM Planner；Policy Planner 为 deterministic code，Semantic Reviewer 作为 D5+ typed LLM worker。 |
| D1-010 | 2026-06-18 | 模型选择走 Model Profile / route lookup，不由 runtime planner 即兴选择模型。 |
| D1-011 | 2026-06-18 | D2 前必须定义 Skip Gate、Prompt Cache、Usage Bucket 和成本基线合同。 |
| D1-012 | 2026-06-18 | Reader 渲染层走 Plate.js（`platejs/react`），作为 long-lived Article Body 文档模型、渲染层和交互引擎。`apps/web/src/lib/reader-plate/` 是 Claread 对 Plate.js projection 的领域封装目录；阅读任务必须以 Plate.js 为实现底座。 |
| D1-013 | 2026-06-18 | Plate document 不是 truth，是 domain truth（Stable Reading Base / Reading Units / Anchor Segments / Enhancement Layers / User Editorial Assets / Ask Supplements）的 projection。`enhancement_layers` / User Editorial Asset 表不改为 patch sequence；刷新恢复从 domain truth 重建 Plate snapshot，不从 Plate value 反推 domain。 |
| D1-014 | 2026-06-18 | `reader_events.event_type` 新增 `projection_ops` 子类型，与 domain events 并存。Projection ops 使用稳定 domain target（unit、Anchor Segment、layer、asset），不持久化 raw Slate path ops；前端再把 ops 转成 Plate transforms。非 Web 客户端继续 polling snapshot。 |
| D1-015 | 2026-06-18 | Ask Sidecar 在 D5+ 改 document tools 模式：`read_range`、`propose_highlight`、`propose_note`、`write_ai_supplement`、`revise_ai_annotation`。写 User Editorial Assets 必须用户确认；每个工具经 Authorization Envelope、anchor validation 和 owner policy 校验后落 domain fact，再 emit projection ops。 |
| D1-016 | 2026-06-18 | D4 正式路径从 Stable Base / Reading Units / Anchor Segments 直接生成 Base Plate Snapshot，不经过旧 `render_scene_json`。`renderSceneToPlateDocument` 只能作为迁移参考或 spike adapter，不作为新 contract 扩展。 |
| D1-017 | 2026-06-18 | Plate owner 权限层覆盖 `stable`、`system_ai`、`ask_supplement`、`user`、`ephemeral`。用户不能删除 system AI truth，只能隐藏/反馈/按策略 dismiss；Ask Supplement 和 User Editorial Assets 有独立生命周期。owner 校验双层：后端权威拒绝 + 前端 Plate UX 镜像。 |
| D1-018 | 2026-06-18 | D2-P0 接受 Plate.js 作为 Article Body 底座；Web 依赖必须对齐到同一稳定 major 主线。当前验证通过的组合是 `platejs@53.2.1`、`@platejs/floating@53.0.0`、`@platejs/ai@53.2.2`、`@platejs/markdown@53.2.2`、`@platejs/suggestion@53.0.3`、`@platejs/selection@53.1.6`。不得再混用 `platejs@50` 与 `@platejs/*@53`。 |
| D1-019 | 2026-06-18 | Plate Markdown / AI fragment 必须经过 typed schema、strict allowlist、length cap、source grounding 和 link protocol allowlist。D5 默认禁 image、table、inline HTML、math、frontmatter、definition 和 footnote；LLM 不得直出 arbitrary Plate JSON 或 raw Slate ops 作为持久事实。 |
| D2-001 | 2026-06-18 | D2-S1 接受 Reading Unit Builder 方向，但新增 Anchor Segment；unit 使用 Stable Base absolute offsets。D5-V2 实现把 span anchor 固化为 `anchor_segment_id` + unit-local offsets，并用 Anchor Segment range 约束。 |
| D2-002 | 2026-06-18 | Stable Base 被视为输入适配后的可读英文正文；低影响处理可直接冻结，高影响处理必须 Candidate Base preview/confirm 后冻结。 |
| D2-003 | 2026-06-18 | Anchor Segment 从严格句子级修订为 sentence-like segment，必须记录 `segment_type = sentence | clause | fallback_window`；LLM 只能作为 D5+ 边界改良器提供受约束建议。 |
| D2-004 | 2026-06-18 | D2-S2 接受 PostgreSQL-backed job lease / publish guard：`reader_jobs` claim 走 `SELECT FOR UPDATE SKIP LOCKED`，`lease_token` 必须是 UUID，`lease_expires_at` 是 per-job absolute timestamp。 |
| D2-005 | 2026-06-18 | D4 最小纵切必须包含最小 `reader_runs` 与 immutable `envelope_json` snapshot；完整 envelope schema、runtime counters 和 DLQ 可后置，但 run/generation/envelope 不能后置到 D5。 |
| D2-006 | 2026-06-18 | `reader_events.sequence` 使用 record-scoped transactional counter 或等价机制，不使用 PostgreSQL global sequence 作为 UI catch-up sequence；sequence 从 `1` 开始并仅对 committed UI events 连续。 |
| D2-007 | 2026-06-18 | D4 snapshot 默认实时聚合，不实现 `reader_snapshots` cache、PG LISTEN/NOTIFY fan-out 或 event TTL；这些属于 D5+ 性能优化。 |
| D2-008 | 2026-06-18 | D4 不要求 `projection_ops` 端到端。D3 可以建立 projection event/schema 骨架；D4 translation layer 可先通过 snapshot reload 或 simple projection refresh 呈现；D5 再启用增量 applier。 |
| D2-009 | 2026-06-18 | D2-S3 接受 deterministic Policy Planner / Skip Gate。D4 不使用 LLM Planner 或 LangGraph Planner；Semantic Reviewer 是 D5+ typed worker。 |
| D2-010 | 2026-06-18 | D2-S4 接受 Unit 级 translation worker + Anchor Segment 级结构化译文；worker 不携带 Ask history、用户资产、planner trace 或整篇长文。 |
| D2-011 | 2026-06-18 | D2-S6/S7 接受 deterministic model route -> profile -> fallback chain；`operation_fingerprint` 表示 business intent，不包含临时 fallback actual provider/model。Prompt cache 是优化和观测信号，不是正确性依赖。 |
| D2-012 | 2026-06-18 | 旧 workflow dependency matrix 作为 D3 cutover guard：不迁移旧 `render_scene_json`，但删除旧 learning workflow 前必须保护词典、Daily Reader、Ask、用户资产、词汇本、收藏、usage/ledger、feedback 和 Directus/Eval 观察面。 |
| D2-013 | 2026-06-18 | D3-P0 后端依赖基线已执行：PydanticAI 1.107.0、DashScope SDK 1.25.23、asyncpg 0.31.0；LangGraph/FastAPI/LangSmith/OpenAI 保持当前锁定版本。D4 worker 实现不得临时升级核心 LLM/runtime 包。 |
| D2-014 | 2026-06-18 | LangGraph 1.x 对 D5+ 复杂 repair / branching / interrupt 有评估价值，但不进入 D4 主路径。D4 继续使用 PostgreSQL run/job/event 作为 durable business state，PydanticAI 只作为 typed worker 执行层。 |
| D2-015 | 2026-06-18 | 旧 `grammar_node` 的正式新口径是 Grammar Bundle Worker：worker 可一次生成 `grammar_note` 与 `sentence_analysis`，但 layer subtype 在存储、发布、RAG、projection、eval 和 policy 中必须独立。`long_sentence` 不是权威 layer type，只是触发 `sentence_analysis` 的适用场景。 |
| D3-001 | 2026-06-18 | D3 Schema / Domain Contract 采用 `reading_records`、`reading_bases`、`reading_units`、`anchor_segments`、`reader_runs`、`reader_jobs`、`reader_events`、`reader_event_sequences`、`reader_job_events`、`enhancement_layers`、`parsed_decisions` 作为最小 D4 后端事实源。 |
| D3-002 | 2026-06-19 | 开发期核心类型和 DTO 不加 `V1` / `V2` 后缀。使用 `ReaderPlateSnapshot`，不使用 `ReaderPlateSnapshotV1` 或 `ReaderPlateSnapshotV2`；snapshot wrapper 使用 `schema_kind = "reader_plate_snapshot"`，`schema_version` 只允许在 layer output、fragment 等 serialized boundary payload 中出现。 |
| D3-003 | 2026-06-19 | D3 contract review 接受后回写：base-scoped `reader_jobs` 必须携带 `base_id`，job retry 状态统一为 `retry_later`，snapshot cursor 只用 `last_event_sequence`，vocabulary layer 保留 `vocab_highlight` / `phrase_gloss` / `context_gloss` item subtype，旧 workflow reset 必须按 protected manifest 执行。 |
| D3-004 | 2026-06-19 | D3-P1 schema baseline 已通过 review：`reader_jobs` 与 `enhancement_layers` 均通过 base/generation 复合 FK 防止 stale generation 写入；只有 `build_base + record` job 可无 `base_id`；`active_base_id -> reading_bases.status='active'` 暂作为 service/publisher invariant，不在 D3-P1 加 trigger。 |
| D3-005 | 2026-06-19 | D3-P2 Reading Base Builder + Base Plate Snapshot 已通过 review：低影响纯文本 builder 使用 deterministic canonicalization、UTF-16 offsets、`fnv1a32-utf16` hash、sentence/clause/fallback Anchor Segment；当前 Unit baseline 是 `1 structure block -> 1 reading unit`。`ReaderPlateSnapshot` 从 domain facts 生成并校验所有 layers/assets/supplements/parsed facts 属于当前 base / unit / anchor；最小 translation projection 可用，但不代表通用 `projection_ops` 已端到端接入。 |
| D3-006 | 2026-06-20 | D3-P3 Article Ready Persistence Service 已通过 review：低风险纯文本提交在一个事务内写入 record/input/base/units/anchors/active base/`article_ready` event；snapshot reload 从 DB facts 重建，并使用 read-only `repeatable_read` transaction 保证 `last_event_sequence` 与 facts 来自同一 consistent read；DB hydration 后必须调用 `validate_reading_base_build_result` 统一校验 Reading Base / Unit / Anchor Segment 全局 invariant。 |
| D3-007 | 2026-06-20 | D3-P4 Runtime Skeleton 已通过 review：job runtime 支持 SKIP LOCKED claim、lease token、heartbeat、retry_later、stale recovery 和 transition guard；claim/publish fence 必须校验 target base 是 record 当前 `active_base_id`；event runtime 支持事务内 record-scoped sequence、rollback no-gap、concurrent publish、polling cursor、empty stream、cursor caught-up、gap reload 和 `Last-Event-ID` parser。D3-P4 不调用 LLM，不引入 LangGraph。 |
| D4-001 | 2026-06-20 | D4-P0 Backend Reader API + Snapshot/Polling 已通过 review：`POST /reader/records/plain-text`、`GET /reader/records/{record_id}/snapshot` 和 `GET /reader/records/{record_id}/events` 复用 D3-P3/D3-P4 services；用户隔离走 `AuthUserDep`；blank `client_record_id` 规范化为 `NULL`，重复 active `client_record_id` 返回 409；新 API 不读取旧 `render_scene_json`。 |
| D4-002 | 2026-06-21 | D4-P1 Translation Layer Worker + Layer Publish 已通过 review：translation run/job bootstrap、PydanticAI typed output boundary、layer publisher、`layer_published` event、snapshot translation projection 和 `ai_usage_events` attribution 已形成最小纵切；worker claim 必须按 `job_type='translate_unit'` / `target_type='unit'` 过滤，retry 后成功必须清空 run failure fields。 |
| D4-003 | 2026-06-21 | D4 backend orchestration、worker runner hardening 和 Web read-only smoke 已通过 review：`ReaderOrchestrator` 串联 article-ready、translation bootstrap、tick、layer publish 和最小 parsed decision；`TranslationWorkerRunner` 是内部 callable runner，不是 public HTTP endpoint；Web reader-plate smoke 只证明浏览器渲染/交互，不等价于真实 auth/backend E2E。 |
| D5-001 | 2026-06-21 | D5-V1 Vocabulary Layer Backend Slice 已通过 review：`vocab_highlight`、`phrase_gloss`、`context_gloss` 是同一 `vocabulary` layer 的 item subtype；vocabulary job 使用正式 `reader_jobs.job_type = 'build_vocabulary_layer'`，不得挪用 `build_base`；worker 默认未配置时失败且不发布空 layer，只有显式 fake executor 可发布空 output。 |
| D5-002 | 2026-06-21 | D5-V2 Vocabulary Projection / Web Read-only Rendering 已通过 review：published `vocabulary` layer 从 domain facts 重建为 stable source leaf 上的 `reader_vocabulary_marks`，Web 只读展示三类 item；不持久化 Plate path/op，不读取旧 `render_scene_json`，仍通过 snapshot reload 承接，不启用 `projection_ops` incremental applier。 |

## 待决问题

- `article_ready` p50/p95 目标。
- Length Class 数值边界和默认 Authorization Envelope 预算。
- 第一版 worker 是仅开发期 in-process，还是一开始独立 worker process。
- 最终 DDL 表名、索引和 schema reset 程序。
- Candidate Reading Base Web 编辑器的最小形态。
- RAG 第一版 provider 和 adapter 实现。
- OCR 第一版是否只支持图片，还是同时支持 PDF 富文档。
- Parsed Decision 的首批 eval dataset 和验收 rubric。
