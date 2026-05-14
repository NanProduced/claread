import "server-only";

import { submitUpstreamFeedback } from "@/services/api/feedback";
import { getWebSession } from "@/services/bff/session";
import type {
  FeedbackCreateRequestDto,
  FeedbackResponseDto,
  FeedbackScopeDto,
  FeedbackSentimentDto,
  FeedbackTypeDto,
} from "@/types/api/feedback";

const FEEDBACK_TYPES_BY_SCOPE: Record<
  FeedbackScopeDto,
  Partial<Record<FeedbackSentimentDto, FeedbackTypeDto[]>>
> = {
  analysis_result: {
    positive: ["thumbs_up"],
    negative: [
      "translation_inaccurate",
      "too_few_annotations",
      "too_many_annotations",
      "wrong_difficulty",
      "other",
    ],
  },
  annotation: {
    positive: ["helpful"],
    negative: [
      "wrong_label",
      "inaccurate",
      "wrong_boundary",
      "should_not_annotate",
      "other",
    ],
  },
  sentence: {
    negative: [
      "translation_inaccurate",
      "sentence_analysis_wrong",
      "annotation_conflict",
      "selection_issue",
      "other",
    ],
  },
  dictionary: {
    negative: [
      "wrong_definition",
      "missing_definition",
      "wrong_pos",
      "wrong_phonetic",
      "bad_example",
      "other",
    ],
  },
  app: {
    neutral: [
      "bug_report",
      "feature_request",
      "quota_issue",
      "input_page_issue",
      "ux_issue",
      "other",
    ],
  },
};

export type WebFeedbackSubmitInput = {
  feedbackScope?: unknown;
  targetId?: unknown;
  analysisRecordId?: unknown;
  sentiment?: unknown;
  feedbackType?: unknown;
  annotationType?: unknown;
  content?: unknown;
  contextJson?: unknown;
  appVersion?: unknown;
};

export type WebFeedbackSubmitResult =
  | {
      ok: true;
      feedback: {
        id: string;
        feedbackScope: FeedbackScopeDto;
        targetId: string;
        sentiment: FeedbackSentimentDto;
        feedbackType: FeedbackTypeDto;
        status: string;
        createdAt: string;
      };
      message: string;
    }
  | {
      ok: false;
      status: number;
      code: string;
      message: string;
    };

function readString(value: unknown): string | undefined {
  return typeof value === "string" ? value.trim() : undefined;
}

function isFeedbackScope(value: unknown): value is FeedbackScopeDto {
  return (
    value === "analysis_result" ||
    value === "annotation" ||
    value === "sentence" ||
    value === "dictionary" ||
    value === "app"
  );
}

function isFeedbackSentiment(value: unknown): value is FeedbackSentimentDto {
  return value === "positive" || value === "negative" || value === "neutral";
}

function isObjectRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function invalid(message: string): WebFeedbackSubmitResult {
  return {
    ok: false,
    status: 400,
    code: "bad_request",
    message,
  };
}

function toFeedbackVm(dto: FeedbackResponseDto): Extract<WebFeedbackSubmitResult, { ok: true }> {
  return {
    ok: true,
    feedback: {
      id: dto.id,
      feedbackScope: dto.feedback_scope,
      targetId: dto.target_id,
      sentiment: dto.sentiment,
      feedbackType: dto.feedback_type,
      status: dto.status,
      createdAt: dto.created_at,
    },
    message: "反馈已提交。",
  };
}

function upstreamErrorCode(status: number): string {
  if (status === 0 || status >= 500) {
    return "upstream_unavailable";
  }
  if (status === 401) {
    return "upstream_auth_failed";
  }
  if (status === 422) {
    return "invalid_feedback";
  }
  return "upstream_error";
}

function buildPayload(input: WebFeedbackSubmitInput): FeedbackCreateRequestDto | WebFeedbackSubmitResult {
  const feedbackScope = input.feedbackScope;
  const sentiment = input.sentiment;
  const feedbackType = input.feedbackType;
  const targetId = readString(input.targetId);

  if (!isFeedbackScope(feedbackScope)) {
    return invalid("Invalid feedback scope.");
  }
  if (!targetId) {
    return invalid("Missing feedback target.");
  }
  if (!isFeedbackSentiment(sentiment)) {
    return invalid("Invalid feedback sentiment.");
  }
  if (typeof feedbackType !== "string" || feedbackType.trim().length === 0) {
    return invalid("Invalid feedback type.");
  }

  const normalizedType = feedbackType.trim() as FeedbackTypeDto;
  const validTypes = FEEDBACK_TYPES_BY_SCOPE[feedbackScope][sentiment] ?? [];

  if (!validTypes.includes(normalizedType)) {
    return invalid("Feedback type does not match scope and sentiment.");
  }

  const analysisRecordId = readString(input.analysisRecordId);
  const annotationType = readString(input.annotationType);

  if ((feedbackScope === "analysis_result" || feedbackScope === "annotation") && !analysisRecordId) {
    return invalid("Missing analysis record id for this feedback scope.");
  }
  if (feedbackScope === "annotation" && !annotationType) {
    return invalid("Missing annotation type for annotation feedback.");
  }

  return {
    feedback_scope: feedbackScope,
    target_id: targetId,
    analysis_record_id: analysisRecordId ?? null,
    sentiment,
    feedback_type: normalizedType,
    annotation_type: annotationType ?? null,
    content: readString(input.content) ?? null,
    context_json: isObjectRecord(input.contextJson) ? input.contextJson : {},
    app_version: readString(input.appVersion) ?? "web",
  };
}

export async function submitFeedbackFromWeb(
  input: WebFeedbackSubmitInput,
): Promise<WebFeedbackSubmitResult> {
  const payload = buildPayload(input);

  if ("ok" in payload) {
    return payload;
  }

  const session = await getWebSession();

  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    return {
      ok: false,
      status: 401,
      code: "auth_required",
      message:
        session.kind === "mock_phone"
          ? "当前登录态不能提交真实反馈，请使用真实登录会话后再试。"
          : "请先登录后再提交反馈。",
    };
  }

  const upstreamResult = await submitUpstreamFeedback(payload, session.sessionToken);

  if (!upstreamResult.ok) {
    const unavailable = upstreamResult.status === 0 || upstreamResult.status >= 500;

    return {
      ok: false,
      status: upstreamResult.status === 0 ? 503 : upstreamResult.status,
      code: upstreamErrorCode(upstreamResult.status),
      message: unavailable ? "反馈服务暂时不可用，请稍后重试。" : upstreamResult.message,
    };
  }

  return toFeedbackVm(upstreamResult.data);
}
