import "server-only";

import { fastApiFetch, type UpstreamResult } from "@/services/api/upstream";
import type {
  DictEntryResultDto,
  DictLookupTypeDto,
  DictResponseDto,
} from "@/types/api/dict";

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
