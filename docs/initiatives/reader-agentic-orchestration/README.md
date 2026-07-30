# Reader Agentic Orchestration 重构专项

> 状态：`进行中专项（Adaptive Reader Orchestration 分阶段落地）`
> 最后更新：2026-07-13（DOC-R3 归档与 TMP 清理：17 文件归档至 `archive/`，5 TMP 删除；DOC-R2 文档收敛已完成）
> 权威性：本目录是 Reader AI Workflow -> agentic orchestration 重构期间的专项事实源。

本目录用于管理 Reader agentic orchestration 重构的目标架构、阶段计划和 coding agent 上下文。它与当前稳定产品/架构文档分开，因为当前系统处于旧 AI Workflow 与新 Reader orchestration 双线并存、验证和切换阶段，而本目录描述本轮重构的目标状态与过渡设计。

## 范围

本轮包含：

- 用户提交内容的 `learning` 解析。
- 先用 Web Reader 做验证。
- 新 Reader 所需的后端 schema、API、orchestration runtime、worker、event log、usage audit 和 eval hooks。
- Web Reader Article Body 的 Plate.js projection、owner 权限和渐进式渲染合同。
- 当前记录内的 RAG substrate 接入边界。
- 文本、URL、PDF、OCR、文件等输入模式的统一适配边界。

本轮不包含：

- `daily_reader_workflow` 的 runtime 重构。
- `academic workflow` 的 agentic orchestration 重构；待 learning workflow 验证稳定后再单独设计。
- 第一验证阶段的小程序实现。
- 旧开发记录的数据迁移。
- 旧 `render_scene_json` 的兼容映射。
- 全局 User Editorial Assets RAG、跨记录知识库化、自动迁移用户编辑资产。

## 当前前提

- Claread 仍处于开发阶段，尚未上线生产用户数据。
- 本地数据库数据可以在重构中清空，但必须保留词典三表：
  - `dict_entries`
  - `dict_lookup_targets`
  - `dict_redirects`
- 数据库 baseline 可以按目标架构重塑，不需要背负旧开发记录迁移复杂度。
- Web 是第一验证客户端；小程序后续基于稳定后的 Web / API contract 做降级适配。
- `daily_reader_workflow` 继续保持固定 workflow。
- 重构期间解析功能可以暂时不可用；不需要维持旧 Reader UI / 旧 `render_scene_json` contract 可用。

## 当前外部服务假设

这些是 D0.5 初步选型，不是最终不可变承诺：

- RAG 向量库：测试阶段优先使用当前已配置的 Zilliz Cloud；后续上线前评估迁移到阿里云 RAG / 向量检索服务。
- RAG 应用层：优先保持 Claread 自有 RAG contract，不直接把百炼知识库当业务事实源；百炼知识库可作为后续托管 RAG 候选。
- OCR / 文档解析：优先评估阿里云百炼的图像理解、Qwen OCR / VL、文档解析能力。
- 文件上传：测试阶段可使用阿里云 OSS；上线目标为阿里云 OSS + CDN。
- 当前开发测试 OSS bucket 已开通：`claread-dev`，endpoint 为 `https://oss-cn-shenzhen.aliyuncs.com`；具体本地环境变量口径见 `docs/operations/local-dev.md`。

## 权威文档

本专项只认以下文档（按阅读优先级排列）：

1. `README.md`（本文件）：专项入口、权威文档索引、模块表、文档治理与生命周期规则。新会话第一站。
2. `agent-brief.md`：发给 coding agent 的最小上下文、必读顺序、不可违反决策、当前阶段检查点。**新 agent 会话按此顺序阅读。**
3. `target-architecture.md`：目标产品形态、范围边界、核心模块、硬约束、决策记录。**架构权威，不写模块细节。**
4. `concepts.md`：术语、概念定义和统一口径。**术语权威。**
5. `adaptive-reader-orchestration-design.md`：Reader 自适应解析策略、分析窗口、渐进发布、长文/超长文处理的当前设计入口。**自适应解析设计权威。**
6. `modules/*.md`：D1 模块合同与 runbook。每份 module 文档是自身领域的详细事实源；架构总览见 `target-architecture.md`。
7. `implementation-plan.md`：阶段计划、门禁、任务包和验收标准。**任务状态权威。**
8. `spikes/README.md`：D2 spikes 启动清单和执行顺序。

### 入口职责切分

| 文档 | 负责 | 不负责 |
|---|---|---|
| `README.md` | 专项入口、权威文档索引、模块表、文档治理规则 | 模块细节、任务状态、术语定义 |
| `agent-brief.md` | 必读顺序、不可违反决策（高层）、当前阶段检查点 | review fix 逐项细节、测试计数明细、验收报告全文 |
| `target-architecture.md` | 目标产品形态、范围边界、核心模块表、硬约束、决策记录、Plate Projection 总览 | 模块级详细合同（归 `modules/`） |
| `concepts.md` | 术语统一口径、生命周期、代码映射、易混淆对比 | 架构决策记录、任务状态 |
| `adaptive-reader-orchestration-design.md` | 自适应解析策略、Analysis Window、渐进发布、长文/超长文处理 | 模块级 schema、任务进度 |
| `modules/*.md` | 各模块详细合同、runbook、代码接入矩阵 | 跨模块架构总览、任务计划 |
| `implementation-plan.md` | 任务 ID、依赖、状态、验收口径、门禁 | review fix 细节、coding agent prompt、验收报告全文 |
| `spikes/README.md` | D2 spike 启动清单、执行顺序、verdict | spike 详细输出（归 `spikes/*.md`） |

## 模块文档

| 文档 | 内容 | 何时需要阅读 |
|---|---|---|
| `modules/input-adapter.md` | 输入适配、Source Artifact、Extraction Result、Candidate Document、Input Suitability Gate | 处理文本/URL/PDF/OCR/文件上传输入链路 |
| `modules/schema-and-domain-contract.md` | D3 schema 边界、domain contract、运行时表、Plate snapshot DTO、reset/cutover 约束 | 修改 reader schema、DB 表、DTO 或 reset 脚本 |
| `modules/reading-base-and-units.md` | Stable Reading Document、Stable Document Blocks、Canonical Text Layer、Reading Units、Anchor Segments、UTF-16/hash、`article_ready` gate | 修改文档冻结、unit/anchor 切分或 article_ready 流程 |
| `modules/orchestration-runtime.md` | run/job、worker lease、并发、Authorization Envelope、Publish Fence、ExecutionBudget、reader_runtime_spans | 修改 worker runtime、job claim/publish、预算或 observability span |
| `modules/policy-and-cost-control.md` | Policy Planner、Skip Gate、Model Profile、Prompt Cache、Usage Bucket、ExecutionBudget | 修改路由策略、成本控制、model profile 或 usage 归因 |
| `modules/enhancement-layers-and-parsed.md` | Enhancement Layer schema、anchor、Parsed Decision、Translation Group 合同 | 修改 translation/vocabulary/grammar/sentence_analysis layer 发布合同 |
| `modules/streaming-and-projection.md` | Reader Events、snapshot、SSE、polling fallback、projection_ops envelope | 修改事件序列、snapshot 重建、SSE 或 polling |
| `modules/representation-event-contract.md` | 会改变 snapshot 表示的 User Asset / Ask Supplement / record metadata 写入与事务事件合同 | 修改 user asset、Ask supplement、display-title 状态、representation event payload 或 freshness |
| `modules/plate-reader-projection.md` | Plate.js Article Body、projection operations、document tools、owner 权限表、anchor bridge | 修改 Plate projection 合同、owner 权限或 projection_ops envelope |
| `modules/ask-claread-reader-workspace.md` | Ask Claread Reader sidecar/floating workspace、outline coexistence、surface state | 修改 Reader Plate Ask workspace、响应式布局或无障碍交互 |
| `modules/rag-substrate.md` | record-scoped RAG、block-scoped citation DTO、provider adapter | 修改 Article RAG substrate、citation 或 vector provider |
| `modules/cutover-and-old-workflow.md` | 停服重构、旧 workflow 移除、旧依赖审计、Web 路由矩阵 | 评估旧代码删除、cutover 阶段或依赖审计 |
| `modules/ask-claread-reader-workspace.md` | Ask Claread Reader Workspace v2 设计规范：sidecar/floating 布局、`requestedSurface`/`effectiveSurface`、组件边界、Phase 1/2 切分 | 修改 Ask 布局、surface 切换、outline 与 Ask 共存或 Phase 2 resize |
| `modules/frontend-integration-contract.md` | 前端可集成的 HTTP route、DTO 字段、polling 建议、truth source 规则 | 修改 BFF route、DTO 字段或前端可消费的 API surface |
| `modules/frontend-integration-status-map.md` | 前端用户可见 status / reason_code 闭枚举、coercion contract、polling 建议 | 修改 status 枚举、reason_code 映射或前端 fail-soft 行为 |
| `modules/reader-plate-component-integration.md` | Web Reader Plate.js 真实接入矩阵：package 状态、plugin kit、组件落点、当前弱接入风险 | 修改 Plate plugin、reader block/leaf 组件或评估 package 接入状态 |
| `modules/reader-record-plate-surface-ui.md` | `/app/reader-record/{recordId}` UI/UX 目标方案：模式、marks/cues、selection toolbar、用户资产、Ask context、移动端 action sheet | 修改 Reader Record 页面 UI/UX、marks 视觉、selection 行为或移动端交互 |
| `modules/local-real-chain-runbook.md` | D5/D6 本地真实链路 runbook：三进程启动、model profile env、worker CLI、DB 诊断、fail-closed 行为 | 本地实跑 reader enhancement 主链路或排障 |
| `modules/local-article-rag-runbook.md` | Article RAG 本地运维 runbook：配置、worker 启动、lifecycle status、失败码、真实 smoke gate | 本地实跑 Article RAG 或排障 |
| `modules/semantic-automatic-layer-policy.md` | Semantic role 分类、automatic T/V/G/S 产品矩阵、off/shadow/enforce 冻结 mode、job fence 与 USER_EXPLICIT section 身份 | 修改 automatic bootstrap 过滤、worker fence、aside/blockquote 语义策略 |

### 前端两文件边界切分

`modules/frontend-integration-contract.md` 与 `modules/frontend-integration-status-map.md` 职责切分：

- **contract** = HTTP route、request/response 字段、DTO shape、polling 建议、truth source 规则。
- **status-map** = 前端用户可见 `status` / `outcome` / `next_action` / `reason_code` 闭枚举、coercion contract、fail-soft 行为。
- 两文件均有 polling 建议：contract 侧重"多久 poll 一次"，status-map 侧重"poll 到未知值如何 coerce"。

`tmp/` 下的研究、验收、诊断材料只作为过程证据库。除非任务明确要求回看某份研究报告，否则 coding agent 不应默认读取 TMP 研究文档。2026-07-07 之后，自适应解析相关结论以 `adaptive-reader-orchestration-design.md` 为准；任务状态、验收结果和下一步实现顺序以 `implementation-plan.md` 为准。

## 文档治理规则

- 长期设计入口应尽量收敛；如确需新增模块外设计文档，必须在本 README 的权威文档列表登记，并说明与 `target-architecture.md` / `modules/` 的关系。
- 新决策写入 `target-architecture.md` 的决策记录。
- 进度和阶段状态写入 `implementation-plan.md`，不要写进研究报告。
- `implementation-plan.md` 只记录任务、依赖、状态和验收口径；coding agent prompt、长验收报告和过程讨论不写入长期计划文档。
- coding agent 默认从 `agent-brief.md` 开始，不从 TMP 文档堆里找上下文。
- 每轮 coding agent prompt 由人工在会话中单独给出。若 prompt 中出现新的长期约束，应在评审后压缩成正式文档条目，而不是原文粘贴。
- 如果实现发现目标架构与代码事实冲突，先暂停并记录冲突，不要自行发明新架构。
- 新增 `modules/*.md` 必须同步登记到上方模块表，否则视为未注册；未登记的模块文档不被视为正式事实源。
- 正式事实只进入 canonical docs（本目录权威文档与 `modules/`）。任务过程材料只能进 `tmp/`。
- 新任务不得复制已有事实表或权限表，必须链接到权威来源（如 owner 权限表归 `modules/plate-reader-projection.md`，projection envelope 归 `modules/streaming-and-projection.md`，Plate Plugin 接入矩阵归 `modules/reader-plate-component-integration.md`）。

## 文档生命周期规则

本节定义本专项文档从产生到消亡的生命周期规则，防止文档膨胀与事实漂移。

### 事实归属

| 类型 | 归宿 | 示例 |
|---|---|---|
| 长期架构事实 | `target-architecture.md` 决策记录 + `concepts.md` 术语 | D1-012 Plate.js 决策、`anchor_segment_id` 权威 |
| 模块合同事实 | 对应 `modules/*.md` | owner 权限表、projection_ops envelope、schema DDL |
| 自适应解析设计 | `adaptive-reader-orchestration-design.md` | Analysis Window、三态路由、渐进发布 |
| 任务状态与验收 | `implementation-plan.md` | T4.2a-V1 closed、Sample A UNRESOLVED |
| Agent 必读与不可违反决策 | `agent-brief.md` | 必读顺序、durable ExecutionBudget 决策 |
| 任务过程材料 | `docs/tmp/reader-orchestration/` 或 `docs/initiatives/reader-agentic-orchestration/tmp/` | review fix 细节、验收报告全文、诊断快照 |

### TMP 生命周期

1. **产生**：一个任务最多产生 1 份 TMP（含 audit/diagnostic/tracker/research）。若需多份过程文档（如多轮 review），任务完成后立即综合为 1 份 synthesis。
2. **存续**：TMP 是过程证据库，不作为长期事实来源。coding agent 不默认读取 TMP。
3. **闭合**：任务在 `implementation-plan.md` 标记为 closed/accepted 后，立即把结论压缩回正式文档。
4. **删除候选**：TMP 满足三条件后进入 DOC-R3 删除候选：(a) TMP 自述结论已压缩进正式文档；(b) 任务已闭合；(c) 无独特未迁移事实。
5. **归档候选**：设计/评审周期已完成的 TMP（review→revise→closeout）可标注 `ARCHIVED` 保留作历史证据；6 个月无回溯需求后进入 DOC-R3 删除候选。
6. **最长保留期**：TMP 自任务闭合起最长保留 14 天；超期未迁移的 TMP 需在下次 doc 清理任务中强制处理。

### 重复内容收敛规则

- 每个事实只有一个权威归宿（owner 文档）。其他文档引用时只写简短约束 + 链接，不复制完整表/DDL/envelope。
- 当前已确认的 owner 分工：
  - owner 权限表 → `modules/plate-reader-projection.md`（`target-architecture.md` 只保留总览 + 链接）
  - projection_ops envelope → `modules/streaming-and-projection.md`（`target-architecture.md` 与 `modules/plate-reader-projection.md` 引用）
  - Plate Plugin 接入矩阵 → `modules/reader-plate-component-integration.md`（`modules/orchestration-runtime.md` 引用）
  - schema DDL → `modules/schema-and-domain-contract.md`
  - 术语定义 → `concepts.md`
  - 决策记录 → `target-architecture.md`
- 修改事实时只改 owner 文档；引用文档不需要同步修改，但应在 owner 文档顶部注明最后更新日期。

### DOC-R3 归档与 TMP 清理（2026-07-13 已执行）

DOC-R3 已完成物理归档与 TMP 清理：

- **17 文件归档**至 [`archive/`](archive/README.md)：Document Graph 链（8）、Translation V2 链（5）、研究材料（2）、Spike 结果（1）、外部来源研究（1）。归档索引和权威事实归宿见 [`archive/README.md`](archive/README.md)。
- **5 TMP 删除**（`initiatives/tmp/` 下，git-ignored）：three-mode-adaptive-reader-research、TMP-t4.2a-pux-r3-test-health、TMP-t42a-pux-r1-progressive-transition、TMP-t42a-pux-r2-runtime-integration、TMP-t42a-v2-r1-boundary-samples。结论已压缩进 `implementation-plan.md` / `agent-brief.md` / `adaptive-reader-orchestration-design.md`。
- **未处理**：LP-R1/LP-R2 TMP（LP-R3.1 未关闭前不处理其报告或相关最新 profiling 文档）；backend-closure verdict 冲突两份 review（仍未裁定，不得删除/归档/改写）。
- **保留**：`docs/tmp/reader-orchestration/` 下 8 个活跃 TMP（含未迁移独特事实或活跃任务）+ `tmp/README.md` 治理指针。

## 与稳定文档的关系

`docs/product/current-state.md`、`docs/development/mainline.md` 和既有 `docs/architecture/*` 描述当前已落地基线或稳定子系统。本目录描述重构期间的目标状态。

某个阶段稳定落地后，再把已经成为现实的事实压回稳定文档，并删除或归档过期专项内容。
