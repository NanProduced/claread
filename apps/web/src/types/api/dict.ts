export type DictLookupTypeDto = "word" | "phrase";

export interface DictMeaningDefinitionDto {
  meaning: string;
  example: string | null;
  example_translation: string | null;
}

export interface DictMeaningDto {
  part_of_speech: string;
  definitions: DictMeaningDefinitionDto[];
}

export interface DictExampleDto {
  example: string;
  example_translation: string | null;
}

export interface DictPhraseDto {
  phrase: string;
  meaning: string | null;
}

export interface DictEntryPayloadDto {
  id: number;
  word: string;
  base_word: string | null;
  homograph_no: number | null;
  phonetic: string | null;
  meanings: DictMeaningDto[];
  examples: DictExampleDto[];
  phrases: DictPhraseDto[];
  entry_kind: "entry" | "fragment";
  exchange?: string[];
  tags?: string[];
}

export interface DictCandidateDto {
  entry_id: number;
  label: string;
  part_of_speech: string | null;
  preview: string | null;
  entry_kind: "entry" | "fragment";
  match_kind?: string;
  lookup_type?: DictLookupTypeDto;
  candidate_kind?: "word" | "phrase" | "proper_noun" | "variant" | "fragment";
}

interface DictResponseBaseDto {
  result_type: "entry" | "disambiguation" | "not_found";
  query: string;
  provider: string;
  cached: boolean;
}

export interface DictEntryResultDto extends DictResponseBaseDto {
  result_type: "entry";
  entry: DictEntryPayloadDto;
}

export interface DictDisambiguationResultDto extends DictResponseBaseDto {
  result_type: "disambiguation";
  ambiguity_kind?:
    | "same_headword_senses"
    | "phrase_vs_word"
    | "proper_vs_common"
    | "lemma_competing"
    | "competing_entries";
  selection_required?: boolean;
  candidates: DictCandidateDto[];
}

export interface DictNotFoundResultDto extends DictResponseBaseDto {
  result_type: "not_found";
  reason: "not_in_dictionary";
}

export type DictResponseDto =
  | DictEntryResultDto
  | DictDisambiguationResultDto
  | DictNotFoundResultDto;

export interface WebDictEntry {
  id: number;
  word: string;
  baseWord?: string;
  homographNo?: number;
  phonetic?: string;
  meanings: Array<{
    partOfSpeech: string;
    definitions: Array<{
      meaning: string;
      example?: string;
      exampleTranslation?: string;
    }>;
  }>;
  examples: Array<{
    example: string;
    exampleTranslation?: string;
  }>;
  phrases: Array<{
    phrase: string;
    meaning?: string;
  }>;
  entryKind: "entry" | "fragment";
  exchange: string[];
  tags: string[];
}

export interface WebDictCandidate {
  entryId: number;
  label: string;
  partOfSpeech?: string;
  preview?: string;
  entryKind: "entry" | "fragment";
  matchKind: string;
  lookupType: DictLookupTypeDto;
  candidateKind: "word" | "phrase" | "proper_noun" | "variant" | "fragment";
}

interface WebDictResultBase {
  query: string;
  provider: string;
  cached: boolean;
}

export interface WebDictEntryResult extends WebDictResultBase {
  kind: "entry";
  entry: WebDictEntry;
}

export interface WebDictDisambiguationResult extends WebDictResultBase {
  kind: "disambiguation";
  ambiguityKind: NonNullable<DictDisambiguationResultDto["ambiguity_kind"]>;
  selectionRequired: boolean;
  candidates: WebDictCandidate[];
}

export interface WebDictNotFoundResult extends WebDictResultBase {
  kind: "not_found";
  reason: "not_in_dictionary";
}

export interface WebDictErrorResult {
  kind: "error";
  query: string;
  status: number;
  code: "bad_request" | "upstream_unavailable" | "upstream_error";
  message: string;
}

export type WebDictResult =
  | WebDictEntryResult
  | WebDictDisambiguationResult
  | WebDictNotFoundResult
  | WebDictErrorResult;
