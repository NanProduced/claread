import { TEXT_RANGE_HASH_ALGORITHM, TEXT_RANGE_OFFSET_UNIT } from "@claread/contracts";

import type { WebAnnotationVm, WebAnnotationCreateRequest } from "@/types/api/annotations";
import type { SentenceModel } from "@/types/view/ReaderMockVm";
import type { ReaderTextSelection } from "../../primitives";

export type ReaderAnchorType = "sentence" | "text_range" | "multi_text";

export interface ReaderAnchorSegment {
  paragraphId?: string | null;
  sentenceId: string;
  selectedText?: string | null;
  startOffset: number;
  endOffset: number;
  textHash?: string | null;
}

export interface ReaderAnchorPayload {
  anchorType: ReaderAnchorType;
  targetKey: string;
  recordId: string;
  paragraphId?: string | null;
  sentenceId?: string | null;
  selectedText: string;
  startOffset?: number | null;
  endOffset?: number | null;
  textHash?: string | null;
  segments?: ReaderAnchorSegment[];
  metadata: {
    offsetUnit: typeof TEXT_RANGE_OFFSET_UNIT;
    textHashAlgorithm: typeof TEXT_RANGE_HASH_ALGORITHM;
    source: string;
    originType: ReaderTargetRef["kind"];
  };
}

export type ReaderTargetRef =
  | {
      kind: "sentence";
      recordId: string;
      sentence: SentenceModel;
    }
  | {
      kind: "text_range";
      recordId: string;
      selection: Extract<ReaderTextSelection, { anchorType: "text_range" }>;
    }
  | {
      kind: "multi_text";
      recordId: string;
      selection: Extract<ReaderTextSelection, { anchorType: "multi_text" }>;
    }
  | {
      kind: "user_annotation";
      annotation: WebAnnotationVm;
    };

export interface ReaderAssetRange extends ReaderAnchorSegment {
  assetKind: "annotation";
  assetId: string;
  targetKey: string;
  color?: WebAnnotationVm["color"] | null;
  annotationType?: WebAnnotationVm["type"];
}

export interface ReaderSentenceAssetProjection {
  sentenceId: string;
  annotations: WebAnnotationVm[];
  annotationRanges: ReaderAssetRange[];
  hasHighlight: boolean;
  primaryHighlightAnnotation: WebAnnotationVm | null;
}

export interface ReaderSentenceAssetSummary {
  annotations: WebAnnotationVm[];
  hasHighlight: boolean;
}

export interface ReaderAssetProjection {
  sentenceAssetProjectionBySentence: Map<string, ReaderSentenceAssetProjection>;
  annotationRangesBySentence: Map<string, ReaderAssetRange[]>;
  sentenceAssetSummaryBySentence: Map<string, ReaderSentenceAssetSummary>;
}

export interface AnnotationRequestFromAnchorPayloadOptions {
  color?: WebAnnotationVm["color"];
  sentenceTextById?: ReadonlyMap<string, string>;
  translationBySentence?: ReadonlyMap<string, { sentenceId: string; translationZh: string }>;
}

export interface ProjectReaderAssetsInput {
  annotations: WebAnnotationVm[];
  recordId: string;
}

export type AnnotationCreateRequest = WebAnnotationCreateRequest;
