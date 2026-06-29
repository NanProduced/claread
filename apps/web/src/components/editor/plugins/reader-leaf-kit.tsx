/**
 * Reader Leaf Kit — 注册 Reader Plate 自定义 leaf plugin
 *
 * 四个 leaf plugin 对应 text leaf 的四种 mark：
 * - vocabulary mark      → 可点击的词汇标记（查词入口）
 * - grammar mark        → 语法标记
 * - user_highlight mark  → 高亮（带颜色）
 * - user_note mark       → 笔记标记
 *
 * 渲染逻辑参考 ReaderRecordPlateSurface.tsx 中的 renderMarkedLeaf / markClassName / markLabel。
 *
 * 交互回调（onActivateVocabulary 等）通过 React Context 注入，
 * 因为 leaf plugin 无法直接接收 props。
 */
import * as React from "react";
import { createContext, useContext } from "react";
import {
  createPlatePlugin,
  type PlateLeafProps,
  useEditorPlugin,
  usePluginOption,
} from "platejs/react";

import { commentPlugin } from "@/components/editor/plugins/comment-kit";
import type {
  ReaderRecordPlateGrammarMark,
  ReaderRecordPlateSentenceChunkMark,
  ReaderRecordPlateUserHighlightMark,
  ReaderRecordPlateUserNoteMark,
  ReaderRecordPlateVocabularyMark,
} from "@/lib/reader-plate/projection/reader-record-plate-document";
import type { PlateTextNode } from "@/lib/reader-plate/projection/reader-record-plate-to-plate-value";
import {
  READER_GRAMMAR_MARK_KEY,
  READER_USER_HIGHLIGHT_MARK_KEY,
  READER_USER_NOTE_MARK_KEY,
  READER_VOCABULARY_MARK_KEY,
} from "@/lib/reader-plate/projection/reader-record-plate-to-plate-value";

function vocabularyMarkClassName(
  mark: ReaderRecordPlateVocabularyMark,
  interactive: boolean,
): string {
  const cursorClassName = interactive ? "cursor-pointer" : "cursor-default";
  if (mark.vocabulary.itemType === "phrase_gloss") {
    return `${cursorClassName} reader-record-mark-hit reader-record-mark-hit--vocabulary rounded-[2px] transition-colors hover:bg-violet-50/40`;
  }
  if (mark.vocabulary.itemType === "context_gloss") {
    return `${cursorClassName} reader-record-mark-hit reader-record-mark-hit--vocabulary rounded-[2px] transition-colors hover:bg-sky-50/40`;
  }
  return `${cursorClassName} reader-record-mark-hit reader-record-mark-hit--vocabulary rounded-[2px] transition-colors hover:bg-amber-50/40`;
}

function vocabularyMarkLabel(mark: ReaderRecordPlateVocabularyMark): string {
  if (mark.vocabulary.itemType === "vocab_highlight") {
    return `词汇 · ${mark.vocabulary.headword}`;
  }
  if (mark.vocabulary.itemType === "phrase_gloss") {
    return `短语 · ${mark.vocabulary.gloss}`;
  }
  return `语境 · ${mark.vocabulary.gloss}`;
}

function grammarMarkClassName(): string {
  return "reader-record-mark-hit reader-record-mark-hit--grammar rounded-[2px] transition-colors hover:bg-emerald-50/35";
}

function grammarMarkLabel(mark: ReaderRecordPlateGrammarMark): string {
  return `语法 · ${mark.grammarPoint}`;
}

function userHighlightMarkClassName(mark: ReaderRecordPlateUserHighlightMark): string {
  const color = mark.color ?? "warm_yellow";
  if (color === "soft_blue" || color === "blue") {
    return "cursor-pointer reader-record-mark-hit reader-record-mark-hit--user-highlight rounded-[3px] transition-colors";
  }
  if (color === "soft_rose" || color === "rose") {
    return "cursor-pointer reader-record-mark-hit reader-record-mark-hit--user-highlight rounded-[3px] transition-colors";
  }
  return "cursor-pointer reader-record-mark-hit reader-record-mark-hit--user-highlight rounded-[3px] transition-colors";
}

function userHighlightMarkLabel(): string {
  return "用户高亮";
}

function userNoteMarkClassName({
  active,
  hover,
}: {
  active: boolean;
  hover: boolean;
}): string {
  const stateClassName =
    active || hover
      ? "bg-blue-100/80 decoration-blue-600"
      : "hover:bg-blue-50/70 decoration-blue-500/80";
  return `cursor-pointer rounded-[2px] underline decoration-dashed decoration-[1.5px] underline-offset-4 transition-colors ${stateClassName}`;
}

function userNoteMarkLabel(): string {
  return "用户笔记";
}

function noteRangeLength(mark: ReaderRecordPlateUserNoteMark): number {
  return Math.max(
    0,
    mark.anchor.segmentEndOffset - mark.anchor.segmentStartOffset,
  );
}

function noteMarksFromLeaf(
  data: PlateTextNode["user_note_data"],
): ReaderRecordPlateUserNoteMark[] {
  if (!data) {
    return [];
  }
  const marks = Array.isArray(data) ? data : [data];
  return [...marks].sort((a, b) => {
    const byLength = noteRangeLength(b) - noteRangeLength(a);
    if (byLength !== 0) {
      return byLength;
    }
    return a.assetId.localeCompare(b.assetId);
  });
}

function highlightColorKey(mark?: ReaderRecordPlateUserHighlightMark): string {
  const color = mark?.color ?? "warm_yellow";
  if (color === "soft_blue" || color === "blue") {
    return "blue";
  }
  if (color === "soft_rose" || color === "rose") {
    return "rose";
  }
  return "yellow";
}

function vocabularyToneKey(mark?: ReaderRecordPlateVocabularyMark): string {
  if (mark?.vocabulary.itemType === "phrase_gloss") {
    return "phrase";
  }
  if (mark?.vocabulary.itemType === "context_gloss") {
    return "context";
  }
  return "vocab";
}

export function sentenceChunkDomId(
  mark: ReaderRecordPlateSentenceChunkMark,
): string {
  return mark.id;
}

export interface ReaderMarkVisualResolution {
  className: string;
  ariaLabel?: string;
  title?: string;
  kinds: string[];
  sentenceChunk?: ReaderRecordPlateSentenceChunkMark;
}

export function resolveReaderMarkVisual(
  leaf: PlateTextNode,
  options: { activeSentenceChunkId?: string | null } = {},
): ReaderMarkVisualResolution {
  const vocabularyMark = leaf.vocabulary_data;
  const grammarMark = leaf.grammar_data;
  const sentenceChunk = leaf.sentence_chunk_data;
  const userHighlight = leaf.user_highlight_data;
  const userNotes = noteMarksFromLeaf(leaf.user_note_data);
  const kinds: string[] = [];
  const classes = ["reader-record-mark-stack"];
  const labels: string[] = [];

  if (vocabularyMark) {
    kinds.push(vocabularyMark.kind);
    classes.push(
      "reader-record-mark-stack--vocabulary",
      `reader-record-mark-stack--${vocabularyToneKey(vocabularyMark)}`,
    );
    labels.push(vocabularyMarkLabel(vocabularyMark));
  }
  if (grammarMark) {
    kinds.push("grammar_note");
    classes.push("reader-record-mark-stack--grammar");
    labels.push(grammarMarkLabel(grammarMark));
  }
  if (sentenceChunk) {
    const chunkId = sentenceChunkDomId(sentenceChunk);
    kinds.push("sentence_analysis_chunk");
    classes.push("reader-record-mark-stack--sentence-chunk");
    if (options.activeSentenceChunkId === chunkId) {
      classes.push("reader-record-mark-stack--sentence-chunk-active");
    }
    labels.push(`句子成分 · ${sentenceChunk.label}`);
  }
  if (userHighlight) {
    kinds.push("user_highlight");
    classes.push(
      "reader-record-mark-stack--user-highlight",
      `reader-record-mark-stack--highlight-${highlightColorKey(userHighlight)}`,
    );
    labels.push(userHighlightMarkLabel());
  }
  if (userNotes.length > 0) {
    kinds.push("user_note");
    classes.push("reader-record-mark-stack--user-note");
    labels.push(userNoteMarkLabel());
  }

  return {
    className: classes.join(" "),
    ariaLabel: labels.length > 0 ? labels.join("；") : undefined,
    title: labels.length > 0 ? labels.join("；") : undefined,
    kinds,
    sentenceChunk,
  };
}

// --- Callback Context ---

export interface ReaderLeafActions {
  onActivateVocabulary?: (
    mark: ReaderRecordPlateVocabularyMark,
    anchor: HTMLElement,
  ) => void;
  onActivateHighlight?: (
    mark: ReaderRecordPlateUserHighlightMark,
    anchor: HTMLElement,
  ) => void;
  onActivateNote?: (
    mark: ReaderRecordPlateUserNoteMark,
    anchor: HTMLElement,
  ) => void;
}

/**
 * Reader Leaf Actions Context — 把 mark 点击回调通过 React Context 传递给 leaf plugin。
 *
 * leaf plugin 在组件树深处渲染，无法直接接收 props。
 * 在 ReaderRecordPlateSurface 中用 Provider 包裹 Plate editor 来注入回调。
 */
export const ReaderLeafActionsContext = createContext<ReaderLeafActions>({});

export function useReaderLeafActions(): ReaderLeafActions {
  return useContext(ReaderLeafActionsContext);
}

// --- Vocabulary leaf plugin ---

function VocabularyLeafComponent({
  children,
  leaf,
  attributes,
}: PlateLeafProps) {
  const mark = (leaf as unknown as PlateTextNode).vocabulary_data;
  const { onActivateVocabulary } = useReaderLeafActions();

  if (!mark) {
    return <span {...attributes}>{children}</span>;
  }

  const interactive = mark.startsHere;

  return (
    <span
      {...attributes}
      className={`${vocabularyMarkClassName(mark, interactive)} ${attributes?.className ?? ""}`.trim()}
      aria-label={vocabularyMarkLabel(mark)}
      title={vocabularyMarkLabel(mark)}
      data-reader-record-mark-entry="stack"
      data-reader-record-mark-id={mark.id}
      data-reader-record-mark-kind={mark.kind}
      data-reader-record-mark-starts-here={mark.startsHere ? "true" : "false"}
      onClick={
        interactive
          ? (event: React.MouseEvent<HTMLElement>) => {
              event.stopPropagation();
              onActivateVocabulary?.(mark, event.currentTarget as HTMLElement);
            }
          : undefined
      }
    >
      {children}
    </span>
  );
}

export const ReaderVocabularyLeafPlugin = createPlatePlugin({
  key: READER_VOCABULARY_MARK_KEY,
  node: {
    isLeaf: true,
    component: VocabularyLeafComponent,
  },
});

// --- Grammar leaf plugin ---

function GrammarLeafComponent({
  children,
  leaf,
  attributes,
}: PlateLeafProps) {
  const mark = (leaf as unknown as PlateTextNode).grammar_data;

  if (!mark) {
    return <span {...attributes}>{children}</span>;
  }

  return (
    <span
      {...attributes}
      className={`${grammarMarkClassName()} ${attributes?.className ?? ""}`.trim()}
      aria-label={grammarMarkLabel(mark)}
      title={grammarMarkLabel(mark)}
      data-reader-record-mark-entry="stack"
      data-reader-record-mark-id={mark.id}
      data-reader-record-mark-kind={mark.kind}
      data-reader-record-mark-starts-here={mark.startsHere ? "true" : "false"}
    >
      {children}
    </span>
  );
}

export const ReaderGrammarLeafPlugin = createPlatePlugin({
  key: READER_GRAMMAR_MARK_KEY,
  node: {
    isLeaf: true,
    component: GrammarLeafComponent,
  },
});

// --- User highlight leaf plugin ---

function UserHighlightLeafComponent({
  children,
  leaf,
  attributes,
}: PlateLeafProps) {
  const mark = (leaf as unknown as PlateTextNode).user_highlight_data;
  const { onActivateHighlight } = useReaderLeafActions();

  if (!mark) {
    return <span {...attributes}>{children}</span>;
  }

  return (
    <span
      {...attributes}
      className={`${userHighlightMarkClassName(mark)} ${attributes?.className ?? ""}`.trim()}
      aria-label={userHighlightMarkLabel()}
      title={userHighlightMarkLabel()}
      data-reader-record-mark-entry="stack"
      data-reader-record-mark-id={mark.id}
      data-reader-record-mark-kind={mark.kind}
      onClick={(event: React.MouseEvent<HTMLElement>) => {
        event.stopPropagation();
        onActivateHighlight?.(mark, event.currentTarget as HTMLElement);
      }}
    >
      {children}
    </span>
  );
}

export const ReaderUserHighlightLeafPlugin = createPlatePlugin({
  key: READER_USER_HIGHLIGHT_MARK_KEY,
  node: {
    isLeaf: true,
    component: UserHighlightLeafComponent,
  },
});

// --- User note leaf plugin ---

function UserNoteLeafComponent({
  children,
  leaf,
  attributes,
}: PlateLeafProps) {
  const marks = noteMarksFromLeaf(
    (leaf as unknown as PlateTextNode).user_note_data,
  );
  const { onActivateNote } = useReaderLeafActions();
  const { setOption } = useEditorPlugin(commentPlugin);
  const activeId = usePluginOption(commentPlugin, "activeId");
  const hoverId = usePluginOption(commentPlugin, "hoverId");

  if (marks.length === 0) {
    return <span {...attributes}>{children}</span>;
  }

  const renderNoteSpan = (
    mark: ReaderRecordPlateUserNoteMark,
    content: React.ReactNode,
    includeLeafAttributes: boolean,
  ) => {
    const active = activeId === mark.assetId;
    const hover = hoverId === mark.assetId;
    const className = `${userNoteMarkClassName({ active, hover })} ${
      includeLeafAttributes ? (attributes?.className ?? "") : ""
    }`.trim();

    return (
      <span
        {...(includeLeafAttributes ? attributes : {})}
        key={mark.id}
        className={className}
        aria-label={userNoteMarkLabel()}
        title={userNoteMarkLabel()}
        data-reader-record-mark-entry="stack"
        data-reader-record-mark-id={mark.id}
        data-reader-record-mark-kind={mark.kind}
        data-reader-record-note-active={active ? "true" : "false"}
        data-reader-record-note-hover={hover ? "true" : "false"}
        onClick={(event: React.MouseEvent<HTMLElement>) => {
          event.stopPropagation();
          onActivateNote?.(mark, event.currentTarget as HTMLElement);
        }}
        onMouseEnter={() => setOption("hoverId", mark.assetId)}
        onMouseLeave={() => setOption("hoverId", null)}
      >
        {content}
      </span>
    );
  };

  let content: React.ReactNode = children;
  for (let index = marks.length - 1; index > 0; index -= 1) {
    content = renderNoteSpan(marks[index], content, false);
  }
  return renderNoteSpan(marks[0], content, true);
}

export const ReaderUserNoteLeafPlugin = createPlatePlugin({
  key: READER_USER_NOTE_MARK_KEY,
  node: {
    isLeaf: true,
    component: UserNoteLeafComponent,
  },
});

// --- Kit aggregation ---

export const ReaderLeafKit = [
  ReaderVocabularyLeafPlugin,
  ReaderGrammarLeafPlugin,
  ReaderUserHighlightLeafPlugin,
  ReaderUserNoteLeafPlugin,
];
