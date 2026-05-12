const LOGIC_TITLE_MAP: Record<string, string> = {
  elaboration: '详细阐述',
  contrast: '对比论证',
  concession: '让步转折',
  rebuttal: '反驳质疑',
  causation: '因果关系',
  sequence: '顺序关系',
  addition: '递进补充',
  summary: '总结概括',
  example: '举例说明',
  evidence: '证据支撑',
  authority: '权威引用',
  analogy: '类比说明',
  condition: '条件假设',
  purpose: '目的意图',
  definition: '定义界定',
}

const INTERPRETATION_TITLE_MAP: Record<string, string> = {
  disambiguation: '消除歧义',
  decontextualization: '语境还原',
  clarification: '澄清说明',
  implication: '隐含意义',
  reformulation: '改写重述',
  connection: '关联提示',
  cultural_context: '文化背景',
  historical_context: '历史背景',
  domain_context: '领域背景',
  paraphrase: '意译解释',
  gloss: '注释说明',
  explication: '详细解释',
}

const GRAMMAR_TITLE_MAP: Record<string, string> = {
  passive_voice: '被动语态',
  active_voice: '主动语态',
  subjunctive_mood: '虚拟语气',
  conditional_mood: '条件语气',
  imperative_mood: '祈使语气',
  inversion: '倒装结构',
  emphasis: '强调句型',
  cleft_sentence: '分裂句',
  existential_there: '存在句',
  relative_clause: '定语从句',
  noun_clause: '名词性从句',
  adverbial_clause: '状语从句',
  infinitive: '不定式结构',
  gerund: '动名词结构',
  participle: '分词结构',
}

const TERM_TITLE_MAP: Record<string, string> = {
  technical_term: '专业术语',
  domain_jargon: '领域行话',
  theoretical_concept: '理论概念',
  methodology_term: '方法术语',
  key_concept: '核心概念',
  key_term: '关键术语',
}

const SUMMARY_TITLE_MAP: Record<string, string> = {
  overview: '内容概述',
  abstract: '摘要',
  executive_summary: '执行摘要',
}

const ENTRY_MAP: Record<string, Record<string, string>> = {
  logic_note: LOGIC_TITLE_MAP,
  interpretation_note: INTERPRETATION_TITLE_MAP,
  grammar_note: GRAMMAR_TITLE_MAP,
  term_note: TERM_TITLE_MAP,
  content_summary: SUMMARY_TITLE_MAP,
}

const FALLBACK_LABELS: Record<string, string> = {
  logic_note: '逻辑关系',
  interpretation_note: '解释说明',
  grammar_note: '语法要点',
  content_summary: '内容概要',
}

function containsChinese(text: string): boolean {
  return /[\u4e00-\u9fff]/.test(text)
}

export function getMappedEntryTitle(
  entryType: string,
  originalTitle: string | undefined,
  fallbackLabel: string
): string {
  if (!originalTitle || originalTitle.trim() === '') {
    return fallbackLabel
  }

  if (containsChinese(originalTitle)) {
    return originalTitle
  }

  const map = ENTRY_MAP[entryType]
  if (map) {
    const mapped = map[originalTitle.toLowerCase().trim()]
    if (mapped) return mapped
  }

  if (entryType === 'term_note') {
    return originalTitle
  }

  return FALLBACK_LABELS[entryType] || fallbackLabel
}

export { LOGIC_TITLE_MAP, INTERPRETATION_TITLE_MAP, GRAMMAR_TITLE_MAP, TERM_TITLE_MAP, SUMMARY_TITLE_MAP }
