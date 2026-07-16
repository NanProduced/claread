import type { ReaderMockVm } from "@/types/view/ReaderMockVm";

export type HeroReaderMode = "intensive" | "immersive";

export interface HeroReaderRecord {
  id: string;
  title: string;
  date: string;
  sourceName: string;
  sourceDomain?: string;
  readingGoal: "daily_reading" | "exam" | "academic";
  readingGoalLabel: string;
  readingVariantLabel: string;
  sourceTypeLabel: string;
  status: "ready";
  excerpt: string;
  wordCount: number;
  noteCount: number;
  vocabularyCount: number;
  isFavorited: boolean;
  scene: ReaderMockVm;
  selectedSentenceId: string;
  expandedEntryIds: string[];
}

export const heroComposeText =
  "Nationally, one in six children miss 15 or more days of school in a year. Education officials have deplored all this missed instruction. These chronically absent students suffer academically because of all the classroom instruction they miss out on.";

const educationScene: ReaderMockVm = {
  schemaVersion: "3.0.0",
  request: {
    requestId: "hero-app-education-policy",
    sourceType: "user_input",
    readingGoal: "exam",
    readingVariant: "cet",
    profileId: "hero-app-stage",
  },
  article: {
    paragraphs: [
      {
        paragraphId: "edu-p1",
        sentenceIds: ["edu-s1", "edu-s2", "edu-s3"],
      },
      {
        paragraphId: "edu-p2",
        sentenceIds: ["edu-s4", "edu-s5", "edu-s6"],
      },
    ],
    sentences: [
      {
        sentenceId: "edu-s1",
        paragraphId: "edu-p1",
        text: "Nationally, one in six children miss 15 or more days of school in a year.",
      },
      {
        sentenceId: "edu-s2",
        paragraphId: "edu-p1",
        text: "Education officials have deplored all this missed instruction.",
      },
      {
        sentenceId: "edu-s3",
        paragraphId: "edu-p1",
        text: "These chronically absent students suffer academically because of all the classroom instruction they miss out on.",
      },
      {
        sentenceId: "edu-s4",
        paragraphId: "edu-p2",
        text: "In 2015, the U.S. secretary of education responded to this crisis, urging communities to support every student to attend every day and be successful in school.",
      },
      {
        sentenceId: "edu-s5",
        paragraphId: "edu-p2",
        text: "His open letter stated that missing 10% of school days in a year for any reason, excused or unexcused, is a primary cause of low academic achievement.",
      },
      {
        sentenceId: "edu-s6",
        paragraphId: "edu-p2",
        text: "The problem is not only an attendance issue; it also reveals how family schedules, school support, and local policy shape a child's chance to keep learning.",
      },
    ],
  },
  userFacingState: "normal",
  translations: [
    {
      sentenceId: "edu-s1",
      translationZh: "在全国范围内，每六个儿童中就有一个在一年内缺勤 15 天或更多。",
    },
    {
      sentenceId: "edu-s2",
      translationZh: "教育官员对所有这些缺失的教学活动表示痛惜。",
    },
    {
      sentenceId: "edu-s3",
      translationZh: "这些长期缺勤的学生因为错过了大量课堂教学而在学业上遭受损失。",
    },
    {
      sentenceId: "edu-s4",
      translationZh: "2015 年，美国教育部长回应了这一危机，敦促社区支持每一位学生每天到校并在学校取得成功。",
    },
    {
      sentenceId: "edu-s5",
      translationZh: "他的公开信指出，无论出于何种原因，请假或缺勤，一年中缺席 10% 的学校日是导致学业成绩低下的主要原因。",
    },
    {
      sentenceId: "edu-s6",
      translationZh: "这个问题不仅是出勤问题，也揭示了家庭安排、学校支持和地方政策如何塑造孩子持续学习的机会。",
    },
  ],
  inlineMarks: [
    {
      id: "edu-im-nationally",
      annotationType: "vocab_highlight",
      anchor: { kind: "text", sentenceId: "edu-s1", anchorText: "Nationally", occurrence: 1 },
      renderType: "background",
      visualTone: "vocab",
      clickable: true,
      lookupText: "Nationally",
      lookupKind: "word",
      glossary: { zh: "在全国范围内" },
    },
    {
      id: "edu-im-one-in-six",
      annotationType: "grammar_note",
      anchor: { kind: "text", sentenceId: "edu-s1", anchorText: "one in six children miss", occurrence: 1 },
      renderType: "underline",
      visualTone: "grammar",
      clickable: false,
    },
    {
      id: "edu-im-deplored",
      annotationType: "vocab_highlight",
      anchor: { kind: "text", sentenceId: "edu-s2", anchorText: "deplored", occurrence: 1 },
      renderType: "background",
      visualTone: "vocab",
      clickable: true,
      lookupText: "deplored",
      lookupKind: "word",
      glossary: { zh: "强烈反对，痛惜" },
    },
    {
      id: "edu-im-chronically",
      annotationType: "vocab_highlight",
      anchor: { kind: "text", sentenceId: "edu-s3", anchorText: "chronically", occurrence: 1 },
      renderType: "background",
      visualTone: "vocab",
      clickable: true,
      lookupText: "chronically",
      lookupKind: "word",
      glossary: { zh: "长期地，反复地" },
    },
    {
      id: "edu-im-miss-out-on",
      annotationType: "phrase_gloss",
      anchor: { kind: "text", sentenceId: "edu-s3", anchorText: "miss out on", occurrence: 1 },
      renderType: "background",
      visualTone: "phrase",
      clickable: true,
      lookupText: "miss out on",
      lookupKind: "phrase",
      glossary: { zh: "错过，失去获得某事的机会", phraseType: "phrasal_verb" },
    },
    {
      id: "edu-im-secretary",
      annotationType: "term_note",
      anchor: { kind: "text", sentenceId: "edu-s4", anchorText: "secretary of education", occurrence: 1 },
      renderType: "background",
      visualTone: "term",
      clickable: true,
      lookupText: "secretary of education",
      lookupKind: "phrase",
      glossary: { zh: "教育部长", phraseType: "proper_noun" },
    },
    {
      id: "edu-im-urging",
      annotationType: "grammar_note",
      anchor: { kind: "text", sentenceId: "edu-s4", anchorText: "urging communities to support", occurrence: 1 },
      renderType: "underline",
      visualTone: "grammar",
      clickable: false,
    },
    {
      id: "edu-im-excused",
      annotationType: "context_gloss",
      anchor: { kind: "text", sentenceId: "edu-s5", anchorText: "excused or unexcused", occurrence: 1 },
      renderType: "background",
      visualTone: "context",
      clickable: true,
      lookupText: "excused or unexcused",
      lookupKind: "phrase",
      glossary: {
        gloss: "无论是否有正当理由",
        reason: "这里强调缺勤比例本身会影响学习结果，不先区分缺勤原因。",
      },
    },
    {
      id: "edu-im-achievement",
      annotationType: "vocab_highlight",
      anchor: { kind: "text", sentenceId: "edu-s5", anchorText: "academic achievement", occurrence: 1 },
      renderType: "background",
      visualTone: "vocab",
      clickable: true,
      lookupText: "academic achievement",
      lookupKind: "phrase",
      glossary: { zh: "学业成就，学业表现", phraseType: "compound" },
    },
    {
      id: "edu-im-not-only",
      annotationType: "grammar_note",
      anchor: {
        kind: "multi_text",
        sentenceId: "edu-s6",
        parts: [
          { anchorText: "not only an attendance issue", role: "不只是 A" },
          { anchorText: "also reveals", role: "还揭示 B" },
        ],
      },
      renderType: "underline",
      visualTone: "grammar",
      clickable: false,
    },
  ],
  sentenceEntries: [
    {
      id: "edu-entry-one-in-six",
      sentenceId: "edu-s1",
      entryType: "grammar_note",
      label: "主谓一致 · 分数/比例作主语",
      title: "one in six children miss",
      content:
        "one in six children 表示“每六个孩子中有一个”。考试阅读里不要只盯 one，要看后面的复数 children，谓语 miss 与 children 保持一致。",
    },
    {
      id: "edu-entry-s4-analysis",
      sentenceId: "edu-s4",
      entryType: "sentence_analysis",
      label: "句子拆解 · 现在分词伴随状语",
      title: "responded..., urging...",
      content:
        "主干是 the U.S. secretary of education responded to this crisis。后面的 urging communities to support... 是现在分词短语，补充说明回应危机的具体做法。\n\n- **时间背景**：`In 2015`\n- **主语**：`the U.S. secretary of education`\n- **主干动作**：`responded to this crisis`\n- **伴随说明**：`urging communities to support every student`\n- **目标结果**：`to attend every day and be successful in school`",
    },
    {
      id: "edu-entry-not-only",
      sentenceId: "edu-s6",
      entryType: "grammar_note",
      label: "递进结构 · not only... also...",
      title: "not only... also...",
      content:
        "这句话不是把问题缩小成出勤，而是把出勤问题推进到家庭、学校和政策三层原因。看到 not only 时，要继续等 also 后面的真正扩展信息。",
    },
  ],
  warnings: [],
};

export const heroDefaultRecord: HeroReaderRecord = {
  id: "hero-record-education",
  title: "旷课背后的家庭危机与教育政策反思",
  date: "2026年6月10日",
  sourceName: "粘贴导入",
  readingGoal: "exam",
  readingGoalLabel: "备考精读",
  readingVariantLabel: "四六级",
  sourceTypeLabel: "粘贴导入",
  status: "ready",
  excerpt:
    "Nationally, one in six children miss 15 or more days of school in a year. Education officials have deplored all this missed instruction.",
  wordCount: 146,
  noteCount: 3,
  vocabularyCount: 7,
  isFavorited: true,
  scene: educationScene,
  selectedSentenceId: "edu-s1",
  expandedEntryIds: ["edu-entry-one-in-six"],
};

export const heroReadingClassName =
  "reader-font-reading text-[1.05rem] leading-[1.95] text-ink";
export const heroImmersiveReadingClassName =
  "reader-font-reading text-[1.18rem] leading-[2] text-ink";
export const heroTranslationClassName =
  "reader-font-sans text-[0.82rem] leading-[1.72]";
export const heroReaderColumnClassName = "max-w-[68ch]";
export const heroReaderDensityClassName = "reader-density-intensive";
export const heroImmersiveDensityClassName = "reader-density-immersive";
