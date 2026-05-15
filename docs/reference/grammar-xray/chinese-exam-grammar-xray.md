# 中国英语考试语境下的 Grammar X-Ray 调研

文档分类：产品与教学法研究参考
创建时间：2026-05-14
状态：参考资料，不是实现规范

## 研究目的

Claread 面向中文用户做英文透读。Web Grammar X-Ray 如果只按狭义语法学中的 morphology / syntax 做句法图，会低估中国英语学习和考试场景中“语法”一词的实际外延。

本研究用于为后续 `grammar_note` 结构化解析、`grammar_note.xray` 数据设计、Web X-Ray 渲染类型和 prompt 策略提供理论支撑。

## 核心结论

中国英语学习和考试语境中的“语法”经常不是狭义的句法/词法，而更接近“词汇量以外的一切语言规则集合”。它至少包含：

- 核心语法：形态变化、句法结构、从句、非谓语、倒装、强调、省略。
- 词汇语法：搭配、动词句型、介词框架、固定表达、惯用表达。
- 语义辨析：熟词僻义、近义功能词、连接词差异、否定/比较/情态作用范围。
- 语篇语法：指代、替代、省略、衔接、连贯、段落功能、篇章结构。
- 翻译与表达转换：英汉结构转换、词性转换、分句拆合、语篇连贯。
- 语用与修辞：语体、正式度、作者态度、修辞手法、表达目的。

因此，Claread 的 Grammar X-Ray 不应只是“拆分 + 箭头”的句法树。更合适的定位是：

> 面向中国英语学习者的语言规则透视层。

## 资料依据

### 高中与高考

《普通高中英语课程标准（2017 年版 2020 年修订）》将英语课程内容组织为主题语境、语篇类型、语言知识、文化知识、语言技能和学习策略等要素。其中语言知识覆盖语音、词汇、语法、语篇和语用知识。

对 Claread 的启发：

- 语法应放在语篇和语用场景中解释，不应只给规则定义。
- `grammar_note` 应说明当前结构如何帮助理解当前句子或段落。
- Web X-Ray 可以覆盖指代、连接、省略、替代、篇章衔接等传统语法卡片较少呈现的内容。

参考：

- [普通高中英语课程标准（2017 年版 2020 年修订）PDF - 人民教育出版社](https://www.pep.com.cn/xw/zt/rjwy/gzkb2020/202205/P020220517522153664167.pdf)
- [《普通高中英语课程标准》摘录页](https://www.kunmingchi.com/archives/m536dx2vmrdjx2kg7098.html)
- [上海高考英语“六选四”语篇衔接研究](https://www.fx361.com/page/2023/1231/25666563.shtml)

### 中国英语能力等级量表

《中国英语能力等级量表》将语言能力中的知识拆为组构知识和语用知识。组构知识包含语法、词汇、句法、篇章、修辞/会话、衔接等；语用知识包含功能、语体、语域、惯用表达、文化参照和修辞。

对 Claread 的启发：

- `grammar_note` 可以覆盖“句内结构”和“篇章组织”两类规则。
- `grammar_note.xray` 的 taxonomy 不应只包含 syntax，应显式保留 cohesion、register、rhetorical move 等上层能力。

参考：

- [中国英语能力等级量表 GF 0018-2018 - 中国教育考试网 PDF](https://cse.neea.edu.cn/res/ceedu/1804/74f837ffd1d18bd6f2d625d7bee57868.pdf)

### 大学英语四、六级

CET 由教育部教育考试院组织实施，目标是检测在校大学生英语能力。官方页面列出的笔试结构包括写作、听力、阅读理解和翻译；阅读包含词汇理解、长篇阅读和仔细阅读，翻译为段落汉译英。

对 Claread 的启发：

- CET 场景中的语法不只出现在独立语法题里，而是融入阅读、写作和翻译。
- 词汇理解、段落翻译和篇章匹配都需要词汇语法、指代、衔接、句间逻辑。
- X-Ray 应能支持快速定位“为什么这里这样搭配/连接/翻译”。

参考：

- [CET 是什么 - 中国教育考试网](https://cet.neea.edu.cn/xhtml1/report/16123/174-1.htm)
- [CET 考试大纲页 - 中国教育考试网](https://cet.neea.edu.cn/xhtml1/folder/16113/1588-1.htm)
- [CET4 笔试考核内容 - 中国教育考试网](https://cet.neea.edu.cn/xhtml1/report/16123/196-1.htm)
- [CET6 笔试考核内容 - 中国教育考试网](https://cet.neea.edu.cn/xhtml1/report/16123/201-1.htm)
- [全国大学英语四、六级考试大纲（2016 年修订版）PDF](https://www.neea.edu.cn/res/Home/1704/55b02330ac17274664f06d9d3db8249d.pdf)

### 考研英语

考研英语强调在特定情境下有效运用词汇、语法、语篇和语用知识。英语（一）阅读要求理解复杂材料、文章结构、上下文逻辑关系、修辞手法和论证方法；翻译部分明确考查理解语篇中概念或结构较复杂的语句并译成汉语。

对 Claread 的启发：

- `kaoyan` 场景中 X-Ray 应优先服务长难句、结构复杂语句和翻译映射。
- 重要渲染类型应包括主干骨架、从句地图、修饰挂靠、翻译拆分。

参考：

- [2025 全国硕士研究生入学统一考试英语（一）考试大纲 PDF](https://www.juyingonline.com/upload/202409/12/202409121759284166.pdf)
- [中国研究生招生信息网：2013 年考研英语（一）大纲](https://yz.chsi.com.cn/kyzx/en/201209/20120918/343670177.html)

### 专四专八

TEM 场景保留更强的语言知识、语法句法、搭配、修辞和语病识别要求。TEM4 关注基本语法和句法、词汇搭配；TEM8 进一步关注语篇结构、语言特点、修辞手法、汉译英和写作表达。

对 Claread 的启发：

- `tem` 场景下 `grammar_note.xray` 可以覆盖 rhetoric_style、error_diagnosis、pragmatics_register。
- 但这些高级能力不应默认铺满 Reader，应作为高价值点或按需展开。

参考：

- [吉林大学本科生院：全国高等学校英语专业四、八级考试介绍](https://kszx.jlu.edu.cn/info/1043/2635.htm)
- [高校英语专业四级考试内容](https://tem.fltonline.cn/?p=74688)
- [高校英语专业八级考试内容](https://tem.fltonline.cn/?p=74676)

## 语法可视化方法调研

### 不直接照搬传统 sentence diagramming

传统 sentence diagramming 的价值在于把句子拆成稳定功能成分，帮助视觉型学习者看到结构关系。但 Reed-Kellogg 图学习成本高，对现代 Web 阅读器和中文学习者不够友好。

Claread 可借鉴“结构可视化”的思想，不照搬图形形式。

参考：

- [Guide to Grammar and Writing: Diagramming Sentences](https://guidetogrammar.org/grammar/diagrams/diagrams.htm)
- [Grammar Alive! Chapter 7: Diagramming Sentences](https://wacclearinghouse.org/docs/books/grammar/chapter7.pdf)

### Clause structure 与 verb pattern 是更适合 Web 的基础

British Council 的 clause structure/verb pattern 资料强调：英语 clause 至少包含 subject noun phrase 和 verb phrase，很多 clause 还包含 object、complement 或 adverbial；不同动词决定不同结构模式。

这与中国学生熟悉的“先抓主干，再看谓语后面接什么”高度兼容。

参考：

- [British Council LearnEnglish: Clause structure and verb patterns](https://learnenglish.britishcouncil.org/free-resources/grammar/english-grammar-reference/clause-structure-verb-patterns)
- [Cambridge Dictionary Grammar: Verb patterns](https://dictionary.cambridge.org/grammar/british-grammar/verb-patterns-verb-)
- [Cambridge Dictionary Grammar: Clauses and sentences](https://dictionary.cambridge.org/us/grammar/british-grammar/clauses-and-sentences)

### Relative clauses 适合做“边界与功能”型 X-Ray

Purdue OWL 和 Cambridge Grammar 都强调定语从句的 defining / non-defining 区分、关系词选择、逗号和意义差异。

这类语法点不适合只画箭头，更适合显示：

- 修饰对象。
- 是否限制意义。
- 逗号是否改变信息功能。
- `that / which / who` 的选择。

参考：

- [Purdue OWL: Defining vs. Non-Defining Clauses](https://owl.purdue.edu/owl/general_writing/grammar/relative_pronouns/defining_vs_non_defining.html)
- [Cambridge Dictionary Grammar: Relative clauses](https://dictionary.cambridge.org/grammar/british-grammar/relative-clauses_1)

### Cohesion 与 reference chain 是中文考试环境里的高价值能力

高考阅读“选句填空”、考研阅读 B 节、CET 长篇阅读都强调文章结构、上下文逻辑、指代和衔接。

这说明 Grammar X-Ray 需要跨句能力，而不应只停留在单句结构。

参考：

- [British Council TeachingEnglish: Cohesion](https://www.teachingenglish.org.uk/professional-development/teachers/teaching-knowledge-database/c/cohesion)
- [上海高考英语“六选四”语篇衔接研究](https://www.fx361.com/page/2023/1231/25666563.shtml)

## `grammar_note.xray` 解析类型建议

建议用 `dimension` 表达中国英语学习语境下的规则外延，用 `subtype` 表达具体语法点，用 `render_type` 决定 Web 渲染方式。

### 一级维度

| dimension | 中文名 | 覆盖内容 |
|---|---|---|
| `structural_rule` | 核心语法 | 形态、句法、从句、非谓语、倒装、强调、省略 |
| `lexico_grammar` | 词汇语法 | 搭配、动词句型、介词框架、固定表达 |
| `semantic_discrimination` | 语义辨析 | 熟词僻义、近义辨析、情态强度、否定/比较作用范围 |
| `discourse_grammar` | 语篇语法 | 指代、衔接、连贯、段落功能、篇章逻辑 |
| `translation_mapping` | 翻译映射 | 英汉结构转换、词性转换、分句拆合、自然译法 |
| `pragmatics_rhetoric` | 语用修辞 | 语域、正式度、作者立场、修辞手法 |

### 二级解析类型

| subtype | dimension | 说明 |
|---|---|---|
| `morphology` | `structural_rule` | 时态、语态、词形变化、比较级、数 |
| `sentence_skeleton` | `structural_rule` | 主语、谓语、宾语、表语、宾补、状语 |
| `clause_structure` | `structural_rule` | 主句、从句、并列、嵌套 |
| `relative_clause` | `structural_rule` | 定语从句、限定/非限定、关系词 |
| `nominal_clause` | `structural_rule` | 主语/宾语/表语/同位语从句 |
| `adverbial_clause` | `structural_rule` | 时间、原因、条件、让步、目的、结果 |
| `non_finite` | `structural_rule` | 不定式、动名词、分词短语 |
| `inversion_emphasis` | `structural_rule` | 倒装、强调、前置 |
| `ellipsis_substitution` | `structural_rule` | 省略、替代 |
| `verb_pattern` | `lexico_grammar` | `V + doing`、`V + to do`、`V + O + C` |
| `collocation` | `lexico_grammar` | 动名、形名、副形、固定搭配 |
| `preposition_pattern` | `lexico_grammar` | `depend on`、`responsible for` |
| `phrasal_verb` | `lexico_grammar` | 短语动词 |
| `fixed_expression` | `lexico_grammar` | 半固定表达、考试常见表达 |
| `polysemy` | `semantic_discrimination` | 熟词僻义、上下文义 |
| `connector_contrast` | `semantic_discrimination` | `but/however`、`because/since/as/for` |
| `scope` | `semantic_discrimination` | 否定、比较、程度副词作用范围 |
| `modality` | `semantic_discrimination` | 情态动词强度和立场 |
| `reference` | `discourse_grammar` | `it/this/that/they/which` 指代 |
| `cohesion` | `discourse_grammar` | 连接、省略、替代、复现、同义复现 |
| `discourse_relation` | `discourse_grammar` | 因果、转折、让步、递进、例证 |
| `paragraph_role` | `discourse_grammar` | 论点、证据、限定、反驳、总结 |
| `translation_shift` | `translation_mapping` | 英汉语序、词性、被动/主动、拆合句 |
| `rhetoric_style` | `pragmatics_rhetoric` | 排比、隐喻、强调、讽刺、语体选择 |

## Web 渲染类型建议

不同解析类型不应统一渲染成“拆分 + 箭头”。建议拆成多种可复用渲染类型：

| render_type | 适用内容 | UI 形态 |
|---|---|---|
| `sentence_skeleton` | 主干、五大句型、长难句入口 | 主轴展示主语、谓语、宾语/表语/宾补 |
| `clause_map` | 主从句、嵌套从句、并列结构 | 层级块、折叠树、连接词入口 |
| `modifier_attachment` | 后置定语、非谓语、介词短语、同位语 | 挂靠线、括号层、修饰目标 |
| `pattern_slots` | 动词句型、介词搭配、形容词补足语 | 结构模板 + 当前填槽 |
| `collocation_rail` | 固定搭配、强搭配、短语动词 | 原文下划线 + 搭配轨道 |
| `scope_overlay` | 否定、比较、程度、情态作用范围 | 彩色范围覆盖 |
| `contrast_card` | 近义辨析、连接词区别、误译点 | A/B 对照、边界说明 |
| `reference_chain` | 指代、替代、复现 | 跨句链路、编号 badge |
| `discourse_link` | 因果、转折、递进、让步 | 句间箭头、段落边栏标记 |
| `translation_split` | 翻译拆解、顺译、结构转换 | 英文结构与中文自然译法对照 |
| `paragraph_role_chip` | 论点、证据、限定、反驳 | 句首或边栏功能标签 |
| `progressive_reveal` | 复杂句综合拆解 | 主干 -> 修饰 -> 从句 -> 逻辑逐步展开 |

### 推荐映射

| subtype | 默认 render_type |
|---|---|
| `sentence_skeleton` | `sentence_skeleton` |
| `clause_structure` | `clause_map` |
| `relative_clause` | `modifier_attachment` 或 `clause_map` |
| `non_finite` | `modifier_attachment` |
| `verb_pattern` | `pattern_slots` |
| `collocation` | `collocation_rail` |
| `polysemy` | `contrast_card` |
| `connector_contrast` | `contrast_card` |
| `scope` | `scope_overlay` |
| `reference` | `reference_chain` |
| `discourse_relation` | `discourse_link` |
| `translation_shift` | `translation_split` |
| `paragraph_role` | `paragraph_role_chip` |

## `grammar_note.xray` 概念模型

以下仅是研究层面的概念模型，不是当前实现 schema。

```ts
type GrammarXray = {
  dimension:
    | "structural_rule"
    | "lexico_grammar"
    | "semantic_discrimination"
    | "discourse_grammar"
    | "translation_mapping"
    | "pragmatics_rhetoric"
  subtype: string
  render_type: string
  pattern?: string
  parts: Array<{
    id: string
    text: string
    role: string
    occurrence?: number
    explanation_zh?: string
  }>
  relations?: Array<{
    from: string
    to: string
    type: string
    label_zh?: string
  }>
  read_hint_zh: string
  learner_risk_zh?: string
  exam_hint_zh?: string
  translation_hint_zh?: string
  confidence?: number
}
```

设计原则：

- `parts[*].text` 必须来自原句或相邻上下文的 exact span。
- `dimension` 决定产品分类。
- `subtype` 决定语法/规则类型。
- `render_type` 决定 Web 组件形态。
- `read_hint_zh` 必须解释“当前句怎么读”，不能只写教科书定义。
- `exam_hint_zh` 由 variant 策略决定是否生成，但不改变 X-Ray 解析模式本身。

## 生成策略

### 适合 workflow 一次性生成

优先自动生成高置信、低成本、复用价值高的轻量 X-Ray：

- 主干骨架。
- 常见从句结构。
- 非谓语修饰。
- 动词句型和介词搭配。
- 高频固定表达。
- 明确指代关系。
- 基础语篇连接关系。
- 熟词僻义或明显误译点。

自动生成应限量。建议每句默认 0-3 个 X-Ray 点，每篇文章优先保留最影响理解的点。

### 适合用户按需生成

以下能力成本高、个体差异大，适合用户点击后生成：

- 深度语义辨析。
- 多方案翻译比较。
- 语法专题讲解。
- 相似句型迁移练习。
- 用户历史相关的错误诊断。
- 段落级或篇章级逻辑链总结。
- 作者立场和修辞深挖。

按需生成结果如果对用户有长期价值，应考虑保存为用户学习资产或 render snapshot。

## 质量与降级规则

X-Ray 错误比不生成更伤用户体验，因为视觉结构会强化错误理解。建议设置硬门槛：

- 每个 X-Ray item 必须绑定原文 evidence span。
- span 必须能 resolve 到 sentence 或 paragraph。
- relation 两端必须引用存在的 part id。
- 低 confidence 不进入默认 UI。
- 同一句自动展示数量有限。
- 无法校验时降级为普通 `grammar_note`。
- 对不确定结构用“可理解为/阅读时可先看作”，不画成确定句法关系。
- 中文解释必须服务当前句理解，禁止只输出术语定义。
- 同一篇文章内术语保持一致。

可用于后续 LLM-as-a-Judge 的维度：

- Groundedness：解释是否有文本依据。
- Usefulness：是否帮助中国学习者读懂当前句。
- Specificity：是否解释当前句，而不是泛泛讲规则。
- Brevity：是否适合 UI 展示。
- Translation Alignment：是否与自然中文译法一致。
- Non-overclaiming：是否避免过度推断作者意图或背景。

## 对 Claread 产品的影响

1. Grammar X-Ray 的差异化不在于画复杂树图，而在于理解中国英语学习者把哪些问题看作“语法”。
2. `grammar_note` 可以保持技术名称不变，但产品含义应扩展为“语言规则标注”。
3. Web 端应根据 `dimension` 和 `render_type` 使用不同视觉形态，避免所有语法都长成 chip + arrow。
4. 小程序端可以继续显示普通 `grammar_note` 卡片，只消费 `label`、`note_zh`、`pattern` 和 `read_hint_zh` 等轻量字段。
5. variant 只决定标注策略和解释口吻，不改变 X-Ray 模式本身。

## 后续落地前需要确认

1. 第一批 `grammar_note.xray` 只支持哪些 subtype。
2. Web 第一版是否优先做 `pattern_slots`、`modifier_attachment`、`reference_chain` 三类。
3. 自动生成上限是按句、按篇，还是按阅读模式控制。
4. 是否引入 `confidence` 和 `evidence_span` 作为后端 schema 硬字段。
5. 按需生成的 X-Ray 是否进入用户资产。
