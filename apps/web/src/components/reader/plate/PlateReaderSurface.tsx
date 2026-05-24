"use client";

import { useCallback, useEffect, useMemo } from "react";
import {
  Plate,
  usePlateEditor,
} from "platejs/react";
import type {
  ReaderAnalysisBlockNode,
  ReaderAssetProjection,
  ReaderAssetRange,
  ReaderSentenceAssetProjection,
  ReaderJumpRangeSegment,
  ReaderJumpTarget,
  ReaderLookupIntent,
  ReaderLookupPreviewAnchor,
  ReaderStructuredInspectIntent,
  ReaderContentSummaryNode,
  ReaderParagraphNode,
  ReaderPlateDocument,
  ReaderTranslationNode,
  ReaderSentenceNode,
  ReaderSentenceTextNode,
} from "@/lib/reader-plate";
import type { WebAnnotationVm } from "@/types/api/annotations";
import type { WebReaderNoteVm } from "@/types/api/reader-notes";
import { Editor, EditorContainer } from "../../ui/editor";
import { ReaderMarkLeaf } from "./ReaderMarkLeaf";
import { ReaderAnalysisElement } from "./nodes/ReaderAnalysisElement";
import { ReaderContentSummaryElement } from "./nodes/ReaderContentSummaryElement";
import { ReaderParagraphElement } from "./nodes/ReaderParagraphElement";
import { ReaderSentenceElement } from "./nodes/ReaderSentenceElement";
import { ReaderSentenceTextElement } from "./nodes/ReaderSentenceTextElement";
import { ReaderTranslationElement } from "./nodes/ReaderTranslationElement";
import {
  buildSentenceAnalysisSegments,
  parseSentenceAnalysisContent,
  type SentenceAnalysisSegment,
} from "../reader-entry-utils";
import {
  analysisEntryVisible,
  type ReaderAnnotationVisibilityGroups,
} from "../settings";

export interface PlateReaderSurfaceProps {
  document: ReaderPlateDocument;
  showTranslation: boolean;
  readingClassName: string;
  annotationVisibilityGroups?: ReaderAnnotationVisibilityGroups;
  themeClassName?: string;
  activeSentenceId?: string | null;
  selectedSentenceId?: string | null;
  selectionFocusRangesBySentence?: Map<string, ReaderJumpRangeSegment[]>;
  jumpTarget?: ReaderJumpTarget | null;
  focusTarget?: ReaderJumpTarget | null;
  hoveredAnnotationTargetKey?: string | null;
  assetProjection?: ReaderAssetProjection | null;
  readerNotesBySentence?: Map<string, WebReaderNoteVm[]>;
  activeReaderNoteId?: string | null;
  activeAnalysisEntryId?: string | null;
  expandedAnalysisEntryId?: string | null;
  expandedAnalysisEntryIds?: string[];
  onSentenceActivate?: (sentenceId: string, anchorEl: HTMLElement) => void;
  onHoverAnnotationTargetKeyChange?: (targetKey: string | null) => void;
  onOpenSentenceNotes?: (sentenceId: string, anchorEl?: HTMLElement) => void;
  onAnalysisFocusChange?: (entryId: string, focused: boolean) => void;
  onAnalysisToggle?: (entryId: string) => void;
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
  onAskTranslation?: (sentenceId: string, translationZh: string) => void;
  onAskAnalysis?: (sentenceId: string, entryId: string) => void;
  onAskContentSummary?: (summary: ReaderContentSummaryNode) => void;
  onDeleteAnalysisSupplement?: (supplementId: string) => void;
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

export function PlateReaderSurface({
  activeSentenceId = null,
  selectedSentenceId = null,
  selectionFocusRangesBySentence = new Map<string, ReaderJumpRangeSegment[]>(),
  activeAnalysisEntryId = null,
  annotationVisibilityGroups = {
    lexical: true,
    analysis: true,
    userAssets: true,
  },
  assetProjection = null,
    document,
  expandedAnalysisEntryId = null,
  expandedAnalysisEntryIds = [],
  jumpTarget = null,
  focusTarget = null,
  hoveredAnnotationTargetKey = null,
  readerNotesBySentence = new Map<string, WebReaderNoteVm[]>(),
  activeReaderNoteId = null,
  onAnalysisFocusChange,
  onAnalysisToggle,
  onAnnotationJump,
  onOpenSentenceNotes,
  onAskAnalysis,
  onAskContentSummary,
  onAskTranslation,
  onDeleteAnalysisSupplement,
  onInspectIntent,
  onLookupIntent,
  onSentenceActivate,
  onHoverAnnotationTargetKeyChange,
  readingClassName,
    showTranslation,
    themeClassName,
  }: PlateReaderSurfaceProps) {
  const paragraphNodes = useMemo(
    () => document.children.filter((node): node is ReaderParagraphNode => node.type === "reader_paragraph"),
    [document.children],
  );

  const paragraphIndexById = useMemo(
    () => new Map(paragraphNodes.map((node, index) => [node.paragraphId, index])),
    [paragraphNodes],
  );

  const expandedIds = useMemo(() => {
    const ids = new Set<string>();
    if (expandedAnalysisEntryId) ids.add(expandedAnalysisEntryId);
    if (expandedAnalysisEntryIds) expandedAnalysisEntryIds.forEach(id => ids.add(id));
    return ids;
  }, [expandedAnalysisEntryId, expandedAnalysisEntryIds]);

  const sentenceAssetsBySentence = useMemo(
    () => assetProjection?.sentenceAssetProjectionBySentence ?? new Map<string, ReaderSentenceAssetProjection>(),
    [assetProjection],
  );

  const assetRangesBySentence = useMemo(() => {
    const map = new Map<string, ReaderAssetRange[]>();
    assetProjection?.annotationRangesBySentence?.forEach((ranges, sentenceId) => {
      map.set(sentenceId, [...ranges]);
    });
    return map;
  }, [assetProjection?.annotationRangesBySentence]);

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
        const translationNode = sentenceNode.children.find(
          (child): child is ReaderTranslationNode => child.type === "reader_translation",
        );
        map.set(sentenceNode.sentenceId, translationNode?.translationZh);
      });
    });
    return map;
  }, [paragraphNodes]);

  const routeFocusSentenceIds = useMemo(
    () => new Set(jumpTarget?.sentenceIds ?? []),
    [jumpTarget],
  );

  const jumpFocusRangesBySentence = useMemo(
    () => focusRangesBySentence(jumpTarget, sentenceTextBySentence),
    [jumpTarget, sentenceTextBySentence],
  );

  const noteFocusRangesBySentence = useMemo(
    () => focusRangesBySentence(focusTarget, sentenceTextBySentence),
    [focusTarget, sentenceTextBySentence],
  );

  const activeSentenceAnalysisSegmentsBySentence = useMemo(() => {
    const map = new Map<string, SentenceAnalysisSegment[]>();
    if (!activeAnalysisEntryId) {
      return map;
    }

    paragraphNodes.forEach((paragraph) => {
      paragraph.children.forEach((sentenceNode) => {
        const activeEntry = sentenceNode.children.find(
          (child): child is ReaderAnalysisBlockNode =>
            child.type === "reader_sentence_analysis" && child.entryId === activeAnalysisEntryId,
        );
        if (!activeEntry) {
          return;
        }

        const parsed = parseSentenceAnalysisContent(activeEntry.content);
        const segments = buildSentenceAnalysisSegments(sentenceNode.sourceText, parsed.chunks);
        if (segments.length > 0) {
          map.set(sentenceNode.sentenceId, segments);
        }
      });
    });

    return map;
  }, [paragraphNodes, activeAnalysisEntryId]);

  const activeGrammarNoteSentenceIds = useMemo(() => {
    const ids = new Set<string>();
    paragraphNodes.forEach((paragraph) => {
      paragraph.children.forEach((sentenceNode) => {
        const hasExpandedGrammarNote = sentenceNode.children.some(
          (child): child is ReaderAnalysisBlockNode =>
            child.type === "reader_grammar_note" && expandedIds.has(child.entryId)
        );
        if (hasExpandedGrammarNote) {
          ids.add(sentenceNode.sentenceId);
        }
      });
    });
    return ids;
  }, [paragraphNodes, expandedIds]);

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
    (props: any) => {
      const element = props.element as unknown as
        | ReaderParagraphNode
        | ReaderSentenceNode
        | ReaderSentenceTextNode
        | ReaderTranslationNode
        | ReaderAnalysisBlockNode
        | ReaderContentSummaryNode;

      switch (element.type) {
        case "reader_content_summary":
          return (
            <ReaderContentSummaryElement
              props={props}
              routeFocused={jumpTarget?.targetType === "content_summary"}
              onAsk={onAskContentSummary ? () => onAskContentSummary(element) : undefined}
            />
          );
        case "reader_paragraph":
          return (
            <ReaderParagraphElement
              props={props}
              paragraphCount={paragraphNodes.length}
              paragraphIndex={paragraphIndexById.get(element.paragraphId) ?? 0}
            />
          );
        case "reader_sentence":
          const sentenceNotes = readerNotesBySentence.get(element.sentenceId) ?? [];
          const activeSentenceNote =
            sentenceNotes.find((note) => note.id === activeReaderNoteId) ?? null;
          return (
            <ReaderSentenceElement
              props={props}
              active={activeSentenceId === element.sentenceId}
              analysisActive={activeSentenceAnalysisSegmentsBySentence.has(element.sentenceId)}
              analysisExpanded={element.children.some(
                (child: any) =>
                  child.type === "reader_sentence_analysis" &&
                  expandedIds.has(child.entryId)
              )}
              annotationVisibilityGroups={annotationVisibilityGroups}
              assetProjection={sentenceAssetsBySentence.get(element.sentenceId) ?? null}
              hoveredAnnotationTargetKey={hoveredAnnotationTargetKey}
              noteCount={sentenceNotes.length}
              noteActive={Boolean(activeSentenceNote)}
              routeFocused={Boolean(routeFocusSentenceIds?.has(element.sentenceId))}
              onAnnotationJump={onAnnotationJump}
              onActivate={onSentenceActivate}
              onOpenNotes={onOpenSentenceNotes}
              onHoverAnnotationTargetKeyChange={onHoverAnnotationTargetKeyChange}
            />
          );
        case "reader_sentence_text":
          return (
            <ReaderSentenceTextElement
              props={props}
              readingClassName={readingClassName}
              sourceContext={sourceContextBySentence.get(element.sentenceId)}
              onLookupIntent={onLookupIntent}
            />
          );
          case "reader_translation":
            if (!showTranslation) {
              return null;
            }
            return (
              <ReaderTranslationElement
                props={props}
                onAsk={onAskTranslation ? () => onAskTranslation(element.sentenceId, element.translationZh) : undefined}
              />
            );
        case "reader_grammar_note":
        case "reader_sentence_analysis":
        case "reader_term_note":
        case "reader_logic_note":
        case "reader_interpretation_note":
          return (
            <ReaderAnalysisElement
              props={props}
              active={activeAnalysisEntryId === element.entryId}
              expanded={expandedIds.has(element.entryId)}
              visible={analysisEntryVisible(element.entryType, annotationVisibilityGroups)}
              onAsk={onAskAnalysis ? () => onAskAnalysis(element.sentenceId, element.entryId) : undefined}
              onDelete={
                onDeleteAnalysisSupplement && element.supplementId
                  ? () => onDeleteAnalysisSupplement(String(element.supplementId))
                  : undefined
              }
              onFocusChange={
                onAnalysisFocusChange
                  ? (focused) => onAnalysisFocusChange(element.entryId, focused)
                  : undefined
              }
              onToggle={onAnalysisToggle ? () => onAnalysisToggle(element.entryId) : undefined}
            />
          );
        default:
          return <div {...props.attributes}>{props.children}</div>;
      }
    },
    [
      activeAnalysisEntryId,
      expandedAnalysisEntryId,
      expandedAnalysisEntryIds,
      expandedIds,
      activeSentenceId,
      selectedSentenceId,
      activeSentenceAnalysisSegmentsBySentence,
      annotationVisibilityGroups,
      onInspectIntent,
      onLookupIntent,
      onSentenceActivate,
      onAnnotationJump,
      onAskAnalysis,
      onAskContentSummary,
      onAskTranslation,
      onDeleteAnalysisSupplement,
      paragraphIndexById,
      paragraphNodes.length,
      readingClassName,
      readerNotesBySentence,
      routeFocusSentenceIds,
      sourceContextBySentence,
      sentenceAssetsBySentence,
        showTranslation,
        activeReaderNoteId,
        onOpenSentenceNotes,
      ],
  );

  const renderLeaf = useCallback(
    (props: any) => (
      <ReaderMarkLeaf
        annotationRangesBySentence={
          annotationVisibilityGroups.userAssets ? assetRangesBySentence : undefined
        }
        annotationVisibilityGroups={annotationVisibilityGroups}
        onInspectIntent={onInspectIntent}
        onLookupIntent={onLookupIntent}
        props={props}
        analysisSegmentsBySentence={activeSentenceAnalysisSegmentsBySentence}
        jumpFocusRangesBySentence={jumpFocusRangesBySentence}
        selectionFocusRangesBySentence={selectionFocusRangesBySentence}
        noteFocusRangesBySentence={noteFocusRangesBySentence}
        hoveredAnnotationTargetKey={hoveredAnnotationTargetKey}
        onHoverAnnotationTargetKeyChange={onHoverAnnotationTargetKeyChange}
        activeAnalysisEntryId={activeAnalysisEntryId}
        expandedAnalysisEntryIds={expandedIds}
        activeGrammarNoteSentenceIds={activeGrammarNoteSentenceIds}
        sentenceTextBySentence={sentenceTextBySentence}
        sourceContextBySentence={sourceContextBySentence}
      />
    ),
    [
      activeAnalysisEntryId,
      activeGrammarNoteSentenceIds,
      activeSentenceAnalysisSegmentsBySentence,
      assetRangesBySentence,
      annotationVisibilityGroups,
      onInspectIntent,
      onLookupIntent,
      jumpFocusRangesBySentence,
      selectionFocusRangesBySentence,
      hoveredAnnotationTargetKey,
      noteFocusRangesBySentence,
      onHoverAnnotationTargetKeyChange,
      sentenceTextBySentence,
      sourceContextBySentence,
    ],
  );

  return (
    <div className={`px-5 py-7 sm:px-8 lg:px-10 lg:py-9 ${themeClassName ?? ""}`.trim()}>
      <div className="mx-auto max-w-[72ch]">
        <Plate editor={editor} readOnly>
          <EditorContainer className="h-auto cursor-default overflow-visible bg-transparent px-0 py-0 [&_.slate-selection-area]:hidden">
            <Editor
              readOnly
              disableDefaultStyles
              className="space-y-10 px-0 py-0 outline-none"
              renderElement={renderElement as never}
              renderLeaf={renderLeaf as never}
            />
          </EditorContainer>
        </Plate>
      </div>
    </div>
  );
}
