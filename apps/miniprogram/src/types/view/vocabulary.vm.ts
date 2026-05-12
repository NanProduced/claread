/**
 * 生词本 VM
 *
 * 记录从结果页"记入生词本"的词条。
 * 以 lemma 为唯一标识，同 lemma 多次收藏会合并 sourceRefs。
 */

/** 单次收藏来源的语境记录 */
export interface SourceRef {
  /** 来源分析记录的前端稳定主键 */
  clientRecordId: string
  /** 来源分析记录的云端 UUID */
  cloudRecordId?: string
  /** 来源句子文本 */
  sourceSentence?: string
  /** 来源上下文文本 */
  sourceContext?: string
  /** 来源句子 ID（用于跳转定位） */
  sourceSentenceId?: string
  /** 收藏时的锚点文本（如 "adopted"） */
  sourceAnchorText?: string
  /** 在句子中的第几次出现 */
  sourceOccurrence?: number
  /** 收藏时间 ISO 字符串 */
  collectedAt?: string
}

export interface VocabEntry {
  id: string
  /** 同步状态 */
  syncState?: 'local_only' | 'syncing' | 'synced' | 'sync_failed'
  /** 待执行操作 */
  pendingOp?: 'create' | 'update' | 'delete' | null
  /** 最近一次同步错误 */
  lastSyncError?: string | null
  /** 软删除标记 */
  tombstone?: boolean

  /** 词形还原后的原形（主键） */
  lemma: string
  /** 展示词头（用户实际收藏的形态，如 "adopted"） */
  word: string
  /** 词性 */
  partOfSpeech: string
  /** 简短释义 */
  meaning: string
  /** 加入时间 */
  addedAt: number
  /** 是否已掌握 */
  mastered: boolean

  /** 掌握状态 */
  masteryStatus?: string
  /** 复习阶段 */
  reviewStage?: number
  /** 下次复习时间 */
  nextReviewAt?: string
  /** 复习次数 */
  reviewCount?: number
  /** 上次复习时间 */
  lastReviewedAt?: string

  /** 词典词条稳定引用 ID（用于按需加载完整词条） */
  dictEntryId?: number
  /** 音标 */
  phonetic?: string
  /** 深度学习数据（从后端 meanings_json 解析，与 DictionaryMeaning 结构对齐） */
  detailMeanings?: Array<{
    partOfSpeech: string
    definitions: Array<{
      meaning: string
      example?: string
      exampleTranslation?: string
    }>
  }>
  /** 短语快照（离线降级用） */
  detailPhrases?: Array<{
    phrase: string
    meaning?: string
  }>
  /** 例句快照（离线降级用） */
  detailExamples?: Array<{
    example: string
    exampleTranslation?: string
  }>
  /** 词形变换列表 */
  exchange?: string[]
  /** 标签列表 */
  tags?: string[]
  /** 词典来源 */
  provider?: string

  /** 最近一次来源句子文本（顶层兼容字段，用于列表页快速展示） */
  sentence?: string
  /** 最近一次来源上下文文本 */
  context?: string

  /** 多来源语境数组（详情页使用） */
  sourceRefs?: SourceRef[]
  /** 用户实际收藏过的词形变体列表 */
  collectedForms?: string[]
  /** Free Dictionary API 音频 URL 缓存 */
  audioUrl?: string
}

/** saveVocabEntry 的返回结果，用于 toast 反馈 */
export interface SaveVocabResult {
  entry: VocabEntry
  /** 是否发生了 lemma 归并（同 lemma 已存在） */
  merged: boolean
  /** 归并后的语境总数 */
  totalSourceCount: number
}

/** highlights API 单个匹配结果 */
export interface VocabHighlightMatch {
  vocabId: string
  lemma: string
  sentenceId: string
  anchorText: string
  occurrence: number
  masteryStatus: string
}
