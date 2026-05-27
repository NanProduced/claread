import type { WebAnnotationVm } from "@/types/api/annotations";
import type { ReaderAnchorPayload, ReaderAnchorSegment, ReaderTargetRef } from "../assets";

export type ReaderJumpTargetType =
  | "sentence"
  | "text_range"
  | "multi_text"
  | "user_annotation"
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
}

export type { ReaderAnchorPayload, ReaderTargetRef };
