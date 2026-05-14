"use client";

import { useState } from "react";

import type { DictLookupTypeDto, WebDictResult } from "@/types/api/dict";

type LookupState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; result: WebDictResult }
  | { kind: "error"; message: string };

interface DictionaryMarkProps {
  children: string;
  className: string;
  query: string;
  contextSentence: string;
  lookupType: DictLookupTypeDto;
  title: string;
}

function firstMeaning(result: WebDictResult): string {
  if (result.kind !== "entry") {
    return "";
  }

  const firstDefinition = result.entry.meanings.at(0)?.definitions.at(0);
  return firstDefinition?.meaning ?? "";
}

function ResultPanel({ state }: { state: LookupState }) {
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
  lookupType,
  title,
}: DictionaryMarkProps) {
  const [state, setState] = useState<LookupState>({ kind: "idle" });
  const [open, setOpen] = useState(false);

  async function handleLookup() {
    if (state.kind === "ready") {
      setOpen((value) => !value);
      return;
    }

    setOpen(true);
    setState({ kind: "loading" });

    try {
      const params = new URLSearchParams({
        word: query,
        type: lookupType,
        context: contextSentence,
      });
      const response = await fetch(`/api/web/dict/lookup?${params.toString()}`);
      const payload = (await response.json()) as WebDictResult;

      if (!response.ok || payload.kind === "error") {
        setState({
          kind: "ready",
          result: payload,
        });
        return;
      }

      setState({ kind: "ready", result: payload });
    } catch (error) {
      setState({
        kind: "error",
        message: error instanceof Error ? error.message : "词典查询失败。",
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
      {open ? <ResultPanel state={state} /> : null}
    </span>
  );
}
