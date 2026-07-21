/** @vitest-environment jsdom */

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ReaderQuickPeek } from "./ReaderQuickPeek";

describe("ReaderQuickPeek", () => {
  it("renders compact lookup states", () => {
    const onDismiss = vi.fn();
    const onOpenDetail = vi.fn();

    render(
      <ReaderQuickPeek
        lookup={{
          query: "memory",
          lookupType: "word",
          contextSentence: "Institutional memory shapes policy choices.",
          recordId: "record-1",
          sentenceId: "s1",
          anchorText: "memory",
          title: "查词",
          state: { kind: "loading" },
        }}
        onDismiss={onDismiss}
        onOpenDetail={onOpenDetail}
      />,
    );

    expect(screen.getAllByRole("dialog")[0]).toBeTruthy();
    expect(screen.getByText("memory")).toBeTruthy();
    expect(screen.getByText("正在查词...")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("打开词典"));
    expect(onOpenDetail).toHaveBeenCalled();
  });

  it("renders structured inspect content and secondary actions", () => {
    const onDismiss = vi.fn();
    const onLookupPhrase = vi.fn();
    const onOpenDetail = vi.fn();
    const onAttachToAsk = vi.fn();

    render(
      <ReaderQuickPeek
        inspect={{
          kind: "structured_annotation_inspect",
          sentenceId: "s1",
          contextSentence: "Institutional memory shapes policy choices.",
          markId: "mark-phrase",
          annotationType: "phrase_gloss",
          visualTone: "phrase",
          anchorText: "policy choices",
          lookupText: "policy choices",
          glossary: {
            zh: "政策选择",
            phraseType: "fixed_collocation",
            example: "Institutional memory shapes policy choices.",
            reason: "这里强调固定搭配。",
          },
          title: "固定搭配",
          label: "固定搭配",
        }}
        onDismiss={onDismiss}
        onAttachToAsk={onAttachToAsk}
        onLookupPhrase={onLookupPhrase}
        onOpenDetail={onOpenDetail}
      />,
    );

    const inspectDialog = screen.getAllByRole("dialog")[1];
    const inspectView = within(inspectDialog);

    expect(inspectDialog).toBeTruthy();
    expect(inspectView.getByText("固定搭配")).toBeTruthy();
    expect(inspectView.getAllByText("policy choices").length).toBeGreaterThan(0);
    expect(inspectView.getByText("政策选择")).toBeTruthy();
    expect(inspectView.getByText("Institutional memory shapes policy choices.")).toBeTruthy();
    expect(inspectView.queryByText(/依据：/)).toBeNull();
    expect(inspectView.getByText("这里强调固定搭配。")).toBeTruthy();

    expect(inspectView.queryByLabelText("查短语")).toBeNull();

    fireEvent.click(inspectView.getByLabelText("打开词典"));
    expect(onOpenDetail).toHaveBeenCalled();
    expect(onLookupPhrase).not.toHaveBeenCalled();

    fireEvent.click(inspectView.getByLabelText("带入 Ask"));
    expect(onAttachToAsk).toHaveBeenCalled();
  });

  it("prefers lookupText as the structured inspect title for multi_text phrases", () => {
    render(
      <ReaderQuickPeek
        inspect={{
          kind: "structured_annotation_inspect",
          sentenceId: "s1",
          contextSentence: "People often refer to this pattern as a shortcut.",
          markId: "mark-multi",
          annotationType: "phrase_gloss",
          visualTone: "phrase",
          anchorText: "refer to",
          lookupText: "refer to ... as",
          glossary: {
            zh: "把……称作……",
            phraseType: "fixed_collocation",
          },
          title: "固定搭配",
          label: "固定搭配",
        }}
        onDismiss={vi.fn()}
        onAttachToAsk={vi.fn()}
        onLookupPhrase={vi.fn()}
      />,
    );

    const inspectDialog = screen.getAllByRole("dialog").at(-1);
    expect(inspectDialog).toBeTruthy();
    expect(within(inspectDialog!).getByText("refer to ... as")).toBeTruthy();
  });

  it("offers AI fallback when the dictionary has no entry", () => {
    const onRequestAI = vi.fn();

    render(
      <ReaderQuickPeek
        lookup={{
          query: "restrainful",
          lookupType: "word",
          contextSentence: "The design felt strangely restrainful.",
          recordId: "record-1",
          sentenceId: "s2",
          anchorText: "restrainful",
          title: "查词",
          state: {
            kind: "ready",
            result: {
              kind: "not_found",
              query: "restrainful",
              provider: "mock-dict",
              cached: false,
              reason: "not_in_dictionary",
            },
          },
        }}
        onDismiss={vi.fn()}
        onOpenDetail={vi.fn()}
        onRequestAI={onRequestAI}
        dictionaryAI={{ kind: "idle" }}
      />,
    );

    const dialogs = screen.getAllByRole("dialog");
    const fallbackDialog = within(dialogs[dialogs.length - 1]!);

    expect(fallbackDialog.getByText("当前词典暂未收录，可用 AI 补充词义。")).toBeTruthy();
    fireEvent.click(fallbackDialog.getByLabelText("词典未收录，试试 AI"));
    expect(onRequestAI).toHaveBeenCalledWith("missing_fallback");
  });

  it("lets users choose compact disambiguation candidates in Quick Peek", () => {
    const onSelectCandidate = vi.fn();

    render(
      <ReaderQuickPeek
        lookup={{
          query: "men",
          lookupType: "word",
          contextSentence: "Several men entered the arena.",
          recordId: "record-1",
          sentenceId: "s3",
          anchorText: "men",
          title: "查词",
          state: {
            kind: "ready",
            result: {
              kind: "disambiguation",
              query: "men",
              provider: "mock-dict",
              cached: false,
              ambiguityKind: "lemma_competing",
              selectionRequired: true,
              candidates: [
                {
                  entryId: 1,
                  label: "man",
                  preview: "男人；人类",
                  entryKind: "entry",
                  matchKind: "lemma",
                  lookupType: "word",
                  candidateKind: "variant",
                },
                {
                  entryId: 2,
                  label: "men",
                  preview: "复数形式",
                  entryKind: "entry",
                  matchKind: "exact",
                  lookupType: "word",
                  candidateKind: "word",
                },
              ],
            },
          },
        }}
        onDismiss={vi.fn()}
        onOpenDetail={vi.fn()}
        onSelectCandidate={onSelectCandidate}
      />,
    );

    const dialog = within(screen.getAllByRole("dialog").at(-1)!);
    expect(dialog.queryByText("多个候选词条，打开详情继续选择。")).toBeNull();
    fireEvent.click(dialog.getByText("man"));
    expect(onSelectCandidate).toHaveBeenCalledWith(1);
  });

  it("keeps context_gloss lookup snapshots blue even when lookup type is phrase", () => {
    render(
      <ReaderQuickPeek
        lookup={{
          query: "allegedly",
          lookupType: "phrase",
          contextSentence: "Today is allegedly the start of it all.",
          recordId: "record-1",
          sentenceId: "s4",
          anchorText: "allegedly",
          title: "语境义",
          label: "语境义",
          annotationType: "context_gloss",
          visualTone: "context",
          glossary: {
            gloss: "据称，据说",
            reason: "这里提示作者保留了事实确认空间。",
          },
          state: {
            kind: "ready",
            result: {
              kind: "entry",
              query: "allegedly",
              provider: "mock-dict",
              cached: false,
              entry: {
                id: 8,
                word: "allegedly",
                baseWord: "allegedly",
                phonetic: undefined,
                meanings: [
                  {
                    partOfSpeech: "adv.",
                    definitions: [{ meaning: "据称，据说" }],
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
        }}
        onDismiss={vi.fn()}
        onOpenDetail={vi.fn()}
      />,
    );

    const dialog = within(screen.getAllByRole("dialog").at(-1)!);
    const labels = dialog.getAllByText("语境义");
    expect(labels.length).toBeGreaterThan(0);
    for (const label of labels) {
      expect(label.className).toContain("text-context-blue");
      expect(label.className).not.toContain("text-phrase-lavender");
    }
  });

  it.each([
    ["verb_expression", "动词短语"] as const,
    ["fixed_collocation", "固定搭配"] as const,
    ["name_or_term", "专名及术语"] as const,
    ["idiom", "习语"] as const,
  ])("shows Chinese subtype label for %s", (phraseType, label) => {
    render(
      <ReaderQuickPeek
        inspect={{
          kind: "structured_annotation_inspect",
          sentenceId: "s1",
          contextSentence: "Sample sentence for phrase type labels.",
          markId: `mark-${phraseType}`,
          annotationType: "phrase_gloss",
          visualTone: "phrase",
          anchorText: "sample phrase",
          lookupText: "sample phrase",
          glossary: {
            gloss: "示例释义",
            phraseType,
          },
          title: label,
          label,
        }}
        onDismiss={vi.fn()}
      />,
    );

    const dialog = within(screen.getAllByRole("dialog").at(-1)!);
    expect(dialog.getByText(label)).toBeTruthy();
    expect(dialog.getByText("示例释义")).toBeTruthy();
  });

  it("renders learning_note markdown when present and omits empty optional chrome when absent", () => {
    const { rerender } = render(
      <ReaderQuickPeek
        inspect={{
          kind: "structured_annotation_inspect",
          sentenceId: "s1",
          contextSentence: "She gave up smoking last year.",
          markId: "mark-give-up",
          annotationType: "phrase_gloss",
          visualTone: "phrase",
          anchorText: "gave up",
          lookupText: "give up",
          glossary: {
            gloss: "放弃；戒掉",
            phraseType: "verb_expression",
            learningNote: "常见搭配：`give up` + 名词。\n- 不要写成 give uping",
            example: "She gave up smoking.",
          },
          title: "动词短语",
          label: "动词短语",
        }}
        onDismiss={vi.fn()}
      />,
    );

    let dialogEl = screen.getAllByRole("dialog").at(-1)!;
    let dialog = within(dialogEl);
    expect(dialog.getByText("放弃；戒掉")).toBeTruthy();
    expect(dialog.getByText("例句")).toBeTruthy();
    expect(dialog.getByText("She gave up smoking.")).toBeTruthy();
    // Streamdown renders inline code as a code element
    expect(dialogEl.querySelector("code")?.textContent).toBe("give up");
    expect(dialog.getByText(/不要写成 give uping/)).toBeTruthy();

    rerender(
      <ReaderQuickPeek
        inspect={{
          kind: "structured_annotation_inspect",
          sentenceId: "s1",
          contextSentence: "She gave up smoking last year.",
          markId: "mark-give-up",
          annotationType: "phrase_gloss",
          visualTone: "phrase",
          anchorText: "gave up",
          lookupText: "give up",
          glossary: {
            gloss: "放弃；戒掉",
            phraseType: "verb_expression",
          },
          title: "动词短语",
          label: "动词短语",
        }}
        onDismiss={vi.fn()}
      />,
    );

    dialogEl = screen.getAllByRole("dialog").at(-1)!;
    dialog = within(dialogEl);
    expect(dialog.getByText("放弃；戒掉")).toBeTruthy();
    expect(dialog.queryByText("例句")).toBeNull();
    expect(dialog.queryByText("She gave up smoking.")).toBeNull();
    expect(dialogEl.querySelector("code")).toBeNull();
  });

  it("renders learning_note markdown in the lookup path when the dictionary panel is open", () => {
    render(
      <ReaderQuickPeek
        lookup={{
          query: "policy choices",
          lookupType: "phrase",
          contextSentence: "Institutional memory shapes policy choices.",
          recordId: "record-1",
          sentenceId: "s1",
          anchorText: "policy choices",
          title: "短语",
          label: "短语",
          annotationType: "phrase_gloss",
          visualTone: "phrase",
          glossary: {
            gloss: "政策选择",
            phraseType: "fixed_collocation",
            learningNote: "注意 `policy choices` 与 `policy decisions` 的区分。",
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
                    definitions: [
                      { meaning: "choices about public policy" },
                    ],
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
        }}
        onDismiss={vi.fn()}
        onOpenDetail={vi.fn()}
      />,
    );

    const dialogEl = screen.getAllByRole("dialog").at(-1)!;
    const dialog = within(dialogEl);
    // subtype label is shown alongside the phrase_gloss eyebrow
    expect(dialog.getAllByText("固定搭配").length).toBeGreaterThan(0);
    // gloss remains the primary content
    expect(dialog.getByText("政策选择")).toBeTruthy();
    // learning_note label and markdown content render in the lookup branch
    expect(dialog.getByText("学习提示")).toBeTruthy();
    const noteRoot = dialogEl.querySelector('[data-testid="learning-note-markdown"]');
    expect(noteRoot).toBeTruthy();
    const codeNodes = Array.from(noteRoot!.querySelectorAll("code"));
    expect(codeNodes.map((node) => node.textContent ?? "")).toEqual([
      "policy choices",
      "policy decisions",
    ]);
    // example still renders after learning_note
    expect(dialog.getByText("例句")).toBeTruthy();
    expect(dialog.getByText("Policy choices shape institutions.")).toBeTruthy();
  });

  it("omits learning_note chrome in the lookup path when phrase_gloss has no learning_note", () => {
    render(
      <ReaderQuickPeek
        lookup={{
          query: "policy choices",
          lookupType: "phrase",
          contextSentence: "Institutional memory shapes policy choices.",
          recordId: "record-1",
          sentenceId: "s1",
          anchorText: "policy choices",
          title: "短语",
          label: "短语",
          annotationType: "phrase_gloss",
          visualTone: "phrase",
          glossary: {
            gloss: "政策选择",
            phraseType: "fixed_collocation",
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
                    definitions: [
                      { meaning: "choices about public policy" },
                    ],
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
        }}
        onDismiss={vi.fn()}
        onOpenDetail={vi.fn()}
      />,
    );

    const dialogEl = screen.getAllByRole("dialog").at(-1)!;
    const dialog = within(dialogEl);
    expect(dialog.getAllByText("固定搭配").length).toBeGreaterThan(0);
    expect(dialog.getByText("政策选择")).toBeTruthy();
    expect(dialog.queryByText("学习提示")).toBeNull();
    expect(dialogEl.querySelector('[data-testid="learning-note-markdown"]')).toBeNull();
    // glossary had no example either — no example chrome should leak.
    expect(dialog.queryByText("例句")).toBeNull();
  });
});
