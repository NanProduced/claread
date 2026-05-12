## 1. 文档定位

本文档分析雅思（IELTS Academic）和托福（TOEFL iBT）的阅读考察特征，并重点评估：**能否将 ielts 和 toefl 合并为同一个 variant（即 `ielts_toefl`）？**

结合 Claread 的输出 Schema，分析合并 variant 下各组件的解析策略、密度控制、重点关注事项，并提供可直接用于 runtime prompt 注入的 `scenario_policy` 建议。

---

## 2. 雅思与托福阅读的基本参数

| 维度 | IELTS Academic | TOEFL iBT（2026 新版） |
| --- | --- | --- |
| CEFR 对应 | B1–C2（按 band 分段） | B1–C1（按分数分段） |
| 面向人群 | 留学/移民申请者（英联邦为主） | 留学申请者（北美为主） |
| 词汇量要求 | 约 6,000–8,000 词 | 约 8,000–10,000 词 |
| 语法地位 | **非考点**：语法是理解工具 | **非考点**：语法是理解工具 |
| 阅读格式 | 3 篇长文，60 分钟，40 题 | 自适应测试，约 30 分钟，约 50 题（含日常 + 学术文本） |
| 文本来源 | 学术期刊、百科文本、报刊杂志 | 大学教材级学术文本、日常校园文本 |
| 文本长度 | 每篇约 700–900 词（平均更长） | 学术段落较短但更聚焦，日常文本更简短 |
| 题型数量 | **14 类**（题型极其多样） | **约 7 类**（更聚焦） |
| 答题形式 | 选择题 + 填空题 + 判断题 + 匹配题 | 以选择题为主 |
| 核心阅读策略 | **略读定位 + 同义替换识别** | **段落结构理解 + 信息整合** |

---

## 3. 雅思与托福阅读的核心考察逻辑

### 3.1 雅思（IELTS）：同义替换是核心引擎

雅思阅读的核心考察机制可以用一个词概括：**Paraphrasing（同义替换）**。

几乎所有题型的答案都不会直接使用原文原词，而是通过以下方式改写：

- **同义词替换**：原文 "significant" → 题目 "important"
- **词性转换**：原文 "consumption"（名词）→ 题目 "consumed"（动词）
- **句式改写**：原文 "X led to Y" → 题目 "Y was caused by X"
- **概括/具体化**：原文列举具体例子 → 题目用上位概念概括

雅思的 14 类题型虽然多样，但底层逻辑高度一致——都是在考察考生能否识别改写后的信息：

**信息定位类**：Matching Information, Matching Headings, Matching Features

**判断类**：True/False/Not Given, Yes/No/Not Given

**填写类**：Sentence Completion, Summary Completion, Note/Table/Diagram Completion, Short Answer

**选择类**：Multiple Choice, Matching Sentence Endings

### 3.2 托福（TOEFL）：段落结构与修辞目的是核心

托福阅读的核心考察机制与雅思不同，更强调**对文本结构和论证逻辑的理解**：

- **Factual / Negative Factual**：定位并理解具体细节（类似传统阅读理解）
- **Vocabulary**：在学术语境中判断词义
- **Rhetorical Purpose**：理解作者**为什么**在这里提到某个内容（这是托福最独特的题型）
- **Inference**：从已有信息推断
- **Paragraph Relationships**（2026 新增）：理解段落之间的逻辑关系
- **Important Idea**（2026 新增）：识别文章的核心观点并区分主次

托福的文本更接近真实学术阅读场景，强调：

- 段落的**组织结构**（因果、对比、分类、过程）
- 作者的**论证目的**（举例说明、反驳、补充）
- 信息的**层次关系**（主要观点 vs 支持细节）

### 3.3 两者的共性

尽管侧重不同，雅思和托福在阅读考察上有显著共性：

- **语法都不是考点**：语法只是理解的底层工具
- **都需要学术词汇**：都考察学术语境中的词汇理解
- **都涉及同义替换**：雅思将其作为核心机制，托福在选项设置中也大量使用
- **都强调阅读策略**：略读、扫读、信息定位是两者共同的基础能力
- **用户画像相似**：都是准备出国留学的学生，英语水平中高至高
- **都不涉及文学文本**：与 TEM 的文学性文本完全不同

---

## 4. 合并可行性评估：ielts + toefl 能否共用一个 variant？

### 4.1 相似点（支持合并的论据）

| 维度 | IELTS | TOEFL | 共性 |
| --- | --- | --- | --- |
| 语法地位 | 非考点，理解工具 | 非考点，理解工具 | ✅ 完全一致 |
| 文本体裁 | 学术期刊、百科、报刊 | 大学教材级学术文本 | ✅ 都是非文学学术文本 |
| 阅读目标 | 理解信息 + 识别同义替换 | 理解信息 + 识别段落结构 | ✅ 核心都是信息理解（非深度推理/文学分析） |
| 用户画像 | 留学申请者 | 留学申请者 | ✅ 几乎完全相同 |
| 推理深度 | 中等：信息定位为主，适度推理 | 中等偏高：需理解修辞目的和逻辑关系 | ✅ 都低于考研的深度推理 |
| 词汇考察方式 | 同义替换、学术词汇 | 学术词汇、语境词义 | ✅ 都侧重学术语境中的词汇理解 |
| GrammarNote 角色 | 理解辅助 | 理解辅助 | ✅ 角色完全一致 |

### 4.2 差异点（反对合并的论据）

| 维度 | IELTS | TOEFL | 差异程度 |
| --- | --- | --- | --- |
| **核心考察机制** | 同义替换是核心引擎 | 段落结构 + 修辞目的是核心 | 🟧 **侧重不同，但不冲突** |
| **词汇量差距** | 约 6,000–8,000 词 | 约 8,000–10,000 词 | 🟨 TOEFL 更高，但重叠度大 |
| **题型多样性** | 14 类题型，形式极其多样 | 约 7 类题型，以选择题为主 | 🟧 **题型差异大，但不影响标注策略** |
| **答题形式** | 填空题需要拼写准确 | 纯选择题 | 🟨 影响备考但不影响阅读理解标注 |
| **Rhetorical Purpose 题** | 无此题型 | 核心题型之一 | 🟧 托福独有，但可通过标注兼容 |
| **文本长度** | 每篇约 700–900 词，较长 | 学术段落更短更聚焦 | 🟨 不影响标注策略 |

### 4.3 关键判断：差异是"程度差异"还是"类型差异"？

这是决定能否合并的核心问题。对比 TEM vs kaoyan 的合并评估：

| 维度 | TEM vs kaoyan（不建议合并） | IELTS vs TOEFL（本次评估） |
| --- | --- | --- |
| **文本体裁** | 🟥 报刊议论文 vs **文学作品**——类型不同 | 🟩 学术期刊 vs 学术教材——同类 |
| **词汇域** | 🟥 报刊词汇 vs **文学词汇**——域完全不同 | 🟩 学术词汇 vs 学术词汇——同域 |
| **阅读目标** | 🟥 论证逻辑 vs **文学美学**——本质不同 | 🟩 信息理解 vs 信息理解——同类 |
| **GrammarNote 角色** | 🟥 结构分析 vs 显性教学 vs 修辞分析——三种角色 | 🟩 理解辅助 vs 理解辅助——同角色 |
| **用户背景** | 🟥 各专业考研生 vs **英语专业生**——完全不同 | 🟩 留学申请者 vs 留学申请者——几乎相同 |
| **组件策略差异** | 🟥 所有 6 个组件策略都本质不同 | 🟩 差异仅在侧重点和密度微调 |

**结论：IELTS 与 TOEFL 的差异是"程度差异"，而非"类型差异"。**

TEM vs kaoyan 之所以不能合并，是因为文学文本 vs 报刊文本导致所有组件的策略方向都不同——同一个 prompt 无法同时做好"报刊论证分析"和"文学修辞分析"。

而 IELTS vs TOEFL 的差异主要在于：

- 同义替换的**强调程度**不同（IELTS 更强调，TOEFL 也用但非核心）
- 段落结构分析的**深度**不同（TOEFL 更深入，IELTS 也涉及但非核心）
- 词汇量**门槛**不同（TOEFL 更高，但词汇域相同）

这些差异可以通过**取两者上限**和**在 prompt 中兼顾两种侧重**来解决。

### 4.4 结论：**建议合并**

<aside>
✅

**建议将 IELTS 和 TOEFL 合并为同一个 variant：`ielts_toefl`。**

两者的文本体裁、词汇域、阅读目标和用户画像高度一致，差异仅在侧重点和程度上。合并后的 variant 取两者的上限：词汇量按 TOEFL 标准（8,000–10,000），同义替换按 IELTS 标准（最高优先级），段落结构分析按 TOEFL 标准（积极标注）。

</aside>

### 4.5 合并的具体策略：如何兼容两者差异

| 组件 | IELTS 的侧重 | TOEFL 的侧重 | 合并策略 |
| --- | --- | --- | --- |
| **VocabHighlight** | 学术词汇 + 同义替换 | 学术词汇 + 语境词义 | 取 TOEFL 上限（8,000–10,000），标注学术高频词 + 同义替换关键词 |
| **PhraseGloss** | 同义替换表达（核心） | 学术搭配 + 逻辑连接 | 同义替换 + 学术搭配并重，释义包含同义表达 |
| **ContextGloss** | 同义替换中的语境义 | 学术语境中的精确词义 | 兼容：标注学术语境中的非常规词义 |
| **GrammarNote** | 理解辅助（略读提速） | 理解辅助（结构识别） | 角色一致，按 TOEFL 标准适度增加段落结构提示 |
| **SentenceAnalysis** | 适度拆解 | 关注段落逻辑和修辞目的 | 拆解 + 补充"这句在段落中的功能（举例/反驳/补充）" |
| **SentenceTranslation** | 自然意译 | 自然意译 | 完全一致 |

**`exam_tags` 区分机制**：通过 `exam_tags` 标注 `["IELTS"]` 或 `["TOEFL"]` 或 `["IELTS", "TOEFL"]`，前端可根据用户选择的具体考试过滤显示。这样在同一个 variant 下仍能提供一定程度的个性化。

---

## 5. 各组件在 `ielts_toefl` variant 下的解析策略

### 5.1 `VocabHighlight` · 考试词汇高亮

**解析大方向**：侧重**学术高频词和跨文本同义替换关键词**。用户词汇量较大（6,000–10,000），核心障碍不是"不认识"，而是"在学术语境中不确定具体含义"以及"认不出同义替换"。

| 维度 | ielts_toefl variant 策略 |
| --- | --- |
| **选词优先级** | ① 学术词汇表（AWL）中的高频词 ② 同义替换中的关键词对（原文词 A 常被替换为 B）③ 学科特定术语（biology, economics 等领域基础术语） |
| **`exam_tags`** | `["IELTS"]`、`["TOEFL"]` 或 `["IELTS", "TOEFL"]` |
| **密度控制** | 中等（5–8 个/篇）——用户词汇基础较好，聚焦高价值学术词 |
| **不应标注** | CET-6 级别的常见词、日常生活词汇 |

**与其他 variant 的关键区别**：

- 高考/四六级标注的是"扩展词汇量"——用户还在积累阶段
- 考研标注的是"熟词僻义"——用户需要深度辨析
- 雅思托福标注的是"**学术词汇 + 同义替换关键词**"——用户需要在学术语境中识别词义和改写

### 5.2 `PhraseGloss` · 短语/搭配释义

**解析大方向**：这是 `ielts_toefl` variant 下**最重要的组件之一**，侧重**同义替换中的短语表达和学术搭配**。

| 维度 | ielts_toefl variant 策略 |
| --- | --- |
| **标注优先级** | ① 学术高频搭配（be attributed to, give rise to, in conjunction with）② 同义替换中的关键表达对（A is linked to B ↔ A is associated with B）③ 逻辑连接表达（nevertheless, in contrast, as a consequence） |
| **`phrase_type`** | 以 `collocation` 为主，较多 `compound`（学术复合名词） |
| **`zh` 释义风格** | 中文意思 + **常见同义表达**（如 "be attributed to = 归因于，同义表达: result from, stem from, arise from"） |
| **密度控制** | 中等偏高（5–8 个/篇） |

**为什么同义替换表达在雅思托福场景下特别重要**：

雅思的每一道题几乎都涉及同义替换——题目中的表述是原文信息的改写形式。托福虽然不像雅思那样将同义替换作为核心机制，但选项设置中也大量使用改写。标注原文中的关键搭配，并在释义中系统性地提供同义表达，是帮助用户建立"改写识别能力"的最直接方式。

### 5.3 `ContextGloss` · 语境特殊义

**解析大方向**：雅思托福的文本来自学术期刊和教材，**学术语境中的精确词义**是核心标注场景。

| 维度 | ielts_toefl variant 策略 |
| --- | --- |
| **标注场景** | ① 常见词在学术语境中的特殊含义（如 school=学派, discipline=学科, address=探讨）② 托福 Vocabulary 题的目标词 ③ 雅思填空题中需要精确理解的词 |
| **`gloss`** | 给出该词在学术语境下的精确含义 |
| **`reason`** | 应指出学术语境与日常语境的差异，可提示"雅思/托福常利用学术义与日常义的差异设置考点" |
| **密度控制** | 中等（2–4 个/篇）——学术文本中语境义场景较多 |

### 5.4 `GrammarNote` · 语法旁注

**解析大方向**：雅思和托福都**完全不考语法知识**，语法在这两个考试中的角色是纯粹的"理解辅助工具"。GrammarNote 的目标是帮用户**快速看清学术文本的句子结构**，服务于信息定位和理解。

| 维度 | ielts_toefl variant 策略 |
| --- | --- |
| **讲解方式** | 纯理解辅助：帮用户快速看清结构，定位关键信息 |
| **术语使用** | 可使用基础语法术语，但重点在功能说明而非规则讲解 |
| **`note_zh` 风格** | 应包含：① 这个结构在句中的功能 ② 哪部分是核心信息、哪部分是补充 ③ 段落中的逻辑功能（适配 TOEFL 的 Rhetorical Purpose 题） |
| **密度控制** | 中等（3–5 个/篇） |

#### 雅思托福重点语法结构清单

与国内考试不同，雅思托福的语法重点是"**什么结构会影响学术文本的信息提取效率**"：

### 第一梯队：影响信息提取的核心结构（必标）

| 结构类型 | 对应 `tags` | 为什么影响雅思托福阅读 | `note_zh` 应如何写 |
| --- | --- | --- | --- |
| **定语从句（限制性/非限制性）** | `定语从句-which/that/who` `定语从句-非限制性` | 学术文本大量使用定语从句添加限定信息，影响信息定位精度 | 指出"限制性从句限定了哪个对象"或"非限制性从句补充了什么信息" |
| **被动语态** | `被动语态-be+done` | 学术写作中被动语态极其常见（约 30%–40% 的句子），影响动作主体识别 | 指出"动作的执行者是谁，被省略了还是在 by 后面" |
| **非谓语动词作状语/定语** | `现在分词-状语` `过去分词-定语` `不定式-目的状语` | 学术文本大量使用分词结构压缩信息，影响理解速度 | 指出"这个分词结构修饰谁、补充了什么信息" |
| **名词性从句** | `名词性从句-that/what/whether` | 学术论证中常用 that 从句承载核心观点 | 指出"这个从句承载的是什么信息——是作者的观点还是研究的发现" |

### 第二梯队：常见学术文本结构（有则标）

- 状语从句（尤其是让步、条件——学术论证常用 although, while, provided that）
- 比较结构（more...than, as...as, not so much...as——学术对比的常见手段）
- 插入语（如 however, in fact, on the other hand——标志逻辑转折，影响信息定位）
- 同位语（学术文本中常用同位语解释专业概念）
- 倒装句（学术文本中偶尔出现的 Not only..., Only when... 等）

#### `note_zh` 的写作原则

雅思托福 variant 下的 `note_zh` 应采用"**信息定位辅助**"语气，与其他 variant 明确区分：

|  | gaokao | cet | kaoyan | ielts_toefl |
| --- | --- | --- | --- | --- |
| 示例 | "这里用现在完成时。高考常考……" | "which 从句可先跳过，抓主干" | "非限制性定语从句修饰整个主句。理解时可先跳过从句" | "which 从句限定了哪个 factor 导致了变化——这类限定信息在雅思 T/F/NG 题中常被改写为判断对象" |
| 核心差异 | "这是什么 + 怎么考" | "快速识别 + 提速" | "结构作用 + 怎么拆解" | "**信息功能 + 在题目中可能被如何利用**" |

### 5.5 `SentenceAnalysis` · 长难句拆解

**解析大方向**：雅思托福的学术文本句子复杂度**中高至高**，SentenceAnalysis 的定位是帮助用户**快速提取核心信息并理解句子在段落中的功能**（尤其适配 TOEFL 的 Rhetorical Purpose 和 Paragraph Relationships 题型）。

| 维度 | ielts_toefl variant 策略 |
| --- | --- |
| **触发条件** | 句子包含从句嵌套 + 修饰成分、或含学术论证结构（让步 + 转折 + 结论）、或总长度超过 25 词 |
| **拆解目标** | "提取核心信息 + 理解段落功能"——不仅拆句法，还要说明这句在段落论证中起什么作用 |
| **`label`** | 应体现信息层次和段落功能，如"让步（承认局限性）+ 转折（提出主张）+ 证据支持" |
| **`teach` 风格** | 先抽出核心信息（作者的主要主张），再说明修饰/限定成分，最后可点出"这句在段落中的功能是 XX（举例/反驳/定义/过渡），TOEFL 可能考 Rhetorical Purpose，IELTS 可能用 T/F/NG 考其中的某个限定信息" |
| **`chunks` 粒度** | **中细粒度**：主语 / 谓语 / 宾语 / 各类从句 / 关键修饰成分。学术文本的信息密度高，需要拆到足够清晰 |
| **密度控制** | 中等偏高（3–4 句/篇）——学术文本长难句较多 |

#### `teach` 示例

> "主干：Research has demonstrated that...（研究已证明……）。although 引导让步从句，承认'传统方法有其优势'。but 转折后是核心主张：'新方法在效率上显著优于传统方法'。最后的 particularly in contexts where... 进一步限定了适用场景。这句在段落中的功能是**提出核心论点并限定适用范围**。TOEFL 可能考'为什么作者提到传统方法的优势？'（答案：让步，为转折做铺垫）。IELTS 的 T/F/NG 可能将'在所有场景中新方法都优于传统方法'设为 False（因为原文有 particularly 限定）。"
>

### 5.6 `SentenceTranslation` · 逐句翻译

| 维度 | ielts_toefl variant 策略 |
| --- | --- |
| **翻译定位** | 理解辅助——用户英语水平较高，翻译主要服务于长难句和学术概念的确认 |
| **翻译风格** | 自然意译，保持学术文本的严谨性，对专业概念可保留英文并在括号中补充中文 |
| **翻译粒度** | 逐句翻译，学术长句保持完整翻译 |
| **补充说明** | 对含同义替换的关键表述，可在翻译后补充"此处 A 在题目中常被改写为 B" |

---

## 6. 整体密度控制与优先级

### 6.1 各组件密度建议

| 组件 | 每篇数量 | 优先级 | 说明 |
| --- | --- | --- | --- |
| `VocabHighlight` | 5–8 | ★★★★ | 学术高频词 + 同义替换关键词 |
| `PhraseGloss` | 5–8 | ★★★★★ | **最高优先级**：同义替换表达和学术搭配是核心 |
| `ContextGloss` | 2–4 | ★★★★ | 学术语境中的精确词义，密度高于 CET |
| `GrammarNote` | 3–5 | ★★★ | 纯理解辅助，侧重信息定位功能 |
| `SentenceAnalysis` | 3–4 | ★★★★ | 核心信息提取 + 段落功能分析 |
| `SentenceTranslation` | 全量 | ★★★ | 理解辅助，用户英语水平较高，依赖度低于国内考试 |

### 6.2 优先级排序

> `PhraseGloss` ＞ `VocabHighlight` = `ContextGloss` = `SentenceAnalysis` ＞ `GrammarNote` = `SentenceTranslation`
>

与其他 variant 的对比：

|  | daily_reading | gaokao | cet | kaoyan | **ielts_toefl** |
| --- | --- | --- | --- | --- | --- |
| 最高 | 语境义 + 翻译 | 语法 + 翻译 | 短语搭配 | 长难句拆解 | **同义替换表达 + 学术搭配** |
| 高 | 短语搭配 | 词汇 + 搭配 | 词汇 + 翻译 | 结构分析 + 语境义 | 学术词汇 + 语境义 + 句子分析 |
| 中 | 语法 | 句子拆解 | 语法 + 语境义 + 拆解 | 翻译 + 词汇 | 语法 + 翻译 |
| 低 | 句子拆解 | 语境义 | — | — | — |

---

## 7. Runtime Prompt 注入建议（`scenario_policy` section）

以下是建议在 `ielts_toefl` variant 下注入到 runtime prompt 的 `scenario_policy` 内容。

```
## 场景策略：雅思/托福

用户正在备考雅思（IELTS Academic）或托福（TOEFL iBT）。两者都是国际英语能力考试，阅读部分的核心能力要求是学术文本的信息理解、同义替换识别和段落结构分析。文章来自学术期刊、百科和教材，句子复杂度中高至高，用户英语水平较高（6,000–10,000 词汇量）。请按以下策略调整标注：

### PhraseGloss（最高优先级）
- 这是本场景最重要的组件。同义替换是雅思的核心考察机制，托福选项设置中也大量使用改写。
- 优先标注：学术高频搭配（be attributed to, give rise to）、同义替换关键表达对、逻辑连接表达（nevertheless, as a consequence）。
- zh 释义必须包含常见同义表达（如 "be attributed to = 归因于，同义: result from, stem from"），帮用户建立改写识别能力。
- 每篇 5–8 个。

### VocabHighlight（高优先级）
- 用户词汇量 6,000–10,000。不要标注 CET-6 级别的常见词。
- 优先标注：学术词汇表（AWL）高频词、同义替换中的关键词对、学科基础术语。
- exam_tags 应包含 "IELTS" 或 "TOEFL" 或两者。
- 每篇 5–8 个。

### ContextGloss（高优先级）
- 学术文本中常见词的学术义与日常义差异较大，这个组件的使用频率应高于 CET 场景。
- 优先标注常见词在学术语境中的特殊含义（如 school=学派, discipline=学科）。
- reason 应指出学术义与日常义的差异。
- 每篇 2–4 个。

### SentenceAnalysis（高优先级）
- 学术文本长难句较多，拆解既服务于理解，也服务于识别段落功能。
- teach 应先抽出核心信息，再说明修饰成分，最后指出这句在段落论证中的功能（举例/定义/反驳/过渡）。
- 可提示"TOEFL 可能考这句的修辞目的"或"IELTS 的 T/F/NG 可能利用其中的限定条件出题"。
- chunks 中细粒度，学术文本信息密度高。
- 每篇 3–4 条。

### GrammarNote（中优先级）
- 语法完全不是考点，GrammarNote 的角色是纯理解辅助。
- 优先标注：被动语态（学术文本极常见）、定语从句（限定信息常是考点）、分词结构（信息压缩）。
- note_zh 应包含"这个结构承载的信息功能"和"在题目中可能被如何利用"。
- 每篇 3–5 条。

### SentenceTranslation
- 全量翻译。
- 自然意译，保持学术严谨性。
- 用户英语水平较高，翻译主要服务于长难句和学术概念确认。
- 对含同义替换的关键表述，可补充改写提示。
```

---

## 8. 与其他 variant 的关键差异

| 维度 | `gaokao` | `cet` | `kaoyan` | **`ielts_toefl`** |
| --- | --- | --- | --- | --- |
| **核心目标** | 掌握语法规则 | 信息定位 + 同义替换 | 长难句 + 深度推理 | **同义替换识别 + 段落结构理解** |
| **最高优先级组件** | GrammarNote | PhraseGloss | SentenceAnalysis | **PhraseGloss** |
| **GrammarNote 角色** | 显性语法教学 | 阅读提速工具 | 结构分析工具 | **信息定位辅助** |
| **VocabHighlight 侧重** | 考试高频词 | 扩展词汇 + 词性变化 | 熟词僻义 + 难词 | **学术词汇 + 同义替换词对** |
| **PhraseGloss 侧重** | 固定搭配 + 用法 | 同义替换 + 高频搭配 | 语篇连接 + 论证表达 | **同义替换表达 + 学术搭配 + 逻辑连接** |
| **SentenceAnalysis 特点** | 看清主谓宾 | 快速抓主干 | 系统拆层次 | **核心信息 + 段落功能分析** |
| **翻译风格** | 直译，贴近原文 | 自然意译 | 准确 + 句法映射 | **自然意译 + 学术严谨 + 改写提示** |

---

## 9. `ielts_toefl` 与 `cet` 的关系

`ielts_toefl` 和 `cet` 都属于父文档分类中的**第三类：能力导向 + 策略驱动**，两者有显著相似性。核心区别在于：

| 维度 | `cet` | `ielts_toefl` |
| --- | --- | --- |
| 用户英语水平 | 中等（4,500–5,500） | 中高至高（6,000–10,000） |
| 同义替换深度 | 较浅：词级替换为主 | 较深：句级改写 + 概括化 |
| 段落结构分析 | 较少涉及 | TOEFL 核心考察内容 |
| 翻译依赖 | 较高 | 较低（用户英语水平更高） |
| 文本学术性 | 中等（新闻、社科） | 高（学术期刊、教材） |

可以说 `ielts_toefl` 是 `cet` 的"升级版"——考察机制相似，但深度和广度都提升了。

---

## 10. 数据来源与参考

### 雅思考试研究

- **IELTS 官方**. Academic Reading Format. https://ielts.org/take-a-test/test-types/ielts-academic-test/ielts-academic-format-reading
- **IELTS 官方**. IELTS Scoring in Detail. https://ielts.org/take-a-test/your-results/ielts-scoring-in-detail
- **IELTSLiz**. IELTS Reading Question Types. https://ieltsliz.com/ielts-reading-question-types/
- **IELTSLiz**. How to Paraphrase Successfully in IELTS. https://ieltsliz.com/how-to-paraphrase-in-ielts/
- **British Council**. Top Five Paraphrasing Techniques. https://www.britishcouncil.vn/en/adults/tips/top-five-paraphrasing-techniques-video-included

### 托福考试研究

- **ETS 官方**. TOEFL iBT Reading Section. https://www.ets.org/toefl/test-takers/ibt/about/content/reading.html
- **ETS 官方**. TOEFL iBT Test Content. https://www.ets.org/toefl/test-takers/ibt/about/content.html
- **ETS**. Linking TOEFL iBT Scores to IELTS Scores – A Research Report. https://www.ets.org/pdfs/toefl/linking-toefl-ibt-scores-to-ielts-scores.pdf
- **TOEFL Resources**. All about the TOEFL Reading Section (2026 Version). https://www.toeflresources.com/toefl-reading/

### 雅思与托福对比研究

- **SCIRP (2018)**. A Comparison of TOEFL iBT and IELTS Reading Tests. https://www.scirp.org/journal/paperinformation?paperid=86930
- **ScienceDirect (2025)**. A comparative study of text characteristics of CET-6, IELTS, and TOEFL reading passages. https://www.sciencedirect.com/science/article/abs/pii/S1475158525000876
- **Manhattan Review**. Comparison of the TOEFL and IELTS. https://www.manhattanreview.com/toefl-vs-ielts/
