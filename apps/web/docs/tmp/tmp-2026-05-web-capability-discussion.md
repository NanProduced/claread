# TMP - Web 能力讨论记录

状态：`TMP`
创建时间：2026-05-14
范围：仅用于讨论记录，不作为正式产品规格或实施计划。

## 背景

Web 端还在第一版 UI/UX 设计与开发阶段。这个阶段不直接改后端 contract、workflow schema 或小程序消费逻辑，避免破坏当前 Web/FastAPI 对接和小程序 baseline。

本文先记录问题、代码事实、产品判断和后续候选方向。稳定结论后续再压缩进正式文档，例如 `backend-adaptation-plan.md`、`api-contract-audit.md`、`reader-ia.md` 或新的产品/架构文档。讨论结束后，本 TMP 文档应删除或精简。

## 当前代码事实

- `services/api/` 当前是 Claread 通用 API，不是 Web 专用后端，也不是小程序专用后端。
- Web 通过 Next.js BFF/RSC 边界调用 FastAPI，上游服务集中在 `apps/web/src/services/api/` 和 `apps/web/src/services/bff/`。
- 小程序和 Web 当前共享 `analysis_records`、`analysis_results`、`vocabulary_book`、`user_annotations`、`favorite_records`、`feedback`、quota 和 dictionary 数据。
- 当前 workflow 选择主要由 `reading_goal` 和 `reading_variant` 驱动，不由 client platform 驱动。
- 当前持久化分析结果主要是单个 `analysis_results.render_scene_json`，外加 `workflow_version` 和 `schema_version`。
- 架构文档里提到的 `analysis_render_snapshots` 还只是后续方向，当前 baseline migration 没有实现。
- 当前 learning schema 已包含 `vocab_highlight`、`phrase_gloss`、`context_gloss`、`grammar_note`、`sentence_analysis`、逐句翻译、段落和句子结构。
- 当前 academic schema 已包含更丰富的 `term_note`、`logic_note`、`interpretation_note` 和可选 `content_summary`。
- Web Reader 通过 `records.adapter.ts` 消费共享 render scene，但当前仍会扁平化或忽略一部分结构信息。
- `vocabulary_book` 已经保存单词快照和上下文相关字段，包括 `source_sentence`、`source_context`、`dict_entry_id` 和 `payload_json.source_refs`。
- 词汇 upsert 逻辑已合并 `source_refs` 和 `collected_forms`，所以“学习资产”方向已经有一部分基础。

## 讨论主题一：Web 高保真能力与共享 workflow

初始问题：

小程序因为渲染环境限制，很多批注效果做不出来，所以当前 workflow 输出 schema 有简化。Web 端是否应该写新的 workflow，以追求更好的批注效果？

当前倾向：

- 不默认拆成“Web 后端 workflow”和“小程序后端 workflow”两套。
- 更好的方向是共享一份 canonical semantic result，在语义层尽量完整，再通过 render/capability profile 生成不同客户端可消费的投影。
- 后续可以考虑的 render profile：
  - `miniprogram_compact`
  - `web_reader`
  - `share_artifact`
  - 未来 `app_reader`
- 需要保留 provenance 信息，例如生成端、请求的 render target、workflow version、prompt version、model profile、schema version。
- Web 生成的高保真结果也应该能在小程序里以紧凑/降级方式查看，而不是因为 Web 能力增强就另起一套后端。

重要限制：

当前 baseline 还没有把 canonical semantic result 和 render scene 分开持久化。后续如果要做多端 profile，需要先判断是引入 canonical result 表、`analysis_render_snapshots`，还是先做更轻量的兼容方案。

## 讨论主题二：词典功能与学习资产

初始问题：

Claread 当前使用 TECD3/DMX 转换出的 PostgreSQL dictionary 数据支持点词查询，不应该和有道词典这类专业词典竞争。真正重要的是把用户加入生词本的原词、上下文、笔记、划线句子、解析文本等“学习资产”保存好，为后续 AI 个性化学习打基础。

当前倾向：

- 点词查询保持“够用支持”，不是产品中心。
- 生词本不应只是 word list，而应保存一次次 contextual encounter。
- 应尽量保留原词形、lemma、dict entry id、来源句、段落/文章上下文、译文、来源 record、sentence id、anchor text、occurrence、收藏时间。
- 保存的单词、笔记、划线、阅读历史、反馈，后续可以成为 AI 个性化学习画像。
- 早期 Web UX 不建议做强学习压力机制，例如打卡、排名、重度 SRS 仪表盘。

后续需要复查的数据问题：

`source_context` 可能在不同调用里同时承担上下文和译文含义。后续后端/contracts 工作应考虑拆分：

- `source_sentence`
- `source_paragraph_context`
- `translation_zh`
- `analysis_record_id`
- `sentence_id`
- `anchor_text`
- `occurrence`
- `text_hash`
- `client_platform`
- `collected_at`

## 产品视角

Claread 应继续保持 reading-first：

1. 阅读理解。
2. 上下文解释与批注。
3. 用户资产：单词、笔记、划线、反馈、收藏。
4. 英语学习工具。

产品机会不是“更好的词典”，而是“结构化、漂亮、可定位的英文精读产物”。

## 用户体验视角

Web 不应被小程序 UI 限制拖住，但也不应该默认把所有能力堆在页面上。

候选 Web 原则：

- 默认页面保持安静、可读。
- Grammar X-Ray 和高保真批注采用渐进式展开。
- 用户笔记和词汇要回到阅读上下文里，而不是变成脱离原文的后台表格。
- 侧栏/旁注区域用于聚焦检查，不应变成 AI chat 或解释堆料区。
- 用户资产应方便回看、整理和导出。

## 重点讨论：Grammar X-Ray

用户当前判断：

Grammar X-Ray 很可能是 Web 端第一批值得重点评估的能力。但它必须结合当前 `grammar_note` 和 `sentence_analysis` 两种输出 schema 讨论，不能空想。

### 当前真实能力边界

- 内部 `GrammarNote` 当前包含 `sentence_id`、最多四个 exact-text `spans`、可选 per-span `role`、`label` 和 `note_zh`。
- 内部 `SentenceAnalysis` 当前包含 `sentence_id`、`label`、`analysis_zh` 和可选有序 `chunks`。
- grammar prompt 已经把局部语法点和整句结构拆开：
  - `grammar_note`：局部语法现象。
  - `sentence_analysis`：复杂句的整句结构和阅读顺序。
- 投影层会把 `grammar_note` 转成 underline `InlineMark`，再生成一个 `SentenceEntry`，其中 `content` 只保留 `note_zh`。
- 投影层会把 `sentence_analysis.chunks` 格式化成 Markdown 文本塞进 `SentenceEntry.content`，结构化 chunks 没有作为 render scene 一等字段保留下来。
- 小程序当前会用正则把这段 Markdown 再解析回 chunks 做展示，这能用，但比较脆弱。
- Web 当前的 `SentenceEntryModel` 基本只有 `id`、`sentenceId`、`entryType`、`label`、`title`、`content`，所以也主要是文本展示。
- Web 当前能展示 `multi_text` grammar marks 的结构线索，但这依赖 `spans.role`，而当前 prompt/examples 并没有把丰富 role 作为强约束。

结论：

当前 schema 可以支持 `X-Ray-lite`：锚点语法说明、简单多片段结构线索、有序句子 chunks。它还不能支持可信的完整 Grammar X-Ray，例如从句层级、依赖关系、主干/修饰结构、连接关系、阅读步骤和可视化结构图。

### 真实数据库抽样 - 2026-05-14

抽样范围：

- 本地 PostgreSQL `analysis_results` 共 7 条结果。
- 解析 `render_scene_json` 后，有 6 条包含 `sentence_entries`。
- 共 13 个 `sentence_entries`：11 个 `grammar_note`，2 个 `sentence_analysis`。
- 共 34 个 `inline_marks`：17 个 `vocab_highlight`，11 个 `grammar_note`，5 个 `phrase_gloss`，1 个 `context_gloss`。
- 11 个 `grammar_note` marks 中，10 个是单段 `text` anchor，1 个是 `multi_text` anchor。
- 当前本地样本中只有 1 个 `multi_text` grammar mark，且两个 parts 的 `role` 都为 `null`。
- 有 2 个 `DRAFT_VALIDATION` warning，都是模型输出的 span/text 无法在原句中精确命中。

抽样发现：

- `grammar_note` 的真实输出已经很接近小程序端“语法卡片”形态：一个原文下划线锚点，配一个中文解释卡。
- 当前 `grammar_note` 标签覆盖了中国用户熟悉的语法学习项，例如：
  - `be useful for + doing`
  - `并列宾语`
  - `keep + 宾语 + 形容词作宾补`
  - `while + 现在分词表示伴随`
  - `动名词短语作主语`
  - `宾语从句`
  - `时间状语从句`
  - `并列宾语从句`
  - `同位语后的限制性定语从句`
  - `名词性从句中的倒装语序`
- `sentence_analysis` 的真实输出只有 2 条，均来自 `exam/tem` 样本。它们是“解释段落 + Markdown chunks”：
  - 例如主语、系动词+表语、地点状语。
  - 例如主语+谓语+宾语、定语从句。
- `sentence_analysis` 已经适合小程序卡片拆分，但它的结构化数据在投影层被压进 Markdown，Web 不应在当前阶段继续依赖它做高保真 X-Ray。
- 当前 `grammar_note` 的 `inline_mark.clickable=false`，说明它现在不是一个可交互结构图入口，而是静态提示入口。
- 本地库还暴露了一个数据形态问题：部分 `render_scene_json` 是 JSONB 字符串包了一层 JSON，而不是直接 JSON object。后续需要单独查入库路径，但这不是当前 X-Ray 讨论的核心。

对 X-Ray 试验的直接启发：

- 如果按用户建议“先不动 `sentence_analysis`，用 `grammar_note` 做 X-Ray 增强试验”，现有数据支持这个方向。
- 第一版 X-Ray 不应试图覆盖整句拆解，而应对一个 `grammar_note` 背后的局部结构做可视化解释。
- 真实样本里的 `grammar_note` 已经有明确教学标签，缺的是可视化所需的结构 payload，例如 role、结构关系、考点功能、阅读动作。
- 幻觉风险已经在本地样本中出现：span 对不上会产生 warning。X-Ray 结构比普通 note 更复杂，所以必须把 exact span resolve 作为硬门槛。

### X-Ray 标注数据从哪里来

X-Ray 数据不应该来自前端猜测，也不应该靠正则或 DOM 解析临时拼出来。它应该来自后端 workflow 生成的结构化语义数据，再由投影层生成不同客户端能消费的 render view。

可选路径有两条：

路径 A：在当前 grammar workflow 中增强 `grammar_note`。

- 保持现有 grammar agent 和 `sentence_analysis` 输出不变。
- 当模型选择输出某个 `grammar_note` 时，额外判断它是否适合 X-Ray。
- 适合时，在 `grammar_note` 中输出可选 `xray` payload。
- 对 `grammar_note.spans.role` 提高要求，让多 span 语法点能表达“被修饰对象、修饰成分、连接词、指代对象”等角色。
- 优点：改动相对渐进，符合当前真实数据以 `grammar_note` 为主的现状，也符合用户“不先动 sentence_analysis”的倾向。
- 缺点：它只能做局部语法点 X-Ray，不适合承担整句结构图。

路径 B：新增专门的 `sentence_xray` / `grammar_xray` 结构。

- 在 workflow 中新增一种结构化 annotation 类型，而不是把所有 X-Ray 信息塞进 `grammar_note` 或 `sentence_analysis`。
- `sentence_xray` 负责描述整句结构，可能包含 `nodes`、`edges`、`reading_steps`。
- `grammar_note` 继续负责局部语法点，但可以引用 X-Ray node id。
- `sentence_analysis` 继续负责自然语言解释，也可以由 X-Ray 数据降级生成。
- 优点：职责清楚，更适合 Web 高保真渲染和 share artifact。
- 缺点：需要 schema、prompt、postprocess、render scene、Web、小程序降级显示一起设计，不能在当前 UI/UX 阶段直接上。

当前更稳妥的判断：

短期不要新开 Web 专用 workflow，也不要先动 `sentence_analysis`。先把它理解为“当前 grammar workflow 中 `grammar_note` 的增强试验”。也就是说，模型仍然在当前 grammar agent 中选择值得标注的语法点，但当某个 `grammar_note` 适合 X-Ray 时，额外输出一份可视化结构 payload。

后续如果 X-Ray 从“局部语法点解释”升级为“整句结构图”，再重新评估是否新增 `sentence_xray`。

### `grammar_note` 应该怎么升级

`grammar_note` 适合继续表达局部语法现象，而不是整句结构图。

可以考虑增强：

- `grammar_tag`：语法类型，例如 relative_clause、participle_phrase、inversion、passive_voice、appositive、nominal_clause、nonfinite、comparison、parallelism、discourse_connector。
- `function_zh`：这个结构在句子里起什么作用。
- `reading_hint_zh`：用户读到这里应该怎么处理。
- `rule_zh`：可选，偏考试/教学场景才需要。
- `spans.role`：多 span 情况下更明确标注每段角色。
- `xray_node_refs`：未来如果有 X-Ray node，可以引用对应结构节点。

但不建议让 `grammar_note` 承担主谓宾、从句层级、依赖图这类整句职责。

### `grammar_note` X-Ray 试验方案

试验目标：

在不改 `sentence_analysis` 的前提下，让 `grammar_note` 从“文本说明卡片”升级为“可视化语法点卡片”。它仍然解释一个局部语法点，但前端可以展示结构关系。

建议新增的概念字段：

- `xray_mode`：是否适合 X-Ray，例如 `none`、`local_pattern`、`clause_link`、`modifier_scope`、`word_order_shift`。
- `grammar_tag`：语法点类型，例如 `relative_clause`、`object_clause`、`adverbial_clause`、`participle_phrase`、`gerund_subject`、`object_complement`、`inversion`、`parallel_objects`、`appositive`。
- `pattern`：中文用户熟悉的结构公式，例如 `keep + 宾语 + 宾补`、`while + doing`、`how/that 并列宾语从句`。
- `xray_parts`：可视化片段，每个片段必须能 exact match 原句。
- `xray_relations`：片段之间的关系，例如 `修饰`、`补充说明`、`作宾语`、`作主语`、`连接并列`、`触发倒装`。
- `read_hint_zh`：阅读动作，例如“先抓主干，再看 while 后面的伴随动作”。
- `exam_hint_zh`：可选，只在考试 variant 需要，例如“高考常考宾补识别”。

候选结构示例：

```json
{
  "type": "grammar_note",
  "sentence_id": "s1",
  "spans": [
    { "text": "Claread keeps the original passage visible", "role": "main_pattern" }
  ],
  "label": "keep + 宾语 + 形容词作宾补",
  "note_zh": "这是 keep 的一个常用句型...",
  "xray": {
    "mode": "local_pattern",
    "grammar_tag": "object_complement",
    "pattern": "keep + 宾语 + 宾补",
    "parts": [
      { "id": "p1", "text": "keeps", "role": "谓语动词" },
      { "id": "p2", "text": "the original passage", "role": "宾语" },
      { "id": "p3", "text": "visible", "role": "宾语补足语" }
    ],
    "relations": [
      { "from": "p3", "to": "p2", "type": "说明状态" }
    ],
    "read_hint_zh": "读到 keep 后先找宾语，再看后面的形容词说明宾语保持什么状态。"
  }
}
```

小程序降级方式：

- 继续展示原有 `label` + `note_zh` 卡片。
- 如果支持轻量增强，可以只展示 `pattern` 和 `read_hint_zh`。
- 不要求小程序渲染 relations 图。

Web 增强方式：

- 正文锚点仍使用 underline。
- 点击/展开 grammar note 后展示：
  - 结构公式。
  - 原句片段 chips。
  - 片段角色。
  - 简单关系箭头或左右/上下结构。
  - 一句阅读提示。

### `sentence_analysis` 应该怎么升级

`sentence_analysis` 更接近 Grammar X-Ray 的基础，因为它已经有整句 `chunks`。

可考虑的第一步升级：

- 保留 `analysis_zh` 作为用户可读总结。
- 将 `chunks` 从 Markdown 文本恢复为 render scene 的结构化字段。
- 每个 chunk 至少需要稳定 id、原文 text、occurrence、label、role、是否主干、中文解释。
- 如果要支持初步可视化，可以再增加 `level`、`parent_id`、`relation_to_parent`。

这样小程序可以继续展示列表卡片，Web 可以展示阅读顺序、结构分层和 hover/focus 对应关系。

当前用户倾向：

- 暂时不建议动 `sentence_analysis`。
- `sentence_analysis` 继续承担长难句卡片拆解能力。
- X-Ray 先以 `grammar_note` 的局部结构增强做试验。
- 后续只有当 X-Ray 需要扩展到整句层级时，再重新讨论 `sentence_analysis` 与 `sentence_xray` 的关系。

### 语言理解与可视化风险

Grammar X-Ray 和正则可视化不同。正则、JSON、AST 这类对象有明确语法规则和确定结构；自然语言句法存在歧义、层级不稳定、不同教学体系标签不一致的问题。

因此 X-Ray 的渲染效果不只取决于前端实现，也取决于我们对英语阅读教学和句法解释的产品抽象是否清楚。

需要避免的问题：

- 为了做出漂亮图形，强行把所有句子都套进固定主谓宾模板。
- 把 LLM 的自然语言解释伪装成确定的语法树。
- 节点太多，用户看图比看句子还累。
- 标签体系过于学术，偏离 Claread “帮助读懂” 的目标。
- 不同 reading variant 下用同一套深度，导致 beginner 太难、exam/academic 又不够细。

更合理的可视化原则：

- 以“帮助读懂句子”为目标，不以“生成完整句法树”为目标。
- 优先展示主干、修饰、从句、连接、指代、逻辑转折这些阅读价值最高的信息。
- 对不确定结构保持克制，可以显示为“阅读建议”而不是确定语法结论。
- 默认只对复杂句或用户主动展开的句子生成/展示 X-Ray。
- Web 端可以高保真，小程序端只展示紧凑版 chunk list 和关键语法点。

### 中国英语学习语境研究迁移

中国英语学习和考试语境下的 Grammar 外延、`grammar_note.xray` 解析类型、渲染类型和理论支撑，已迁移到长期参考文档：

- `docs/reference/grammar-xray/chinese-exam-grammar-xray.md`

### 语法可视化调研迁移

语法可视化、中文考试语境下的语法外延、`grammar_note.xray` 解析类型与渲染类型，已迁移到长期参考文档：

- `docs/reference/grammar-xray/chinese-exam-grammar-xray.md`

### variant 与 X-Ray 模式

用户倾向：

- variant 只决定标注哪些语法点。
- variant 不应改变 X-Ray 这种解析模式本身。

建议解释：

- `grammar_note.xray` 的数据结构保持统一。
- `reading_variant` 影响 grammar agent 的选择策略和文案语气：
  - beginner：少术语，多读法提示。
  - intermediate：术语 + 直白解释。
  - gaokao/kaoyan/tem：更偏考点和长难句识别。
  - academic：更偏信息结构和论证功能。
- 前端 X-Ray 组件不因 variant 改结构，只根据字段有无展示 `exam_hint_zh`、`rhetoric_hint_zh` 等附加信息。

### X-Ray 生成场景

需要明确两种生成方式：

方式 A：workflow 一次性生成。

- 在文章分析 workflow 中，模型直接为高价值 `grammar_note` 输出 `xray`。
- 用户打开 Reader 时即可看到 X-Ray。
- 适合高置信、常见、教学价值稳定的语法点，例如宾语从句、定语从句、非谓语、宾补、倒装、并列结构。
- 风险是会增加生成成本，并且错误结构会被持久化。

方式 B：用户选句/点开后再调用 AI 生成。

- 用户选中一句或点击某个 grammar note 后，按需生成 X-Ray。
- 适合更深、更重、更交互的解释。
- 优点是更贴合用户意图，不必给所有句子都生成。
- 缺点是有等待时间，需要任务状态、缓存和失败降级。

建议混合策略：

- 第一版 `grammar_note` X-Ray 试验用方式 A，但只对高置信语法点生成轻量 xray。
- 后续 Web 可以加方式 B：用户对任意句子或片段请求“深度 X-Ray”。
- 方式 B 的结果应缓存为用户资产或 render snapshot，而不是每次临时消失。

### 幻觉与质量门槛

X-Ray 错误比不生成更伤用户体验，因为它会用视觉结构强化错误理解。

必须设置硬门槛：

- `xray.parts[*].text` 必须 exact match 原句。
- 每个 part 必须能 resolve 到具体 sentence span。
- 多 part 不应出现不合理 overlap，除非显式声明父子包含关系。
- relation 两端必须引用存在的 part id。
- part 数量要有限制，避免图形过载。
- 无法校验时，不展示 X-Ray，只降级为普通 `grammar_note` 卡片。
- 对不确定结构，文案使用“可理解为/阅读时可先看作”，不要画成确定依存关系。

### 分阶段路径

阶段 0：当前 Web UI/UX 阶段。

- 只设计 `X-Ray-lite`。
- 使用已有 `sentence_analysis` 文本做阅读顺序列表。
- 使用已有 `grammar_note` 锚点做局部语法说明。
- 使用已有 `multi_text` role 做结构线索。
- 页面文案和交互不能暗示已经有完整句法图谱。

阶段 1：后续 schema 兼容升级。

- 不动 `sentence_analysis`。
- 在 `grammar_note` 上新增可选 `xray` payload。
- 小程序继续降级为普通语法卡片。
- Web 在语法卡片详情里展示 X-Ray 局部结构。

阶段 2：真正的 X-Ray 模型。

- 如果局部 `grammar_note.xray` 试验有效，再评估是否新增 `sentence_xray`。
- `sentence_xray` 只用于整句层级结构，不替代 `grammar_note`。
- `sentence_analysis` 继续作为长难句自然语言拆解卡片。

阶段 3：render profile / snapshot。

- `web_reader`：交互式结构层、正文高亮、旁注联动。
- `miniprogram_compact`：紧凑列表、关键语法点、必要降级。
- `share_artifact`：可分享的句子 X-Ray 卡片或精读卡片。

### 后续需要确认的问题

1. `grammar_note.xray` 第一批支持哪些语法点：宾补、非谓语、定语从句、宾语从句、状语从句、倒装、并列结构？
2. X-Ray 第一版视觉形态是片段 chips + 关系线，还是结构公式 + 层级卡片？
3. workflow 一次性生成的 X-Ray 应限制在多少个 grammar notes 内？
4. 用户主动请求深度 X-Ray 的结果是否要保存为用户资产？
5. 上线 X-Ray 前需要哪些校验：exact span resolve、overlap 检查、节点数量上限、禁止 hallucinated substring？
6. Web 的 `grammar_note.xray` 在小程序里是否只显示 `pattern` 和 `read_hint_zh`？

## 调研综合结论 - 2026-05-14

### 竞品模式

外部产品文档与本地竞品分析大体一致：

- Readwise Reader 把 annotation 当成一等资产，支持高亮、标签、笔记和导出。
- Readlang 验证了点词翻译和 context-aware translation，但核心仍偏语言学习和 flashcard。
- LingQ 验证了 saved terms 和 SRS，但 Claread 早期不应把强复习压力做成中心。
- NotebookLM 验证了生成式学习产物和分享能力，但它是多来源知识工作台，Claread 不应把 Reader 改成 NotebookLM。
- Goodnotes 验证了导出形态和文档质感的重要性。

对 Claread 的启发：

可以借鉴 annotation asset、context-aware lookup、artifact output，但差异化仍应是面向中文用户的结构化英文精读。

### 现阶段可安全探索的 Web 工作

不改后端/schema 时，可以做：

- Reader 展示控制：译文显示、字号、主题、侧栏开合、旁注过滤、布局切换。
- 基于现有 BFF 的词典 popover UX：查询、消歧义、收藏状态、完整词条入口。
- 基于现有 annotations API 的句子级笔记和划线体验。
- 资料库和词汇页体验：分组、排序、卡片、空状态、回到来源记录。
- 现有 academic 字段的更好展示，例如 `term_note`、`logic_note`、`interpretation_note`、`content_summary`。
- 当前 schema 下的 `X-Ray-lite` 展示探索，但必须避免冒充完整 X-Ray。

需要后续后端/schema 工作：

- 真正的 per-client render profile 或 render snapshots。
- 高保真 Grammar X-Ray。
- 精确 text-range annotation 持久化。
- records search/filter。
- source/render metadata，例如 `requested_render_target`、`render_profile`、`created_client_type` 和更完整的 `source_metadata`。

小程序兼容风险：

- 不要为了 Web-only 需求随意改变共享 `render_scene_json`。
- 新增 common `visual_tone`、`annotation_type`、anchor kind 时必须同步小程序 DTO/渲染逻辑。
- offset-only 或 DOM range anchor 不适合当前小程序 inline mark 假设。
- `source_type` 扩展也需要协调，因为后端和小程序 DTO 当前不一定完全一致。

## 后续讨论候选题

1. Grammar X-Ray 是否作为 Web 稳定 UI 之后的第一个 signature capability？
2. 第一轮 X-Ray 后端升级是否先做 `grammar_note.xray`，暂不改 `sentence_analysis`？
3. 词汇/笔记最少需要保存哪些上下文，才能支持后续 AI 个性化？
4. 第一种 share artifact 应该是什么：句子 X-Ray 卡、完整精读笔记、词汇上下文卡，还是文章概要？
5. 最小 render profile/snapshot contract 应该长什么样，才能避免 Web 后端分叉又不过度设计？

## 已检查外部参考

- Readwise Reader：highlights、tags、notes、keyboard reading、export。
- Readlang：features 和 context-aware translations。
- NotebookLM Help：Audio Overview 分享和 public/featured notebooks。
- Goodnotes Help：document/page export formats。
- LingQ Help：SRS review schedule。
- Grammar X-Ray 相关外部参考已迁移到 `docs/reference/grammar-xray/chinese-exam-grammar-xray.md`。

## 当前 Web UI/UX 阶段不做的事

- 不改后端 schema。
- 不拆 Web 专用 workflow。
- 不改会破坏当前 Web/FastAPI 对接的 API contract。
- 不破坏小程序 contract。
- 不把 Claread 做成专业词典产品。
- 不把 Reader 改成 AI chat 中心。
