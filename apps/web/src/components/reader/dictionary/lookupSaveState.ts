"use client";

import type { VocabularyMasteryStatusDto, VocabularySourceRefDto } from "@/types/api/vocabulary";
import type { DictionaryLookupSnapshot } from "./contracts";

export type LookupSaveState =
  | "not_saved"
  | "same_lemma_new_context"
  | "already_saved_here"
  | "multiple_contexts"
  | "mastered";

export interface ReaderVocabularyLookupMatch {
  id: string;
  lemma: string;
  displayWord: string;
  dictEntryId: number | null;
  masteryStatus: VocabularyMasteryStatusDto;
  sourceRefs: VocabularySourceRefDto[];
  collectedForms: string[];
}

export interface ReaderVocabularyLookupMatchRequest {
  dictEntryId: number | null;
  lemma: string;
  form: string;
}

function normalizeValue(value?: string | null) {
  return value?.trim().toLowerCase() ?? "";
}

function uniqStrings(values: Array<string | null | undefined>) {
  const seen = new Set<string>();
  return values
    .map((value) => normalizeValue(value))
    .filter((value) => {
      if (!value || seen.has(value)) {
        return false;
      }
      seen.add(value);
      return true;
    });
}

function sourceRefSameSentence(ref: VocabularySourceRefDto, currentSentenceId?: string) {
  return Boolean(currentSentenceId && ref.source_sentence_id === currentSentenceId);
}

export function getLookupSaveState(
  isCurrentlySaved: boolean,
  currentSentenceId?: string,
  savedSourceRefs?: VocabularySourceRefDto[],
  isMastered?: boolean,
): LookupSaveState {
  if (!isCurrentlySaved) {
    return "not_saved";
  }

  if (isMastered) {
    return "mastered";
  }

  const refs = savedSourceRefs ?? [];
  if (refs.some((ref) => sourceRefSameSentence(ref, currentSentenceId))) {
    return "already_saved_here";
  }

  if (refs.length > 1) {
    return "multiple_contexts";
  }

  if (refs.length > 0) {
    return "same_lemma_new_context";
  }

  return "already_saved_here";
}

export function getSaveActionCopy(state: LookupSaveState, contextCount?: number, defaultCopy = "加入生词本") {
  switch (state) {
    case "not_saved":
      return defaultCopy;
    case "same_lemma_new_context":
      return "加入当前语境";
    case "already_saved_here":
      return "已加入";
    case "multiple_contexts":
      return contextCount && contextCount > 1 ? `已加入 · ${contextCount}个语境` : "已加入生词本";
    case "mastered":
      return "已掌握";
    default:
      return defaultCopy;
  }
}

export function lookupSaveRequestFromSnapshot(
  lookup: DictionaryLookupSnapshot | null,
): ReaderVocabularyLookupMatchRequest | null {
  if (!lookup || lookup.state.kind !== "ready" || lookup.state.result.kind !== "entry") {
    return null;
  }

  const entry = lookup.state.result.entry;
  return {
    dictEntryId: entry.id,
    lemma: entry.baseWord?.trim() || entry.word.trim() || lookup.query.trim(),
    form: lookup.anchorText.trim() || lookup.query.trim(),
  };
}

export function lookupSaveCacheKey(request: ReaderVocabularyLookupMatchRequest | null) {
  if (!request) {
    return null;
  }

  if (request.dictEntryId) {
    return `dict:${request.dictEntryId}`;
  }

  if (request.lemma) {
    return `lemma:${normalizeValue(request.lemma)}`;
  }

  if (request.form) {
    return `form:${normalizeValue(request.form)}`;
  }

  return null;
}

export function buildSourceRefFromLookup(lookup: DictionaryLookupSnapshot): VocabularySourceRefDto {
  return {
    client_record_id: lookup.recordId,
    cloud_record_id: lookup.recordId,
    source_sentence: lookup.contextSentence || null,
    source_context: lookup.sourceContext ?? null,
    source_sentence_id: lookup.sentenceId || null,
    source_anchor_text: lookup.anchorText || null,
    source_occurrence: lookup.occurrence ?? null,
    collected_at: new Date().toISOString(),
  };
}

export function mergeLookupSourceRefs(
  existing: VocabularySourceRefDto[],
  incoming: VocabularySourceRefDto,
) {
  const dedupKey = [
    incoming.source_sentence_id ?? "",
    incoming.source_anchor_text ?? "",
    incoming.source_occurrence ?? "",
  ].join("::");
  const seen = new Set<string>();
  const merged = [...existing, incoming].filter((ref) => {
    const key = [
      ref.source_sentence_id ?? "",
      ref.source_anchor_text ?? "",
      ref.source_occurrence ?? "",
    ].join("::");
    if (seen.has(key)) {
      return key !== dedupKey;
    }
    seen.add(key);
    return true;
  });

  return merged;
}

export function buildOptimisticLookupMatch(
  lookup: DictionaryLookupSnapshot,
  existing: ReaderVocabularyLookupMatch | null,
  createdId: string,
): ReaderVocabularyLookupMatch | null {
  if (lookup.state.kind !== "ready" || lookup.state.result.kind !== "entry") {
    return existing;
  }

  const entry = lookup.state.result.entry;
  const sourceRef = buildSourceRefFromLookup(lookup);

  return {
    id: existing?.id ?? createdId,
    lemma: entry.baseWord?.trim() || entry.word.trim() || lookup.query.trim(),
    displayWord: entry.word.trim() || lookup.anchorText.trim() || lookup.query.trim(),
    dictEntryId: entry.id,
    masteryStatus: existing?.masteryStatus ?? "new",
    sourceRefs: mergeLookupSourceRefs(existing?.sourceRefs ?? [], sourceRef),
    collectedForms: uniqStrings([
      ...(existing?.collectedForms ?? []),
      lookup.anchorText,
      lookup.query,
      entry.word,
      entry.baseWord,
    ]),
  };
}
