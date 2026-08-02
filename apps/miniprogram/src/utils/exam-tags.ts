/**
 * 考试标签中性 helper（从 config/purpose.ts 迁出）
 *
 * CUTOVER-MINI-LONG: 旧 purpose/reading-variant 配置随 analysis 主链下线，
 * 但 WordPopup 仍需按 reading_variant 过滤词条考试标签。本文件只保留
 * 纯展示用的标签映射，不依赖任何旧 purpose/store/API。
 */

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

/**
 * 按阅读 variant 过滤考试标签并映射为展示文本。
 * 未知 variant 时回退为全量标签映射。
 */
export const filterExamTags = (tags: string[], readingVariant?: string | null): string[] => {
  if (!tags.length) return [];
  if (!readingVariant) return tags.map(t => EXAM_TAG_LABELS[t] || t);
  const allowed = VARIANT_TO_EXAM_TAGS[readingVariant];
  if (!allowed) return tags.map(t => EXAM_TAG_LABELS[t] || t);
  return tags.filter(t => allowed.includes(t)).map(t => EXAM_TAG_LABELS[t] || t);
};