import "server-only";

import {
  getUpstreamDueReviewVocabulary,
  submitUpstreamVocabularyReview,
} from "@/services/api/review";
import { getWebSession } from "@/services/bff/session";
import type {
  ReviewResultResponseDto,
  ReviewSubmitResultDto,
} from "@/types/api/review";
import type { VocabularyResponseDto } from "@/types/api/vocabulary";
import type { ReviewItemVm } from "@/types/view/ReviewItemVm";
import type { ReviewQueueVm, ReviewSubmitResultVm } from "@/types/view/ReviewQueueVm";

export type WebReviewSubmitResult =
  | {
      ok: true;
      item: ReviewSubmitResultVm;
      message: string;
    }
  | {
      ok: false;
      status: number;
      code: string;
      message: string;
    };

function normalizeLimit(limit: number | undefined): number {
  if (!Number.isFinite(limit)) {
    return 20;
  }

  return Math.min(Math.max(Math.trunc(limit ?? 20), 1), 100);
}

function reviewErrorState(
  status: number,
  message: string,
): Pick<ReviewQueueVm, "state" | "status" | "message"> {
  if (status === 0 || status >= 500) {
    return {
      state: "upstream_unavailable",
      status: status === 0 ? 503 : status,
      message: "复习服务暂时不可用，请稍后重试。",
    };
  }

  return {
    state: "error",
      status,
      message,
  };
}

function submitErrorCode(status: number): string {
  if (status === 0 || status >= 500) {
    return "upstream_unavailable";
  }
  if (status === 401) {
    return "upstream_auth_failed";
  }
  if (status === 404) {
    return "vocabulary_not_found";
  }
  if (status === 422) {
    return "invalid_review_result";
  }
  return "upstream_error";
}

function toReviewItem(dto: VocabularyResponseDto): ReviewItemVm {
  const review = dto.payload_json?.review;
  const reviewStage = typeof review?.stage === "number" ? review.stage : 0;
  const nextReviewAt =
    typeof review?.next_review_at === "string" ? review.next_review_at : undefined;
  const reviewLastReviewedAt =
    typeof review?.last_reviewed_at === "string" ? review.last_reviewed_at : undefined;

  return {
    id: dto.id,
    lemma: dto.lemma,
    displayWord: dto.display_word,
    phonetic: dto.phonetic ?? undefined,
    partOfSpeech: dto.part_of_speech ?? undefined,
    meaning: dto.short_meaning,
    sourceSentence: dto.source_sentence ?? undefined,
    sourceContext: dto.source_context ?? undefined,
    masteryStatus: dto.mastery_status,
    reviewStage,
    reviewCount: dto.review_count,
    nextReviewAt,
    lastReviewedAt: dto.last_reviewed_at ?? reviewLastReviewedAt,
    createdAt: dto.created_at,
  };
}

function toSubmitVm(dto: ReviewResultResponseDto): ReviewSubmitResultVm {
  return {
    vocabId: dto.vocab_id,
    lemma: dto.lemma,
    stage: dto.stage,
    nextReviewAt: dto.next_review_at ?? undefined,
    masteryStatus: dto.mastery_status,
    reviewCount: dto.review_count,
  };
}

export async function getReviewQueue(limit?: number): Promise<ReviewQueueVm> {
  const normalizedLimit = normalizeLimit(limit);
  const session = await getWebSession();

  if (session.kind === "anonymous") {
    return {
      state: "anonymous",
      items: [],
      total: 0,
      limit: normalizedLimit,
      status: 401,
      message: "当前会话已过期，请重新登录。",
    };
  }

  if (session.kind === "mock_phone") {
    return {
      state: "mock_session",
      items: [],
      total: 0,
      limit: normalizedLimit,
      status: 401,
      message: "当前登录态不能访问真实账户数据，请使用真实登录会话后查看复习队列。",
    };
  }

  const upstreamResult = await getUpstreamDueReviewVocabulary(
    session.sessionToken,
    normalizedLimit,
  );

  if (!upstreamResult.ok) {
    const error = reviewErrorState(upstreamResult.status, upstreamResult.message);

    return {
      state: error.state,
      items: [],
      total: 0,
      limit: normalizedLimit,
      status: error.status,
      message: error.message,
    };
  }

  const items = upstreamResult.data.items.map(toReviewItem);

  return {
    state: items.length > 0 ? "ready" : "empty",
    items,
    total: upstreamResult.data.total,
    limit: upstreamResult.data.limit,
  };
}

export async function submitReviewItem(
  vocabId: string,
  result: ReviewSubmitResultDto,
): Promise<WebReviewSubmitResult> {
  const normalizedId = vocabId.trim();

  if (!normalizedId) {
    return {
      ok: false,
      status: 400,
      code: "bad_request",
      message: "Missing vocabulary item id.",
    };
  }

  const session = await getWebSession();

  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    return {
      ok: false,
      status: 401,
      code: "auth_required",
      message:
        session.kind === "mock_phone"
          ? "当前登录态不能访问真实账户数据，请使用真实登录会话后提交复习。"
          : "请先登录后再提交真实复习。",
    };
  }

  const upstreamResult = await submitUpstreamVocabularyReview(
    normalizedId,
    result,
    session.sessionToken,
  );

  if (!upstreamResult.ok) {
    const unavailable = upstreamResult.status === 0 || upstreamResult.status >= 500;

    return {
      ok: false,
      status: upstreamResult.status === 0 ? 503 : upstreamResult.status,
      code: submitErrorCode(upstreamResult.status),
      message: unavailable
        ? "复习服务暂时不可用，请稍后重试。"
        : upstreamResult.message,
    };
  }

  return {
    ok: true,
    item: toSubmitVm(upstreamResult.data),
    message: "复习结果已提交。",
  };
}
