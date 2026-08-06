/**
 * Ask send-request wire projection (pure).
 *
 * Maps the Reader-owned attachment / page-identity shapes onto the Ask BFF
 * request DTOs and merges send-time context. No React, no IO — the panel's
 * send/retry orchestration is the only caller family.
 */
import {
  askAttachmentKey,
  type ReaderAskAttachment,
  type ReaderAskPageIdentity,
} from "@/lib/reader-plate";
import type {
  ReaderAskAttachmentDto,
  ReaderAskEntryActionDto,
  ReaderAskPageIdentityDto,
  ReaderAskResolvedContextInputDto,
} from "@/types/api/reader-ask";

export function deriveAvailableContextCapabilities(
  pageIdentity: ReaderAskPageIdentity,
): string[] {
  if (Array.isArray(pageIdentity.availableContextCapabilities)) {
    return [...new Set(pageIdentity.availableContextCapabilities.filter((item) => item.trim().length > 0))];
  }

  const capabilities = ["record_context", "dictionary"];
  if (pageIdentity.hasArticleOverview || pageIdentity.hasSentenceEntries) {
    capabilities.push("record_insights");
  }
  if (pageIdentity.hasAnnotations) {
    capabilities.push("reader_annotations");
  }
  if (pageIdentity.hasReaderNotes) {
    capabilities.push("reader_notes");
  }
  return capabilities;
}

export function serializePageIdentity(
  pageIdentity: ReaderAskPageIdentity,
): ReaderAskPageIdentityDto {
  return {
    record_id: pageIdentity.recordId,
    title: pageIdentity.recordTitle ?? null,
    surface: pageIdentity.surface,
    source: pageIdentity.source,
    available_context_capabilities: deriveAvailableContextCapabilities(pageIdentity),
    has_article_overview: pageIdentity.hasArticleOverview ?? false,
    has_sentence_entries: pageIdentity.hasSentenceEntries ?? false,
    has_annotations: pageIdentity.hasAnnotations ?? false,
    has_reader_notes: pageIdentity.hasReaderNotes ?? false,
  };
}

export function serializeAttachment(
  attachment: ReaderAskAttachment,
): ReaderAskAttachmentDto {
  return {
    kind: attachment.kind,
    subtype: attachment.subtype,
    label: attachment.label,
    selected_text: attachment.selectedText ?? null,
    target_key: attachment.targetKey ?? null,
    anchor_payload: attachment.anchorPayload
      ? {
          anchor_type: attachment.anchorPayload.anchorType,
          target_key: attachment.anchorPayload.targetKey,
          record_id: attachment.anchorPayload.recordId,
          paragraph_id: attachment.anchorPayload.paragraphId ?? null,
          sentence_id: attachment.anchorPayload.sentenceId ?? null,
          selected_text: attachment.anchorPayload.selectedText,
          start_offset: attachment.anchorPayload.startOffset ?? null,
          end_offset: attachment.anchorPayload.endOffset ?? null,
          text_hash: attachment.anchorPayload.textHash ?? null,
          segments:
            attachment.anchorPayload.segments?.map((segment) => ({
              paragraph_id: segment.paragraphId ?? null,
              sentence_id: segment.sentenceId,
              selected_text: segment.selectedText ?? "",
              start_offset: segment.startOffset,
              end_offset: segment.endOffset,
              text_hash: segment.textHash ?? "",
            })) ?? [],
        }
      : null,
    metadata: {
      source_surface: attachment.metadata.sourceSurface,
      entry_action: attachment.metadata.entryAction ?? null,
      record_id: attachment.metadata.recordId ?? null,
      record_title: attachment.metadata.recordTitle ?? null,
      sentence_id: attachment.metadata.sentenceId ?? null,
      paragraph_id: attachment.metadata.paragraphId ?? null,
      entry_id: attachment.metadata.entryId ?? null,
      entry_type: attachment.metadata.entryType ?? null,
      asset_id: attachment.metadata.assetId ?? null,
      annotation_type: attachment.metadata.annotationType ?? null,
      start_offset: attachment.metadata.startOffset ?? null,
      end_offset: attachment.metadata.endOffset ?? null,
      translation_zh: attachment.metadata.translationZh ?? null,
      note: attachment.metadata.note ?? null,
      title: attachment.metadata.title ?? null,
      query: attachment.metadata.query ?? null,
      lookup_text: attachment.metadata.lookupText ?? null,
      visual_tone: attachment.metadata.visualTone ?? null,
      reading_record_anchor: attachment.metadata.readingRecordAnchor ?? null,
    },
  };
}

export function defaultEntryAction(): ReaderAskEntryActionDto {
  return "ask_about_this";
}

export function mergeAttachments(
  current: ReaderAskAttachment[],
  incoming: ReaderAskAttachment[],
): ReaderAskAttachment[] {
  const merged = [...current];
  const seen = new Set(current.map((item) => askAttachmentKey(item)));
  for (const item of incoming) {
    const key = askAttachmentKey(item);
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    merged.push(item);
  }
  return merged;
}

export function buildOptimisticResolvedContextInput(
  pageIdentity: ReaderAskPageIdentity,
  entryAction: ReaderAskEntryActionDto,
  attachments: ReaderAskAttachment[],
): ReaderAskResolvedContextInputDto {
  return {
    page_identity: serializePageIdentity(pageIdentity),
    entry_action: entryAction,
    attachments: attachments.map(serializeAttachment),
    normalized_anchors: [],
    current_record_context: null,
    external_record_contexts: [],
    external_asset_contexts: [],
  };
}
