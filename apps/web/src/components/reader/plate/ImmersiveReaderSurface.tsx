"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { RenderElement, RenderLeaf } from "platejs/react";
import { Plate, usePlateEditor } from "platejs/react";
import { Highlighter, MessageSquare } from "lucide-react";

import type {
  ReaderAssetProjection,
  ReaderAssetRange,
  ReaderContentSummaryNode,
  ReaderJumpRangeSegment,
  ReaderJumpTarget,
  ReaderLookupIntent,
  ReaderLookupPreviewAnchor,
  ReaderParagraphNode,
  ReaderPlateDocument,
  ReaderAnalysisBlockNode,
  ReaderSentenceAssetProjection,
  ReaderSentenceNode,
  ReaderSentenceTextNode,
  ReaderStructuredInspectIntent,
  ReaderTranslationNode,
} from "@/lib/reader-plate";
import type { WebAnnotationVm } from "@/types/api/annotations";
import type { WebReaderNoteVm } from "@/types/api/reader-notes";
import { Editor, EditorContainer } from "../../ui/editor";
import { ReaderMarkLeaf } from "./ReaderMarkLeaf";
import {
  lookupIntentFromTokenClick,
} from "../../../lib/reader-plate";

const IMMERSIVE_VISIBILITY = {
  lexical: true,
  analysis: false,
  userAssets: true,
} as const;

interface ImmersiveReaderSurfaceProps {
  document: ReaderPlateDocument;
  readingClassName: string;
  columnClassName?: string;
  paragraphDensityClassName?: string;
  themeClassName?: string;
  selectionFocusRangesBySentence?: Map<string, ReaderJumpRangeSegment[]>;
  contextFocusRangesBySentence?: Map<string, ReaderJumpRangeSegment[]>;
  jumpTarget?: ReaderJumpTarget | null;
  focusTarget?: ReaderJumpTarget | null;
  hoveredAnnotationTargetKey?: string | null;
  activeInlineMarkKey?: string | null;
  assetProjection?: ReaderAssetProjection | null;
  readerNotesBySentence?: Map<string, WebReaderNoteVm[]>;
  onHoverAnnotationTargetKeyChange?: (targetKey: string | null) => void;
  onOpenSentenceNotes?: (sentenceId: string, anchorEl?: HTMLElement) => void;
  onAnnotationJump?: (annotation: WebAnnotationVm, triggerEl?: HTMLElement, sentenceId?: string) => void;
  onLookupIntent?: (
    intent: ReaderLookupIntent,
    anchor: ReaderLookupPreviewAnchor | null,
    triggerEl?: HTMLElement | null,
  ) => void;
  onInspectIntent?: (
    intent: ReaderStructuredInspectIntent,
    anchor: ReaderLookupPreviewAnchor | null,
    triggerEl?: HTMLElement | null,
  ) => void;
}

interface ImmersiveParagraphCue {
  kind: "note" | "highlight";
  sentenceId: string;
  count: number;
  annotation?: WebAnnotationVm;
}

function focusRangesBySentence(
  target: ReaderJumpTarget | null,
  sentenceTextBySentence: Map<string, string>,
): Map<string, ReaderJumpRangeSegment[]> {
  const map = new Map<string, ReaderJumpRangeSegment[]>();
  if (!target) {
    return map;
  }

  if (target.rangeSegments && target.rangeSegments.length > 0) {
    target.rangeSegments.forEach((segment) => {
      const current = map.get(segment.sentenceId) ?? [];
      map.set(segment.sentenceId, [...current, segment]);
    });
    return map;
  }

  target.sentenceIds.forEach((sentenceId) => {
    const sentenceText = sentenceTextBySentence.get(sentenceId);
    if (!sentenceText) {
      return;
    }
    map.set(sentenceId, [
      {
        sentenceId,
        paragraphId: target.paragraphIds?.[0] ?? null,
        selectedText: sentenceText,
        startOffset: 0,
        endOffset: sentenceText.length,
        textHash: null,
      },
    ]);
  });

  return map;
}

function ImmersiveSentenceTextElement({
  leadSentence = false,
  onLookupIntent,
  props,
  readingClassName,
  sourceContext,
}: {
  leadSentence?: boolean;
  props: Parameters<RenderElement>[0];
  readingClassName: string;
  sourceContext?: string;
  onLookupIntent?: (
    intent: ReaderLookupIntent,
    anchor: ReaderLookupPreviewAnchor | null,
    triggerEl?: HTMLElement | null,
  ) => void;
}) {
  const sentenceTextElement = props.element as unknown as {
    sentenceId: string;
    children?: Array<{ text?: string }>;
  };

  return (
    <span
      {...props.attributes}
      className={`${readingClassName} reader-immersive-sentence-text ${leadSentence ? "reader-immersive-sentence-text--lead" : ""}`.trim()}
      data-reader-node="sentence-text"
      data-reader-sentence-text="true"
      tabIndex={-1}
      onClick={(event) => {
        if (!onLookupIntent) {
          return;
        }

        const selection = window.getSelection();
        if (selection && !selection.isCollapsed && selection.toString().trim()) {
          return;
        }

        const currentTarget = event.currentTarget;
        const sentenceText = sentenceTextElement.children?.map((child) => child.text ?? "").join("") ?? "";
        if (!sentenceText) {
          return;
        }

        const result = lookupIntentFromTokenClick({
          element: currentTarget,
          sentence: {
            sentenceId: sentenceTextElement.sentenceId,
            text: sentenceText,
          },
          sourceContext,
          clientX: event.clientX,
          clientY: event.clientY,
        });

        if (!result) {
          return;
        }

        event.stopPropagation();
        currentTarget.focus({ preventScroll: true });
        onLookupIntent(result.intent, result.anchor, currentTarget);
      }}
    >
      {props.children}
    </span>
  );
}

function ImmersiveReaderParagraphElement({
  props,
  isLead,
  routeFocused,
  cue,
  onCueAction,
}: {
  props: Parameters<RenderElement>[0];
  isLead: boolean;
  routeFocused: boolean;
  cue: ImmersiveParagraphCue | null;
  onCueAction?: (cue: ImmersiveParagraphCue, triggerEl: HTMLElement) => void;
}) {
  const element = props.element as unknown as ReaderParagraphNode;

  return (
    <section
      {...props.attributes}
      className={`reader-immersive-paragraph ${isLead ? "reader-immersive-paragraph--lead" : ""} ${routeFocused ? "reader-route-focus-frame" : ""}`.trim()}
      data-reader-node="paragraph"
      data-paragraph-id={element.paragraphId}
    >
      {cue ? (
        <button
          type="button"
          className={`reader-immersive-paragraph-cue reader-immersive-paragraph-cue--${cue.kind}`}
          aria-label={
            cue.kind === "note"
              ? cue.count > 1
                ? `打开本段相关的 ${cue.count} 条笔记`
                : "打开本段相关笔记"
              : cue.count > 1
                ? `查看本段相关的 ${cue.count} 处高亮`
                : "查看本段相关高亮"
          }
          onClick={(event) => {
            onCueAction?.(cue, event.currentTarget);
          }}
        >
          {cue.kind === "note" ? (
            <MessageSquare aria-hidden="true" className="h-3.5 w-3.5" />
          ) : (
            <Highlighter aria-hidden="true" className="h-3.5 w-3.5" />
          )}
          {cue.count > 1 ? (
            <span className="reader-immersive-paragraph-cue-count" aria-hidden="true">
              {cue.count}
            </span>
          ) : null}
        </button>
      ) : null}
      <div
        className={`reader-immersive-paragraph-copy ${isLead ? "reader-immersive-paragraph-copy--lead" : ""}`.trim()}
      >
        {props.children}
      </div>
    </section>
  );
}

export function ImmersiveReaderSurface({
  document,
  readingClassName,
  columnClassName = "max-w-[68ch]",
  paragraphDensityClassName = "reader-density-immersive",
  themeClassName,
  selectionFocusRangesBySentence = new Map<string, ReaderJumpRangeSegment[]>(),
  contextFocusRangesBySentence = new Map<string, ReaderJumpRangeSegment[]>(),
  jumpTarget = null,
  focusTarget = null,
  hoveredAnnotationTargetKey = null,
  activeInlineMarkKey = null,
  assetProjection = null,
  readerNotesBySentence = new Map<string, WebReaderNoteVm[]>(),
  onHoverAnnotationTargetKeyChange,
  onOpenSentenceNotes,
  onAnnotationJump,
  onLookupIntent,
  onInspectIntent,
}: ImmersiveReaderSurfaceProps) {
  const paragraphNodes = useMemo(
    () => document.children.filter((node): node is ReaderParagraphNode => node.type === "reader_paragraph"),
    [document.children],
  );

  const sentenceTextBySentence = useMemo(() => {
    const map = new Map<string, string>();
    paragraphNodes.forEach((paragraph) => {
      paragraph.children.forEach((sentenceNode) => {
        const sentenceTextNode = sentenceNode.children.find(
          (child): child is ReaderSentenceTextNode => child.type === "reader_sentence_text",
        );
        if (!sentenceTextNode) {
          return;
        }
        map.set(
          sentenceNode.sentenceId,
          sentenceTextNode.children.map((leaf) => leaf.text).join(""),
        );
      });
    });
    return map;
  }, [paragraphNodes]);

  const sourceContextBySentence = useMemo(() => {
    const map = new Map<string, string | undefined>();
    paragraphNodes.forEach((paragraph) => {
      paragraph.children.forEach((sentenceNode) => {
        const translationNode = sentenceNode.children.find((child) => child.type === "reader_translation");
        map.set(
          sentenceNode.sentenceId,
          translationNode?.type === "reader_translation" ? translationNode.translationZh : undefined,
        );
      });
    });
    return map;
  }, [paragraphNodes]);

  const sentenceAssetsBySentence = useMemo(
    () =>
      assetProjection?.sentenceAssetProjectionBySentence ??
      new Map<string, ReaderSentenceAssetProjection>(),
    [assetProjection],
  );

  const assetRangesBySentence = useMemo(() => {
    const map = new Map<string, ReaderAssetRange[]>();
    assetProjection?.annotationRangesBySentence?.forEach((ranges, sentenceId) => {
      map.set(sentenceId, [...ranges]);
    });
    return map;
  }, [assetProjection?.annotationRangesBySentence]);
  const [hoveredInlineMarkKey, setHoveredInlineMarkKey] = useState<string | null>(null);
  const [focusedInlineMarkKey, setFocusedInlineMarkKey] = useState<string | null>(null);

  const jumpFocusRangesBySentence = useMemo(
    () => focusRangesBySentence(jumpTarget, sentenceTextBySentence),
    [jumpTarget, sentenceTextBySentence],
  );

  const noteFocusRangesBySentence = useMemo(
    () => focusRangesBySentence(focusTarget, sentenceTextBySentence),
    [focusTarget, sentenceTextBySentence],
  );

  const sentenceLayoutBySentence = useMemo(() => {
    const map = new Map<string, { paragraphId: string; isLast: boolean; isLead: boolean }>();
    paragraphNodes.forEach((paragraph, paragraphIndex) => {
      paragraph.children.forEach((sentenceNode, sentenceIndex) => {
        map.set(sentenceNode.sentenceId, {
          paragraphId: paragraph.paragraphId,
          isLast: sentenceIndex === paragraph.children.length - 1,
          isLead: paragraphIndex === 0 && sentenceIndex === 0,
        });
      });
    });
    return map;
  }, [paragraphNodes]);

  const paragraphCueById = useMemo(() => {
    const cues = new Map<string, ImmersiveParagraphCue>();

    paragraphNodes.forEach((paragraph) => {
      const notes = paragraph.sentenceIds.flatMap((sentenceId) => readerNotesBySentence.get(sentenceId) ?? []);
      if (notes.length > 0) {
        const firstNote = notes[0];
        cues.set(paragraph.paragraphId, {
          kind: "note",
          sentenceId: firstNote.anchorSentenceId ?? paragraph.sentenceIds[0] ?? "",
          count: notes.length,
        });
        return;
      }

      const annotations = new Map<string, WebAnnotationVm>();
      paragraph.sentenceIds.forEach((sentenceId) => {
        const sentenceProjection = sentenceAssetsBySentence.get(sentenceId);
        sentenceProjection?.annotations.forEach((annotation) => {
          annotations.set(annotation.id, annotation);
        });
      });

      const firstAnnotation = annotations.values().next().value as WebAnnotationVm | undefined;
      if (firstAnnotation) {
        cues.set(paragraph.paragraphId, {
          kind: "highlight",
          sentenceId: firstAnnotation.sentenceId ?? paragraph.sentenceIds[0] ?? "",
          count: annotations.size,
          annotation: firstAnnotation,
        });
      }
    });

    return cues;
  }, [paragraphNodes, readerNotesBySentence, sentenceAssetsBySentence]);

  const routeFocusParagraphIds = useMemo(
    () => new Set(jumpTarget?.paragraphIds ?? []),
    [jumpTarget],
  );

  const routeFocusSentenceIds = useMemo(
    () => new Set(jumpTarget?.sentenceIds ?? []),
    [jumpTarget],
  );

  const editor = usePlateEditor(
    {
      value: document.children as never[],
    },
    [],
  );

  useEffect(() => {
    if (editor.children !== document.children) {
      editor.tf.setValue(document.children as never[]);
    }
  }, [document.children, editor]);

  const renderElement = useCallback(
    (props: Parameters<RenderElement>[0]) => {
      const element = props.element as unknown as
        | ReaderContentSummaryNode
        | ReaderParagraphNode
        | ReaderSentenceNode
        | ReaderSentenceTextNode
        | ReaderTranslationNode
        | ReaderAnalysisBlockNode;

      switch (element.type) {
        case "reader_content_summary":
          return null;
        case "reader_paragraph":
          return (
            <ImmersiveReaderParagraphElement
              props={props}
              isLead={paragraphNodes[0]?.paragraphId === element.paragraphId}
              routeFocused={routeFocusParagraphIds.has(element.paragraphId)}
              cue={paragraphCueById.get(element.paragraphId) ?? null}
              onCueAction={(cue, triggerEl) => {
                if (cue.kind === "note") {
                  onOpenSentenceNotes?.(cue.sentenceId, triggerEl);
                  return;
                }

                if (cue.annotation) {
                  onAnnotationJump?.(cue.annotation, triggerEl, cue.sentenceId);
                }
              }}
            />
          );
        case "reader_sentence": {
          const sentenceLayout = sentenceLayoutBySentence.get(element.sentenceId);
          return (
            <span
              {...props.attributes}
              id={`reader-sentence-${element.sentenceId}`}
              className={`reader-immersive-sentence ${routeFocusSentenceIds.has(element.sentenceId) ? "reader-immersive-sentence--route-focused" : ""}`.trim()}
              data-reader-anchor="sentence"
              data-reader-node="sentence"
              data-paragraph-id={element.paragraphId}
              data-sentence-id={element.sentenceId}
            >
              {props.children}
              {!sentenceLayout?.isLast ? (
                <span aria-hidden="true" className="reader-immersive-sentence-gap">
                  {" "}
                </span>
              ) : null}
            </span>
          );
        }
        case "reader_sentence_text": {
          const sentenceLayout = sentenceLayoutBySentence.get(element.sentenceId);
          return (
            <ImmersiveSentenceTextElement
              props={props}
              leadSentence={Boolean(sentenceLayout?.isLead)}
              readingClassName={readingClassName}
              sourceContext={sourceContextBySentence.get(element.sentenceId)}
              onLookupIntent={onLookupIntent}
            />
          );
        }
        case "reader_translation":
        case "reader_grammar_note":
        case "reader_sentence_analysis":
        case "reader_term_note":
        case "reader_logic_note":
        case "reader_interpretation_note":
          return null;
        default:
          return <span {...props.attributes}>{props.children}</span>;
      }
    },
    [
      onAnnotationJump,
      onLookupIntent,
      onOpenSentenceNotes,
      paragraphCueById,
      paragraphNodes,
      readingClassName,
      routeFocusParagraphIds,
      routeFocusSentenceIds,
      sentenceLayoutBySentence,
      sourceContextBySentence,
    ],
  );

  const renderLeaf = useCallback(
    (props: Parameters<RenderLeaf>[0]) => (
      <ReaderMarkLeaf
        annotationRangesBySentence={assetRangesBySentence}
        annotationVisibilityGroups={IMMERSIVE_VISIBILITY}
        onInspectIntent={onInspectIntent}
        onLookupIntent={onLookupIntent}
        props={props}
        analysisSegmentsBySentence={new Map()}
        jumpFocusRangesBySentence={jumpFocusRangesBySentence}
        selectionFocusRangesBySentence={selectionFocusRangesBySentence}
        contextFocusRangesBySentence={contextFocusRangesBySentence}
        noteFocusRangesBySentence={noteFocusRangesBySentence}
        hoveredAnnotationTargetKey={hoveredAnnotationTargetKey}
        hoveredInlineMarkKey={hoveredInlineMarkKey}
        focusedInlineMarkKey={focusedInlineMarkKey}
        activeInlineMarkKey={activeInlineMarkKey}
        onHoverAnnotationTargetKeyChange={onHoverAnnotationTargetKeyChange}
        onHoverInlineMarkKeyChange={setHoveredInlineMarkKey}
        onFocusInlineMarkKeyChange={setFocusedInlineMarkKey}
        activeAnalysisEntryId={null}
        expandedAnalysisEntryIds={new Set()}
        sentenceTextBySentence={sentenceTextBySentence}
        sourceContextBySentence={sourceContextBySentence}
      />
    ),
    [
      assetRangesBySentence,
      activeInlineMarkKey,
      hoveredAnnotationTargetKey,
      hoveredInlineMarkKey,
      focusedInlineMarkKey,
      jumpFocusRangesBySentence,
      noteFocusRangesBySentence,
      onHoverAnnotationTargetKeyChange,
      onInspectIntent,
      onLookupIntent,
      selectionFocusRangesBySentence,
      contextFocusRangesBySentence,
      sentenceTextBySentence,
      sourceContextBySentence,
    ],
  );

  return (
    <div
      className={`reader-immersive-stage px-5 py-8 sm:px-8 sm:py-9 lg:px-10 lg:py-12 ${themeClassName ?? ""} ${paragraphDensityClassName}`.trim()}
    >
      <div className={`mx-auto ${columnClassName}`.trim()}>
        <Plate editor={editor} readOnly>
          <EditorContainer className="h-auto cursor-default overflow-visible bg-transparent px-0 py-0 [&_.slate-selection-area]:hidden">
            <Editor
              readOnly
              disableDefaultStyles
              className="space-y-0 px-0 py-0 outline-none"
              renderElement={renderElement as never}
              renderLeaf={renderLeaf as never}
            />
          </EditorContainer>
        </Plate>
      </div>
    </div>
  );
}
