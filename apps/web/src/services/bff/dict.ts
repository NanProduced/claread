import "server-only";

import {
  lookupUpstreamDict,
  lookupUpstreamDictAI,
  lookupUpstreamDictEntry,
  type LookupDictParams,
} from "@/services/api/dict";
import { getWebSession } from "@/services/bff/session";
import type {
  DictDisambiguationResultDto,
  DictEntryPayloadDto,
  DictLookupTypeDto,
  DictResponseDto,
  WebDictCandidate,
  WebDictEntry,
  WebDictErrorResult,
  WebDictResult,
} from "@/types/api/dict";
import type {
  DictAIEntryPayloadDto,
  DictAIResponseDto,
  DictAISourceDto,
  WebDictAIEntry,
  WebDictAIErrorResult,
  WebDictAIRequest,
  WebDictAIResult,
} from "@/types/api/dict-ai";

export interface WebDictLookupParams {
  query: string;
  type?: DictLookupTypeDto;
  context?: string;
  sentenceId?: string;
  occurrence?: number;
}

function isLookupType(value: string | null): value is DictLookupTypeDto {
  return value === "word" || value === "phrase";
}

function isDictAISource(value: string | null): value is DictAISourceDto {
  return value === "reader_click" || value === "selection" || value === "manual_search";
}

function normalizeNullableString(value: string | null | undefined): string | undefined {
  const trimmed = value?.trim();
  return trimmed ? trimmed : undefined;
}

function readPayloadString(payload: unknown, key: string): string | undefined {
  if (!payload || typeof payload !== "object") {
    return undefined;
  }

  const value = (payload as Record<string, unknown>)[key];
  return typeof value === "string" ? value : undefined;
}

function readPayloadNumber(payload: unknown, key: string): number | undefined {
  if (!payload || typeof payload !== "object") {
    return undefined;
  }

  const value = (payload as Record<string, unknown>)[key];
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function toWebEntry(entry: DictEntryPayloadDto): WebDictEntry {
  return {
    id: entry.id,
    word: entry.word,
    baseWord: entry.base_word ?? undefined,
    homographNo: entry.homograph_no ?? undefined,
    phonetic: entry.phonetic ?? undefined,
    meanings: entry.meanings.map((meaning) => ({
      partOfSpeech: meaning.part_of_speech,
      definitions: meaning.definitions.map((definition) => ({
        meaning: definition.meaning,
        example: definition.example ?? undefined,
        exampleTranslation: definition.example_translation ?? undefined,
      })),
    })),
    examples: entry.examples.map((example) => ({
      example: example.example,
      exampleTranslation: example.example_translation ?? undefined,
    })),
    phrases: entry.phrases.map((phrase) => ({
      phrase: phrase.phrase,
      meaning: phrase.meaning ?? undefined,
    })),
    entryKind: entry.entry_kind,
    exchange: entry.exchange ?? [],
    tags: entry.tags ?? [],
  };
}

function toWebAIEntry(entry: DictAIEntryPayloadDto): WebDictAIEntry {
  return {
    word: entry.word,
    baseWord: entry.base_word ?? undefined,
    phonetic: entry.phonetic ?? undefined,
    meanings: entry.meanings.map((meaning) => ({
      partOfSpeech: meaning.part_of_speech,
      definitions: meaning.definitions.map((definition) => ({
        meaning: definition.meaning,
        example: definition.example ?? undefined,
        exampleTranslation: definition.example_translation ?? undefined,
      })),
    })),
    examples: entry.examples.map((example) => ({
      example: example.example,
      exampleTranslation: example.example_translation ?? undefined,
    })),
    phrases: entry.phrases.map((phrase) => ({
      phrase: phrase.phrase,
      meaning: phrase.meaning ?? undefined,
    })),
    entryKind: entry.entry_kind ?? "entry",
    exchange: entry.exchange ?? [],
    tags: entry.tags ?? [],
  };
}

function toWebCandidate(candidate: DictDisambiguationResultDto["candidates"][number]): WebDictCandidate {
  return {
    entryId: candidate.entry_id,
    label: candidate.label,
    partOfSpeech: candidate.part_of_speech ?? undefined,
    preview: candidate.preview ?? undefined,
    entryKind: candidate.entry_kind,
    matchKind: candidate.match_kind ?? "headword",
    lookupType: candidate.lookup_type ?? "word",
    candidateKind: candidate.candidate_kind ?? "word",
  };
}

function projectUpstreamResult(dto: DictResponseDto): WebDictResult {
  if (dto.result_type === "entry") {
    return {
      kind: "entry",
      query: dto.query,
      provider: dto.provider,
      cached: dto.cached,
      entry: toWebEntry(dto.entry),
    };
  }

  if (dto.result_type === "disambiguation") {
    return {
      kind: "disambiguation",
      query: dto.query,
      provider: dto.provider,
      cached: dto.cached,
      ambiguityKind: dto.ambiguity_kind ?? "competing_entries",
      selectionRequired: dto.selection_required ?? true,
      candidates: dto.candidates.map(toWebCandidate),
    };
  }

  return {
    kind: "not_found",
    query: dto.query,
    provider: dto.provider,
    cached: dto.cached,
    reason: dto.reason,
  };
}

function projectDictAIResult(dto: DictAIResponseDto): WebDictAIResult {
  if (dto.mode === "context_explain") {
    return {
      kind: "context_explain",
      mode: "context_explain",
      query: dto.query,
      summary: dto.summary,
      bestFitSense: dto.best_fit_sense ?? undefined,
      whyHere: dto.why_here ?? undefined,
      cue: dto.cue ?? undefined,
      translation: dto.translation ?? undefined,
      contrast: dto.contrast ?? undefined,
      learningTip: dto.learning_tip ?? undefined,
      confidence: dto.confidence ?? undefined,
    };
  }

  if (dto.result_kind === "ai_entry") {
    return {
      kind: "ai_entry",
      resultKind: "ai_entry",
      mode: "missing_fallback",
      query: dto.query,
      classification: dto.classification,
      summary: dto.summary,
      confidence: dto.confidence ?? undefined,
      verified: dto.verified,
      source: dto.source,
      suggestedQuery: dto.suggested_query,
      entry: toWebAIEntry(dto.entry),
    };
  }

  return {
    kind: "ai_unresolved",
    resultKind: "ai_unresolved",
    mode: "missing_fallback",
    query: dto.query,
    classification: dto.classification,
    summary: dto.summary,
    confidence: dto.confidence ?? undefined,
    verified: dto.verified,
    source: dto.source,
    suggestedQuery: dto.suggested_query,
    reason: dto.reason ?? undefined,
  };
}

function upstreamError(query: string, status: number, message: string): WebDictErrorResult {
  return {
    kind: "error",
    query,
    status: status === 0 ? 503 : status,
    code: status === 0 || status >= 500 ? "upstream_unavailable" : "upstream_error",
    message:
      status === 0 || status >= 500
        ? "Dictionary upstream is unavailable. No mock dictionary fallback was used."
        : message,
  };
}

function dictAIUpstreamError(
  body: WebDictAIRequest,
  status: number,
  message: string,
  payload?: unknown,
): WebDictAIErrorResult {
  const normalizedStatus = status === 0 ? 503 : status;

  if (status === 401) {
    return {
      kind: "error",
      query: body.query,
      mode: body.mode,
      status: 401,
      code: "auth_required",
      message: "请先登录真实账户后，再使用 AI 查词。",
    };
  }

  if (status === 402) {
    const remainingPoints = readPayloadNumber(payload, "remaining_points") ?? 0;
    const requiredPoints = readPayloadNumber(payload, "required_points") ?? 5;
    return {
      kind: "error",
      query: body.query,
      mode: body.mode,
      status: 402,
      code: "insufficient_credits",
      message: "当前可用点数不足，暂时无法使用 AI 查词。",
      remainingPoints,
      requiredPoints,
    };
  }

  if (status === 404) {
    return {
      kind: "error",
      query: body.query,
      mode: body.mode,
      status: 404,
      code: "entry_not_found",
      message: "当前词条已失效，请重新点词后再试。",
    };
  }

  if (status === 409) {
    const errorCode = readPayloadString(payload, "error");
    if (errorCode === "CANONICAL_DICTIONARY_AVAILABLE") {
      return {
        kind: "error",
        query: body.query,
        mode: body.mode,
        status: 409,
        code: "canonical_dictionary_available",
        message: "当前词条已可由正式词典解析，正在刷新词典结果。",
      };
    }
    if (errorCode === "ENTRY_QUERY_MISMATCH") {
      return {
        kind: "error",
        query: body.query,
        mode: body.mode,
        status: 409,
        code: "entry_query_mismatch",
        message: "当前词形和已选词条不再匹配，请重新点词。",
      };
    }
  }

  if (status === 0 || status >= 500) {
    return {
      kind: "error",
      query: body.query,
      mode: body.mode,
      status: normalizedStatus,
      code: "upstream_unavailable",
      message: "AI 查词暂时不可用，请稍后再试。",
    };
  }

  return {
    kind: "error",
    query: body.query,
    mode: body.mode,
    status: normalizedStatus,
    code: "upstream_error",
    message,
  };
}

export function parseLookupSearchParams(searchParams: URLSearchParams): WebDictLookupParams | WebDictErrorResult {
  const query = normalizeNullableString(
    searchParams.get("word") ?? searchParams.get("query") ?? searchParams.get("q"),
  );

  if (!query) {
    return {
      kind: "error",
      query: "",
      status: 400,
      code: "bad_request",
      message: "Missing dictionary lookup query. Use word, query, or q.",
    };
  }

  const rawType = searchParams.get("type") ?? searchParams.get("lookupType");
  const rawOccurrence = searchParams.get("occurrence");
  const occurrence = rawOccurrence === null ? undefined : Number.parseInt(rawOccurrence, 10);

  if (rawType !== null && !isLookupType(rawType)) {
    return {
      kind: "error",
      query,
      status: 400,
      code: "bad_request",
      message: "Invalid dictionary lookup type. Use word or phrase.",
    };
  }

  if (rawOccurrence !== null && !Number.isSafeInteger(occurrence)) {
    return {
      kind: "error",
      query,
      status: 400,
      code: "bad_request",
      message: "Invalid occurrence. Use an integer.",
    };
  }

  return {
    query,
    type: rawType ?? undefined,
    context: normalizeNullableString(
      searchParams.get("context") ??
        searchParams.get("contextSentence") ??
        searchParams.get("context_sentence"),
    ),
    sentenceId: normalizeNullableString(searchParams.get("sentenceId") ?? searchParams.get("sentence_id")),
    occurrence,
  };
}

export function parseEntrySearchParams(searchParams: URLSearchParams): number | WebDictErrorResult {
  const rawId = searchParams.get("id") ?? searchParams.get("entryId") ?? searchParams.get("entry_id");
  const entryId = rawId === null ? Number.NaN : Number.parseInt(rawId, 10);

  if (!Number.isSafeInteger(entryId) || entryId < 1) {
    return {
      kind: "error",
      query: rawId ?? "",
      status: 400,
      code: "bad_request",
      message: "Missing or invalid dictionary entry id.",
    };
  }

  return entryId;
}

export function parseDictAIRequestBody(body: unknown): WebDictAIRequest | WebDictAIErrorResult {
  if (!body || typeof body !== "object") {
    return {
      kind: "error",
      query: "",
      status: 400,
      code: "bad_request",
      message: "Missing dictionary AI request body.",
    };
  }

  const payload = body as Record<string, unknown>;
  const mode = typeof payload.mode === "string" ? payload.mode : null;
  const query = typeof payload.query === "string" ? payload.query.trim() : "";
  const queryType = typeof payload.queryType === "string" ? payload.queryType : null;
  const contextSentence =
    typeof payload.contextSentence === "string" ? payload.contextSentence.trim() : "";
  const occurrence =
    payload.occurrence === undefined ? undefined : Number.parseInt(String(payload.occurrence), 10);
  const recordId = typeof payload.recordId === "string" ? payload.recordId.trim() : undefined;
  const sentenceId =
    typeof payload.sentenceId === "string" ? payload.sentenceId.trim() : undefined;
  const source = typeof payload.source === "string" ? payload.source : null;

  if (mode !== "context_explain" && mode !== "missing_fallback") {
    return {
      kind: "error",
      query,
      mode: undefined,
      status: 400,
      code: "bad_request",
      message: "Invalid dictionary AI mode.",
    };
  }

  if (!query) {
    return {
      kind: "error",
      query: "",
      mode,
      status: 400,
      code: "bad_request",
      message: "Missing dictionary AI query.",
    };
  }

  if (!isLookupType(queryType)) {
    return {
      kind: "error",
      query,
      mode,
      status: 400,
      code: "bad_request",
      message: "Invalid dictionary AI query type.",
    };
  }

  if (!contextSentence) {
    return {
      kind: "error",
      query,
      mode,
      status: 400,
      code: "bad_request",
      message: "Missing dictionary AI context sentence.",
    };
  }

  if (payload.occurrence !== undefined && !Number.isSafeInteger(occurrence)) {
    return {
      kind: "error",
      query,
      mode,
      status: 400,
      code: "bad_request",
      message: "Invalid dictionary AI occurrence.",
    };
  }

  if (source !== null && !isDictAISource(source)) {
    return {
      kind: "error",
      query,
      mode,
      status: 400,
      code: "bad_request",
      message: "Invalid dictionary AI source.",
    };
  }

  if (mode === "context_explain") {
    const entryId = Number.parseInt(String(payload.entryId), 10);
    if (!Number.isSafeInteger(entryId) || entryId < 1) {
      return {
        kind: "error",
        query,
        mode,
        status: 400,
        code: "bad_request",
        message: "Missing dictionary AI entry id.",
      };
    }

    return {
      mode,
      query,
      queryType,
      contextSentence,
      occurrence,
      recordId: recordId || undefined,
      sentenceId: sentenceId || undefined,
      source: source ?? undefined,
      entryId,
    };
  }

  return {
    mode,
    query,
    queryType,
    contextSentence,
    occurrence,
    recordId: recordId || undefined,
    sentenceId: sentenceId || undefined,
    source: source ?? undefined,
  };
}

export async function lookupDictForWeb(params: WebDictLookupParams): Promise<WebDictResult> {
  const upstreamParams: LookupDictParams = {
    query: params.query,
    type: params.type,
    contextSentence: params.context,
    occurrence: params.occurrence,
  };
  const upstreamResult = await lookupUpstreamDict(upstreamParams);

  if (!upstreamResult.ok) {
    return upstreamError(params.query, upstreamResult.status, upstreamResult.message);
  }

  return projectUpstreamResult(upstreamResult.data);
}

export async function lookupDictEntryForWeb(entryId: number): Promise<WebDictResult> {
  const upstreamResult = await lookupUpstreamDictEntry(entryId);

  if (!upstreamResult.ok) {
    return upstreamError(String(entryId), upstreamResult.status, upstreamResult.message);
  }

  return projectUpstreamResult(upstreamResult.data);
}

export async function lookupDictAIForWeb(
  body: WebDictAIRequest,
): Promise<WebDictAIResult | WebDictAIErrorResult> {
  const session = await getWebSession();

  if (session.kind === "anonymous" || session.kind === "mock_phone") {
    return {
      kind: "error",
      query: body.query,
      mode: body.mode,
      status: 401,
      code: "auth_required",
      message:
        session.kind === "mock_phone"
          ? "当前登录态不能使用真实 AI 查词，请使用真实登录会话后再试。"
          : "请先登录后再使用 AI 查词。",
    };
  }

  const upstreamResult = await lookupUpstreamDictAI(body, session.sessionToken);

  if (!upstreamResult.ok) {
    return dictAIUpstreamError(body, upstreamResult.status, upstreamResult.message, upstreamResult.payload);
  }

  return projectDictAIResult(upstreamResult.data);
}
