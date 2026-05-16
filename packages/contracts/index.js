export const USER_ANNOTATION_TYPES = ["highlight", "note"];

export const USER_ANNOTATION_ANCHOR_TYPES = ["sentence", "paragraph", "text_range", "multi_text"];

export const FAVORITE_TARGET_TYPES = [
  "analysis_record",
  "sentence",
  "paragraph",
  "phrase",
  "vocab",
  "text_range",
  "multi_text",
];

export const USER_ANNOTATION_COLORS = [
  "soft_green",
  "soft_blue",
  "soft_purple",
  "warm_yellow",
  "sage_green",
];

export const TEXT_RANGE_OFFSET_UNIT = "utf16";

export const TEXT_RANGE_HASH_ALGORITHM = "fnv1a32-utf16";

export function computeUtf16FNV1a(text) {
  let hash = 0x811c9dc5;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

export function buildSentenceTargetKey(recordId, sentenceId) {
  return `record:${recordId}:sentence:${sentenceId}`;
}

export function buildTextRangeTargetKey(recordId, sentenceId, startOffset, endOffset, textHash) {
  return `record:${recordId}:range:${sentenceId}:${startOffset}:${endOffset}:${textHash}`;
}

export function buildMultiTextTargetKey(recordId, segments) {
  const signature = segments
    .map((segment, index) =>
      [
        index,
        segment.paragraphId ?? "",
        segment.sentenceId,
        segment.startOffset,
        segment.endOffset,
        segment.textHash,
      ].join(":"),
    )
    .join("|");
  return `record:${recordId}:multi_text:${segments.length}:${computeUtf16FNV1a(signature)}`;
}
