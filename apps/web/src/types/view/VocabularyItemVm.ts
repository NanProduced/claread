import type { VocabularySourceRefDto } from "@/types/api/vocabulary";

export interface DetailMeaning {
  partOfSpeech: string
  definitions: Array<{
    meaning: string
    example?: string
    exampleTranslation?: string
  }>
}

export interface DetailPhrase {
  phrase: string
  meaning?: string
}

export interface DetailExample {
  example: string
  exampleTranslation?: string
}

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
  sourceReadingRecordId?: string
  sourceRecordTitle?: string
  createdAt: string
  updatedAt?: string
  mastered: boolean
  masteryStatus?: string
  reviewCount?: number
  tags?: string[]
  nextReviewAt: string | undefined
  reviewStage: number | undefined
  lastReviewedAt: string | undefined
  sourceRefs: VocabularySourceRefDto[]
  collectedForms: string[]
  dictEntryId: number | null
  audioUrl: string | undefined
  detailMeanings: DetailMeaning[] | undefined
  detailPhrases: DetailPhrase[] | undefined
  detailExamples: DetailExample[] | undefined
  totalSourceCount: number
  totalSourceArticleCount: number
}
