export declare const USER_ANNOTATION_TYPES: readonly ["highlight", "note"];
export type UserAnnotationType = (typeof USER_ANNOTATION_TYPES)[number];

export interface AnchorSegmentLike {
  paragraphId?: string | null;
  sentenceId: string;
  startOffset: number;
  endOffset: number;
  textHash: string;
}

export declare const USER_ANNOTATION_ANCHOR_TYPES: readonly ["sentence", "paragraph", "text_range", "multi_text"];
export type UserAnnotationAnchorType = (typeof USER_ANNOTATION_ANCHOR_TYPES)[number];

export declare const FAVORITE_TARGET_TYPES: readonly [
  "analysis_record",
  "sentence",
  "paragraph",
  "phrase",
  "vocab",
  "text_range",
  "multi_text",
];
export type FavoriteTargetType = (typeof FAVORITE_TARGET_TYPES)[number];

export declare const USER_ANNOTATION_COLORS: readonly [
  "soft_green",
  "soft_blue",
  "soft_purple",
  "warm_yellow",
  "sage_green",
];
export type UserAnnotationColor = (typeof USER_ANNOTATION_COLORS)[number];

export declare const TEXT_RANGE_OFFSET_UNIT: "utf16";
export declare const TEXT_RANGE_HASH_ALGORITHM: "fnv1a32-utf16";
export declare function computeUtf16FNV1a(text: string): string;
export declare function buildSentenceTargetKey(recordId: string, sentenceId: string): string;
export declare function buildTextRangeTargetKey(
  recordId: string,
  sentenceId: string,
  startOffset: number,
  endOffset: number,
  textHash: string,
): string;
export declare function buildMultiTextTargetKey(
  recordId: string,
  segments: readonly AnchorSegmentLike[],
): string;
