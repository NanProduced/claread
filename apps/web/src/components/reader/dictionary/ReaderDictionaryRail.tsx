"use client";

import type { CSSProperties } from "react";
import type { ReaderStructuredInspectIntent } from "@/lib/reader-plate";
import type { DictionaryAIViewState, WebDictAIRequest } from "@/types/api/dict-ai";
import type { DictionaryLookupSnapshot, SaveState } from "./contracts";
import { ReaderDictionaryDetailPanel } from "./ReaderDictionaryDetailPanel";
import { ReaderDictionaryRecentStrip } from "./ReaderDictionaryRecentStrip";

interface ReaderDictionaryRailProps {
  lookup: DictionaryLookupSnapshot | null;
  inspect?: ReaderStructuredInspectIntent | null;
  history: DictionaryLookupSnapshot[];
  readingGoal: string;
  saveState: SaveState;
  dictionaryAI: DictionaryAIViewState;
  dictionaryAIPanelOpen: boolean;
  dictionaryAINoteState: SaveState;
  searchQuery: string;
  searchExpanded: boolean;
  onSave: () => void;
  onRequestAI: (mode: WebDictAIRequest["mode"]) => void;
  onCreateAINote: () => void;
  onSelectAISuggestedQuery: (query: string) => void;
  onSearchQueryChange: (value: string) => void;
  onSearchSubmit: (query: string) => void;
  onSelectCandidate: (entryId: number) => void;
  onToggleAIPanel: () => void;
  onToggleSearchExpanded: () => void;
  onDismiss?: () => void;
  pinned?: boolean;
  onTogglePinned?: () => void;
  variant?: "card" | "sheet";
  canSaveVocabulary?: boolean;
  onLookupPhraseFromInspect?: (intent: ReaderStructuredInspectIntent) => void;
  onAttachToAsk?: (intent: ReaderStructuredInspectIntent) => void;
  onSelectHistory: (lookup: DictionaryLookupSnapshot) => void;
  className?: string;
  style?: CSSProperties;
  canCreateAINote?: boolean;
}

export function ReaderDictionaryRail({
  canSaveVocabulary = true,
  className,
  dictionaryAI,
  dictionaryAIPanelOpen,
  dictionaryAINoteState,
  history,
  inspect = null,
  lookup,
  onAttachToAsk,
  onCreateAINote,
  onDismiss,
  onLookupPhraseFromInspect,
  onRequestAI,
  onSave,
  onSearchQueryChange,
  onSearchSubmit,
  onSelectAISuggestedQuery,
  onSelectCandidate,
  onSelectHistory,
  onToggleAIPanel,
  onTogglePinned,
  onToggleSearchExpanded,
  pinned = false,
  readingGoal,
  saveState,
  searchExpanded,
  searchQuery,
  style,
  variant = "sheet",
  canCreateAINote = false,
}: ReaderDictionaryRailProps) {
  return (
    <div className={className} style={style}>
      <div className="flex h-full flex-col gap-3">
        <div className="min-h-0 flex-1 overflow-hidden rounded-panel">
          <ReaderDictionaryDetailPanel
            lookup={lookup}
            inspect={inspect}
            readingGoal={readingGoal}
            saveState={saveState}
            dictionaryAI={dictionaryAI}
            dictionaryAIPanelOpen={dictionaryAIPanelOpen}
            dictionaryAINoteState={dictionaryAINoteState}
            searchQuery={searchQuery}
            searchExpanded={searchExpanded}
            onSave={onSave}
            onRequestAI={onRequestAI}
            onCreateAINote={onCreateAINote}
            onSelectAISuggestedQuery={onSelectAISuggestedQuery}
            onSearchQueryChange={onSearchQueryChange}
            onSearchSubmit={onSearchSubmit}
            onSelectCandidate={onSelectCandidate}
            onToggleAIPanel={onToggleAIPanel}
            onToggleSearchExpanded={onToggleSearchExpanded}
            onDismiss={onDismiss}
            pinned={pinned}
            onTogglePinned={onTogglePinned}
            variant={variant}
            canSaveVocabulary={canSaveVocabulary}
            canCreateAINote={canCreateAINote}
            onAttachToAsk={onAttachToAsk}
            onLookupPhraseFromInspect={onLookupPhraseFromInspect}
          />
        </div>
        <div className="shrink-0">
          <ReaderDictionaryRecentStrip
            history={history}
            activeLookup={lookup}
            onSelectHistory={onSelectHistory}
          />
        </div>
      </div>
    </div>
  );
}
