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
  downgraded: boolean,
): string {
  const cursorClassName = interactive ? "cursor-pointer" : "cursor-default";

  if (downgraded) {
    if (mark.vocabulary.itemType === "phrase_gloss") {
      return `${cursorClassName} reader-record-mark-hit reader-record-mark-hit--vocabulary text-[var(--reader-mark-phrase-ink)] hover:text-[var(--reader-mark-phrase-hover-ink)] transition-colors`;
    }
    if (mark.vocabulary.itemType === "context_gloss") {
      return `${cursorClassName} reader-record-mark-hit reader-record-mark-hit--vocabulary text-[var(--reader-mark-context-ink)] hover:text-[var(--reader-mark-context-hover-ink)] transition-colors`;
    }
    return `${cursorClassName} reader-record-mark-hit reader-record-mark-hit--vocabulary text-[var(--reader-mark-vocab-ink)] hover:text-[var(--reader-mark-vocab-hover-ink)] transition-colors`;
  }

  if (mark.vocabulary.itemType === "phrase_gloss") {
    return `${cursorClassName} reader-record-mark-hit reader-record-mark-hit--vocabulary bg-[var(--reader-mark-phrase-fill)] text-[var(--reader-mark-phrase-ink)] hover:bg-[var(--reader-mark-phrase-hover-fill)] hover:text-[var(--reader-mark-phrase-hover-ink)] rounded-[2px] transition-colors`;
  }
  if (mark.vocabulary.itemType === "context_gloss") {
    return `${cursorClassName} reader-record-mark-hit reader-record-mark-hit--vocabulary bg-[var(--reader-mark-context-fill)] text-[var(--reader-mark-context-ink)] hover:bg-[var(--reader-mark-context-hover-fill)] hover:text-[var(--reader-mark-context-hover-ink)] rounded-[2px] transition-colors`;
  }
  return `${cursorClassName} reader-record-mark-hit reader-record-mark-hit--vocabulary bg-[var(--reader-mark-vocab-fill)] text-[var(--reader-mark-vocab-ink)] hover:bg-[var(--reader-mark-vocab-hover-fill)] hover:text-[var(--reader-mark-vocab-hover-ink)] rounded-[2px] transition-colors`;
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
  return "reader-record-mark-hit reader-record-mark-hit--grammar transition-colors font-medium underline decoration-ink/30 decoration-1 underline-offset-[3px]";
}

function grammarMarkLabel(mark: ReaderRecordPlateGrammarMark): string {
  return `语法 · ${mark.grammarPoint}`;
}

function userHighlightMarkClassName(mark: ReaderRecordPlateUserHighlightMark): string {
  const colorKey = highlightColorKey(mark);
  return [
    "cursor-pointer reader-record-mark-hit reader-record-mark-hit--user-highlight rounded-[3px] transition-colors",
    colorKey ? `reader-record-user-asset--${colorKey}` : "",
  ]
    .filter(Boolean)
    .join(" ");
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
  const stateClassName = active || hover ? "reader-record-user-note--active" : "";
  return `cursor-pointer reader-record-mark-hit reader-record-mark-hit--user-note reader-record-user-asset--yellow rounded-[2px] underline underline-offset-4 transition-colors ${stateClassName}`;
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

function highlightColorKey(
  mark?: ReaderRecordPlateUserHighlightMark,
): "yellow" | "mint" | "rose" | null {
  switch (mark?.color) {
    case "warm_yellow":
      return "yellow";
    case "soft_mint":
      return "mint";
    case "soft_rose":
      return "rose";
    default:
      return null;
  }
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
  options: {
    activeSentenceChunkId?: string | null;
    activeGrammarItemId?: string | null;
    /**
     * 当 vocabulary mark 与 user_highlight / user_note 重叠时，降级为
     * 仅文字色，不渲染背景，避免遮盖 user asset 填充色。
     */
    downgradeVocabulary?: boolean;
    /**
     * 当前处于 active/hover 状态的 note assetId 集合。叶子上有任意 note
     * 命中时追加 `reader-record-mark-stack--user-note-active` class。
     */
    activeNoteAssetIds?: Set<string> | null;
  } = {},
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
    classes.push("reader-record-mark-stack--vocabulary");
    const tone = vocabularyToneKey(vocabularyMark);
    classes.push(`reader-record-mark-stack--vocab-${tone}`);
    if (options.downgradeVocabulary) {
      classes.push("reader-record-mark-stack--vocabulary-downgraded");
    }
    labels.push(vocabularyMarkLabel(vocabularyMark));
  }
  if (grammarMark) {
    kinds.push("grammar_note");
    classes.push("reader-record-mark-stack--grammar");
    if (options.activeGrammarItemId === grammarMark.itemId) {
      classes.push("reader-record-mark-stack--grammar-active");
    }
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
    const colorKey = highlightColorKey(userHighlight);
    kinds.push("user_highlight");
    classes.push("reader-record-mark-stack--user-highlight");
    if (colorKey) {
      classes.push(`reader-record-mark-stack--highlight-${colorKey}`);
    }
    labels.push(userHighlightMarkLabel());
  }
  if (userNotes.length > 0) {
    kinds.push("user_note");
    classes.push("reader-record-mark-stack--user-note");
    const noteActive = options.activeNoteAssetIds
      ? userNotes.some((note) => options.activeNoteAssetIds!.has(note.assetId))
      : false;
    if (noteActive) {
      classes.push("reader-record-mark-stack--user-note-active");
    }
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
  onActivateLeaf?: (
    leaf: PlateTextNode,
    anchor: HTMLElement,
    event: React.MouseEvent<HTMLElement>,
  ) => void;
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

function hasNonCollapsedNativeSelection(): boolean {
  if (typeof window === "undefined") {
    return false;
  }
  const domSelection = window.getSelection();
  return Boolean(
    domSelection &&
      domSelection.rangeCount > 0 &&
      !domSelection.isCollapsed &&
      domSelection.toString().trim().length > 0,
  );
}

function useTypedLeafClickHandler(leaf: PlateTextNode) {
  const { onActivateLeaf } = useReaderLeafActions();
  return onActivateLeaf
    ? (event: React.MouseEvent<HTMLElement>) => {
        if (hasNonCollapsedNativeSelection()) {
          return;
        }
        event.stopPropagation();
        onActivateLeaf(leaf, event.currentTarget, event);
      }
    : undefined;
}

// --- Vocabulary leaf plugin ---

function VocabularyLeafComponent({
  children,
  leaf,
  attributes,
}: PlateLeafProps) {
  const plateLeaf = leaf as unknown as PlateTextNode;
  const mark = plateLeaf.vocabulary_data;
  const userHighlight = plateLeaf.user_highlight_data;
  const userNotes = noteMarksFromLeaf(plateLeaf.user_note_data);
  const hasUserAsset = !!userHighlight || userNotes.length > 0;
  const onClick = useTypedLeafClickHandler(plateLeaf);

  if (!mark) {
    return <span {...attributes}>{children}</span>;
  }

  const interactive = true;

  return (
    <span
      {...attributes}
      className={`${vocabularyMarkClassName(mark, interactive, hasUserAsset)} ${attributes?.className ?? ""}`.trim()}
      aria-label={vocabularyMarkLabel(mark)}
      title={vocabularyMarkLabel(mark)}
      data-reader-record-mark-entry="stack"
      data-reader-record-mark-id={mark.id}
      data-reader-record-mark-kind={mark.kind}
      data-reader-record-mark-starts-here={mark.startsHere ? "true" : "false"}
      onClick={onClick}
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
  const plateLeaf = leaf as unknown as PlateTextNode;
  const mark = plateLeaf.grammar_data;
  const onClick = useTypedLeafClickHandler(plateLeaf);

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
      data-reader-record-grammar-item-id={mark.itemId}
      data-reader-record-mark-starts-here={mark.startsHere ? "true" : "false"}
      onClick={onClick}
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
  const plateLeaf = leaf as unknown as PlateTextNode;
  const mark = plateLeaf.user_highlight_data;
  const onClick = useTypedLeafClickHandler(plateLeaf);

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
      onClick={onClick}
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
  const plateLeaf = leaf as unknown as PlateTextNode;
  const marks = noteMarksFromLeaf(
    plateLeaf.user_note_data,
  );
  const onClick = useTypedLeafClickHandler(plateLeaf);
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
        onClick={onClick}
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
