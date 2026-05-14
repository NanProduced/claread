import "server-only";

import { listVocabulary } from "@/services/api/vocabulary";
import { getWebSession, projectSession, type WebSession } from "@/services/bff/session";
import type { VocabularyResponseDto } from "@/types/api/vocabulary";
import type { VocabularyItemVm } from "@/types/view/VocabularyItemVm";

export type VocabularyBffStatus =
  | "ready"
  | "unauthenticated"
  | "mock_session"
  | "upstream_unavailable"
  | "upstream_error";

export interface VocabularyBffResult {
  status: VocabularyBffStatus;
  items: VocabularyItemVm[];
  total: number;
  page: number;
  limit: number;
  session: ReturnType<typeof projectSession>;
  message?: string;
}

export interface GetVocabularyOptions {
  page?: number;
  limit?: number;
}

function upstreamStatus(status: number): VocabularyBffStatus {
  return status === 0 ? "upstream_unavailable" : "upstream_error";
}

function unauthenticatedResult(
  session: WebSession,
  options: Required<GetVocabularyOptions>,
): VocabularyBffResult {
  return {
    status: session.kind === "mock_phone" ? "mock_session" : "unauthenticated",
    items: [],
    total: 0,
    page: options.page,
    limit: options.limit,
    session: projectSession(session),
    message:
      session.kind === "mock_phone"
        ? "当前登录态不能访问真实账户数据，请使用真实登录会话后查看生词本。"
        : "请先登录后查看生词本。",
  };
}

function lookupKindFromWord(word: string): VocabularyItemVm["lookupKind"] {
  return /\s/.test(word.trim()) ? "phrase" : "word";
}

function firstSourceRecordId(item: VocabularyResponseDto): string | undefined {
  const firstRef = item.payload_json?.source_refs?.[0];
  return firstRef?.cloud_record_id ?? firstRef?.client_record_id;
}

function projectVocabularyItem(item: VocabularyResponseDto): VocabularyItemVm {
  const word = item.display_word || item.lemma;

  return {
    id: item.id,
    word,
    lemma: item.lemma,
    lookupKind: lookupKindFromWord(word),
    phonetic: item.phonetic ?? undefined,
    partOfSpeech: item.part_of_speech ?? undefined,
    shortMeaning: item.short_meaning,
    contextSentence: item.source_sentence ?? undefined,
    contextTranslation: item.source_context ?? undefined,
    sourceRecordId: firstSourceRecordId(item),
    createdAt: item.created_at,
    updatedAt: item.updated_at,
    mastered: item.mastery_status === "mastered",
    masteryStatus: item.mastery_status,
    reviewCount: item.review_count,
    tags: item.tags,
  };
}

export async function getVocabularyList(
  options: GetVocabularyOptions = {},
): Promise<VocabularyBffResult> {
  const normalizedOptions = {
    page: options.page ?? 1,
    limit: options.limit ?? 50,
  };
  const session = await getWebSession();

  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    return unauthenticatedResult(session, normalizedOptions);
  }

  const upstreamResult = await listVocabulary(session.sessionToken, {
    ...normalizedOptions,
    lite: false,
  });

  if (!upstreamResult.ok) {
    return {
      status: upstreamStatus(upstreamResult.status),
      items: [],
      total: 0,
      page: normalizedOptions.page,
      limit: normalizedOptions.limit,
      session: projectSession(session),
      message:
        upstreamResult.status === 0 || upstreamResult.status >= 500
          ? "生词本服务暂时不可用，请稍后重试。"
          : upstreamResult.message,
    };
  }

  return {
    status: "ready",
    items: upstreamResult.data.items.map(projectVocabularyItem),
    total: upstreamResult.data.total,
    page: upstreamResult.data.page,
    limit: upstreamResult.data.limit,
    session: projectSession(session),
  };
}
