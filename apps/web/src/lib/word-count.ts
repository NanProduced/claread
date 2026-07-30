/**
 * 近似英文词数统计（"约 xxx 词"）。
 *
 * 优先走 `Intl.Segmenter("en", { granularity: "word" })` + `isWordLike`：
 * ICU 词边界规则天然处理 apostrophe（don't）、英文缩写（e.g. / U.S.A.）
 * 与连字符（state-of-the-art 按词干拆分）。
 *
 * 无 Intl.Segmenter 的环境（老浏览器 / 测试 jsdom）退回正则：匹配由字母
 * 数字组成、内部允许 apostrophe / 连字符 / 缩写点的词序列。fallback 把
 * 连字符词算作一个词，与 Segmenter 略有出入，但两者都是"约数"，不构成
 * 用户决策依据。
 *
 * 性能合同：调用方必须传入已经 debounce 的文本（编辑器 R2 分层状态流
 * 产物），禁止在逐键路径上调用。
 */

interface WordSegment {
  segment: string;
  isWordLike?: boolean;
}

interface WordSegmenter {
  segment(input: string): Iterable<WordSegment>;
}

interface WordSegmenterConstructor {
  new (locale: string, options: { granularity: "word" }): WordSegmenter;
}

let cachedSegmenter: WordSegmenter | null | undefined;

function resolveSegmenter(): WordSegmenter | null {
  if (cachedSegmenter !== undefined) {
    return cachedSegmenter;
  }
  const ctor = (Intl as typeof Intl & { Segmenter?: WordSegmenterConstructor })
    .Segmenter;
  if (!ctor) {
    cachedSegmenter = null;
    return null;
  }
  try {
    cachedSegmenter = new ctor("en", { granularity: "word" });
  } catch {
    cachedSegmenter = null;
  }
  return cachedSegmenter;
}

/**
 * fallback 词模式：字母/数字开头，内部可衔接 apostrophe（' ’）、连字符
 * 或缩写点（e.g. / U.S.A. 中间的点）。结尾句点不匹配。
 */
export const FALLBACK_WORD_PATTERN = /[A-Za-z0-9]+(?:[.'’-][A-Za-z0-9]+)*/g;

export function countEnglishWordsWithFallback(text: string): number {
  if (!text || !text.trim()) {
    return 0;
  }
  return text.match(FALLBACK_WORD_PATTERN)?.length ?? 0;
}

export function countEnglishWords(text: string): number {
  if (!text || !text.trim()) {
    return 0;
  }
  const segmenter = resolveSegmenter();
  if (!segmenter) {
    return countEnglishWordsWithFallback(text);
  }
  let count = 0;
  for (const segment of segmenter.segment(text)) {
    if (segment.isWordLike) {
      count += 1;
    }
  }
  return count;
}

/**
 * 状态栏文案："约 1,234 词"。空文本返回 null（调用方不渲染）。
 */
export function formatApproxWordCount(text: string): string | null {
  const count = countEnglishWords(text);
  if (count === 0) {
    return null;
  }
  return `约 ${count.toLocaleString("zh-CN")} 词`;
}
