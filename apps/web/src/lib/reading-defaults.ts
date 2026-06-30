import type {
  ReaderOrchestrationReadingGoalDto,
  ReaderOrchestrationReadingVariantDto,
} from "@/types/api/reader-plate";
import type { ReadingGoalDto, ReadingVariantDto } from "@/types/api/tasks";

export interface ReadingDefaultState {
  readingGoal: ReadingGoalDto;
  readingVariant: ReadingVariantDto;
}

// ---------------------------------------------------------------------------
// Reader Record submit scope
//
// The new Reader Orchestration backend only accepts `daily_reading` / `exam`
// (and their legal variants). `academic` / `academic_general` from legacy AI
// Workflow are rejected at the backend schema layer (422). The global
// `ReadingGoalDto` / `ReadingVariantDto` types still include `academic` for
// other surfaces (settings, legacy task); this scoped derivation filters it
// out for `/app/read` Reader Record submit so users never see or submit an
// unsupported strategy.
// ---------------------------------------------------------------------------

export type ReaderRecordReadingGoal = ReaderOrchestrationReadingGoalDto;
export type ReaderRecordReadingVariant = ReaderOrchestrationReadingVariantDto;

export interface ReaderRecordReadingDefaultState {
  readingGoal: ReaderRecordReadingGoal;
  readingVariant: ReaderRecordReadingVariant;
}

export const READER_RECORD_READING_GOAL_OPTIONS = [
  { value: "daily_reading", label: "日常阅读", description: "面向一般阅读，侧重读懂文章与自然积累表达。" },
  { value: "exam", label: "备考精读", description: "面向应试场景，突出长难句、考点与题感。" },
] as const satisfies ReadonlyArray<{
  value: ReaderRecordReadingGoal;
  label: string;
  description: string;
}>;

export const READER_RECORD_READING_VARIANT_OPTIONS: Record<
  ReaderRecordReadingGoal,
  Array<{ value: ReaderRecordReadingVariant; label: string; description: string }>
> = {
  daily_reading: [
    { value: "beginner_reading", label: "入门", description: "句意拆解更直白，适合先建立阅读信心。" },
    { value: "intermediate_reading", label: "进阶", description: "词句平衡，适合日常长期使用。" },
    { value: "intensive_reading", label: "精读", description: "更重视语法、结构和表达细节。" },
  ],
  exam: [
    { value: "gaokao", label: "高考", description: "中学语法与阅读题感优先。" },
    { value: "cet", label: "四六级", description: "快速定位主干信息与同义替换。" },
    { value: "kaoyan", label: "考研", description: "长难句结构和深层推理优先。" },
    { value: "tem", label: "专四专八", description: "修辞、文学语感和高级表达。" },
    { value: "ielts_toefl", label: "雅思托福", description: "信息提取、学术语境与题型判断。" },
  ],
};

export const READER_RECORD_DEFAULT_READING_VARIANT_BY_GOAL: Record<
  ReaderRecordReadingGoal,
  ReaderRecordReadingVariant
> = {
  daily_reading: "intermediate_reading",
  exam: "cet",
};

export const DEFAULT_READER_RECORD_READING_DEFAULTS: ReaderRecordReadingDefaultState = {
  readingGoal: "daily_reading",
  readingVariant: READER_RECORD_DEFAULT_READING_VARIANT_BY_GOAL.daily_reading,
};

function isReaderRecordReadingGoal(
  value: unknown,
): value is ReaderRecordReadingGoal {
  return value === "daily_reading" || value === "exam";
}

function isReaderRecordReadingVariant(
  value: unknown,
): value is ReaderRecordReadingVariant {
  return (
    value === "gaokao" ||
    value === "cet" ||
    value === "kaoyan" ||
    value === "tem" ||
    value === "ielts_toefl" ||
    value === "beginner_reading" ||
    value === "intermediate_reading" ||
    value === "intensive_reading"
  );
}

/**
 * Normalize a (possibly legacy / `academic`) reading defaults state into the
 * Reader Record submit scope. `academic` / `academic_general` are mapped to
 * the default `daily_reading` / `intermediate_reading` pair so the user is
 * never shown or submitted an unsupported strategy.
 */
export function normalizeReaderRecordReadingDefaults(
  input: Partial<ReadingDefaultState> | null | undefined,
): ReaderRecordReadingDefaultState {
  const rawGoal = input?.readingGoal;
  const goal: ReaderRecordReadingGoal = isReaderRecordReadingGoal(rawGoal)
    ? rawGoal
    : DEFAULT_READER_RECORD_READING_DEFAULTS.readingGoal;

  const rawVariant = input?.readingVariant;
  let variant: ReaderRecordReadingVariant;
  if (isReaderRecordReadingVariant(rawVariant)) {
    const allowedVariants = READER_RECORD_READING_VARIANT_OPTIONS[goal].map(
      (option) => option.value,
    );
    variant = allowedVariants.includes(
      rawVariant as ReaderRecordReadingVariant,
    )
      ? (rawVariant as ReaderRecordReadingVariant)
      : READER_RECORD_DEFAULT_READING_VARIANT_BY_GOAL[goal];
  } else {
    variant = READER_RECORD_DEFAULT_READING_VARIANT_BY_GOAL[goal];
  }

  return { readingGoal: goal, readingVariant: variant };
}

export const READING_GOAL_OPTIONS = [
  { value: "daily_reading", label: "日常阅读", description: "面向一般阅读，侧重读懂文章与自然积累表达。" },
  { value: "academic", label: "学术摘要", description: "面向论文与专业材料，强调术语、结构与论证关系。" },
  { value: "exam", label: "备考精读", description: "面向应试场景，突出长难句、考点与题感。" },
] as const satisfies ReadonlyArray<{
  value: ReadingGoalDto;
  label: string;
  description: string;
}>;

export const READING_VARIANT_OPTIONS: Record<
  ReadingGoalDto,
  Array<{ value: ReadingVariantDto; label: string; description: string }>
> = {
  daily_reading: [
    { value: "beginner_reading", label: "入门", description: "句意拆解更直白，适合先建立阅读信心。" },
    { value: "intermediate_reading", label: "进阶", description: "词句平衡，适合日常长期使用。" },
    { value: "intensive_reading", label: "精读", description: "更重视语法、结构和表达细节。" },
  ],
  academic: [
    { value: "academic_general", label: "学术通用", description: "关注术语、逻辑与摘要表达。" },
  ],
  exam: [
    { value: "gaokao", label: "高考", description: "中学语法与阅读题感优先。" },
    { value: "cet", label: "四六级", description: "快速定位主干信息与同义替换。" },
    { value: "kaoyan", label: "考研", description: "长难句结构和深层推理优先。" },
    { value: "tem", label: "专四专八", description: "修辞、文学语感和高级表达。" },
    { value: "ielts_toefl", label: "雅思托福", description: "信息提取、学术语境与题型判断。" },
  ],
};

export const DEFAULT_READING_VARIANT_BY_GOAL: Record<ReadingGoalDto, ReadingVariantDto> = {
  daily_reading: "intermediate_reading",
  academic: "academic_general",
  exam: "cet",
};

export const DEFAULT_READING_DEFAULTS: ReadingDefaultState = {
  readingGoal: "daily_reading",
  readingVariant: DEFAULT_READING_VARIANT_BY_GOAL.daily_reading,
};

function isReadingGoal(value: unknown): value is ReadingGoalDto {
  return value === "daily_reading" || value === "academic" || value === "exam";
}

function isReadingVariant(value: unknown): value is ReadingVariantDto {
  return (
    value === "gaokao" ||
    value === "cet" ||
    value === "kaoyan" ||
    value === "tem" ||
    value === "ielts_toefl" ||
    value === "beginner_reading" ||
    value === "intermediate_reading" ||
    value === "intensive_reading" ||
    value === "academic_general"
  );
}

export function normalizeReadingGoal(value: unknown): ReadingGoalDto {
  return isReadingGoal(value) ? value : DEFAULT_READING_DEFAULTS.readingGoal;
}

export function normalizeReadingVariant(goal: ReadingGoalDto, value: unknown): ReadingVariantDto {
  if (!isReadingVariant(value)) {
    return DEFAULT_READING_VARIANT_BY_GOAL[goal];
  }

  const allowedVariants = READING_VARIANT_OPTIONS[goal].map((option) => option.value);
  return allowedVariants.includes(value) ? value : DEFAULT_READING_VARIANT_BY_GOAL[goal];
}

export function normalizeReadingDefaults(input: Partial<ReadingDefaultState> | null | undefined): ReadingDefaultState {
  const goal = normalizeReadingGoal(input?.readingGoal);
  return {
    readingGoal: goal,
    readingVariant: normalizeReadingVariant(goal, input?.readingVariant),
  };
}

export function readReadingDefaultsFromSettings(
  settings: Record<string, unknown> | null | undefined,
): ReadingDefaultState {
  if (!settings || typeof settings !== "object") {
    return DEFAULT_READING_DEFAULTS;
  }

  const goal = normalizeReadingGoal(settings.default_reading_goal);
  const variant = normalizeReadingVariant(goal, settings.default_reading_variant);
  return {
    readingGoal: goal,
    readingVariant: variant,
  };
}
