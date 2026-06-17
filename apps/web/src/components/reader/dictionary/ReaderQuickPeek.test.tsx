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
            phraseType: "collocation",
            reason: "这里强调固定搭配。",
          },
          title: "固定搭配",
          label: "固定搭配",
        }}
        onDismiss={onDismiss}
        onAttachToAsk={onAttachToAsk}
        onLookupPhrase={onLookupPhrase}
      />,
    );

    const inspectDialog = screen.getAllByRole("dialog")[1];
    const inspectView = within(inspectDialog);

    expect(inspectDialog).toBeTruthy();
    expect(inspectView.getByText("固定搭配")).toBeTruthy();
    expect(inspectView.getAllByText("policy choices").length).toBeGreaterThan(0);
    expect(inspectView.getByText("政策选择")).toBeTruthy();

    fireEvent.click(inspectView.getByLabelText("查短语"));
    expect(onLookupPhrase).toHaveBeenCalled();

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
            phraseType: "collocation",
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
});
