export interface DictMeaningDefinitionDto {
  meaning: string
  example: string | null
  example_translation: string | null
}

export interface DictMeaningDto {
  part_of_speech: string
  definitions: DictMeaningDefinitionDto[]
}

export interface DictExampleDto {
  example: string
  example_translation: string | null
}

export interface DictPhraseDto {
  phrase: string
  meaning: string | null
}

export interface DictEntryPayloadDto {
  id: number
  word: string
  base_word: string | null
  homograph_no: number | null
  phonetic: string | null
  meanings: DictMeaningDto[]
  examples: DictExampleDto[]
  phrases: DictPhraseDto[]
  entry_kind: 'entry' | 'fragment'
  exchange?: string[]
  tags?: string[]
}

export interface DictCandidateDto {
  entry_id: number
  label: string
  part_of_speech: string | null
  preview: string | null
  entry_kind: 'entry' | 'fragment'
  match_kind?: string
  lookup_type?: 'word' | 'phrase'
  candidate_kind?: 'word' | 'phrase' | 'proper_noun' | 'variant' | 'fragment'
}

interface DictResponseBaseDto {
  result_type: 'entry' | 'disambiguation' | 'not_found'
  query: string
  provider: string
  cached: boolean
}

export interface DictEntryResultDto extends DictResponseBaseDto {
  result_type: 'entry'
  entry: DictEntryPayloadDto
}

export interface DictDisambiguationResultDto extends DictResponseBaseDto {
  result_type: 'disambiguation'
  ambiguity_kind?: 'same_headword_senses' | 'phrase_vs_word' | 'proper_vs_common' | 'lemma_competing' | 'competing_entries'
  selection_required?: boolean
  candidates: DictCandidateDto[]
}

export interface DictNotFoundResultDto extends DictResponseBaseDto {
  result_type: 'not_found'
  reason: 'not_in_dictionary'
}

export type DictResponseDto = DictEntryResultDto | DictDisambiguationResultDto | DictNotFoundResultDto
