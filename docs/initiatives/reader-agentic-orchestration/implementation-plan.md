# Reader Agentic Orchestration 实施计划

> 状态：`D5 active / adaptive three-mode closure in progress`
> 最后更新：2026-07-09

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
2. Strategy / Bootstrap：根据记录与 base 状态创建 reader jobs。当前 translation 短文走 whole-article batch、非短文走 T3.1 grouped/windowed batch（多条 `translate_article` window job），不再创建 per-unit `translate_unit`；vocabulary 已有短文 batch 与非短文 grouped 路径；grammar 已有 analysis window 路径；完整 short / structured / windowed / section / selective planner 尚未落地。
3. Layer Workers：执行 LLM 或 deterministic 后处理。worker 可以选择 batch/window 计算形态，但不能改变 Enhancement Layer 的公开输出语义。
4. Layer Publisher：校验 schema、anchor、publish fence、source hash 和 generation，写 `enhancement_layers` 与 `reader_events`。Publisher 是合同守门，不应替 worker 猜测或改写语义粒度。
5. Snapshot / Plate Projection：从 domain facts 重建页面。前端显示异常优先回查已发布 layer output；不要把 projection 误判为 layer truth。
6. Observability / Eval：对比 old AI Workflow、新 orchestration、真实页面行为和 usage events。没有 baseline/eval 证据，不把实现标记为完成。

当前已暴露的教训：成本优化不能压过产品合同。`translate_article` 的 batch compute 降低了调用数，但短文 batch translation group 一度被实现成 whole-unit group，破坏了 group-native translation 的阅读体验。该类回归已通过 T1.1a 的 group planning / hydration 合同修复；后续扩展 grouped/windowed execution 时必须沿用同一合同。

2026-07-09 长文抽查进一步确认：长文路径仍处于中间态。translation 非短文此前是 57/58 次 `translate_unit` per-unit 调用（T3.1 已改为 grouped/windowed `translate_article` batch job，待真实 LLM 验收确认降幅）；grammar window 花费较高但大多数 window `no_op`，且缺少候选数与 selector 拒绝原因 diagnostics。该结果不推翻 adaptive orchestration 方向，只说明 T3.4a/T4/T5 仍未闭环。

### 里程碑

| # | Milestone | 目标 | 成功标准 |
|---|---|---|---|
| M0 | Baseline and Evaluation Harness | 固化新旧链路对比口径 | 同一文章可稳定比较 token、耗时、调用数、layer 数量、grammar/sentence 质量 |
| M1 | Short Article Recovery | 恢复短文解析质量、成本和首屏速度 | 短文不再 per-unit fan-out；translation 先出；grammar/sentence 接近旧 workflow 质量基线 |
| M2 | Stable Progressive Delivery | 修复页面闪烁、折叠和无序输出 | 发布结果尽量按阅读顺序出现；前端状态不因 layer 更新丢失 |
| M3 | Grouped Layer Execution | translation/vocabulary/grammar 支持 grouped/windowed 路径 | 中长文章降低调用次数；vocabulary 全文去重；window 结果仍 anchor-grounded |
| M4 | Adaptive Planner | 自动选择短文 batch、中长文 grouped/windowed、长文 section 策略 | LLM 只输出 schema profile，deterministic planner 决定执行策略 |
| M5 | Outline and Longform | 长文导航大纲与超长文 lazy enhancement | 普通导航 outline 可先用 deterministic 生成；semantic outline 只在长文/超长文启用 |
| M6 | Streaming UX Upgrade | SSE / patch delivery 逐步替代高频全量 reload | 事件可恢复；更新不闪烁；后续支持 committed patch merge |

### 推荐执行顺序

1. M0/M1 的短文合同继续保持；短文真实页面已抽查过的部分不再反复消耗真 LLM。
2. M3 长文 grouped execution：T3.1（translation）与 T3.2b（vocabulary）均已完成实施/待真实 LLM 验收。下一步补 T3.4a grammar window diagnostics。
3. T3.4a 先补 grammar window diagnostics，再调 grammar prompt / selector / budget。没有 diagnostics 时不要盲目加预算。
4. T4/M5 再补完整三模式 planner 与超长文 outline-first / section-lazy 策略。超长文不应 eager 全文增强。
5. 三种模式代码级闭环后，再统一跑真实 LLM / 页面验收，覆盖短文、长文、超长文和碎段新闻。
6. M6 先做 debounce / state preservation，再做 SSE 和 patch merge。SSE 本身不能解决全量 reload 闪烁。

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
| T3.5 | completion state finalizer | 3-6h | T3.1/T3.2/T3.4a | 所有目标 jobs/window terminal 后，record/product state、plan status、progress summary 正确收尾 |
| T4.1 | deterministic document feature extractor | 4-8h | M1/M3 | 输出 token、word、paragraph、heading、noise、block histogram、requested layers 等 profile input |
| T4.2 | bounded LLM document profiler | 4-8h | T4.1 | LLM 只返回 genre/structure/schema_risk/selective hints；失败时 deterministic fallback |
| T4.3 | strategy planner | 4-8h | T4.1/T4.2 | planner 选择 short batch、structured batch、grouped/windowed、section longform、selective longform |
| T4.4 | three-mode validation harness | 4-8h | T4.3/T5.1 | 用 fake/recorded outputs 覆盖 short/long/very-long mode 的 job plan、layer counts、usage attribution、completion state |
| T5.1 | deterministic navigation outline | 4-8h | Stable Base | 基于 heading/paragraph/unit 生成 Notion-like outline；不调用 LLM；不阻塞 translation |
| T5.2 | outline frontend contract | 4-8h | T5.1 | outline item 可跳转 anchor/unit；当前段高亮；长文显示进度感 |
| T5.3 | semantic outline worker | 4-8h | T4.3/T5.1 | 仅长文/超长文启用；输出 section title/summary/key idea/anchor range，不改变 Stable Base |
| T5.4 | lazy section enhancement | 6-10h | T5.3/T2.2 | 当前 section、用户跳转 section、Ask-relevant region 优先增强；成本按 section 控制 |
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
- T2.1 已有第一轮实现并提交（progressive reload cursor、in-flight guard、scroll/selection best-effort 恢复），但必须在 T1.1a 修复后用真实页面重新验收；前端稳定性不能替代正确的 layer output。
- 默认 worker / baseline budget 已调整为 `max_ticks=96`、`max_jobs=48`，覆盖当前 6/7 worker slots 下的中等短文样本；这是验收预算，不是最终调度模型。
- 2026-07-08 fake baseline 验收只能证明 job 数、completion 和测试 fake output 口径；不能证明真实 LLM 下 Translation Group 粒度正确。真实页面已暴露 one-unit-one-group 回归，因此此前 `translation_groups` 数量不能作为 T1.1a 验收依据。
- 2026-07-09 长文真实抽查记录 `5afd3f93-2105-47f8-9e0f-5886b7e97623`、`67ad6b9f-a45d-42b4-a8bd-d3e9c704358e`：两篇分别约 36.9k / 28.5k chars，所有 jobs succeeded，translation 当时为 57/58 次 `translate_unit`（T3.1 已改为 grouped/windowed `translate_article`，待真实 LLM 验收确认降幅）；total tokens 约 320k / 297k；grammar plan 仅发布 3 条 grammar_note + 1 条 sentence_analysis，且 17/19、13/15 grammar windows 为 `no_op`。该抽查结论用于确认 T3.4a 优先级，不作为三模式最终验收。

### 下一轮建议

1. 下一轮优先进入 T3.4a：grammar window diagnostics。先补可观测性（raw candidate count、accepted/rejected count、reject reasons、budget、strategy hash），再根据 diagnostics 修 prompt、selector 或 budget。T3.1 已完成实施/待真实 LLM 验收。
2. T3.5 completion state finalizer 可作为小任务穿插，但不要混进 T3.4a 的实现范围。
3. T5.1 deterministic navigation outline 与 T4.x planner 可在 T3.4a 后推进；semantic outline 和 lazy section enhancement 等超长文能力等 outline / planner 代码级闭环后再做。
4. T3.1 / T3.2b / T3.4a 三模式代码级闭环后，再统一跑真实 LLM / 页面验收，覆盖短文、长文、超长文和碎段新闻，验证窗口化 translation/vocabulary 与 grammar diagnostics 在真实 LLM 下的成本与质量。

### 暂缓项

- 不先做完整 adaptive planner。
- 不先做 semantic outline。
- 不先做 SSE patch merge。
- 不在 short/long/very-long 三种模式代码级闭环前，频繁真实跑长文或超长文页面验收。
- 不把短文 batch 的 whole-article computation 理解成 whole-unit translation display。
- 不用前端按标点或句子拆译文来修复后端 group 输出错误。
- 不先删除旧 AI Workflow。旧链路仍是质量和成本对照基线，除非人工确认不再需要 fallback。

### 任务派发规则

- `implementation-plan.md` 只记录任务拆分、依赖、状态和验收口径，不保存每轮 coding agent prompt。
- 每轮 prompt 由人工根据当前代码事实和最近验收结果单独生成，并通过会话发给 coding agent。
- 当前 T1.1a 已通过本轮验收，T3.1/T3.2b 已完成代码级实施并等待统一真实 LLM / 页面验收。下一轮默认派发 T3.4a grammar window diagnostics；不要把 planner / semantic outline worker / SSE patch / grammar quality tuning 混入同一任务。
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

状态：active。入口见 `spikes/README.md`；D2-S1 Reading Unit Builder 已完成并以 `accepted_with_changes` 写回模块合同。D2-P0 Plate dependency 已通过并对齐 Web 依赖到 Plate 53.x 稳定主线；D2-P1 到 D2-P4 与 fragment sanitize 的调研结论已由 TMP disposition 汇总，正式合同以本目录模块文档为准。

必做 spike：

| Spike | 输出 |
|---|---|
| Reading Unit Builder | Stable Base -> Reading Units 的 deterministic builder、UTF-16/hash 校验、focused tests |
| 依赖基线 | D3-P0 已完成：PydanticAI 1.107.0、DashScope 1.25.23、asyncpg 0.31.0；LangGraph/FastAPI/LangSmith/OpenAI 保持当前锁定版本；现有 focused tests 通过 |
| 当前成本基线 | 代表性 learning 样本的 token、latency、retry 数据 |
| DB job lease 原型 | claim、heartbeat、stale recovery、幂等 resume、cancel/supersede |
| Policy Planner / Skip Gate | deterministic policy table、Decision schema、rationale_code、pre-claim gate |
| Translation Worker structured output | PydanticAI typed output、usage limits、retry policy、provider transport |
| SSE + polling 原型 | event log、Last-Event-ID/cursor 恢复、snapshot fallback |
| Model Profile / Cost Baseline | 当前官方 model id、route lookup、fallback chain、translation benchmark |
| Prompt Cache / Usage Bucket | cache hit/miss audit、usage_by_layer、usage_by_cache_status |
| RAG substrate 原型 | Stable Base / Units -> chunk -> embedding -> Zilliz search -> cited units |
| 阿里云 RAG 可替换性 spike | 验证百炼知识库或阿里云向量检索是否可通过 adapter 替换 Zilliz |
| OSS 上传 spike | Web 直传/后端签名、对象 metadata、checksum、权限、过期清理 |
| OCR / 文档解析 spike | 百炼 OCR/VL/文档解析输出能否稳定形成 Extraction Result 和 Candidate Base |
| Anchor validation 原型 | span-bound layer 发布前必须通过稳定 anchor validation |
| Parsed Decision eval | human/LLM judge rubric 能识别“合理跳过”和“偷懒跳过” |
| Length Class 与 envelope 预算 | 文本长度分类、默认 unit range、token/cost/continuation 策略 |
| D2-P0 Plate dependency / API / license | Plate core、Markdown、comment/suggestion/AI 插件的 license、版本、API 可用性；不可默认依赖未验证商业能力 |
| D2-P1 Base Plate Snapshot | Stable Base / Units / Anchor Segments -> Base Plate Snapshot，不经过旧 `render_scene_json` |
| D2-P2 Projection Operations / Replay | domain-targeted `projection_ops`、snapshot reload、event replay、gap recovery；不持久化 raw Slate path ops |
| D2-P3 Selection / Anchor / Owner | Plate selection -> domain anchor，UTF-16/hash 校验，owner 权限拦截和后端 policy 对齐 |
| D2-P4 Ask Document Tools | `read_range`、`propose_highlight`、`propose_note`、`write_ai_supplement`、`revise_ai_annotation` 的用户确认和事件投影 |
| 旧依赖矩阵 | 标记旧 analysis/reader scene/Ask/user asset 依赖的 delete / rewrite / keep 策略 |

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

在 D3 后端骨架正式实现前，先完成后端依赖升级与能力验证。该任务不实现 Reader runtime 业务功能，只解决依赖版本、provider capability 和 lockfile 风险。

任务包：

- 盘点 `services/api` 当前 Python dependencies、lockfile 和 LLM/provider 相关封装。
- 升级 PydanticAI 到当前最新稳定 1.x；不使用 2.0 beta。验证 typed output、ToolOutput / native output fallback、usage limits、validator retry 和 provider usage extraction。
- 升级 DashScope SDK 到当前最新 patch，验证 native streaming、`reasoning_content`、tool call、usage extraction 和错误分类。
- 升级 asyncpg 到 0.31.x，验证 job lease、record-scoped transactional counter、transaction rollback no-gap 和 pool timeout 行为。
- 明确 LangGraph posture：D4/D5 不主动升级、不引入主路径；若未来保留 D6+ 入口，必须在隔离 spike 中基于当时官方文档和 lockfile 实测记录 LangGraph v1+ 能力边界、breaking risk 和不进入 durable control plane 的理由。
- 对齐 LangSmith / tracing SDK，确认 trace id 与 `ai_usage_events` / reader run/job/layer 的关联字段；只有 focused tests 需要时升级。
- 验证 provider SDK / OpenAI-compatible adapters 的 structured output、tool calls、cache usage、provider request id 和 error classification。
- 验证 FastAPI SSE response/helper、Last-Event-ID、heartbeat、disconnect handling。
- 验证 asyncpg / SQLAlchemy transaction helper、pool timeout、serializable/read committed 策略。

完成标准：

- 依赖版本和 lockfile 更新完成，或明确记录阻塞和降级方案。D3-P0 已完成，后续不得在 D4 worker 实现中临时升级核心 LLM/runtime 包。
- focused tests 覆盖 PydanticAI structured output、usage extraction、provider cache normalization、route/profile resolution。
- LangGraph 明确不进入 D4 主路径，代码中不得临时引入第三个 orchestration 控制面。
- `app/llm/routes.py` 增加 Reader worker route 的设计确认，至少覆盖 `reader_layer_translation`，并为 D5 预留 `reader_layer_vocabulary` / `reader_layer_grammar_bundle`。
- D3 runtime skeleton 可以基于已验证依赖实现，不需要中途升级核心 LLM/runtime 包。

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

## 当前下一步

进入 D5 guardrails 与运行形态收口：

1. D5-G1 parsed decision same-transaction decision 已完成；保留 orphan diagnostic 只用于历史/人为 partial state 检测。
2. D5-G2 vocabulary boundary policy 已完成；vocabulary 与 grammar 统一跳过 `fallback_window` 并记录 `boundary_low_fallback_window` diagnostics。
3. projection ops consistency spike 后置：D5 页面 smoke 继续使用 snapshot reload，只有启用 incremental applier 前才处理 race / replay / path adapter consistency。
4. D6 product hardening 再决定 `failed_terminal` 是否映射到 `action_required`、是否引入 coverage / rerun policy 和更细粒度调度 hint。
5. 保持 LangGraph D6+ 隔离 spike 口径，不在 D5 guardrails 中升级或引入。
