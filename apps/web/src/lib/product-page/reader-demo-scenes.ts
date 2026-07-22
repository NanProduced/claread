import type { ReaderMockVm } from "@/types/view/ReaderMockVm";

export type ProductDemoGoalId = "daily_reading" | "exam" | "academic";
export type ProductDemoExamVariant = "cet" | "kaoyan" | "ielts_toefl";
export type ProductDemoSceneId =
  | "daily_intermediate"
  | "exam_cet"
  | "exam_kaoyan"
  | "exam_ielts_toefl"
  | "academic_general";

export type ProductDemoGoal = {
  id: ProductDemoGoalId;
  label: string;
  sceneId: ProductDemoSceneId;
  description: string;
};

export const productDemoGoals: ProductDemoGoal[] = [
  {
    id: "daily_reading",
    label: "日常阅读",
    sceneId: "daily_intermediate",
    description: "保留原文节奏，只在卡住的词、短语和句子关系上展开。",
  },
  {
    id: "exam",
    label: "考试阅读",
    sceneId: "exam_cet",
    description: "围绕题目常考的信息定位、改写和长句结构做解释。",
  },
  {
    id: "academic",
    label: "Academic",
    sceneId: "academic_general",
    description: "把术语、逻辑关系和解释性笔记锚定回原文论证。",
  },
];

export const productDemoExamVariants: Array<{
  id: ProductDemoExamVariant;
  label: string;
  sceneId: ProductDemoSceneId;
}> = [
  { id: "cet", label: "四六级", sceneId: "exam_cet" },
  { id: "kaoyan", label: "考研", sceneId: "exam_kaoyan" },
  { id: "ielts_toefl", label: "雅思托福", sceneId: "exam_ielts_toefl" },
];

export const productDemoSceneState: Record<
  ProductDemoSceneId,
  { selectedSentenceId: string; expandedEntryIds: string[] }
> = {
  daily_intermediate: {
    selectedSentenceId: "daily-s2",
    expandedEntryIds: ["daily-se-analysis-s2"],
  },
  exam_cet: {
    selectedSentenceId: "exam-s1",
    expandedEntryIds: ["exam-cet-grammar-not-a-but-b"],
  },
  exam_kaoyan: {
    selectedSentenceId: "exam-s1",
    expandedEntryIds: ["exam-kaoyan-analysis-s1"],
  },
  exam_ielts_toefl: {
    selectedSentenceId: "exam-s2",
    expandedEntryIds: ["exam-ielts-grammar-while"],
  },
  academic_general: {
    selectedSentenceId: "academic-s1",
    expandedEntryIds: ["academic-logic-after"],
  },
};

const examSentences = [
  {
    sentenceId: "exam-s1",
    paragraphId: "exam-p1",
    text: "For students preparing for exams, the difficulty is often not that every word is unknown, but that a sentence hides the tested information inside clauses, modifiers, and rewritten expressions.",
  },
  {
    sentenceId: "exam-s2",
    paragraphId: "exam-p1",
    text: "A question may ask about the writer's attitude, while the answer is carried by a contrast marker, a reduced relative clause, or a phrase that has been paraphrased in the options.",
  },
  {
    sentenceId: "exam-s3",
    paragraphId: "exam-p1",
    text: "Claread helps students locate the main structure first, then notice the grammar points and signal words that exam questions are likely to use.",
  },
] satisfies ReaderMockVm["article"]["sentences"];

const examTranslations = [
  {
    sentenceId: "exam-s1",
    translationZh: "对备考学生来说，难点往往不是每个词都不认识，而是句子把考查信息藏在从句、修饰语和改写表达里。",
  },
  {
    sentenceId: "exam-s2",
    translationZh: "题目可能问作者态度，而答案却由一个转折标志、一个简化的关系从句，或一个在选项中被改写过的短语承载。",
  },
  {
    sentenceId: "exam-s3",
    translationZh: "Claread 会帮助学生先定位主干结构，再注意考试题目可能利用的语法点和信号词。",
  },
] satisfies ReaderMockVm["translations"];

function examScene(
  requestId: string,
  readingVariant: string,
  inlineMarks: ReaderMockVm["inlineMarks"],
  sentenceEntries: ReaderMockVm["sentenceEntries"],
): ReaderMockVm {
  return {
    schemaVersion: "3.0.0",
    request: {
      requestId,
      sourceType: "user_input",
      readingGoal: "exam",
      readingVariant,
      profileId: readingVariant,
    },
    article: {
      paragraphs: [{ paragraphId: "exam-p1", sentenceIds: ["exam-s1", "exam-s2", "exam-s3"] }],
      sentences: examSentences,
    },
    userFacingState: "normal",
    translations: examTranslations,
    inlineMarks,
    sentenceEntries,
    warnings: [],
  };
}

export const productDemoScenes: Record<ProductDemoSceneId, ReaderMockVm> = {
  daily_intermediate: {
    schemaVersion: "3.0.0",
    request: {
      requestId: "product-demo-daily-intermediate",
      sourceType: "user_input",
      readingGoal: "daily_reading",
      readingVariant: "intermediate_reading",
      profileId: "daily_intermediate",
    },
    article: {
      paragraphs: [{ paragraphId: "daily-p1", sentenceIds: ["daily-s1", "daily-s2", "daily-s3"] }],
      sentences: [
        {
          sentenceId: "daily-s1",
          paragraphId: "daily-p1",
          text: "Claread is built for readers who want to stay with the original English, not skip past it.",
        },
        {
          sentenceId: "daily-s2",
          paragraphId: "daily-p1",
          text: "Instead of turning an article into a short answer, it keeps each sentence visible and opens the vocabulary, grammar, and meaning only where they help.",
        },
        {
          sentenceId: "daily-s3",
          paragraphId: "daily-p1",
          text: "When a phrase feels familiar but the sentence still feels uncertain, Claread shows the relationship between the words so the paragraph becomes easier to follow.",
        },
      ],
    },
    userFacingState: "normal",
    translations: [
      {
        sentenceId: "daily-s1",
        translationZh: "Claread 是为那些想留在英文原文里阅读、而不是直接跳过原文的读者设计的。",
      },
      {
        sentenceId: "daily-s2",
        translationZh: "Claread 不会把文章变成一个简短答案，而是保留每个句子，只在有助于理解时展开词汇、语法和意思。",
      },
      {
        sentenceId: "daily-s3",
        translationZh: "当一个短语看起来熟悉、但整句话仍然不确定时，Claread 会展示词语之间的关系，让段落更容易读下去。",
      },
    ],
    inlineMarks: [
      {
        id: "daily-im-phrase-short-answer",
        annotationType: "phrase_gloss",
        anchor: {
          kind: "text",
          sentenceId: "daily-s2",
          anchorText: "turning an article into a short answer",
          occurrence: 1,
        },
        renderType: "background",
        visualTone: "phrase",
        clickable: true,
        lookupText: "turning an article into a short answer",
        lookupKind: "phrase",
        glossary: { zh: "把文章压缩成一个简短答案", phraseType: "fixed_collocation" },
      },
      {
        id: "daily-im-context-opens",
        annotationType: "context_gloss",
        anchor: { kind: "text", sentenceId: "daily-s2", anchorText: "opens", occurrence: 1 },
        renderType: "underline",
        visualTone: "context",
        clickable: true,
        lookupText: "opens",
        lookupKind: "word",
        glossary: {
          gloss: "展开、呈现",
          reason: "这里不是打开门，而是把有助于理解的信息展开给读者看。",
        },
      },
      {
        id: "daily-im-grammar-instead",
        annotationType: "grammar_note",
        anchor: {
          kind: "text",
          sentenceId: "daily-s2",
          anchorText: "Instead of turning an article into a short answer",
          occurrence: 1,
        },
        renderType: "underline",
        visualTone: "grammar",
        clickable: false,
      },
    ],
    sentenceEntries: [
      {
        id: "daily-se-grammar-instead",
        sentenceId: "daily-s2",
        entryType: "grammar_note",
        label: "instead of 结构",
        title: "instead of + doing",
        content:
          "Instead of + doing 表示“不做前一件事，而做后一件事”。前半句说明 Claread 不把文章压缩成短答案，真正的主句从 it keeps each sentence visible 开始。",
      },
      {
        id: "daily-se-analysis-s2",
        sentenceId: "daily-s2",
        entryType: "sentence_analysis",
        label: "对比结构 + 并列动作",
        title: "对比结构 + 并列动作",
        content:
          "这句话先用 Instead of 引出 Claread 不做什么，再用 it keeps... and opens... 说明它真正做什么。\n\n- **1. 对比背景**：`Instead of turning an article into a short answer`\n- **2. 主语**：`it`\n- **3. 动作一**：`keeps each sentence visible`\n- **4. 动作二**：`opens the vocabulary, grammar, and meaning`\n- **5. 限定条件**：`only where they help`",
      },
    ],
    warnings: [],
  },
  exam_cet: examScene(
    "product-demo-exam-cet",
    "cet",
    [
      {
        id: "exam-cet-im-not-a-but-b",
        annotationType: "grammar_note",
        anchor: {
          kind: "multi_text",
          sentenceId: "exam-s1",
          parts: [
            { anchorText: "not that every word is unknown", role: "不是 A" },
            { anchorText: "but that a sentence hides the tested information", role: "而是 B" },
          ],
        },
        renderType: "underline",
        visualTone: "grammar",
        clickable: false,
      },
      {
        id: "exam-cet-im-tested-information",
        annotationType: "phrase_gloss",
        anchor: {
          kind: "text",
          sentenceId: "exam-s1",
          anchorText: "tested information",
          occurrence: 1,
        },
        renderType: "background",
        visualTone: "phrase",
        clickable: true,
        lookupText: "tested information",
        lookupKind: "phrase",
        glossary: { zh: "题目真正考查的信息点", phraseType: "name_or_term" },
      },
      {
        id: "exam-cet-im-rewritten",
        annotationType: "context_gloss",
        anchor: {
          kind: "text",
          sentenceId: "exam-s1",
          anchorText: "rewritten expressions",
          occurrence: 1,
        },
        renderType: "underline",
        visualTone: "context",
        clickable: true,
        lookupText: "rewritten expressions",
        lookupKind: "phrase",
        glossary: {
          gloss: "被同义改写的表达",
          reason: "四六级阅读中，题干和选项常不用原文原词，而用同义替换制造定位难度。",
        },
      },
    ],
    [
      {
        id: "exam-cet-grammar-not-a-but-b",
        sentenceId: "exam-s1",
        entryType: "grammar_note",
        label: "提速结构：not A but B",
        title: "not that... but that...",
        content:
          "四六级阅读里，这类结构常把真正强调的信息放在 but that 后面。快速阅读时先定位 B：a sentence hides the tested information，再回看 A 只是被排除的原因。",
      },
      {
        id: "exam-cet-analysis-s1",
        sentenceId: "exam-s1",
        entryType: "sentence_analysis",
        label: "主干 + 提速定位",
        title: "主干 + 提速定位",
        content:
          "主干是 the difficulty is not..., but...。开头 For students preparing for exams 只是对象背景，真正的判断在 not A but B。\n\n- **1. 对象背景**：`For students preparing for exams`\n- **2. 主干开头**：`the difficulty is often`\n- **3. 排除项**：`not that every word is unknown`\n- **4. 真正重点**：`but that a sentence hides the tested information`\n- **5. 信息隐藏位置**：`inside clauses, modifiers, and rewritten expressions`",
      },
    ],
  ),
  exam_kaoyan: examScene(
    "product-demo-exam-kaoyan",
    "kaoyan",
    [
      {
        id: "exam-kaoyan-im-long-structure",
        annotationType: "grammar_note",
        anchor: {
          kind: "text",
          sentenceId: "exam-s1",
          anchorText: "inside clauses, modifiers, and rewritten expressions",
          occurrence: 1,
        },
        renderType: "underline",
        visualTone: "grammar",
        clickable: false,
      },
      {
        id: "exam-kaoyan-im-modifiers",
        annotationType: "context_gloss",
        anchor: { kind: "text", sentenceId: "exam-s1", anchorText: "modifiers", occurrence: 1 },
        renderType: "underline",
        visualTone: "context",
        clickable: true,
        lookupText: "modifiers",
        lookupKind: "word",
        glossary: {
          gloss: "修饰成分",
          reason: "考研长难句中，修饰成分经常拉长主干距离，导致读者找不到核心判断。",
        },
      },
    ],
    [
      {
        id: "exam-kaoyan-analysis-s1",
        sentenceId: "exam-s1",
        entryType: "sentence_analysis",
        label: "长难句层次拆解",
        title: "先找主干，再处理枝叶",
        content:
          "这句的理解门槛不在单词，而在层次。主干是 the difficulty is not A, but B。考研阅读中要先抓住这个判断框架，再处理句首对象背景和句尾介词短语。\n\n- **1. 句首背景**：`For students preparing for exams`\n- **2. 判断框架**：`the difficulty is often not that every word is unknown, but that a sentence hides the tested information`\n- **3. 真正难点**：`a sentence hides the tested information`\n- **4. 位置补充**：`inside clauses, modifiers, and rewritten expressions`",
      },
    ],
  ),
  exam_ielts_toefl: examScene(
    "product-demo-exam-ielts-toefl",
    "ielts_toefl",
    [
      {
        id: "exam-ielts-im-while",
        annotationType: "grammar_note",
        anchor: {
          kind: "text",
          sentenceId: "exam-s2",
          anchorText: "while the answer is carried by a contrast marker",
          occurrence: 1,
        },
        renderType: "underline",
        visualTone: "grammar",
        clickable: false,
      },
      {
        id: "exam-ielts-im-paraphrased",
        annotationType: "phrase_gloss",
        anchor: {
          kind: "text",
          sentenceId: "exam-s2",
          anchorText: "has been paraphrased",
          occurrence: 1,
        },
        renderType: "background",
        visualTone: "phrase",
        clickable: true,
        lookupText: "paraphrased",
        lookupKind: "word",
        glossary: { zh: "被改写", phraseType: "fixed_collocation" },
      },
    ],
    [
      {
        id: "exam-ielts-grammar-while",
        sentenceId: "exam-s2",
        entryType: "grammar_note",
        label: "信息定位：while 对比",
        title: "while 引出信息落差",
        content:
          "IELTS / TOEFL 阅读中，while 常标记对比或转折。题目问 writer's attitude，但答案可能不在 attitude 这个词附近，而在后半句的 contrast marker、reduced relative clause 或 paraphrased phrase。",
      },
      {
        id: "exam-ielts-analysis-s2",
        sentenceId: "exam-s2",
        entryType: "sentence_analysis",
        label: "题干与答案位置错开",
        title: "题干与答案位置错开",
        content:
          "这句话模拟了学术阅读题的常见难点：题目问的是一个抽象对象，但答案由结构信号承载。\n\n- **1. 题干方向**：`A question may ask about the writer's attitude`\n- **2. 对比转入答案**：`while the answer is carried by`\n- **3. 答案线索一**：`a contrast marker`\n- **4. 改写线索**：`a phrase that has been paraphrased in the options`",
      },
    ],
  ),
  academic_general: {
    schemaVersion: "3.0.0-academic",
    request: {
      requestId: "product-demo-academic-general",
      sourceType: "user_input",
      readingGoal: "academic",
      readingVariant: "academic_general",
      profileId: "academic_general",
    },
    article: {
      paragraphs: [{ paragraphId: "academic-p1", sentenceIds: ["academic-s1", "academic-s2"] }],
      sentences: [
        {
          sentenceId: "academic-s1",
          paragraphId: "academic-p1",
          text: "In the Claread workflow, the source text is treated as an anchored reading object: after sentence boundaries are established, goal-specific agents generate candidate annotations, which are normalized, grounded against the original text, and projected into a reader scene.",
        },
        {
          sentenceId: "academic-s2",
          paragraphId: "academic-p1",
          text: "This design reduces the risk that an explanation becomes detached from the passage, while allowing academic readers to inspect terminology, logical relations, and interpretive notes without losing sight of the original argument.",
        },
      ],
    },
    userFacingState: "normal",
    translations: [
      {
        sentenceId: "academic-s1",
        translationZh:
          "在 Claread 工作流中，源文本被视为一个带锚点的阅读对象：在句子边界建立之后，面向特定阅读目标的 agent 会生成候选标注；这些标注随后被归一化、与原文校验，并投影到 reader scene 中。",
      },
      {
        sentenceId: "academic-s2",
        translationZh:
          "这一设计降低了解释脱离原文段落的风险，同时让学术读者能够查看术语、逻辑关系和解释性笔记，而不丢失对原始论证的把握。",
      },
    ],
    inlineMarks: [
      {
        id: "academic-im-term-anchored-object",
        annotationType: "term_note",
        anchor: {
          kind: "text",
          sentenceId: "academic-s1",
          anchorText: "anchored reading object",
          occurrence: 1,
        },
        renderType: "background",
        visualTone: "term",
        clickable: true,
        lookupText: "anchored reading object",
        glossary: {
          zh: "锚定阅读对象",
          gloss: "可被句子、片段和标注稳定引用的原文阅读单元。",
        },
      },
      {
        id: "academic-im-logic-after",
        annotationType: "logic_note",
        anchor: {
          kind: "text",
          sentenceId: "academic-s1",
          anchorText: "after sentence boundaries are established",
          occurrence: 1,
        },
        renderType: "underline",
        visualTone: "logic",
        clickable: true,
        lookupText: "after sentence boundaries are established",
        glossary: {
          gloss: "时序关系",
          reason: "after 标明流程顺序：先建立句子边界，再生成候选标注。",
        },
      },
    ],
    sentenceEntries: [
      {
        id: "academic-term-anchored-object",
        sentenceId: "academic-s1",
        entryType: "term_note",
        label: "anchored reading object: 锚定阅读对象",
        title: "锚定阅读对象",
        content: "在本文语境中，指可被句子、片段和标注稳定引用的原文对象。重点不是文件本身，而是可回源的阅读单元。",
      },
      {
        id: "academic-logic-after",
        sentenceId: "academic-s1",
        entryType: "logic_note",
        label: "sequence: after",
        title: "时序关系",
        content:
          "after 标明流程顺序：先建立 sentence boundaries，再生成 candidate annotations。这个逻辑关系说明 Claread 的解析不是脱离原文自由发挥，而是先建立可定位的文本基础。",
      },
      {
        id: "academic-interpretation-s1",
        sentenceId: "academic-s1",
        entryType: "interpretation_note",
        label: "解释",
        title: "解释性改写",
        content: "这句话说明 Claread 先把原文切成可定位的句子，再让不同目标的 agent 生成标注，最后把结果校验并投影成 Reader 可渲染的场景。",
      },
    ],
    warnings: [],
  },
};
