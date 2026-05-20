import type {
  InlineMarkModel,
  ReaderMockVm,
  SentenceEntryModel,
  SentenceModel,
  TranslationModel,
} from "@/types/view/ReaderMockVm";
import type {
  ReaderAnalysisBlockNode,
  ReaderAnalysisBlockNodeType,
  ReaderContentSummaryNode,
  ReaderParagraphNode,
  ReaderPlateDocument,
  ReaderPlateTextLeaf,
  ReaderSentenceNode,
  ReaderSentenceTextNode,
  ReaderTranslationNode,
} from "../model";

const ANALYSIS_BLOCK_TYPE_BY_ENTRY: Record<
  Exclude<SentenceEntryModel["entryType"], "content_summary">,
  ReaderAnalysisBlockNodeType
> = {
  grammar_note: "reader_grammar_note",
  sentence_analysis: "reader_sentence_analysis",
  term_note: "reader_term_note",
  logic_note: "reader_logic_note",
  interpretation_note: "reader_interpretation_note",
};

const tonePriority: Record<InlineMarkModel["visualTone"], number> = {
  vocab: 1,
  phrase: 2,
  context: 3,
  grammar: 4,
  term: 5,
  logic: 6,
};

type MarkRange = {
  key: string;
  mark: InlineMarkModel;
  start: number;
  end: number;
  anchorText: string;
  fullAnchorText: string;
};

function createTextLeaf(
  text: string,
  options?: {
    mark?: InlineMarkModel;
    markAnchorText?: string;
    sentenceId?: string;
    startOffset?: number;
    endOffset?: number;
  },
): ReaderPlateTextLeaf {
  const baseLeaf: ReaderPlateTextLeaf = {
    text,
    readerSentenceId: options?.sentenceId,
    readerTextStartOffset: options?.startOffset,
    readerTextEndOffset: options?.endOffset,
  };

  const mark = options?.mark;
  if (!mark) {
    return baseLeaf;
  }

  return {
    ...baseLeaf,
    readerMarkAnnotationType: mark.annotationType,
    readerMarkAnchorText: options?.markAnchorText ?? undefined,
    readerMarkClickable: mark.clickable,
    readerMarkGlossary: mark.glossary,
    readerMarkId: mark.id,
    readerMarkParentId: mark.parentId,
    readerMarkLookupKind: mark.lookupKind,
    readerMarkLookupText: mark.lookupText,
    readerMarkRenderType: mark.renderType,
    readerMarkVisualTone: mark.visualTone,
  };
}

function normalizedText(value: string): string {
  return value.trim();
}

function findTextAnchorPosition(text: string, anchorText: string, occurrence = 1): number {
  let count = 0;
  let position = 0;
  const safeOccurrence = occurrence || 1;

  while (count < safeOccurrence) {
    const index = text.indexOf(anchorText, position);
    if (index === -1) {
      return -1;
    }

    count += 1;
    if (count === safeOccurrence) {
      return index;
    }

    position = index + 1;
  }

  return -1;
}

function buildMarkRanges(
  sentence: SentenceModel,
  inlineMarks: InlineMarkModel[],
): MarkRange[] {
  const ranges: MarkRange[] = [];

  for (const mark of inlineMarks) {
    if (mark.anchor.kind === "text") {
      const start = findTextAnchorPosition(
        sentence.text,
        mark.anchor.anchorText,
        mark.anchor.occurrence ?? 1,
      );

      if (start >= 0) {
        ranges.push({
          key: mark.id,
          mark,
          start,
          end: start + mark.anchor.anchorText.length,
          anchorText: mark.anchor.anchorText,
          fullAnchorText: mark.anchor.anchorText,
        });
      }

      continue;
    }

    mark.anchor.parts.forEach((part, index) => {
      const start = findTextAnchorPosition(
        sentence.text,
        part.anchorText,
        part.occurrence ?? 1,
      );

      if (start >= 0) {
        ranges.push({
          key: `${mark.id}-part-${index}`,
          mark,
          start,
          end: start + part.anchorText.length,
          anchorText: part.anchorText,
          fullAnchorText: part.anchorText,
        });
      }
    });
  }

  return ranges
    .sort((left, right) => {
      if (left.start !== right.start) {
        return left.start - right.start;
      }
      if (left.end !== right.end) {
        return right.end - left.end;
      }

      return tonePriority[left.mark.visualTone] - tonePriority[right.mark.visualTone];
    })
    .reduce<MarkRange[]>((accepted, range) => {
      const previous = accepted.at(-1);
      if (previous && range.start < previous.end) {
        if (range.end <= previous.end) {
          return accepted;
        }

        const visibleStart = previous.end;
        accepted.push({
          ...range,
          key: `${range.key}-tail`,
          start: visibleStart,
          anchorText: sentence.text.slice(visibleStart, range.end),
        });

        return accepted;
      }

      accepted.push(range);
      return accepted;
    }, []);
}

function createSentenceTextLeaves(
  sentence: SentenceModel,
  inlineMarks: InlineMarkModel[],
): ReaderPlateTextLeaf[] {
  const ranges = buildMarkRanges(sentence, inlineMarks);
  if (ranges.length === 0) {
    return [
      createTextLeaf(sentence.text, {
        sentenceId: sentence.sentenceId,
        startOffset: 0,
        endOffset: sentence.text.length,
      }),
    ];
  }

  const leaves: ReaderPlateTextLeaf[] = [];
  let cursor = 0;

  for (const range of ranges) {
    if (range.start > cursor) {
      leaves.push(
        createTextLeaf(sentence.text.slice(cursor, range.start), {
          sentenceId: sentence.sentenceId,
          startOffset: cursor,
          endOffset: range.start,
        }),
      );
    }

    leaves.push(
      createTextLeaf(range.anchorText, {
        mark: range.mark,
        markAnchorText: range.fullAnchorText,
        sentenceId: sentence.sentenceId,
        startOffset: range.start,
        endOffset: range.end,
      }),
    );
    cursor = range.end;
  }

  if (cursor < sentence.text.length) {
    leaves.push(
      createTextLeaf(sentence.text.slice(cursor), {
        sentenceId: sentence.sentenceId,
        startOffset: cursor,
        endOffset: sentence.text.length,
      }),
    );
  }

  return leaves.filter((leaf) => leaf.text.length > 0);
}

function buildTranslationBySentence(
  translations: TranslationModel[],
  sentenceById: Map<string, SentenceModel>,
): Map<string, TranslationModel> {
  const map = new Map<string, TranslationModel>();

  for (const translation of translations) {
    if (!sentenceById.has(translation.sentenceId)) {
      continue;
    }

    const translationZh = normalizedText(translation.translationZh);
    if (!translationZh || map.has(translation.sentenceId)) {
      continue;
    }

    map.set(translation.sentenceId, {
      ...translation,
      translationZh,
    });
  }

  return map;
}

function buildInlineMarksBySentence(
  inlineMarks: InlineMarkModel[],
  sentenceById: Map<string, SentenceModel>,
): Map<string, InlineMarkModel[]> {
  const map = new Map<string, InlineMarkModel[]>();

  for (const inlineMark of inlineMarks) {
    const sentenceId = inlineMark.anchor.sentenceId;
    if (!sentenceById.has(sentenceId)) {
      continue;
    }

    const current = map.get(sentenceId) ?? [];
    current.push(inlineMark);
    map.set(sentenceId, current);
  }

  return map;
}

function buildEntriesBySentence(
  sentenceEntries: SentenceEntryModel[],
  sentenceById: Map<string, SentenceModel>,
): Map<string, SentenceEntryModel[]> {
  const map = new Map<string, SentenceEntryModel[]>();

  for (const entry of sentenceEntries) {
    if (entry.entryType === "content_summary") {
      continue;
    }
    if (!entry.id || !sentenceById.has(entry.sentenceId)) {
      continue;
    }

    const current = map.get(entry.sentenceId) ?? [];
    current.push(entry);
    map.set(entry.sentenceId, current);
  }

  return map;
}

function createSentenceTextNode(
  sentence: SentenceModel,
  inlineMarks: InlineMarkModel[],
): ReaderSentenceTextNode {
  return {
    type: "reader_sentence_text",
    sentenceId: sentence.sentenceId,
    inlineMarks,
    children: createSentenceTextLeaves(sentence, inlineMarks),
  };
}

function createTranslationNode(
  translation: TranslationModel | undefined,
): ReaderTranslationNode | null {
  if (!translation) {
    return null;
  }

  return {
    type: "reader_translation",
    sentenceId: translation.sentenceId,
    translationZh: translation.translationZh,
    children: [createTextLeaf(translation.translationZh)],
  };
}

function createAnalysisBlockNode(
  entry: SentenceEntryModel,
): ReaderAnalysisBlockNode | null {
  if (entry.entryType === "content_summary") {
    return null;
  }

  const type = ANALYSIS_BLOCK_TYPE_BY_ENTRY[entry.entryType];
  if (!type) {
    return null;
  }

  return {
    type,
    entryId: entry.id,
    sentenceId: entry.sentenceId,
    entryType: entry.entryType,
    label: entry.label,
    title: entry.title,
    content: entry.content,
    sourceKind: entry.sourceKind,
    supplementId: entry.supplementId,
    deletable: entry.deletable,
    createdFromTurnRunId: entry.createdFromTurnRunId,
    children: [createTextLeaf(entry.content)],
  };
}

function createContentSummaryNode(
  contentSummary: ReaderMockVm["contentSummary"],
): ReaderContentSummaryNode | null {
  if (!contentSummary) {
    return null;
  }

  const overview = normalizedText(contentSummary.overview);
  if (!overview) {
    return null;
  }

  const parts = [overview];
  if (contentSummary.researchQuestion) {
    parts.push(`研究问题: ${contentSummary.researchQuestion}`);
  }
  if (contentSummary.methodology) {
    parts.push(`方法: ${contentSummary.methodology}`);
  }
  if (contentSummary.keyFindings.length > 0) {
    parts.push(`主要发现: ${contentSummary.keyFindings.join("; ")}`);
  }
  if (contentSummary.limitations.length > 0) {
    parts.push(`局限性: ${contentSummary.limitations.join("; ")}`);
  }

  return {
    type: "reader_content_summary",
    completeness: contentSummary.completeness,
    overview,
    researchQuestion: contentSummary.researchQuestion,
    methodology: contentSummary.methodology,
    keyFindings: contentSummary.keyFindings,
    limitations: contentSummary.limitations,
    children: [createTextLeaf(parts.join("\n"))],
  };
}

function createSentenceNode(
  sentence: SentenceModel,
  translation: TranslationModel | undefined,
  inlineMarks: InlineMarkModel[],
  sentenceEntries: SentenceEntryModel[],
): ReaderSentenceNode {
  const children: ReaderSentenceNode["children"] = [
    createSentenceTextNode(sentence, inlineMarks),
  ];

  const translationNode = createTranslationNode(translation);
  if (translationNode) {
    children.push(translationNode);
  }

  for (const entry of sentenceEntries) {
    const analysisBlock = createAnalysisBlockNode(entry);
    if (analysisBlock) {
      children.push(analysisBlock);
    }
  }

  return {
    type: "reader_sentence",
    sentenceId: sentence.sentenceId,
    paragraphId: sentence.paragraphId,
    sourceText: sentence.text,
    children,
  };
}

function createParagraphNode(
  paragraphId: string,
  sentences: SentenceModel[],
  translationBySentence: Map<string, TranslationModel>,
  inlineMarksBySentence: Map<string, InlineMarkModel[]>,
  entriesBySentence: Map<string, SentenceEntryModel[]>,
): ReaderParagraphNode | null {
  if (!paragraphId || sentences.length === 0) {
    return null;
  }

  return {
    type: "reader_paragraph",
    paragraphId,
    sentenceIds: sentences.map((sentence) => sentence.sentenceId),
    children: sentences.map((sentence) =>
      createSentenceNode(
        sentence,
        translationBySentence.get(sentence.sentenceId),
        inlineMarksBySentence.get(sentence.sentenceId) ?? [],
        entriesBySentence.get(sentence.sentenceId) ?? [],
      ),
    ),
  };
}

export function renderSceneToPlateDocument(
  readerScene: ReaderMockVm,
): ReaderPlateDocument {
  const sentenceById = new Map(
    readerScene.article.sentences
      .filter((sentence) => sentence.sentenceId && sentence.paragraphId && sentence.text)
      .map((sentence) => [sentence.sentenceId, sentence] as const),
  );

  const translationBySentence = buildTranslationBySentence(
    readerScene.translations,
    sentenceById,
  );
  const inlineMarksBySentence = buildInlineMarksBySentence(
    readerScene.inlineMarks,
    sentenceById,
  );
  const entriesBySentence = buildEntriesBySentence(
    readerScene.sentenceEntries,
    sentenceById,
  );

  const children: ReaderPlateDocument["children"] = [];
  const contentSummaryNode = createContentSummaryNode(readerScene.contentSummary);
  if (contentSummaryNode) {
    children.push(contentSummaryNode);
  }

  for (const paragraph of readerScene.article.paragraphs) {
    const sentences = paragraph.sentenceIds
      .map((sentenceId) => sentenceById.get(sentenceId))
      .filter((sentence): sentence is SentenceModel => Boolean(sentence));

    const paragraphNode = createParagraphNode(
      paragraph.paragraphId,
      sentences,
      translationBySentence,
      inlineMarksBySentence,
      entriesBySentence,
    );

    if (paragraphNode) {
      children.push(paragraphNode);
    }
  }

  return {
    type: "reader_document",
    schemaVersion: readerScene.schemaVersion,
    request: readerScene.request,
    userFacingState: readerScene.userFacingState,
    children,
  };
}
