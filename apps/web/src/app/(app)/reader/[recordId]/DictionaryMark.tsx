"use client";

import { useState } from "react";

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

interface DictionaryMarkProps {
  children: string;
  className: string;
  query: string;
  contextSentence: string;
  sourceContext?: string;
  recordId: string;
  sentenceId: string;
  anchorText: string;
  occurrence?: number;
  lookupType: DictLookupTypeDto;
  title: string;
  label?: string;
  annotationType?: AnnotationType;
  visualTone?: VisualTone;
  glossary?: InlineGlossary;
  onLookupSnapshot?: (snapshot: DictionaryLookupSnapshot) => void;
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

export function meaningsJson(result: WebDictResult): Record<string, unknown>[] {
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

function ResultPanel({
  state,
  saveState,
  onSave,
}: {
  state: LookupState;
  saveState: SaveState;
  onSave: () => void;
}) {
  if (state.kind === "idle") {
    return null;
  }

  return (
    <span className="absolute left-0 top-full z-20 mt-2 block w-72 rounded-md border border-hairline bg-surface p-3 text-left text-sm leading-6 text-ink-soft shadow-surface-quiet">
      {state.kind === "loading" ? <span className="text-muted">查询词典中...</span> : null}
      {state.kind === "error" ? <span className="text-error-red">{state.message}</span> : null}
      {state.kind === "ready" && state.result.kind === "entry" ? (
        <span className="block">
          <span className="block font-semibold text-ink">
            {state.result.entry.word}
            {state.result.entry.phonetic ? (
              <span className="ml-2 font-normal text-muted">{state.result.entry.phonetic}</span>
            ) : null}
          </span>
          <span className="mt-1 block">{firstMeaning(state.result) || "暂无释义。"}</span>
          <span className="mt-3 flex items-center gap-2">
            <button
              type="button"
              className="rounded-md border border-hairline bg-ink px-2.5 py-1 text-xs font-semibold text-surface disabled:cursor-not-allowed disabled:opacity-60"
              onClick={onSave}
              disabled={saveState.kind === "saving" || saveState.kind === "saved"}
            >
              {saveState.kind === "saving" ? "写入中..." : "加入生词本"}
            </button>
            {saveState.kind === "saved" ? (
              <span className="text-xs text-structure-green">{saveState.message}</span>
            ) : null}
            {saveState.kind === "error" ? (
              <span className="text-xs text-error-red">{saveState.message}</span>
            ) : null}
          </span>
        </span>
      ) : null}
      {state.kind === "ready" && state.result.kind === "disambiguation" ? (
        <span className="block">
          <span className="block font-semibold text-ink">请选择具体词条</span>
          <span className="mt-1 block text-muted">
            {state.result.candidates
              .slice(0, 3)
              .map((candidate) => candidate.label)
              .join(" / ")}
          </span>
        </span>
      ) : null}
      {state.kind === "ready" && state.result.kind === "not_found" ? (
        <span className="text-muted">未在当前词典中找到该词条。</span>
      ) : null}
      {state.kind === "ready" && state.result.kind === "error" ? (
        <span className="text-error-red">{state.result.message}</span>
      ) : null}
    </span>
  );
}

export function DictionaryMark({
  children,
  className,
  query,
  contextSentence,
  sourceContext,
  recordId,
  sentenceId,
  anchorText,
  occurrence,
  lookupType,
  title,
  label,
  annotationType,
  visualTone,
  glossary,
  onLookupSnapshot,
}: DictionaryMarkProps) {
  const [state, setState] = useState<LookupState>({ kind: "idle" });
  const [saveState, setSaveState] = useState<SaveState>({ kind: "idle" });
  const [open, setOpen] = useState(false);

  function emitSnapshot(nextState: LookupState) {
    onLookupSnapshot?.({
      query,
      lookupType,
      contextSentence,
      sourceContext,
      recordId,
      sentenceId,
      anchorText,
      occurrence,
      title,
      label,
      annotationType,
      visualTone,
      glossary,
      state: nextState,
    });
  }

  async function handleLookup() {
    if (state.kind === "ready") {
      setOpen((value) => !value);
      emitSnapshot(state);
      return;
    }

    setOpen(true);
    const loadingState: LookupState = { kind: "loading" };
    setState(loadingState);
    emitSnapshot(loadingState);

    try {
      const params = new URLSearchParams({
        word: query,
        type: lookupType,
        context: contextSentence,
        sentenceId,
      });
      if (occurrence !== undefined) {
        params.set("occurrence", String(occurrence));
      }
      const response = await fetch(`/api/web/dict/lookup?${params.toString()}`);
      const payload = (await response.json()) as WebDictResult;

      if (!response.ok || payload.kind === "error") {
        const readyState: LookupState = {
          kind: "ready",
          result: payload,
        };
        setState(readyState);
        emitSnapshot(readyState);
        return;
      }

      const readyState: LookupState = { kind: "ready", result: payload };
      setState(readyState);
      emitSnapshot(readyState);
      setSaveState({ kind: "idle" });
    } catch (error) {
      const errorState: LookupState = {
        kind: "error",
        message: error instanceof Error ? error.message : "词典查询失败。",
      };
      setState(errorState);
      emitSnapshot(errorState);
    }
  }

  async function handleSave() {
    if (state.kind !== "ready" || state.result.kind !== "entry") {
      setSaveState({ kind: "error", message: "请先查到明确词条后再加入生词本。" });
      return;
    }

    const result = state.result;
    const shortMeaning = firstMeaning(result);

    if (!shortMeaning) {
      setSaveState({ kind: "error", message: "当前词条缺少可写入的释义。" });
      return;
    }

    setSaveState({ kind: "saving" });

    const body: VocabularyCreateRequestDto = {
      lemma: result.entry.baseWord ?? result.entry.word,
      display_word: result.entry.word,
      phonetic: result.entry.phonetic ?? null,
      part_of_speech: firstPartOfSpeech(result),
      short_meaning: shortMeaning,
      meanings_json: meaningsJson(result),
      tags: result.entry.tags,
      exchange: exchangeForms(result),
      source_provider: result.provider,
      dict_entry_id: result.entry.id,
      source_sentence: contextSentence,
      source_context: sourceContext ?? null,
      payload_json: {
        source_refs: [
          {
            client_record_id: recordId,
            cloud_record_id: recordId,
            source_sentence: contextSentence,
            source_context: sourceContext ?? null,
            source_sentence_id: sentenceId,
            source_anchor_text: anchorText,
            source_occurrence: occurrence ?? null,
            collected_at: new Date().toISOString(),
          },
        ],
        collected_forms: [anchorText, query],
      },
    };

    try {
      const response = await fetch("/api/web/vocabulary", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = (await response.json().catch(() => ({}))) as { message?: string };

      if (!response.ok) {
        setSaveState({
          kind: "error",
          message: payload.message ?? "加入生词本失败。",
        });
        return;
      }

      setSaveState({
        kind: "saved",
        message: payload.message ?? "已加入生词本。",
      });
    } catch (error) {
      setSaveState({
        kind: "error",
        message: error instanceof Error ? error.message : "加入生词本失败。",
      });
    }
  }

  return (
    <span className="relative inline-block">
      <button
        type="button"
        className={`cursor-pointer border-0 text-inherit ${className}`}
        onClick={handleLookup}
        title={title}
        aria-expanded={open}
      >
        {children}
      </button>
      {open ? <ResultPanel state={state} saveState={saveState} onSave={handleSave} /> : null}
    </span>
  );
}
