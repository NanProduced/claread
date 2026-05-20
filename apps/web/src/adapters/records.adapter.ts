import type { RecordResponseDto } from "@/types/api/records";
import type {
  ArticleModel,
  AnnotationType,
  ContentSummaryCompleteness,
  ContentSummaryModel,
  ContentResultState,
  InlineGlossary,
  InlineMarkAnchor,
  InlineMarkModel,
  ParagraphModel,
  PhraseKind,
  ReaderMockVm,
  RenderType,
  SentenceEntryModel,
  SentenceEntryType,
  SentenceModel,
  TranslationModel,
  VisualTone,
  WarningLevel,
  WarningModel,
} from "@/types/view/ReaderMockVm";

type UnknownRecord = Record<string, unknown>;

export interface ReaderRecordVm {
  id: string;
  title: string;
  createdAt: string;
  sourceText: string;
  readingGoal: string;
  readingVariant: string;
  analysisStatus: string;
  reader: ReaderMockVm;
}

function isRecord(value: unknown): value is UnknownRecord {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function readString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function readOptionalString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function readBoolean(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function readArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function toContentResultState(value: unknown): ContentResultState {
  return value === "degraded_light" || value === "degraded_heavy" ? value : "normal";
}

function toContentSummaryCompleteness(value: unknown): ContentSummaryCompleteness {
  if (value === "full" || value === "partial" || value === "minimal") {
    return value;
  }

  return "minimal";
}

function toAnnotationType(value: unknown): AnnotationType {
  if (
    value === "phrase_gloss" ||
    value === "context_gloss" ||
    value === "grammar_note" ||
    value === "term_note" ||
    value === "logic_note"
  ) {
    return value;
  }

  return "vocab_highlight";
}

function inferVisualTone(annotationType: AnnotationType, value: unknown): VisualTone {
  if (value === "vocab" || value === "phrase" || value === "context" || value === "grammar" || value === "term" || value === "logic") {
    return value;
  }

  if (annotationType === "phrase_gloss") {
    return "phrase";
  }
  if (annotationType === "context_gloss") {
    return "context";
  }
  if (annotationType === "grammar_note") {
    return "grammar";
  }
  if (annotationType === "term_note") {
    return "term";
  }
  if (annotationType === "logic_note") {
    return "logic";
  }
  return "vocab";
}

function inferRenderType(annotationType: AnnotationType, value: unknown): RenderType {
  if (value === "background" || value === "underline") {
    return value;
  }
  return annotationType === "context_gloss" || annotationType === "grammar_note" || annotationType === "logic_note"
    ? "underline"
    : "background";
}

function inferLookupKind(annotationType: AnnotationType, value: unknown): PhraseKind | undefined {
  const explicit = toPhraseKind(value);
  if (explicit) {
    return explicit;
  }
  if (annotationType === "phrase_gloss") {
    return "phrase";
  }
  if (annotationType === "vocab_highlight" || annotationType === "context_gloss") {
    return "word";
  }
  return undefined;
}

function toPhraseKind(value: unknown): PhraseKind | undefined {
  if (
    value === "word" ||
    value === "phrase" ||
    value === "collocation" ||
    value === "phrasal_verb" ||
    value === "idiom" ||
    value === "proper_noun" ||
    value === "compound"
  ) {
    return value;
  }

  return undefined;
}

function toGlossaryPhraseType(value: unknown): InlineGlossary["phraseType"] {
  if (
    value === "collocation" ||
    value === "phrasal_verb" ||
    value === "idiom" ||
    value === "proper_noun" ||
    value === "compound"
  ) {
    return value;
  }

  return undefined;
}

function toWarningLevel(value: unknown): WarningLevel {
  if (value === "warning" || value === "error") {
    return value;
  }

  return "info";
}

function mapAnchor(value: unknown): InlineMarkAnchor | null {
  if (!isRecord(value)) {
    return null;
  }

  const sentenceId = readString(value.sentence_id ?? value.sentenceId);

  if (value.kind === "multi_text") {
    return {
      kind: "multi_text",
      sentenceId,
      parts: readArray(value.parts)
        .filter(isRecord)
        .map((part) => ({
          anchorText: readString(part.anchor_text ?? part.anchorText),
          occurrence: typeof part.occurrence === "number" ? part.occurrence : undefined,
          role: readOptionalString(part.role),
        }))
        .filter((part) => part.anchorText.length > 0),
    };
  }

  return {
    kind: "text",
    sentenceId,
    anchorText: readString(value.anchor_text ?? value.anchorText),
    occurrence: typeof value.occurrence === "number" ? value.occurrence : undefined,
  };
}

function mapGlossary(value: unknown): InlineGlossary | undefined {
  if (!isRecord(value)) {
    return undefined;
  }

  const phraseType = toGlossaryPhraseType(value.phrase_type ?? value.phraseType);

  return {
    zh: readOptionalString(value.zh),
    gloss: readOptionalString(value.gloss ?? value.context_definition ?? value.contextDefinition),
    reason: readOptionalString(value.reason),
    phraseType,
  };
}

function mapArticle(value: unknown, sourceText: string): ArticleModel {
  if (!isRecord(value)) {
    return sourceTextToArticle(sourceText);
  }

  const sentences: SentenceModel[] = readArray(value.sentences)
    .filter(isRecord)
    .map((sentence) => ({
      sentenceId: readString(sentence.sentence_id),
      paragraphId: readString(sentence.paragraph_id),
      text: readString(sentence.text),
    }))
    .filter((sentence) => sentence.sentenceId && sentence.text);

  const paragraphs: ParagraphModel[] = readArray(value.paragraphs)
    .filter(isRecord)
    .map((paragraph) => ({
      paragraphId: readString(paragraph.paragraph_id),
      sentenceIds: readArray(paragraph.sentence_ids)
        .map((id) => readString(id))
        .filter(Boolean),
    }))
    .filter((paragraph) => paragraph.paragraphId);

  if (sentences.length === 0 || paragraphs.length === 0) {
    return sourceTextToArticle(readString(value.source_text, sourceText));
  }

  return { paragraphs, sentences };
}

function mapTranslations(value: unknown): TranslationModel[] {
  return readArray(value)
    .filter(isRecord)
    .map((translation) => ({
      sentenceId: readString(translation.sentence_id),
      translationZh: readString(translation.translation_zh),
    }))
    .filter((translation) => translation.sentenceId);
}

function mapContentSummary(value: unknown): ContentSummaryModel | undefined {
  if (!isRecord(value)) {
    return undefined;
  }

  const overview = readString(value.overview).trim();
  if (!overview) {
    return undefined;
  }

  return {
    completeness: toContentSummaryCompleteness(value.completeness),
    overview,
    researchQuestion: readOptionalString(value.research_question ?? value.researchQuestion),
    methodology: readOptionalString(value.methodology),
    keyFindings: readArray(value.key_findings ?? value.keyFindings)
      .map((item) => readString(item).trim())
      .filter(Boolean),
    limitations: readArray(value.limitations)
      .map((item) => readString(item).trim())
      .filter(Boolean),
  };
}

function mapInlineMarks(value: unknown): InlineMarkModel[] {
  return readArray(value)
    .filter(isRecord)
    .map((mark): InlineMarkModel | null => {
      const anchor = mapAnchor(mark.anchor);
      if (!anchor) {
        return null;
      }
      const annotationType = toAnnotationType(mark.annotation_type ?? mark.annotationType);

      return {
        id: readString(mark.id),
        annotationType,
        anchor,
        renderType: inferRenderType(annotationType, mark.render_type ?? mark.renderType),
        visualTone: inferVisualTone(annotationType, mark.visual_tone ?? mark.visualTone),
        clickable: readBoolean(mark.clickable, annotationType !== "grammar_note"),
        lookupText: readOptionalString(mark.lookup_text ?? mark.lookupText),
        lookupKind: inferLookupKind(annotationType, mark.lookup_kind ?? mark.lookupKind),
        glossary: mapGlossary(mark.glossary),
        parentId: readOptionalString(mark.parent_id),
      };
    })
    .filter((mark): mark is InlineMarkModel => mark !== null && mark.id.length > 0);
}

function mapSentenceEntries(value: unknown): SentenceEntryModel[] {
  return readArray(value)
    .filter(isRecord)
    .map((entry) => {
      const sourceKind: SentenceEntryModel["sourceKind"] =
        entry.source_kind === "ask_supplement" ? "ask_supplement" : "workflow";
      return {
        id: readString(entry.id),
        sentenceId: readString(entry.sentence_id),
        entryType: readString(entry.entry_type, "sentence_analysis") as SentenceEntryType,
        label: readString(entry.label, "解析"),
        title: readOptionalString(entry.title),
        content: readString(entry.content),
        sourceKind,
        supplementId: readOptionalString(entry.supplement_id),
        deletable: readBoolean(entry.deletable, false),
        createdFromTurnRunId: readOptionalString(entry.created_from_turn_run_id),
      };
    })
    .filter((entry) => entry.id && entry.sentenceId);
}

function mapWarnings(value: unknown): WarningModel[] {
  return readArray(value)
    .filter(isRecord)
    .map((warning) => ({
      code: readString(warning.code, "unknown"),
      level: toWarningLevel(warning.level),
      message: readString(warning.message),
      sentenceId: readOptionalString(warning.sentence_id),
      annotationId: readOptionalString(warning.annotation_id),
    }));
}

function sourceTextToArticle(sourceText: string): ArticleModel {
  const paragraphs = sourceText
    .split(/\n{2,}/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean);

  const sentenceModels: SentenceModel[] = [];
  const paragraphModels: ParagraphModel[] = [];

  paragraphs.forEach((paragraphText, paragraphIndex) => {
    const paragraphId = `p${paragraphIndex}`;
    const sentenceIds: string[] = [];
    const sentenceTexts = paragraphText
      .split(/(?<=[.!?])\s+/)
      .map((sentence) => sentence.trim())
      .filter(Boolean);

    sentenceTexts.forEach((sentenceText) => {
      const sentenceId = `s${sentenceModels.length}`;
      sentenceIds.push(sentenceId);
      sentenceModels.push({
        sentenceId,
        paragraphId,
        text: sentenceText,
      });
    });

    paragraphModels.push({ paragraphId, sentenceIds });
  });

  if (sentenceModels.length === 0) {
    return {
      paragraphs: [{ paragraphId: "p0", sentenceIds: ["s0"] }],
      sentences: [{ sentenceId: "s0", paragraphId: "p0", text: sourceText }],
    };
  }

  return {
    paragraphs: paragraphModels,
    sentences: sentenceModels,
  };
}

export function adaptRecordToReaderRecord(record: RecordResponseDto): ReaderRecordVm {
  const renderScene = isRecord(record.render_scene_json) ? record.render_scene_json : {};
  const request = isRecord(renderScene.request) ? renderScene.request : {};
  const schemaVersion = readString(renderScene.schema_version, record.schema_version ?? "3.0.0");

  return {
    id: record.id,
    title: record.title ?? readString(renderScene.title, "Untitled record"),
    createdAt: record.created_at,
    sourceText: record.source_text,
    readingGoal: record.reading_goal ?? readString(request.reading_goal, "daily_reading"),
    readingVariant: record.reading_variant ?? readString(request.reading_variant, "intermediate_reading"),
    analysisStatus: record.analysis_status,
    reader: {
      schemaVersion,
      request: {
        requestId: readString(request.request_id, record.client_record_id ?? record.id),
        sourceType: readString(request.source_type, record.source_type),
        readingGoal: record.reading_goal ?? readString(request.reading_goal, "daily_reading"),
        readingVariant: record.reading_variant ?? readString(request.reading_variant, "intermediate_reading"),
        profileId: readString(request.profile_id, "upstream"),
      },
      article: mapArticle(renderScene.article, record.source_text),
      userFacingState: toContentResultState(renderScene.user_facing_state ?? record.user_facing_state),
      contentSummary: mapContentSummary(renderScene.content_summary),
      translations: mapTranslations(renderScene.translations),
      inlineMarks: mapInlineMarks(renderScene.inline_marks),
      sentenceEntries: mapSentenceEntries(renderScene.sentence_entries),
      warnings: mapWarnings(renderScene.warnings),
    },
  };
}
