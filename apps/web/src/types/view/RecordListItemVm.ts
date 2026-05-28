/**
 * 历史记录列表项 VM
 */
export interface RecordListItemVm {
  id: string
  title: string
  sourceText: string
  sourceTextExcerpt: string
  sourceType: string
  readingGoal: string
  readingVariant: string
  analysisStatus: string
  lastOpenedAt: string | null
  createdAt: string
  updatedAt: string
  wordCount: number
  noteCount: number
  vocabularyCount: number
  isFavorited: boolean
}
