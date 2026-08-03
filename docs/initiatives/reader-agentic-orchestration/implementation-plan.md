# Reader Agentic Orchestration 实施计划

> 状态：`Architectural Cutover Complete（Reader/Ask 主链已单轨化，旧生产链已物理删除）；Operational Readiness 与 Test Governance 为 post-cutover backlog`
> 最后更新：2026-08-03（DOC-TRUTH-LIFECYCLE-R2：从实施工作区收缩为稳定架构包，删除 D0-D6 流水账与已不存在 TMP 的引用，保留 cutover 总结、关键遗留风险与 post-cutover 路线）

## 当前实施口径

本文是 Reader agentic orchestration 专项的**post-cutover 维护计划**。Architectural Cutover 已完成；本文件不再维护 D0-D6 阶段流水账与每轮 coding agent 任务详情。新会话从 [`README.md`](README.md) 与 [`agent-brief.md`](agent-brief.md) 入口；架构与模块合同归 `target-architecture.md` / `concepts.md` / `adaptive-reader-orchestration-design.md` / `modules/*.md`。

`tmp/` 下的研究、验收和诊断材料只作为历史证据来源；临时开发代号不进入长期文档、代码注释、数据库命名或对外沟通口径。本计划不引用任何已删除 TMP 文件；过程证据由 git history 承担。

## Cutover 总结

Architectural Cutover 已完成，Reader 与 Ask 主链已单轨化：

- 旧 Learning Workflow（`learning_workflow.py` 固定全量 graph）、Analysis service 写入路径、Ask legacy lane、旧 Web Reader 产品页实现（`ReaderWorkbench` / `ReaderRecordWorkbenchSurface` / `ReaderPlateSnapshotSurface`）、旧 Directus Eval Center / Workflow Lab / Node Lab / Render Scene Inspector 均已物理删除。
- 新链事实源：Reading Record、Stable Document、Reading Units、Anchor Segments、Enhancement Layers、`reader_events`。
- 新 Web 入口：`/app/read`（提交）、`/app/reader/[recordId]`（产品页）、`/api/web/reader/records/*`（BFF）。
- 新 Directus 控制面：`endpoints-bundle/src/reader-orch/` 提供 4 个只读 JSON endpoint（`trace` / `run` / `record-summary` / `dashboard`），无 Console heatmap / span-tree UI。
- 旧 `analysis_*` 表分三类：4 张 legacy 孤儿表（待 DATA-AUDIT DROP）、3 张 legacy 仍被只读引用表（需先迁移引用）、2 张新链在用表（`analysis_windows` 与 `layer_analysis_plans`，保护）。

详细 cutover 落地结论、表分类、保留合同与历史过程证据见 [`modules/cutover-and-old-workflow.md`](modules/cutover-and-old-workflow.md)。

## Post-cutover backlog（关键遗留风险与未决事项）

以下事项已登记为 post-cutover backlog，由后续专项任务单独推进；不在本计划写成已完成，也不在本计划展开任务细节：

### 数据与依赖清理

- **DATA-AUDIT**：4 张 legacy 孤儿表（`analysis_debug_snapshots`、`analysis_task_events`、`analysis_overview_tasks`、`analysis_overview_task_events`）可直接 DROP；3 张 legacy 仍被只读引用表（`analysis_records`、`analysis_results`、`analysis_tasks`）必须先迁移 `services/api/app/services/user_assets/records.py`、`text_anchors.py`、`quota/ledger.py` 中的引用；`analysis_windows` 与 `layer_analysis_plans` 必须保护。禁止使用「删除 analysis_*」这类 wildcard 指令。
- 旧 Eval 表与 Directus legacy module metadata 清理。
- **TEST-GOVERNANCE**：测试体系治理盘点与迁移设计（输入材料 `docs/tmp/reader-orchestration/TMP-test-governance-audit-2026-07-23.md`）。
- **ARCH-OPT-AUDIT**：代码架构优化审计。

### Console / Eval / Observability 重建

- Console / Eval 按新 orchestration 重建（治理化控制面，非临时 heatmap / span-tree UI）。
- 统一监测、计费适配、usage/ledger 与新 Reader run/job/layer attribution 闭环。
- **T4.2a-O3 遗留**：Sample A grammar 0-token usage attribution 仍 **UNRESOLVED**；Cost/Latency Baseline 仍 **PARTIAL**（无可靠 provider 账单、无同样本旧链路真实 LLM 对照、per-job provider latency 不完整）。后续可评估 cache token normalization、versioned price snapshot / estimated cost 或 Progressive UX fixture；不得宣称 token / 成本 / 时延已可靠或 Sample A 已修复。

### Adaptive Reader 后续切片

- **T5.4 / T5.5 outline projection / UI**：T5.3 semantic outline durable layer 已闭合（默认 eligibility=false，未挂 `ReaderPlateSnapshot`）；T5.4-R0 snapshot projection 只读设计门已闭合；T5.4a snapshot projection、T5.4b 文档硬化、T5.5a L2 Reader UI、T5.6a/b/c section identity 与「解析此段」lane 均已闭合。后续 outline 真实 LLM 质量与成本未做样本验收；不得把「T5.3/T5.4a/T5.5a closed」表述为「生产默认会生成内容大纲」。真实 LLM blocker：仓库尚无 outline `MODEL_ROUTE` 与 prompt agent（T5.8a 已注册形状，默认关闭；T5.8b/c/d 已实施 dev-activation 与 opt-in real-LLM smoke，未放开产品默认启用）。
- **T4.2 bounded LLM document profiler**：继续暂缓。只有 deterministic router 在真实边界样本上出现稳定误判时才重新评估；不得因为已有真实 baseline 就直接引入自由决策 LLM。
- **T4.2a-PUX-R4-R3-R2**：selective grammar expansion cleanup + scroll-anchor compensation。前置 R3-R1 已闭合（commit `9a925f82`：source-identity reset + re-anchor）。
- **长内容交付与渐进阅读 UX**：T4.2a-O4-R1 已接受 representation event contract；O4-R2 transactional atomic slices A+B+C 与 Web payload-aware reader event classifier 已完成；PUX-R4 interaction-stable incremental projection、semantic fragment transport、SSE 通知通道仍未实施；snapshot HTTP schema、ETag、304、压缩、fragment route、JSON Patch、WebSocket 均未改动。LP-R4 已以结论 B 闭合：`snapshot_id` 不可复用为 HTTP ETag。
- **M6 streaming UX**：SSE / patch delivery 逐步替代高频全量 reload。SSE 只作为带 sequence / generation / target 的通知通道，不能承载整份 snapshot，也不能替代局部 Plate projection；不得预先实现 ETag、304、压缩、SSE、fragment route、JSON Patch 或 WebSocket。

## Post-cutover 路线

按以下顺序推进，每轮先完成可验证闭环，再进入下一轮架构扩展：

1. **短文 / 长文 / 超长文三模式合同闭环**：T1.1a、T3.1、T3.2b、T3.3、T3.4a、T3.4b、T3.5、T4.1 / T4.1a / T4.1b / T4.1c、T4.2a-R1 / R2 / V1 / V2-R1、T4.2a-O1 / O2-V1-R1 / O3、T4.2a-PUX-R1 / R2 / R3 / R4-R3-R1 均已完成代码级实施与 deterministic acceptance；T4.2a-V1 已正式关闭。后续不重跑已闭合真实 LLM 样本；中间阶段优先使用 deterministic tests、fake executor、recorded LLM response 和 DB contract checks。
2. **导航与 outline 持续推进**：T5.1 L0/L1 deterministic navigation 已闭合；T5.2a / T5.3 semantic outline durable 已闭合；T5.4a / T5.4b / T5.5a / T5.6a / T5.6b / T5.6c / T5.7 已闭合；T5.8a / T5.8b / T5.8c / T5.8d-dev-activation 已实施 dev-activation + opt-in real-LLM smoke。下一可选项：outline 真实 LLM 质量与成本样本验收、产品级 eligibility 阈值冻结、L2 首次生成入口与成本上限。
3. **Progressive UX 增量投影**：T4.2a-PUX-R4-R3-R2（selective grammar expansion cleanup + scroll-anchor compensation）→ 才评估 PUX-R4 interaction-stable incremental projection → 才评估 semantic fragment transport → 最后才评估 SSE 替换可见页 event polling。
4. **数据 / 治理 / Console 重建**：DATA-AUDIT、TEST-GOVERNANCE、ARCH-OPT-AUDIT、Console / Eval 按新 orchestration 重建、统一监测 / 计费 / usage attribution 闭环。

固定样本 V2（碎段新闻、>4,000 words 超长文与 no-op window）使用固定样本、预先声明调用上限；与已关闭的 V1 分开。持续记录 calls、token、分层 duration（agent-run vs provider-request vs worker_tick wall）、首个可用输出时间和人工质量，但在没有同样本对照前不得宣称降本增效。

## 成功标准

当 Web 用户提交 learning 输入后，系统能够提供：

- 长期存在的 Reading Record。
- Stable Reading Base 和不可变 Reading Units。
- 早于完整增强完成的 `article_ready`。
- 可恢复的渐进 Enhancement Layers。
- 带 audit / eval hooks 的 Parsed Decisions。
- 基于新 base 的 Ask Claread sidecar，且不成为 orchestrator。
- run / step / layer 级 usage events。
- 可重置的 baseline schema，并保留词典三表。
- 当前记录内可构建和恢复的 RAG substrate。
- 文本、URL、PDF、OCR、文件上传等输入模式的统一适配入口。
- 不依赖旧 `render_scene_json` contract 的新 Web Reader Plate projection。

## Coding Agent 任务规则

- 每个 coding task 尽量控制在 2-8 小时。
- 每个任务必须写清 touched areas、expected tests、done criteria。
- 除非任务明确要求，agent 不读取 TMP research。
- agent 只更新本计划的阶段/任务状态或决策引用。
- 发现架构冲突时，先更新或讨论 `target-architecture.md` 的决策记录，再继续实现。
- 任何降本、batch、window 或 planner 改造都不得改变既有产品层公开合同；如果实现发现成本策略和产品语义冲突，先停在设计评审，不继续局部补丁。
- 验收节奏：短文、长文、超长文三种模式先分别完成代码级合同闭环，再统一真实 LLM / 页面验收；中间实现阶段优先使用 deterministic tests、fake executor、recorded LLM response 和 DB contract checks；不要每修一个局部就反复真实跑长文/超长文，避免高 token 成本和中间态误判。
- T4.2a-R2 budget / fence / publish contract 仍是当前权威护栏；不得通过 test-local worker subclass 或临时改写数据库 fingerprint 模拟成功；publish fence 测试必须经过真实 publisher + worker/pipeline catch；partial exhaustion 测试必须经过 WorkerLoop + Finalizer；budget diagnostics 测试必须查询持久 `reader_runtime_spans.metadata_json`。

## 任务历史

D0-D6 阶段流水账、每轮 coding agent prompt、T 系列任务的详细验收报告与已删除 TMP 的引用不在本计划维护。任务级 commit 与 verdict 由 git history 承担；cutover 落地与旧依赖审计结论见 [`modules/cutover-and-old-workflow.md`](modules/cutover-and-old-workflow.md)；D2 spike 结论历史索引见 [`archive/README.md`](archive/README.md)（spike 全文已删除，结论已压缩进 `target-architecture.md` 决策记录与对应 `modules/*.md`）。