import "server-only";

import { fastApiFetch, type UpstreamResult } from "@/services/api/upstream";
import type {
  DictEntryResultDto,
  DictLookupTypeDto,
  DictResponseDto,
} from "@/types/api/dict";
import type { DictAIResponseDto, WebDictAIRequest } from "@/types/api/dict-ai";

export interface LookupDictParams {
  query: string;
  type?: DictLookupTypeDto;
  contextSentence?: string;
  occurrence?: number;
}

export function lookupUpstreamDict(params: LookupDictParams): Promise<UpstreamResult<DictResponseDto>> {
  const searchParams = new URLSearchParams({
    q: params.query,
    type: params.type ?? (params.query.trim().includes(" ") ? "phrase" : "word"),
  });

  if (params.contextSentence) {
    searchParams.set("context_sentence", params.contextSentence.slice(0, 500));
  }

  if (params.occurrence !== undefined) {
    searchParams.set("occurrence", String(params.occurrence));
  }

  return fastApiFetch<DictResponseDto>(`/dict?${searchParams.toString()}`);
}

export function lookupUpstreamDictEntry(entryId: number): Promise<UpstreamResult<DictEntryResultDto>> {
  const searchParams = new URLSearchParams({ id: String(entryId) });

  return fastApiFetch<DictEntryResultDto>(`/dict/entry?${searchParams.toString()}`);
}

function toUpstreamDictAIRequest(body: WebDictAIRequest) {
  return {
    mode: body.mode,
    query: body.query,
    query_type: body.queryType,
    context_sentence: body.contextSentence.slice(0, 5000),
    occurrence: body.occurrence,
    record_id: body.recordId,
    sentence_id: body.sentenceId,
    source: body.source,
    ...(body.mode === "context_explain" ? { entry_id: body.entryId } : {}),
  };
}

export function lookupUpstreamDictAI(
  body: WebDictAIRequest,
  sessionToken: string,
): Promise<UpstreamResult<DictAIResponseDto>> {
  return fastApiFetch<DictAIResponseDto>("/dict/ai", {
    method: "POST",
    sessionToken,
    body: JSON.stringify(toUpstreamDictAIRequest(body)),
  });
}
