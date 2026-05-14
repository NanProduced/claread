export type ReviewSubmitResultDto = "known" | "unfamiliar";

export interface ReviewSubmitRequestDto {
  result: ReviewSubmitResultDto;
}

export interface ReviewResultResponseDto {
  vocab_id: string;
  lemma: string;
  stage: number;
  next_review_at: string | null;
  mastery_status: string;
  review_count: number;
}
