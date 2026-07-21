/** @vitest-environment jsdom */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { hashAnchorText } from "@/lib/reader-plate";
import { ReaderDictionaryDetailPanel } from "./ReaderDictionaryDetailPanel";
import type { DictionaryLookupSnapshot } from "./contracts";

function createEntryLookup(): DictionaryLookupSnapshot {
  return {
    query: "memory",
    lookupType: "word",
    contextSentence: "Institutional memory shapes policy choices.",
    sourceContext: "制度记忆会塑造政策选择。",
    recordId: "record-1",
    sentenceId: "s1",
    anchorText: "memory",
    anchorOffsets: {
      startOffset: 14,
      endOffset: 20,
    },
    occurrence: 1,
    textHash: hashAnchorText("memory"),
    title: "查词",
    state: {
      kind: "ready",
      result: {
        kind: "entry",
        query: "memory",
        provider: "mock-dict",
        cached: false,
        entry: {
          id: 12,
          word: "memory",
          baseWord: "memory",
          phonetic: "/ˈmeməri/",
          meanings: [
            {
              partOfSpeech: "n.",
              definitions: [{ meaning: "记忆；经验积累" }],
            },
          ],
          examples: [],
          phrases: [],
          entryKind: "entry",
          exchange: [],
          tags: [],
        },
      },
    },
  };
}

function createTaggedEntryLookup(): DictionaryLookupSnapshot {
  const lookup = createEntryLookup();
  if (lookup.state.kind !== "ready" || lookup.state.result.kind !== "entry") {
    return lookup;
  }

  return {
    ...lookup,
    state: {
      ...lookup.state,
      result: {
        ...lookup.state.result,
        entry: {
          ...lookup.state.result.entry,
          tags: ["cet4", "cet6", "gaokao", "kaoyan", "gre"],
        },
      },
    },
  };
}

function createPhraseGlossLookup(): DictionaryLookupSnapshot {
  return {
    ...createEntryLookup(),
    query: "policy choices",
    lookupType: "phrase",
    anchorText: "policy choices",
    title: "短语",
    label: "短语",
    annotationType: "phrase_gloss",
    visualTone: "phrase",
    glossary: {
      gloss: "政策选择",
      phraseType: "fixed_collocation",
      example: "Policy choices shape institutions.",
    },
    state: {
      kind: "ready",
      result: {
        kind: "entry",
        query: "policy choices",
        provider: "mock-dict",
        cached: false,
        entry: {
          id: 32,
          word: "policy choices",
          baseWord: "policy choice",
          phonetic: undefined,
          meanings: [
            {
              partOfSpeech: "n.",
              definitions: [{ meaning: "choices about public policy" }],
            },
          ],
          examples: [],
          phrases: [],
          entryKind: "entry",
          exchange: [],
          tags: [],
        },
      },
    },
  };
}

function createContextGlossPhraseLookup(): DictionaryLookupSnapshot {
  return {
    ...createEntryLookup(),
    query: "allegedly",
    lookupType: "phrase",
    anchorText: "allegedly",
    title: "语境义",
    label: "语境义",
    annotationType: "context_gloss",
    visualTone: "context",
    glossary: {
      gloss: "据称，据说",
      reason: "allegedly 是高考阅读中常见的副词，表示‘据称’，暗示所述内容未必属实。",
    },
    state: {
      kind: "ready",
      result: {
        kind: "entry",
        query: "allegedly",
        provider: "mock-dict",
        cached: false,
        entry: {
          id: 33,
          word: "allegedly",
          baseWord: "allegedly",
          phonetic: "/əˈledʒɪdli/",
          meanings: [
            {
              partOfSpeech: "adv.",
              definitions: [{ meaning: "根据(人们)宣称" }],
            },
          ],
          examples: [],
          phrases: [],
          entryKind: "entry",
          exchange: [],
          tags: [],
        },
      },
    },
  };
}

describe("ReaderDictionaryDetailPanel", () => {
  beforeEach(() => {
    Object.defineProperty(HTMLElement.prototype, "scrollTo", {
      configurable: true,
      value: vi.fn(),
    });
  });
  afterEach(() => {
    cleanup();
  });

  it("renders a collapsed AI stub and re-expands cached context explain results", () => {
    const onToggleAIPanel = vi.fn();

    render(
      <ReaderDictionaryDetailPanel
        lookup={createEntryLookup()}
        readingGoal="general"
        saveState={{ kind: "idle" }}
        dictionaryAI={{
          kind: "ready",
          mode: "context_explain",
          requestKey: "context::record-1::s1",
          result: {
            kind: "context_explain",
            mode: "context_explain",
            query: "memory",
            summary: "这里强调长期积累下来的制度经验。",
            bestFitSense: "长期积累的经验",
          },
        }}
        dictionaryAIPanelOpen={false}
        dictionaryAINoteState={{ kind: "idle" }}
        searchQuery="memory"
        searchExpanded={false}
        onSave={vi.fn()}
        onRequestAI={vi.fn()}
        onCreateAINote={vi.fn()}
        onSelectAISuggestedQuery={vi.fn()}
        onSearchQueryChange={vi.fn()}
        onSearchSubmit={vi.fn()}
        onSelectCandidate={vi.fn()}
        onToggleAIPanel={onToggleAIPanel}
        onToggleSearchExpanded={vi.fn()}
      />,
    );

    expect(screen.getByText("AI 语境解读")).toBeTruthy();
    expect(screen.getByText("这里强调长期积累下来的制度经验。")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("展开AI 语境解读"));
    expect(onToggleAIPanel).toHaveBeenCalled();
  });

  it("shows precise-anchor AI note action only when it is supported", () => {
    const onCreateAINote = vi.fn();
    const lookup = createEntryLookup();

    const { rerender } = render(
      <ReaderDictionaryDetailPanel
        lookup={lookup}
        readingGoal="general"
        saveState={{ kind: "idle" }}
        dictionaryAI={{
          kind: "ready",
          mode: "context_explain",
          requestKey: "context::record-1::s1",
          result: {
            kind: "context_explain",
            mode: "context_explain",
            query: "memory",
            summary: "这里强调长期积累下来的制度经验。",
          },
        }}
        dictionaryAIPanelOpen={true}
        dictionaryAINoteState={{ kind: "idle" }}
        searchQuery="memory"
        searchExpanded={false}
        onSave={vi.fn()}
        onRequestAI={vi.fn()}
        onCreateAINote={onCreateAINote}
        onSelectAISuggestedQuery={vi.fn()}
        onSearchQueryChange={vi.fn()}
        onSearchSubmit={vi.fn()}
        onSelectCandidate={vi.fn()}
        onToggleAIPanel={vi.fn()}
        onToggleSearchExpanded={vi.fn()}
        canCreateAINote={true}
      />,
    );

    fireEvent.click(screen.getByText("AI 生成笔记"));
    expect(onCreateAINote).toHaveBeenCalled();

    rerender(
      <ReaderDictionaryDetailPanel
        lookup={{
          ...lookup,
          sentenceId: "__manual__",
          textHash: null,
          anchorOffsets: undefined,
          label: "手动查词",
        }}
        readingGoal="general"
        saveState={{ kind: "idle" }}
        dictionaryAI={{
          kind: "ready",
          mode: "context_explain",
          requestKey: "context::record-1::manual",
          result: {
            kind: "context_explain",
            mode: "context_explain",
            query: "memory",
            summary: "这里强调长期积累下来的制度经验。",
          },
        }}
        dictionaryAIPanelOpen={true}
        dictionaryAINoteState={{ kind: "idle" }}
        searchQuery="memory"
        searchExpanded={false}
        onSave={vi.fn()}
        onRequestAI={vi.fn()}
        onCreateAINote={vi.fn()}
        onSelectAISuggestedQuery={vi.fn()}
        onSearchQueryChange={vi.fn()}
        onSearchSubmit={vi.fn()}
        onSelectCandidate={vi.fn()}
        onToggleAIPanel={vi.fn()}
        onToggleSearchExpanded={vi.fn()}
        canCreateAINote={false}
      />,
    );

    expect(screen.queryByText("AI 生成笔记")).toBeNull();
  });

  it("keeps recent history fully collapsible and removes the fake dictionary page action", () => {
    const history = [
      createEntryLookup(),
      ...["policy", "warning", "appeared", "stationary", "Thursday", "asphalt-curling"].map((query, index) => ({
        ...createEntryLookup(),
        query,
        anchorText: query,
        occurrence: index + 2,
        textHash: hashAnchorText(query),
      })),
    ];
    const { container } = render(
      <ReaderDictionaryDetailPanel
        lookup={createEntryLookup()}
        readingGoal="general"
        saveState={{ kind: "idle" }}
        dictionaryAI={{ kind: "idle" }}
        dictionaryAIPanelOpen={false}
        dictionaryAINoteState={{ kind: "idle" }}
        searchQuery="memory"
        searchExpanded={false}
        onSave={vi.fn()}
        onRequestAI={vi.fn()}
        onCreateAINote={vi.fn()}
        onSelectAISuggestedQuery={vi.fn()}
        onSearchQueryChange={vi.fn()}
        onSearchSubmit={vi.fn()}
        onSelectCandidate={vi.fn()}
        onToggleAIPanel={vi.fn()}
        onToggleSearchExpanded={vi.fn()}
        history={history}
        onSelectHistory={vi.fn()}
      />,
    );

    expect(screen.queryByText("制度记忆会塑造政策选择。")).toBeNull();
    expect(screen.queryByText("在词典页中查看")).toBeNull();
    expect(screen.queryByPlaceholderText("搜索词典…")).toBeNull();
    expect(screen.getAllByLabelText("搜索词典").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByText("最近查阅"));
    expect(screen.getByText("policy")).toBeTruthy();
    const historyScroll = container.querySelector<HTMLElement>(
      '[data-reader-record-dictionary-history-scroll="active"]',
    );
    expect(historyScroll).not.toBeNull();
    expect(historyScroll?.className).toContain("h-64");
    expect(historyScroll?.className).toContain("scroll-area-thumb");
  });

  it("uses icon-only save feedback and collapses exam tags with a chevron action", () => {
    render(
      <ReaderDictionaryDetailPanel
        lookup={createTaggedEntryLookup()}
        readingGoal="exam"
        saveState={{ kind: "idle" }}
        lookupSaveState="already_saved_here"
        savedVocabularyMatch={{
          id: "vocab-1",
          lemma: "memory",
          displayWord: "memory",
          dictEntryId: 12,
          masteryStatus: "new",
          sourceRefs: [{ source_sentence_id: "s1" }],
          collectedForms: ["memory"],
        }}
        dictionaryAI={{ kind: "idle" }}
        dictionaryAIPanelOpen={false}
        dictionaryAINoteState={{ kind: "idle" }}
        searchQuery="memory"
        searchExpanded={false}
        onSave={vi.fn()}
        onRequestAI={vi.fn()}
        onCreateAINote={vi.fn()}
        onSelectAISuggestedQuery={vi.fn()}
        onSearchQueryChange={vi.fn()}
        onSearchSubmit={vi.fn()}
        onSelectCandidate={vi.fn()}
        onToggleAIPanel={vi.fn()}
        onToggleSearchExpanded={vi.fn()}
      />,
    );

    const saveButton = screen.getByLabelText("已加入");
    expect(saveButton.className).toContain("hover:bg-transparent");

    fireEvent.click(screen.getByText("+2"));
    expect(screen.getByLabelText("收起考试标签")).toBeTruthy();
  });

  it("keeps phrase_gloss as an inserted reading hint while the rail remains dictionary-led", () => {
    render(
      <ReaderDictionaryDetailPanel
        lookup={createPhraseGlossLookup()}
        readingGoal="general"
        saveState={{ kind: "idle" }}
        dictionaryAI={{ kind: "idle" }}
        dictionaryAIPanelOpen={false}
        dictionaryAINoteState={{ kind: "idle" }}
        searchQuery="policy choices"
        searchExpanded={false}
        onSave={vi.fn()}
        onRequestAI={vi.fn()}
        onCreateAINote={vi.fn()}
        onSelectAISuggestedQuery={vi.fn()}
        onSearchQueryChange={vi.fn()}
        onSearchSubmit={vi.fn()}
        onSelectCandidate={vi.fn()}
        onToggleAIPanel={vi.fn()}
        onToggleSearchExpanded={vi.fn()}
      />,
    );

    expect(screen.getByText("policy choices")).toBeTruthy();
    expect(screen.getByText("解析提示")).toBeTruthy();
    expect(screen.getByText("短语")).toBeTruthy();
    expect(screen.getByText("固定搭配")).toBeTruthy();
    expect(screen.getByText("政策选择")).toBeTruthy();
    expect(screen.getByText("Policy choices shape institutions.")).toBeTruthy();
    expect(screen.getByText("choices about public policy")).toBeTruthy();
    const annotation = screen
      .getByText("解析提示")
      .closest("[data-reader-dictionary-contextual-annotation]");
    expect(annotation).not.toBeNull();
    expect(annotation?.className).toContain("space-y-1.5");
    expect(annotation?.className).not.toContain("border");
    expect(annotation?.className).not.toContain("rounded");
  });

  it("renders phrase_gloss learning_note markdown inside the rail contextual annotation inset", () => {
    const lookup = createPhraseGlossLookup();
    lookup.glossary = {
      ...lookup.glossary,
      learningNote: "注意 `policy choices` 与 `policy decisions` 的区分：前者强调选项，后者强调决策动作。",
    };

    render(
      <ReaderDictionaryDetailPanel
        lookup={lookup}
        readingGoal="general"
        saveState={{ kind: "idle" }}
        dictionaryAI={{ kind: "idle" }}
        dictionaryAIPanelOpen={false}
        dictionaryAINoteState={{ kind: "idle" }}
        searchQuery="policy choices"
        searchExpanded={false}
        onSave={vi.fn()}
        onRequestAI={vi.fn()}
        onCreateAINote={vi.fn()}
        onSelectAISuggestedQuery={vi.fn()}
        onSearchQueryChange={vi.fn()}
        onSearchSubmit={vi.fn()}
        onSelectCandidate={vi.fn()}
        onToggleAIPanel={vi.fn()}
        onToggleSearchExpanded={vi.fn()}
      />,
    );

    const annotation = screen
      .getByText("解析提示")
      .closest("[data-reader-dictionary-contextual-annotation]");
    expect(annotation).not.toBeNull();

    const noteRoot = within(annotation as HTMLElement).getByTestId("learning-note-markdown");
    expect(noteRoot).toBeTruthy();
    const codeNodes = Array.from(noteRoot.querySelectorAll("code"));
    expect(codeNodes.map((node) => node.textContent ?? "")).toEqual([
      "policy choices",
      "policy decisions",
    ]);
    expect(noteRoot.textContent).toContain("前者强调选项");
    expect(noteRoot.textContent).toContain("后者强调决策动作");

    // Example still renders after learning_note so order is subtype → gloss → note → example.
    expect(within(annotation as HTMLElement).getByText("例句")).toBeTruthy();
    expect(within(annotation as HTMLElement).getByText("Policy choices shape institutions.")).toBeTruthy();
  });

  it("omits learning_note chrome in the rail inset when phrase_gloss has no learning_note", () => {
    const lookup = createPhraseGlossLookup();
    // createPhraseGlossLookup ships glossary without learningNote; assert baseline.
    expect(lookup.glossary?.learningNote).toBeUndefined();

    render(
      <ReaderDictionaryDetailPanel
        lookup={lookup}
        readingGoal="general"
        saveState={{ kind: "idle" }}
        dictionaryAI={{ kind: "idle" }}
        dictionaryAIPanelOpen={false}
        dictionaryAINoteState={{ kind: "idle" }}
        searchQuery="policy choices"
        searchExpanded={false}
        onSave={vi.fn()}
        onRequestAI={vi.fn()}
        onCreateAINote={vi.fn()}
        onSelectAISuggestedQuery={vi.fn()}
        onSearchQueryChange={vi.fn()}
        onSearchSubmit={vi.fn()}
        onSelectCandidate={vi.fn()}
        onToggleAIPanel={vi.fn()}
        onToggleSearchExpanded={vi.fn()}
      />,
    );

    const annotation = screen
      .getByText("解析提示")
      .closest("[data-reader-dictionary-contextual-annotation]");
    expect(annotation).not.toBeNull();
    expect(within(annotation as HTMLElement).queryByText("学习提示")).toBeNull();
    expect(within(annotation as HTMLElement).queryByTestId("learning-note-markdown")).toBeNull();
    // Example still renders — only the optional learning_note chrome is suppressed.
    expect(within(annotation as HTMLElement).getByText("例句")).toBeTruthy();
  });

  it.each([
    ["verb_expression", "动词短语"] as const,
    ["fixed_collocation", "固定搭配"] as const,
    ["name_or_term", "专名及术语"] as const,
    ["idiom", "习语"] as const,
  ])(
    "renders the %s subtype label inside the rail contextual annotation inset",
    (phraseType, label) => {
      const lookup = createPhraseGlossLookup();
      lookup.glossary = {
        ...lookup.glossary,
        phraseType,
      };

      render(
        <ReaderDictionaryDetailPanel
          lookup={lookup}
          readingGoal="general"
          saveState={{ kind: "idle" }}
          dictionaryAI={{ kind: "idle" }}
          dictionaryAIPanelOpen={false}
          dictionaryAINoteState={{ kind: "idle" }}
          searchQuery="policy choices"
          searchExpanded={false}
          onSave={vi.fn()}
          onRequestAI={vi.fn()}
          onCreateAINote={vi.fn()}
          onSelectAISuggestedQuery={vi.fn()}
          onSearchQueryChange={vi.fn()}
          onSearchSubmit={vi.fn()}
          onSelectCandidate={vi.fn()}
          onToggleAIPanel={vi.fn()}
          onToggleSearchExpanded={vi.fn()}
        />,
      );

      const annotation = screen
        .getByText("解析提示")
        .closest("[data-reader-dictionary-contextual-annotation]");
      expect(annotation).not.toBeNull();
      expect(within(annotation as HTMLElement).getByText(label)).toBeTruthy();
    },
  );

  it("keeps context_gloss typography blue even when the lookup itself is phrase-shaped", () => {
    render(
      <ReaderDictionaryDetailPanel
        lookup={createContextGlossPhraseLookup()}
        readingGoal="general"
        saveState={{ kind: "idle" }}
        dictionaryAI={{ kind: "idle" }}
        dictionaryAIPanelOpen={false}
        dictionaryAINoteState={{ kind: "idle" }}
        searchQuery="allegedly"
        searchExpanded={false}
        onSave={vi.fn()}
        onRequestAI={vi.fn()}
        onCreateAINote={vi.fn()}
        onSelectAISuggestedQuery={vi.fn()}
        onSearchQueryChange={vi.fn()}
        onSearchSubmit={vi.fn()}
        onSelectCandidate={vi.fn()}
        onToggleAIPanel={vi.fn()}
        onToggleSearchExpanded={vi.fn()}
      />,
    );

    const label = screen.getByText("语境提示");
    const annotation = label.closest("[data-reader-dictionary-contextual-annotation]");
    expect(label.className).toContain("text-context-blue");
    expect(label.className).not.toContain("text-phrase-lavender");
    expect(annotation).not.toBeNull();
    expect(annotation?.className).toContain("space-y-1.5");
    expect(annotation?.className).not.toContain("border");
    if (!annotation) {
      throw new Error("Expected contextual annotation typography");
    }
    expect(within(annotation as HTMLElement).getByText("语境义")).toBeTruthy();
    expect(within(annotation as HTMLElement).queryByText("短语")).toBeNull();
    expect(screen.getByText("据称，据说")).toBeTruthy();
    expect(screen.getByText("根据(人们)宣称")).toBeTruthy();
  });

  it("shows a lighter current-context save action for a new sentence on the same lemma", () => {
    render(
      <ReaderDictionaryDetailPanel
        lookup={createEntryLookup()}
        readingGoal="general"
        saveState={{ kind: "idle" }}
        lookupSaveState="same_lemma_new_context"
        savedVocabularyMatch={{
          id: "vocab-2",
          lemma: "memory",
          displayWord: "memory",
          dictEntryId: 12,
          masteryStatus: "new",
          sourceRefs: [{ source_sentence_id: "s9" }],
          collectedForms: ["memory"],
        }}
        dictionaryAI={{ kind: "idle" }}
        dictionaryAIPanelOpen={false}
        dictionaryAINoteState={{ kind: "idle" }}
        searchQuery="memory"
        searchExpanded={false}
        onSave={vi.fn()}
        onRequestAI={vi.fn()}
        onCreateAINote={vi.fn()}
        onSelectAISuggestedQuery={vi.fn()}
        onSearchQueryChange={vi.fn()}
        onSearchSubmit={vi.fn()}
        onSelectCandidate={vi.fn()}
        onToggleAIPanel={vi.fn()}
        onToggleSearchExpanded={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("加入当前语境")).toBeTruthy();
  });
});
