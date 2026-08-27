import type {
  ComprehensionCheckpointDto,
  DailyReaderArticleDto,
  DailyReaderListItemDto,
  LanguageTargetDto,
  SentenceMapDto,
  TransferTaskDto,
} from "@/types/api/daily-reader";
import type {
  DailyReaderArticle,
  DailyReaderCheckpoint,
  DailyReaderLanguageTarget,
  DailyReaderListItem,
  DailyReaderSentenceMap,
  DailyReaderTransferTask,
} from "@/types/view/DailyReaderVm";

function stripHtml(value: string | null | undefined): string | null {
  if (!value) {
    return value ?? null;
  }
  return value.replace(/<[^>]+>/g, "").trim();
}

function cleanText(value: string | null | undefined): string {
  return value?.trim() ?? "";
}

function cleanOrNull(value: string | null | undefined): string | null {
  const cleaned = cleanText(value);
  return cleaned || null;
}

function dtoToCheckpoint(dto: ComprehensionCheckpointDto): DailyReaderCheckpoint {
  return {
    skill: cleanText(dto.skill),
    prompt: cleanText(dto.prompt),
    promptSubject: cleanOrNull(dto.prompt_subject),
    referenceAnswer: cleanText(dto.reference_answer),
    answerSubject: cleanOrNull(dto.reference_answer_subject),
    evidenceUnitIds: Array.isArray(dto.evidence_paragraph_ids)
      ? dto.evidence_paragraph_ids
      : [],
    answerEvidenceUnitIds: Array.isArray(dto.answer_evidence_paragraph_ids)
      ? dto.answer_evidence_paragraph_ids
      : [],
  };
}

function dtoToLanguageTarget(dto: LanguageTargetDto): DailyReaderLanguageTarget {
  return {
    expression: cleanText(dto.expression),
    unitId: cleanText(dto.paragraph_id),
    targetKind: cleanOrNull(dto.target_kind),
    teachingPurpose: cleanOrNull(dto.teaching_purpose),
    meaningZh: cleanText(dto.meaning_zh),
    usageNote: cleanText(dto.usage_note),
    reusablePattern: cleanText(dto.reusable_pattern),
  };
}

function dtoToSentenceMap(dto: SentenceMapDto): DailyReaderSentenceMap {
  return {
    sentence: cleanText(dto.sentence),
    unitId: cleanText(dto.paragraph_id),
    translation: cleanText(dto.translation),
    complexityKind:
      dto.complexity_kind === "complex_syntax" || dto.complexity_kind === "argument_structure"
        ? dto.complexity_kind
        : null,
    teachingPurpose: cleanOrNull(dto.teaching_purpose),
  };
}

function dtoToTransferTask(dto: TransferTaskDto | undefined): DailyReaderTransferTask | null {
  if (!dto) return null;
  const prompt = cleanText(dto.prompt);
  if (!prompt) return null;
  return {
    taskKind: cleanText(dto.task_kind),
    prompt,
    scaffold: cleanOrNull(dto.scaffold),
    referencePoints: Array.isArray(dto.reference_points)
      ? dto.reference_points.map(cleanText).filter(Boolean)
      : [],
    contentRequirement: cleanOrNull(dto.content_requirement),
  };
}

export function dtoToDailyReaderArticle(dto: DailyReaderArticleDto): DailyReaderArticle {
  const blueprint = dto.lesson_blueprint ?? {};
  const pkg = dto.learning_package ?? {};
  const translations = pkg.translations_by_paragraph_id ?? {};
  const highDifficulty = new Set(
    Array.isArray(pkg.high_difficulty_unit_ids) ? pkg.high_difficulty_unit_ids : [],
  );
  const units = Array.isArray(dto.reading_units) ? dto.reading_units : [];

  // checkpoints 与 transfer_task 在两容器共存：学习包副本是教学内容，优先。
  const checkpointDtos = Array.isArray(pkg.comprehension_checkpoints)
    ? pkg.comprehension_checkpoints
    : Array.isArray(blueprint.comprehension_checkpoints)
      ? blueprint.comprehension_checkpoints
      : [];
  const transferTask = dtoToTransferTask(pkg.transfer_task ?? blueprint.transfer_task);

  return {
    id: dto.id,
    title: cleanText(dto.title),
    subtitle: stripHtml(dto.subtitle),
    originalTitle: cleanOrNull(dto.original_title),
    subtitleZh: cleanOrNull(dto.subtitle_zh),
    source: dto.source,
    sourceUrl: dto.source_url,
    publishDate: dto.publish_date,
    difficulty: dto.difficulty,
    articleType: cleanOrNull(blueprint.article_type),
    readTimeMinutes: dto.read_time_minutes,
    tags: Array.isArray(dto.tags) ? dto.tags : [],
    coverImageUrl: dto.cover_image_url,
    coverTheme: dto.cover_theme,
    mission: blueprint.reading_mission
      ? {
          reading: cleanText(blueprint.reading_mission),
          objectives: Array.isArray(blueprint.learning_objectives)
            ? blueprint.learning_objectives.map(cleanText).filter(Boolean)
            : [],
        }
      : null,
    units: units.map((unit) => {
      const translation = cleanOrNull(translations[unit.id]);
      return {
        id: unit.id,
        text: unit.text,
        translation,
        isHighDifficulty: highDifficulty.has(unit.id),
      };
    }),
    structureMap: Array.isArray(blueprint.structure_map)
      ? blueprint.structure_map.map((node) => ({
          label: cleanText(node.label),
          role: cleanText(node.function),
          unitIds: Array.isArray(node.paragraph_ids) ? node.paragraph_ids : [],
        }))
      : [],
    languageTargets: Array.isArray(pkg.language_targets)
      ? pkg.language_targets.map(dtoToLanguageTarget)
      : [],
    sentenceMaps: Array.isArray(pkg.sentence_maps) ? pkg.sentence_maps.map(dtoToSentenceMap) : [],
    checkpoints: checkpointDtos.map(dtoToCheckpoint),
    transferTask,
    postReadSummary: cleanOrNull(pkg.post_read_summary),
    translationCoverage: {
      translated: units.filter((unit) => cleanOrNull(translations[unit.id])).length,
      total: units.length,
    },
  };
}

export function dtoToDailyReaderListItem(dto: DailyReaderListItemDto): DailyReaderListItem {
  return {
    id: dto.id,
    title: cleanText(dto.title),
    subtitle: stripHtml(dto.subtitle),
    originalTitle: cleanOrNull(dto.original_title),
    subtitleZh: cleanOrNull(dto.subtitle_zh),
    source: dto.source,
    publishDate: dto.publish_date,
    difficulty: dto.difficulty,
    readTimeMinutes: dto.read_time_minutes,
    tags: Array.isArray(dto.tags) ? dto.tags : [],
    coverImageUrl: dto.cover_image_url,
    coverTheme: dto.cover_theme,
  };
}
