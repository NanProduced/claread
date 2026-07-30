import type {
  ReaderOrchestrationReadingGoalDto,
  ReaderOrchestrationReadingVariantDto,
} from "@/types/api/reader-plate";

// ---------------------------------------------------------------------------
// Reader reading-plan contract
//
// The new Reader Orchestration backend only accepts `daily_reading` / `exam`
// and their legal variants. `academic` / `academic_general` belong to the
// retired legacy workflow: persisted legacy values are normalized at the
// boundary, but no current creation or preference surface may expose them.
//
// All Reader UI surfaces consume this one option/copy/default contract so the
// per-article override and account preference cannot drift.
// ---------------------------------------------------------------------------

export type ReaderRecordReadingGoal = ReaderOrchestrationReadingGoalDto;
export type ReaderRecordReadingVariant = ReaderOrchestrationReadingVariantDto;

export interface ReadingDefaultState {
  readingGoal: ReaderRecordReadingGoal;
  readingVariant: ReaderRecordReadingVariant;
}

export type ReaderRecordReadingDefaultState = ReadingDefaultState;

export interface ReadingPlanOption<T extends string> {
  value: T;
  label: string;
  description: string;
}

export const READING_GOAL_OPTIONS = [
  {
    value: "daily_reading",
    label: "日常阅读",
    description: "兼顾理解、词汇与表达积累，适合持续阅读。",
  },
  {
    value: "exam",
    label: "备考精读",
    description: "围绕考试要求，突出长难句、考点与题感。",
  },
] as const satisfies ReadonlyArray<{
  value: ReaderRecordReadingGoal;
  label: string;
  description: string;
}>;

export const READING_VARIANT_OPTIONS: Record<
  ReaderRecordReadingGoal,
  Array<ReadingPlanOption<ReaderRecordReadingVariant>>
> = {
  daily_reading: [
    {
      value: "beginner_reading",
      label: "入门",
      description: "句意直白拆解，注释更详尽，适合建立信心。",
    },
    {
      value: "intermediate_reading",
      label: "进阶",
      description: "平衡理解、词汇与语法，适合日常泛读。",
    },
    {
      value: "intensive_reading",
      label: "精读",
      description: "深度拆解语法、结构与表达细节，适合深度学习。",
    },
  ],
  exam: [
    { value: "gaokao", label: "高考", description: "贴近高中课标，突出核心词汇、语法与常见题型。" },
    { value: "cet", label: "四六级", description: "抓取主干信息与同义替换，训练常见考点。" },
    { value: "kaoyan", label: "考研", description: "拆解长难句与篇章逻辑，强化深层推理。" },
    { value: "tem", label: "专四专八", description: "关注高级语法、修辞与语言表达，适合专业考试。" },
    { value: "ielts_toefl", label: "雅思托福", description: "适应学术语境，训练信息定位与题型判断。" },
  ],
};

export const DEFAULT_READING_VARIANT_BY_GOAL: Record<
  ReaderRecordReadingGoal,
  ReaderRecordReadingVariant
> = {
  daily_reading: "intermediate_reading",
  exam: "cet",
};

export const DEFAULT_READING_DEFAULTS: ReadingDefaultState = {
  readingGoal: "daily_reading",
  readingVariant: DEFAULT_READING_VARIANT_BY_GOAL.daily_reading,
};

function isReadingGoal(value: unknown): value is ReaderRecordReadingGoal {
  return value === "daily_reading" || value === "exam";
}

function isReadingVariant(value: unknown): value is ReaderRecordReadingVariant {
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
 * Normalize a possibly stale or legacy reading plan into the current Reader
 * contract. Retired `academic` values map to the broad, non-exam default.
 */
export function normalizeReadingDefaults(
  input: Partial<ReadingDefaultState> | null | undefined,
): ReadingDefaultState {
  const rawGoal = input?.readingGoal;
  const goal: ReaderRecordReadingGoal = isReadingGoal(rawGoal)
    ? rawGoal
    : DEFAULT_READING_DEFAULTS.readingGoal;

  const rawVariant = input?.readingVariant;
  let variant: ReaderRecordReadingVariant;
  if (isReadingVariant(rawVariant)) {
    const allowedVariants = READING_VARIANT_OPTIONS[goal].map(
      (option) => option.value,
    );
    variant = allowedVariants.includes(
      rawVariant as ReaderRecordReadingVariant,
    )
      ? (rawVariant as ReaderRecordReadingVariant)
      : DEFAULT_READING_VARIANT_BY_GOAL[goal];
  } else {
    variant = DEFAULT_READING_VARIANT_BY_GOAL[goal];
  }

  return { readingGoal: goal, readingVariant: variant };
}

export const READER_RECORD_READING_GOAL_OPTIONS = READING_GOAL_OPTIONS;
export const READER_RECORD_READING_VARIANT_OPTIONS = READING_VARIANT_OPTIONS;
export const READER_RECORD_DEFAULT_READING_VARIANT_BY_GOAL =
  DEFAULT_READING_VARIANT_BY_GOAL;
export const DEFAULT_READER_RECORD_READING_DEFAULTS = DEFAULT_READING_DEFAULTS;
export const normalizeReaderRecordReadingDefaults = normalizeReadingDefaults;

export function getReadingGoalOption(goal: ReaderRecordReadingGoal) {
  return READING_GOAL_OPTIONS.find((option) => option.value === goal);
}

export function getReadingVariantOption(
  goal: ReaderRecordReadingGoal,
  variant: ReaderRecordReadingVariant,
) {
  return READING_VARIANT_OPTIONS[goal].find((option) => option.value === variant);
}

export function formatReadingPlanSummary(
  goal: ReaderRecordReadingGoal,
  variant: ReaderRecordReadingVariant,
): string {
  const goalLabel = getReadingGoalOption(goal)?.label;
  const variantLabel = getReadingVariantOption(goal, variant)?.label;
  return [goalLabel, variantLabel].filter(Boolean).join(" · ");
}

export function readReadingDefaultsFromSettings(
  settings: Record<string, unknown> | null | undefined,
): ReadingDefaultState {
  if (!settings || typeof settings !== "object") {
    return DEFAULT_READING_DEFAULTS;
  }

  return normalizeReadingDefaults({
    readingGoal: settings.default_reading_goal as ReaderRecordReadingGoal,
    readingVariant: settings.default_reading_variant as ReaderRecordReadingVariant,
  });
}
