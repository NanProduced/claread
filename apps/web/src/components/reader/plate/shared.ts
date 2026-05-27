import type { ReaderAnalysisBlockNode, ReaderContentSummaryNode } from "@/lib/reader-plate";
import type { SentenceEntryModel, VisualTone } from "@/types/view/ReaderMockVm";
import type { ReaderAnnotationVisibilityGroups } from "../settings";

const toneClass: Record<VisualTone, string> = {
  vocab: "reader-mark reader-mark--vocab",
  phrase: "reader-mark reader-mark--phrase",
  context: "reader-mark reader-mark--context",
  grammar: "reader-mark reader-mark--grammar",
  term: "reader-mark reader-mark--term",
  logic: "reader-mark reader-mark--logic",
};

const quietToneClass: Record<VisualTone, string> = {
  vocab: "reader-mark reader-mark--quiet reader-mark--vocab",
  phrase: "reader-mark reader-mark--quiet reader-mark--phrase",
  context: "reader-mark reader-mark--quiet reader-mark--context",
  grammar: "reader-mark reader-mark--quiet reader-mark--grammar",
  term: "reader-mark reader-mark--quiet reader-mark--term",
  logic: "reader-mark reader-mark--quiet reader-mark--logic",
};

const entryTypeLabel: Record<SentenceEntryModel["entryType"], string> = {
  grammar_note: "语法旁注",
  sentence_analysis: "句子拆解",
  term_note: "术语说明",
  logic_note: "逻辑提示",
  interpretation_note: "理解提示",
  content_summary: "内容摘要",
};

export function entryLabel(
  entry:
    | Pick<SentenceEntryModel, "entryType" | "label">
    | Pick<ReaderAnalysisBlockNode, "entryType" | "label">,
): string {
  return entryTypeLabel[entry.entryType] ?? entry.label ?? "解析";
}

export function readerMarkEnabled(
  visualTone: VisualTone,
  visibility: ReaderAnnotationVisibilityGroups,
) {
  if (visualTone === "vocab" || visualTone === "phrase" || visualTone === "context") {
    return visibility.lexical;
  }
  return visibility.analysis;
}

export function readerMarkClassName(
  visualTone: VisualTone,
  visibility: ReaderAnnotationVisibilityGroups,
): string | null {
  if (!readerMarkEnabled(visualTone, visibility)) {
    return null;
  }

  if (visualTone === "vocab" || visualTone === "phrase" || visualTone === "context") {
    return toneClass[visualTone];
  }

  return quietToneClass[visualTone];
}

export function contentSummaryCompletenessLabel(
  completeness: ReaderContentSummaryNode["completeness"],
): string {
  if (completeness === "full") {
    return "完整概要";
  }
  if (completeness === "partial") {
    return "部分概要";
  }

  return "简要概要";
}
