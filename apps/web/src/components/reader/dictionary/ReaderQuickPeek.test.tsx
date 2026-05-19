/** @vitest-environment jsdom */

import { fireEvent, render, screen } from "@testing-library/react";
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

    expect(screen.getByText("memory")).toBeTruthy();
    expect(screen.getByText("正在查词...")).toBeTruthy();

    fireEvent.click(screen.getByText("打开详情"));
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

    expect(screen.getByText("结构化解释")).toBeTruthy();
    expect(screen.getByText("policy choices")).toBeTruthy();
    expect(screen.getByText("政策选择")).toBeTruthy();

    fireEvent.click(screen.getByText("查短语"));
    expect(onLookupPhrase).toHaveBeenCalled();

    fireEvent.click(screen.getByText("带入 Ask"));
    expect(onAttachToAsk).toHaveBeenCalled();
  });
});
