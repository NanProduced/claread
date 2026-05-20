import { TEXT_RANGE_HASH_ALGORITHM, TEXT_RANGE_OFFSET_UNIT } from "@claread/contracts";

import type { ReaderAskAnchorRefDto, ReaderAskCitationDto } from "@/types/api/reader-ask";
import type { WebAnnotationVm } from "@/types/api/annotations";
import type { WebFavoriteTargetVm } from "@/types/api/favorites";
import type { SentenceModel } from "@/types/view/ReaderMockVm";
import type { ReaderAnalysisBlockNode, ReaderContentSummaryNode } from "../../model";
import type { ReaderTextSelection } from "../../primitives";
import type { ReaderStructuredInspectIntent } from "../dictionary";
import {
  anchorPayloadFromAnnotation,
  anchorPayloadFromFavorite,
  anchorPayloadFromSelection,
  anchorPayloadFromSentence,
  annotationToTargetRef,
  favoriteToTargetRef,
  sentenceToTargetRef,
  selectionToTargetRef,
  type ReaderAnchorPayload,
} from "../jump";
import { jumpToAnchorPayload, jumpToTargetKey, jumpToTargetRef, type ReaderJumpContext, type ReaderJumpTarget } from "../jump";
import type {
  ReaderAskAttachment,
  ReaderAskAttachmentFactoryOptions,
  ReaderAskCitationView,
  ReaderAskEntryAction,
  ReaderAskLegacyAnchorView,
  ReaderAskPageIdentity,
} from "./types";

function pageIdentityFromTargetKey(targetKey?: string | null): ReaderAskPageIdentity {
  const recordId = targetKey?.match(/^record:([^:]+):/)?.[1] ?? "";
  return {
    recordId,
    recordTitle: null,
    surface: "reader",
    source: "reader_2_0",
  };
}

function isJumpableTargetKey(targetKey?: string | null) {
  if (!targetKey) {
    return false;
  }

  return (
    /^record:[^:]+:sentence:[^:]+$/.test(targetKey) ||
    /^record:[^:]+:range:[^:]+:\d+:\d+:[^:]+$/.test(targetKey) ||
    /^record:[^:]+:multi_text:\d+:[^:]+$/.test(targetKey) ||
    /^record:[^:]+:analysis:content_summary$/.test(targetKey)
  );
}

function buildAnalysisTargetKey(
  recordId: string,
  subtype: string,
  suffix?: string | null,
): string {
  return suffix
    ? `record:${recordId}:analysis:${subtype}:${suffix}`
    : `record:${recordId}:analysis:${subtype}`;
}

function serializeAnchorPayload(payload?: ReaderAnchorPayload) {
  if (!payload) {
    return null;
  }

  return {
    anchor_type: payload.anchorType,
    target_key: payload.targetKey,
    record_id: payload.recordId,
    paragraph_id: payload.paragraphId ?? null,
    sentence_id: payload.sentenceId ?? null,
    selected_text: payload.selectedText,
    start_offset: payload.startOffset ?? null,
    end_offset: payload.endOffset ?? null,
    text_hash: payload.textHash ?? null,
    segments:
      payload.segments?.map((segment) => ({
        paragraph_id: segment.paragraphId ?? null,
        sentence_id: segment.sentenceId,
        selected_text: segment.selectedText ?? null,
        start_offset: segment.startOffset,
        end_offset: segment.endOffset,
        text_hash: segment.textHash ?? null,
      })) ?? [],
  };
}

function defaultLabelForSelection(selection: ReaderTextSelection): string {
  if (selection.anchorType === "sentence") {
    return "整句";
  }
  if (selection.anchorType === "multi_text") {
    return selection.selectedText;
  }
  return selection.selectedText;
}

function selectedTextForSentence(sentence: SentenceModel): string {
  return sentence.text;
}

function labelFromAnnotation(annotation: WebAnnotationVm): string {
  if (annotation.note?.trim()) {
    return annotation.note.trim();
  }
  if (annotation.selectedText?.trim()) {
    return annotation.selectedText.trim();
  }
  return annotation.anchorType === "text_range" ? "选区批注" : "句子批注";
}

function labelFromFavorite(favorite: WebFavoriteTargetVm): string {
  return favorite.selectedText?.trim() || "收藏锚点";
}

function buildAttachment<T extends ReaderAskAttachment>(
  attachment: T,
): T {
  return {
    ...attachment,
    label: attachment.label.trim() || attachment.selectedText?.trim() || attachment.kind,
  };
}

function payloadFromUnknown(value: unknown): ReaderAnchorPayload | undefined {
  if (!value || typeof value !== "object") {
    return undefined;
  }

  const raw = value as Record<string, unknown>;
  if (
    (raw.anchor_type === "sentence" || raw.anchor_type === "text_range" || raw.anchor_type === "multi_text") &&
    typeof raw.target_key === "string" &&
    typeof raw.record_id === "string"
  ) {
    return {
      anchorType: raw.anchor_type,
      targetKey: raw.target_key,
      recordId: raw.record_id,
      paragraphId: typeof raw.paragraph_id === "string" ? raw.paragraph_id : null,
      sentenceId: typeof raw.sentence_id === "string" ? raw.sentence_id : null,
      selectedText: typeof raw.selected_text === "string" ? raw.selected_text : "",
      startOffset: typeof raw.start_offset === "number" ? raw.start_offset : null,
      endOffset: typeof raw.end_offset === "number" ? raw.end_offset : null,
      textHash: typeof raw.text_hash === "string" ? raw.text_hash : null,
      segments: Array.isArray(raw.segments)
        ? raw.segments
            .filter((segment): segment is Record<string, unknown> => Boolean(segment) && typeof segment === "object")
            .map((segment) => ({
              paragraphId: typeof segment.paragraph_id === "string" ? segment.paragraph_id : null,
              sentenceId: String(segment.sentence_id ?? ""),
              selectedText: typeof segment.selected_text === "string" ? segment.selected_text : null,
              startOffset: Number(segment.start_offset ?? 0),
              endOffset: Number(segment.end_offset ?? 0),
              textHash: typeof segment.text_hash === "string" ? segment.text_hash : null,
            }))
        : undefined,
      metadata: {
        offsetUnit: TEXT_RANGE_OFFSET_UNIT,
        textHashAlgorithm: TEXT_RANGE_HASH_ALGORITHM,
        source: "ask_anchor_dto",
        originType:
          raw.anchor_type === "sentence"
            ? "sentence"
            : raw.anchor_type === "multi_text"
              ? "multi_text"
              : "text_range",
      },
    };
  }

  return undefined;
}

export function askAttachmentKey(attachment: ReaderAskAttachment): string {
  return [
    attachment.kind,
    attachment.subtype,
    attachment.targetKey ?? "",
    attachment.metadata.sentenceId ?? "",
    attachment.selectedText ?? "",
  ].join(":");
}

export function askAttachmentLabel(attachment: ReaderAskAttachment): string {
  return attachment.label.trim() || attachment.selectedText?.trim() || attachment.kind;
}

export function askAttachmentFromSelection(
  pageIdentity: ReaderAskPageIdentity,
  selection: ReaderTextSelection,
  options: ReaderAskAttachmentFactoryOptions = {},
): ReaderAskAttachment {
  const targetRef = selectionToTargetRef(pageIdentity.recordId, selection);
  const anchorPayload = anchorPayloadFromSelection(pageIdentity.recordId, selection);
  return buildAttachment({
    kind: "text_selection",
    subtype: selection.anchorType,
    label: options.label ?? defaultLabelForSelection(selection),
    selectedText: selection.selectedText,
    targetKey: anchorPayload.targetKey,
    targetRef,
    anchorPayload,
    jumpTarget: jumpToTargetRef(targetRef),
    metadata: {
      pageIdentity,
      sourceSurface: options.sourceSurface ?? "selection_toolbar",
      entryAction: options.entryAction ?? "ask_about_this",
      sentenceId: selection.sentence.sentenceId,
      paragraphId: selection.sentence.paragraphId,
    },
  });
}

export function askAttachmentFromSentence(
  pageIdentity: ReaderAskPageIdentity,
  sentence: SentenceModel,
  options: ReaderAskAttachmentFactoryOptions & {
    selectedText?: string;
  } = {},
): ReaderAskAttachment {
  const targetRef = sentenceToTargetRef(pageIdentity.recordId, sentence);
  const anchorPayload = anchorPayloadFromSentence(pageIdentity.recordId, sentence);
  return buildAttachment({
    kind: "analysis_ref",
    subtype: "sentence",
    label: options.label ?? selectedTextForSentence(sentence),
    selectedText: options.selectedText ?? selectedTextForSentence(sentence),
    targetKey: anchorPayload.targetKey,
    targetRef,
    anchorPayload,
    jumpTarget: jumpToTargetRef(targetRef),
    metadata: {
      pageIdentity,
      sourceSurface: options.sourceSurface ?? "context_panel",
      entryAction: options.entryAction ?? "explain_this",
      sentenceId: sentence.sentenceId,
      paragraphId: sentence.paragraphId,
    },
  });
}

export function askAttachmentFromTranslation(
  pageIdentity: ReaderAskPageIdentity,
  sentence: SentenceModel,
  translationZh: string,
  options: ReaderAskAttachmentFactoryOptions = {},
): ReaderAskAttachment {
  const anchorPayload = anchorPayloadFromSentence(pageIdentity.recordId, sentence);
  return buildAttachment({
    kind: "analysis_ref",
    subtype: "translation",
    label: options.label ?? "译文对照",
    selectedText: translationZh,
    targetKey: buildAnalysisTargetKey(pageIdentity.recordId, "translation", sentence.sentenceId),
    targetRef: sentenceToTargetRef(pageIdentity.recordId, sentence),
    anchorPayload,
    jumpTarget: jumpToAnchorPayload(anchorPayload),
    metadata: {
      pageIdentity,
      sourceSurface: options.sourceSurface ?? "translation",
      entryAction: options.entryAction ?? "compare_translation",
      sentenceId: sentence.sentenceId,
      paragraphId: sentence.paragraphId,
      translationZh,
    },
  });
}

export function askAttachmentFromAnalysisBlock(
  pageIdentity: ReaderAskPageIdentity,
  sentence: SentenceModel,
  block: Pick<ReaderAnalysisBlockNode, "entryId" | "entryType" | "label" | "title" | "content" | "sourceKind" | "supplementId">,
  options: ReaderAskAttachmentFactoryOptions = {},
): ReaderAskAttachment {
  const anchorPayload = anchorPayloadFromSentence(pageIdentity.recordId, sentence);
  return buildAttachment({
    kind: block.sourceKind === "ask_supplement" ? "supplement_ref" : "analysis_ref",
    subtype: block.entryType,
    label: options.label ?? block.title ?? block.label,
    selectedText: block.content,
    targetKey: buildAnalysisTargetKey(pageIdentity.recordId, block.entryType, block.entryId),
    targetRef: sentenceToTargetRef(pageIdentity.recordId, sentence),
    anchorPayload,
    jumpTarget: jumpToAnchorPayload(anchorPayload),
    metadata: {
      pageIdentity,
      sourceSurface: options.sourceSurface ?? "analysis_block",
      entryAction: options.entryAction ?? "explain_this",
      sentenceId: sentence.sentenceId,
      paragraphId: sentence.paragraphId,
      entryId: block.supplementId ?? block.entryId,
      entryType: block.entryType,
      title: block.title ?? block.label,
    },
  });
}

export function askAttachmentFromContentSummary(
  pageIdentity: ReaderAskPageIdentity,
  summary: Pick<ReaderContentSummaryNode, "overview">,
  options: ReaderAskAttachmentFactoryOptions = {},
): ReaderAskAttachment {
  return buildAttachment({
    kind: "analysis_ref",
    subtype: "reader_content_summary",
    label: options.label ?? "内容概要",
    selectedText: summary.overview,
    targetKey: buildAnalysisTargetKey(pageIdentity.recordId, "content_summary"),
    jumpTarget: {
      targetType: "content_summary",
      targetKey: buildAnalysisTargetKey(pageIdentity.recordId, "content_summary"),
      sentenceIds: [],
      highlightMode: "sentence_group",
      scrollStrategy: "center",
    },
    metadata: {
      pageIdentity,
      sourceSurface: options.sourceSurface ?? "content_summary",
      entryAction: options.entryAction ?? "explain_this",
      entryType: "content_summary",
      title: "Academic Summary",
    },
  });
}

export function askAttachmentFromAnnotation(
  pageIdentity: ReaderAskPageIdentity,
  annotation: WebAnnotationVm,
  options: ReaderAskAttachmentFactoryOptions = {},
): ReaderAskAttachment {
  const anchorPayload = anchorPayloadFromAnnotation(annotation);
  const targetRef = annotationToTargetRef(annotation);
  return buildAttachment({
    kind: "annotation_ref",
    subtype: "user_annotation",
    label: options.label ?? labelFromAnnotation(annotation),
    selectedText: annotation.selectedText ?? undefined,
    targetKey: annotation.targetKey,
    targetRef,
    anchorPayload,
    jumpTarget: jumpToTargetRef(targetRef),
    metadata: {
      pageIdentity,
      sourceSurface: options.sourceSurface ?? "annotation",
      entryAction: options.entryAction ?? "ask_about_this",
      sentenceId: annotation.sentenceId ?? null,
      paragraphId: annotation.paragraphId ?? null,
      assetId: annotation.id,
      annotationType: annotation.type,
      note: annotation.note ?? null,
    },
  });
}

export function askAttachmentFromFavorite(
  pageIdentity: ReaderAskPageIdentity,
  favorite: WebFavoriteTargetVm,
  options: ReaderAskAttachmentFactoryOptions = {},
): ReaderAskAttachment {
  const anchorPayload = anchorPayloadFromFavorite(favorite);
  const targetRef = favoriteToTargetRef(favorite);
  return buildAttachment({
    kind: "annotation_ref",
    subtype: "favorite",
    label: options.label ?? labelFromFavorite(favorite),
    selectedText: favorite.selectedText ?? undefined,
    targetKey: favorite.targetKey,
    targetRef,
    anchorPayload,
    jumpTarget: jumpToTargetRef(targetRef),
    metadata: {
      pageIdentity,
      sourceSurface: options.sourceSurface ?? "favorite",
      entryAction: options.entryAction ?? "ask_about_this",
      sentenceId: favorite.sentenceId ?? null,
      assetId: favorite.id,
    },
  });
}

export function askAttachmentFromRecord(
  pageIdentity: ReaderAskPageIdentity,
  options: ReaderAskAttachmentFactoryOptions = {},
): ReaderAskAttachment {
  return buildAttachment({
    kind: "record_ref",
    subtype: "current_record",
    label: options.label ?? pageIdentity.recordTitle ?? "整篇文章",
    selectedText: pageIdentity.recordTitle ?? "当前文章",
    targetKey: `record:${pageIdentity.recordId}:record`,
    jumpTarget: null,
    metadata: {
      pageIdentity,
      sourceSurface: options.sourceSurface ?? "ask_panel",
      entryAction: options.entryAction ?? "ask_about_this",
    },
  });
}

export function askAttachmentFromStructuredInspect(
  pageIdentity: ReaderAskPageIdentity,
  intent: ReaderStructuredInspectIntent,
  sentence: SentenceModel,
  options: ReaderAskAttachmentFactoryOptions = {},
): ReaderAskAttachment {
  const anchorPayload = anchorPayloadFromSentence(pageIdentity.recordId, sentence);
  return buildAttachment({
    kind: "analysis_ref",
    subtype: "sentence",
    label: options.label ?? `结构化解释：${intent.anchorText}`,
    selectedText: intent.anchorText,
    targetKey: buildAnalysisTargetKey(pageIdentity.recordId, "structured_inspect", intent.markId),
    targetRef: sentenceToTargetRef(pageIdentity.recordId, sentence),
    anchorPayload,
    jumpTarget: jumpToAnchorPayload(anchorPayload),
    metadata: {
      pageIdentity,
      sourceSurface: options.sourceSurface ?? "dictionary_inspect",
      entryAction: options.entryAction ?? "lookup_in_context",
      sentenceId: sentence.sentenceId,
      paragraphId: sentence.paragraphId,
      entryType: "structured_inspect",
      markId: intent.markId,
      annotationType: intent.annotationType,
      visualTone: intent.visualTone,
      query: intent.lookupText ?? intent.anchorText,
      lookupText: intent.lookupText ?? intent.anchorText,
      title: intent.title,
      startOffset: intent.anchorOffsets?.startOffset ?? null,
      endOffset: intent.anchorOffsets?.endOffset ?? null,
    },
  });
}

function anchorDtoFromPayload(
  anchorType: ReaderAskAnchorRefDto["anchor_type"],
  payload: ReaderAnchorPayload | undefined,
  attachment: ReaderAskAttachment,
  overrides: Partial<ReaderAskAnchorRefDto> = {},
): ReaderAskAnchorRefDto {
  return {
    anchor_type: anchorType,
    target_key: attachment.targetKey ?? payload?.targetKey ?? null,
    sentence_id: payload?.sentenceId ?? null,
    paragraph_id: payload?.paragraphId ?? null,
    selected_text: attachment.selectedText ?? payload?.selectedText ?? null,
    start_offset: anchorType === "text_range" ? payload?.startOffset ?? null : null,
    end_offset: anchorType === "text_range" ? payload?.endOffset ?? null : null,
    text_hash: anchorType === "text_range" ? payload?.textHash ?? null : null,
    label: attachment.label,
    segments:
      anchorType === "multi_text"
        ? (payload?.segments ?? []).map((segment) => ({
            paragraph_id: segment.paragraphId ?? null,
            sentence_id: segment.sentenceId,
            selected_text: segment.selectedText ?? "",
            start_offset: segment.startOffset,
            end_offset: segment.endOffset,
            text_hash: segment.textHash ?? "",
          }))
        : [],
    payload_json: {
      attachment_kind: attachment.kind,
      attachment_subtype: attachment.subtype,
      entry_action: attachment.metadata.entryAction ?? null,
      page_identity: attachment.metadata.pageIdentity,
      source_surface: attachment.metadata.sourceSurface,
      attachment_metadata: attachment.metadata,
      anchor_payload: serializeAnchorPayload(payload),
    },
    ...overrides,
  };
}

export function askAnchorsFromAttachments(attachments: ReaderAskAttachment[]): ReaderAskAnchorRefDto[] {
  return attachments.map((attachment) => {
    if (attachment.kind === "text_selection") {
      const payload = attachment.anchorPayload;
      if (attachment.subtype === "multi_text") {
        return anchorDtoFromPayload("multi_text", payload, attachment);
      }
      if (attachment.subtype === "text_range") {
        return anchorDtoFromPayload("text_range", payload, attachment);
      }
      return anchorDtoFromPayload("sentence", payload, attachment);
    }

    if (attachment.kind === "annotation_ref") {
      const payload = attachment.anchorPayload;
      return anchorDtoFromPayload(
        attachment.subtype === "favorite" ? "favorite" : "user_annotation",
        payload,
        attachment,
        {
          anchor_id: attachment.metadata.assetId ?? null,
          note: attachment.metadata.note ?? null,
        },
      );
    }

    if (attachment.kind === "analysis_ref") {
      if (attachment.metadata.entryType === "structured_inspect") {
        return anchorDtoFromPayload("sentence_entry", attachment.anchorPayload, attachment, {
          entry_type: "structured_inspect",
          query: attachment.metadata.lookupText ?? attachment.metadata.query ?? null,
        });
      }

      if (attachment.subtype === "sentence") {
        return anchorDtoFromPayload("sentence", attachment.anchorPayload, attachment);
      }

      return anchorDtoFromPayload("sentence_entry", attachment.anchorPayload, attachment, {
        entry_type: String(attachment.subtype),
      });
    }

    return anchorDtoFromPayload("sentence_entry", attachment.anchorPayload, attachment, {
      entry_type: String(attachment.subtype),
    });
  });
}

function payloadToAnchorPayload(anchor: ReaderAskAnchorRefDto): ReaderAnchorPayload | undefined {
  if (anchor.anchor_type === "text_range" && anchor.sentence_id && anchor.selected_text && anchor.text_hash) {
    return {
      anchorType: "text_range",
      targetKey: anchor.target_key ?? "",
      recordId: pageIdentityFromTargetKey(anchor.target_key).recordId,
      paragraphId: anchor.paragraph_id ?? null,
      sentenceId: anchor.sentence_id,
      selectedText: anchor.selected_text,
      startOffset: anchor.start_offset ?? null,
      endOffset: anchor.end_offset ?? null,
      textHash: anchor.text_hash,
      metadata: {
        offsetUnit: TEXT_RANGE_OFFSET_UNIT,
        textHashAlgorithm: TEXT_RANGE_HASH_ALGORITHM,
        source: "ask_anchor_dto",
        originType: "text_range",
      },
    };
  }

  if (anchor.anchor_type === "multi_text" && anchor.segments.length >= 2) {
    const primary = anchor.segments[0];
    return {
      anchorType: "multi_text",
      targetKey: anchor.target_key ?? "",
      recordId: pageIdentityFromTargetKey(anchor.target_key).recordId,
      paragraphId: anchor.paragraph_id ?? primary?.paragraph_id ?? null,
      sentenceId: anchor.sentence_id ?? primary?.sentence_id ?? null,
      selectedText: anchor.selected_text ?? "",
      segments: anchor.segments.map((segment) => ({
        paragraphId: segment.paragraph_id ?? null,
        sentenceId: segment.sentence_id,
        selectedText: segment.selected_text,
        startOffset: segment.start_offset,
        endOffset: segment.end_offset,
        textHash: segment.text_hash,
      })),
      metadata: {
        offsetUnit: TEXT_RANGE_OFFSET_UNIT,
        textHashAlgorithm: TEXT_RANGE_HASH_ALGORITHM,
        source: "ask_anchor_dto",
        originType: "multi_text",
      },
    };
  }

  if (anchor.anchor_type === "sentence" && anchor.sentence_id) {
    return {
      anchorType: "sentence",
      targetKey: anchor.target_key ?? "",
      recordId: pageIdentityFromTargetKey(anchor.target_key).recordId,
      paragraphId: anchor.paragraph_id ?? null,
      sentenceId: anchor.sentence_id,
      selectedText: anchor.selected_text ?? "",
      metadata: {
        offsetUnit: TEXT_RANGE_OFFSET_UNIT,
        textHashAlgorithm: TEXT_RANGE_HASH_ALGORITHM,
        source: "ask_anchor_dto",
        originType: "sentence",
      },
    };
  }

  const rawPayload = anchor.payload_json?.anchor_payload;
  return payloadFromUnknown(rawPayload);
}

export function askAttachmentFromAnchor(
  anchor: ReaderAskAnchorRefDto,
  fallbackPageIdentity?: ReaderAskPageIdentity,
): ReaderAskLegacyAnchorView {
  const payloadJson = anchor.payload_json ?? {};
  const pageIdentity =
    (payloadJson.page_identity as ReaderAskPageIdentity | undefined) ??
    fallbackPageIdentity ??
    pageIdentityFromTargetKey(anchor.target_key);
  const kind =
    (payloadJson.attachment_kind as ReaderAskAttachment["kind"] | undefined) ??
    (anchor.anchor_type === "user_annotation" || anchor.anchor_type === "favorite"
      ? "annotation_ref"
      : anchor.anchor_type === "sentence" || anchor.anchor_type === "text_range" || anchor.anchor_type === "multi_text"
        ? "text_selection"
        : "analysis_ref");
  const subtype =
    (payloadJson.attachment_subtype as ReaderAskAttachment["subtype"] | undefined) ??
    (anchor.anchor_type === "favorite"
      ? "favorite"
      : anchor.anchor_type === "user_annotation"
        ? "user_annotation"
        : anchor.anchor_type === "sentence_entry"
          ? (anchor.entry_type as ReaderAskAttachment["subtype"]) ?? "sentence"
          : (anchor.anchor_type as ReaderAskAttachment["subtype"]));
  const anchorPayload = payloadToAnchorPayload(anchor);

  return {
    attachment: buildAttachment({
      kind,
      subtype,
      label: anchor.label ?? anchor.selected_text ?? anchor.query ?? anchor.entry_type ?? anchor.anchor_type,
      selectedText: anchor.selected_text ?? undefined,
      targetKey: anchor.target_key ?? undefined,
      anchorPayload,
      jumpTarget: null,
      metadata: {
        pageIdentity,
        sourceSurface:
          (payloadJson.source_surface as string | undefined) ?? "ask_history",
        entryAction:
          (payloadJson.entry_action as ReaderAskEntryAction | undefined) ?? undefined,
        recordId: typeof (payloadJson.attachment_metadata as Record<string, unknown> | undefined)?.recordId === "string"
          ? ((payloadJson.attachment_metadata as Record<string, unknown>).recordId as string)
          : null,
        recordTitle: typeof (payloadJson.attachment_metadata as Record<string, unknown> | undefined)?.recordTitle === "string"
          ? ((payloadJson.attachment_metadata as Record<string, unknown>).recordTitle as string)
          : null,
        sentenceId: anchor.sentence_id ?? null,
        paragraphId: anchor.paragraph_id ?? null,
        entryType: anchor.entry_type ?? null,
        query: anchor.query ?? null,
      },
    }),
    sourceAnchor: {
      anchorType: anchor.anchor_type,
      targetKey: anchor.target_key,
      sentenceId: anchor.sentence_id,
    },
  };
}

export function jumpTargetFromAskAttachment(
  attachment: ReaderAskAttachment,
  context?: ReaderJumpContext,
): ReaderJumpTarget | null {
  if (attachment.targetRef) {
    const fromTargetRef = jumpToTargetRef(attachment.targetRef, context);
    if (fromTargetRef) {
      return fromTargetRef;
    }
  }

  if (attachment.jumpTarget) {
    return attachment.jumpTarget;
  }

  if (attachment.kind === "record_ref") {
    return null;
  }

  if (attachment.subtype === "reader_content_summary" && attachment.targetKey) {
    return {
      targetType: "content_summary",
      targetKey: attachment.targetKey,
      sentenceIds: [],
      highlightMode: "sentence_group",
      scrollStrategy: "center",
    };
  }

  if (attachment.anchorPayload) {
    const fromPayload = jumpToAnchorPayload(attachment.anchorPayload);
    if (fromPayload) {
      return fromPayload;
    }
  }

  if (attachment.targetKey) {
    return jumpToTargetKey(attachment.targetKey, context) ?? null;
  }

  return null;
}

export function askCitationViewFromDto(citation: ReaderAskCitationDto): ReaderAskCitationView {
  return {
    citation,
    label: citation.label,
    targetKey: citation.target_key ?? null,
    sentenceId: citation.sentence_id ?? null,
  };
}

export function citationCanJump(
  citation: ReaderAskCitationDto,
  currentRecordId: string,
) {
  if (isJumpableTargetKey(citation.target_key)) {
    return true;
  }

  if (payloadFromUnknown(citation.metadata_json?.["anchor_payload"])) {
    return true;
  }

  return citation.record_id === currentRecordId && typeof citation.sentence_id === "string";
}

export function jumpTargetFromAskCitation(
  citation: ReaderAskCitationDto,
  currentRecordId: string,
  context?: ReaderJumpContext,
): ReaderJumpTarget | null {
  if (citation.target_key) {
    if (/^record:[^:]+:analysis:content_summary$/.test(citation.target_key)) {
      return {
        targetType: "content_summary",
        targetKey: citation.target_key,
        sentenceIds: [],
        highlightMode: "sentence_group",
        scrollStrategy: "center",
      };
    }

    const nextJumpTarget = jumpToTargetKey(citation.target_key, context);
    if (nextJumpTarget) {
      return nextJumpTarget;
    }
  }

  const anchorPayload = payloadFromUnknown(citation.metadata_json?.["anchor_payload"]);
  if (anchorPayload) {
    const nextJumpTarget = jumpToAnchorPayload(anchorPayload);
    if (nextJumpTarget) {
      return nextJumpTarget;
    }
  }

  if (citation.record_id === currentRecordId && citation.sentence_id) {
    return {
      targetType: "sentence",
      targetKey: citation.target_key ?? `record:${currentRecordId}:sentence:${citation.sentence_id}`,
      sentenceIds: [citation.sentence_id],
      primarySentenceId: citation.sentence_id,
      highlightMode: "sentence_frame",
      scrollStrategy: "center",
    };
  }

  return null;
}
