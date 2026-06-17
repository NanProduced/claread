export type ReaderGoalDemoId = "daily_reading" | "exam" | "academic";

export type ReaderGoalDemoVariantId =
  | "beginner_reading"
  | "intensive_reading"
  | "cet"
  | "kaoyan"
  | "ielts_toefl"
  | "academic_general";

export type ReaderGoalAnnotationLayout = "upper-margin" | "middle-margin" | "lower-margin";

export type ReaderDemoTextSegment = {
  text: string;
  highlight?: boolean;
};

export type ReaderGoalGrammarNote = {
  label: string;
  note: string;
};

export type ReaderGoalDemoPreview = {
  sourceKey: string;
  annotationLayout: ReaderGoalAnnotationLayout;
  source: ReaderDemoTextSegment[];
  translation: string;
  note: ReaderGoalGrammarNote;
};

export type ReaderGoalDemoVariant = {
  id: ReaderGoalDemoVariantId;
  label: string;
  headline: string;
  description: string;
  preview: ReaderGoalDemoPreview;
};

export type ReaderGoalDemoSection = {
  id: ReaderGoalDemoId;
  title: string;
  beta?: boolean;
  description: string;
  defaultVariantId: ReaderGoalDemoVariantId;
  variants: ReaderGoalDemoVariant[];
};

const dailySourceBeginner: ReaderDemoTextSegment[] = [
  { text: "Instead of turning an article into a short answer", highlight: true },
  { text: ", Claread keeps each sentence visible and opens the grammar only where it helps." },
];

const dailySourceIntensive: ReaderDemoTextSegment[] = [
  { text: "Instead of turning an article into a short answer, Claread " },
  { text: "keeps each sentence visible and opens the grammar only where it helps", highlight: true },
  { text: "." },
];

const examSourceCet: ReaderDemoTextSegment[] = [
  { text: "For exam readers, the difficulty is " },
  { text: "not that every word is unknown, but that the key information is hidden", highlight: true },
  { text: " inside a long structure." },
];

const examSourceKaoyan: ReaderDemoTextSegment[] = [
  { text: "For exam readers, " },
  { text: "the difficulty is not that every word is unknown, but that the key information is hidden inside a long structure", highlight: true },
  { text: "." },
];

const examSourceIelts: ReaderDemoTextSegment[] = [
  { text: "For exam readers, the difficulty is not that every word is unknown, but that the key information is " },
  { text: "hidden inside a long structure", highlight: true },
  { text: "." },
];

const academicSource: ReaderDemoTextSegment[] = [
  { text: "After sentence boundaries are established", highlight: true },
  { text: ", goal-specific notes are grounded against the original text before they enter the reader scene." },
];

export const readerGoalDemoSections: ReaderGoalDemoSection[] = [
  {
    id: "daily_reading",
    title: "Daily Reading",
    description: "日常阅读先保留原文节奏，再在真正卡住的词、短语和句子关系上展开。",
    defaultVariantId: "intensive_reading",
    variants: [
      {
        id: "beginner_reading",
        label: "入门",
        headline: "入门：先让句意成立",
        description: "更直白地拆句，少用术语，先让句意成立。",
        preview: {
          sourceKey: "daily-reading-principle",
          annotationLayout: "upper-margin",
          source: dailySourceBeginner,
          translation: "Claread 不把文章变成简短答案，而是保留每个句子，只在有助于理解时展开语法。",
          note: {
            label: "instead of + doing",
            note: "先看 instead of 后面被排除的动作：不是把文章压成短答案。再回到主句，看 Claread 实际保留了什么。",
          },
        },
      },
      {
        id: "intensive_reading",
        label: "精读",
        headline: "精读：看结构如何承载意思",
        description: "更关注结构如何承载意义，保留用词和表达的细节。",
        preview: {
          sourceKey: "daily-reading-principle",
          annotationLayout: "middle-margin",
          source: dailySourceIntensive,
          translation: "Claread 不把文章变成简短答案，而是保留每个句子，只在有助于理解时展开语法。",
          note: {
            label: "对比结构",
            note: "这句话不是简单说功能，而是先排除“压缩成答案”，再用 keeps 和 opens 展开阅读原则，结构本身承载了产品立场。",
          },
        },
      },
    ],
  },
  {
    id: "exam",
    title: "Exam Reading",
    description: "考试阅读会把解释重点转到定位、改写、长难句主干和结构信号。",
    defaultVariantId: "kaoyan",
    variants: [
      {
        id: "cet",
        label: "四六级",
        headline: "四六级：先定位结构信号",
        description: "提速定位，识别 not A but B、同义替换和关键信息块。",
        preview: {
          sourceKey: "exam-reading-difficulty",
          annotationLayout: "middle-margin",
          source: examSourceCet,
          translation: "对备考学生来说，难点不是每个词都不认识，而是关键信息藏在很长的结构里。",
          note: {
            label: "not A but B",
            note: "四六级阅读里，这类结构常用于排除表面原因，再给出真正原因。第一遍先抓 but 后面的信息，更容易对应选项改写。",
          },
        },
      },
      {
        id: "kaoyan",
        label: "考研",
        headline: "考研：先拆长难句主干",
        description: "优先拆长难句层次，先找主干，再处理修饰和嵌套。",
        preview: {
          sourceKey: "exam-reading-difficulty",
          annotationLayout: "middle-margin",
          source: examSourceKaoyan,
          translation: "对备考学生来说，难点不是每个词都不认识，而是关键信息藏在很长的结构里。",
          note: {
            label: "长难句主干",
            note: "这句话的主干不是所有修饰成分，而是 the difficulty is not A, but B。先抽出判断框架，再处理后面的 inside 短语。",
          },
        },
      },
      {
        id: "ielts_toefl",
        label: "雅思托福",
        headline: "雅思托福：识别题干与答案落差",
        description: "服务信息提取，说明结构在题目判断和改写中的作用。",
        preview: {
          sourceKey: "exam-reading-difficulty",
          annotationLayout: "lower-margin",
          source: examSourceIelts,
          translation: "对备考学生来说，难点不是每个词都不认识，而是关键信息藏在很长的结构里。",
          note: {
            label: "题干与答案落差",
            note: "雅思托福题干常把注意力放在表层难点上，但真正答案可能藏在结构里。这里要顺着 hidden inside 去找信息落点。",
          },
        },
      },
    ],
  },
  {
    id: "academic",
    title: "Academic Reading",
    beta: true,
    description: "学术模式正在打磨中，方向是术语、逻辑关系和论证结构回到原文位置。",
    defaultVariantId: "academic_general",
    variants: [
      {
        id: "academic_general",
        label: "Academic General",
        headline: "Academic Beta：把术语和逻辑锚回原文",
        description: "Beta，先展示术语、时序和解释性笔记如何锚定到原文。",
        preview: {
          sourceKey: "academic-reading-policy",
          annotationLayout: "upper-margin",
          source: academicSource,
          translation: "在句子边界建立之后，面向目标的笔记会先和原文校验，再进入阅读场景。",
          note: {
            label: "after + 主句",
            note: "after 把流程顺序固定下来：先建立句子边界，再生成并校验笔记。这个关系说明解释不是脱离原文自由发挥。",
          },
        },
      },
    ],
  },
];
