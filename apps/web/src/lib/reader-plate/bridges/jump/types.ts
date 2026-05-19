import type { WebAnnotationVm } from "@/types/api/annotations";
import type { WebFavoriteTargetVm } from "@/types/api/favorites";
import type { ReaderAnchorPayload, ReaderAnchorSegment, ReaderTargetRef } from "../assets";

export type ReaderJumpTargetType =
  | "sentence"
  | "text_range"
  | "multi_text"
  | "user_annotation"
  | "favorite"
  | "content_summary";
export type ReaderJumpHighlightMode = "sentence_frame" | "range_segments" | "sentence_group";
export type ReaderJumpScrollStrategy = "center" | "none";

export type ReaderJumpRangeSegment = ReaderAnchorSegment;

export interface ReaderJumpTarget {
  targetType: ReaderJumpTargetType;
  targetKey: string;
  sentenceIds: string[];
  paragraphIds?: string[];
  rangeSegments?: ReaderJumpRangeSegment[];
  primarySentenceId?: string;
  highlightMode: ReaderJumpHighlightMode;
  scrollStrategy: ReaderJumpScrollStrategy;
}

export interface ReaderJumpContext {
  annotations?: WebAnnotationVm[];
  favoriteTargets?: WebFavoriteTargetVm[];
}

export type { ReaderAnchorPayload, ReaderTargetRef };
