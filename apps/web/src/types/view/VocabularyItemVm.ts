/**
 * 生词本条目 VM
 */
export interface VocabularyItemVm {
  id: string
  word: string
  lookupKind?: 'word' | 'phrase' | 'proper_noun' | 'compound'
  partOfSpeech?: string
  contextSentence?: string
  contextTranslation?: string
  sourceRecordId?: string
  sourceRecordTitle?: string
  createdAt: string
  mastered: boolean
}