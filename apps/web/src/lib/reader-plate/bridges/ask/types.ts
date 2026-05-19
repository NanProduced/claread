import type { ReaderAskCitationDto } from "@/types/api/reader-ask";
import type { ReaderContentSummaryNode, ReaderAnalysisBlockNode } from "../../model";
import type { ReaderTextSelection } from "../../primitives";
import type { ReaderAnchorPayload, ReaderTargetRef } from "../assets";
import type { ReaderJumpTarget } from "../jump";

export interface ReaderAskPageIdentity {
  recordId: string;
  recordTitle?: string | null;
  surface: "reader";
  source: "reader_2_0";
}

export type ReaderAskAttachmentKind =
  | "text_selection"
  | "annotation_ref"
  | "analysis_ref"
  | "supplement_ref"
  | "record_ref";

export type ReaderAskEntryAction =
  | "ask_about_this"
  | "explain_this"
  | "why_here"
  | "lookup_in_context"
  | "compare_translation";

export type ReaderAskAttachmentSubtype =
  | ReaderTextSelection["anchorType"]
  | "sentence"
  | "translation"
  | ReaderAnalysisBlockNode["entryType"]
  | ReaderContentSummaryNode["type"]
  | "user_annotation"
  | "favorite"
  | "current_record"
  | "supplement_ref";

export interface ReaderAskAttachmentMetadata {
  pageIdentity: ReaderAskPageIdentity;
  sourceSurface: string;
  entryAction?: ReaderAskEntryAction;
  markId?: string | null;
  sentenceId?: string | null;
  paragraphId?: string | null;
  entryId?: string | null;
  entryType?: string | null;
  assetId?: string | null;
  annotationType?: string | null;
  startOffset?: number | null;
  endOffset?: number | null;
  translationZh?: string | null;
  note?: string | null;
  title?: string | null;
  query?: string | null;
  lookupText?: string | null;
  visualTone?: string | null;
}

export interface ReaderAskAttachment {
  kind: ReaderAskAttachmentKind;
  subtype: ReaderAskAttachmentSubtype;
  label: string;
  selectedText?: string;
  targetKey?: string;
  targetRef?: ReaderTargetRef;
  anchorPayload?: ReaderAnchorPayload;
  jumpTarget?: ReaderJumpTarget | null;
  metadata: ReaderAskAttachmentMetadata;
}

export interface ReaderAskAttachmentFactoryOptions {
  sourceSurface?: string;
  entryAction?: ReaderAskEntryAction;
  label?: string;
}

export interface ReaderAskLegacyAnchorView {
  attachment: ReaderAskAttachment;
  sourceAnchor: {
    anchorType: string;
    targetKey?: string | null;
    sentenceId?: string | null;
  };
}

export interface ReaderAskCitationView {
  citation: ReaderAskCitationDto;
  label: string;
  targetKey?: string | null;
  sentenceId?: string | null;
}
