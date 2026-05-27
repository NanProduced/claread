import "server-only";

import { createVocabulary, listVocabulary } from "@/services/api/vocabulary";
import { getWebSession, projectSession, type WebSession } from "@/services/bff/session";
import type {
  ReaderVocabularyLookupMatchDto,
  VocabularyCreateRequestDto,
  VocabularyResponseDto,
  VocabularySourceRefDto,
  VocabularyUpsertResponseDto,
} from "@/types/api/vocabulary";
import type { VocabularyItemVm } from "@/types/view/VocabularyItemVm";

export type VocabularyBffStatus =
  | "ready"
  | "unauthenticated"
  | "limited_debug"
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

export interface VocabularyLookupMatchQuery {
  dictEntryId?: number | null;
  lemma?: string | null;
  form?: string | null;
}

export type AddVocabularyResult =
  | {
      ok: true;
      status: 200;
      code: "ready";
      data: VocabularyUpsertResponseDto;
      message: string;
    }
  | {
      ok: false;
      status: number;
      code: "bad_request" | "auth_required" | "upstream_unavailable" | "upstream_error";
      message: string;
    };

export type VocabularyLookupMatchResult =
  | {
      ok: true;
      status: 200;
      item: ReaderVocabularyLookupMatchDto | null;
      truncated?: boolean;
    }
  | {
      ok: false;
      status: number;
      code: "bad_request" | "auth_required" | "upstream_unavailable" | "upstream_error";
      message: string;
    };

type IncomingVocabularyBody = Partial<VocabularyCreateRequestDto>;

function upstreamStatus(status: number): VocabularyBffStatus {
  return status === 0 ? "upstream_unavailable" : "upstream_error";
}

function normalizeLookupValue(value?: string | null) {
  return value?.trim().toLowerCase() ?? "";
}

function unauthenticatedResult(
  session: WebSession,
  options: Required<GetVocabularyOptions>,
): VocabularyBffResult {
  return {
    status: session.kind === "mock_phone" ? "limited_debug" : "unauthenticated",
    items: [],
    total: 0,
    page: options.page,
    limit: options.limit,
    session: projectSession(session),
    message:
      session.kind === "mock_phone"
        ? "当前登录态不能访问真实账户数据，请使用真实登录会话后查看生词本。"
        : "当前会话已过期，请重新登录。",
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

function projectLookupMatch(item: VocabularyResponseDto): ReaderVocabularyLookupMatchDto {
  return {
    id: item.id,
    lemma: item.lemma,
    display_word: item.display_word,
    dict_entry_id: item.dict_entry_id,
    mastery_status: item.mastery_status,
    source_refs: item.payload_json?.source_refs ?? [],
    collected_forms: item.payload_json?.collected_forms ?? [],
  };
}

function matchesVocabularyLookup(
  item: VocabularyResponseDto,
  query: Required<VocabularyLookupMatchQuery>,
): boolean {
  if (query.dictEntryId !== null && item.dict_entry_id === query.dictEntryId) {
    return true;
  }

  const lemma = normalizeLookupValue(item.lemma);
  if (query.lemma && lemma === query.lemma) {
    return true;
  }

  if (!query.form) {
    return false;
  }

  const candidateForms = [
    item.display_word,
    item.lemma,
    ...(item.payload_json?.collected_forms ?? []),
  ].map((value) => normalizeLookupValue(value));

  return candidateForms.includes(query.form);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function readString(value: unknown): string | undefined {
  const trimmed = typeof value === "string" ? value.trim() : "";
  return trimmed ? trimmed : undefined;
}

function readNullableString(value: unknown): string | null {
  return readString(value) ?? null;
}

function readStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map(readString).filter((item): item is string => Boolean(item))
    : [];
}

function readDictEntryId(value: unknown): number | null {
  if (value === null || value === undefined || value === "") {
    return null;
  }

  return typeof value === "number" && Number.isSafeInteger(value) && value > 0 ? value : Number.NaN;
}

function readSourceRefs(value: unknown): VocabularySourceRefDto[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value.filter(isRecord).map((ref) => ({
    client_record_id: readString(ref.client_record_id) ?? "",
    cloud_record_id: readNullableString(ref.cloud_record_id),
    source_sentence: readNullableString(ref.source_sentence),
    source_context: readNullableString(ref.source_context),
    source_sentence_id: readNullableString(ref.source_sentence_id),
    source_anchor_text: readNullableString(ref.source_anchor_text),
    source_occurrence:
      typeof ref.source_occurrence === "number" && Number.isSafeInteger(ref.source_occurrence)
        ? ref.source_occurrence
        : null,
    collected_at: readNullableString(ref.collected_at),
  }));
}

function normalizeCreateBody(body: unknown): VocabularyCreateRequestDto | AddVocabularyResult {
  if (!isRecord(body)) {
    return {
      ok: false,
      status: 400,
      code: "bad_request",
      message: "Invalid vocabulary request body.",
    };
  }

  const incoming = body as IncomingVocabularyBody;
  const lemma = readString(incoming.lemma);
  const displayWord = readString(incoming.display_word);
  const shortMeaning = readString(incoming.short_meaning);
  const sourceSentence = readString(incoming.source_sentence);
  const sourceProvider = readString(incoming.source_provider);
  const dictEntryId = readDictEntryId(incoming.dict_entry_id);
  const payload = isRecord(incoming.payload_json) ? incoming.payload_json : {};
  const sourceRefs = readSourceRefs(payload.source_refs);

  if (!lemma || !displayWord || !shortMeaning || !sourceSentence || !sourceProvider) {
    return {
      ok: false,
      status: 400,
      code: "bad_request",
      message:
        "Missing required vocabulary fields: lemma, display_word, short_meaning, source_sentence, source_provider.",
    };
  }

  if (dictEntryId === null || Number.isNaN(dictEntryId)) {
    return {
      ok: false,
      status: 400,
      code: "bad_request",
      message: "Invalid dict_entry_id. Use a positive integer from a dictionary entry.",
    };
  }

  if (sourceRefs.length === 0) {
    return {
      ok: false,
      status: 400,
      code: "bad_request",
      message: "Missing payload_json.source_refs.",
    };
  }

  return {
    lemma,
    display_word: displayWord,
    phonetic: readNullableString(incoming.phonetic),
    part_of_speech: readNullableString(incoming.part_of_speech),
    short_meaning: shortMeaning,
    meanings_json: Array.isArray(incoming.meanings_json) ? incoming.meanings_json : [],
    tags: readStringArray(incoming.tags),
    exchange: readStringArray(incoming.exchange),
    source_provider: sourceProvider,
    dict_entry_id: dictEntryId,
    source_sentence: sourceSentence,
    source_context: readNullableString(incoming.source_context),
    payload_json: {
      ...payload,
      source_refs: sourceRefs,
      collected_forms: readStringArray(payload.collected_forms),
    },
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

export async function addVocabularyFromWeb(body: unknown): Promise<AddVocabularyResult> {
  const normalizedBody = normalizeCreateBody(body);

  if ("ok" in normalizedBody) {
    return normalizedBody;
  }

  const session = await getWebSession();

  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    return {
      ok: false,
      status: 401,
      code: "auth_required",
      message:
        session.kind === "mock_phone"
          ? "当前登录态不能写入真实生词本，请使用真实登录会话后再试。"
          : "请先登录后加入生词本。",
    };
  }

  const upstreamResult = await createVocabulary(session.sessionToken, normalizedBody);

  if (!upstreamResult.ok) {
    const status = upstreamResult.status === 0 ? 503 : upstreamResult.status;

    return {
      ok: false,
      status,
      code: upstreamResult.status === 0 || upstreamResult.status >= 500
        ? "upstream_unavailable"
        : "upstream_error",
      message:
        upstreamResult.status === 0 || upstreamResult.status >= 500
          ? "生词本写入服务暂时不可用，请稍后重试。"
          : upstreamResult.message,
    };
  }

  return {
    ok: true,
    status: 200,
    code: "ready",
    data: upstreamResult.data,
    message: upstreamResult.data.created ? "已加入生词本。" : "已更新生词本来源。",
  };
}

export async function getVocabularyLookupMatch(
  query: VocabularyLookupMatchQuery,
): Promise<VocabularyLookupMatchResult> {
  const normalizedQuery = {
    dictEntryId:
      typeof query.dictEntryId === "number" && Number.isSafeInteger(query.dictEntryId) && query.dictEntryId > 0
        ? query.dictEntryId
        : null,
    lemma: normalizeLookupValue(query.lemma),
    form: normalizeLookupValue(query.form),
  };

  if (!normalizedQuery.dictEntryId && !normalizedQuery.lemma && !normalizedQuery.form) {
    return {
      ok: false,
      status: 400,
      code: "bad_request",
      message: "至少提供一个词汇匹配条件。",
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
          ? "当前登录态不能访问真实生词本状态，请使用真实登录会话后重试。"
          : "请先登录后查看生词本状态。",
    };
  }

  // Hard cap: scan at most 5 pages (500 items). This prevents unbounded
  // sequential upstream requests when a user has a large vocabulary.
  // TODO: Replace with a direct upstream query by dict_entry_id / lemma
  // once the FastAPI /vocabulary endpoint supports filtered lookups.
  const MAX_PAGES = 5;
  const limit = 100;
  let page = 1;
  let total = 0;

  while (page <= MAX_PAGES && (page === 1 || (page - 1) * limit < total)) {
    const upstreamResult = await listVocabulary(session.sessionToken, {
      page,
      limit,
      lite: false,
    });

    if (!upstreamResult.ok) {
      return {
        ok: false,
        status: upstreamResult.status === 0 ? 503 : upstreamResult.status,
        code:
          upstreamResult.status === 0 || upstreamResult.status >= 500
            ? "upstream_unavailable"
            : "upstream_error",
        message:
          upstreamResult.status === 0 || upstreamResult.status >= 500
            ? "生词本状态读取暂时不可用，请稍后重试。"
            : upstreamResult.message,
      };
    }

    total = upstreamResult.data.total;

    const matched = upstreamResult.data.items.find((item) => matchesVocabularyLookup(item, normalizedQuery));
    if (matched) {
      return {
        ok: true,
        status: 200,
        item: projectLookupMatch(matched),
      };
    }

    if (upstreamResult.data.items.length < limit) {
      break;
    }

    page += 1;
  }

  return {
    ok: true,
    status: 200,
    item: null,
    truncated: total > MAX_PAGES * limit,
  };
}
