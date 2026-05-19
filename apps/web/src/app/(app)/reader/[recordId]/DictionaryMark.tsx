"use client";

import type { ReactNode } from "react";

export {
  exchangeForms,
  firstMeaning,
  firstPartOfSpeech,
  meaningsJson,
  type DictionaryLookupSnapshot,
  type LookupState,
  type SaveState,
} from "@/components/reader/dictionary/contracts";

interface DictionaryMarkProps {
  children: ReactNode;
  className?: string;
}

export function DictionaryMark({ children, className }: DictionaryMarkProps) {
  return <span className={className}>{children}</span>;
}
