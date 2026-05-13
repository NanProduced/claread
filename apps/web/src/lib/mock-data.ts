/**
 * Mock 数据
 *
 * 供 /app、history、vocabulary、profile、reader demo 使用。
 * 不接真实 API，不改后端。
 * 类型简单可读，后续可被真实 adapter 替换。
 */

import type { RecordListItemVm } from '@/types/view/RecordListItemVm'
import type { VocabularyItemVm } from '@/types/view/VocabularyItemVm'
import type { QuotaVm } from '@/types/view/QuotaVm'
import type { ReaderMockVm } from '@/types/view/ReaderMockVm'

// ============ /app 主页 Mock ============

export const mockAppStats = {
  totalRecords: 12,
  totalWords: 3847,
  totalVocabulary: 156,
  streak: 5,
}

// ============ history Mock ============

export const mockHistoryRecords: RecordListItemVm[] = [
  {
    id: 'rec_demo_001',
    title: 'The Silent Spring of AI Regulation',
    sourceText:
      'As artificial intelligence systems become increasingly integrated into critical infrastructure, regulators around the world are grappling with how to balance innovation against systemic risk.',
    readingGoal: 'academic',
    readingVariant: 'academic_general',
    createdAt: '2026-05-12T08:30:00Z',
    wordCount: 142,
    inlineMarkCount: 7,
    sentenceEntryCount: 2,
    translationCount: 5,
  },
  {
    id: 'rec_demo_002',
    title: 'Understanding Quantum Entanglement',
    sourceText:
      'Quantum entanglement is a phenomenon where two or more particles become linked and the quantum state of each particle cannot be described independently.',
    readingGoal: 'daily_reading',
    readingVariant: 'intermediate_reading',
    createdAt: '2026-05-11T14:22:00Z',
    wordCount: 89,
    inlineMarkCount: 4,
    sentenceEntryCount: 1,
    translationCount: 3,
  },
  {
    id: 'rec_demo_003',
    title: 'The Economics of Climate Transition',
    sourceText:
      'The global shift toward renewable energy sources is reshaping economic structures, creating both challenges and opportunities across sectors.',
    readingGoal: 'exam',
    readingVariant: 'cet',
    createdAt: '2026-05-10T09:15:00Z',
    wordCount: 203,
    inlineMarkCount: 9,
    sentenceEntryCount: 3,
    translationCount: 7,
  },
]

// ============ vocabulary Mock ============

export const mockVocabularyList: VocabularyItemVm[] = [
  {
    id: 'voc_001',
    word: 'entanglement',
    lookupKind: 'word',
    partOfSpeech: 'noun',
    contextSentence:
      'Quantum entanglement is a phenomenon where two or more particles become linked.',
    contextTranslation: '量子纠缠是一种两个或多个粒子变得关联的现象。',
    sourceRecordId: 'rec_demo_002',
    sourceRecordTitle: 'Understanding Quantum Entanglement',
    createdAt: '2026-05-11T14:30:00Z',
    mastered: false,
  },
  {
    id: 'voc_002',
    word: 'infrastructure',
    lookupKind: 'word',
    partOfSpeech: 'noun',
    contextSentence:
      'AI systems are increasingly integrated into critical infrastructure.',
    contextTranslation: '人工智能系统正日益集成到关键基础设施中。',
    sourceRecordId: 'rec_demo_001',
    sourceRecordTitle: 'The Silent Spring of AI Regulation',
    createdAt: '2026-05-12T08:45:00Z',
    mastered: true,
  },
  {
    id: 'voc_003',
    word: 'renewable energy',
    lookupKind: 'phrase',
    contextSentence:
      'The global shift toward renewable energy sources is reshaping economic structures.',
    contextTranslation: '全球向可再生能源的转变正在重塑经济结构。',
    sourceRecordId: 'rec_demo_003',
    sourceRecordTitle: 'The Economics of Climate Transition',
    createdAt: '2026-05-10T09:30:00Z',
    mastered: false,
  },
  {
    id: 'voc_004',
    word: 'systemic risk',
    lookupKind: 'phrase',
    contextSentence:
      'Regulators are grappling with how to balance innovation against systemic risk.',
    contextTranslation: '监管机构正在努力平衡创新与系统性风险。',
    sourceRecordId: 'rec_demo_001',
    sourceRecordTitle: 'The Silent Spring of AI Regulation',
    createdAt: '2026-05-12T08:50:00Z',
    mastered: false,
  },
]

// ============ profile / quota Mock ============

export const mockQuota: QuotaVm = {
  profileId: 'prof_demo_001',
  quotaUsed: 8,
  quotaLimit: 20,
  quotaType: 'daily',
  resetAt: '2026-05-13T00:00:00Z',
}

// ============ reader Mock ============

const SENTENCES = [
  {
    sentenceId: 's0',
    paragraphId: 'p0',
    text: 'As artificial intelligence systems become increasingly integrated into critical infrastructure, regulators around the world are grappling with how to balance innovation against systemic risk.',
  },
  {
    sentenceId: 's1',
    paragraphId: 'p0',
    text: 'The European Union\'s AI Act, which came into force in 2024, represents the most comprehensive attempt to date to govern AI deployment.',
  },
  {
    sentenceId: 's2',
    paragraphId: 'p0',
    text: 'Under the regulation, high-risk AI systems must undergo rigorous conformity assessments before being permitted to operate.',
  },
  {
    sentenceId: 's3',
    paragraphId: 'p1',
    text: 'Critics argue that compliance costs could stifle innovation and entrench the position of large incumbents.',
  },
  {
    sentenceId: 's4',
    paragraphId: 'p1',
    text: 'Proponents counter that without such guardrails, the potential for harm far outweighs any economic convenience.',
  },
]

const PARAGRAPHS = [
  { paragraphId: 'p0', sentenceIds: ['s0', 's1', 's2'] },
  { paragraphId: 'p1', sentenceIds: ['s3', 's4'] },
]

export const mockReaderVm: ReaderMockVm = {
  schemaVersion: '3.0.0',
  request: {
    requestId: 'req_demo_001',
    sourceType: 'user_input',
    readingGoal: 'academic',
    readingVariant: 'academic_general',
    profileId: 'prof_demo_001',
  },
  article: {
    paragraphs: PARAGRAPHS,
    sentences: SENTENCES,
  },
  userFacingState: 'normal',
  translations: [
    { sentenceId: 's0', translationZh: '随着人工智能系统日益集成到关键基础设施中，全球监管机构正在努力平衡创新与系统性风险。' },
    { sentenceId: 's1', translationZh: '欧盟于2024年生效的《人工智能法案》代表了迄今为止治理人工智能部署最全面的尝试。' },
    { sentenceId: 's2', translationZh: '根据该法规，高风险人工智能系统在获准运营前必须接受严格的合规评估。' },
    { sentenceId: 's3', translationZh: '批评者认为，合规成本可能会扼杀创新，并巩固大型老牌企业的地位。' },
    { sentenceId: 's4', translationZh: '支持者反驳说，如果没有这些护栏，潜在的危害远远超过任何经济便利。' },
  ],
  inlineMarks: [
    // vocab_highlight
    {
      id: 'im_001',
      annotationType: 'vocab_highlight',
      anchor: { kind: 'text', sentenceId: 's0', anchorText: 'infrastructure', occurrence: 1 },
      renderType: 'background',
      visualTone: 'vocab',
      clickable: true,
      lookupText: 'infrastructure',
      lookupKind: 'word',
      glossary: { zh: '基础设施', gloss: 'the basic physical and organizational structures needed for operation', phraseType: 'compound' },
    },
    {
      id: 'im_002',
      annotationType: 'vocab_highlight',
      anchor: { kind: 'text', sentenceId: 's1', anchorText: 'comprehensive', occurrence: 1 },
      renderType: 'background',
      visualTone: 'vocab',
      clickable: true,
      lookupText: 'comprehensive',
      lookupKind: 'word',
      glossary: { zh: '全面的', gloss: 'including all or nearly all elements or aspects' },
    },
    // phrase_gloss
    {
      id: 'im_003',
      annotationType: 'phrase_gloss',
      anchor: { kind: 'text', sentenceId: 's0', anchorText: 'systemic risk', occurrence: 1 },
      renderType: 'underline',
      visualTone: 'phrase',
      clickable: true,
      lookupText: 'systemic risk',
      lookupKind: 'phrase',
      glossary: {
        zh: '系统性风险',
        gloss: 'risk that threatens an entire system or market, as opposed to risk specific to an individual',
        phraseType: 'compound',
      },
    },
    {
      id: 'im_004',
      annotationType: 'phrase_gloss',
      anchor: { kind: 'text', sentenceId: 's2', anchorText: 'conformity assessments', occurrence: 1 },
      renderType: 'underline',
      visualTone: 'phrase',
      clickable: true,
      lookupText: 'conformity assessments',
      lookupKind: 'phrase',
      glossary: {
        zh: '合规评估',
        gloss: 'formal evaluation to ensure products or systems meet required standards',
        phraseType: 'collocation',
      },
    },
    // context_gloss
    {
      id: 'im_005',
      annotationType: 'context_gloss',
      anchor: { kind: 'text', sentenceId: 's3', anchorText: 'compliance costs', occurrence: 1 },
      renderType: 'underline',
      visualTone: 'context',
      clickable: true,
      lookupText: 'compliance costs',
      lookupKind: 'phrase',
      glossary: { zh: '合规成本', gloss: 'expenses incurred to meet regulatory requirements' },
    },
    {
      id: 'im_006',
      annotationType: 'context_gloss',
      anchor: { kind: 'text', sentenceId: 's4', anchorText: 'guardrails', occurrence: 1 },
      renderType: 'underline',
      visualTone: 'context',
      clickable: true,
      lookupText: 'guardrails',
      lookupKind: 'word',
      glossary: { zh: '护栏', gloss: 'restrictions or safeguards put in place to prevent harmful outcomes' },
    },
    // grammar_note
    {
      id: 'im_007',
      annotationType: 'grammar_note',
      anchor: { kind: 'text', sentenceId: 's0', anchorText: 'As artificial intelligence systems become increasingly integrated', occurrence: 1 },
      renderType: 'background',
      visualTone: 'grammar',
      clickable: true,
      lookupText: 'As + subject + become/is + past participle',
      lookupKind: 'word',
      glossary: {
        zh: 'As 引导的状语从句（原因/时间）',
        gloss: 'As introduces a subordinate clause indicating simultaneity or cause-effect; the subject in the main clause undergoes the state described.',
      },
    },
  ],
  sentenceEntries: [
    {
      id: 'se_001',
      sentenceId: 's0',
      entryType: 'grammar_note',
      label: '语法笔记',
      title: 'As 状语从句结构',
      content: 'As 引导原因或时间状语从句，修饰主句。\n\n结构：As + 主语 + 谓语（状态动词如 become, be）+ ...\n\n本句中 "As artificial intelligence systems become increasingly integrated into critical infrastructure" 为原因状语，解释主句动作 "regulators are grappling" 的背景。\n\n翻译：随着……（因为……）',
    },
    {
      id: 'se_002',
      sentenceId: 's0',
      entryType: 'sentence_analysis',
      label: '句子分析',
      title: '并列复合句结构',
      content: '本句为并列复合句，由 but 连接两个意义相反的分句。\n\n第一分句（主句）：regulators are grappling with how to balance innovation against systemic risk\n第二分句：主语省略，理解为 Critics argue...\n\n\n关键词：\n- grapple with: 努力应对\n- balance A against B: 在 A 与 B 之间取得平衡\n- systemic risk: 系统性风险',
    },
    {
      id: 'se_003',
      sentenceId: 's2',
      entryType: 'grammar_note',
      label: '语法笔记',
      title: 'before being permitted 结构',
      content: 'before + being + past participle 为动名词被动式作时间状语。\n\n还原：before they are permitted to operate\n\n本句中 "before being permitted to operate" = "before high-risk AI systems are permitted to operate"',
    },
    {
      id: 'se_004',
      sentenceId: 's4',
      entryType: 'sentence_analysis',
      label: '句子分析',
      title: 'without such guardrails 介词短语作状语',
      content: '"without such guardrails" 为介词短语（with + 名词）是否定条件状语，相当于 if there were no such guardrails。\n\n主句：the potential for harm far outweighs any economic convenience\n- outweigh: 超过（重要性、价值）\n- any economic convenience: 任何经济便利\n\n语气：虚拟语气意味淡（实际发生的危害），强调对比。',
    },
  ],
  warnings: [],
}