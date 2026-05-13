import "server-only";

import {
  lookupUpstreamDict,
  lookupUpstreamDictEntry,
  type LookupDictParams,
} from "@/services/api/dict";
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

function normalizeNullableString(value: string | null | undefined): string | undefined {
  const trimmed = value?.trim();
  return trimmed ? trimmed : undefined;
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
