# Reader 学习批注生成策略

> **状态**: `CURRENT` | **最后验证**: 2026-08-08（四值 phrase 分类、`learning_note`、单词条 `context_gloss` 守卫已端到端落地）

本文记录 Reader 学习批注（vocabulary / grammar bundle / translation）的当前生成质量策略。它描述内容契约与教学责任划分，不描述 worker 拓扑；执行边界（锚点解析、fencing、发布）以代码为准。

## 原则

1. **教学价值优先于批注数量**。文本没有值得讲的学习点时，空输出是合法结果。
2. **当前文本优先于通用规则**。解释从"这个形式在当前句子里是什么意思、起什么作用"开始。
3. **差异化是软透镜**。`reading_goal` / `reading_variant` 影响选材、深度、术语量和可选角度，不规定反复出现的模板话术（速度、考试、释义、修辞意图等）。
4. **语言不是刚性分类法**。确定性校验只保护结构完整性，不试图编码全部语言学判断。
5. **一条批注一个主要教学任务**。词汇条目不互相重复，翻译不教语法，语法类条目不重复同一个学习点。
6. **Markdown 是表达手段，不是模板**。模型可以在有用时使用强调、行内代码或简短列表，但不要求固定 Markdown 版式。

## Vocabulary 契约

三类条目：`vocab_highlight`、`context_gloss`、`phrase_gloss`。

### `vocab_highlight`

值得注意或积累的单词，无需特别语境义解释时使用。字段：`headword`、可选 `brief_explanation`、可选 `reason`。当 `context_gloss` 或整体 `phrase_gloss` 在同一原文位置上提供更大学习价值时，不应再发 `vocab_highlight`。

### `context_gloss`

单词条语境义注释：

- 锚定 `selected_text` 去空白后不含空白字符；连字符词、所有格形式和普通屈折形式仍是合法单词条。
- `gloss` 给出当前语境中的精确含义；`reason` 说明语境中什么因素选择了该含义，或为什么常见/默认含义不成立。"这里需要结合语境理解"这类泛话不充分。
- 含义依赖组合的多词单位属于 `phrase_gloss`。
- 后端对含空白的多词 `context_gloss` 候选 fail-closed 跳过并给出诊断（`context_gloss_not_single_lexical_item`），不自动改判为 `phrase_gloss`。

### `phrase_gloss`

短语注释契约：`phrase`、`phrase_type`、`gloss`（必填，直接给出整体含义）、可选 `learning_note`（简体中文 Markdown，提供用法、构成、对比、语域等真实增量，不复述 gloss）、可选 `example`（英文例句）。

`phrase_type` 固定四值，无别名、无 `other` 兜底：

| ID | 中文标签 | 含义 |
|---|---|---|
| `verb_expression` | 动词短语 | 动词中心的多词表达，含 phrasal verb、介词动词和惯用动词组合 |
| `fixed_collocation` | 固定搭配 | 非习语、非专名、非动词表达的惯用组合 |
| `name_or_term` | 专名及术语 | 多词专名、机构、地点、作品名或领域概念 |
| `idiom` | 习语 | 意义不能可靠逐词还原的惯用比喻性表达 |

候选不属于四者之一时模型应跳过。旧值 `collocation` / `phrasal_verb` / `proper_noun` / `compound` / `other` 已移除，schema 校验直接拒绝。

短语有效性不由统一词数定义：旧的七词上限已从 prompt 和确定性守卫中移除，后端只保留结构安全检查（连续精确锚定、字段长度上限、来源出现唯一性、拒绝明显完整句）。

## Grammar bundle 契约

`grammar_bundle` 联合生成 `grammar_note` 和 `sentence_analysis`，选材标准是有真实理解或学习价值的点，不是覆盖检查表；基本透明结构保持不标注。

### `grammar_note`

一条有用的旁注通常：指出关键形式或对比、解释它在当前句中的含义或作用、可选补充最有用的扩展（例句、对比、中英差异、常见学习者错误、考试相关区分——均非必选）。术语从属于理解；使用"过去分词"这类标准术语时须同时用学习者语言解释。"这是高考高频考点"这类泛称只有在紧跟具体教学内容时才可接受。

### `sentence_analysis`

为整句理解障碍而选，不因词数或从句数阈值而选。职责分离：

- `chunks` 提供结构地图。
- `analysis` 讲解如何走这张地图、还原意义、处理非常规阅读顺序；不得逐块复述 chunks、不得复制完整中文译文、不得用术语替代对阅读障碍的解释。

### 两类语法条目的关系

同一学习点上 `grammar_note` 与 `sentence_analysis` 通常竞争一个位置：局部形式/用法点选 `grammar_note`，整句组织构成理解障碍选 `sentence_analysis`。两者明确讲授不同内容时允许共存；这是语义判断，由共享 prompt 与 variant policy 承担，后端不做一刀切同句排除。

## Translation 契约

翻译是最基础的增强节点，所有 variant 共享同一核心要求：准确完整地把英文译为自然简体中文；保留事实、逻辑、指代、语气、程度和重要限定；遵循自然中文表达，允许合理调整语序和分句；专名优先通行译法；只输出译文，不夹带词汇、语法、修辞、释义或考试点评。"信达雅"通过上述操作规则落地，不作为口号。per-unit 与 batch 指令表达同一质量契约。

## Variant 软透镜

`reader_variants.yaml` 只承载受众校准：哪些候选点更可能重要、受众所能接受的术语量、合适的解释深度、以及当前学习点真正支持时的可选考试/阅读/文体角度。variant 行不得要求每条批注都提及速度、释义识别、考试频率、修辞目的或日常实用性；文本没有 variant 专属机会时，透镜可以对该条目无可见效果。

## 失败与校验行为

- 旧值或臆造的 `phrase_type` 直接 schema 校验失败，不合成兜底类别。
- 多词 `context_gloss` 候选带诊断跳过。
- 非法或歧义锚点继续走现有 fail-closed / skip 行为。
- 可选 `learning_note` 缺失是正常状态，不降低条目等级。
- prompt 引导 Markdown 形态；Web 渲染路径即使在模型 Markdown 不完美时也必须保持安全（禁止原始 HTML / `dangerouslySetInnerHTML`）。
- 翻译专名一致性为 best-effort，不新增文章级实体解析节点。

## Web 呈现

- 短语条目头部显示 phrase 与中文子类标签；`gloss` 永远是最可扫读的主要内容。
- `learning_note` / `example` 仅存在时渲染，缺失不留空标签或分隔符。
- 子类标签与四个 `phrase_type` ID 一一对应。

## 验收边界

自动化测试只保证结构契约与传输链路；生成内容的教学质量由真实文章解析的人工评审把关，不由测试断言。
