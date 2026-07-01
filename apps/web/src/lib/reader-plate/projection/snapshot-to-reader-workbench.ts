import type {
  ReaderAnchorSegmentNodeDto,
  ReaderGrammarNoteMarkDto,
  ReaderPlateSnapshotDto,
  ReaderSourceBlockChildNodeDto,
  ReaderTranslationGroupNodeDto,
  ReaderTranslationNodeDto,
  ReaderUnitChildNodeDto,
  ReaderUnitNodeDto,
  ReaderVocabularyMarkDto,
  VocabularyPhraseType,
} from "@/types/api/reader-plate";
import type {
  InlineGlossary,
  InlineMarkModel,
  ParagraphModel,
  PhraseType,
  ReaderMockVm,
  SentenceEntryModel,
  SentenceModel,
  TranslationModel,
} from "@/types/view/ReaderMockVm";

import type { ReaderPlateDocument } from "../model";
import { renderSceneToPlateDocument } from "./render-scene-to-plate-document";

type SnapshotProjectionContext = {
  anchorSegmentById: Map<string, ReaderAnchorSegmentNodeDto>;
  sentenceByAnchorSegmentId: Map<string, SentenceModel>;
  sentenceIdsByUnitId: Map<string, string[]>;
};

function isAnchorSegmentNode(
  node: ReaderSourceBlockChildNodeDto,
): node is ReaderAnchorSegmentNodeDto {
  return "type" in node && node.type === "reader_anchor_segment";
}

function isSourceBlockNode(
  node: ReaderUnitChildNodeDto,
): node is Extract<ReaderUnitChildNodeDto, { type: "reader_source_block" }> {
  return node.type === "reader_source_block";
}

function isTranslationNode(
  node: ReaderUnitChildNodeDto,
): node is ReaderTranslationNodeDto | ReaderTranslationGroupNodeDto {
  return (
    node.type === "reader_translation" ||
    node.type === "reader_translation_group"
  );
}

function sentenceIdForAnchor(anchor: ReaderAnchorSegmentNodeDto): string {
  return anchor.sentence_id || anchor.anchor_segment_id;
}

function textFromAnchorSegment(anchor: ReaderAnchorSegmentNodeDto): string {
  return anchor.children.map((leaf) => leaf.text).join("");
}

function textFromTranslation(
  node: ReaderTranslationNodeDto | ReaderTranslationGroupNodeDto,
): string {
  return node.children.map((leaf) => leaf.text).join("").trim();
}

function sentenceIdForTranslation(
  translation: ReaderTranslationNodeDto | ReaderTranslationGroupNodeDto,
  unit: ReaderUnitNodeDto,
  context: SnapshotProjectionContext,
): string | undefined {
  // Reader Workbench remains a legacy sentence-oriented fallback. It cannot
  // represent group-native translations losslessly, so a translation group is
  // attached to its first covered sentence instead of being split or duplicated.
  if (translation.type === "reader_translation_group") {
    const firstAnchorSegmentId = translation.covered_anchor_segment_ids[0];
    return firstAnchorSegmentId
      ? context.sentenceByAnchorSegmentId.get(firstAnchorSegmentId)?.sentenceId
      : context.sentenceIdsByUnitId.get(unit.unit_id)?.[0];
  }

  return translation.target_scope === "anchor_segment"
    ? context.sentenceByAnchorSegmentId.get(
        translation.anchor_segment_id ?? translation.target_key,
      )?.sentenceId
    : context.sentenceIdsByUnitId.get(unit.unit_id)?.[0];
}

function phraseTypeForWorkbench(
  phraseType: VocabularyPhraseType,
): PhraseType | undefined {
  return phraseType === "other" ? undefined : phraseType;
}

function glossaryFromVocabularyMark(
  mark: ReaderVocabularyMarkDto,
): InlineGlossary | undefined {
  if (mark.item_type === "vocab_highlight") {
    return {
      zh: mark.brief_explanation ?? undefined,
      reason: mark.reason ?? undefined,
    };
  }

  if (mark.item_type === "phrase_gloss") {
    return {
      gloss: mark.gloss,
      phraseType: phraseTypeForWorkbench(mark.phrase_type),
    };
  }

  return {
    gloss: mark.gloss,
    reason: mark.reason,
  };
}

function inlineMarkFromVocabularyMark(
  mark: ReaderVocabularyMarkDto,
  sentenceId: string,
): InlineMarkModel {
  const isWordMark = mark.item_type === "vocab_highlight";

  return {
    id: mark.mark_id,
    annotationType: mark.item_type,
    anchor: {
      kind: "range",
      sentenceId,
      offsetUnit: "utf16",
      start: mark.segment_start_utf16,
      end: mark.segment_end_utf16,
      text: mark.selected_text,
    },
    renderType: "background",
    visualTone:
      mark.item_type === "phrase_gloss"
        ? "phrase"
        : mark.item_type === "context_gloss"
          ? "context"
          : "vocab",
    clickable: true,
    lookupKind: isWordMark ? "word" : "phrase",
    lookupText:
      mark.item_type === "vocab_highlight"
        ? mark.headword
        : mark.item_type === "phrase_gloss"
          ? mark.phrase
          : mark.display,
    glossary: glossaryFromVocabularyMark(mark),
  };
}

function inlineMarkFromGrammarNoteMark(
  mark: ReaderGrammarNoteMarkDto,
  sentenceId: string,
): InlineMarkModel {
  return {
    id: mark.mark_id,
    parentId: mark.item_id,
    annotationType: "grammar_note",
    anchor: {
      kind: "range",
      sentenceId,
      offsetUnit: "utf16",
      start: mark.segment_start_utf16,
      end: mark.segment_end_utf16,
      text: mark.selected_text,
    },
    renderType: "underline",
    visualTone: "grammar",
    clickable: false,
    lookupKind: "phrase",
    lookupText: mark.pattern ?? mark.grammar_point,
    glossary: {
      gloss: mark.note,
      reason: mark.grammar_point,
    },
  };
}

function sentenceEntryFromGrammarNoteMark(
  mark: ReaderGrammarNoteMarkDto,
  sentenceId: string,
): SentenceEntryModel {
  return {
    id: mark.item_id,
    sentenceId,
    entryType: "grammar_note",
    label: "语法旁注",
    title: mark.grammar_point,
    content: mark.note,
    analysisText: mark.note,
    sourceKind: "workflow",
  };
}

function sentenceEntryFromAnalysisNode(
  node: Extract<ReaderUnitChildNodeDto, { type: "reader_sentence_analysis" }>,
  context: SnapshotProjectionContext,
): SentenceEntryModel | null {
  const sentence = context.sentenceByAnchorSegmentId.get(node.anchor_segment_id);
  if (!sentence) {
    return null;
  }

  return {
    id: node.analysis_id,
    sentenceId: sentence.sentenceId,
    entryType: "sentence_analysis",
    label: node.label || "句式拆解",
    title: node.label || "句式拆解",
    content: node.analysis || node.children.map((leaf) => leaf.text).join(""),
    analysisText: node.analysis,
    chunks: node.chunks.map((chunk) => ({
      order: chunk.order,
      label: chunk.label,
      text: chunk.text,
    })),
    sourceKind: "workflow",
  };
}

function buildArticleAndContext(value: ReaderUnitNodeDto[]): {
  article: ReaderMockVm["article"];
  context: SnapshotProjectionContext;
} {
  const paragraphs: ParagraphModel[] = [];
  const sentences: SentenceModel[] = [];
  const anchorSegmentById = new Map<string, ReaderAnchorSegmentNodeDto>();
  const sentenceByAnchorSegmentId = new Map<string, SentenceModel>();
  const sentenceIdsByUnitId = new Map<string, string[]>();

  value.forEach((unit) => {
    const sentenceIds: string[] = [];

    unit.children.filter(isSourceBlockNode).forEach((sourceBlock) => {
      sourceBlock.children.filter(isAnchorSegmentNode).forEach((anchor) => {
        const sentenceId = sentenceIdForAnchor(anchor);
        const sentence: SentenceModel = {
          sentenceId,
          paragraphId: unit.unit_id,
          text: textFromAnchorSegment(anchor),
        };

        anchorSegmentById.set(anchor.anchor_segment_id, anchor);
        sentenceByAnchorSegmentId.set(anchor.anchor_segment_id, sentence);
        sentenceIds.push(sentenceId);
        sentences.push(sentence);
      });
    });

    sentenceIdsByUnitId.set(unit.unit_id, sentenceIds);

    if (sentenceIds.length > 0) {
      paragraphs.push({
        paragraphId: unit.unit_id,
        sentenceIds,
      });
    }
  });

  return {
    article: { paragraphs, sentences },
    context: {
      anchorSegmentById,
      sentenceByAnchorSegmentId,
      sentenceIdsByUnitId,
    },
  };
}

function buildTranslations(
  value: ReaderUnitNodeDto[],
  context: SnapshotProjectionContext,
): TranslationModel[] {
  const translations: TranslationModel[] = [];
  const seen = new Set<string>();

  value.forEach((unit) => {
    unit.children.filter(isTranslationNode).forEach((translation) => {
      const translationText = textFromTranslation(translation);
      if (!translationText) {
        return;
      }

      const sentenceId = sentenceIdForTranslation(translation, unit, context);

      if (!sentenceId || seen.has(sentenceId)) {
        return;
      }

      seen.add(sentenceId);
      translations.push({
        sentenceId,
        translationZh: translationText,
      });
    });
  });

  return translations;
}

function buildInlineMarksAndGrammarEntries(
  context: SnapshotProjectionContext,
): {
  inlineMarks: InlineMarkModel[];
  grammarEntries: SentenceEntryModel[];
} {
  const inlineMarks: InlineMarkModel[] = [];
  const grammarEntries: SentenceEntryModel[] = [];
  const seenInlineMarkIds = new Set<string>();
  const seenGrammarEntryIds = new Set<string>();

  context.anchorSegmentById.forEach((anchor) => {
    const sentence = context.sentenceByAnchorSegmentId.get(anchor.anchor_segment_id);
    if (!sentence) {
      return;
    }

    anchor.children.forEach((leaf) => {
      leaf.reader_vocabulary_marks?.forEach((mark) => {
        if (seenInlineMarkIds.has(mark.mark_id)) {
          return;
        }
        seenInlineMarkIds.add(mark.mark_id);
        inlineMarks.push(inlineMarkFromVocabularyMark(mark, sentence.sentenceId));
      });

      leaf.reader_grammar_note_marks?.forEach((mark) => {
        if (!seenInlineMarkIds.has(mark.mark_id)) {
          seenInlineMarkIds.add(mark.mark_id);
          inlineMarks.push(inlineMarkFromGrammarNoteMark(mark, sentence.sentenceId));
        }

        if (!seenGrammarEntryIds.has(mark.item_id)) {
          seenGrammarEntryIds.add(mark.item_id);
          grammarEntries.push(
            sentenceEntryFromGrammarNoteMark(mark, sentence.sentenceId),
          );
        }
      });
    });
  });

  return { inlineMarks, grammarEntries };
}

function buildSentenceAnalysisEntries(
  value: ReaderUnitNodeDto[],
  context: SnapshotProjectionContext,
): SentenceEntryModel[] {
  return value.flatMap((unit) =>
    unit.children
      .filter((node): node is Extract<ReaderUnitChildNodeDto, { type: "reader_sentence_analysis" }> =>
        node.type === "reader_sentence_analysis",
      )
      .map((node) => sentenceEntryFromAnalysisNode(node, context))
      .filter((entry): entry is SentenceEntryModel => Boolean(entry)),
  );
}

export function adaptReaderPlateSnapshotToReaderVm(
  snapshot: ReaderPlateSnapshotDto,
): ReaderMockVm {
  const { article, context } = buildArticleAndContext(snapshot.value);
  const { inlineMarks, grammarEntries } = buildInlineMarksAndGrammarEntries(context);
  const sentenceAnalysisEntries = buildSentenceAnalysisEntries(
    snapshot.value,
    context,
  );

  return {
    schemaVersion: snapshot.schema_kind,
    request: {
      requestId: snapshot.snapshot_id,
      sourceType: "reader_plate_snapshot",
      readingGoal: "daily_reading",
      readingVariant: "intermediate_reading",
      profileId: "reader-plate-snapshot",
    },
    article,
    userFacingState: "normal",
    translations: buildTranslations(snapshot.value, context),
    inlineMarks,
    sentenceEntries: [...grammarEntries, ...sentenceAnalysisEntries],
    warnings: [],
  };
}

export function adaptReaderPlateSnapshotToPlateDocument(
  snapshot: ReaderPlateSnapshotDto,
): ReaderPlateDocument {
  return renderSceneToPlateDocument(adaptReaderPlateSnapshotToReaderVm(snapshot));
}
