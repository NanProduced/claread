import type {
  DictExampleDto,
  DictLookupTypeDto,
  DictMeaningDto,
  DictPhraseDto,
  WebDictEntry,
} from "@/types/api/dict";

export type DictAISourceDto = "reader_click" | "selection" | "manual_search";
export type DictAIConfidenceDto = "high" | "medium" | "low";
export type DictAIClassificationDto =
  | "valid_word"
  | "slang_or_informal"
  | "proper_noun"
  | "domain_term"
  | "variant_or_inflection"
  | "possible_typo_or_ocr"
  | "unrecognized_noise";

export interface WebDictAIContextExplainRequest {
  mode: "context_explain";
  query: string;
  queryType: DictLookupTypeDto;
  contextSentence: string;
  occurrence?: number;
  recordId?: string;
  sentenceId?: string;
  source?: DictAISourceDto;
  entryId: number;
}

export interface WebDictAIMissingFallbackRequest {
  mode: "missing_fallback";
  query: string;
  queryType: DictLookupTypeDto;
  contextSentence: string;
  occurrence?: number;
  recordId?: string;
  sentenceId?: string;
  source?: DictAISourceDto;
}

export type WebDictAIRequest =
  | WebDictAIContextExplainRequest
  | WebDictAIMissingFallbackRequest;

export interface DictAIContextExplainResponseDto {
  mode: "context_explain";
  query: string;
  summary: string;
  best_fit_sense: string | null;
  why_here: string | null;
  cue: string | null;
  translation: string | null;
  contrast: string | null;
  learning_tip: string | null;
  confidence: DictAIConfidenceDto | null;
}

export interface DictAIEntryPayloadDto {
  word: string;
  base_word: string | null;
  phonetic: string | null;
  meanings: DictMeaningDto[];
  examples: DictExampleDto[];
  phrases: DictPhraseDto[];
  entry_kind: "entry" | "fragment" | null;
  exchange: string[];
  tags: string[];
}

interface DictAIMissingFallbackBaseDto {
  mode: "missing_fallback";
  query: string;
  classification: DictAIClassificationDto;
  summary: string;
  confidence: DictAIConfidenceDto | null;
  verified: false;
  source: "ai_generated";
  suggested_query: string[];
}

export interface DictAIMissingFallbackEntryResponseDto extends DictAIMissingFallbackBaseDto {
  result_kind: "ai_entry";
  entry: DictAIEntryPayloadDto;
}

export interface DictAIMissingFallbackUnresolvedResponseDto extends DictAIMissingFallbackBaseDto {
  result_kind: "ai_unresolved";
  reason: string | null;
}

export type DictAIResponseDto =
  | DictAIContextExplainResponseDto
  | DictAIMissingFallbackEntryResponseDto
  | DictAIMissingFallbackUnresolvedResponseDto;

export type WebDictAIEntry = Omit<WebDictEntry, "id" | "homographNo">;

export interface WebDictAIContextExplainResult {
  kind: "context_explain";
  mode: "context_explain";
  query: string;
  summary: string;
  bestFitSense?: string;
  whyHere?: string;
  cue?: string;
  translation?: string;
  contrast?: string;
  learningTip?: string;
  confidence?: DictAIConfidenceDto;
}

interface WebDictAIMissingFallbackBaseResult {
  mode: "missing_fallback";
  query: string;
  classification: DictAIClassificationDto;
  summary: string;
  confidence?: DictAIConfidenceDto;
  verified: false;
  source: "ai_generated";
  suggestedQuery: string[];
}

export interface WebDictAIMissingFallbackEntryResult extends WebDictAIMissingFallbackBaseResult {
  kind: "ai_entry";
  resultKind: "ai_entry";
  entry: WebDictAIEntry;
}

export interface WebDictAIMissingFallbackUnresolvedResult extends WebDictAIMissingFallbackBaseResult {
  kind: "ai_unresolved";
  resultKind: "ai_unresolved";
  reason?: string;
}

export type WebDictAIResult =
  | WebDictAIContextExplainResult
  | WebDictAIMissingFallbackEntryResult
  | WebDictAIMissingFallbackUnresolvedResult;

export type WebDictAIErrorCode =
  | "bad_request"
  | "auth_required"
  | "insufficient_credits"
  | "entry_query_mismatch"
  | "canonical_dictionary_available"
  | "entry_not_found"
  | "upstream_unavailable"
  | "upstream_error";

export interface WebDictAIErrorResult {
  kind: "error";
  query: string;
  mode?: WebDictAIRequest["mode"];
  status: number;
  code: WebDictAIErrorCode;
  message: string;
  remainingPoints?: number;
  requiredPoints?: number;
}

export type DictionaryAIViewState =
  | { kind: "idle" }
  | {
      kind: "loading";
      mode: WebDictAIRequest["mode"];
      requestKey: string;
    }
  | {
      kind: "ready";
      mode: WebDictAIRequest["mode"];
      requestKey: string;
      result: WebDictAIResult;
    }
  | {
      kind: "error";
      mode: WebDictAIRequest["mode"];
      requestKey: string;
      error: WebDictAIErrorResult;
    };
