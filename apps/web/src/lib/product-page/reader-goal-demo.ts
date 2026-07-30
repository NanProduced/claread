import {
  getReadingGoalOption,
  getReadingVariantOption,
} from "@/lib/reading-defaults";

export type ReaderGoalDemoId = "daily_reading" | "exam";

export type ReaderGoalDemoVariantId =
  | "beginner_reading"
  | "intermediate_reading"
  | "intensive_reading"
  | "gaokao"
  | "cet"
  | "kaoyan"
  | "tem"
  | "ielts_toefl";

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

export const readerGoalDemoSections: ReaderGoalDemoSection[] = [
  {
    id: "daily_reading",
    title: getReadingGoalOption("daily_reading")?.label ?? "日常阅读",
    description:
      getReadingGoalOption("daily_reading")?.description ??
      "兼顾理解、词汇与表达积累，适合持续阅读。",
    defaultVariantId: "intensive_reading",
    variants: [
      {
        id: "beginner_reading",
        label: "入门",
        headline: "入门：先让句意成立",
        description:
          getReadingVariantOption("daily_reading", "beginner_reading")
            ?.description ?? "更直白地拆解句意，适合建立阅读信心。",
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
        id: "intermediate_reading",
        label: "进阶",
        headline: "进阶：兼顾理解与表达积累",
        description:
          getReadingVariantOption("daily_reading", "intermediate_reading")
            ?.description ?? "平衡理解、词汇与语法，适合日常使用。",
        preview: {
          sourceKey: "daily-reading-principle",
          annotationLayout: "middle-margin",
          source: dailySourceIntensive,
          translation:
            "Claread 不把文章变成简短答案，而是保留每个句子，只在有助于理解时展开语法。",
          note: {
            label: "表达积累",
            note: "先理解主句，再观察 keeps 与 opens 如何组织产品原则，同时积累可复用的表达方式。",
          },
        },
      },
      {
        id: "intensive_reading",
        label: "精读",
        headline: "精读：看结构如何承载意思",
        description:
          getReadingVariantOption("daily_reading", "intensive_reading")
            ?.description ?? "深入分析语法、结构与表达细节。",
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
    title: getReadingGoalOption("exam")?.label ?? "备考精读",
    description:
      getReadingGoalOption("exam")?.description ??
      "围绕考试要求，突出长难句、考点与题感。",
    defaultVariantId: "kaoyan",
    variants: [
      {
        id: "gaokao",
        label: "高考",
        headline: "高考：先抓核心语法与题型",
        description:
          getReadingVariantOption("exam", "gaokao")?.description ??
          "聚焦中学核心词汇、语法与阅读题型。",
        preview: {
          sourceKey: "exam-reading-difficulty",
          annotationLayout: "middle-margin",
          source: examSourceCet,
          translation:
            "对备考学生来说，难点不是每个词都不认识，而是关键信息藏在很长的结构里。",
          note: {
            label: "核心语法",
            note: "先识别 not A but B 的对比骨架，再把修饰语放回句子，减少被表层长句干扰。",
          },
        },
      },
      {
        id: "cet",
        label: "四六级",
        headline: "四六级：先定位结构信号",
        description:
          getReadingVariantOption("exam", "cet")?.description ??
          "聚焦主干信息、同义替换与常见考点。",
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
        description:
          getReadingVariantOption("exam", "kaoyan")?.description ??
          "聚焦长难句结构、篇章逻辑与深层推理。",
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
        id: "tem",
        label: "专四专八",
        headline: "专四专八：辨析高级语法与表达",
        description:
          getReadingVariantOption("exam", "tem")
            ?.description ?? "聚焦高级语法、修辞与语言表达。",
        preview: {
          sourceKey: "exam-reading-difficulty",
          annotationLayout: "middle-margin",
          source: examSourceKaoyan,
          translation:
            "对备考学生来说，难点不是每个词都不认识，而是关键信息藏在很长的结构里。",
          note: {
            label: "结构与表达",
            note: "在识别主干后继续观察修饰层次和表达选择，区分语法正确与表达精确。",
          },
        },
      },
      {
        id: "ielts_toefl",
        label: "雅思托福",
        headline: "雅思托福：识别题干与答案落差",
        description:
          getReadingVariantOption("exam", "ielts_toefl")?.description ??
          "聚焦学术语境、信息定位与题型判断。",
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
];
