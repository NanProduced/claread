/**
 * 生词本条目 VM
 */
export interface VocabularyItemVm {
  id: string
  word: string
  lookupKind?: 'word' | 'phrase' | 'proper_noun' | 'compound'
  lemma?: string
  phonetic?: string
  partOfSpeech?: string
  shortMeaning?: string
  contextSentence?: string
  contextTranslation?: string
  sourceRecordId?: string
  sourceRecordTitle?: string
  createdAt: string
  updatedAt?: string
  mastered: boolean
  masteryStatus?: string
  reviewCount?: number
  tags?: string[]
}
