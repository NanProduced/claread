/**
 * 历史记录列表项 VM
 */
export interface RecordListItemVm {
  id: string
  title: string
  sourceText: string
  readingGoal: string
  readingVariant: string
  createdAt: string
  wordCount: number
  inlineMarkCount: number
  sentenceEntryCount: number
  translationCount: number
}