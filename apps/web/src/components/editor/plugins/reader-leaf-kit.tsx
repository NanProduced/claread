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
import { createPlatePlugin, type PlateLeafProps } from "platejs/react";

import type {
  ReaderRecordPlateGrammarMark,
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

// --- Mark styling helpers (mirror ReaderRecordPlateSurface.tsx) ---

function vocabularyMarkClassName(mark: ReaderRecordPlateVocabularyMark): string {
  if (mark.vocabulary.itemType === "phrase_gloss") {
    return "rounded-sm bg-violet-50 underline decoration-violet-500/70 underline-offset-4";
  }
  if (mark.vocabulary.itemType === "context_gloss") {
    return "rounded-sm bg-sky-50 underline decoration-sky-500/70 underline-offset-4";
  }
  return "rounded-sm bg-amber-50";
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
  return "rounded-sm underline decoration-emerald-600/80 decoration-[1.5px] underline-offset-4";
}

function grammarMarkLabel(mark: ReaderRecordPlateGrammarMark): string {
  return `语法 · ${mark.grammarPoint}`;
}

function userHighlightMarkClassName(): string {
  return "rounded-sm bg-amber-100/80 ring-1 ring-amber-200/80";
}

function userHighlightMarkLabel(): string {
  return "用户高亮";
}

function userNoteMarkClassName(): string {
  return "rounded-sm bg-blue-50/60 underline decoration-blue-500/80 decoration-dashed underline-offset-4";
}

function userNoteMarkLabel(): string {
  return "用户笔记";
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

  return (
    <span
      {...attributes}
      className={`${vocabularyMarkClassName(mark)} ${attributes?.className ?? ""}`.trim()}
      aria-label={vocabularyMarkLabel(mark)}
      title={vocabularyMarkLabel(mark)}
      data-reader-record-mark-entry="stack"
      data-reader-record-mark-id={mark.id}
      data-reader-record-mark-kind={mark.kind}
      onClick={(event: React.MouseEvent<HTMLElement>) => {
        event.stopPropagation();
        onActivateVocabulary?.(mark, event.currentTarget as HTMLElement);
      }}
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
      className={`${userHighlightMarkClassName()} ${attributes?.className ?? ""}`.trim()}
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
  const mark = (leaf as unknown as PlateTextNode).user_note_data;
  const { onActivateNote } = useReaderLeafActions();

  if (!mark) {
    return <span {...attributes}>{children}</span>;
  }

  return (
    <span
      {...attributes}
      className={`${userNoteMarkClassName()} ${attributes?.className ?? ""}`.trim()}
      aria-label={userNoteMarkLabel()}
      title={userNoteMarkLabel()}
      data-reader-record-mark-entry="stack"
      data-reader-record-mark-id={mark.id}
      data-reader-record-mark-kind={mark.kind}
      onClick={(event: React.MouseEvent<HTMLElement>) => {
        event.stopPropagation();
        onActivateNote?.(mark, event.currentTarget as HTMLElement);
      }}
    >
      {children}
    </span>
  );
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
