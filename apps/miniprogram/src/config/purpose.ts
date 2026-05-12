export type ReadingGoal = 'exam' | 'academic' | 'daily';
export type ServerReadingGoal = 'exam' | 'daily_reading' | 'academic';
export type ReadingVariant =
  | 'gaokao'
  | 'cet'
  | 'kaoyan'
  | 'tem'
  | 'ielts_toefl'
  | 'beginner_reading'
  | 'intermediate_reading'
  | 'intensive_reading'
  | 'academic_general';

export interface VariantOption {
  value: ReadingVariant; // This matches the reading_variant expected by the server
  label: string;
  description?: string;
}

export interface PurposeOption {
  label: string;
  description: string;
  icon: string;
  serverGoal: ServerReadingGoal; // This matches the reading_goal expected by the server
  defaultVariant: ReadingVariant;
  variants?: VariantOption[];
}

export const READING_CONFIG_MAP: Record<ReadingGoal, PurposeOption> = {
  exam: {
    label: '考试备考',
    description: '面向考试场景，侧重考点理解、长难句拆解和高频词汇。',
    icon: 'graduationCap',
    serverGoal: 'exam',
    defaultVariant: 'cet',
    variants: [
      { value: 'gaokao', label: '高考英语', description: '语法填空与阅读高频考点，逐步搭建句型框架' },
      { value: 'cet', label: '大学四六级(CET-4/6)', description: '训练快速定位与同义替换，突破阅读提速瓶颈' },
      { value: 'kaoyan', label: '考研英语', description: '主攻长难句与深度推理，还原命题思路' },
      { value: 'tem', label: '专业英语(TEM-4/8)', description: '精读文学语篇，强化翻译与高级语言运用' },
      { value: 'ielts_toefl', label: '雅思/托福', description: '熟悉学术语境下的同义改写与段落论证逻辑' }
    ]
  },
  daily: {
    label: '日常阅读',
    description: '面向日常阅读，侧重读懂文章并自然积累词汇与表达。',
    icon: 'coffee',
    serverGoal: 'daily_reading',
    defaultVariant: 'intermediate_reading',
    variants: [
      { value: 'beginner_reading', label: '入门模式', description: '适合初学者，重点帮你先读懂词义和句子' },
      { value: 'intermediate_reading', label: '进阶模式', description: '适合有一定基础，侧重语境理解和自然表达' },
      { value: 'intensive_reading', label: '精读模式', description: '适合深入精读，逐句拆解结构、用法和表达细节' }
    ]
  },
  academic: {
    label: '学术文献',
    description: '面向论文和专业材料，侧重术语理解、结构梳理和论证关系。',
    icon: 'microscope',
    serverGoal: 'academic',
    defaultVariant: 'academic_general'
  }
};

export const SERVER_GOAL_TO_UI_GOAL: Record<string, ReadingGoal> = {
  exam: 'exam',
  daily_reading: 'daily',
  academic: 'academic',
};

const LEGACY_VARIANT_ALIASES: Record<string, ReadingVariant> = {
  cet4: 'cet',
  cet6: 'cet',
  kaoyan: 'kaoyan',
  tem: 'tem',
  ielts: 'ielts_toefl',
  toefl: 'ielts_toefl',
  beginner: 'beginner_reading',
  intermediate: 'intermediate_reading',
  advanced: 'intensive_reading',
};

export const normalizeVariantForGoal = (
  goal: ReadingGoal,
  variant?: string | null
): ReadingVariant => {
  const config = READING_CONFIG_MAP[goal];
  const candidate =
    (variant && LEGACY_VARIANT_ALIASES[variant]) ||
    (variant as ReadingVariant | null) ||
    config.defaultVariant;
  const allowedVariants = config.variants?.map((item) => item.value) || [config.defaultVariant];
  return allowedVariants.includes(candidate) ? candidate : config.defaultVariant;
};

/**
 * 获取显示的标签文本
 */
export const getDisplayLabel = (goal: ReadingGoal, variant?: string | null) => {
  const config = READING_CONFIG_MAP[goal];
  if (!config) return '未知模式';

  const currentVariant = normalizeVariantForGoal(goal, variant);
  if (!config.variants) return config.label;

  const variantLabel = config.variants.find(v => v.value === currentVariant)?.label;
  return variantLabel ? `${config.label} (${variantLabel})` : config.label;
};

/**
 * 获取 API 参数
 */
export const getApiParams = (goal: ReadingGoal, variant?: string | null) => {
  const config = READING_CONFIG_MAP[goal];
  return {
    reading_goal: config.serverGoal,
    reading_variant: normalizeVariantForGoal(goal, variant)
  };
};

export const normalizeServerAnalyzeParams = (
  readingGoal: string,
  readingVariant?: string | null
) => {
  const goal = SERVER_GOAL_TO_UI_GOAL[readingGoal] || 'daily';
  return getApiParams(goal, readingVariant);
};

export const VARIANT_TO_EXAM_TAGS: Record<string, string[]> = {
  cet: ['cet4', 'cet6'],
  gaokao: ['gaokao'],
  kaoyan: ['kaoyan'],
  tem: ['tem4', 'tem8'],
  ielts_toefl: ['ielts', 'toefl'],
};

export const EXAM_TAG_LABELS: Record<string, string> = {
  cet4: 'CET-4',
  cet6: 'CET-6',
  gaokao: '高考',
  kaoyan: '考研',
  tem4: 'TEM-4',
  tem8: 'TEM-8',
  ielts: 'IELTS',
  toefl: 'TOEFL',
};

export const filterExamTags = (tags: string[], readingVariant?: string | null): string[] => {
  if (!tags.length) return [];
  if (!readingVariant) return tags.map(t => EXAM_TAG_LABELS[t] || t);
  const allowed = VARIANT_TO_EXAM_TAGS[readingVariant];
  if (!allowed) return tags.map(t => EXAM_TAG_LABELS[t] || t);
  return tags.filter(t => allowed.includes(t)).map(t => EXAM_TAG_LABELS[t] || t);
};

/**
 * 从服务器返回的原始字段获取友好的显示文本
 */
export const getSafeDisplayLabel = (serverGoal: string, serverVariant?: string | null) => {
  const normalizedGoalKey = serverGoal.replace(/([A-Z])/g, "_$1").toLowerCase();

  const goal = SERVER_GOAL_TO_UI_GOAL[serverGoal] || SERVER_GOAL_TO_UI_GOAL[normalizedGoalKey] || 'daily';
  return getDisplayLabel(goal, serverVariant);
};

/**
 * 紧凑标签映射表（≤4字符）
 * 用于：ReaderContextBar、History卡片tag、Input模式chip等紧凑空间
 */
const COMPACT_LABEL_MAP: Record<string, string> = {
  cet: '四六级',
  gaokao: '高考',
  kaoyan: '考研',
  tem: '专英',
  ielts_toefl: '雅/托',
  beginner_reading: '入门',
  intermediate_reading: '进阶',
  intensive_reading: '精读',
  academic_general: '学术',
};

/**
 * 标准标签映射表（优化版，无双层括号）
 * 用于：Profile菜单项、Tooltip详细说明等宽松空间
 */
const STANDARD_LABEL_MAP: Record<string, string> = {
  cet: '四六级考试',
  gaokao: '高考英语',
  kaoyan: '考研英语',
  tem: '专业英语(TEM)',
  ielts_toefl: '雅思/托福',
  beginner_reading: '日常 · 入门',
  intermediate_reading: '日常 · 进阶',
  intensive_reading: '日常 · 精读',
  academic_general: '学术文献',
};

/**
 * 获取紧凑标签（≤4字符，绝不换行）
 * 规则：仅显示最具体的子类型简称，省略父类别和括号
 */
export const getCompactLabel = (serverGoal: string, serverVariant?: string | null): string => {
  const normalizedGoalKey = serverGoal.replace(/([A-Z])/g, "_$1").toLowerCase();
  const goal = SERVER_GOAL_TO_UI_GOAL[serverGoal] || SERVER_GOAL_TO_UI_GOAL[normalizedGoalKey] || 'daily';
  const currentVariant = serverVariant || READING_CONFIG_MAP[goal].defaultVariant;

  if (COMPACT_LABEL_MAP[currentVariant]) {
    return COMPACT_LABEL_MAP[currentVariant];
  }

  const GOAL_SHORT_MAP: Record<ReadingGoal, string> = {
    exam: '考试',
    daily: '日常',
    academic: '学术',
  };

  return GOAL_SHORT_MAP[goal] || '阅读';
};

/**
 * 获取标准标签（优化格式，避免双层括号）
 * 规则：使用优化后的完整标签，信息清晰但不冗余
 */
export const getStandardLabel = (serverGoal: string, serverVariant?: string | null): string => {
  const normalizedGoalKey = serverGoal.replace(/([A-Z])/g, "_$1").toLowerCase();
  const goal = SERVER_GOAL_TO_UI_GOAL[serverGoal] || SERVER_GOAL_TO_UI_GOAL[normalizedGoalKey] || 'daily';
  const currentVariant = serverVariant || READING_CONFIG_MAP[goal].defaultVariant;

  if (STANDARD_LABEL_MAP[currentVariant]) {
    return STANDARD_LABEL_MAP[currentVariant];
  }

  return READING_CONFIG_MAP[goal].label;
};
