"use client";

import type { DictLookupTypeDto, WebDictResult } from "@/types/api/dict";
import type { VocabularyCreateRequestDto } from "@/types/api/vocabulary";
import type { AnnotationType, InlineGlossary, VisualTone } from "@/types/view/ReaderMockVm";

export type LookupState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; result: WebDictResult }
  | { kind: "error"; message: string };

export type SaveState =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved"; message: string }
  | { kind: "error"; message: string };

export interface DictionaryLookupSnapshot {
  query: string;
  lookupType: DictLookupTypeDto;
  contextSentence: string;
  sourceContext?: string;
  recordId: string;
  sentenceId: string;
  anchorText: string;
  occurrence?: number;
  title: string;
  label?: string;
  annotationType?: AnnotationType;
  visualTone?: VisualTone;
  glossary?: InlineGlossary;
  state: LookupState;
}

export function firstMeaning(result: WebDictResult): string {
  if (result.kind !== "entry") {
    return "";
  }

  const firstDefinition = result.entry.meanings.at(0)?.definitions.at(0);
  return firstDefinition?.meaning ?? "";
}

export function firstPartOfSpeech(result: WebDictResult): string | null {
  if (result.kind !== "entry") {
    return null;
  }

  return result.entry.meanings.at(0)?.partOfSpeech ?? null;
}

export function meaningsJson(result: WebDictResult): VocabularyCreateRequestDto["meanings_json"] {
  if (result.kind !== "entry") {
    return [];
  }

  return result.entry.meanings.map((meaning) => ({
    part_of_speech: meaning.partOfSpeech,
    definitions: meaning.definitions.map((definition) => ({
      meaning: definition.meaning,
      example: definition.example ?? null,
      example_translation: definition.exampleTranslation ?? null,
    })),
  }));
}

export function exchangeForms(result: WebDictResult): string[] {
  return result.kind === "entry" ? result.entry.exchange : [];
}
