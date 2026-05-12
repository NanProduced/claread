## 1. 研究目的

> **文档分类：历史参考**。本文档是纯语言学/教学学研究参考，不是实现规范。当前 daily_reading 的实现规范以 [差异化输出执行架构设计](../workflow/v3/differentiated-output-strategy-design.md) 为准。

本文档从纯英语学习与语言学研究角度，分析 `daily_reading` 下三个 variant（`beginner_reading` / `intermediate_reading` / `intensive_reading`）在词汇、语法、翻译/阅读理解三个维度上的差异，为后续讲解策略设计提供学理依据。

**不涉及**项目代码、架构或业务逻辑。

---

## 2. 水平分级框架：CEFR 与阅读能力特征

本文档采用 CEFR（Common European Framework of Reference for Languages）作为水平划分的主要参考框架，将三个 variant 映射到对应的 CEFR 区间。

### 2.1 三个 variant 的 CEFR 映射

| variant | 对应 CEFR 区间 | 典型阅读能力描述 |
| --- | --- | --- |
| `beginner_reading` | A2 – B1 | 能理解高频日常词汇和简单句构成的文本；遇到低频词和复杂句时明显受阻 |
| `intermediate_reading` | B1 – B2 | 能理解主流媒体文本的主旨和多数细节；对复杂从句和多义词语境义有一定处理能力但不稳定 |
| `intensive_reading` | B2 – C1 | 能理解较复杂的议论文和叙事文本；主要障碍在于隐含逻辑、精细语义和高密度结构 |

### 2.2 CEFR 各级阅读能力的关键差异

根据 CEFR 官方描述和相关研究：

- **A2 阅读者**：能理解由高频日常用语或工作相关语言构成的文本，能在简单文本中找到特定信息（如广告、菜单、时刻表）
- **B1 阅读者**：能理解主要由高频词汇构成的文本，能理解个人信件中对事件、感受和愿望的描述
- **B2 阅读者**：能阅读关于当代问题的文章和报告，能理解当代文学散文
- **C1 阅读者**：能理解长篇复杂的事实性和文学性文本，能识别风格差异；能理解专业文章和较长的技术说明

---

## 3. 词汇维度的差异分析

### 3.1 词汇量与阅读门槛

Nation（2006）的研究是词汇量与阅读理解关系的核心参考。其关键发现：

- 要达到 **95% 的文本覆盖率**（最低可读门槛），需要约 **4,000–5,000 词族**
- 要达到 **98% 的文本覆盖率**（自主阅读门槛），需要约 **8,000–9,000 词族**
- 最高频的 **2,000 词族**覆盖一般文本约 80%–84% 的词汇
- **3,000 词族**（含前 2,000）是阅读理解的基本门槛（Laufer, 1989）

按 variant 映射：

| variant | 预估词汇量范围 | 词汇覆盖率特征 | 主要词汇障碍 |
| --- | --- | --- | --- |
| `beginner` | 1,500 – 3,000 词族 | 约 80%–90% 覆盖率，每行可能遇到 1–2 个不认识的词 | **低频词本身**：直接不认识的词是主要障碍 |
| `intermediate` | 3,000 – 5,000 词族 | 约 90%–95% 覆盖率，大部分词认识但部分语境义不确定 | **多义词的语境义**：认识但不确定这里什么意思 |
| `intensive` | 5,000 – 8,000 词族 | 约 95%–98% 覆盖率，极少遇到完全不认识的词 | **精细语义差异**：近义词辨析、搭配隐含义、修辞用法 |

### 3.2 多义词与语境义处理能力

多义词（polysemy）对 L2 学习者是一个显著挑战。Maby（2016）的研究表明，多义词"混淆了形式到意义的映射"（confounds the mapping of form to meaning），L2 学习者对多义词次要义项的掌握显著弱于主要义项。

Crosskey & Skalicky（2019）的启动实验发现，即使是高水平 L2 学习者，在处理多义词的远距离义项（subordinate polysemy）时，仍然与 L1 说话者存在显著差异。

按 variant 的多义词处理特征：

- **beginner**：倾向于只知道高频词的核心义项，遇到次要义项时直接按主要义理解，导致误读
- **intermediate**：能意识到某些词"在这里可能不是平时那个意思"，但推断能力不稳定
- **intensive**：能利用上下文主动推断语境义，但在隐喻延伸义、领域特定义上仍有盲区

### 3.3 词汇讲解策略差异建议

| 维度 | `beginner` | `intermediate` | `intensive` |
| --- | --- | --- | --- |
| **选词优先级** | 优先标注 3,000 词族以外的低频词和关键短语 | 优先标注多义词的语境义和常见短语搭配 | 优先标注精细语义差异、修辞用法和隐含搭配 |
| **释义方式** | 直接给出中文对应义 + 简短英文释义 | 先给语境义，再对比常见义项 | 侧重辨析（为什么用这个词而不是近义词） |
| **示例需求** | 需要简单例句帮助记忆 | 需要对比例句展示不同语境下的义项差异 | 可省略基础例句，适当补充搭配和用法说明 |
| **标注密度** | 较高（用户词汇缺口大） | 中等（聚焦高价值语境义） | 较低但更精细（少标但标得深） |

---

## 4. 语法维度的差异分析

### 4.1 显性语法教学 vs 隐性语法教学

语法教学研究中最核心的分歧是 **显性教学（explicit instruction）** 与 **隐性教学（implicit instruction）** 的效果差异。

Spada & Tomita（2010）的元分析发现：**总体而言，显性语法教学比隐性教学更有效**，但效果取决于学习者水平、目标结构复杂度和教学情境。

关键研究结论：

- **低水平学习者**更依赖显性规则讲解，因为他们缺乏足够的语言输入来自主归纳规则（Klapper & Rees, 2003）
- **中等水平学习者**最适合 **Focus on Form（FonF）** 策略：在意义理解过程中顺带关注语言形式，而不是脱离语境单独讲规则
- **高水平学习者**在大量可理解输入（comprehensible input）的基础上，能通过隐性学习进一步内化语法，显性讲解此时的价值在于纠正化石化错误和处理细微结构差异

### 4.2 句法复杂度与阅读处理

Papadopoulou（2002）的经典研究发现，L2 学习者在句法解析（parsing）策略上与 L1 说话者存在系统性差异，且这种差异与水平高度相关。

Tan（2020）的研究进一步表明，不同水平的 L2 学习者在处理 wh- 提取结构等复杂句法时，表现出不同的加工模式：低水平学习者依赖逐词线性解码，高水平学习者才能调用层级化的句法解析。

按 CEFR 区间的句法处理特征：

| CEFR 区间 | 能流畅处理的句法复杂度 | 典型困难点 |
| --- | --- | --- |
| A2–B1 | 简单句、并列句、单层从句 | 定语从句嵌套、分词结构、倒装 |
| B1–B2 | 单层从句、部分多层嵌套 | 多层从句嵌套、名词性从句做主语、复杂分词短语 |
| B2–C1 | 多层嵌套、分词结构、独立主格 | 高度压缩结构（名词化堆叠、省略、插入语嵌套） |

### 4.3 Krashen 的可理解输入假说与 scaffolding

Krashen 的 **i+1 假说**认为：语言习得发生在学习者接收到"略高于当前水平"的可理解输入时。关键要点：

- 如果输入完全不可理解，它**不构成 i+1**，而是无效输入
- 理想状态是大部分内容可理解，少部分新结构通过上下文自然推断
- 这意味着语法讲解的密度应该与用户水平匹配：**讲太多 = 信息过载，讲太少 = 遇到障碍无法继续**

结合 Vygotsky 的 **最近发展区（Zone of Proximal Development）** 理论，scaffolding（脚手架）策略要求：在学习者**能做但不稳定**的区域提供支持，而不是在已经完全掌握或完全超出能力的区域。

### 4.4 语法讲解策略差异建议

| 维度 | `beginner` | `intermediate` | `intensive` |
| --- | --- | --- | --- |
| **讲解方式** | 显性为主：直接说明句子结构，拆清主谓宾 | FonF 为主：在理解语义的过程中点出关键结构 | 隐性为主：仅在结构确实阻碍理解时才点出 |
| **术语使用** | 避免术语，用"修饰""补充说明"等直白表达 | 可适度引入术语，但必须附带直白解释 | 可使用标准语法术语，用户能理解 |
| **拆句策略** | 积极拆句：遇到从句就拆，帮用户看清"谁做了什么" | 选择性拆句：只拆影响理解的复杂结构 | 精细拆句：分析信息层次、修饰关系和逻辑连接 |
| **标注密度** | 高：多数长句都应得到结构说明 | 中：只标注对理解有实际影响的结构点 | 低但精细：标注重点在"为什么这样写"而不是"这是什么结构" |
| **教学目标** | "看懂这句话在说什么" | "理解结构如何承载意义" | "理解作者为什么选择这种表达方式" |

---

## 5. 翻译与阅读理解维度的差异分析

### 5.1 阅读处理模式：bottom-up vs top-down

阅读研究中的经典框架区分了两种处理模式：

- **Bottom-up（自下而上）**：从字母、单词、短语逐步向上构建意义。依赖词汇识别和句法解码
- **Top-down（自上而下）**：利用背景知识、语篇结构和预测来理解文本。依赖 schema 激活和推理

Stanovich（1980）提出的**交互补偿模型（interactive-compensatory model）** 是当前主流共识：阅读是两种模式的交互过程，当某一方面能力弱时，读者会依赖另一方面补偿。

按 variant 的处理模式特征：

- **beginner**：严重依赖 **bottom-up**，因为词汇和句法解码消耗了大部分认知资源，难以调用 top-down 策略
- **intermediate**：开始能交替使用两种模式，但在遇到复杂段落时仍回退到 bottom-up
- **intensive**：能流畅运用 **top-down** 策略（预测、推理、跳读），bottom-up 只在遇到真正的障碍时启用

### 5.2 翻译风格与理解支持

对不同水平的读者，翻译（中文对照）承担的角色不同：

- **beginner**：翻译是**主要理解通道**。用户可能先看翻译再回看原文，翻译必须直白、完整、贴近原文结构
- **intermediate**：翻译是**理解确认工具**。用户先尝试自己理解，翻译用于验证是否理解正确。翻译可以更自然，不必严格对应原文结构
- **intensive**：翻译是**精细理解辅助**。用户基本能自主理解，翻译的价值在于揭示微妙含义、隐含逻辑和修辞效果

### 5.3 翻译策略差异建议

| 维度 | `beginner` | `intermediate` | `intensive` |
| --- | --- | --- | --- |
| **翻译定位** | 主要理解通道 | 理解确认工具 | 精细理解辅助 |
| **翻译风格** | 直译为主，结构尽量贴近原文语序，方便对照 | 意译为主，追求自然通顺的中文表达 | 在准确基础上，适当体现原文的修辞和语气 |
| **翻译粒度** | 逐句翻译，必要时拆分长句为多个短句 | 逐句翻译，保持与原文一一对应 | 逐句翻译，对关键表达可附加注释说明 |
| **补充说明** | 可在翻译中用括号补充省略的主语或逻辑关系 | 不需要额外补充，翻译本身应自洽 | 可在翻译旁标注"原文此处的隐含义/修辞手法" |

---

## 6. 三个 variant 的综合画像

### 6.1 `beginner_reading`（A2–B1）

**核心特征**：词汇缺口大，句法解码能力弱，严重依赖 bottom-up 处理和翻译辅助

**讲解策略总方向**：

- 词汇：**广覆盖、浅解释**——多标词，给出直接中文释义
- 语法：**显性拆句**——直接说明句子结构，用直白语言
- 翻译：**直译、完整、贴近原文结构**——翻译是主要理解通道
- 总体密度：**高**——用户需要更多支持才能读懂

**用户心理预期**："帮我看懂这篇文章在说什么"

### 6.2 `intermediate_reading`（B1–B2）

**核心特征**：基础词汇已覆盖，多义词语境义是主要词汇障碍，能处理单层从句但对复杂嵌套不稳定

**讲解策略总方向**：

- 词汇：**精选标注、语境义优先**——不标用户大概率认识的词，聚焦"认识但这里不确定"的情况
- 语法：**Focus on Form**——在意义理解过程中顺带点出结构，不单独讲语法
- 翻译：**自然意译**——翻译是验证工具，不是主要理解通道
- 总体密度：**中等**——只在用户可能卡住的地方提供帮助

**用户心理预期**："帮我确认理解对不对，顺便学到一些东西"

### 6.3 `intensive_reading`（B2–C1）

**核心特征**：词汇覆盖率高，主要障碍在精细语义、高密度结构和隐含逻辑

**讲解策略总方向**：

- 词汇：**少标但深挖**——聚焦近义词辨析、搭配隐含义、修辞用法
- 语法：**隐性为主**——仅在结构真正阻碍理解时分析，侧重"为什么这样写"
- 翻译：**精确 + 修辞感知**——在准确基础上体现原文的语气和修辞选择
- 总体密度：**低但精细**——少标注，每条标注的信息密度更高

**用户心理预期**："帮我更深入地理解这篇文章的细节和表达"

---

## 7. 数据来源与参考文献

以下资源可用于后续 few-shot 采集和 RAG 构建：

### CEFR 框架与阅读分级

- **Council of Europe CEFR 官方描述**：https://www.coe.int/en/web/common-european-framework-reference-languages
- **Erasmus University Rotterdam CEFR 分级说明**：https://www.eur.nl/en/education/language-training-centre/cefr-levels
- **Michigan Language Assessment CEFR 解读**：https://michiganassessment.org/blog/understanding-the-cefr-scale-what-english-levels-really-mean/
- **English Path CEFR 分级指南**：https://www.englishpath.com/blog/cefr-english-language-levels-explained-a1-c2/

### 词汇量与阅读门槛

- **Nation, I.S.P. (2006)**. How large a vocabulary is needed for reading and listening? *Canadian Modern Language Review*, 63(1), 59–82. 核心论文：词汇量与文本覆盖率的量化关系
- **Laufer, B. (1989)**. What percentage of text-lexis is essential for comprehension? *Lauren & Nordman (eds.), Special Language: From Humans Thinking to Thinking Machines*, 316–323. 阅读理解的词汇门槛研究
- **Vocabulary Size Test（Nation）**：https://www.wgtn.ac.nz/lals/resources/paul-nations-resources/vocabulary-tests
- **Updated Vocabulary Levels Test（Webb, Sasao & Balance, 2017）**：https://www.lextutor.ca/tests/webb_sasao_ballance_2017.pdf
- **Ehsanzadeh, S.J. (2020)**. Assessing Threshold Level of L2 Vocabulary Depth in Reading Comprehension. *Language Education & Assessment*, 3(1), 1–12. https://files.eric.ed.gov/fulltext/EJ1290239.pdf

### 多义词与语境义习得

- **Maby, M. (2016)**. An investigation of L2 English learners' knowledge of polysemous word meanings. Cardiff University PhD thesis. https://orca.cardiff.ac.uk/id/eprint/99799/
- **Crossley, S.A. & Skalicky, S. (2019)**. Making sense of polysemy relations in first and second language speakers of English. *International Journal of Bilingualism*, 23(6). https://journals.sagepub.com/doi/abs/10.1177/1367006917728396
- **Zhang, Y. (2020)**. The Effect of Semantic Similarity on Learning Ambiguous Words. *PMC*. https://pmc.ncbi.nlm.nih.gov/articles/PMC7381155/
- **PMC (2015)**. Contextual learning of L2 word meanings: proficiency modulates learning. https://pmc.ncbi.nlm.nih.gov/articles/PMC4428693/

### 语法教学策略

- **Spada, N. & Tomita, Y. (2010)**. Interactions between type of instruction and type of language feature: A meta-analysis. *Language Learning*, 60(2), 263–308. 显性 vs 隐性语法教学的元分析
- **Klapper, J. & Rees, J. (2003)**. Reviewing the case for explicit grammar instruction in the university foreign language learning context. *Language Teaching Research*, 7(3), 285–314.
- **Liu University (2023)**. Reviewing the Effects of Explicit and Implicit Grammar Instruction. https://liu.diva-portal.org/smash/get/diva2:1732730/FULLTEXT01.pdf
- **Krashen, S. (1982)**. *Principles and Practice in Second Language Acquisition*. Pergamon Press. https://www.sdkrashen.com/content/books/principles_and_practice.pdf
- **Krashen, S. (2017)**. The Case for Comprehensible Input. *Language Magazine*. https://www.sdkrashen.com/content/articles/case_for_comprehensible_input.pdf

### 句法处理与阅读策略

- **Papadopoulou, D. (2002)**. Parsing Strategies in L1 and L2 Sentence Processing. Essex Research Reports. https://repository.essex.ac.uk/157/1/errl-hc.pdf
- **Tan, M. (2020)**. Task Sensitivity in L2 English Speakers' Syntactic Processing. *PMC*. https://pmc.ncbi.nlm.nih.gov/articles/PMC7517905/
- **Stanovich, K.E. (1980)**. Toward an interactive-compensatory model of individual differences in the development of reading fluency. *Reading Research Quarterly*, 16(1), 32–71. 阅读的交互补偿模型
- **Stiborská et al. (2025)**. Syntactic Complexity in L2 Reading: A Comparison of Adapted and Original Czech Texts. *SyntaxFest 2025*. https://aclanthology.org/2025.quasy-1.7.pdf
- **Atlantis Press**. Bottom-up or Top-down Reading Strategies. https://www.atlantis-press.com/article/125961967.pdf
- **JALT Publications**. Beyond the Sentence: Finding a Balance Between Bottom-Up and Top-Down Reading Approaches. https://jalt-publications.org/tlt/articles/2450-beyond-sentence-finding-balance-between-bottom-and-top-down-reading-approaches
