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
  translationClassName?: string;
  columnClassName?: string;
  paragraphDensityClassName?: string;
  annotationVisibilityGroups?: ReaderAnnotationVisibilityGroups;
  themeClassName?: string;
  activeSentenceId?: string | null;
  sentenceActionsOpenSentenceId?: string | null;
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
  onOpenSentenceActions?: (sentenceId: string, anchorEl?: HTMLElement) => void;
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
  sentenceActionsOpenSentenceId = null,
  selectedSentenceId = null,
  selectionFocusRangesBySentence = new Map<string, ReaderJumpRangeSegment[]>(),
  activeAnalysisEntryId = null,
  columnClassName = "max-w-[72ch]",
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
  onDeleteAnalysisSupplement,
  onInspectIntent,
  onLookupIntent,
  onOpenSentenceActions,
  onHoverAnnotationTargetKeyChange,
  paragraphDensityClassName = "reader-density-intensive",
  readingClassName,
  translationClassName = "reader-font-sans text-[0.88rem] leading-[1.62]",
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

  const expandedSentenceAnalysisSegmentsBySentence = useMemo(() => {
    const map = new Map<string, Array<SentenceAnalysisSegment & { entryId: string }>>();
    paragraphNodes.forEach((paragraph) => {
      paragraph.children.forEach((sentenceNode) => {
        const segmentsList: Array<SentenceAnalysisSegment & { entryId: string }> = [];
        sentenceNode.children.forEach((child) => {
          if (child.type === "reader_sentence_analysis" && expandedIds.has(child.entryId)) {
            const parsed = parseSentenceAnalysisContent(child.content);
            const segments = buildSentenceAnalysisSegments(sentenceNode.sourceText, parsed.chunks);
            segmentsList.push(
              ...segments.map((segment) => ({
                ...segment,
                entryId: child.entryId,
              })),
            );
          }
        });
        if (segmentsList.length > 0) {
          map.set(sentenceNode.sentenceId, segmentsList);
        }
      });
    });
    return map;
  }, [paragraphNodes, expandedIds]);

  const lastLeafOffsetsByMarkKey = useMemo(() => {
    const map = new Map<string, number>();
    paragraphNodes.forEach((paragraph) => {
      paragraph.children.forEach((sentence) => {
        sentence.children.forEach((child) => {
          if (child.type !== "reader_sentence_text") return;
          child.children.forEach((leaf) => {
            const markKey = leaf.readerMarkParentId ?? leaf.readerMarkId;
            if (markKey && typeof leaf.readerTextEndOffset === "number") {
              const currentMax = map.get(markKey) ?? -1;
              if (leaf.readerTextEndOffset > currentMax) {
                map.set(markKey, leaf.readerTextEndOffset);
              }
            }
          });
        });
      });
    });
    return map;
  }, [paragraphNodes]);

  const grammarCueMetaBySentence = useMemo(() => {
    const map = new Map<
      string,
      {
        cueIndexByEntryId: Map<string, number>;
        cueIndexByMarkKey: Map<string, number>;
        entryIdByMarkKey: Map<string, string>;
      }
    >();

    paragraphNodes.forEach((paragraph) => {
      paragraph.children.forEach((sentenceNode) => {
        const grammarEntries = sentenceNode.children.filter(
          (child): child is ReaderAnalysisBlockNode => child.type === "reader_grammar_note",
        );
        if (grammarEntries.length === 0) {
          return;
        }

        const cueIndexByEntryId = new Map<string, number>();
        const cueIndexByMarkKey = new Map<string, number>();
        const entryIdByMarkKey = new Map<string, string>();
        const sentenceTextNode = sentenceNode.children.find(
          (child): child is ReaderSentenceTextNode => child.type === "reader_sentence_text",
        );

        const orderedMarkKeys: string[] = [];
        const seenMarkKeys = new Set<string>();
        sentenceTextNode?.children.forEach((leaf) => {
          if (leaf.readerMarkAnnotationType !== "grammar_note") {
            return;
          }
          const markKey = leaf.readerMarkParentId ?? leaf.readerMarkId;
          if (!markKey || seenMarkKeys.has(markKey)) {
            return;
          }
          seenMarkKeys.add(markKey);
          orderedMarkKeys.push(markKey);
        });

        grammarEntries.forEach((entry, index) => {
          cueIndexByEntryId.set(entry.entryId, index + 1);
        });

        const unresolvedEntryIds = new Set(grammarEntries.map((entry) => entry.entryId));
        const unresolvedMarkKeys = [...orderedMarkKeys];

        orderedMarkKeys.forEach((markKey) => {
          if (!unresolvedEntryIds.has(markKey)) {
            return;
          }
          entryIdByMarkKey.set(markKey, markKey);
          unresolvedEntryIds.delete(markKey);
          const nextIndex = cueIndexByEntryId.get(markKey);
          if (nextIndex !== undefined && grammarEntries.length > 1) {
            cueIndexByMarkKey.set(markKey, nextIndex);
          }
          const unresolvedIndex = unresolvedMarkKeys.indexOf(markKey);
          if (unresolvedIndex >= 0) {
            unresolvedMarkKeys.splice(unresolvedIndex, 1);
          }
        });

        Array.from(unresolvedEntryIds).forEach((entryId, index) => {
          const markKey = unresolvedMarkKeys[index];
          if (!markKey) {
            return;
          }
          entryIdByMarkKey.set(markKey, entryId);
          const nextIndex = cueIndexByEntryId.get(entryId);
          if (nextIndex !== undefined && grammarEntries.length > 1) {
            cueIndexByMarkKey.set(markKey, nextIndex);
          }
        });

        if (grammarEntries.length <= 1) {
          cueIndexByEntryId.clear();
        }

        map.set(sentenceNode.sentenceId, {
          cueIndexByEntryId,
          cueIndexByMarkKey,
          entryIdByMarkKey,
        });
      });
    });
    return map;
  }, [paragraphNodes]);

  const grammarCueIndexByMarkKeyBySentence = useMemo(
    () =>
      new Map(
        Array.from(grammarCueMetaBySentence.entries()).map(([sentenceId, meta]) => [sentenceId, meta.cueIndexByMarkKey]),
      ),
    [grammarCueMetaBySentence],
  );

  const grammarEntryIdByMarkKeyBySentence = useMemo(
    () =>
      new Map(
        Array.from(grammarCueMetaBySentence.entries()).map(([sentenceId, meta]) => [sentenceId, meta.entryIdByMarkKey]),
      ),
    [grammarCueMetaBySentence],
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
              contentClassName="space-y-7"
              props={props}
              paragraphCount={paragraphNodes.length}
              paragraphIndex={paragraphIndexById.get(element.paragraphId) ?? 0}
            />
          );
        case "reader_sentence":
          const sentenceNotes = readerNotesBySentence.get(element.sentenceId) ?? [];
          const activeSentenceNote =
            sentenceNotes.find((note) => note.id === activeReaderNoteId) ?? null;
          const hasExpandedSentenceAnalysis = element.children.some(
            (child: any) =>
              child.type === "reader_sentence_analysis" &&
              expandedIds.has(child.entryId)
          );
          return (
            <ReaderSentenceElement
              props={props}
              active={activeSentenceId === element.sentenceId}
              analysisActive={hasExpandedSentenceAnalysis}
              analysisExpanded={hasExpandedSentenceAnalysis}
              annotationVisibilityGroups={annotationVisibilityGroups}
              assetProjection={sentenceAssetsBySentence.get(element.sentenceId) ?? null}
              sentenceActionsActive={sentenceActionsOpenSentenceId === element.sentenceId}
              hoveredAnnotationTargetKey={hoveredAnnotationTargetKey}
              noteCount={sentenceNotes.length}
              noteActive={Boolean(activeSentenceNote)}
              routeFocused={Boolean(routeFocusSentenceIds?.has(element.sentenceId))}
              onAnnotationJump={onAnnotationJump}
              onOpenSentenceActions={onOpenSentenceActions}
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
            <ReaderTranslationElement copyClassName={translationClassName} props={props} />
          );
        case "reader_grammar_note":
        case "reader_sentence_analysis":
        case "reader_term_note":
        case "reader_logic_note":
        case "reader_interpretation_note":
          const cueIndex =
            element.type === "reader_grammar_note"
              ? grammarCueMetaBySentence.get(element.sentenceId)?.cueIndexByEntryId.get(element.entryId)
              : undefined;
          return (
            <ReaderAnalysisElement
              props={props}
              active={activeAnalysisEntryId === element.entryId}
              expanded={expandedIds.has(element.entryId)}
              visible={analysisEntryVisible(element.entryType, annotationVisibilityGroups)}
              cueIndex={cueIndex}
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
      activeReaderNoteId,
      sentenceActionsOpenSentenceId,
      selectedSentenceId,
      annotationVisibilityGroups,
      onInspectIntent,
      onLookupIntent,
      onOpenSentenceActions,
      onAnnotationJump,
      onAskAnalysis,
      onAskContentSummary,
      onDeleteAnalysisSupplement,
      paragraphIndexById,
      paragraphNodes.length,
      readingClassName,
      readerNotesBySentence,
      routeFocusSentenceIds,
      sourceContextBySentence,
      sentenceAssetsBySentence,
      showTranslation,
      translationClassName,
      onOpenSentenceNotes,
      grammarCueMetaBySentence,
      onAnalysisFocusChange,
      onAnalysisToggle,
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
        analysisSegmentsBySentence={expandedSentenceAnalysisSegmentsBySentence}
        jumpFocusRangesBySentence={jumpFocusRangesBySentence}
        selectionFocusRangesBySentence={selectionFocusRangesBySentence}
        noteFocusRangesBySentence={noteFocusRangesBySentence}
        hoveredAnnotationTargetKey={hoveredAnnotationTargetKey}
        onHoverAnnotationTargetKeyChange={onHoverAnnotationTargetKeyChange}
        activeAnalysisEntryId={activeAnalysisEntryId}
        expandedAnalysisEntryIds={expandedIds}
        sentenceTextBySentence={sentenceTextBySentence}
        sourceContextBySentence={sourceContextBySentence}
        lastLeafOffsetsByMarkKey={lastLeafOffsetsByMarkKey}
        grammarCueIndexByMarkKeyBySentence={grammarCueIndexByMarkKeyBySentence}
        grammarEntryIdByMarkKeyBySentence={grammarEntryIdByMarkKeyBySentence}
        onAnalysisToggle={onAnalysisToggle}
      />
    ),
    [
      activeAnalysisEntryId,
      expandedSentenceAnalysisSegmentsBySentence,
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
      lastLeafOffsetsByMarkKey,
      grammarCueIndexByMarkKeyBySentence,
      grammarEntryIdByMarkKeyBySentence,
      onAnalysisToggle,
    ],
  );

  return (
    <div className={`px-5 py-7 sm:px-8 lg:px-10 lg:py-9 ${themeClassName ?? ""} ${paragraphDensityClassName}`.trim()}>
      <div className={`mx-auto ${columnClassName}`.trim()}>
        <Plate editor={editor} readOnly>
          <EditorContainer className="h-auto cursor-default overflow-visible bg-transparent px-0 py-0 [&_.slate-selection-area]:hidden">
            <Editor
              readOnly
              disableDefaultStyles
              className="space-y-9 px-0 py-0 outline-none"
              renderElement={renderElement as never}
              renderLeaf={renderLeaf as never}
            />
          </EditorContainer>
        </Plate>
      </div>
    </div>
  );
}
