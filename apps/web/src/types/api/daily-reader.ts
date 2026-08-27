/**
 * Daily Reader API DTO（teaching-v2 形状）。
 *
 * 服务端 `DailyReaderArticleResponse`：标量列 + lesson_blueprint +
 * learning_package + reading_units。v1 的 body/highlights/paragraph_notes/
 * takeaways 已退役，客户端不得再消费（零投影决策）。
 */

export interface DailyReaderArticleDto {
  id: string;
  title: string;
  subtitle: string | null;
  /** 英文原题（caption 级展示）；pre-v2 行可能缺失。 */
  original_title?: string | null;
  /** 中文副标题（blueprint 产出并提升到列）。 */
  subtitle_zh?: string | null;
  source: string;
  source_url: string;
  publish_date: string;
  difficulty: string;
  read_time_minutes: number;
  tags: string[];
  cover_image_url: string | null;
  cover_theme: string;
  /** v2 教学蓝图；pre-v2 行为 {}。 */
  lesson_blueprint: LessonBlueprintDto;
  /** v2 学习包；pre-v2 行为 {}。 */
  learning_package: LearningPackageDto;
  /** 正文单元（id 与 lesson 锚点一致）；pre-v2 行为 []。 */
  reading_units: DailyReaderReadingUnitDto[];
}

export interface DailyReaderReadingUnitDto {
  id: string;
  text: string;
}

export interface LessonBlueprintDto {
  article_type?: string;
  effective_difficulty?: string;
  title_zh?: string;
  subtitle_zh?: string;
  tags_zh?: string[];
  reading_mission?: string;
  reading_mission_stance?: string;
  learning_objectives?: string[];
  structure_map?: LessonStructureNodeDto[];
  selected_paragraph_ids?: string[];
  comprehension_checkpoints?: ComprehensionCheckpointDto[];
  transfer_task?: TransferTaskDto;
}

export interface LessonStructureNodeDto {
  label: string;
  function: string;
  paragraph_ids: string[];
}

export interface ComprehensionCheckpointDto {
  skill: string;
  prompt: string;
  prompt_subject?: string;
  reference_answer: string;
  reference_answer_subject?: string;
  evidence_paragraph_ids?: string[];
  answer_evidence_paragraph_ids?: string[];
}

export interface TransferTaskDto {
  task_kind: string;
  prompt: string;
  scaffold?: string;
  reference_points?: string[];
  content_requirement?: string;
}

export interface LearningPackageDto {
  language_targets?: LanguageTargetDto[];
  sentence_maps?: SentenceMapDto[];
  translations_by_paragraph_id?: Record<string, string>;
  comprehension_checkpoints?: ComprehensionCheckpointDto[];
  transfer_task?: TransferTaskDto;
  post_read_summary?: string;
  high_difficulty_unit_ids?: string[];
}

export interface LanguageTargetDto {
  expression: string;
  paragraph_id: string;
  target_kind?: string;
  teaching_purpose?: string;
  meaning_zh: string;
  usage_note: string;
  reusable_pattern: string;
}

export interface SentenceMapDto {
  sentence: string;
  paragraph_id: string;
  translation: string;
  complexity_kind?: "complex_syntax" | "argument_structure" | null;
  teaching_purpose?: string;
}

export interface DailyReaderTodayResponseDto {
  articles: DailyReaderArticleDto[];
}

export interface DailyReaderListItemDto {
  id: string;
  title: string;
  subtitle: string | null;
  original_title?: string | null;
  subtitle_zh?: string | null;
  source: string;
  publish_date: string;
  difficulty: string;
  read_time_minutes: number;
  tags: string[];
  cover_image_url: string | null;
  cover_theme: string;
}

export interface DailyReaderListResponseDto {
  items: DailyReaderListItemDto[];
  cursor: string | null;
  has_more: boolean;
}
