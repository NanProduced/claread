/**
 * Daily Reader 阅读页 ViewModel（teaching-v2）。
 *
 * 面向"教学会话式精读"的信息架构：阅读任务 → 正文流（译文按需）→
 * 结构提纲 → 语言精讲 → 证据自测 → 迁移任务 → 收束。
 */

export interface DailyReaderArticle {
  id: string;
  /** 中文主标题（列上的 title，即 blueprint 的 title_zh）。 */
  title: string;
  subtitle: string | null;
  /** 英文原题（caption 级展示）。 */
  originalTitle: string | null;
  /** 中文副标题。 */
  subtitleZh: string | null;
  source: string;
  sourceUrl: string;
  publishDate: string;
  /** 学习难度（CEFR 级别）。 */
  difficulty: string;
  /** v2 文章类型（news_report/opinion_commentary/explainer/narrative_profile）。 */
  articleType: string | null;
  readTimeMinutes: number;
  tags: string[];
  coverImageUrl: string | null;
  coverTheme: string;
  /** 阅读任务卡：mission + 学习目标；pre-v2 行为 null。 */
  mission: DailyReaderMission | null;
  /** 正文流单元（含按需译文与高难标记）。 */
  units: DailyReaderReadingUnit[];
  /** 文章结构提纲（2-6 节点）。 */
  structureMap: DailyReaderStructureNode[];
  /** 语言精讲（3-5 个可迁移表达）。 */
  languageTargets: DailyReaderLanguageTarget[];
  /** 长难句精讲（1-2 句）。 */
  sentenceMaps: DailyReaderSentenceMap[];
  /** 证据型自测（2-4 题，答案默认折叠）。 */
  checkpoints: DailyReaderCheckpoint[];
  /** 单一迁移任务。 */
  transferTask: DailyReaderTransferTask | null;
  /** 收束总结。 */
  postReadSummary: string | null;
  /** 译文覆盖（translated/total），供 UI 展示"选段译文"口径。 */
  translationCoverage: { translated: number; total: number };
}

export interface DailyReaderMission {
  reading: string;
  objectives: string[];
}

export interface DailyReaderReadingUnit {
  id: string;
  text: string;
  /** 本段译文（低级别全量、高级别仅入选段）。 */
  translation: string | null;
  /** blueprint 标记的高难单元。 */
  isHighDifficulty: boolean;
}

export interface DailyReaderStructureNode {
  label: string;
  role: string;
  unitIds: string[];
}

export interface DailyReaderLanguageTarget {
  expression: string;
  unitId: string;
  targetKind: string | null;
  teachingPurpose: string | null;
  meaningZh: string;
  usageNote: string;
  reusablePattern: string;
}

export interface DailyReaderSentenceMap {
  sentence: string;
  unitId: string;
  translation: string;
  complexityKind: "complex_syntax" | "argument_structure" | null;
  teachingPurpose: string | null;
}

export interface DailyReaderCheckpoint {
  skill: string;
  prompt: string;
  promptSubject: string | null;
  referenceAnswer: string;
  answerSubject: string | null;
  evidenceUnitIds: string[];
  answerEvidenceUnitIds: string[];
}

export interface DailyReaderTransferTask {
  taskKind: string;
  prompt: string;
  scaffold: string | null;
  referencePoints: string[];
  contentRequirement: string | null;
}

export interface DailyReaderListItem {
  id: string;
  title: string;
  subtitle: string | null;
  originalTitle: string | null;
  subtitleZh: string | null;
  source: string;
  publishDate: string;
  difficulty: string;
  readTimeMinutes: number;
  tags: string[];
  coverImageUrl: string | null;
  coverTheme: string;
}
