import type { ReviewSubmitResultDto } from "@/types/api/review";

export type ReviewAction = ReviewSubmitResultDto;

export interface ReviewItemVm {
  id: string;
  lemma: string;
  displayWord: string;
  phonetic?: string;
  partOfSpeech?: string;
  meaning: string;
  sourceSentence?: string;
  sourceContext?: string;
  masteryStatus: string;
  reviewStage: number;
  reviewCount: number;
  nextReviewAt?: string;
  lastReviewedAt?: string;
  createdAt: string;
}
