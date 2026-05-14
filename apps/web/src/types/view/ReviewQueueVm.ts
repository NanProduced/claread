import type { ReviewItemVm } from "@/types/view/ReviewItemVm";

export type ReviewQueueState =
  | "ready"
  | "empty"
  | "anonymous"
  | "mock_session"
  | "upstream_unavailable"
  | "error";

export interface ReviewQueueVm {
  state: ReviewQueueState;
  items: ReviewItemVm[];
  total: number;
  limit: number;
  message?: string;
  status?: number;
}

export interface ReviewSubmitResultVm {
  vocabId: string;
  lemma: string;
  stage: number;
  nextReviewAt?: string;
  masteryStatus: string;
  reviewCount: number;
}
