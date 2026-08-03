# Reader Agentic Orchestration 实施计划

> 状态：`Architectural Cutover Complete（Reader/Ask 主链已单轨化，旧生产链已物理删除；Cutover milestone 已 closed）；Operational Readiness（计费、统一监测、Console/Eval 重建、Test Governance、ARCH 优化）为 post-cutover backlog`
> 最后更新：2026-08-03（CUTOVER-DOC-TRUTH-CLOSEOUT-R1：cutover milestone 标记 closed；DOC-R2 backend-closure verdict 冲突由 cutover 落地事实闭环；剩余事项移入 post-cutover backlog）

## 当前实施口径

本文保留 D0-D6 的既有实施推进线。2026-07-07 后，Reader enhancement 的批注链路、成本策略、渐进式发布和长文处理，以 `adaptive-reader-orchestration-design.md` 为当前权威设计。

下一轮开发不应把 adaptive planner、SSE / patch delivery、longform lazy enhancement 和全部 layer grouping 打包成一次大重构。实施顺序应先恢复短文质量、成本、Translation Group 产品语义和稳定发布，再补齐长文 grouped/windowed execution，最后进入超长文本的 outline-first / section-lazy 增强。

2026-07-09 后的验收节奏调整为：短文、长文、超长文三种模式先分别完成代码级合同闭环，再做统一真实 LLM / 页面验收。中间实现阶段优先使用 deterministic tests、fake executor、recorded LLM response 和 DB contract checks；不要每修一个局部就反复真实跑长文/超长文，避免高 token 成本和中间态误判。

`tmp/` 下的研究、验收和诊断材料只作为历史证据来源；临时开发代号不进入长期文档、代码注释、数据库命名或对外沟通口径。

## Adaptive Reader Orchestration 任务拆分

本节是 2026-07-07 后的执行入口。任务拆分遵循两个原则：

- 每一轮先完成可验证闭环，再进入下一轮架构扩展。
- coding agent 只负责单个任务包内的实现；架构方向、质量门禁和后续任务选择由人工评审后决定。
- 任何降本、batch、window 或 planner 改造都不得改变既有产品层公开合同；如果实现发现成本策略和产品语义冲突，先停在设计评审，不继续局部补丁。

### 整体模块地图

Reader enhancement 的当前主链路按层分开理解：

1. Input Adapter / Article Ready：把用户输入转成 Stable Reading Base、Reading Units、Anchor Segments。该层决定稳定文本与锚点，不决定批注密度或译文分组。
2. Strategy / Bootstrap：根据记录与 base 状态创建 reader jobs。当前 deterministic router 已提供 `SHORT_BATCH` / `STRUCTURED_BATCH` / `GROUPED_WINDOWED` 三态：translation 与 vocabulary 分别采用 batch 或 grouped/windowed job topology；short/structured grammar 走 compact `build_grammar_bundle` batch path，grouped/windowed grammar 保留 Z+ windows。Section-oriented / selective longform planner 尚未落地，不能描述为已有 runtime mode。
3. Layer Workers：执行 LLM 或 deterministic 后处理。worker 可以选择 batch/window 计算形态，但不能改变 Enhancement Layer 的公开输出语义。
4. Layer Publisher：校验 schema、anchor、publish fence、source hash 和 generation，写 `enhancement_layers` 与 `reader_events`。Publisher 是合同守门，不应替 worker 猜测或改写语义粒度。
5. Snapshot / Plate Projection：从 domain facts 重建页面。前端显示异常优先回查已发布 layer output；不要把 projection 误判为 layer truth。
6. Observability / Eval：对比 old AI Workflow、新 orchestration、真实页面行为和 usage events。没有 baseline/eval 证据，不把实现标记为完成。

当前已暴露的教训：成本优化不能压过产品合同。`translate_article` 的 batch compute 降低了调用数，但短文 batch translation group 一度被实现成 whole-unit group，破坏了 group-native translation 的阅读体验。该类回归已通过 T1.1a 的 group planning / hydration 合同修复；后续扩展 grouped/windowed execution 时必须沿用同一合同。

2026-07-09 长文抽查进一步确认：长文路径仍处于中间态。translation 非短文此前是 57/58 次 `translate_unit` per-unit 调用（T3.1 已改为 grouped/windowed `translate_article` batch job，待真实 LLM 验收确认降幅）；grammar window 花费较高但大多数 window `no_op`，且缺少候选数与 selector 拒绝原因 diagnostics。该结果不推翻 adaptive orchestration 方向，只说明 T3.4a/T4/T5 仍未闭环。

同日复盘还暴露了第二个结构问题：当前代码仍使用 raw `content_utf16_length <= 6000` 作为短文/非短文硬分流，导致一部分接近阈值、但按 `estimated_word_count` / `estimated_token_count` / 段落结构仍应属于短文或中档 batch 的新闻文本，被过早送入 heavy grouped/windowed 路径。三模式路线当前最缺的不是“更强的大 planner”，而是把 short batch / structured batch / grouped-windowed 的 complexity routing 做实，并补上中档 `structured batch` 与 short/medium compact grammar path。

### 里程碑

| # | Milestone | 目标 | 成功标准 |
|---|---|---|---|
| M0 | Baseline and Evaluation Harness | 固化新旧链路对比口径 | 同一文章可稳定比较 token、耗时、调用数、layer 数量、grammar/sentence 质量 |
| M1 | Short Article Recovery | 恢复短文解析质量、成本和首屏速度 | 短文不再 per-unit fan-out；translation 先出；grammar/sentence 接近旧 workflow 质量基线 |
| M2 | Stable Progressive Delivery | 修复页面闪烁、折叠和无序输出 | 发布结果尽量按阅读顺序出现；前端状态不因 layer 更新丢失 |
| M3 | Grouped Layer Execution | translation/vocabulary/grammar 支持 grouped/windowed 路径 | 中长文章降低调用次数；vocabulary 全文去重；window 结果仍 anchor-grounded |
| M4 | Adaptive Planner | 自动选择短文 batch、structured batch、中长文 grouped/windowed、长文 section 策略 | 先落 deterministic router；LLM 只输出 schema profile，deterministic planner 决定执行策略 |
| M5 | Outline and Longform | 长文导航与超长文 lazy enhancement | **L0/L1 deterministic navigation 已落地**；**T5.3 semantic outline durable layer 已落地**（`enhancement_layers.layer_type='semantic_outline'`，record/`document` scope；默认不请求、不进 budget/coverage 必需路径、不挂 `ReaderPlateSnapshot`）；**T5.4-R0** 才设计 snapshot projection；**T5.5** 才做 UI；lazy section enhancement 后置 |
| M6 | Streaming UX Upgrade | SSE / patch delivery 逐步替代高频全量 reload | 事件可恢复；更新不闪烁；后续支持 committed patch merge |

### 推荐执行顺序

1. M0/M1 的短文合同继续保持；短文真实页面已抽查过的部分不再反复消耗真 LLM。
2. M3 长文 grouped execution：T3.1（translation）与 T3.2b（vocabulary）均已完成实施；T3.3 phrase_gloss guard、T3.4a diagnostics、T3.4b density bug fix 已完成。
3. T3.5 completion state finalizer 已完成代码级实施（详见下方 T3.5 章节）；T4.1/T4.1a deterministic complexity routing、T4.1b structured article batch runtime mode 与 T4.1c short/medium compact grammar path 均已完成代码级实施（详见下方 T4.1/T4.1a/T4.1b/T4.1c 章节）。
4. T4.2a-R1 three-mode evidence parity and observability closure 已完成（详见下方 T4.2a-R1 章节）：acceptance harness 忠实复现 production topology（WorkerLoop + CompletionFinalizer + coverage_complete）；grammar batch ai_usage_events 漏传 usage_data 已修复；smoke/acceptance harness 注入 DevFakeGrammarBatchExecutor + DevFakeGrammarWindowExecutor，不得因 enable_zplus_grammar=True 意外调用真实 LLM；SHORT_BATCH / STRUCTURED_BATCH / GROUPED_WINDOWED 三态固定覆盖测试已落地（route/fingerprint/policy、job topology、effective calls、layer counts、final readiness、usage attribution）。
5. **T4.2a-V1 已正式关闭**。真实 LLM DB/runtime 验收覆盖三种 route、4 个 records，共 34 calls / 142,990 input tokens / 55,051 output tokens / 198,041 total tokens；Contract、Output Integrity、样本级 Semantic Quality 与 Page UX Gate 均通过。Page UX 在 commit `760402c2c` 的 clean worktree baseline Web 上完成 final-ready 验收：4/4 页面可访问；33/33 严格 click→active→explanation 断言通过；GROUPED 中后段 vocabulary / grammar / sentence interaction 通过；刷新、滚动、readiness 与 console/network 检查通过；页面验收阶段**无新的 LLM 调用**。Cost/Latency Baseline 仍为 PARTIAL（无可靠 provider 账单、无同样本旧链路真实 LLM 对照、per-job provider latency 不完整），**不得宣称实际 token、成本或时延降幅**。Progressive UX 已由 **T4.2a-PUX-R1 fixture 合同、PUX-R2 runtime 集成门、PUX-R3 测试卫生**闭合（不重跑 LLM）。
6. **T4.2a-R2 三态执行预算与切换安全护栏已完成代码级 review**（T4.2a-R2-R3a 补充 Test M + 文档终态同步）。核心护栏：durable per-layer `ExecutionBudget`（`max_effective_calls = planned * 3`）、batch-first/fallback 不重复执行、route flip claim+publish fencing、partial/full exhaustion 分层 force-fail（display_title 排除）、suppressed legacy 正式 supersede、budget-denied 持久观测、fingerprint 保守集合（方案 B）。V1 首次成功样本证明 guardrails 未干扰正常 topology，但未触发 retry/budget exhaustion，不替代 R2 deterministic failure-path 验收。**权威不可违反决策与详细预算模型归 [`agent-brief.md`](agent-brief.md) T4.2a-R2 章节；budget 合同归 [`modules/policy-and-cost-control.md`](modules/policy-and-cost-control.md#t42a-r2-durable-executionbudget--publish-fence--route-flip-fencing)；budget diagnostics 持久化归 [`modules/orchestration-runtime.md`](modules/orchestration-runtime.md#t42a-r2-budget-diagnostics-持久化)；决策记录归 [`target-architecture.md`](target-architecture.md#决策记录) `T4.2a-R2` 行**。下方 T4.2a-R2 章节保留状态摘要与测试证据。
7. bounded enhancement planner + specialized structured workers 的设计评估应先聚焦 long/very-long selective enhancement，优先减少 vocabulary/grammar 的空跑与重复扫描，而不是一开始扩到全部 translation。
8. Provider prompt cache / cache-hit 归因可作为后续成本杠杆继续验证，但它是成本优化项，不是三模式路由设计的替代品。
9. **T4.2a-V2-R1 已完成**（deterministic）：碎段新闻 / STRUCTURED 边界 / >4000 words 超长文 / no-op grammar window 四类固定样本，用 fake executor 验证 route、job topology、fingerprint/policy、reading-order publish、readiness 与 no-op 终态；详见下方 T4.2a-V2-R1 章节。不得与已关闭的 V1 混做真实 LLM。
10. M6 先做 debounce / state preservation，再做 SSE 和 patch merge。SSE 本身不能解决全量 reload 闪烁。
11. T4.2 bounded LLM document profiler **继续暂缓**；只有 deterministic router 在真实边界样本上出现稳定误判时才重新评估。

### 任务包

| ID | Task | Effort | Depends On | Done Criteria |
|---|---:|---|---|---|
| T0.1 | 建立新旧链路对比脚本 | 4-8h | 无 | 对同一输入输出 token、latency、usage event、layer counts、grammar/sentence counts、failed/no-op windows |
| T0.2 | 建立 golden sample 集 | 3-6h | T0.1 | 至少包含短新闻、970 词 Reuters/BBC、碎段新闻、长文、有 heading 长文；样本固定 record/input metadata |
| T0.3 | 定义 grammar/sentence 质量评审表 | 3-6h | T0.2 | 评审项覆盖 reading_goal、reading_variant、语法价值、锚定正确性、密度、重复率 |
| T1.1 | 短文 route hard switch | 4-8h | T0.1 | 短文进入 whole-article batch per layer，不创建 per-unit translation/vocabulary jobs；只代表调度降本，不代表 layer 产品语义已验收 |
| T1.1a | 短文 batch translation group 语义修复 | 4-8h | T1.1 | `translate_article` 保持 batch compute，但发布的 Translation Groups 恢复为语义阅读组；不得 one-anchor-one-group、one-sentence-one-group 或 one-unit-one-group |
| T1.2 | grammar batch worker 恢复 variant strategy | 4-8h | T1.1 | grammar prompt 注入 reading_goal、reading_variant、strategy、few-shot；不再使用孤立硬编码 prompt |
| T1.3 | grammar/sentence budget 回归 | 4-8h | T1.2 | 修复 window/batch budget key；970 词样本不再只输出 4 条总批注；密度不过载 |
| T1.4 | short-path publishing order | 4-8h | T1.1a | translation 验证通过后先 publish；vocabulary/grammar 可并发计算但不阻塞首屏阅读 |
| T2.1 | 前端 layer reload 防抖与状态保留 | 4-8h | T1.4 | layer 更新不会折叠当前打开批注，不重置 scroll/selection，不造成明显闪烁 |
| T2.2 | 后端 release order policy | 4-8h | T1.4 | group/window 结果按 reading order 或 current viewport priority 发布，不按纯完成顺序刷尾部 |
| T2.3 | Reader event payload audit | 3-6h | T2.1 | 明确哪些事件需要 full reload，哪些可局部合并；保留 polling fallback |
| T3.1 | translation grouped execution | 4-8h | T1.1a | 中长文章按 group/window 批量翻译，输出仍能按 translation group 前端显示；复用 T1.1a 的 group planning / hydration 合同（已完成实施/待验收：详见"当前进度"T3.1 章节） |
| T3.2 | vocabulary grouped execution | 4-8h | T1.1 | 中长文章 vocabulary 不再 per-unit 调用；同 lemma/surface 的重复标注可控 |
| T3.3 | vocabulary highlight policy | 3-6h | T3.2 | 明确只高亮首次、每处高亮、或首次强高亮加弱提示的产品规则，并有测试覆盖 |
| T3.4 | grammar grouped/window worker cleanup | 4-8h | T1.3 | grammar window 生成候选充足，selector 只做质量/密度裁剪，不因预算/schema bug 大量 no-op |
| T3.4a | grammar window diagnostics | 4-8h | T1.3 | 每个 grammar window 记录 raw candidate count、accepted/rejected count、reject reasons、budget、strategy hash；能解释 no-op |
| T3.5 | completion state finalizer | 3-6h | T3.1/T3.2/T3.4a | 所有目标 jobs/window terminal 后，`readiness_state` 推进到 `coverage_complete` 并发布 `record_state_changed` 事件（不更新 `product_state` 或 `layer_analysis_plans.status`）（已完成实施/待验收：详见"当前进度"T3.5 章节） |
| T4.1 | deterministic document feature extractor | 4-8h | M1/M3 | 输出 token、word、paragraph、heading、noise、block histogram、requested layers 等 profile input；作为 route hardening 的统一输入（已完成实施/待验收：详见"当前进度"T4.1/T4.1a 章节） |
| T4.1a | short/medium route hardening | 4-8h | T4.1/T3.5 | 不再用 raw `content_utf16_length` 单独决定短文路径；改用 estimated_token / estimated_word、paragraph、heading/noise、reading_goal 判定 short batch / structured batch / grouped-windowed（已完成实施/待验收：详见"当前进度"T4.1/T4.1a 章节） |
| T4.1b | structured article batch | 4-8h | T4.1a | 为中等文章补 whole-article structured batch mode；translation/vocabulary 尽量整篇 batch，保留 grounded publish 与 release order（已完成实施/待验收：详见"当前进度"T4.1b 章节） |
| T4.1c | short/medium compact grammar path | 4-8h | T4.1a/T4.1b | 短文与 structured batch 文章的 grammar 不再默认走重型全窗口空跑；候选生成更紧凑，publish 仍受 budget/density/anchor 约束（已完成实施/待验收：详见"当前进度"T4.1c 章节） |
| T4.2a-R1 | three-mode evidence parity and observability closure | 4-8h | T4.1c | acceptance harness 忠实复现 production topology（WorkerLoop + CompletionFinalizer + coverage_complete）；注入 DevFakeGrammarBatchExecutor/WindowExecutor；修复 grammar batch ai_usage_events 漏传 usage_data；三态固定覆盖测试（fake executor，仅代码级合同闭环，真实 LLM 验收暂缓）（已完成实施：详见"当前进度"T4.2a-R1 章节） |
| T4.2a-R2 | three-mode execution budget and cutover safety | 4-8h | T4.2a-R1 | per-layer 确定性执行预算；batch-first/fallback 不重复执行；route flip fencing（claim + publish）；预算耗尽不错误进入 coverage_complete；usage evidence 区分 attempted/executed/published/budget-denied（**代码级 review 通过 — T4.2a-R2-R3a**；权威决策归 [`agent-brief.md`](agent-brief.md)） |
| T4.2a-O1 | usage / cost / latency observability contract audit | 3-6h | T4.2a-V1 | 只读追踪 usage/cache/latency 从 provider result 到 usage event/runtime span/page readiness 的数据链；解释现有缺口并定义权威指标、可计算性矩阵和最小后续实现切片；不改生产代码、不调用 LLM（**已完成**） |
| T4.2a-O2 | usage presence diagnostics & durable execution correlation | 4-8h | T4.2a-O1 | `attempt_ordinal`/`execution_id`/`agent_run_id` 关联；usage presence diagnostics；event/span mismatch 诊断；translation/vocabulary/grammar/window/display_title 传播；deterministic tests + gated real-LLM（**O2-V1-R1 closed：隔离 SHORT_BATCH 4/4 correlation + agent_run_id + event/span token alignment；Sample A 仍 UNRESOLVED；Cost/Latency 仍 PARTIAL**） |
| T4.2a-O3 | duration provenance & provider-request observability | 4-8h | T4.2a-O2 | 单独记录 agent-run/executor duration；只有 provider 返回可归属 timing 时才记录 provider-request duration，否则显式 unavailable；不得改写 `latency_ms` 语义或宣称 provider latency（**代码级完成 / deterministic tests；Sample A 仍 UNRESOLVED；Cost/Latency 仍 PARTIAL**） |
| T4.2a-V2-R1 | three-mode boundary & very-long fixed-sample validation | 4-8h | T4.2a-R1/R2 | 碎段新闻 SHORT_BATCH、STRUCTURED 边界、>4000 words GROUPED_WINDOWED、no-op grammar window；deterministic fixture/fake executor；route/topology/fingerprint/publish/readiness/no-op（**代码级完成：5 focused tests passed；无生产代码改动；详见下方 T4.2a-V2-R1**） |
| T4.2a-PUX-R1 | progressive transition UX fixture / event replay | 4-8h | T4.2a-V1 / T2.1 | **fixture contract closed**：pure phase/replay/stale/interaction helpers（**21 tests**）；**不**单独构成 runtime 验收 |
| T4.2a-PUX-R2 | progressive transition runtime integration | 4-8h | T4.2a-PUX-R1 | **runtime integration gate**：page `reloadSnapshot` 接入 progressive 单调校验；单 cursor；stale/layer regression 不覆盖 UI；status strip；scroll 保留；4 page integration tests（**已完成；详见下方 T4.2a-PUX-R2**） |
| T4.2a-PUX-R3 | Reader Plate test-health closure | 1-2h | T4.2a-PUX-R2 | **closed**：测试源码读取先归一化 CRLF/LF，保留 auto-dismiss 语义断言；全套 Web Vitest 67 files / 958 tests 通过；不改生产行为 |
| T4.2a-PUX-R4-R3-R1 | Reader Plate Quick Peek source-identity reset & re-anchor | 6-10h | T4.2a-PUX-R3 / T4.2a-O4-R2-D | **closed**：full reload 时 Quick Peek 以 `anchor_segment_id + markId + generation + baseId` 稳定身份重新锚定；`{generation, base_id}` 构成 source identity，任一变化清理 selection/Quick Peek/anchor/restore token/grammar expansion；frozen rect 仅覆盖 setValue→rAF 恢复窗口；resolver 不回退到同段 sibling mark；rejected stale/fence snapshot 仅在 polling/page seam 断言，不进入 Surface value swap；same-snapshot early-return 只是 duplicate accepted snapshot guard。验证：R3-R1 Chromium 13/13、P2c + R2.1D + Gate-R1 Chromium 10/10、`pnpm --filter @claread/web typecheck` clean、`git diff --check` clean。commit `9a925f82`。不批准 SSE/WebSocket/JSON Patch/ETag/304/通用 tree diff |
| T4.2 | bounded LLM document profiler | 4-8h | T4.1a | LLM 只返回 genre/structure/schema_risk/selective hints；失败时 deterministic fallback；不直接决定流程（**暂缓**） |
| T4.3 | strategy planner | 4-8h | T4.1b/T4.2 | planner 选择 short batch、structured batch、grouped/windowed、section longform、selective longform |
| T4.3a | longform bounded enhancement planner | 4-8h | T4.3 | 先为 long/very-long 的 vocabulary/grammar 选择高价值 targets；translation 仍优先沿用独立 semantic group planner，除非后续证据支持扩权 |
| T4.4 | three-mode validation harness | 4-8h | T4.3/T5.1 | 用 fake/recorded outputs 覆盖 short batch、structured batch、grouped/windowed、section/selective 模式的 job plan、layer counts、usage attribution、completion state，并补 beginning/middle/end 与 section-jump 的位置敏感验收 |
| T5.1 | deterministic L0/L1 navigation | 4-8h | Stable Base | **closed（前端 + Chromium）**：L0 = `navigation.units` 全量段落导航；L1 = 前端纯派生 flat heading 章节导航（非树、非 semantic outline）。启用门槛 `unit_count >= 6 && heading_count >= 2` 且 units 非空；document-fallback / 未过门槛完整回退 L0。lead 区 `active=null`；`sourceIdentityKey = base_id:generation` 正式 reset；validated target cache + rAF source-identity fence。commits `701a9463` / `970d54d8` / `20be3d75` / `9fe6d94d`。不调用 LLM、不阻塞 translation、不改 schema/event/transport。权威 UI 合同见 [`modules/reader-record-plate-surface-ui.md`](modules/reader-record-plate-surface-ui.md#deterministic-navigation-l0--l1) |
| T5.1e | deterministic navigation contract docs sync | 1-2h | T5.1 | **docs-only closed**：L0/L1 分层、交互/身份、snapshot 边界与 semantic-outline 后置边界写入正式文档 |
| T5.2 | semantic outline contract + fixture design gate | 4-8h | T5.1 | **closed（只读门 + 合同产物）**：optional semantic-outline 的 schema/status/fixture 边界；不污染 `navigation.units`、不阻塞 `article_ready` |
| T5.2a | semantic outline validation contract | 4-8h | T5.2 | **closed**（commit `2bf3db97`）：`validate_semantic_outline_projection` + `ReaderSemanticOutlineProjection` typed schema + fixture cases；**明确不**挂 `ReaderPlateSnapshot` |
| T5.3 | semantic outline worker + durable publisher | 6-10h | T5.2a | **closed**（commit `781e4117`）：migration `0020` + `build_semantic_outline` job + record-level publisher + pipeline non-budget slot；详见下方 [T5.3](#t53-semantic-outline-worker--durable-layer) |
| T5.3b | semantic outline formal docs sync | 1-2h | T5.3 | **docs-only**：把 T5.3 durable 长期事实写入本计划 / target-architecture / representation-event-contract；不改生产代码 |
| T5.4-R0 | semantic outline snapshot projection design gate | 4-8h | T5.3 / T5.3b | **closed（设计门）**：optional `ReaderPlateSnapshot.semantic_outline` 投影边界 |
| T5.4a | semantic outline snapshot projection | 4-8h | T5.4-R0 | **closed**：published ready\|partial → 可选 snapshot 字段；invalid/stale → None；不改 `navigation.units`；不启用 SSE/patch |
| T5.4b | snapshot formal docs / contract harden | 1-2h | T5.4a | **closed（文档+回归）** |
| T5.5a | semantic outline L2 Reader UI | 6-10h | T5.4a | **closed（Web）**：内容大纲 rail 与 L0/L1 独立；revision-scoped node 不写 durable identity；默认不请求后端生成 |
| T5.6a | section identity + request planner | 4-8h | T5.5a | **closed**：纯 `SectionIdentity` / candidates / `plan_explicit_section_request`（Admit/NoOp/Reject）；零 I/O |
| T5.6b | section_v1 translation lane | 8-12h | T5.6a | **closed**（commit `c5abd4f7d`）：`request_origin=section_v1` + `translation_article_section_v1`；budget 共用 translation；coverage/ordinary supersede/worker tracked 用 `IS DISTINCT FROM` 隔离；bootstrap/drain/publisher 全量 range 校验；默认不扩 HTTP/UI |
| T5.6c | “解析此段” HTTP/UI | — | T5.6b | **closed（commit `8841c3d37`）**：认证 POST + Next BFF 传递完整 range witness；同步、job_id-bounded drain 与 queued recovery；L2 行内键盘可访问“解析此段/重试”与成功后的 snapshot reload；不新增 job type、不扫描 section lane、无 SSE/patch |
| T5.7 | semantic outline production readiness | 6-10h | T5.3–T5.6b | **closed（commit `0fe6fed78`）**：fixture 应用 `0020`；默认 Unconfigured generator + job/run permanent 终态闭合；`allow_semantic_outline_request_eligibility` 仅 DI |
| T5.8-R0 | real executor activation design gate | 2-4h | T5.7 | **closed（TMP）**：`TMP-t5.8-r0-...` R1-P2；usage 唯一记账、kill-switch 双检查、独立 policy、禁 empty stub |
| T5.8a | outline route/prompt/settings registration | 2-4h | T5.8-R0 | **implemented（注册形状）**：route/profile/enabled 字段/capability/完整 prompt；默认关闭 |
| T5.8b | controlled real adapter + policy + usage | 4-8h | T5.8a | **committed（commit `86df75fda`）**：`PydanticAISemanticOutlineGenerator`（DI only）；`SemanticOutlineExecutionPolicy` pre-call；worker `record_ai_usage_event` 按 call/usage 规则；默认仍 Unconfigured；**未** bootstrap kill-switch（T5.8d）；无默认启用/无真实 LLM |
| T5.8c | semantic outline opt-in real-LLM smoke harness | 2-4h | T5.8b | **committed（commit `062389e7d`）**：`tests/test_reader_semantic_outline_t58c_real_llm.py` 单一 `@pytest.mark.real_llm` 测试；默认 skip + 零外呼（conftest triple gate）；DI-only `PydanticAISemanticOutlineGenerator`；fail-closed 模型比对；单次 provider call；snapshot 真实验收 seam（production `build_reader_plate_snapshot` 前后构建，断言 `navigation.units` 逐值不变 + top-level `semantic_outline` ready\|partial + source identity 一致）+ published layer provenance fence（`generation`/`source_job_id`/`source_run_id`）+ usage audit；`observability_inconclusive` 不视为通过；**已于 2026-07-19 受控真实执行一次并通过**：显式授权 `deepseek-v4-flash`；单 provider call；functional 与 usage audit 均通过；未记录密钥、endpoint、prompt 或 provider payload |
| T5.8d-dev-activation | dev auto-activation of semantic outline main path | 2-4h | T5.8b | **closed（commit `0454c2172`）**：开发期自动激活路线。`activation_ready = semantic_outline_generation_enabled AND reader_semantic_outline_model_profile != ""`。`job_bootstrap.settings_aware_semantic_outline_request_eligibility(settings)` 工厂派生谓词；`pipeline_runner.ReaderEnhancementPipelineRunner.__init__` 接收 `settings: Settings | None = None`，activation_ready=True 时条件注入 `PydanticAISemanticOutlineGenerator`（延迟导入，仿 grammar_window）+ settings-aware eligibility，否则保持 `UnconfiguredSemanticOutlineGenerator` + 默认 always-false 谓词。committed defaults 仍关闭（`semantic_outline_generation_enabled=False`、`reader_semantic_outline_model_profile=""`）；显式注入优先于 activation_ready 自动装配。TDD 覆盖 A 默认关闭 / B 自动资格 / C 装配（sentinel + C7 explicit override）/ D 真链路 seam / E runner-level `bootstrap_missing_jobs` 真 seam，共 20 测试；该实现提交本身未运行真实 LLM；其后 T5.8c 已完成一次受控 real-LLM smoke |
| T6.1 | SSE reader event endpoint | 4-8h | T2.3 | 支持 cursor/reconnect/heartbeat；语义等价 polling；不引入不可恢复状态 |
| T6.2 | committed patch envelope | 6-10h | T6.1 | layer/outline/progress 更新可局部 merge；raw LLM token 不进入 article annotation stream |
| T6.3 | frontend patch merge | 6-10h | T6.2 | 页面不全量替换 snapshot；打开面板、selection、scroll 和当前阅读位置稳定 |

### 可并行项

- T0.2/T0.3 可由评审 agent 与实现 agent 并行准备。
- T1.2/T1.3 可在 T1.1 完成后同轮推进，但必须共同验收。
- T1.1a 是当前 M1 阻塞项，不应与 T3/M4 混做；可以和页面验证并行，但必须先于新的 grouped/windowed translation 扩展。
- T2.1 可与 T2.2 并行，但需要同一篇文章做联合页面验收。
- T5.1 可在 M1 修复期间提前做，因为它只依赖 Stable Base，不依赖 LLM planner。
- 真实长文/超长文页面验收在三模式代码级闭环后统一执行；中间阶段只做低成本合同验证。

### 当前进度（2026-07-09）

- T0.1/T0.2 已完成：已建立 `compare_reader_chains.py`、baseline harness 和 golden samples，支持 fake / real executor、usage event、layer count、completion reason 与 reading metadata 对比。
- T1.1 已完成调度降本部分：短文 translation / vocabulary 进入 whole-article batch compute、per-unit publish；`reuters_bbc_970` 默认 fake baseline 从 translation/vocabulary 9 次 per-unit 调用降为各 1 次 batch 调用。
- T1.1a 已通过当前代码层与页面抽查验收：短文 `translate_article` batch path 已用 `plan_translation_groups` 恢复 semantic Translation Group。planner 只返回连续 `anchor_segment_ids`；后端校验 coverage/contiguity/no-overlap/membership/stable order；后端 hydrate `group_id`/`source_text_hash`/`source_text`；translator 只返回 `group_id` + `translated_text`。`translate_article` batch compute 成本优势保留。新增/更新单测覆盖 single-sentence paragraph merge、sentence cluster、`$2.13 per hour` decimal boundary、planner validator fail-closed、fake planner/translator 端到端。2026-07-08 抽查记录 `cc128dca-1499-4f03-910d-9fbade42550a` 走 `translate_article` batch path，2 个 unit 发布 6 个 3-anchor translation groups；`$2.13 per hour` 保持在同一 anchor/group。记录 `43c1cb1b-75d9-4dcf-a140-cdbaa875fd8f` 因 `content_utf16_length=6064` 超过当前短文阈值，仍走非短文 per-unit translation path，可作为未被 T1.1a 破坏的页面抽查，但不是 batch path 验收样本。
- T1.2/T1.3 已完成：grammar window worker 已恢复 `reading_goal` / `reading_variant` strategy 注入，并修复 `grammar_note.count` / `sentence_analysis.count` budget key 对齐问题。
- T1.4 已部分覆盖：batch publisher 会按 `target_unit_ids` 重排输出，translation batch 在 worker 顺序上先于 vocabulary / grammar；但页面防闪烁、批注展开状态保留、SSE/patch 仍属于 M2/M6。
- T3.2a vocabulary duplicate highlight policy v1 已完成（含 cleanup）/待验收；T3.2b non-short vocabulary grouped execution 已完成实施/待验收。保留短文 `build_vocabulary_layer_article` batch path 成本优势；新增 vocabulary duplicate policy 在 worker 侧 `_build_vocabulary_output_from_candidates` 内 span dedup 之后、`MAX_VOCABULARY_ITEMS` 上限之前执行。per-unit path 单 unit 内 dedup；batch path 额外在 `_build_vocabulary_batch_outputs` 末尾做跨 unit dedup（按 reading order，首次出现 wins）。产品规则：`vocab_highlight` 同 `headword.lower()` 首次强高亮，后续 skip `duplicate_vocab_highlight_headword`；`phrase_gloss` 同 `(phrase, phrase_type, gloss)` 去重，不同 gloss 保留；`context_gloss` 同 `(display, gloss)` 去重，不同 gloss 保留；cross item_type 永不去重。T3.2 review findings P1-1：dedup 已移到 `MAX_VOCABULARY_ITEMS` cap 之前，candidate schema 改用 `MAX_VOCABULARY_CANDIDATE_ITEMS=10`，确保重复 candidate 不占用 published slot。T3.2 review findings P1-2：batch path per-unit diagnostics + cross-unit duplicate skips 现在流入 `quality_json.skipped_items`，包含 `reason_code`、`item_type`、`unit_id`、`anchor_segment_id`、`selected_text`。T3.2a cleanup：batch prompt 显式暴露 `max_published_items_per_unit` / `max_candidate_items_per_unit`，与 per-unit prompt 口径一致。T3.3 highlight policy v1 已定为"首次强高亮，后续重复项不发布；diagnostics 记录 skipped duplicate"。

#### T3.2b non-short vocabulary grouped execution（已完成实施/待验收）

1. 当前 short article vocabulary batch path 入口条件：`job_bootstrap._is_short_article` 判定 `len(reading_bases.text) <= SHORT_ARTICLE_MAX_CHAR_COUNT=6000` 为 true 时，`_bootstrap_vocabulary_jobs` 创建单条 `build_vocabulary_layer_article` batch job（`target_scope='unit_range'`，`input_json.target_unit_ids` 覆盖所有缺 vocabulary layer 的 unit）。
2. non-short 现状：`len(reading_bases.text) > SHORT_ARTICLE_MAX_CHAR_COUNT=6000` 时已路由到 `_bootstrap_vocabulary_grouped_jobs`，按连续 unit window 创建多条 `build_vocabulary_layer_article` batch job；不再创建 per-unit `build_vocabulary_layer` job。
3. 最小合理方案（已实施）：复用 `build_vocabulary_layer_article` batch job type + `publish_article_vocabulary_batch` publisher，但在 bootstrap 阶段按"窗口"（连续若干 unit 一组）切分，每窗口一条 batch job，`target_unit_ids` 限定为该窗口的 unit 子集；不再按整篇 6000 char 阈值一刀切。worker 侧无需新增 job type，新增 grouped 路由分支 `_bootstrap_vocabulary_grouped_jobs`。保持 unit publish（publisher 已按 `:unit_id` suffix fingerprint 发布 N 条 per-unit `enhancement_layers` 行）。复用 T3.2a duplicate policy：per-unit dedup 在 `_build_vocabulary_output_from_candidates` 内已生效；cross-unit dedup 在 `_build_vocabulary_batch_outputs` 末尾已生效，窗口内跨 unit dedup 直接复用，跨窗口 dedup 不执行（v1 行为，见风险）。
   - 不新增 grouped/window vocabulary job type：现有 batch job type + publisher 已足够承载窗口化语义。
   - 实现：`plan_vocabulary_windows` 纯函数按 `order_index` 排序，贪心累加 unit 直到达到 target chars（`VOCABULARY_WINDOW_TARGET_CHAR_COUNT=3000`）或超过 safety max（`VOCABULARY_WINDOW_SAFETY_MAX_CHAR_COUNT=5000`）。单个 unit 超过 safety max 时独占一个 window。`window_id` 是 sorted unit_ids 的 sha256 前 12 位，保证同 unit 集合的窗口在 re-plan 后 window_id 不变（idempotency 基础）。
   - 每窗口 job 的 `target_key = f"{record_id}:window:{window_id}"`，`idempotency_key = f"{operation_fingerprint}:{record_id}:window:{window_id}"`，`input_signature_suffix` 包含 `window_id`。多窗口 job 的 `target_key` / `input_hash` / `idempotency_key` 互不相同，不冲突。
   - 风险 A（已锁定）：跨窗口重复 headword 仍会各自强高亮（cross-unit dedup 只在单 batch job 内生效）。v1 可接受：长文同 headword 在不同窗口各高亮一次，优于 per-unit 全高亮。测试 `test_t32b_cross_window_duplicate_headword_v1_both_windows_keep_highlight` 锁定此行为。不声称全文 dedup 已完成。
   - 风险 B（已锁定）：窗口切分边界与 translation group / grammar window 不对齐。v1 保持 vocabulary 窗口独立于 translation group 粒度，只按 unit 连续性切分；不影响 translation group 合同。
4. 影响面：不改 translation group 合同；不改 grammar window worker；不改 frontend layer contract；不触碰 `apps/web/**`；不新增 migration。
5. 已新增测试：窗口切分 planner（连续 unit 覆盖、无重叠、safety max、empty/single）；跨窗口重复 headword v1 行为锁定；bootstrap 在 non-short 文章下创建多条 batch job 并保证 idempotency；partial publish 只为缺失 unit 建 window；多窗口 target_key/fingerprint/input_json 区分；pipeline runner 处理多窗口 batch job 并发布 per-unit vocabulary layer。

#### T3.1 non-short / long translation grouped execution（已完成实施/待验收）

1. 短文路径不变：`_bootstrap_translation_jobs` 仍走 `_bootstrap_translation_batch_job`，创建单条 whole-article `translate_article` batch job（`target_scope='unit_range'`，`input_json.target_unit_ids` 覆盖所有缺 translation layer 的 unit），无 `window_id`。
2. non-short 现状：`_is_short_article` 为 false 时路由到 `_bootstrap_translation_grouped_jobs`，按连续 unit window 创建多条 `translate_article` batch job；不再创建 per-unit `translate_unit` job。原 per-unit `_bootstrap_translation_jobs` 老循环已删除。
3. 最小合理方案（已实施）：复用 `translate_article` batch job type + 现有 batch translation worker + `publish_article_translation_batch` publisher。worker 与 publisher 已 window-agnostic：worker 从 `input_json.target_unit_ids` 读取子集只翻译窗口内 units；publisher 校验 `sorted(target_unit_ids) == sorted(output_unit_ids)` 后按 `:unit_id` suffix fingerprint 发布 N 条 per-unit `enhancement_layers`。无新增 job type / migration。
   - 实现：`plan_translation_windows` 纯函数按 `order_index` 排序，贪心累加 unit 直到达到 target chars（`TRANSLATION_WINDOW_TARGET_CHAR_COUNT=6000`）或超过 safety max（`TRANSLATION_WINDOW_SAFETY_MAX_CHAR_COUNT=10000`）。translation 阈值高于 vocabulary（3000/5000），因为 translation 需要更多上下文保证 group 语义完整。单个 unit 超过 safety max 时独占一个 window。`window_id` 是 sorted unit_ids 的 sha256 前 12 位，保证同 unit 集合 re-plan 后 window_id 不变。
   - 每窗口 job 的 `target_key = f"{record_id}:window:{window_id}"`，`idempotency_key = f"{operation_fingerprint}:{record_id}:window:{window_id}"`，`input_signature_suffix` 包含 `window:{window_id}:batch`。多窗口 job 的 `target_key` / `input_hash` / `idempotency_key` 互不相同。`_insert_unit_range_job` 已支持 `target_key_override` / `idempotency_key_suffix`（T3.2b 引入），直接复用。
   - Translation Group 合同保留：每窗口的 LLM 输出仍走 `build_deterministic_translation_groups`（`plan_translation_groups` + `_hydrate_translation_groups`）。LLM 不能 choose/merge/split/reorder/add/drop groups；后端校验 coverage/contiguity/no-overlap/membership/stable order 并 hydrate `group_id` / `source_text_hash` / `source_text`。窗口化不破坏 group-native 合同。
   - 风险 A（已锁定）：跨窗口 translation group 顺序不保证按 `published_at` 与 reading order 一致。窗口并发完成时 `published_at` 顺序不可预测，但每个 unit 内 groups 仍按 reading order 稳定；前端按 unit_id 加载 layer，不依赖跨 unit `published_at`。测试 `test_t31_pipeline_runner_processes_multiple_translation_windows_and_publishes_per_unit_layers` 验证 per-unit group `group_id` 稳定格式而非跨 unit `published_at` 顺序。
   - 风险 B（已锁定）：窗口边界与 vocabulary window / grammar window 不对齐。v1 保持各层窗口独立，只按 unit 连续性切分；不影响其他层合同。
4. 影响面：不改 Translation Group 合同；不改 grammar window worker；不改 vocabulary worker；不改 frontend layer contract；不触碰 `apps/web/**`；不新增 migration。`translation_worker.py` / `layer_publisher.py` 未修改。
5. 已新增/更新测试：window planner（连续 unit 覆盖、无重叠、safety max 独占、empty/single、`window_id` 稳定）；short article bootstrap 仍单条 batch；non-short bootstrap 多 window job 且无 `translate_unit`；idempotency 重复 bootstrap 不重复；partial publish 只为缺失 unit 建 window；多窗口 `target_key`/`idempotency_key`/`input_hash` DB 级区分；pipeline runner 处理多窗口 batch job 并发布 per-unit translation layer；Translation Group 合同回归（不退化为 one-unit-one-group；`$2.13 per hour` decimal boundary 不被切坏）。同步更新 `test_t11_long_article_routes_to_per_unit_path` 与 `test_superseded_stale_jobs_are_not_claimed_by_worker` 以反映 non-short 现在走 `translate_article`。

#### T3.5 completion state finalizer（已完成实施/待验收）

1. 目标：在所有目标 jobs/windows 进入 terminal 状态后，把 `readiness_state` 推进到 `coverage_complete` 并发布 `record_state_changed` 事件，避免 candidate scan 语义下的永久卡死。**v1 不更新 `product_state` 或 `layer_analysis_plans.status`**：`product_state` 在 clean / no_op / completed_with_failures 路径上 intentionally 留在 `readable_enhancing`，避免把 translation + vocabulary 已成功的文章锁成 `failed`；plan status 由现有 grammar window publisher / pipeline runner 路径维护。
2. 核心实现（`services/api/app/services/reader_orchestration/completion_finalizer.py` + `worker_loop.py` 集成）：
   - `should_attempt_finalization(summary)`：只在 pipeline 停止原因属于可 finalizable 集合时返回 true；`NON_FINALIZABLE_STOPPED_REASONS` 当前仅包含 `attention_required`。`max_ticks_reached` / `max_jobs_reached` / `all_workers_no_job` 都是 finalizable。
   - `CompletionFinalizer.finalize_completion_state`：在 worker_loop 决定不再更新 record product_state（`should_update_record=False`）且 `should_attempt_finalization=True` 时调用。基于 durable state（`count_enhancement_jobs_by_terminal_status` + `count_analysis_windows_by_terminal_status`）判定 outcome，不依赖 in-memory pipeline summary 的瞬时计数。
   - 三种 outcome：`completed_clean`（全部 succeeded 且无 failed/no_op windows）、`completed_with_no_op`（无 failed 但有 no_op windows）、`completed_with_failures`（有 failed windows 或 failed_terminal jobs）。
   - 完成后只做两件事：调用 `update_record_readiness_state_if_active` 把 `readiness_state` 推进到 `coverage_complete`，并通过 `event_runtime.publish_event_in_transaction` 发布 `record_state_changed` 事件（`field=readiness_state`）。**不写 `product_state`、不写 `layer_analysis_plans.status`、不写 progress summary**。
3. 关键策略（v1 锁定）：
   - **cap 不再自动阻断 finalization**：pipeline runner 在 processed-count 自增之后才检查 max_ticks / max_jobs，所以最后一个成功的 tick 可能恰好落在预算上。finalizer 以 durable state 为准；只要所有 enhancement jobs 已 terminal，`max_ticks_reached` 与 `max_jobs_reached` 都应正常 finalize 到 `coverage_complete`。两个 cap 对称。`NON_FINALIZABLE_STOPPED_REASONS` 只保留 `attention_required`。
   - **stuck analysis windows 的 v1 策略是 force-fail + completed_with_failures**：当所有 enhancement jobs 已 terminal 但仍有 `pending` / `running` analysis windows 时，finalizer 调用 `force_fail_non_terminal_analysis_windows`，把这些 windows 标记为 `failed` 并写入 diagnostics（`failure_code=finalizer_forced_window_failure`、`forced_by=completion_finalizer`），然后以 `completed_with_failures` 收尾。
   - **避免 candidate scan 永久卡死**：candidate scan 只会重新挑选 `runnable_job_count > 0` 的记录；如果 jobs 全部 terminal 但 windows 卡住，记录会永远不再被 scan 到，readiness_state 会永远停在 `article_ready` / `initial_enhancement_ready`。force-fail + completed_with_failures 是 v1 的闭合手段，不尝试重跑 windows。
4. 影响面：不改 `job_bootstrap.py`；不改 route hardening；不改 structured batch；不触碰 `apps/web/**`；不新增 migration。新增 `repository.py` 的 finalizer helper（`count_enhancement_jobs_by_terminal_status` / `count_analysis_windows_by_terminal_status` / `update_record_readiness_state_if_active` / `force_fail_non_terminal_analysis_windows`）与 `worker_loop.py` 的 finalizer 调用集成。finalizer 本身只写 `reading_records.readiness_state` + `reader_events`，不写 `reading_records.product_state` 或 `layer_analysis_plans.status`。
5. 已新增/更新测试（`tests/test_completion_finalizer.py` 12 个 + `tests/test_reader_orchestration_worker_loop.py` 新增 stuck-windows 闭环）：
   - `completed_clean` 转换（全部 succeeded、无 windows）。
   - `completed_with_no_op`（有 no_op windows、无 failed）。
   - `completed_with_failures`（有 failed windows）。
   - `max_jobs_reached` + all-terminal durable state -> `coverage_complete`（P1 回归）。
   - `max_ticks_reached` + all-terminal durable state -> `coverage_complete`（与 max_jobs 对称，本次补齐）。
   - 非 terminal jobs 仍存在（`retry_later`）时不 finalize。
   - 所有 enhancement jobs terminal 但 windows stuck -> force-fail + `completed_with_failures`（finalizer 单元级 + worker_loop 集成级双覆盖）。
   - 未 bootstrap enhancement jobs 时不 finalize。
   - worker_loop real chain 集成：drain 完所有 enhancement job 后 finalize 到 `coverage_complete`，`record_state_changed` 事件 payload `field=readiness_state`，记录退出 candidate scan。
6. 风险/边界（已锁定，不扩 scope）：
   - force-fail 不尝试重跑 windows；被 force-fail 的 windows 在 v1 不提供 retry 入口。如需 retry，应作为 T4+ 的 action-required UX 单独设计。
   - `attention_required` 仍是非 finalizable 的唯一原因；finalizer 不会在 attention_required 下覆盖 record 状态。
   - finalizer 只处理 ENHANCEMENT_PIPELINE_JOB_TYPES 范围内的 jobs（不含 `article_rag_index_build`）；RAG substrate 不进入 completion 闭环。

#### T4.1 / T4.1a deterministic document feature extractor + short/medium route hardening（已完成实施/待验收）

1. 目标：把短文/中档/长文路由从 legacy raw `content_utf16_length <= 6000` 二元分流，升级为 deterministic 三态路由：`SHORT_BATCH`、`STRUCTURED_BATCH`、`GROUPED_WINDOWED`。本轮只修 **route decision**，不扩到 bounded planner，不改变 worker/publisher 的公开 layer contract。
2. 核心实现（`services/api/app/services/reader_orchestration/document_feature_extractor.py` + `job_bootstrap.py` 集成）：
   - 新增 `DocumentFeatureProfile` 纯函数 profile：从 `base_text`、`unit_types`、`reading_goal`、`reading_variant`、`requested_layers` 派生 `estimated_word_count`、`estimated_token_count`、`paragraph_count`、`heading_count`、`list_item_count`、`quote_count`、`unknown_block_count`、`structural_noise_ratio`、`extractor_version=document_feature_v1`。
   - 新增 `ArticleRoute` 三态 classifier：`estimated_word_count <= 1100` -> `SHORT_BATCH`；`1100 < estimated_word_count <= 2000` 且 `content_utf16_length <= 12000` -> `STRUCTURED_BATCH`；其余 -> `GROUPED_WINDOWED`。`estimated_word_count` 是 primary router，`content_utf16_length` 只保留为 structured-tier coarse guardrail。
   - `job_bootstrap.py` 以 `_load_article_route()` 取代 legacy `_is_short_article()`：translation/vocabulary bootstrap 都先走 route classifier，再决定“单条 whole-article batch job”还是“grouped/windowed batch jobs”。本轮 `SHORT_BATCH` 与 `STRUCTURED_BATCH` 仍共用现有 whole-article batch 执行路径；`GROUPED_WINDOWED` 继续沿用 T3.1/T3.2b 的 window job 路径。
3. 关键修复（本轮锁定）：
   - **BBC near-threshold regression 修复**：约 1000 词、约 6300 chars 的新闻文，在 legacy raw-char router 下会被误送入 heavy grouped/windowed；现在按词数进入 `SHORT_BATCH`。
   - **中档文章 landing zone 修复**：约 1450 词、约 8900 chars 的文章不再直接掉进 grouped/windowed，而是进入 `STRUCTURED_BATCH`；虽然本轮执行仍复用 whole-article batch path，但 route label 已正确，为 T4.1b 留出稳定落点。
   - **Unicode non-CJK word counting 修复**：非 ASCII / 非 CJK 脚本（Cyrillic / Arabic / Greek / Devanagari / Thai 等）不再被计成 0 词。non-CJK token 改为“包含至少一个 Unicode 字母或数字、且不属于 CJK/Hangul/Kana 范围”的 whitespace token；纯标点仍不计词。
   - **missing-base route stability 修复**：`_LockedActiveBaseState` 新增 `cached_route`，同一 `bootstrap_missing_jobs()` 调用内 translation 与 vocabulary 共用一次路由决策。base row 缺失时首次会缓存 `GROUPED_WINDOWED`，第二次调用不再对空 profile 重新判成 `SHORT_BATCH`。
4. 影响面：不改 `translation_worker.py` / `vocabulary_worker.py` / `grammar` worker；不改 route/schema 公共 contract；不改 `apps/web/**`；不新增 migration；不引入 LLM profiler。`SHORT_ARTICLE_MAX_CHAR_COUNT=6000` 仅作为 legacy observability 常量保留，不再作为唯一路由判定。
5. 已新增/更新测试：
   - `tests/test_document_feature_extractor.py`：23 个纯单测，覆盖 short/structured/grouped 三态、CJK/混合文本、UTF-16 surrogate pair、profile replayability、pure punctuation exclusion，以及 Cyrillic / Arabic / Greek 长文不再误路由到 `SHORT_BATCH`。
   - `tests/test_reader_orchestration_job_bootstrap_strategy.py`：49 个测试，覆盖 BBC near-threshold -> `SHORT_BATCH`、中档文章 -> `STRUCTURED_BATCH`（translation/vocabulary 各 1 条 whole-article batch job、无 `:window:` target key、无 per-unit job）、长文 -> `GROUPED_WINDOWED`（多 window jobs 保留），以及 missing-base 双调用 route stability。
   - `tests/test_reader_orchestration_pipeline_runner.py`：回归通过，证明 route hardening 未破坏既有 batch/window publish contract。
6. 风险/边界（已锁定，不扩 scope）：
   - `STRUCTURED_BATCH` 当前只是 **正确的 route label + whole-article batch landing zone**，不是独立 runtime mode。若要让 structured batch 使用不同 budget/prompt/release policy，应在 T4.1b 单独实现，不在本轮扩。
   - `structural_noise_ratio`、`heading_count`、`list_item_count` 等结构信号本轮只写入 profile 供观测与后续 planner 使用，不参与当前 router 判定。
   - `cached_route` 只在单次 `bootstrap_missing_jobs()` 调用内稳定；跨调用不缓存，这是预期行为，因为 active base 可能已重建，下一次应重新评估 route。

#### T4.1b structured article batch（已完成实施/待验收）

1. 目标：让 `STRUCTURED_BATCH` 从 T4.1a 的"正确 route label + 共用 short-batch 执行路径"升级为**独立可审计 runtime mode**。translation / vocabulary 在 `STRUCTURED_BATCH` 下仍尽量整篇 batch compute；publish contract 不变（仍 grounded、仍按现有 layer contract 输出）；不破坏 `SHORT_BATCH` 与 `GROUPED_WINDOWED` 既有 contract；为后续 T4.1c grammar compact path 留出清晰接口（`input_json.article_route` + `envelope_json.document_features`）。
2. 核心实现（`services/api/app/services/reader_orchestration/job_bootstrap.py`）：
   - 新增 `TRANSLATION_STRUCTURED_BATCH_OPERATION_FINGERPRINT` / `TRANSLATION_STRUCTURED_BATCH_POLICY_VERSION` / `VOCABULARY_STRUCTURED_BATCH_OPERATION_FINGERPRINT` / `VOCABULARY_STRUCTURED_BATCH_POLICY_VERSION` 四个常量。`STRUCTURED_BATCH` 使用独立的 `*_structured_v1` fingerprint base 与 `*_structured_bootstrap_v1` policy_version；`SHORT_BATCH` 与 `GROUPED_WINDOWED` 保留现有 `*_v1` base 以保护既有 idempotency contract。
   - `_LockedActiveBaseState` 新增 `cached_profile: DocumentFeatureProfile | None`，与 `cached_route` 一起由 `_load_article_route` 一次性缓存。新增 `_build_document_features_metadata` / `_route_document_features` helper 把 profile 信号（`estimated_word_count` / `estimated_token_count` / `unit_count` / `paragraph_count` / `heading_count` / `structural_noise_ratio` / `extractor_version`）写入 `envelope_json.document_features`，供审计与 T4.1c grammar compact path 直接读取，无需重算。
   - 四个 batch/grouped bootstrap 方法（`_bootstrap_translation_batch_job` / `_bootstrap_translation_grouped_jobs` / `_bootstrap_vocabulary_batch_job` / `_bootstrap_vocabulary_grouped_jobs`）统一接受 `route: ArticleRoute` 参数。batch 方法（translation / vocabulary）按 route 选择 fingerprint base + policy_version + `input_signature_suffix` 中的 `route_suffix`（`"short"` vs `"structured"`）；grouped 方法只用 `GROUPED_WINDOWED`，保留共享 `*_v1` base。所有四个方法的 `envelope_json` / `input_json` 都写入 `article_route`，`envelope_json` 同时写入 `document_features`。
   - 路由身份三态可审计：`reader_jobs.operation_fingerprint` 区分 `STRUCTURED_BATCH`（`*_structured_v1:hash`）与 `SHORT_BATCH`/`GROUPED_WINDOWED`（共享 `*_v1:hash`）；`reader_runs.policy_version` 区分 `STRUCTURED_BATCH`（`*_structured_bootstrap_v1`）与另外两态；`reader_jobs.input_json.article_route` / `reader_runs.envelope_json.article_route` 三态全区分（`short_batch` / `structured_batch` / `grouped_windowed`）。route 变化（如 base 重建后 short -> structured）会触发 `_supersede_stale_fingerprint_jobs` supersede 旧 route 的 jobs。
3. 影响面：不改 worker prompt；不改 translation / vocabulary publish contract；不改 `apps/web/**`；不改 API route/schema 公共 contract；不新增 migration；不扩到 bounded LLM profiler / strategy planner / semantic outline；不回头扩 T3.5 scope。
4. 已新增测试（`tests/test_reader_orchestration_job_bootstrap_strategy.py`）：4 个 T4.1b focused tests：
   - `test_t41b_short_batch_route_identity_in_job_and_run_metadata`：SHORT_BATCH 文章在 `input_json.article_route` / `envelope_json.article_route` / `envelope_json.document_features` / `operation_fingerprint` base / `policy_version` 全部记录 `short_batch` 身份。
   - `test_t41b_structured_batch_route_identity_distinct_from_short`：STRUCTURED_BATCH 文章使用 DISTINCT fingerprint base + policy_version（与 SHORT_BATCH 不同），translation 与 vocabulary 两层都携带 `structured_batch` 身份；medium fixture 的 `document_features.estimated_word_count > 1100` 证明路由决策信号被审计。
   - `test_t41b_grouped_windowed_route_identity_in_job_and_run_metadata`：GROUPED_WINDOWED 文章每个 window job 都携带 `grouped_windowed` 身份，保留共享 `*_v1` base + `policy_version`（T3.1/T3.2b idempotency contract 不变）。
   - `test_t41b_no_per_unit_fanout_regression_across_three_routes`：三态都不产生 `translate_unit` / `build_vocabulary_layer` per-unit job；每个 batch/window job 的 `input_json.article_route` 与预期 route 一致。
5. 风险/边界（已锁定，不扩 scope）：
   - `STRUCTURED_BATCH` 当前只让 route identity 在 job/runtime 中**可见、可测、可审计**；worker prompt / budget / release policy 仍与 `SHORT_BATCH` 共用整篇 batch path。若要给 structured batch 独立 budget/prompt/release policy，应在 T4.1c 或后续任务单独实现，不在本轮扩。
   - `SHORT_BATCH` 与 `GROUPED_WINDOWED` 共享 `*_v1` fingerprint base 是**有意设计**：保护既有 idempotency contract，避免 T4.1b 引入无谓的 supersede 风暴；三态区分由 `input_json.article_route` 完成。
   - `document_features` 在 missing-base 防御分支下为 `None`（无 profile 可记录），这是预期行为。
   - 真实 LLM 下 structured batch 的成本/质量/时延仍需 T4.1c + 页面验收收口，本轮只做 runtime boundary 与测试护栏。

#### T4.1c short/medium compact grammar path（已完成实施/待验收）

1. 目标：让 `SHORT_BATCH` 与 `STRUCTURED_BATCH` 的 grammar 不再默认走重型 Z+ analysis-window 路径。短文/中档文使用单次 whole-article `build_grammar_bundle` / `unit_range` batch job，一次 LLM call 覆盖全部 unit；publisher 按 `unit_id` 拆分输出为 per-unit `grammar_note` / `sentence_analysis` layer。`GROUPED_WINDOWED` 长文 grammar 继续保持现有 analysis-window / window-publisher 合同，不回归。
2. 核心实现（`services/api/app/services/reader_orchestration/`）：
   - `job_bootstrap.py`：`_bootstrap_grammar_jobs_or_zplus` 新增 route-aware 三路分流——`force_legacy_grammar=True` → legacy per-unit；`GROUPED_WINDOWED` → Z+ analysis-window path（合同不变）；`SHORT_BATCH` / `STRUCTURED_BATCH` → compact grammar batch path。`_bootstrap_grammar_batch_job` 创建单个 `build_grammar_bundle` / `unit_range` batch job，`input_json.target_unit_ids` 列出全部待发布 unit。route-specific fingerprint：`SHORT_BATCH` 使用 `grammar_bundle_article_v1` base + `reader_grammar_batch_bootstrap_v1` policy_version；`STRUCTURED_BATCH` 使用 `grammar_bundle_article_structured_v1` base + `reader_grammar_batch_structured_bootstrap_v1` policy_version（T4.1b pattern）。
   - **job_type 复用决策**：`GRAMMAR_BATCH_JOB_TYPE = GRAMMAR_JOB_TYPE`（均为 `"build_grammar_bundle"`）。batch 与 per-unit job 由 `target_type`（`unit_range` vs `unit`）和 `operation_fingerprint` base 区分，不新增 migration。两路径在 pipeline runner 中均报告 `worker_type="grammar_bundle"`，满足现有 `reader_runtime_spans.worker_type` CHECK constraint。
   - `grammar_worker.py`：新增 `GrammarBatchCandidateOutput`（无固定 `max_length`，per-unit budget 在 split 后执行）、`GrammarBatchExecutor` Protocol / `PydanticAIGrammarBatchExecutor` / `FakeGrammarBatchExecutor`、`GrammarBundleWorkerService.process_next_grammar_batch_job_for_record` + `claim_grammar_batch_job_for_record` + `process_claimed_grammar_batch_job`。`GrammarBatchJobContext` 从 `input_json.article_route` + `envelope_json.document_features` 读取 T4.1b 路由信号；`_build_grammar_batch_prompt` 将 `article_route` 和紧凑 `document_features` 摘要写入 prompt，使模型可按文章 tier 调整候选密度。
   - `layer_publisher.py`：新增 `PublishedGrammarBatch` + `publish_article_grammar_batch`，将 batch LLM 输出按 `unit_id` 拆分为 per-unit `grammar_note` / `sentence_analysis` layer。per-unit layer fingerprint suffix 使用 `f"{operation_fingerprint}:{unit_id}"` 模式避免 N 个 per-unit layer 发布时的 unique constraint 冲突。
   - `pipeline_runner.py`：`_run_grammar_attempt` 合并 batch-first + per-unit-fallback 逻辑——先尝试 `process_next_grammar_batch_job_for_record`（SHORT_BATCH / STRUCTURED_BATCH），无 batch job 时回退到 `process_next_grammar_job_for_record`（GROUPED_WINDOWED 或 `force_legacy_grammar`）。`WorkerType` 不新增 `grammar_bundle_batch`；worker_order 不变。supersede 统计使用 `_count_grammar_batch_superseded_jobs` 覆盖 `GRAMMAR_BATCH_OPERATION_FINGERPRINT` 与 `GRAMMAR_STRUCTURED_BATCH_OPERATION_FINGERPRINT` 两个 base，确保 STRUCTURED_BATCH route flip 的 `superseded_jobs` / `outcome_counts.superseded` 完整可观测。
3. 影响面：不改 worker prompt 文件；不改 translation / vocabulary publish contract；不改 `apps/web/**`；不改 API route/schema 公共 contract；不新增 migration；不扩到 bounded LLM profiler / strategy planner / semantic outline / SSE / RAG / prompt cache；不回头扩 T3.5 scope；不用"恢复默认 per-unit grammar fan-out"假装完成。
4. 已新增/更新测试：
   - `tests/test_reader_orchestration_job_bootstrap_strategy.py`：4 个 T4.1c focused tests（short→compact、structured→compact、long→Z+ window、publish contract no regression）。新增 `_count_grammar_jobs_by_target_type` helper 按 `job_type:target_type` 统计；`_count_analysis_windows` 修正为 join `layer_analysis_plans`。
   - `tests/test_reader_orchestration_grammar_worker.py`：2 个 T4.1c batch worker tests（batch worker publishes per-unit grammar_note + sentence_analysis layers from single batch LLM call；batch worker returns None for GROUPED_WINDOWED articles）。
   - `tests/test_reader_orchestration_pipeline_runner.py`：3 个 T4.1c pipeline dispatch tests（short article → compact grammar batch path + no analysis windows；no per-unit fan-out regression；worker loop completes cleanly with `all_workers_no_job`）。
   - `tests/test_pipeline_runner_window_dispatch.py`：修正 mock 以适配 T4.1c batch dispatch（window-only fixture 下 batch worker 返回 None）。
5. 测试结果（249 passed）：`test_reader_orchestration_job_bootstrap_strategy.py` 57 passed；`test_reader_orchestration_grammar_worker.py` + `test_reader_orchestration_pipeline_runner.py` 146 passed；`test_grammar_window_worker.py` + `test_pipeline_runner_window_dispatch.py` + `test_reader_orchestration_worker_loop.py` 103 passed。
6. 风险/边界（已锁定，不扩 scope）：
   - compact grammar batch 仍复用 short-batch compute path 的 LLM 调用基础设施；真实 LLM 下的 cost / quality / latency 改善需页面验收收口，本轮只做代码级合同与测试护栏。
   - `STRUCTURED_BATCH` grammar 的 prompt / budget / release policy 当前与 `SHORT_BATCH` 共用 compact batch path；若要给 structured batch 独立 grammar budget/prompt，应在后续任务单独实现。
   - batch candidate output 无固定 `max_length`，per-unit budget（`MAX_GRAMMAR_NOTE_ITEMS` / `MAX_SENTENCE_ANALYSIS_ITEMS`）在 publisher split 后执行；超限 candidate 会被 drop 并记入 diagnostics。
   - GROUPED_WINDOWED 长文 grammar 路径（Z+ analysis-window / window-publisher）完全未改动，既有合同不回归。

#### T4.2a-R1 three-mode evidence parity and observability closure（已完成）

1. 目标：不实现 T4.2 LLM profiler、T4.3 planner 或新 UI；先让 acceptance harness 忠实复现 production topology，并让成本与 completion 数据可信。
2. 核心修复与注入：
   - **grammar batch ai_usage_events 漏传 usage_data**：`grammar_worker.py` 的 `_record_batch_usage_event` 在成功路径漏传 `execution.usage_data`，导致 `ai_usage_events` token 列恒为 0。已修复，与 per-unit 路径对齐。
   - **smoke/acceptance harness 注入 DevFakeGrammarBatchExecutor + DevFakeGrammarWindowExecutor**：`smoke_harness.py` 新增 `SmokeGrammarTopology` 类型（`"legacy"` / `"production"`）。`"production"` topology 注入 `DevFakeGrammarBatchExecutor`（覆盖 SHORT_BATCH / STRUCTURED_BATCH grammar batch path）+ `DevFakeGrammarWindowExecutor`（覆盖 GROUPED_WINDOWED Z+ window path）+ `GrammarWindowPublisher`，使 `enable_zplus_grammar=True` 时不会意外调用真实 LLM。
   - **GROUPED_WINDOWED fake worker**：`DevFakeGrammarWindowExecutor` 产出 `GrammarWindowExecutionResult` with `CandidateItem` list，使 Z+ window path 可在 fake 模式下完整执行。
   - **生产 translation/vocabulary claim + publisher 正式接受 short + structured fingerprint**：`translation_worker.py` / `vocabulary_worker.py` 的 batch claim 方法改为 `operation_fingerprint=None`（与 grammar batch worker 一致），`layer_publisher.py` 的 translation/vocabulary batch publisher 接受 SHORT_BATCH + STRUCTURED_BATCH 两个 fingerprint base（与 grammar batch publisher 一致）。此修复消除了此前测试中 `_RouteAware*` wrapper 与 DB fingerprint 临时改写的需要。
3. acceptance 路径覆盖正式 WorkerLoop + CompletionFinalizer：
   - 新增 `tests/test_reader_orchestration_three_mode_acceptance.py`（4 test functions：`test_short_batch_acceptance_through_worker_loop`、`test_structured_batch_acceptance_through_worker_loop`、`test_grouped_windowed_acceptance_through_worker_loop`、`test_short_batch_usage_event_tokens_match_runtime_span`）：覆盖 SHORT_BATCH、STRUCTURED_BATCH、GROUPED_WINDOWED 三态的 route/fingerprint/policy、job topology、effective calls、layer counts（含 GROUPED_WINDOWED sentence_analysis > 0 断言）、final readiness（`coverage_complete`）、usage attribution。
   - 测试使用生产 `TranslationWorkerService` / `VocabularyWorkerService` / `GrammarBundleWorkerService`（无 test-local 子类）和真实 layer publisher，exercise production claim/publish fingerprint checks。仅 LLM executor 被 fake。
   - 测试通过 `ReaderEnhancementWorkerLoopService.process_candidate` 执行（覆盖正式 WorkerLoop + CompletionFinalizer），而非只调用 pipeline runner。
   - 新增 `test_short_batch_usage_event_tokens_match_runtime_span`：验证 grammar batch `ai_usage_events` token 与 `reader_runtime_spans` token 一致。
   - smoke harness 新增 2 个 construction/metadata 测试（`test_real_mode_forces_production_grammar_topology_in_metadata`、`test_build_pipeline_runner_real_mode_uses_production_topology`），验证 real 模式下 `grammar_topology` 元数据强制为 `production` 且 runner 使用 `enable_zplus_grammar=True`，不调用真实 LLM。
4. 预存失败修复：
   - `test_zplus_observability.py`：T4.1c 引入 batch-first fallback 后，SHORT_BATCH 文章的 Z+ observability 测试会触发真实 LLM。已扩展 `ZPLUS_OBSERVABILITY_ARTICLE` 为 26x 重复段落（~2230 words）路由到 GROUPED_WINDOWED，并注入 `_StaticGrammarBatchExecutor` 作为安全网。所有测试的 `max_ticks` / `max_jobs` 从 30/20 提升到 100/80 适应更大文章。
   - `test_zplus_bbc_regression.py`：T4.1c 引入 route-aware routing 后，BBC 文章（858 words）路由到 SHORT_BATCH 而非 Z+ window path，导致测试失败。已添加 migration 0017（`translate_article` / `build_vocabulary_layer_article` job type CHECK constraint），注入 fake batch executors（translation / vocabulary / grammar），并扩展 BBC 文章 3x（~2574 words）路由到 GROUPED_WINDOWED 以保留 Z+ window 测试目的。测试已重命名为 `test_synthetic_expanded_long_form_grammar_window_regression`，module docstring 明确声明不再覆盖原始 BBC 3-5 window 回归；原 BBC 858-word 样本作为 SHORT/STRUCTURED 路由回归应另设测试。更新 expected window count 从 3-5 到 9-15。
5. 影响面：不实现 output-token planner、selector redesign、SSE 或页面改造；不扩到 bounded LLM profiler / strategy planner / semantic outline。
6. 风险/边界（已锁定，不扩 scope）：
   - **R1 checkpoint 的证据边界**：三态固定覆盖测试使用 fake executor，只验证代码级合同闭环。后续 T4.2a-V1 已补充真实 LLM normal-path DB/runtime 验收与 Page UX 收口；可靠实付成本与用户感知时延仍 PARTIAL，未闭环。
   - `end_worker_span_success` 不识别 aggregate usage_data 格式（仅测试 workaround `_FlatUsageGrammarBatchExecutor`）。
   - T4.2 bounded LLM document profiler **暂缓**，下一步为 T4.2a-R2 execution budget / cutover safety。

#### T4.2a-R2 three-mode execution budget and cutover safety（代码级 review 通过 — T4.2a-R2-R3a display-title regression + 文档终态同步）

1. 目标：在三态路由已经通过代码级验收的基础上，建立确定性的成本上限和 route cutover 安全机制。不引入自由运行 orchestration agent，不实现 T4.2 LLM document profiler。保证 batch-first/fallback 不会导致同一 layer 重复执行或额外 LLM 调用；route/fingerprint 切换时旧任务不能继续 claim/execute/publish；每个 route、layer、record 建立可验证的 effective-call 执行预算；预算耗尽或旧路由失效时不得错误进入 coverage_complete/final-ready。
2. T4.2a-R2-R3 review 修复背景：T4.2a-R2-R2 修复后 reviewer 仍发现 5 个 findings 未闭合（P1-1 publish fence 核心状态机未真实 transition job/run、P1-2 Test J 为空断言无法证明 fence 闭环、P1-3 full budget exhaustion 仍会误伤 display-title、P2-1 legacy cleanup 不是原子状态转换、P2-2 Test G/I 终态断言弱于报告结论）。本轮 T4.2a-R2-R3 已实施修复并已通过代码级 review；T4.2a-R2-R3a 补充 Test M（display-title budget isolation regression）并同步文档终态，**代码级验收已通过，真实 LLM / 页面验收为下一阶段 gated validation**。前轮 R2-R2 的 5 个 findings（suppressed legacy job 正式终态、partial exhaustion 分层 force-fail、publish fence 状态一致性、budget-denied 持久观测、fingerprint 确定性集合）已在 R2-R2 闭合。
3. **权威归宿（DOC-R2 收敛）**：详细预算模型、fingerprint 方案 B、fallback 决策表、publish fence 状态一致性、partial/full exhaustion 语义、budget-denied 可观测性等设计结论归 [`agent-brief.md`](agent-brief.md) T4.2a-R2 不可违反决策；budget 合同与 Layer Applicability 归 [`modules/policy-and-cost-control.md`](modules/policy-and-cost-control.md#t42a-r2-durable-executionbudget--publish-fence--route-flip-fencing)；budget diagnostics 持久化（`reader_runtime_spans.metadata_json`）归 [`modules/orchestration-runtime.md`](modules/orchestration-runtime.md#t42a-r2-budget-diagnostics-持久化)；决策记录归 [`target-architecture.md`](target-architecture.md#决策记录) `T4.2a-R2` 行。本节不再重复上述长段，只保留状态、测试证据与风险边界。
4. 影响面：不改 `apps/web/**`；不改 API route/schema 公共 contract；不新增 migration；不扩到 bounded LLM profiler / strategy planner / semantic outline / SSE / RAG / prompt cache；不实现自主 orchestration agent。核心实现文件：`execution_budget.py` / `pipeline_runner.py` / `job_runtime.py` / `completion_finalizer.py` / `repository.py` / `worker_loop.py`（详见 [`modules/orchestration-runtime.md`](modules/orchestration-runtime.md) 与 [`modules/policy-and-cost-control.md`](modules/policy-and-cost-control.md)）。
5. 已新增测试（`tests/test_execution_budget_cutover_safety.py`，**35 passed** = 12 unit + 23 async integration；仅 asyncio backend，不推算 trio 倍增。8 文件组合回归 **94 passed**）：
   - **Unit (12)**：`from_planned_calls` 默认 max_multiplier=3、显式 multiplier、consume 递减、is_exhausted、any_exhausted、zero planned、unknown layer、exhausted_layers、BUDGET_CONSUMING_OUTCOMES、to_diagnostics、has_active_jobs_for_layer。
   - **Integration (23，分 4 轮)**：
     - **R2-R1 (A-F, 6)**：跨 run hard budget durable；batch succeeded/failed suppresses per-unit fallback；partial layer exhaustion；route flip rejects translation batch publish；budget diagnostics observability。
     - **R2-R1 (16 async)**：三态 budget consistency；fallback guard decision table fail-closed；route flip supersedes at claim + rejects publish through real publisher；budget exhausted finalizable；usage evidence 区分 succeeded calls；recover stale leases；partial budget exhaustion 经 WorkerLoop + Finalizer；budget diagnostics observability 查询 `reader_runtime_spans.metadata_json`。
     - **R2-R2 (G-L, 6)**：G batch succeeded cleanup + WorkerLoop completion；H failed batch fail-closed cleanup；I partial exhaustion preserves other layers；J pipeline-level publish fence（强断言 job/run 状态 + layers/events `==` 而非 `>=`）；K persistent budget observability；L multi-fingerprint determinism（方案 B 稳定排序）。
     - **R2-R3a (M, 1)**：M full budget exhaustion preserves display_title（全预算层 exhaustion 时 display_title 不被误伤；finalizer `non_terminal_jobs_present` → 二次 WorkerLoop display_title succeeded → `completed_with_failures`）。
6. 风险/边界（已锁定，不扩 scope）：
   - **R2 与 V1 的证据边界**：执行预算与 cutover failure-path 仍由 deterministic fake-executor tests 验收；V1 真实 LLM records 只覆盖首次成功的 normal path，没有触发 retry、budget exhaustion 或 route cutover。durable budget formalizes `max_attempts=3` 的确定性上限，本身不降低当前 retry ceiling；是否将某些 route/job 从 3 次调整为 2 次需要固定样本 cost/quality 对照。当前仍不得表述为已经实现实际降本增效。
   - **max_attempts=3 与 durable budget 的关系**：durable budget 使用 `SUM(max_attempts)` 作为 layer ceiling（`max_multiplier=3` 与 `max_attempts=3` 对齐），提供确定性上限和观测/调度作用，**本身不降低原有 retry 成本**。是否调整 `max_attempts` 应作为真实 LLM cost/quality 数据驱动的独立决策。
   - **fingerprint 方案 B 限制**：保守集合将所有非 superseded fingerprint 的 consumed 合并计算，在 cutover 中间态可能略高估 consumed。这是保守偏差，符合 fail-closed 原则。若未来需要精确按 active route 分离预算，需新增 schema 字段标记 active route，不在本轮实施。
   - route flip fencing 依赖 `article_route` 写入 `reader_runs.envelope_json`（由 job_bootstrap 写入）。若未来有 run 不携带 `article_route`，fencing 不生效（返回 None = 通过）。这是预期行为：只有 T4.1b+ 的 batch/window run 才需要 route fencing。
   - 预算耗尽后的 force-fail 不尝试重跑 jobs；被 force-fail 的 jobs 在 v1 不提供 retry 入口。如需 retry，应作为 T4+ 的 action-required UX 单独设计。
   - `attempt_count` 在 claim 时自增，含 claim 后但 LLM 调用前崩溃的 attempt；durable consumed 是 claim 次数上限，可能略高于真实 effective LLM call 数。这是保守偏差，符合 fail-closed 原则。

### T4.2a-V1. Gated Real-LLM Validation

状态：**closed**。Contract / Output Integrity / sample-level Semantic Quality / Page UX PASS；Cost/Latency Baseline PARTIAL。

- 4 个真实 records 覆盖 `SHORT_BATCH`（2）、`STRUCTURED_BATCH`（1）和 `GROUPED_WINDOWED`（1）：34 effective calls，142,990 input tokens，55,051 output tokens，198,041 total tokens。所有 jobs 首次 claim 成功（`attempt_count=1`），无 retry、budget denial、stale/superseded publish 或 duplicate fallback；最终均为 `coverage_complete / completed_clean`。
- 首个 route baseline：SHORT 526 words = 4 calls / 9,966 tokens / pipeline-root 44.7s；SHORT 986 words = 4 / 29,996 / 80.7s；STRUCTURED 1,509 words = 4 / 46,506 / 103.7s；GROUPED 2,515 words = 22 / 111,573 / 204.7s（4 translation + 6 vocabulary + 11 grammar windows + 1 display title）。这些是当前配置的 baseline，不是旧链路对照。
- Gate：Contract PASS；Output Integrity PASS；Semantic Quality PASS（仅代表本轮人工抽查样本：translation 20、vocabulary subtype-aware 17、grammar 12、sentence analysis 8）；**Page UX PASS**；Cost/Latency Baseline PARTIAL。DB 非空或 schema 合法不能单独证明语义质量，当前 PASS 不可外推到碎段新闻、超长文或其他模型配置。
- **Page UX 收口（T4.2a-V1-PV / R1）**：在正式 baseline commit `760402c2c` 的 clean worktree Web 上验收，不新增真实 LLM 调用。4/4 页面 final-ready rendering 通过；33/33 严格 click→active mark→explanation/card 断言通过；GROUPED 中后段 vocabulary / grammar / sentence interaction 通过；刷新一致性、internal scroller 无 reset-to-top、readiness 与 durable DB `coverage_complete` 一致、无阻断性 console/network error。Page UX Gate 已关闭。
- 实际 provider 账单不可得，可靠实付成本为 unavailable；按当时配置价格与无法完整归因的 cache 情况，只能给出约 `$0.0158-$0.0354` 理论区间。无历史同样本真实 LLM baseline，`ai_usage_events.latency_ms` 不完整，因此**不得宣称已实现 token、成本或时延降幅**。
- Observability gap：Sample A grammar usage event 存在但无 `usage_snapshot`，recording 层收到 `usage_data=None`；同一路径 B/C/D 正常。`extract_run_usage(result)` 在 V1 前已经存在，根因仍为 unresolved intermittent usage attribution gap，不能描述为 worker 修复已落地。per-job provider latency 与真实用户感知延迟尚不可用。
- Vocabulary 质量必须按 discriminated union 评估：`vocab_highlight` 检查 `headword` / `brief_explanation` / `reason`；`phrase_gloss` 检查 `phrase` / `phrase_type` / `gloss`；`context_gloss` 检查 `display` / `gloss` / `reason`。不得跨 subtype 强求不存在的 `headword` 或 `gloss` 字段。
- **Progressive Transition UX**：final-ready V1 records 本身不能证明实时过渡；**T4.2a-PUX-R1** = fixture 合同；**T4.2a-PUX-R2** = 页面 runtime 集成门（均已闭合，见下方），不重跑 LLM、不改 V1 范围。
- 碎段新闻、超长文、no-op window 已由 **T4.2a-V2-R1** 完成 deterministic 合同验证（见下）；真实 LLM / 页面验收仍后置。T4.2 bounded LLM document profiler 继续暂缓，只有 deterministic router 出现稳定边界误判时再评估。

#### T4.2a-PUX-R1 progressive transition UX fixture / event replay（fixture contract closed）

1. **口径**：仅 pure fixture / event-replay **合同**；**不是**页面 runtime 验收。
2. 相位与安全 helpers：`progressive-transition.ts`（21 tests）。
3. Runtime 必须由 **T4.2a-PUX-R2** 接入真实 polling / snapshot reload。

#### T4.2a-PUX-R2 progressive transition runtime integration（runtime gate closed）

1. 目标：把 PUX-R1 合同接到 `reader-record/[recordId]/page.tsx` 真实 `useReaderPlatePolling` + `reloadSnapshot` 路径；用户可见「正文可读 → 译文先到 → 批注逐步丰富 → 完整解析」；刷新不打断 scroll/selection/Quick Peek。
2. **单 cursor**：polling 仍唯一；只在 progressive `applySnapshotReload` **成功**后更新 `snapshotState`（从而推进 `initialCursor`）；stale / layer regression 拒绝时 return `false` → cursor hold。
3. **UI**：底部固定轻量 status pill（`reader-record-progressive-status`），无全屏覆盖、不挤正文。
4. **Plate**：generation 变化清 generation-scoped interaction，保留 scroll；plateValue swap 继续 restore scroll/selection path。
5. **Runtime tests**（page.test.tsx）：layer_published 成功路径；stale 拒绝 + cursor hold；layer regression 拒绝；scroll 保留。共 4 PUX-R2 tests。
6. 验证：67 tests（21 progressive + 18 polling + 28 page）；`pnpm --filter=@claread/web typecheck` clean。
7. 过程 TMP 已在 DOC-R3 中删除（结论已压缩进本节及 [`adaptive-reader-orchestration-design.md`](adaptive-reader-orchestration-design.md) §8）。
8. 未做：SSE、patch merge、Playwright progressive E2E、真实 LLM。

#### T4.2a-V2-R1 three-mode boundary & very-long fixed samples（已完成 / deterministic）

1. 目标：在不调用真实 LLM、不改 router 阈值/prompt/model 的前提下，用固定边界样本锁定三态 route、job topology、fingerprint/policy、publish/readiness 与 no-op grammar window 合同。
2. 样本矩阵：
   - **碎段新闻**（golden `fragmented_news.txt`，≈263 words）→ `SHORT_BATCH`：4 条 whole-article batch jobs；无 `:window:` / 无 per-unit fan-out；Translation Group 保持 anchor 成员与非 one-anchor-one-group；effective = planned = 1/层。
   - **STRUCTURED 边界**（≈1540 words，UTF-16 ≤ 12000）→ `STRUCTURED_BATCH`：topology 与 short 同为 4 batch jobs，但 fingerprint/policy 为独立 `*_structured_v1` / `*_structured_bootstrap_v1`；compact grammar batch，无 Z+ window。
   - **超长文 >4000 words** → `GROUPED_WINDOWED`：translation/vocabulary/grammar 均为 multi-window；translation/vocabulary jobs 断言 `article_route=grouped_windowed` 与 `:window:` target key；legacy Z+ grammar job 断言 `grammar_bundle_window_v1` 且 `target_key == input_json.window_id`（window UUID identity）；effective calls = planned window job 数（first-success）；translation layers 按 `reading_units.order_index` 阅读序；允许 grammar density 导致部分 window `no_op` 与 `completed_with_no_op`。
   - **no-op grammar window**：empty executor → 全部 analysis window `status=no_op`、`no_op_cause=llm_empty`；window job `succeeded` + `attempt_count=1`（不重试）；executor call_count == planned windows；`coverage_complete` / `completed_with_no_op`；grammar layers=0，trans/vocab 仍 full publish。
3. Calls 口径：planned = bootstrap job 数；effective = fake executor 实际调用次数；max ≈ planned×3（R2）；no-op **计 1 次 effective call**，但不产生重复 claim/retry。本任务不声明真实 token/成本。
4. 测试：`tests/test_reader_orchestration_v2_boundary_samples.py`（5 passed）；ruff F,I 与 `git diff --check` clean。**无生产代码改动**；未发现需先红后绿的 route/合同缺陷。
5. 过程 TMP 已在 DOC-R3 中删除（结论已压缩进本节及 [`adaptive-reader-orchestration-design.md`](adaptive-reader-orchestration-design.md) §4.2）。
6. 未解决：真实 LLM 下超长文 no-op 比例/质量；Page UX；跨 window `published_at` 全局序（T3.1 风险 A 既有锁定）；Sample A / Cost-Latency PARTIAL 不变；T4.2 profiler 仍暂缓。

### T4.2a-O1. Usage / Cost / Latency Observability Contract Audit

状态：**closed / read-only audit complete**。只读合同审计已完成；Cost/Latency Gate 仍为 PARTIAL；Sample A grammar 0-token 根因 **UNRESOLVED**。

- 目标：从 provider/agent result 开始，逐层核对 `extract_run_usage`、worker execution result、`ai_usage_events`、`reader_runtime_spans`、job/run/event 时间戳与页面 readiness，明确每个成本/时延字段的生成者、转换、持久化位置、消费者和缺失语义。
- 必须解释的已知缺口：Sample A grammar usage event 有记录但无 `usage_snapshot` / token；其他 samples 同路径正常；`ai_usage_events.latency_ms` 不完整或为 NULL；cache hit/miss 字段分散在列与 `metadata_json.usage_snapshot`；缺少可靠 provider bill 与前端用户感知时间。
- 权威口径：区分 estimated 与 billed cost；区分 input/cache-read/cache-write/output/reasoning tokens；区分 provider latency、worker duration、pipeline-root duration、submit→first-layer、per-layer-ready、coverage-complete 与 browser-perceived latency。禁止用 claim `attempt_count` 代替 effective provider call，也禁止用 pipeline wall-clock 伪装 provider latency。`(reader_job_id, reader_run_id)` 只能标识 job，无法区分同一 job 的多个 retry。
- 交付：字段 lineage、现状矩阵（available / derivable / missing / unreliable）、四个 V1 records 的只读 evidence table、根因已证/未知边界、建议的最小后续实现切片及对应 deterministic tests。若无法从现有证据确定根因，必须标记 unresolved，不得推测“当前代码已修复”。
- 不在本轮决定 provider/model/window/retry 优化，不调整成本策略，不实现 dashboard，不进入 Progressive UX、固定样本 V2、T4.2 profiler 或 T4.3 planner。

### T4.2a-O2. Usage Presence Diagnostics & Durable Execution Correlation

状态：**T4.2a-O2-V1-R1 closed。** 隔离 `SHORT_BATCH` record `f0a9163f-5a76-4fed-9974-1cd75b15d737` 在唯一 harness lease 下完成 4/4 jobs、`coverage_complete` 和四层 correlation：`attempt_ordinal=1`、`execution_id` / `agent_run_id` 非空且 event/span 一致、tokens 一致、无 retry/stale/superseded。`run_reader_scoped_agent` 已覆盖全部 Reader layer 真实 `agent.run`；validation isolation preflight 在进程枚举不可用时 fail closed，并记录 Git HEAD、workspace root、目标源码 SHA-256 与 dirty target-slice，运行后拒绝任何 foreign lease owner。Sample A 根因仍 **UNRESOLVED**；Cost/Latency Gate 仍为 **PARTIAL**。

- 目标：为 Sample A grammar 0-token gap 建立可诊断、可跨 retry 关联的运行证据；只做 usage presence diagnostics 与 execution correlation，不实施 latency / cache pricing / estimated cost / readiness milestone / Browser RUM。
- **Correlation 合同**：`attempt_ordinal` = claim 后 durable `reader_jobs.attempt_count`；`execution_id` = 每次 claimed-job worker execution 新 mint 的 UUID；`agent_run_id` = 每次 `agent.run()` 新 mint 的 UUID（不得冒充 provider request id）。关联键推荐 `reader_job_id + attempt_ordinal + execution_id`。
- **Grammar window 生命周期（O2-R1a）**：`execution_id` 在 `pipeline_runner._run_grammar_window_attempt` claim 成功后由外层 `bind_execution_from_claim` 绑定，覆盖 process_window_job → publish → usage event → terminal span/transition；`process_window_job` **不再** 挂内层 correlation decorator（避免同一 attempt 双 `execution_id`）。
- **持久化**：无 migration。correlation 写入 `ai_usage_events.metadata_json` 与 `reader_runtime_spans.metadata_json`（schema kind `usage_execution_correlation` / version `1`），集中构造于 `app/services/ai_usage/execution_diagnostics.py`。
- **Diagnostics**：在 adapter / event DTO / normalize / event persist / span write 边界记录结构化诊断码。不写 prompt、article、raw provider response、API key。
- **Mismatch**：同一 execution 的 event 与 span token totals 不一致时只记 diagnostic，不自动篡改任一侧、不阻止 annotation publish。
- **覆盖**：translation / vocabulary / grammar unit+batch / display title 在 worker `process_claimed_*` 绑定；grammar window 在 pipeline_runner claim 后外层绑定。
- **测试证据**：reviewer 现场执行 `uv run pytest tests/test_usage_execution_diagnostics.py -q` 得到 **62 passed**；`uv run pytest tests/test_grammar_window_worker.py -q` 得到 **25 passed**；完整 O2 target slice 的 Ruff `F/I` 与 scoped `git diff --check` 通过。不得沿用过期的 53/71/83/96 passed 或 anyio-backend-inflation 口径。
- 不得写“token/cost/latency 已可靠”；不得将 Cost/Latency Gate 改为 PASS。

### T4.2a-O3. Duration Provenance & Provider-Request Observability

状态：**代码级完成（deterministic tests）**。未调用真实 LLM；未改 `ai_usage_events.latency_ms` 语义；Sample A 仍 **UNRESOLVED**；Cost/Latency Gate 仍 **PARTIAL**。

- **字段字典（metadata_json，schema `duration_provenance` v1）**：

| 字段 | 测量边界 | 时钟/来源 | 持久化 | 可否代表 provider duration |
|------|----------|-----------|--------|---------------------------|
| `agent_run_duration_ms` | 本地 `agent.run` 包裹 | `time.perf_counter`（local monotonic） | event/span metadata | **否** |
| `agent_run_duration_source` | 固定 `local_monotonic` | 标签 | 同上 | 否 |
| `agent_run_duration_boundary` | 固定 `agent.run` | 标签 | 同上 | 否 |
| `provider_request_duration_ms` | 仅专用 adapter envelope | `result._claread_provider_response_timing` | 同上 | **仅当 status=available** |
| `provider_request_duration_status` | `available` / `unavailable` | 派生 | 同上 | — |
| `provider_request_duration_source` | `provider_adapter_envelope` / `none` | 派生 | 同上 | — |
| `provider_request_duration_field` | envelope 内 duration key | 字符串 | 同上 | — |

- **非声明**：worker_tick `duration_ms`（PG wall）、pipeline-root duration、claim wait **不是** provider latency；**不得**把上述任一值写入或冒充 `latency_ms`。
- **实现落点**：`run_reader_scoped_agent` 计时 + `DurationProvenance`；`merge_correlation_metadata` 合并 duration；success/failure span 与 usage event 持久化；异常路径仍保留 agent-run duration 与 provider unavailable。
- **Provider timing fail-closed**：只有 `kind=claread_provider_response_timing` / `version=1` 的 adapter envelope（`make_provider_response_timing_envelope`）可使 status=`available`。任意 `usage_data`、`usage.details`、`result.timing` / `response_timing` / 同名字段 **一律 unavailable**。
- **历史任务证据归属（DOC-R2 收敛）**：T2.1 progressive reload cursor、T3.3 phrase_gloss guard、T3.4a grammar window diagnostics、T3.4b RECORD_DENSITY bug fix、2026-07-08/09 fake baseline 与长文真实抽查记录（`5afd3f93` / `67ad6b9f` / `318d5fa1` / `0108ecca` / `4f6ad1cd` 等）的证据与结论归各任务对应章节与 TMP closeout；本节只保留 O3 duration provenance 字段字典与 fail-closed 合同。

### 下一轮建议

1. T3.5、T4.1、T4.1a、T4.1b、T4.1c、T4.2a-R1 已完成代码级实施；**T4.2a-R2 已通过代码级 review（T4.2a-R2-R3a 补充 Test M + 文档终态同步）**。核心闭合项：completion finalizer、deterministic 三态 router（替换 legacy raw-char 分流）、`STRUCTURED_BATCH` 独立 runtime mode、compact grammar batch path、acceptance harness 复现 production topology、durable per-layer 执行预算与 route cutover fencing。详细设计结论与权威归宿见上方 T4.2a-R2 章节第 3 项。下一步不再回头扩这几项 scope。
2. **T4.2a-V1 已正式关闭**：Contract / Output Integrity / sample-level Semantic Quality / Page UX PASS；Cost/Latency Baseline 仍 PARTIAL，真实降本增效尚未被证明。**T4.2 bounded LLM document profiler 继续暂缓**。
3. 原 T4.2a 实现阶段约 **85%**：实现与合同层已闭合；Sample A 仍 UNRESOLVED、Cost/Latency 仍 PARTIAL、PUX rejected-snapshot retry/backoff 仍待设计。下一阶段唯一预批准动作是 **T4.2a-LP-R2 Phase 0 snapshot payload profiling**（测量而非传输改造）：按文长、generation、surfaceMode、reload reason 记录 payload 与用户感知耗时。旧的 bounded-LLM document profiler 继续暂缓；不得把 ETag、压缩、SSE、fragment、planner 或 T6 patch merge 混入 LP-R2。
4. T4.2 bounded LLM document profiler 与 T4.3 strategy planner 仍作为后续阶段：bounded profiler 只返回 genre/structure/schema_risk/selective hints；planner 选择 short batch、structured batch、grouped/windowed、section longform、selective longform。T4.1b/T4.1c 的三态可审计 runtime mode + compact grammar batch path 是 planner 的稳定输入。只有 deterministic router 在真实边界样本上出现稳定误判时才重新评估 profiler。
5. bounded enhancement planner + specialized structured workers 的第一落点仍应是 long/very-long selective enhancement：planner 只负责选择候选 enhancement targets，专业 worker 负责 schema output 与 publish；translation semantic group planner 继续保持独立。
6. **T5.1 L0/L1 deterministic navigation 已闭合**（前端 projection + rail + Chromium；T5.1e 文档同步）。L1 是 accepted snapshot 上的本地 flat heading 投影，**不是** semantic outline。
7. **T5.2a + T5.3 已闭合**（commits `2bf3db97` / `781e4117`）：semantic outline 以 `enhancement_layers` record/`document` durable layer 存在；默认不请求；不进既有 ExecutionBudget / coverage_complete 必需路径；不挂 `ReaderPlateSnapshot`。下一导航相关任务是 **T5.4-R0 snapshot projection 设计门**，不是直接做 UI 或 lazy section。
8. Provider prompt cache / cache-hit 归因继续作为成本优化项跟踪，但不作为当前三模式架构是否成立的前提。
9. 继续跟踪真实测试的 token、耗时、首个可用输出时间和输出质量，但避免每个局部补丁后真实跑长文或超长文。

### 暂缓项

- 不先做完整 adaptive planner。
- 不在 T5.4-R0 设计门前实现 semantic outline snapshot projection 或 Reader UI（T5.4 / T5.5）。
- 不冻结 request eligibility 阈值、L2 UI IA、partial 节点混排产品细节。
- 不先做 SSE patch merge。
- 不在 short/long/very-long 三种模式代码级闭环前，频繁真实跑长文或超长文页面验收。
- 不把短文 batch 的 whole-article computation 理解成 whole-unit translation display。
- 不用前端按标点或句子拆译文来修复后端 group 输出错误。
- 不把 bounded enhancement planner 一开始扩到全部 layer；优先先管 long/very-long 的 vocabulary/grammar selective enhancement，再根据证据判断是否扩到 translation。
- 不把 provider prompt cache 当作三模式路由替代品；它是成本优化杠杆，不是模式设计本身。

### Cutover milestone closure（2026-08-03，CUTOVER-DOC-TRUTH-CLOSEOUT-R1）

Architectural Cutover 已完成：旧 Learning Workflow、Analysis Ask、Ask legacy lane、旧 Web/Mini 页面、旧 Directus Eval Center / Workflow Lab / Node Lab 已注销并物理删除，Reader 与 Ask 主链已单轨化。早期版本中"不先删除旧 AI Workflow / 旧链路仍是质量和成本对照基线"的暂缓项已由 cutover 落地事实闭环，不再适用。

DOC-R2 期间曾登记的"同日后端闭环 review verdict 冲突"（`docs/tmp/reader-orchestration/review/reader-orchestration-backend-closure-review-2026-07-01.md` 与 `closed-loop-review-2026-07-01.md`）已由 cutover 落地事实闭环——旧生产链已物理删除，前端 cutover 不存在回退路径，6 P0 findings 不再需要逐项裁定。两份 review TMP 仍按 TMP 生命周期规则处置，不作为长期事实来源；如需回看 verdict，应在 TMP 中查阅，不在正式文档中写为唯一结论。

### Post-cutover backlog（不在本文展开任务细节）

以下事项已登记为 post-cutover backlog，由后续任务单独推进；不在本文写成已完成：

- 12 张旧 Eval 表与 `analysis_*` 数据层清理（DATA-AUDIT）。
- Console / Eval 按新 orchestration 重建（治理化控制面）。
- 统一监测、计费适配、usage/ledger 与新 Reader run/job/layer attribution 闭环。
- Test Governance 与代码架构优化（TEST-GOVERNANCE、ARCH-OPT-AUDIT）。

> **DOC-R2 边界（历史保留）**：在用户裁定前，`cutover-and-old-workflow.md` 的 DOC-R2 代码现场核验结论只陈述当前代码事实（旧依赖审计、delete/rewrite/keep matrix），不采用任一 TMP review 的 verdict 作为正式结论。Cutover 落地后该边界已无回退路径，但 DOC-R2 的"代码事实优先于 TMP verdict"原则继续生效。

### 任务派发规则

- `implementation-plan.md` 只记录任务拆分、依赖、状态和验收口径，不保存每轮 coding agent prompt。
- 每轮 prompt 由人工根据当前代码事实和最近验收结果单独生成，并通过会话发给 coding agent。
- 当前 T1.1a、T3.1、T3.2b、T3.3、T3.4a、T3.4b、T3.5、T4.1、T4.1a、T4.1b、T4.1c、T4.2a-R1、T4.2a-R2 已完成代码级实施与 deterministic acceptance；**T4.2a-V1 已正式关闭**；**T5.1 L0/L1**、**T5.2a validation contract**、**T5.3 semantic outline worker durable** 已闭合（详见任务表与下方 T5.3 章节）。导航相关下一步优先 **T5.4-R0 semantic outline snapshot projection 设计门（只读）**；不要把 snapshot projection / UI / lazy section / SSE patch / planner 混成一个无边界任务。
- 评审 agent 完成 review 后，如发现实现改变了任务状态、产品合同或执行顺序，必须同步更新本计划和相关模块合同。

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

## 阶段门禁

| Phase | 名称 | 门禁 |
|---|---|---|
| D0 | 边界决策 | scope、数据重置、Web first、framework spike、queue posture 已记录 |
| D0.5 | 接入边界确认 | RAG、输入适配、orchestration 入口三类粗合同已写入目标架构 |
| D1 | 架构 RFC | schema、runtime、events、milestones、product states 可评审 |
| D2 | 技术 spikes | 依赖升级、DB job lease、SSE/polling、bounded worker、RAG/OCR/OSS、Length Class、成本基线有结果 |
| D3 | 后端骨架 | 新 schema、domain services、run/job 状态机、events、usage audit 编译并通过 focused tests |
| D4 | 最小纵切 | Web submit -> article_ready -> translation layer -> parsed decision -> progressive Reader display |
| D5 | 增强扩展 | vocabulary、grammar bundle（grammar_note + sentence_analysis）、summary/outline policy、anchor validation、repair、eval |
| D6 | 产品硬化 | Candidate preview、Library states、quota/action_required、Ask sidecar actions、failure recovery |

## D0. 边界决策

状态：已完成，后续只接受评审修正。

完成标准：

- 本专项目录存在，并作为专项权威上下文。
- TMP 研究材料已标记为仅作证据库。
- 旧开发记录迁移已移出本轮约束。
- 词典三表保护已明确。
- Web 优先与小程序暂缓已明确。
- Daily Reader 不进入本轮重构已明确。
- Academic workflow 暂缓重构，待 learning workflow 验证稳定后再单独设计。
- runtime 首选 PostgreSQL-backed job state，外部队列等 spike 结果后再决定。

## D0.5. 接入边界确认

状态：已完成，结论已写入 `target-architecture.md`。

目标：在 D1 schema/runtime 设计前，先确认 RAG、输入模式、orchestration 接入三类粗合同，避免 D1 反复返工。

完成标准：

- RAG 只服务当前 Reading Record，不做全局 User Editorial Assets RAG。
- RAG substrate 不阻塞 `article_ready`，归入 `substrate_ready`。
- 测试阶段 RAG vector store 初步选择 Zilliz Cloud；上线前评估阿里云 RAG / 向量检索服务。
- 所有 RAG 供应商通过 adapter 隔离。
- 文本、URL、PDF、OCR、文件上传统一进入 Input Adapter。
- OSS / OCR / 文档解析只产生 Source Artifact / Extraction Result / Candidate Base，不直接写 Stable Base。
- 测试阶段文件上传使用阿里云 OSS；上线目标为 OSS + CDN。
- OCR / 富文档解析优先评估阿里云百炼图像理解、Qwen OCR/VL 和文档解析能力。
- Orchestration 入口必须是 bounded run/job，不是页面常驻 thread。
- Ask sidecar action 走同一 Authorization Envelope。

## D1. 架构 RFC

状态：草案已形成；R12 policy/cost 合同已补；可进入 D2 spikes。

交付物：

- 完善 `target-architecture.md`。
- 完成 `concepts.md`，统一概念口径。
- 完成 `modules/` 下的模块合同文档。
- 定义 D1 模块边界。
- 设计数据模型和 API contract。
- 定义 Reader milestones 与状态流转。
- 定义 layer publish policy。
- 定义 Authorization Envelope。
- 定义 input adapter、source artifact、extraction result。
- 定义 RAG substrate 与 adapter contract。
- 定义 worker/run 失败语义。
- 定义 streaming event envelope、domain events、snapshot/polling fallback。
- 定义 Plate.js Article Body、Base Plate Snapshot、projection operations、owner 权限和 document tools。
- 定义旧 AI Workflow 的复用、改造和隔离边界。
- 定义 Policy Planner、Semantic Reviewer、Skip Gate、Model Profile、Prompt Cache 和 Usage Bucket。
- 定义 eval 与 observability hooks。

完成标准：

- coding agent 不读 TMP research 也能基于 RFC 实现 backend skeleton。
- 待决问题与已接受决策分离。
- 硬约束都能转成测试或 schema 校验。
- D2 spike 的输出只用于校准技术选型、版本和参数，不再改变 Reading Record / Stable Base / Event / RAG 的核心合同。

评审重点：

- Product state、run/job state、event/projection state 是否足够分离。
- Candidate Reading Base 是否覆盖 PDF/OCR/网页抽取的 source loss 风险。
- `article_ready` 是否足够轻，不被 RAG、全文增强或 Semantic Outline 阻塞。
- API / BFF contract 是否能支持刷新恢复、渐进渲染和 Library states。
- 旧 workflow 处理策略是否避免把 `analysis_tasks` 和 `render_scene_json` 语义带入新架构。

## D2. 技术 Spikes

状态：D2-P0~P4 / D2-S1 已 accepted / accepted_with_changes；D2-S2~S10 标记为 ready 但结论已落地到正式文档（见 [`spikes/README.md`](spikes/README.md#doc-r2-状态核验结论2026-07-13) 映射表）。D2-P0 Plate dependency 已通过并对齐 Web 依赖到 Plate 53.x 稳定主线；D2-P1 到 D2-P4 与 fragment sanitize 的调研结论已由 TMP disposition 汇总，正式合同以本目录模块文档为准。

**必做 spike 清单与状态归 [`spikes/README.md`](spikes/README.md)**（DOC-R2 收敛：不再在此复制 spike 表格；spike 输出只记录短结论，被接受的长期结论写回 [`target-architecture.md`](target-architecture.md#决策记录) 或对应 [`modules/`](modules/) 文档）。

完成标准：

- 每个 spike 只产出短结果，写回本计划或 PR summary。
- spike 不新增长期设计文档；需要固化的结论写回 `target-architecture.md`。

## D3-P0. Backend Dependency Alignment

状态：completed on 2026-06-18，详细记录见 `docs/tmp/reader-orchestration/D3/TMP-D3-P0-backend-dependency-closeout.md`。

Closeout 结论：

- 依赖升级已落到 `services/api/pyproject.toml` 与 `services/api/uv.lock`。
- PydanticAI 升级到 `1.107.0`，DashScope SDK 升级到 `1.25.23`，asyncpg 升级到 `0.31.0`。
- LangGraph 保持 `0.6.11`，D4/D5 主路径不引入 LangGraph；LangGraph v1+ 只作为 D6+ complex repair / branching / interrupt 隔离 spike 候选。
- FastAPI、LangSmith、OpenAI SDK 未升级；focused tests 未暴露必须升级的缺口。
- asyncpg job lease、record-scoped event counter、rollback no-gap、SSE `Last-Event-ID` 等运行时语义延后到 D3-P4 在新 schema 上测试。

**原任务定义（任务包 + 完成标准）归 TMP closeout；本节只保留 Closeout 结论作为历史证据。**

## D3. 后端骨架

Schema / Domain Contract：见 `modules/schema-and-domain-contract.md`。D3-P1 到 D3-P4 的四份 TMP 评审和两份 D3 contract review 已合并为该正式合同；实现以正式合同为准，不以 TMP 中的 `ReaderPlateSnapshotV2` 等临时命名为准。

### D3-P1. Schema Baseline

状态：completed on 2026-06-19，详细记录见 `docs/tmp/reader-orchestration/D3/TMP-D3-P1-schema-baseline-closeout.md`。

Closeout 结论：

- Fresh baseline migration 已新增 D3-P1 最小 Reader tables：`reading_records`、`original_inputs`、`reading_bases`、`reading_units`、`anchor_segments`、`reader_runs`、`reader_jobs`、`reader_job_events`、`reader_event_sequences`、`reader_events`、`enhancement_layers`、`parsed_decisions`。
- `ai_usage_events` 和 `user_credit_ledger` 已增加 nullable Reader attribution 字段。
- `reader_jobs` 已用 `(base_id, reading_record_id, expected_generation)` 复合 FK 绑定 `reading_bases(id, reading_record_id, record_generation)`。
- 只有 `job_type='build_base' AND target_type='record'` 可 `base_id IS NULL`；其他 job 必须带 base。
- `enhancement_layers` 已用 `(base_id, reading_record_id, generation)` 复合 FK 绑定 base generation。
- `reader_event_sequences` 使用 record-scoped counter，focused tests 覆盖 first sequence 和 rollback no-gap。
- `check_schema_baseline.sql` 已覆盖 D3-P1 全部新表。
- `active_base_id -> reading_bases.status='active'` 暂不做 DDL trigger，作为 service / publisher invariant。
- Focused tests 已通过：`test_reader_orchestration_schema_baseline.py`、`test_reader_orchestration_schema_models.py`、`test_jsonb_storage_contract.py`。

### D3-P2. Reading Base Builder + Base Plate Snapshot

状态：completed on 2026-06-19，详细记录见 `docs/tmp/reader-orchestration/D3/TMP-D3-P2-reading-base-builder-snapshot-closeout.md`。

Closeout 结论：

- 已实现低影响纯文本路径的 deterministic Reading Base Builder。
- 已从 Stable Base 生成 Reading Units、Anchor Segments 和 Navigation Skeleton。
- 已实现 D4 所需 `ReaderPlateSnapshot` serializer / Base Plate Snapshot builder。
- Snapshot builder 只从 domain facts 生成 Plate `value`，不读取旧 `render_scene_json`。
- Snapshot builder 会拒绝不属于当前 base / unit / anchor 的 layers、parsed decisions、ask supplements 和 user assets。
- 已实现最小 published translation layer snapshot projection，并补充 top-level layer 与 Plate value 对齐测试。
- 当前 Unit baseline 是 `1 structure block -> 1 reading unit`；target-length aggregation 留给 D5+ builder refinement。

D3-P2 不包含：

- Translation Worker。
- 数据库持久化 service。
- 公开 Reader API。
- Layer Publisher。
- `projection_ops` 端到端 applier。
- Web Reader UI 接入。
- LangGraph 或 LLM Planner。

Focused tests 已通过：

- `test_reader_orchestration_base_builder.py`
- `test_reader_orchestration_schema_models.py`
- `test_reader_orchestration_schema_baseline.py`
- targeted `ruff check`
- targeted `compileall`

### D3-P3. Article Ready Persistence Service

状态：completed on 2026-06-20，详细记录见 `docs/tmp/reader-orchestration/D3/TMP-D3-P3-article-ready-persistence-closeout.md`。

Closeout 结论：

- 已把 D3-P2 builder / snapshot 接到 D3-P1 schema。
- 已实现纯文本低风险提交的内部 application service。
- 已在一个事务内创建 `reading_records`、`original_inputs`、`reading_bases`、`reading_units`、`anchor_segments`。
- 已设置 `reading_records.active_base_id`，并显式校验 active base 属于同一 record、同一 generation 且 `status='active'`。
- 已初始化 `reader_event_sequences` 并写入 `reader_events.event_type='article_ready'`；sequence 从 `1` 开始，rollback 不产生 gap。
- 已将 `readiness_state` 推进到 `article_ready`，将 `product_state` 推进到 `readable_enhancing`。
- 已从数据库 facts 重建 `ReaderPlateSnapshot`，而不是复用提交时的内存对象。
- Snapshot reload 使用 read-only `repeatable_read` transaction，保证 `last_event_sequence` 与 domain facts 来自同一 consistent read。
- DB hydration 后调用 `validate_reading_base_build_result` 作为 Reading Base / Unit / Anchor Segment 全局 invariant 校验入口。

D3-P3 不包含：

- Translation Worker。
- run/job worker lease runtime。
- Layer Publisher。
- Web Reader UI 或 FastAPI 公开接口纵切。
- LangGraph、LLM Planner 或 PydanticAI worker。
- `projection_ops` 端到端 applier。

Focused tests 已通过：

- `test_reader_orchestration_article_ready_service.py`
- `test_reader_orchestration_base_builder.py`
- `test_reader_orchestration_schema_models.py`
- targeted `ruff check`
- targeted `compileall`

### D3-P4. Runtime Skeleton

状态：completed on 2026-06-20，详细记录见 `docs/tmp/reader-orchestration/D3/TMP-D3-P4-runtime-skeleton-closeout.md`。

Closeout 结论：

- 已在新 D3 schema 上实现最小 Reader run/job/event runtime 骨架。
- 已实现 claimable `reader_jobs` helper：`SELECT FOR UPDATE SKIP LOCKED`、lease token、lease expiry、attempt count、heartbeat。
- 已实现 stale claimed job recovery 和 `retry_later` 调度语义。
- 已实现 base/generation fence：claim/publish 拒绝 stale generation、非 active base、`active_base_id != job.base_id` 或 lease token mismatch。
- 已抽出 event publisher helper：在 publish transaction 内使用 `reader_event_sequences` 分配 committed UI sequence 并写入 `reader_events`。
- 已实现 polling event read model：`after_sequence`、`limit`、`last_event_sequence`、truncated response、empty stream、cursor already caught up 和 gap/reload 语义。
- D3-P4 保持不引入 LangGraph；runtime 主控仍是 PostgreSQL run/job/event。

D3-P4 不包含：

- 实际 Translation LLM Worker。
- PydanticAI worker 调用。
- Layer Publisher 业务发布完整逻辑。
- Web Reader UI。
- `projection_ops` 端到端 applier。
- LangGraph planner 或 branching flow。

Focused tests 已通过：

- `test_reader_orchestration_job_runtime.py`
- `test_reader_orchestration_event_runtime.py`
- `test_reader_orchestration_schema_baseline.py`
- targeted `ruff check`
- targeted `compileall`

### D4-P0. Backend Reader API + Snapshot/Polling Vertical Slice

状态：completed on 2026-06-20，详细记录见 `docs/tmp/reader-orchestration/D4/TMP-D4-P0-backend-reader-api-closeout.md`。

Closeout 结论：

- 已新增最小后端 Reader API surface，让 Web 可以走通 plain text submit、snapshot reload 和 event polling。
- `POST /reader/records/plain-text` 调用 `ArticleReadyPersistenceService.submit_plain_text`，返回 record id、base id、`article_ready` event sequence 和 `ReaderPlateSnapshot`。
- `GET /reader/records/{record_id}/snapshot` 调用 D3-P3 snapshot reload，从 DB facts 重建 `ReaderPlateSnapshot`。
- `GET /reader/records/{record_id}/events` 调用 D3-P4 `ReaderEventRuntime.poll_events`，支持 `after_sequence`、`limit`、`last_event_sequence`、truncated response 和 reload-required signal。
- 用户隔离复用 `AuthUserDep`；record 不存在或不属于当前 user 均返回 404。
- `client_record_id` blank 会规范化为 `NULL`；同一用户重复 active `client_record_id` 返回 409。
- 新 API 路径不读取旧 `render_scene_json`。

D4-P0 不包含：

- Translation Worker。
- Layer Publisher 业务逻辑。
- PydanticAI / LLM 调用。
- Web Reader UI。
- SSE endpoint 纵切；polling 先行。
- `projection_ops` 端到端 applier。

Focused tests 已通过：

- `test_reader_orchestration_api.py`
- `test_reader_orchestration_article_ready_service.py`
- `test_reader_orchestration_event_runtime.py`
- targeted `ruff check`
- targeted `compileall`

### D4-P1. Translation Layer Worker + Layer Publish Vertical Slice

状态：completed on 2026-06-21，详细记录见 `docs/tmp/reader-orchestration/D4/TMP-D4-P1-translation-layer-closeout.md`。

Closeout 结论：

- 已新增 deterministic translation run/job bootstrap，创建最小 `reader_runs` 与 base-scoped `reader_jobs`。
- `ReaderJobRuntime.claim_next_job()` 支持 `job_type` / `target_type` 过滤，translation worker 不会 claim mixed queue 中的非 translation jobs。
- Translation worker 使用 PydanticAI typed output 边界生成 `TranslationLayerOutput`；测试使用 fake translator，不调用真实 LLM。
- Layer publisher 在一个事务内写 `enhancement_layers(layer_type='translation')`、发布 `layer_published` event、完成 job transition 和 run completion。
- Snapshot reload 能看到 published translation layer，并在 Plate value 中投影 `reader_translation` node。
- 成功和失败路径均写 `ai_usage_events`，带 record / run / job / layer attribution、model route/profile/provider/name 和 operation fingerprint。
- retryable failure 后重新成功会清空 `reader_runs.failure_class` / `failure_code`，避免 completed run 带旧失败状态。

D4-P1 不包含：

- Web Plate Reader UI。
- vocabulary、grammar_note、sentence_analysis。
- SSE endpoint。
- Ask Document Tools。
- LangGraph flow。
- RAG substrate。
- URL / PDF / OCR / 文件上传。

Focused tests 已通过：

- `test_reader_orchestration_translation_worker.py`
- `test_reader_orchestration_layer_publisher.py`
- `test_reader_orchestration_job_runtime.py`
- `test_reader_orchestration_event_runtime.py`
- `test_reader_orchestration_article_ready_service.py`
- targeted `ruff check`
- targeted `compileall`

### D4-P2. Backend Orchestration Integration + Parsed Decision

状态：completed on 2026-06-21，详细记录见 `docs/tmp/reader-orchestration/D4/TMP-D4-P2-orchestration-parsed-closeout.md`。

Closeout 结论：

- 已新增 `ReaderOrchestrator` service，作为 D4 后端最小 orchestration facade。
- `POST /reader/records/plain-text` 现在通过 `ReaderOrchestrator.submit_plain_text_and_bootstrap_translation()` 先创建 article-ready facts，再启动 translation run/job。
- 已新增 testable tick path：`ReaderOrchestrator.tick_translation_worker()` 复用 D4-P1 `TranslationWorkerService`，从 queued translation job 推进到 `layer_published`。
- Translation layer published 后写最小 `parsed_decisions`，并发布 `parsed_decision_updated` event。
- Snapshot reload 可同时看到 translation layer 和 parsed decision；event polling 顺序覆盖 `layer_published` 后 `parsed_decision_updated`。
- 保持 PostgreSQL run/job/event 作为 durable control plane；未引入 LangGraph。
- D4-P2 没有新增 HTTP tick endpoint；worker tick 仍是 service/testable entry，后续是否暴露内部 route 另行设计。
- Parsed decision 写入与 layer publish 暂不同事务。D4 单线程 tick 可接受；如果 D5 需要强一致，应把 decision 写入收敛到 publisher transaction 或明确 compensating repair。

D4-P2 不包含：

- Web Plate Reader UI。
- SSE endpoint。
- vocabulary、grammar_note、sentence_analysis。
- Ask Document Tools。
- RAG substrate。
- URL / PDF / OCR / 文件上传。
- LangGraph flow。
- `projection_ops` 端到端 applier。

Focused tests 已通过：

- `test_reader_orchestration_orchestrator.py`
- `test_reader_orchestration_api.py`
- `test_reader_orchestration_translation_worker.py`
- `test_reader_orchestration_event_runtime.py`
- targeted `ruff check`

### D4-P3. Web Reader Plate Read-only Surface + BFF Polling Slice

状态：completed on 2026-06-21，详细记录见 `docs/tmp/reader-orchestration/D4/TMP-D4-P3-web-reader-plate-closeout.md`。

Closeout 结论：

- 已新增 Web BFF routes：
  - `POST /api/web/reader-plate/submit`
  - `GET /api/web/reader-plate/{recordId}/snapshot`
  - `GET /api/web/reader-plate/{recordId}/events`
- BFF 复用当前 Web session token，拒绝 anonymous / mock phone session；缺失或跨用户 record 仍由后端映射为 404。
- Web API client 只调用新 Reader API：`/reader/records/plain-text`、`/reader/records/{record_id}/snapshot`、`/reader/records/{record_id}/events`。
- 已新增 `ReaderPlateSnapshot` DTO mirror、只读 `ReaderPlateSnapshotSurface`、polling decision hook 和 `/app/reader-plate` 最小真实提交入口。
- Web polling 在 `layer_published`、`projection_reset_required` 或 server reload signal 时触发 snapshot reload；D4 不应用 `projection_ops`。
- 页面用户可见文案不暴露 D4、Plate.js、Snapshot、cursor、sequence 等实现术语。
- 新 Web 路径不读取旧 `/scene` 或 `render_scene_json`。

D4-P3 不包含：

- Rich Reader production UI polish。
- Selection bridge / anchor adapter。
- User highlights / notes。
- Ask Document Tools。
- `projection_ops` incremental applier。
- SSE endpoint。
- URL / PDF / OCR / 文件上传。

Focused tests 已通过：

- `pnpm --filter=@claread/web typecheck`
- `pnpm --filter=@claread/web test`
- `pnpm --filter=@claread/web build`

### D4-P4. Worker Runner Hardening + Web Smoke/Test Gap Closeout

状态：completed on 2026-06-21，详细记录见 `docs/tmp/reader-orchestration/D4/TMP-D4-P4-worker-web-hardening-closeout.md`。

Closeout 结论：

- 已新增 `TranslationWorkerRunner`，作为 D4 内部 callable runner，封装 single tick 与 bounded drain。
- Runner 不新增 public HTTP endpoint，不启动后台进程，不引入 LangGraph / MQ / Temporal / SSE。
- Runner 使用 `ReaderOrchestrator.tick_translation_worker()`，并把 worker result 分类为 `no_job`、`succeeded`、`retry_later`、`failed_terminal`、`fence_rejected`。
- Drain 遇到 retry / terminal failure / fence rejection 不立即停止，因为同一队列中可能仍有其他可处理 job；caller 通过 `WorkerDrainResult` 决定是否继续。
- 已新增 orphan diagnostic：查找 published translation layer 但缺失 `parsed_decisions` 的记录。D4 单线程 tick 下应返回空；D5 若引入并发 tick 或 crash recovery，再决定是否把 parsed decision 写入 publisher transaction 或补 repair。
- Web 侧补齐 Reader Plate BFF auth/error tests，覆盖 anonymous / mock phone 拒绝、上游 401/404/409/5xx/网络失败、空文本与成功提交。
- Web 侧新增 reader-plate Playwright smoke，使用 mocked BFF routes 验证真实页面交互、只读 Plate surface 渲染 source text 和 translation、polling caught-up 无错误。
- Web 页面与 polling 文案继续保持产品语义，不暴露 D4、Plate.js、Snapshot、cursor、sequence 等实现术语。

D4-P4 不包含：

- 真实后台 worker daemon。
- Public 或 internal HTTP tick endpoint。
- Crash-recovery repair job。
- `projection_ops` incremental applier。
- vocabulary、grammar bundle、Ask tools、RAG、SSE 或 LangGraph flow。
- 真实后端/auth 的 browser E2E；当前 smoke 只验证浏览器渲染与交互路径。

Focused tests 已通过：

- `uv run ruff check app/services/reader_orchestration tests/test_reader_orchestration_worker_runner.py tests/test_reader_orchestration_orchestrator.py`
- `uv run pytest tests/test_reader_orchestration_worker_runner.py tests/test_reader_orchestration_orchestrator.py tests/test_reader_orchestration_api.py tests/test_reader_orchestration_translation_worker.py tests/test_reader_orchestration_event_runtime.py -q`
- `pnpm --filter=@claread/web typecheck`
- `pnpm --filter=@claread/web test`
- `pnpm --filter=@claread/web build`
- `pnpm --filter=@claread/web test:e2e -- reader-plate-smoke.spec.ts`

## D4. 最小纵切

流程：

1. Web 提交文本。
2. 后端创建 Original Input 和 Reading Record。
3. 低影响 base path 创建 Stable Reading Base。
4. 创建 Reading Units、Anchor Segments 和 Navigation Skeleton。
5. 创建 Base Plate Snapshot。
6. Reader 到达 `article_ready`。
7. Translation layer 为第一个/当前 units 发布。
8. 记录 Parsed Decision。
9. Web Plate Reader Surface 渲染稳定文章 + 渐进译文。

完成标准：

- 用户可在 full coverage 前开始阅读。
- 刷新/恢复后状态正确。
- Annotation layer 不修改 Stable Base source text；只通过 projection 呈现在 Plate Article Body。
- LLM 调用有 usage event。
- 旧 `render_scene_json` contract 不参与新 Web Reader 路径。
- RAG substrate 可以在后台构建，不阻塞阅读。
- **Plate.js 承接 Article Body**：Web 直接加载 Base Plate Snapshot；readOnly 起步，具备 selection bridge。
- **D4 不要求 projection_ops 端到端**：translation layer 可先通过 snapshot reload 或 simple projection refresh 呈现；D5 才接增量 `projection_ops`。

明确不包含：

- URL / PDF / OCR / 文件上传实现。
- Candidate Base preview/edit/confirm UI。
- vocabulary、grammar_note、sentence_analysis、summary、Semantic Outline。
- 小程序适配。
- 旧 Reader scene 兼容映射。

## D5. 增强扩展

任务包：

- 增加 vocabulary、grammar bundle layers：发布为 `grammar_note` 与 `sentence_analysis` 两个 subtype。
- 增加 anchor validation gates。
- 增加 summary / Semantic Outline 作为 planner-selected optional layers。
- 完善 learning policy variants；academic 只保留未来扩展点，不在本轮实现。
- 增加 local retry / repair。
- 增加 Parsed Decision 和 anchor failures 的 eval sampling。

完成标准：

- 不使用机械 annotation-count threshold。
- 失败层不让文章不可读。
- Parsed coverage 单调递增。
- **projection_ops 端到端可用**：
  - Layer Publisher 在 publish 末尾同事务 emit domain event + `projection_ops`。
  - 前端订阅 `projection_ops` event，把 domain target 解析成当前 Plate path，再应用 Plate transforms；snapshot reload 作为 fallback。
  - Plate path adapter 提供 `unitIdToPath`、`anchorSegmentIdToPath`、`pathToAnchorSegment`、`selectionToDomainAnchor`。
  - owner 权限层覆盖 `stable`、`system_ai`、`ask_supplement`、`user`、`ephemeral`，前端镜像后端拒绝逻辑。
  - 增强层（vocab / grammar_note / sentence_analysis / summary）以 typed layer result + sanitized fragment 投影为 Plate marks/nodes；是否使用 Plate AI/suggestion 插件取决于 D2-P0 license/API 结论。

### D5-V1. Vocabulary Layer Backend Slice

状态：completed on 2026-06-21，详细记录见 `docs/tmp/reader-orchestration/D5/TMP-D5-V1-vocabulary-backend-closeout.md`。

Closeout 结论：

- 已新增 `VocabularyLayerOutput` typed schema，保留旧 AI Workflow 的三类 item subtype：`vocab_highlight`、`phrase_gloss`、`context_gloss`。
- 三类 item 属于同一个 `enhancement_layers.layer_type = 'vocabulary'`；不拆成三个顶层 layer type。
- Vocabulary anchor 使用 `anchor_segment_id`、UTF-16 range、`selected_text` 和 FNV text hash；`sentence_id` 只作为可选兼容 alias，不是权威锚点。
- DB baseline 正式支持 `reader_jobs.job_type = 'build_vocabulary_layer'`；不再挪用 `build_base`，也不把 job 语义藏在 `input_json.job_intent`。
- 已新增 `VocabularyJobBootstrapService`、`VocabularyWorkerService`、`VocabularyLayerPublisher` 与 focused tests。
- Worker 默认 executor 是未配置失败路径，会把 job/run 标为 `failed_terminal` 且不发布空 layer；只有显式注入 `FakeVocabularyExecutor()` 时才允许发布空 `VocabularyLayerOutput(items=[])`。
- Publisher 在事务内校验 unit、anchor segment、UTF-16 range、selected text 和 hash，成功后写 `enhancement_layers(layer_type='vocabulary')` 与 `reader_events(event_type='layer_published')`。
- Snapshot reload 目前只暴露 top-level `enhancement_layers` metadata；D5-V1 不实现 Plate vocabulary marks/nodes。

D5-V1 不包含：

- Web Plate vocabulary projection / rendering。
- `projection_ops` incremental applier。
- real PydanticAI vocabulary executor / prompt。
- parsed decision for vocabulary。
- grammar bundle、Ask tools、RAG、SSE 或 LangGraph flow。

Focused tests 已通过：

- `uv run ruff check app/schemas/reader_orchestration.py app/services/reader_orchestration tests/test_reader_orchestration_vocabulary_worker.py tests/test_reader_orchestration_schema_models.py tests/test_reader_orchestration_schema_baseline.py`
- `uv run pytest tests/test_reader_orchestration_schema_baseline.py tests/test_reader_orchestration_vocabulary_worker.py tests/test_reader_orchestration_translation_worker.py tests/test_reader_orchestration_orchestrator.py tests/test_reader_orchestration_job_runtime.py tests/test_reader_orchestration_layer_publisher.py tests/test_reader_orchestration_schema_models.py -q`

### D5-V2. Vocabulary Projection / Web Read-only Rendering

状态：completed on 2026-06-21，详细记录见 `docs/tmp/reader-orchestration/D5/TMP-D5-V2-vocabulary-projection-closeout.md`。

Closeout 结论：

- Published `vocabulary` layer 继续以 `VocabularyLayerOutput` 作为 domain truth；Plate marks 是 snapshot projection，不是持久事实。
- Snapshot reload 会按当前 base/unit/anchor 重新校验 layer output，并把三类 item 投影为 stable source leaf 上的 `reader_vocabulary_marks`。
- Vocabulary mark 使用 `anchor_segment_id` + unit-local UTF-16 `start_offset` / `end_offset`；serializer 派生 leaf 内 `segment_start_utf16` / `segment_end_utf16`、`starts_here`、`ends_here`。
- Web read-only surface 已能区分展示 `vocab_highlight`、`phrase_gloss`、`context_gloss`；translation node 保持原有 projection 形态。
- D5-V2 没有读取旧 `render_scene_json`，没有持久化 Plate path/op，也没有启用 `projection_ops` incremental applier。
- Review 修正：vocabulary snapshot layer 必须 `target_scope='unit'`，不能投到 `anchor_segment` scope；测试已覆盖该边界。

D5-V2 不包含：

- real PydanticAI vocabulary executor / prompt。
- grammar bundle。
- Ask tools / user editable vocabulary interactions。
- RAG、SSE 或 LangGraph flow。
- `projection_ops` incremental applier。

Focused tests 已通过：

- `uv run ruff check app/services/reader_orchestration/snapshot.py tests/test_reader_orchestration_base_builder.py tests/test_reader_orchestration_vocabulary_worker.py`
- `uv run pytest tests/test_reader_orchestration_base_builder.py tests/test_reader_orchestration_vocabulary_worker.py tests/test_reader_orchestration_schema_models.py -q`
- `pnpm --filter=@claread/web typecheck`
- `pnpm --filter=@claread/web test`
- `pnpm --filter=@claread/web build`（沙箱网络无法拉 Google Fonts 时会失败；联网重跑已通过）
- `pnpm --filter=@claread/web test:e2e -- reader-plate-smoke.spec.ts`

### D5-V3. Real Vocabulary Executor / Prompt

状态：completed on 2026-06-21，详细记录见 `docs/tmp/reader-orchestration/D5/TMP-D5-V3-real-vocabulary-executor-closeout.md`。

Closeout 结论：

- 已新增 `reader_layer_vocabulary` model route、`reader_vocabulary_model_profile` 配置和 prompt registry 入口。
- `reader_layer_vocabulary` 必须显式配置 `reader_vocabulary_model_profile` 才会注册；不得 fallback 到 annotation model profile。
- `PydanticAIVocabularyExecutor` 让模型输出内部候选 schema，不让 LLM 直接输出正式 `VocabularyLayerOutput`、UTF-16 offsets、hash、Plate JSON 或 raw ops。
- 后端 deterministic postprocess 只接收 `anchor_segment_id + selected_text`，在目标 Anchor Segment 内 exact-match 后生成 unit-local UTF-16 offsets、`selected_text` 和 `fnv1a32-utf16` hash。
- 找不到文本、重复命中、unknown segment、重复 candidate 或结构化输出无效时 fail closed 或跳过对应 item，并把原因写入 `quality_json.diagnostics`；不会发布错误 anchor。
- 同一 span 冲突按 `context_gloss > phrase_gloss > vocab_highlight` 保留，非冲突项保持稳定输入顺序。
- Candidate output 有硬上限和字段长度限制；diagnostics 会裁剪数量和文本长度，防止坏模型撑大 payload。
- 模型返回空 items 或全部候选被安全跳过时，允许发布空 `VocabularyLayerOutput(items=[])`，用于标记该 unit 已处理；跳过原因必须留在 diagnostics。
- D5-V3 不改变 public `VocabularyLayerOutput` schema，不读取旧 `render_scene_json`，不启用 `projection_ops` incremental applier。

D5-V3 不包含：

- Grammar bundle。
- vocabulary parsed decision / coverage policy。
- Ask tools / user editable vocabulary interactions。
- RAG、SSE 或 LangGraph flow。
- `projection_ops` incremental applier。

Focused tests 已通过：

- `uv run ruff check app/config/settings.py app/llm/routes.py app/llm/registry.py app/services/reader_orchestration/vocabulary_worker.py tests/test_reader_orchestration_vocabulary_worker.py tests/test_reader_orchestration_vocabulary_executor.py`
- `uv run pytest tests/test_reader_orchestration_vocabulary_executor.py tests/test_reader_orchestration_vocabulary_worker.py -q`
- `uv run pytest tests/test_reader_orchestration_translation_worker.py tests/test_reader_orchestration_orchestrator.py tests/test_reader_orchestration_layer_publisher.py tests/test_reader_orchestration_job_runtime.py -q`
- `uv run pytest tests/test_reader_orchestration_schema_models.py tests/test_reader_orchestration_schema_baseline.py -q`
- `uv run python -m compileall app/services/reader_orchestration/vocabulary_worker.py app/llm/routes.py app/llm/registry.py app/config/settings.py tests/test_reader_orchestration_vocabulary_executor.py tests/test_reader_orchestration_vocabulary_worker.py`

### D5-V4. Grammar Bundle Backend Slice

状态：completed on 2026-06-21，详细记录见 `docs/tmp/reader-orchestration/D5/TMP-D5-V4-grammar-bundle-backend-closeout.md`。

Closeout 结论：

- 已新增正式 `reader_jobs.job_type = 'build_grammar_bundle'`，固定 `target_type = 'unit'` 和 `operation_fingerprint = 'grammar_bundle_unit_v1'`。
- 已新增 grammar typed schema：`GrammarNoteItem`、`SentenceAnalysisChunk`、`SentenceAnalysisItem`、`GrammarNoteLayerOutput`、`SentenceAnalysisLayerOutput` 和 internal `GrammarBundleOutput`。
- `GrammarJobBootstrapService` 会选择当前 active base 下最早未处理 unit，并避免重复 active/succeeded grammar bundle job。
- `GrammarBundleWorkerService` 默认 unconfigured executor 失败且不发布 layer；显式 fake executor 可用于 focused tests。
- `GrammarBundleLayerPublisher` 将一次 bundle 发布拆成两个独立 layer rows：`grammar_note_unit_v1` 与 `sentence_analysis_unit_v1`，并发布对应 `layer_published` events。
- Empty sanitized output 采用 no-op success：不插入 layer，不发布 `layer_published` reader event，job/run 成功，`output_ref_json.no_op = true`。
- Usage attribution 采用单条 job-level `ai_usage_events`，`enhancement_layer_id = NULL`，metadata 记录 produced layer ids/types 与 no-op，避免双 layer 重复计费。
- fallback_window 处理：`sentence_analysis` 命中 fallback window 时跳过；`grammar_note` 任一 span 命中 fallback window 时整条 item 跳过，不发布部分 grounding。
- Snapshot reload 保持 read-only；D5-V4 只暴露 top-level grammar layer metadata，不投影到 `snapshot.value`。

D5-V4 不包含：

- real PydanticAI grammar executor / prompt。
- Web grammar projection / rendering。
- grammar parsed decision / coverage policy。
- Ask tools、RAG、SSE、LangGraph flow。
- `projection_ops` incremental applier。

Focused tests 已通过：

- `uv run ruff check app/schemas/reader_orchestration.py app/services/reader_orchestration/grammar_worker.py app/services/reader_orchestration/job_bootstrap.py app/services/reader_orchestration/layer_publisher.py app/services/ai_usage tests/test_reader_orchestration_grammar_worker.py tests/test_reader_orchestration_schema_models.py tests/test_reader_orchestration_schema_baseline.py`
- `uv run pytest tests/test_reader_orchestration_grammar_worker.py tests/test_reader_orchestration_schema_models.py tests/test_reader_orchestration_schema_baseline.py -q`

### D5-E1. Vocabulary Eval Seed Disposition

状态：accepted_with_changes on 2026-06-21，详细记录见 `docs/tmp/reader-orchestration/D5/TMP-D5-vocabulary-eval-seed-disposition.md`。

结论：

- 评估方向接受：优先建立 vocabulary deterministic eval seed，覆盖 anchor resolution、bounds compliance、diagnostics coverage、same-span arbitration 和 item quality。
- 原调研中的单文件 `vocabulary_seed_v1.jsonl` 不采纳；实现必须匹配现有 `evals` harness 的 `dataset.yaml + cases/*.json` 目录形态，或显式新增 vocabulary seed loader。
- 原调研中的 `evals/claread_eval/judge/judges/vocabulary_judge.yaml` 不采纳为下一步范围；当前 judge runner/rubric contract 仍是 article-analysis oriented，LLM judge 泛化单独后置。
- LangSmith `evaluate()` 不进入下一步；先用本地 deterministic graders 和 pytest 验收。
- 超过 5 个 candidate 的预期需按 D5-V3 真实实现修正：通常在 `VocabularyCandidateOutput` validation 阶段 fail closed，不作为普通 `candidate_limit_exceeded` diagnostics gate。
- vocabulary `boundary_low_fallback_window` 在 D5-G2 后已成为 acceptance gate；vocabulary worker 在 `_build_vocabulary_output_from_candidates` 中显式拒绝 `segment_type=fallback_window` 的候选 item，reason_code 写入 `diagnostics.skipped_items[]`，与 grammar bundle 口径一致。

### D5-R1. LangGraph / Orchestration Architecture Review Disposition

状态：`accepted_with_changes` on 2026-06-22，详细记录见 `docs/tmp/reader-orchestration/D5/TMP-D5-langgraph-orchestration-disposition.md`。

结论：

- 两份 LangGraph / orchestration 架构评估的大方向接受：当前 PostgreSQL durable control plane + PydanticAI typed worker + Plate snapshot projection 的三层架构符合本轮 Reader 重构目标。
- D5 主链路 runner、translation、vocabulary、grammar bundle、snapshot projection 和 eval 任务不引入、不升级 LangGraph。
- LangGraph 不得替换 `reader_runs`、`reader_jobs`、`reader_events`、`enhancement_layers` 或 Reader product state。
- D6-LG0 仅作为隔离 spike 候选；触发条件必须是具体 Ask Document Tools / human approval / multi-branch repair flow 需求。
- D5 第一条页面可测主链路继续使用 snapshot reload；`projection_ops` incremental applier 不阻塞 smoke。
- 评估中被修正的风险排序：`parsed_decisions` 跨事务和 vocabulary boundary policy 是 P1；`active_base_id -> status='active'` 是 service / publisher invariant hardening，不是 D5 主线 P0；`projection_ops` race 只在启用 incremental applier 前需要 spike。

下一步影响：

- D5 主链路 runner 只做 deterministic bootstrap/drain，不引入 LangGraph / MQ / Temporal / SSE。
- Runner review 后优先做页面 smoke，再进入 parsed decision repair、vocabulary boundary policy 和 projection ops consistency guardrails。

### D5-R2. Main Chain Runner + Web Record Load Closeout

状态：completed on 2026-06-22。

Closeout 结论：

- 已新增 `ReaderEnhancementPipelineRunner`，统一 bootstrap / drain `translation`、`vocabulary`、`grammar_bundle` jobs。
- Runner 复用现有 `ArticleReadyPersistenceService`、`EnhancementJobBootstrapService`、`ReaderJobRuntime`、三类 worker 和 Layer Publisher，不另建 orchestration 控制面。
- Runner drain 顺序为 translation -> vocabulary -> grammar bundle；遇到 `retry_later`、`failed_terminal` 或 publish fence supersede 时返回 attention summary。
- `ReaderJobRuntime.claim_next_job()` 已支持可选 `reading_record_id`、`base_id`、`expected_generation` scope；三类 worker 增加 record-scoped claim/process 入口，runner 不会消费其他 Reading Record 的 queued jobs。
- Runner 不新增 public HTTP endpoint，不启动后台 daemon，不引入 LangGraph / MQ / Temporal / SSE，也不启用 `projection_ops` incremental applier。
- 已新增本地 D5 dev smoke harness / CLI，用于准备 record 和验证 snapshot reload；fake executors 默认禁用，必须显式 opt-in，且生产环境禁用。该 harness 不是产品运行路径。
- Web `/app/reader-plate` 已支持 `record_id` / `recordId` query 直达加载已有 `ReaderPlateSnapshot`；提交成功后会把 URL replace 到 `?record_id=...`。
- Web 页面继续通过现有 BFF snapshot/events 路径读取新 Reader API，不回退旧 `/scene` 或 `render_scene_json`。
- 当前页面可测路径包括：已准备好的 record -> snapshot/events -> Reader Plate 渲染 source、translation、vocabulary、grammar_note、sentence_analysis。

D5-R2 不包含：

- 生产后台 worker loop / daemon。
- public 或 internal HTTP worker-control endpoint。
- 页面 submit 后自动同步执行真实 LLM 全链路。
- `projection_ops` incremental applier。
- parsed decision repair、vocabulary boundary policy 或 readiness/coverage policy。

Focused tests 已通过：

- `uv run ruff check app/services/reader_orchestration/smoke_harness.py scripts/prepare_reader_d5_smoke.py tests/test_reader_orchestration_smoke_harness.py`
- `uv run pytest tests/test_reader_orchestration_smoke_harness.py tests/test_reader_orchestration_pipeline_runner.py -q`
- `uv run pytest tests/test_reader_orchestration_smoke_harness.py tests/test_reader_orchestration_pipeline_runner.py tests/test_reader_orchestration_orchestrator.py tests/test_reader_orchestration_translation_worker.py tests/test_reader_orchestration_vocabulary_worker.py tests/test_reader_orchestration_grammar_worker.py -q`
- `pnpm --filter=@claread/web typecheck`
- `pnpm --filter=@claread/web test`
- `pnpm --filter=@claread/web test:e2e -- tests/e2e/reader-plate-smoke.spec.ts`

### D5-W1. Local / Deployment Worker Loop Evaluation

状态：`accepted_with_changes` on 2026-06-22，评估材料已移至 `docs/tmp/reader-orchestration/D5/TMP-D5-D6-worker-loop-evaluation-2026-06-22.md`。

结论：

- 接受独立 worker process 方向：本地用 CLI entrypoint 启动，部署时作为独立 worker service / process / container 运行。
- Worker loop 复用 `ReaderEnhancementPipelineRunner`，不另建 orchestration 控制面。
- API 服务保持 request-serving；worker loop 不挂到 FastAPI lifespan / startup task，避免 dev reload、多副本和 API 请求路径混杂。
- Web submit 仍只负责创建 `article_ready` facts；不要把 runner 同步塞进 submit request。
- 不新增 public 或 semi-public worker-control endpoint。
- 不使用 smoke harness / fake executors 作为产品运行路径；fake 仍只能显式 opt-in 用于 dev/test。
- 真实 model profile 缺失保持 fail-closed，不静默 fallback 到 fake 或 synthetic layer。

修正口径：

- 扫描条件不能只写死 `readiness_state = 'article_ready'`。因为 `readiness_state` 是单调 milestone，worker loop 初版应默认考虑 `article_ready` 与 `initial_enhancement_ready`，并把 `coverage_complete` 作为默认停止态；如后续有 repair / retry policy，再显式允许 coverage-complete records 回到 eligible set。
- 粗筛只负责找候选 record；exact missing work 仍由 `EnhancementJobBootstrapService` / `ReaderEnhancementPipelineRunner` 决定，不在 scanner 复制每个 layer 的 eligibility 逻辑。
- 初版并发保持保守：per-record advisory lock 必须有；per-user concurrency 和 per-worker concurrency 默认 `1`，后续再通过配置放宽。
- `retry_later` 不应导致 hot-loop；worker loop 应尊重 job `available_at` 或通过 runnable-job 优先扫描减少空转。
- `failed_terminal` 初版只进入 logs / metrics / summary；是否映射为 `product_state='action_required'` 留给 D6 product hardening。

最小实现建议：

1. `ReaderEnhancementWorkerLoopService`：扫描 eligible records、获取 advisory locks、调用 runner、解释 summary。
2. `scripts/run_reader_enhancement_worker.py`：初始化 DB，按 scan interval / batch size / runner limits 循环。
3. 新增 settings：`reader_worker_scan_interval_seconds`、`reader_worker_batch_size`、`reader_worker_max_ticks`、`reader_worker_max_jobs`、`reader_worker_lease_owner_prefix`。不新增 fake executor product config。
4. Focused tests 覆盖 eligibility scan、record/user lock、record-scoped runner、retry_later backoff、missing profile fail-closed 和 stale base fence。

### D5-W2. Local / Deployment Worker Loop Closeout

状态：completed on 2026-06-22。

Closeout 结论：

- 已新增 `ReaderEnhancementWorkerLoopService`，使用 coarse eligibility scan + per-record / per-user advisory locks 调度 `ReaderEnhancementPipelineRunner`。
- worker loop scanner 只筛 `reading_records` / `reading_bases` 的 coarse readiness，不复制 translation / vocabulary / grammar bundle 的 missing-work 判定。
- scanner 会优先处理当前 active base / generation 下存在 runnable jobs 的 record；无 runnable jobs 时，仅对当前 generation 不存在 tracked jobs 的 record 允许重新进入 bootstrap，从而避免 `retry_later` hot-loop 和 `failed_terminal` 反复重建。
- `retry_later` 继续尊重 `available_at`；`failed_terminal` 只进入 summary / log，不修改 `product_state='action_required'`。
- 已新增 `scripts/run_reader_enhancement_worker.py`，支持 `--once` 和 loop mode；本地和部署共用同一入口。
- 已新增 settings：`reader_worker_scan_interval_seconds`、`reader_worker_batch_size`、`reader_worker_max_ticks`、`reader_worker_max_jobs`、`reader_worker_lease_owner_prefix`。
- 未新增 public endpoint，未把 runner 放进 Web submit，未挂到 FastAPI lifespan，未引入 LangGraph / MQ / SSE / `projection_ops`。
- API / Web / worker 的真实本地启动步骤、mock phone session 获取、model profile wiring 和 fail-closed 观察方式见 `docs/initiatives/reader-agentic-orchestration/modules/local-real-chain-runbook.md`；该 runbook 只验证 CLI help / 参数解析与配置连线，未在文档任务中实跑真实 provider / LLM。

Focused tests 已通过：

- `uv run ruff check app/services/reader_orchestration app/config/settings.py tests/test_reader_orchestration_worker_loop.py scripts/run_reader_enhancement_worker.py`
- `uv run pytest tests/test_reader_orchestration_worker_loop.py tests/test_reader_orchestration_pipeline_runner.py -q`

### D5-G1/G2. Runtime Guardrails Closeout

状态：completed on 2026-06-22。

Closeout 结论：

- D5-G1 已把 translation layer publish 与最小 `parsed_decisions` 写入收敛到同一 publisher transaction，消除 layer 已发布但 parsed decision 正常缺失的 crash gap。
- `diagnose_orphaned_translation_decisions()` 保留为 diagnostic，用于发现 pre-D5 遗留数据或测试中人为制造的 partial state；snapshot reload 不做隐式 repair。
- D5-G2 已统一 vocabulary 与 grammar bundle 的 fallback_window boundary policy：`segment_type = fallback_window` 的 anchor segment 不产出 vocabulary / grammar item。
- Vocabulary fallback skip 使用 reason_code `boundary_low_fallback_window`，写入 worker diagnostics；空有效 vocabulary output 仍可发布，用于标记该 unit 已处理。
- Vocabulary eval seed 已新增 fallback-window skip fixture，并更新 baseline。

Focused tests 已通过：

- `uv run ruff check app/services/reader_orchestration app/config/settings.py tests/test_reader_orchestration_layer_publisher.py tests/test_reader_orchestration_orchestrator.py tests/test_reader_orchestration_vocabulary_worker.py tests/test_reader_orchestration_worker_loop.py scripts/run_reader_enhancement_worker.py`
- `uv run pytest tests/test_reader_orchestration_layer_publisher.py tests/test_reader_orchestration_orchestrator.py tests/test_reader_orchestration_vocabulary_worker.py tests/test_reader_orchestration_worker_loop.py tests/test_reader_orchestration_pipeline_runner.py -q`
- `uv run ruff check claread_eval/schemas/vocabulary.py claread_eval/graders/vocabulary.py scripts/build_vocabulary_seed.py tests/test_vocabulary_dataset.py tests/test_vocabulary_graders.py tests/test_vocabulary_seed_pipeline.py tests/test_vocabulary_runner.py tests/test_vocabulary_baseline.py`
- `uv run pytest tests/test_vocabulary_dataset.py tests/test_vocabulary_graders.py tests/test_vocabulary_seed_pipeline.py tests/test_vocabulary_runner.py tests/test_vocabulary_baseline.py -q`

## D6. 产品硬化

任务包：

- 高影响适配的 Candidate Reading Base preview/edit/confirm。
- Library states：processing、readable/enhancing、paused、needs_confirmation、failed、quota_required。
- continuation、quota、retry、re-parse-as-new-record 的 action-required UX。
- Ask sidecar action envelope：continue enhancement、save note、context expansion。
- cost / credit decision surfaces。
- 失败恢复和 support/debug details。

完成标准：

- action-required states 在 Reader 和 Library 都可发现。
- Ask 不能绕过 Authorization Envelope。
- 超长文 continuation 可 pause/resume。

## Coding Agent 任务规则

- 每个 coding task 尽量控制在 2-8 小时。
- 每个任务必须写清 touched areas、expected tests、done criteria。
- 除非任务明确要求，agent 不读取 TMP research。
- agent 只更新本计划的阶段/任务状态或决策引用。
- 发现架构冲突时，先更新或讨论 `target-architecture.md` 的决策记录，再继续实现。

## T5.3 Semantic Outline Worker + Durable Layer

状态：**closed** on 2026-07-17（commit `781e4117`；前置 T5.2a validation contract `2bf3db97`；本轮 T5.3b 为 docs-only 同步）。

### 已验证事实（可追溯代码 / commit）

1. **Durable truth 复用 `enhancement_layers`**，不新增 outline 专用事实表。
   - `layer_type = 'semantic_outline'`
   - `target_scope = 'record'`，layer `target_key = 'document'`
   - job：`reader_jobs.job_type = 'build_semantic_outline'`（job 行 `target_type = 'record'`，`target_key = reading_record_id`）
   - worker_type：`semantic_outline`（runtime span allow-list）
   - migration：`infra/migrations/0020_reader_semantic_outline_layer.sql`

2. **默认不请求**。`default_semantic_outline_request_eligibility` 恒为 `False`；仅当注入的 request-eligibility 为真 **且** record 已达 `article_ready` 里程碑 readiness（`article_ready` / `initial_enhancement_ready` / `coverage_complete`）时，bootstrap 才创建 outline job。不阻塞 `article_ready` 发布路径。

3. **不进入既有 budget / coverage 必需路径**。
   - `semantic_outline` **不在** `WORKER_TYPE_TO_BUDGET_LAYER` / `_JOB_TYPE_TO_BUDGET_LAYER`。
   - pipeline 以最低优先级 non-budget slot 调度（`pipeline_runner` worker order 末位）。
   - `ENHANCEMENT_PIPELINE_JOB_TYPES`（worker loop 扫描 / completion finalizer tracked types）**不含** `build_semantic_outline`，故 outline job 不参与既有 `coverage_complete` 必需集合。

4. **模型候选 → revision-scoped opaque IDs → T5.2a validator**。
   - `map_candidates_to_opaque_nodes` 分配 `outline_revision` 与 opaque `node_id` / `parent_node_id`。
   - 再调用 `validate_semantic_outline_projection`（T5.2a）。
   - `V=0`、`worker_failure`，或 status ∈ `{failed, stale, unavailable, pending}`：**不得**发布 ready/partial layer（`outcome=not_published`，零 layer / 零 event / 零 sequence）。

5. **发布前 job lease fence（与 grammar publisher 同类）**。`publish_from_candidates` **强制** `job_id` + `lease_token`；事务内先 `SELECT reader_jobs ... FOR UPDATE`，校验：
   - `status = claimed`
   - `job_type = build_semantic_outline`、target identity、record / base / `expected_generation`
   - `operation_fingerprint` 与 outline fingerprint 族
   - lease token + expiry（`_assert_lease_valid`）
   - 完整 `ReaderJobRuntime._validate_fence`（含 route/strategy 一致性）
   - source provenance 绑定锁定 claim 的 `run_id` / `job_id`，不信任调用方随意覆盖
   - 任一失败：`FenceViolationError`；**不** supersede / insert / 分配 sequence。

6. **Record-level 原子 replace**（新 fingerprint）：同一事务 supersede 旧 published → insert 新 published → `layer_published` event。同 fingerprint 幂等 reuse，不发新 event。失败 / stale / lease / provenance / target-key fence 失败均 **保留** 旧同源 published layer，且不产生新 layer / event / sequence。

7. **Snapshot / UI 边界**。`ReaderSemanticOutlineProjection` 仅为 layer envelope / validator 形状；**当前不**挂入 `ReaderPlateSnapshot`。T5.4-R0 设计、T5.4 实现 projection；T5.5 做 UI。L0/L1 继续只消费 `navigation.units`；outline **不得**污染 `navigation.units`。

8. **继续禁止**：SSE、WebSocket、JSON Patch、ETag/304、通用 Plate tree diff 作为 outline 交付通道。

### 权威代码落点

| 能力 | 路径 |
|------|------|
| migration | `infra/migrations/0020_reader_semantic_outline_layer.sql` |
| typed projection + statuses | `services/api/app/schemas/reader_orchestration.py`（`ReaderSemanticOutline*`） |
| validator | `services/api/app/services/reader_orchestration/semantic_outline.py` |
| bootstrap + eligibility | `job_bootstrap.py`（`_bootstrap_semantic_outline_job`、`default_semantic_outline_request_eligibility`、`allow_semantic_outline_request_eligibility` DI、`settings_aware_semantic_outline_request_eligibility` dev-activation factory [T5.8d-dev-activation]） |
| publisher | `semantic_outline_publisher.py` |
| worker | `semantic_outline_worker.py` |
| pipeline slot | `pipeline_runner.py`（`semantic_outline` non-budget） |
| seam tests | `services/api/tests/test_reader_semantic_outline_worker.py`（含 stale lease / superseded job / route fence 负向） |

### 明确未冻结 / 未宣称

- request eligibility 产品阈值（长度/route 等）未写入默认实现；默认始终 false。
- L2 首次自动生成入口、真实模型成本上限未产品冻结。
- 真实 LLM outline 质量与成本未做样本验收。
- 不得把「T5.3/T5.4a/T5.5a closed」表述为「生产默认会生成内容大纲」。

### T5.7 生产就绪边界（2026-07-18）

1. **CHECK 来源**：`infra/migrations/0020_reader_semantic_outline_layer.sql` 已把 `semantic_outline` 写入 `reader_runtime_spans.worker_type` allow-list。不新增 0021 重复扩展；测试/fresh schema 必须 apply 到 0020（`worker_loop_env` 已补）。
2. **默认安全**：`default_semantic_outline_request_eligibility ≡ false`；worker 默认 `UnconfiguredSemanticOutlineGenerator` → permanent `failed_terminal`，零 layer/event。
3. **受控启用 seam**：注入 `allow_semantic_outline_request_eligibility` + `FakeSemanticOutlineGenerator`（或未来 real adapter）可跑通完整 fake real-chain。
4. **真实 LLM blocker**：仓库尚无 `reader_layer_semantic_outline`（或等价）`MODEL_ROUTE`、无 outline prompt agent、无 settings profile 字段。在注册这些之前不得猜测模型配置或写死密钥/模型名。
5. **不进入** ExecutionBudget / coverage 必需集 / ordinary supersede；不扩 transport。

### T5.8c opt-in real-LLM smoke harness 边界（2026-07-18）

1. **设计门**：`C:/tmp/TMP-t5.8c-r0-semantic-outline-opt-in-eval-gate-2026-07-18.md` P2 修订版（§1.1.3 错误分类表拆分 timeout；§2.4 Smoke verdict 分层 functional/usage-audit）。
2. **harness 文件**：`services/api/tests/test_reader_semantic_outline_t58c_real_llm.py`，单一 `@pytest.mark.real_llm` 测试。
3. **默认 skip + 零外呼**：conftest.py triple gate（`CLAREAD_ALLOW_REAL_LLM_TESTS=1` + `CLAREAD_REAL_LLM_MODEL=<非空>` + `-m real_llm`）+ `fail_on_real_llm_attempts` autouse monkeypatch；任一缺失即 skip 且不构造模型。
4. **fail-closed 模型比对**：测试自身在 `build_model_for_route` 后将 `model_config.model_name` 与 `CLAREAD_REAL_LLM_MODEL` 精确比较；不一致 `pytest.fail`，零 provider call（**不** `pytest.skip`）。
5. **DI-only adapter**：`PydanticAISemanticOutlineGenerator(settings=..., policy=SemanticOutlineExecutionPolicy.for_tests(generation_enabled=True))`；生产默认仍 `UnconfiguredSemanticOutlineGenerator`。
6. **单次 provider call**：policy `DEFAULT_MAX_PROVIDER_CALLS_PER_JOB=1` + PydanticAI `output retries=0` + 单一 `generate` 路径；无 repair、无 retry、无 rerun。
7. **success-path 验收**：job `succeeded` + run `completed`；published `enhancement_layers` row `status='published'`，`base_id`/`generation`/`source_identity` fence 一致；envelope `status ∈ {ready, partial}`；`nodes` 非空；`provenance.model` = resolved model_name；`navigation.units`（`reading_units` 表）调用前后逐值不变；`ai_usage_events` 恰 1 行 `status='succeeded'`。
8. **未真实执行**：本轮不设置真实 provider env、不运行 `-m real_llm`；invalid-output / timeout / usage-writer 失败路径不真实执行，仅由 T5.8b 既有 DB seam 覆盖。
9. **leak-safe 报告**：仅记录 `job_id` / `run_id` / `model_name` / `status` / `node_count` / `usage` aggregate token totals / `functional_verdict` / `usage_audit_verdict`；**不**记录 API key / endpoint / 完整 prompt / 完整 provider payload。
10. **observability_inconclusive**：usage writer 容错失败（`record_ai_usage_event` 返回 `None` 且 DB 实际 0 行）时，smoke verdict = `INCONCLUSIVE`；**不**视为完整通过；**不**据此确认成本或产品启用。
11. **T5.8d 待决事项（仅未来生产化/产品化路线，不阻塞当前开发期 activation_ready 自动主链路）**：自动 eligibility 阈值、L2 首次生成入口、真实成本上限、bootstrap kill-switch wiring、`semantic_outline_generation_enabled` 默认是否打开、capability seam / CTA（如选 B.3-a）等仅是未来生产化/产品化路线的待决事项；不阻塞当前开发期 `activation_ready = semantic_outline_generation_enabled AND reader_semantic_outline_model_profile != ""` 自动主链路。当前开发期只需在本地环境显式配置 `SEMANTIC_OUTLINE_GENERATION_ENABLED=true` + `READER_SEMANTIC_OUTLINE_MODEL_PROFILE=<profile>`，随后由人工执行真实 LLM 验证（仍受 conftest real-LLM gate 约束）。不引入 beta、白名单、CTA、capability seam 作为当前开发主线前置；这些决策未落地前 harness 仅做 smoke 验收，不放开产品默认启用。
12. **T5.8d-dev-activation 已实施（开发期自动激活路线，非产品决策）**：`activation_ready = semantic_outline_generation_enabled AND reader_semantic_outline_model_profile != ""`。`job_bootstrap.settings_aware_semantic_outline_request_eligibility(settings)` 派生谓词；`pipeline_runner.ReaderEnhancementPipelineRunner` 接收 `settings` 参数，activation_ready=True 时条件注入 `PydanticAISemanticOutlineGenerator` + settings-aware eligibility，否则保持 `UnconfiguredSemanticOutlineGenerator` + 默认 always-false 谓词。committed defaults 仍关闭；显式注入优先于自动装配。仅开发期适用，不包含 beta / 白名单 / CTA / capability endpoint / 历史数据兼容 / 迁移保留。TDD 20 测试（含 runner-level bootstrap_missing_jobs 真 seam、sentinel 与显式 bootstrap override）覆盖 A 默认关闭 / B 自动资格 / C 装配 / D 真链路 seam / E runner-level 公开 bootstrap seam；该实现提交本身未运行真实 LLM；其后 T5.8c 已完成一次受控 real-LLM smoke。

### 下一任务

1. 两条独立边界，互不互为前置：
   - **T5.8c pytest smoke**（`tests/test_reader_semantic_outline_t58c_real_llm.py` + `-m real_llm`）：仅在人工授权且本地 profile 已配置后运行；受 conftest triple gate 与 `CLAREAD_ALLOW_REAL_LLM_TESTS=1` / `CLAREAD_REAL_LLM_MODEL=<authorized>` 约束。
   - **本地应用 T5.8d-dev-activation 自动主链路**：配置 `SEMANTIC_OUTLINE_GENERATION_ENABLED=true` 和 `READER_SEMANTIC_OUTLINE_MODEL_PROFILE=<profile>` 后可运行；**不**经过 pytest / conftest gate，运行时门是 `activation_ready`、profile/route 配置及既有 execution policy。
   - 单篇成本上限、beta、白名单、CTA、capability seam **不**写成当前开发期前置。
2. T5.8d 待决事项（仅未来生产化/产品化路线，不阻塞当前开发期 activation_ready 自动主链路）：自动 eligibility 阈值（产品级，不同于 dev activation 的 `article_ready` 直通）、L2 首次生成入口、真实成本上限、`semantic_outline_generation_enabled` 默认是否打开、kill-switch wiring、capability seam / CTA（如选 B.3-a）。这些仅是未来生产化/产品化路线的待决事项；不阻塞当前开发期 `activation_ready` 自动主链路。当前开发期只需在本地环境显式配置 `SEMANTIC_OUTLINE_GENERATION_ENABLED=true` + `READER_SEMANTIC_OUTLINE_MODEL_PROFILE=<profile>`，随后由人工执行真实 LLM 验证（仍受 conftest real-LLM gate 约束）。不引入 beta、白名单、CTA、capability seam 作为当前开发主线前置。

---

## 当前下一步

以本文开头的 Adaptive Reader Orchestration 任务拆分为准。D5 guardrails 与 worker loop 旧章节保留为历史 closeout，不再作为下一轮任务入口。

当前下一步：

1. **T4.2a-O1、T4.2a-O2-V1-R1、T4.2a-O3 代码级**已完成。O3 将 agent-run duration 与 provider-request timing 分层；无 provider timing 时显式 `unavailable`；禁止把 worker/pipeline/agent duration 当作 provider latency 或改写 `latency_ms`。Sample A 仍 UNRESOLVED；Cost/Latency 仍 PARTIAL。
2. **后续 observability** 可评估 cache token normalization、versioned price snapshot / estimated cost 或 Progressive UX fixture；不得因 O3 宣称 token/cost/latency 已可靠或 Sample A 已修复。
3. **Progressive UX**：**T4.2a-PUX-R1 fixture 合同 + T4.2a-PUX-R2 runtime 集成均已完成**；**不重跑 LLM**。
4. **T4.2a-PUX-R4-R3-R1 已闭合**（commit `9a925f82`）：Reader Plate Quick Peek 在 full reload 时以 `anchor_segment_id + markId + generation + baseId` 稳定身份重新锚定；`{generation, base_id}` 是 source identity，任一变化必须清理 selection、Quick Peek、anchor、restore token 与 grammar expansion，禁止跨 source identity 恢复；frozen rect 仅用于 setValue→rAF 恢复窗口，避免 detached `(0,0)` panel；精确 resolver 不得回退挂到同段 sibling mark。rejected stale/fence snapshot 属于 polling/page seam：保持当前 accepted UI 与 Quick Peek，拒绝值不得进入 Surface value swap。Surface same-snapshot early-return 只是 duplicate accepted snapshot guard，不称 stale/fence rejection。验证事实：R3-R1 Chromium 13/13 通过；P2c + R2.1D + Gate-R1 Chromium 10/10 通过；`pnpm --filter @claread/web typecheck` clean。**不得据此宣称 SSE、WebSocket、JSON Patch、ETag/304 或通用 tree diff 已获批准**——这些仍属 PUX-R4 interaction-stable incremental projection / semantic fragment transport，未实施。
5. **下一实施任务为 T4.2a-PUX-R4-R3-R2**：selective grammar expansion cleanup + scroll-anchor compensation。前置条件已满足：R3-R1 source-identity reset 已闭合；R3-R1 语义伴侣 P2c E2E 已与 R3-R1 同提交落地。R3-R3 grammar mark controlled local apply 仍后置，不在 R3-R2 范围内。
6. **固定样本 V2**：碎段新闻、>4,000 words 超长文与 no-op window 使用固定样本、预先声明调用上限；与已关闭的 V1 分开。
7. **长内容交付与渐进阅读 UX（候选路线，未批准传输改造）**：LP-R4 已以结论 B 闭合：`snapshot_id` 不可复用为 HTTP ETag，G1 user assets、G2 Ask supplements、G3 用户可见 record metadata 存在 event coverage gap。T4.2a-O4-R1 已接受正式 [`representation event contract`](./modules/representation-event-contract.md)：先实施 **O4-R2 transactional representation event coverage**，再完成 **PUX-R4 interaction-stable incremental projection**（稳定 generation 内保留 grammar accordion、Quick Peek、panel、selection 与语义 scroll anchor，并只更新受影响 target/layer），才评估 semantic fragment transport，最后才评估以 SSE 替换可见页的 event polling。SSE 只作为带 sequence / generation / target 的通知通道，不能承载整份 snapshot，也不能替代局部 Plate projection；不得预先实现 ETag、304、压缩、SSE、fragment route、JSON Patch 或 WebSocket。

    **O4-R2 实施进度**：
    - 后端 transactional atomic slices A+B+C 已完成（G1 user_assets、G2 ask_supplements、G3 record_metadata 的 same-transaction publish + no-op detection + payload validator）。
    - **O4-R2-D（Web payload-aware reader event classifier）已完成**：在 `apps/web` 引入唯一纯函数 `classifyReaderEvent`，替换静态 `RELOAD_TRIGGER_EVENT_TYPES` 判定；G1/G2/G3 表示事件、未知 schema/section/operation、target_keys 缺失/非法、generation/base fence 不一致一律 reload 或 reset，绝不当作 cursor-only 静默推进；保留 `layer_published`/`record_product_state_updated`/`projection_reset_required` 既有可靠 reload。PUX-R2 单 cursor / 单调 reload 合同保持不变：reload 成功才推进 cursor，stale snapshot 拒绝或 reload 失败时 cursor hold。Vitest 1043 tests / tsc clean / git diff --check clean。
    - **PUX-R4 interaction-stable incremental projection、semantic fragment transport、SSE 通知通道仍未实施**；snapshot HTTP schema、ETag、304、压缩、fragment route、JSON Patch、WebSocket 均未改动。
8. T4.2 bounded LLM document profiler **继续暂缓**。只有 deterministic router 在真实边界样本上出现稳定误判时才重新评估；不得因为已有真实 baseline 就直接引入自由决策 LLM。
9. **T5.1 L0/L1 deterministic navigation 已闭合**（commits `701a9463` / `970d54d8` / `20be3d75` / `9fe6d94d`；T5.1e 文档同步）。L1 ≠ semantic outline；不得用「文章目录 / 大纲 / 第 N 节」描述当前确定性能力。详见 [`modules/reader-record-plate-surface-ui.md`](modules/reader-record-plate-surface-ui.md#deterministic-navigation-l0--l1)。
10. **T5.2a + T5.3 semantic outline durable 已闭合**（commits `2bf3db97` / `781e4117`；T5.3b 正式文档同步）。durable truth 在 `enhancement_layers`；默认不请求；job lease fence + record-level atomic replace；**仍不**挂 `ReaderPlateSnapshot`。详见上方 [T5.3](#t53-semantic-outline-worker--durable-layer)。
11. **下一导航相关任务 = T5.4-R0 Semantic Outline Snapshot Projection Design Gate（只读）**。在实施 T5.4 projection / T5.5 UI 前先定 snapshot DTO 与 eligibility 产品暴露边界；不冻结 L2 UI IA、partial 混排细节或 eligibility 数值阈值；继续禁止 SSE / WebSocket / JSON Patch / ETag/304 / 通用 tree diff。
12. 持续记录 calls、token、**分层** duration（agent-run vs provider-request vs worker_tick wall）、首个可用输出时间和人工质量，但在没有同样本对照前不得宣称降本增效。

T3.5、T4.1、T4.1a、T4.1b、T4.1c、T4.2a-R1 与 T4.2a-R2 已完成代码级实施和 deterministic acceptance。**T4.2a-V1 已正式关闭**。**T5.1 / T5.2a / T5.3 已闭合**。如需 retry force-failed windows、扩展 finalizer 到 RAG substrate，或给 structured batch 独立 grammar budget/prompt/release policy，应分别作为后续独立任务设计。
