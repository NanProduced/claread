import "server-only";

import { createVocabulary, deleteVocabulary, listVocabulary, patchVocabulary } from "@/services/api/vocabulary";
import { getUpstreamDueReviewVocabulary } from "@/services/api/review";
import { getWebSession, projectSession, type WebSession } from "@/services/bff/session";
import type {
  ReaderVocabularyLookupMatchDto,
  VocabularyCreateRequestDto,
  VocabularyMasteryStatusDto,
  VocabularyResponseDto,
  VocabularySourceRefDto,
  VocabularyUpsertResponseDto,
} from "@/types/api/vocabulary";
import type {
  DetailExample,
  DetailMeaning,
  DetailPhrase,
} from "@/types/view/VocabularyItemVm";
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
  dueCount: number;
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
    dueCount: 0,
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

function firstSourceReadingRecordId(item: VocabularyResponseDto): string | undefined {
  return item.payload_json?.source_refs?.[0]?.reading_record_id ?? undefined;
}

function parseDetailMeanings(value: unknown): DetailMeaning[] | undefined {
  if (!Array.isArray(value)) return undefined

  const result = value
    .filter(isRecord)
    .map((m) => {
      const partOfSpeech =
        (typeof m.partOfSpeech === "string" ? m.partOfSpeech : "") ||
        (typeof m.part_of_speech === "string" ? m.part_of_speech : "")

      const definitions = Array.isArray(m.definitions)
        ? m.definitions
            .map((d: unknown) => {
              if (typeof d === "string") return { meaning: d }
              if (!isRecord(d)) return null
              const def: { meaning: string; example?: string; exampleTranslation?: string } = {
                meaning: typeof d.meaning === "string" ? d.meaning : "",
              }
              if (typeof d.example === "string") def.example = d.example
              if (typeof d.exampleTranslation === "string")
                def.exampleTranslation = d.exampleTranslation
              return def
            })
            .filter(
              (d): d is { meaning: string; example?: string; exampleTranslation?: string } =>
                d !== null,
            )
        : []

      return { partOfSpeech, definitions }
    })
    .filter((m) => m.definitions.length > 0)

  return result.length > 0 ? result : undefined
}

function parseDetailPhrases(value: unknown): DetailPhrase[] | undefined {
  if (!Array.isArray(value)) return undefined

  const result = value.filter(isRecord).map((p) => ({
    phrase: typeof p.phrase === "string" ? p.phrase : "",
    meaning: typeof p.meaning === "string" ? p.meaning : undefined,
  }))

  return result.length > 0 ? result : undefined
}

function parseDetailExamples(value: unknown): DetailExample[] | undefined {
  if (!Array.isArray(value)) return undefined

  const result = value.filter(isRecord).map((e) => ({
    example: typeof e.example === "string" ? e.example : "",
    exampleTranslation:
      typeof e.exampleTranslation === "string" ? e.exampleTranslation : undefined,
  }))

  return result.length > 0 ? result : undefined
}

function projectVocabularyItem(item: VocabularyResponseDto): VocabularyItemVm {
  const word = item.display_word || item.lemma
  const payload = item.payload_json ?? {}
  const review = payload.review

  const sourceRefs = readSourceRefs(payload.source_refs)
  const collectedForms = readStringArray(payload.collected_forms)

  const articleIds = new Set<string>()
  for (const ref of sourceRefs) {
    if (ref.cloud_record_id) articleIds.add(ref.cloud_record_id)
    if (ref.client_record_id) articleIds.add(ref.client_record_id)
  }

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
    sourceReadingRecordId: firstSourceReadingRecordId(item),
    createdAt: item.created_at,
    updatedAt: item.updated_at,
    mastered: item.mastery_status === "mastered",
    masteryStatus: item.mastery_status,
    reviewCount: item.review_count,
    tags: item.tags,
    nextReviewAt: review?.next_review_at ?? undefined,
    reviewStage: review?.stage ?? undefined,
    lastReviewedAt: item.last_reviewed_at ?? review?.last_reviewed_at ?? undefined,
    sourceRefs,
    collectedForms,
    dictEntryId: item.dict_entry_id,
    audioUrl: typeof payload.audio_url === "string" ? payload.audio_url : undefined,
    detailMeanings: parseDetailMeanings(item.meanings_json),
    detailPhrases: parseDetailPhrases(payload.detail_phrases),
    detailExamples: parseDetailExamples(payload.detail_examples),
    totalSourceCount: sourceRefs.length,
    totalSourceArticleCount: articleIds.size,
  }
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
    reading_record_id: readNullableString(ref.reading_record_id),
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
      message: "生词请求格式不正确。",
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
      message: "生词信息不完整，请从词典词条重新保存。",
    };
  }

  if (dictEntryId === null || Number.isNaN(dictEntryId)) {
    return {
      ok: false,
      status: 400,
      code: "bad_request",
      message: "缺少有效的词典词条，请从词典词条重新保存。",
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
  void options;
  const PAGE_LIMIT = 100
  const MAX_PAGES = 20
  const session = await getWebSession();

  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    return unauthenticatedResult(session, { page: 1, limit: PAGE_LIMIT });
  }

  let allItems: VocabularyResponseDto[] = []
  let page = 1
  let total = 0

  while (page <= MAX_PAGES) {
    const upstreamResult = await listVocabulary(session.sessionToken, {
      page,
      limit: PAGE_LIMIT,
      lite: false,
    })

    if (!upstreamResult.ok) {
      if (allItems.length > 0) {
        break
      }
      return {
        status: upstreamStatus(upstreamResult.status),
        items: [],
        total: 0,
        page: 1,
        limit: PAGE_LIMIT,
        dueCount: 0,
        session: projectSession(session),
        message:
          upstreamResult.status === 0 || upstreamResult.status >= 500
            ? "生词本服务暂时不可用，请稍后重试。"
            : "生词本读取失败，请稍后重试。",
      }
    }

    total = upstreamResult.data.total
    allItems = allItems.concat(upstreamResult.data.items)

    if (upstreamResult.data.items.length < PAGE_LIMIT || allItems.length >= total) {
      break
    }

    page++
  }

  const projectedItems = allItems.map(projectVocabularyItem)

  let dueCount = 0
  try {
    const dueResult = await getUpstreamDueReviewVocabulary(session.sessionToken, 1)
    if (dueResult.ok) {
      dueCount = dueResult.data.total
    } else {
      const now = Date.now()
      dueCount = projectedItems.filter(
        (item: VocabularyItemVm) => item.nextReviewAt && new Date(item.nextReviewAt).getTime() <= now,
      ).length
    }
  } catch {
    const now = Date.now()
    dueCount = projectedItems.filter(
      (item: VocabularyItemVm) => item.nextReviewAt && new Date(item.nextReviewAt).getTime() <= now,
    ).length
  }

  return {
    status: "ready",
    items: projectedItems,
    total,
    page: 1,
    limit: PAGE_LIMIT,
    dueCount,
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

  const MAX_PAGES = 20;
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
  };
}

export async function updateVocabularyFromWeb(
  vocabId: string,
  body: { mastery_status?: VocabularyMasteryStatusDto },
): Promise<{ ok: true; item: VocabularyItemVm } | { ok: false; status: number; code: string; message: string }> {
  const session = await getWebSession();

  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    return {
      ok: false,
      status: 401,
      code: "auth_required",
      message:
        session.kind === "mock_phone"
          ? "当前登录态不能修改真实生词本，请使用真实登录会话后再试。"
          : "请先登录后修改生词本。",
    };
  }

  const upstreamResult = await patchVocabulary(session.sessionToken, vocabId, body);

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
          ? "生词本更新服务暂时不可用，请稍后重试。"
          : upstreamResult.message,
    };
  }

  return {
    ok: true,
    item: projectVocabularyItem(upstreamResult.data),
  };
}

export async function deleteVocabularyFromWeb(
  vocabId: string,
): Promise<{ ok: true; deleted: boolean } | { ok: false; status: number; code: string; message: string }> {
  const session = await getWebSession();

  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    return {
      ok: false,
      status: 401,
      code: "auth_required",
      message:
        session.kind === "mock_phone"
          ? "当前登录态不能删除真实生词本条目，请使用真实登录会话后再试。"
          : "请先登录后删除生词本条目。",
    };
  }

  const upstreamResult = await deleteVocabulary(session.sessionToken, vocabId);

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
          ? "生词本删除服务暂时不可用，请稍后重试。"
          : upstreamResult.message,
    };
  }

  return {
    ok: true,
    deleted: upstreamResult.data.deleted,
  };
}
