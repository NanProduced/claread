"use client";

import { useCallback, useEffect, useMemo } from "react";
import { Plate, usePlateEditor } from "platejs/react";
import type { RenderElement, RenderLeaf } from "platejs/react";

import type {
  ReaderAnchorSegmentNodeDto,
  ReaderPlateValueDto,
  ReaderSourceBlockNodeDto,
  ReaderStableSegmentTextLeafDto,
  ReaderStableSeparatorLeafDto,
  ReaderTranslationNodeDto,
  ReaderUnitNodeDto,
  ReaderVocabularyMarkDto,
} from "@/types/api/reader-plate";
import { Editor, EditorContainer } from "../../ui/editor";

/**
 * ReadOnly Plate surface for the D5 Reader Plate snapshot.
 *
 * Renders `snapshot.value` (the new domain-first Plate projection built from
 * Stable Reading Base / Reading Units / Anchor Segments / Enhancement Layers)
 * — NOT the legacy `render_scene_json` document.
 *
 * Node taxonomy handled here:
 *   - `reader_unit` (top-level block)
 *     - `reader_source_block` (source text container)
 *       - `reader_anchor_segment` (sentence-like inline anchor)
 *         - stable `segment_text` leaf
 *       - stable `separator` leaf (whitespace between anchors)
 *     - `reader_translation` (system_ai translation projection)
 *       - translation text leaf
 *
 * Stable `segment_text` leaves may also carry `reader_vocabulary_marks`, which
 * are rendered as inline highlight marks plus compact read-only gloss chips.
 *
 * Styling is intentionally minimal but distinguishes source text (serif /
 * reading font) from translation projection (sans-serif, muted).
 */

export interface ReaderPlateSnapshotSurfaceProps {
  value: ReaderPlateValueDto;
  readingClassName?: string;
  translationClassName?: string;
  containerClassName?: string;
  columnClassName?: string;
}

type PlateElement =
  | ReaderUnitNodeDto
  | ReaderSourceBlockNodeDto
  | ReaderAnchorSegmentNodeDto
  | ReaderTranslationNodeDto;

type PlateLeaf = ReaderStableSegmentTextLeafDto | ReaderStableSeparatorLeafDto | { text: string };

function isStableLeaf(leaf: unknown): leaf is ReaderStableSegmentTextLeafDto | ReaderStableSeparatorLeafDto {
  return (
    typeof leaf === "object" &&
    leaf !== null &&
    "owner" in leaf &&
    (leaf as { owner: unknown }).owner === "stable" &&
    "lock_source" in leaf
  );
}

function isVocabularyMarkedLeaf(
  leaf: unknown,
): leaf is ReaderStableSegmentTextLeafDto & {
  reader_vocabulary_marks: ReaderVocabularyMarkDto[];
} {
  return (
    isStableLeaf(leaf) &&
    "reader_vocabulary_marks" in leaf &&
    Array.isArray(
      (leaf as ReaderStableSegmentTextLeafDto & {
        reader_vocabulary_marks?: ReaderVocabularyMarkDto[];
      }).reader_vocabulary_marks,
    ) &&
    ((leaf as ReaderStableSegmentTextLeafDto & {
      reader_vocabulary_marks?: ReaderVocabularyMarkDto[];
    }).reader_vocabulary_marks?.length ?? 0) > 0
  );
}

function vocabularyTone(itemType: ReaderVocabularyMarkDto["item_type"]) {
  if (itemType === "phrase_gloss") {
    return "phrase";
  }
  if (itemType === "context_gloss") {
    return "context";
  }
  return "vocab";
}

function vocabularyMarkClassName(itemType: ReaderVocabularyMarkDto["item_type"]) {
  return `reader-mark reader-mark--${vocabularyTone(itemType)}`;
}

function vocabularyChipClassName(itemType: ReaderVocabularyMarkDto["item_type"]) {
  if (itemType === "phrase_gloss") {
    return "border-violet-200/80 bg-violet-50 text-violet-900";
  }
  if (itemType === "context_gloss") {
    return "border-sky-200/80 bg-sky-50 text-sky-900";
  }
  return "border-amber-200/80 bg-amber-50 text-amber-900";
}

function phraseTypeLabel(phraseType: string) {
  switch (phraseType) {
    case "collocation":
      return "搭配";
    case "phrasal_verb":
      return "短语动词";
    case "idiom":
      return "习语";
    case "proper_noun":
      return "专名";
    case "compound":
      return "复合词";
    default:
      return "短语";
  }
}

function vocabularyChipLabel(mark: ReaderVocabularyMarkDto) {
  if (mark.item_type === "vocab_highlight") {
    return mark.brief_explanation?.trim()
      ? `词义 · ${mark.brief_explanation}`
      : `词汇 · ${mark.headword}`;
  }
  if (mark.item_type === "phrase_gloss") {
    return `${phraseTypeLabel(mark.phrase_type)} · ${mark.gloss}`;
  }
  return `语境 · ${mark.gloss}`;
}

function vocabularyMarkTitle(mark: ReaderVocabularyMarkDto) {
  if (mark.item_type === "vocab_highlight") {
    return mark.brief_explanation?.trim()
      ? `${mark.headword}: ${mark.brief_explanation}`
      : mark.headword;
  }
  if (mark.item_type === "phrase_gloss") {
    return `${mark.phrase} (${phraseTypeLabel(mark.phrase_type)})`;
  }
  return `${mark.display}: ${mark.reason}`;
}

function sortVocabularyMarks(marks: ReaderVocabularyMarkDto[]) {
  const priority = {
    context_gloss: 0,
    phrase_gloss: 1,
    vocab_highlight: 2,
  } as const;
  return [...marks].sort((left, right) => {
    if (left.segment_start_utf16 !== right.segment_start_utf16) {
      return left.segment_start_utf16 - right.segment_start_utf16;
    }
    const leftSpan = left.segment_end_utf16 - left.segment_start_utf16;
    const rightSpan = right.segment_end_utf16 - right.segment_start_utf16;
    if (leftSpan !== rightSpan) {
      return rightSpan - leftSpan;
    }
    return priority[left.item_type] - priority[right.item_type];
  });
}

function renderVocabularyContent(
  leaf: ReaderStableSegmentTextLeafDto & {
    reader_vocabulary_marks: ReaderVocabularyMarkDto[];
  },
  children: React.ReactNode,
) {
  const marks = sortVocabularyMarks(leaf.reader_vocabulary_marks);
  let highlighted = children;
  [...marks].reverse().forEach((mark) => {
    highlighted = (
      <span
        className={vocabularyMarkClassName(mark.item_type)}
        data-reader-mark-id={mark.mark_id}
        data-reader-mark-tone={vocabularyTone(mark.item_type)}
        data-reader-vocabulary-item-type={mark.item_type}
        title={vocabularyMarkTitle(mark)}
      >
        {highlighted}
      </span>
    );
  });

  const chips = marks.filter((mark) => mark.ends_here);
  if (chips.length === 0) {
    return highlighted;
  }

  return (
    <>
      {highlighted}
      <span
        data-reader-node="vocabulary-inline"
        className="ml-1 inline-flex flex-wrap items-center gap-1 align-middle"
      >
        {chips.map((mark) => (
          <span
            key={`${mark.mark_id}:${mark.item_type}`}
            data-reader-vocabulary-chip={mark.item_type}
            className={`inline-flex items-center rounded-full border px-2 py-0.5 font-sans text-[0.68rem] font-medium leading-none ${vocabularyChipClassName(mark.item_type)}`}
            title={vocabularyMarkTitle(mark)}
          >
            {vocabularyChipLabel(mark)}
          </span>
        ))}
      </span>
    </>
  );
}

function ReaderUnitElement({
  props,
  children,
}: {
  props: Parameters<RenderElement>[0];
  children: React.ReactNode;
}) {
  const element = props.element as unknown as ReaderUnitNodeDto;
  return (
    <section
      {...props.attributes}
      data-reader-node="unit"
      data-unit-id={element.unit_id}
      data-unit-type={element.unit_type}
      className="reader-plate-unit"
    >
      {children}
    </section>
  );
}

function ReaderSourceBlockElement({
  props,
  children,
  readingClassName,
}: {
  props: Parameters<RenderElement>[0];
  children: React.ReactNode;
  readingClassName: string;
}) {
  const element = props.element as unknown as ReaderSourceBlockNodeDto;
  return (
    <div
      {...props.attributes}
      data-reader-node="source-block"
      data-unit-id={element.unit_id}
      className={`reader-plate-source-block ${readingClassName}`.trim()}
    >
      {children}
    </div>
  );
}

function ReaderAnchorSegmentElement({
  props,
  children,
}: {
  props: Parameters<RenderElement>[0];
  children: React.ReactNode;
}) {
  const element = props.element as unknown as ReaderAnchorSegmentNodeDto;
  return (
    <span
      {...props.attributes}
      data-reader-node="anchor-segment"
      data-anchor-segment-id={element.anchor_segment_id}
      data-sentence-id={element.sentence_id}
      data-segment-type={element.segment_type}
      className="reader-plate-anchor-segment"
    >
      {children}
    </span>
  );
}

function ReaderTranslationProjectionElement({
  props,
  children,
  translationClassName,
}: {
  props: Parameters<RenderElement>[0];
  children: React.ReactNode;
  translationClassName: string;
}) {
  const element = props.element as unknown as ReaderTranslationNodeDto;
  return (
    <div
      {...props.attributes}
      data-reader-node="translation"
      data-layer-id={element.layer_id}
      data-target-scope={element.target_scope}
      data-target-key={element.target_key}
      data-target-language={element.target_language}
      data-confidence={element.confidence}
      className={`reader-plate-translation ${translationClassName}`.trim()}
    >
      {children}
    </div>
  );
}

export function ReaderPlateSnapshotSurface({
  value,
  readingClassName = "reader-serif text-ink",
  translationClassName = "reader-font-sans text-[0.92rem] leading-[1.7] text-muted",
  containerClassName = "px-5 py-8 sm:px-8 lg:px-10",
  columnClassName = "mx-auto max-w-[68ch]",
}: ReaderPlateSnapshotSurfaceProps) {
  const editor = usePlateEditor(
    {
      value: value as never[],
    },
    [],
  );

  useEffect(() => {
    if (editor.children !== value) {
      editor.tf.setValue(value as never[]);
    }
  }, [value, editor]);

  const renderElement = useCallback(
    (props: Parameters<RenderElement>[0]) => {
      const element = props.element as unknown as PlateElement;

      switch (element.type) {
        case "reader_unit":
          return <ReaderUnitElement props={props}>{props.children}</ReaderUnitElement>;
        case "reader_source_block":
          return (
            <ReaderSourceBlockElement props={props} readingClassName={readingClassName}>
              {props.children}
            </ReaderSourceBlockElement>
          );
        case "reader_anchor_segment":
          return (
            <ReaderAnchorSegmentElement props={props}>
              {props.children}
            </ReaderAnchorSegmentElement>
          );
        case "reader_translation":
          return (
            <ReaderTranslationProjectionElement
              props={props}
              translationClassName={translationClassName}
            >
              {props.children}
            </ReaderTranslationProjectionElement>
          );
        default:
          return <div {...props.attributes}>{props.children}</div>;
      }
    },
    [readingClassName, translationClassName],
  );

  const renderLeaf = useCallback((props: Parameters<RenderLeaf>[0]) => {
    const leaf = props.leaf as unknown as PlateLeaf;
    if (isVocabularyMarkedLeaf(leaf)) {
      return (
        <span
          {...props.attributes}
          data-reader-leaf={leaf.source_role}
          data-owner="stable"
          data-anchor-segment-id={leaf.anchor_segment_id}
        >
          {renderVocabularyContent(leaf, props.children)}
        </span>
      );
    }
    if (isStableLeaf(leaf)) {
      return (
        <span
          {...props.attributes}
          data-reader-leaf={leaf.source_role}
          data-owner="stable"
          data-anchor-segment-id={
            leaf.source_role === "segment_text" ? leaf.anchor_segment_id : undefined
          }
        >
          {props.children}
        </span>
      );
    }
    return <span {...props.attributes}>{props.children}</span>;
  }, []);

  const hasContent = useMemo(() => value.length > 0, [value]);

  if (!hasContent) {
    return (
      <div className={`${containerClassName} ${columnClassName}`.trim()}>
        <p className="font-sans text-sm text-muted">这篇文章还没有可显示的正文内容。</p>
      </div>
    );
  }

  return (
    <div className={containerClassName.trim()}>
      <div className={columnClassName.trim()}>
        <Plate editor={editor} readOnly>
          <EditorContainer className="h-auto cursor-default overflow-visible bg-transparent px-0 py-0 [&_.slate-selection-area]:hidden">
            <Editor
              readOnly
              disableDefaultStyles
              className="space-y-6 px-0 py-0 outline-none"
              renderElement={renderElement as never}
              renderLeaf={renderLeaf as never}
            />
          </EditorContainer>
        </Plate>
      </div>
    </div>
  );
}
