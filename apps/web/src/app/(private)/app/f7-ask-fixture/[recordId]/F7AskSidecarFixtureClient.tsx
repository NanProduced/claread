"use client";

import { useCallback, useState } from "react";

import { AiWorkspacePanel } from "@/components/reader/AiWorkspacePanel";
import type {
  ReaderAskAttachment,
  ReaderAskPageIdentity,
} from "@/lib/reader-plate";

const FIXTURE_RECORD_TITLE = "F7 Ask Sidecar Fixture";
const FIXTURE_ARTICLE_TEXT =
  "Institutional memory shapes policy choices in subtle ways. " +
  "A scarce few can turn passion into a stable income, but most simply adapt. " +
  "The city was built to be read, not only to be crossed.";

const FIXTURE_ATTACHMENT_TARGET_KEY = "record:f7-ask-fixture:sentence:s1";

export function F7AskSidecarFixtureClient({
  recordId,
}: {
  recordId: string;
}) {
  const [askOpen, setAskOpen] = useState(true);

  const pageIdentity: ReaderAskPageIdentity = {
    recordId,
    recordTitle: FIXTURE_RECORD_TITLE,
    surface: "reader",
    source: "reader_2_0",
    availableContextCapabilities: ["record_context"],
    hasArticleOverview: false,
    hasSentenceEntries: true,
    hasAnnotations: false,
    hasReaderNotes: false,
  };

  // Pre-populate a text_selection attachment so the fixture simulates the
  // real reader-record flow without depending on the Plate selection toolbar.
  const [attachments, setAttachments] = useState<ReaderAskAttachment[]>(() => [
    {
      kind: "text_selection",
      subtype: "sentence",
      label: "Institutional memory shapes policy choices...",
      selectedText: FIXTURE_ARTICLE_TEXT.slice(0, 50),
      targetKey: FIXTURE_ATTACHMENT_TARGET_KEY,
      metadata: {
        pageIdentity: {
          recordId,
          recordTitle: FIXTURE_RECORD_TITLE,
          surface: "reader",
          source: "reader_2_0",
          availableContextCapabilities: ["record_context"],
          hasSentenceEntries: true,
        },
        sourceSurface: "reader",
        entryAction: "ask_about_this",
        sentenceId: "s1",
        paragraphId: "u1",
      },
    },
  ]);

  const handleAddAttachment = useCallback(() => {
    setAttachments((current) => {
      if (current.some((a) => a.targetKey === FIXTURE_ATTACHMENT_TARGET_KEY)) {
        return current;
      }
      const attachment: ReaderAskAttachment = {
        kind: "text_selection",
        subtype: "sentence",
        label: "Institutional memory shapes policy choices...",
        selectedText: FIXTURE_ARTICLE_TEXT.slice(0, 50),
        targetKey: FIXTURE_ATTACHMENT_TARGET_KEY,
        metadata: {
          pageIdentity,
          sourceSurface: "reader",
          entryAction: "ask_about_this",
          sentenceId: "s1",
          paragraphId: "u1",
        },
      };
      return [...current, attachment];
    });
  }, [pageIdentity]);

  return (
    <main
      className="min-h-screen text-ink"
      data-f7-fixture-page="ask-sidecar"
      data-f7-fixture-record-id={recordId}
    >
      <div className="mx-auto max-w-[72ch] px-5 py-8 sm:px-8 lg:py-10">
        <header className="mb-6">
          <p className="text-xs font-semibold tracking-[0.12em] text-lens-blue">
            F7 Fixture - Test-only
          </p>
          <h1 className="mt-2 font-headline text-xl font-semibold text-ink sm:text-2xl">
            Ask Sidecar Fixture
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Page-level e2e fixture for Ask article_rag sidecar integration.
            Renders only AiWorkspacePanel, no Plate editor or FloatingToolbar.
          </p>
        </header>

        <section className="rounded-note border border-hairline bg-surface p-6 shadow-surface-quiet">
          <p className="text-sm text-muted-foreground">Record ID</p>
          <p className="font-mono text-sm text-ink">{recordId}</p>

          <p className="mt-4 text-sm text-muted-foreground">Article preview</p>
          <p className="mt-1 text-sm text-ink">{FIXTURE_ARTICLE_TEXT}</p>

          <div className="mt-4 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => setAskOpen(true)}
              className="rounded-md border border-hairline bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-muted"
              data-f7-fixture-action="open-ask"
            >
              打开 Ask 面板
            </button>
            <button
              type="button"
              onClick={handleAddAttachment}
              className="rounded-md border border-hairline bg-surface px-3 py-1.5 text-xs font-medium text-ink hover:bg-muted"
              data-f7-fixture-action="add-attachment"
            >
              添加选区附件
            </button>
          </div>
        </section>

        <AiWorkspacePanel
          open={askOpen}
          presentation="intensive"
          pageIdentity={pageIdentity}
          recordId={recordId}
          recordScope="reading_record"
          hideClosedLauncher
          recordTitle={FIXTURE_RECORD_TITLE}
          attachments={attachments}
          onRemoveAttachment={(key) =>
            setAttachments((current) =>
              current.filter((a) => (a.targetKey ?? "") !== key),
            )
          }
          onClearAttachments={() => setAttachments([])}
          onToggle={() => setAskOpen(false)}
        />
      </div>
    </main>
  );
}
