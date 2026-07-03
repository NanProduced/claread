"use client";

import {
  BookPlus,
  Copy,
  FileText,
  GitBranch,
  LoaderCircle,
  MessageSquare,
  PencilLine,
  Quote,
  RotateCcw,
  Search,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  Attachment,
  AttachmentInfo,
  AttachmentPreview,
  AttachmentRemove,
  Attachments,
  type AttachmentData,
} from "@/components/ai-elements/attachments";
import {
  Confirmation,
  ConfirmationAccepted,
  ConfirmationActions,
  ConfirmationRejected,
  ConfirmationRequest,
  ConfirmationTitle,
} from "@/components/ai-elements/confirmation";
import {
  Message as AiMessage,
  MessageAction,
  MessageActions,
  MessageContent,
  MessageResponse,
} from "@/components/ai-elements/message";
import {
  Plan,
  PlanContent,
  PlanDescription,
  PlanHeader,
  PlanTitle,
  PlanTrigger,
} from "@/components/ai-elements/plan";
import {
  Tool,
  ToolContent,
  ToolHeader,
  ToolOutput,
} from "@/components/ai-elements/tool";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Loader } from "@/components/ui/loader";
import { SystemMessage } from "@/components/ui/system-message";
import { IconButton } from "@/components/primitives/icon-button";
import { AskComposer } from "@/components/reader/ask-chat/AskComposer";
import { AssistantMessage } from "@/components/reader/ask-chat/AssistantMessage";
import { CitationList } from "@/components/reader/ask-chat/CitationList";
import { ConversationShell } from "@/components/reader/ask-chat/ConversationShell";
import { FollowUpSuggestionChips } from "@/components/reader/ask-chat/FollowUpSuggestionChips";
import { PromptSuggestions } from "@/components/reader/ask-chat/PromptSuggestions";
import { ReasoningPanel } from "@/components/reader/ask-chat/ReasoningPanel";
import { TaskProcessCard } from "@/components/reader/ask-chat/TaskProcessCard";
import { ToolChipRow } from "@/components/reader/ask-chat/ToolChipRow";
import {
  readerCommandControl,
  readerPanelItem,
  readerTransitionStandard,
} from "@/components/reader/interaction";
import { cn } from "@/lib/cn";
import {
  askAttachmentFromDto,
  askAttachmentKey,
  askAttachmentLabel,
  type ReaderAskAttachment,
  type ReaderAskPageIdentity,
} from "@/lib/reader-plate";
import type {
  ReaderAskActionConfirmResponseDto,
  ReaderAskActionProposalDto,
  ReaderAskAttachmentDto,
  ReaderAskAssetDisambiguationCandidateDto,
  ReaderAskAssetDisambiguationDto,
  ReaderAskCompletedPayloadDto,
  ReaderAskContextPlanDto,
  ReaderAskContextRecordItemDto,
  ReaderAskContextRecordSearchResponseDto,
  ReaderAskDeleteSupplementResponseDto,
  ReaderAskDisambiguationDto,
  ReaderAskEntryActionDto,
  ReaderAskEvidenceItemDto,
  ReaderAskMessageDto,
  ReaderAskMessageUiStateDto,
  ReaderAskModelOptionListResponseDto,
  ReaderAskModelOptionSummaryDto,
  ReaderAskMessageStreamRequestDto,
  ReaderAskPageIdentityDto,
  ReaderAskPersistedSupplementDto,
  ReaderAskResolvedContextInputDto,
  ReaderAskResolvedContextSummaryDto,
  ReaderAskResponseCardDto,
  ReaderAskSelectedModelDto,
  ReaderAskSupplementCandidateDto,
  ReaderAskStreamEnvelopeDto,
  ReaderAskTraceSummaryDto,
  ReaderAskThreadDetailDto,
  ReaderAskThreadSummaryDto,
  ReaderAskToolTraceEntryDto,
  ReaderAskFollowUpSuggestionDto,
  ReaderAskUiMessageDto,
} from "@/types/api/reader-ask";
import { consumeReaderAskSse } from "./ask/sse";

type ErrorEnvelope = {
  message?: string;
  detail?: string;
  code?: string;
  payload?: unknown;
};

const IS_DEV = process.env.NODE_ENV !== "production";
const SHOW_ASK_DEBUG_DISCLOSURES = process.env.NEXT_PUBLIC_ASK_CLAREAD_DEBUG === "true";
const COMPOSER_PLACEHOLDER = "继续问这篇文章…";
const workspaceRelatedRecordItemClassName = cn(
  readerPanelItem,
  "w-full justify-between rounded-[12px] px-2.5 py-2 text-left",
);
const workspaceLauncherClassName = cn(
  readerCommandControl,
  "group fixed bottom-[5.25rem] right-4 z-40 h-14 w-14 rounded-full border border-hairline/85",
  "bg-[linear-gradient(180deg,rgba(255,255,255,0.96),rgba(249,247,241,0.98))] text-ink shadow-[0_14px_34px_rgba(17,17,17,0.08)] hover:border-muted hover:bg-reader-paper hover:shadow-[0_18px_38px_rgba(17,17,17,0.1)] active:bg-[linear-gradient(180deg,rgba(246,243,236,0.98),rgba(241,237,227,1))] active:shadow-[0_10px_24px_rgba(17,17,17,0.08)]",
  "dark:bg-[linear-gradient(180deg,rgba(42,47,53,0.96),rgba(30,34,39,0.98))] dark:text-ink dark:shadow-[0_14px_34px_rgba(0,0,0,0.28)] dark:hover:border-muted dark:hover:bg-[#2a2f35] dark:active:bg-[linear-gradient(180deg,rgba(38,43,49,0.98),rgba(28,32,37,1))] dark:active:shadow-[0_10px_24px_rgba(0,0,0,0.22)]",
  "md:bottom-6 md:right-6",
);
type StarterMode = "record" | "sentence" | "selection";

const STARTER_CONTENT: Record<
  StarterMode,
  {
    title: string;
    description: string;
    prompts: [string, string, string, string];
  }
> = {
  record: {
    title: "从这篇文章开始问",
    description: "当前文章默认在场，可以直接问核心观点、结构关系或作者意图。",
    prompts: [
      "概括这篇文章的核心观点。",
      "作者最想说明什么？",
      "这篇文章是怎么展开论证的？",
      "基于这篇文章出一道小练习。",
    ],
  },
  sentence: {
    title: "继续追问这句内容",
    description: "当前句会直接带入这轮提问。",
    prompts: [
      "解释这句在这里的意思。",
      "为什么作者这里这样写？",
      "这句和前面的内容是什么关系？",
      "围绕这一句出一道小练习。",
    ],
  },
  selection: {
    title: "继续围绕这段内容问",
    description: "当前选区会直接带入这轮提问。",
    prompts: [
      "解释这段内容在这里的意思。",
      "作者为什么在这里这样写？",
      "这段和前面的内容是什么关系？",
      "围绕这段内容出一道小练习。",
    ],
  },
};

type ContextRecordSearchState = {
  items: ReaderAskContextRecordItemDto[];
  loading: boolean;
  query: string;
};

type AskPanelBlockKind =
  | "answer"
  | "response_cards"
  | "disambiguation"
  | "external_asset_disambiguation"
  | "action_proposals"
  | "supplement_candidates"
  | "persisted_supplements"
  | "context_summary"
  | "evidence"
  | "trace_summary"
  | "citations"
  | "tool_trace"
  | "follow_up_suggestions";

type AskPanelBlock = {
  kind: AskPanelBlockKind;
};

type AskPanelConversationItem = {
  id: string;
  role: ReaderAskMessageDto["role"];
  status: ReaderAskMessageDto["status"];
  message: ReaderAskUiMessageDto;
  blocks: AskPanelBlock[];
};

type ReaderAskQuickActionRequest = {
  content: string;
  entryAction: ReaderAskEntryActionDto;
  attachments: ReaderAskAttachment[];
  submissionMode?: "chat" | "quick_action";
};

function submissionModeOf(message: Pick<ReaderAskMessageDto, "submission_mode"> | Pick<ReaderAskCompletedPayloadDto, "submission_mode">) {
  return message.submission_mode === "quick_action" ? "quick_action" : "chat";
}

function quickActionLabel(entryAction?: ReaderAskEntryActionDto | null) {
  if (entryAction === "why_here") {
    return "语法解析";
  }
  if (entryAction === "explain_this") {
    return "句子拆分";
  }
  return "快捷分析";
}

function deriveAvailableContextCapabilities(pageIdentity: ReaderAskPageIdentity): string[] {
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

function serializePageIdentity(pageIdentity: ReaderAskPageIdentity): ReaderAskPageIdentityDto {
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

function serializeAttachment(attachment: ReaderAskAttachment): ReaderAskAttachmentDto {
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

function defaultEntryAction(): ReaderAskEntryActionDto {
  return "ask_about_this";
}

function buildRelatedRecordAttachment(
  pageIdentity: ReaderAskPageIdentity,
  item: ReaderAskContextRecordItemDto,
): ReaderAskAttachment {
  return {
    kind: "record_ref",
    subtype: "related_record",
    label: item.title?.trim() || "关联文章",
    targetKey: `record:${item.record_id}:record`,
    metadata: {
      pageIdentity,
      sourceSurface: "ask_context_picker",
      entryAction: "ask_about_this",
      recordId: item.record_id,
      recordTitle: item.title?.trim() || null,
      assetId: item.record_id,
      title: item.title?.trim() || null,
    },
  };
}

function buildExternalAssetAttachment(
  pageIdentity: ReaderAskPageIdentity,
  recordId: string,
  recordTitle: string | null | undefined,
  candidate: ReaderAskAssetDisambiguationCandidateDto,
): ReaderAskAttachment {
  const entryType = (
    candidate.entry_type?.trim() || (candidate.asset_type === "supplement" ? "grammar_note" : "sentence_analysis")
  ) as ReaderAskAttachment["subtype"];
  return {
    kind: candidate.asset_type === "supplement" ? "supplement_ref" : "analysis_ref",
    subtype: entryType,
    label: candidate.title?.trim() || "外部稳定资产",
    selectedText: candidate.summary ?? undefined,
    targetKey: `record:${recordId}:analysis:${entryType}:${candidate.asset_id}`,
    metadata: {
      pageIdentity,
      sourceSurface: "ask_hitp_asset_picker",
      entryAction: "ask_about_this",
      recordId,
      recordTitle: recordTitle?.trim() || null,
      entryId: candidate.asset_id,
      entryType,
      assetId: candidate.asset_id,
      title: candidate.title?.trim() || null,
      note: candidate.summary ?? null,
    },
  };
}

function mergeAttachments(
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

function attachmentsFromResolvedContext(
  message: ReaderAskUiMessageDto | null | undefined,
  fallbackPageIdentity: ReaderAskPageIdentity,
): ReaderAskAttachment[] {
  if (!message?.resolved_context_input?.attachments?.length) {
    return [];
  }
  return message.resolved_context_input.attachments.map((attachment) =>
    askAttachmentFromDto(attachment, fallbackPageIdentity),
  );
}

function buildOptimisticResolvedContextInput(
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

function toThreadSummary(detail: ReaderAskThreadDetailDto): ReaderAskThreadSummaryDto {
  return {
    id: detail.id,
    record_id: detail.record_id,
    title: detail.title,
    is_default: detail.is_default,
    selected_model: detail.selected_model ?? null,
    archived_at: detail.archived_at ?? null,
    created_at: detail.created_at,
    updated_at: detail.updated_at,
    last_message_at: detail.last_message_at,
  };
}

function replaceThreadSummary(
  threads: ReaderAskThreadSummaryDto[],
  nextThread: ReaderAskThreadSummaryDto,
): ReaderAskThreadSummaryDto[] {
  const index = threads.findIndex((thread) => thread.id === nextThread.id);
  if (index < 0) {
    return [nextThread, ...threads];
  }
  return threads.map((thread) => (thread.id === nextThread.id ? nextThread : thread));
}

function isKnownModelOptionKey(
  items: ReaderAskModelOptionSummaryDto[],
  key: string | null | undefined,
): key is string {
  return Boolean(key && items.some((item) => item.key === key));
}

function findModelOptionSummary(
  items: ReaderAskModelOptionSummaryDto[],
  key: string | null | undefined,
): ReaderAskModelOptionSummaryDto | null {
  if (!key) {
    return null;
  }
  return items.find((item) => item.key === key) ?? null;
}

function toSelectedModelSummary(
  option: ReaderAskModelOptionSummaryDto | null | undefined,
): ReaderAskSelectedModelDto | null {
  if (!option) {
    return null;
  }
  return {
    key: option.key,
    label: option.label,
    description: option.description ?? null,
    model_name: option.model_name ?? null,
    replan_model_name: option.replan_model_name ?? null,
    price_multiplier: option.price_multiplier,
  };
}

function formatStreamError(event: ReaderAskStreamEnvelopeDto) {
  const userMessage =
    typeof (event.data as { user_message?: unknown }).user_message === "string"
      ? String((event.data as { user_message?: string }).user_message)
      : null;
  const code =
    typeof (event.data as { code?: unknown }).code === "string"
      ? String((event.data as { code?: string }).code)
      : null;
  const detail =
    typeof (event.data as { detail?: unknown }).detail === "string"
      ? String((event.data as { detail?: string }).detail)
      : "Ask Claread 暂时不可用。";
  if (userMessage) {
    return userMessage;
  }
  return IS_DEV && code ? `${code}: ${detail}` : detail;
}

function parseJsonPayload<T>(rawText: string): T | string | null {
  if (!rawText.trim()) {
    return null;
  }
  try {
    return JSON.parse(rawText) as T;
  } catch {
    return rawText;
  }
}

function extractNestedDetail(value: unknown): string | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  if (typeof (value as { detail?: unknown }).detail === "string") {
    return String((value as { detail: string }).detail);
  }
  if (typeof (value as { message?: unknown }).message === "string") {
    return String((value as { message: string }).message);
  }
  return null;
}

function extractErrorMessage(payload: unknown, fallback: string) {
  if (typeof payload === "string" && payload.trim()) {
    return payload.trim();
  }
  if (payload && typeof payload === "object") {
    const envelope = payload as ErrorEnvelope;
    const directDetail = envelope.detail || envelope.message || extractNestedDetail(envelope.payload);
    const code = envelope.code;
    if (directDetail) {
      return IS_DEV && code ? `${code}: ${directDetail}` : directDetail;
    }
  }
  return fallback;
}

function syncToolTrace(
  entries: ReaderAskToolTraceEntryDto[],
  event: ReaderAskStreamEnvelopeDto,
): ReaderAskToolTraceEntryDto[] {
  if (!event.event.startsWith("tool.")) {
    return entries;
  }
  const toolName = String((event.data as { tool_name?: unknown }).tool_name ?? "");
  if (!toolName) {
    return entries;
  }
  if (event.event === "tool.started") {
    return [
      ...entries,
      {
        tool_name: toolName,
        status: "started",
        started_at: new Date().toISOString(),
        completed_at: null,
        input_summary: null,
        summary: null,
        next_actions: [],
        artifacts: [],
        metadata_json: {},
      },
    ];
  }

  const status: ReaderAskToolTraceEntryDto["status"] =
    event.event === "tool.completed" ? "completed" : "failed";
  let updated = false;
  const next = entries.map((entry) => {
    if (!updated && entry.tool_name === toolName && entry.status === "started") {
      updated = true;
      return {
        ...entry,
        status,
        completed_at: new Date().toISOString(),
        summary:
          typeof (event.data as { summary?: unknown; detail?: unknown }).summary === "string"
            ? String((event.data as { summary?: string }).summary)
            : typeof (event.data as { detail?: unknown }).detail === "string"
              ? String((event.data as { detail?: string }).detail)
              : entry.summary,
      };
    }
    return entry;
  });

  if (updated) {
    return next;
  }

  return [
    ...entries,
    {
      tool_name: toolName,
        status,
        started_at: new Date().toISOString(),
        completed_at: new Date().toISOString(),
        input_summary: null,
        summary:
          typeof (event.data as { summary?: unknown; detail?: unknown }).summary === "string"
            ? String((event.data as { summary?: string }).summary)
            : typeof (event.data as { detail?: unknown }).detail === "string"
              ? String((event.data as { detail?: string }).detail)
              : null,
      next_actions: [],
      artifacts: [],
      metadata_json: {},
    },
  ];
}

type MessageUpdater = ( updater: (messages: ReaderAskUiMessageDto[]) => ReaderAskUiMessageDto[] ) => void;

/**
 * Creates a throttled streaming message updater that batches SSE updates
 * via requestAnimationFrame instead of calling flushSync per chunk.
 * High-frequency events (message.delta, reasoning.delta) are coalesced;
 * low-frequency events (started/completed/interrupted) flush immediately.
 */
function createStreamingCommit(updateMessage: MessageUpdater) {
  let pendingUpdater: Parameters<MessageUpdater>[0] | null = null;
  let rafId: number | null = null;

  function flush() {
    rafId = null;
    if (pendingUpdater !== null) {
      const updater = pendingUpdater;
      pendingUpdater = null;
      updateMessage(updater);
    }
  }

  function scheduleFlush() {
    if (rafId === null) {
      rafId = requestAnimationFrame(flush);
    }
  }

  return function commitStreamingMessageUpdate(
    updater: Parameters<MessageUpdater>[0],
    immediate: boolean = false,
  ) {
    if (typeof window === "undefined") {
      updateMessage(updater);
      return;
    }

    if (immediate) {
      // Cancel any pending batched update and apply immediately
      if (rafId !== null) {
        cancelAnimationFrame(rafId);
        rafId = null;
      }
      if (pendingUpdater !== null) {
        // Apply the pending batch first so we don't lose it
        const prev = pendingUpdater;
        pendingUpdater = null;
        updateMessage(prev);
      }
      updateMessage(updater);
      return;
    }

    // Batch: compose with any pending updater
    if (pendingUpdater === null) {
      pendingUpdater = updater;
    } else {
      const prev = pendingUpdater;
      pendingUpdater = (messages: ReaderAskUiMessageDto[]) =>
        updater(prev(messages));
    }
    scheduleFlush();
  };
}

export function createSseMessageHandler(
  initialMessageId: string,
  updateMessage: MessageUpdater,
  onMessageIdAssigned: ((assignedId: string) => void) | undefined,
  onError: (message: string) => void,
) {
  let currentMessageId = initialMessageId;
  const commitStreamingMessageUpdate = createStreamingCommit(updateMessage);

  return function handleSseEvent(event: ReaderAskStreamEnvelopeDto) {
    if (event.event === "message.started") {
      const messageId = String((event.data as { message_id?: unknown }).message_id ?? currentMessageId);
      currentMessageId = messageId;
      onMessageIdAssigned?.(messageId);
      return;
    }

    if (event.event === "message.delta") {
      const delta = String((event.data as { delta?: unknown }).delta ?? "");
      commitStreamingMessageUpdate((messages) =>
        messages.map((message) =>
          message.id === currentMessageId
            ? {
                ...message,
                content_md: message.regenerate_preview ? delta : `${message.content_md}${delta}`,
                regenerate_preview: false,
                compacting: false,
              }
            : message,
        ),
      );
      return;
    }

    if (event.event === "reasoning.started") {
      commitStreamingMessageUpdate((messages) =>
        messages.map((message) =>
          message.id === currentMessageId
            ? { ...message, reasoning_status: "streaming", reasoning_md: message.reasoning_md ?? "", compacting: false }
            : message,
        ),
        true,
      );
      return;
    }

    if (event.event === "reasoning.delta") {
      const delta = String((event.data as { delta?: unknown }).delta ?? "");
      commitStreamingMessageUpdate((messages) =>
        messages.map((message) =>
          message.id === currentMessageId
            ? {
                ...message,
                reasoning_status: "streaming",
                reasoning_md: `${message.reasoning_md ?? ""}${delta}`,
              }
            : message,
        ),
      );
      return;
    }

    if (event.event === "reasoning.completed") {
      commitStreamingMessageUpdate((messages) =>
        messages.map((message) =>
          message.id === currentMessageId
            ? { ...message, reasoning_status: "completed" }
            : message,
        ),
        true,
      );
      return;
    }

    if (event.event === "tool.started" || event.event === "tool.completed" || event.event === "tool.failed") {
      commitStreamingMessageUpdate((messages) =>
        messages.map((message) =>
          message.id === currentMessageId
            ? { ...message, tool_trace: syncToolTrace(message.tool_trace, event) }
            : message,
        ),
        true,
      );
      return;
    }

    if (event.event === "replan.started") {
      commitStreamingMessageUpdate((messages) =>
        messages.map((message) =>
          message.id === currentMessageId
            ? { ...message, replan_status: "replanning" }
            : message,
        ),
        true,
      );
      return;
    }

    if (event.event === "context.compacting") {
      commitStreamingMessageUpdate((messages) =>
        messages.map((message) =>
          message.id === currentMessageId
            ? { ...message, compacting: true }
            : message,
        ),
        true,
      );
      return;
    }

    if (event.event === "message.completed") {
      const payload = event.data as unknown as ReaderAskCompletedPayloadDto;
      // Update currentMessageId to the server-assigned id
      if (payload.id) {
        currentMessageId = payload.id;
      }
      commitStreamingMessageUpdate((messages) => {
        const assistantIndex = messages.findIndex(
          (candidate) => candidate.id === currentMessageId,
        );
        const priorUserIndex =
          assistantIndex > 0
            ? [...messages.slice(0, assistantIndex)].reverse().findIndex((candidate) => candidate.role === "user")
            : -1;
        const normalizedPriorUserIndex =
          priorUserIndex >= 0 && assistantIndex > 0 ? assistantIndex - 1 - priorUserIndex : -1;
        return messages.map((message, index) => {
          const isStreamingAssistant =
            message.id === currentMessageId;
          if (isStreamingAssistant) {
            // Preserve streamed reasoning content: payload.reasoning_md may be an
            // empty string from the server while the frontend has accumulated deltas.
            // Only fall back to the payload value when the frontend has none.
            const nextReasoningMd = message.reasoning_md || payload.reasoning_md || null;
            // Derive terminal status from the final content: if any source
            // indicates completed/streaming or there is reasoning content, it
            // must be "completed" — never null when reasoning_md is present.
            const nextReasoningStatus =
              payload.reasoning_status === "completed" ||
              message.reasoning_status === "completed" ||
              message.reasoning_status === "streaming" ||
              nextReasoningMd
                ? "completed"
                : null;
            return {
              ...message,
              id: payload.id,
              thread_id: payload.thread_id,
              status: "completed",
              content_md: payload.content_md,
              submission_mode: payload.submission_mode ?? message.submission_mode ?? "chat",
              resolved_intent: payload.resolved_intent ?? null,
              citations: payload.citations,
              action_proposals: payload.action_proposals,
              tool_trace: payload.tool_trace,
              evidence: payload.evidence ?? [],
              trace_summary: payload.trace_summary ?? null,
              disambiguation: payload.disambiguation ?? null,
              external_asset_disambiguation: payload.external_asset_disambiguation ?? null,
              response_cards: payload.response_cards,
              resolved_context: payload.resolved_context,
              context_plan: payload.context_plan ?? null,
              resolved_context_input: payload.resolved_context_input ?? null,
              run_info: payload.run_info ?? null,
              supplement_candidates: payload.supplement_candidates ?? [],
              persisted_supplements: payload.persisted_supplements ?? [],
              reasoning_md: nextReasoningMd,
              reasoning_status: nextReasoningStatus,
              follow_up_suggestions: payload.follow_up_suggestions ?? [],
              replan_status: "idle",
              compacting: false,
              regenerate_preview: false,
              usage_event_id: payload.usage_event_id ?? message.usage_event_id ?? null,
            };
          }
          const isPriorUser =
            payload.resolved_context_input &&
            message.role === "user" &&
            index === normalizedPriorUserIndex;
          if (isPriorUser) {
            return {
              ...message,
              submission_mode: payload.submission_mode ?? message.submission_mode ?? "chat",
              resolved_context_input: payload.resolved_context_input ?? message.resolved_context_input ?? null,
              context_anchors:
                payload.resolved_context_input?.normalized_anchors ?? message.context_anchors,
            };
          }
          return message;
        });
      }, true);
      return;
    }

    if (event.event === "message.interrupted") {
      const payload = event.data as { content_md?: unknown };
      commitStreamingMessageUpdate((messages) =>
        messages.map((message) =>
          message.id === currentMessageId
            ? {
                ...message,
                status: "interrupted",
                content_md: typeof payload.content_md === "string" ? payload.content_md : message.content_md,
                // If reasoning was started (streaming or has content), mark it
                // as completed so it doesn't stay in streaming after interrupt.
                // An empty reasoning_md after reasoning.started means the model
                // started thinking but produced no content — still not streaming.
                reasoning_status:
                  message.reasoning_status === "streaming" || message.reasoning_md
                    ? "completed"
                    : message.reasoning_status,
                compacting: false,
                regenerate_preview: false,
              }
            : message,
        ),
      true);
      return;
    }

    if (event.event === "error") {
      onError(formatStreamError(event));
      commitStreamingMessageUpdate((messages) =>
        messages.map((message) =>
          message.id === currentMessageId
            ? { ...message, status: "failed", compacting: false, replan_status: "idle" }
            : message,
        ),
      true);
    }
  };
}

function toolLabel(toolName: string) {
  switch (toolName) {
    case "get_record_context":
      return "当前文章上下文";
    case "get_record_insights":
      return "解析卡片";
    case "get_user_vocabulary_book":
      return "生词本";
    case "resolve_known_reference":
      return "跨文章引用";
    case "suggest_prompts":
      return "追问建议";
    case "generate_sentence_annotation":
      return "句法生成";
    case "propose_save_note":
      return "保存笔记确认";
    case "propose_save_highlight":
      return "保存高亮确认";
    default:
      return toolName;
  }
}

function toolTraceState(entry: ReaderAskToolTraceEntryDto) {
  if (entry.status === "started") {
    return "input-available" as const;
  }
  if (entry.status === "completed") {
    return "output-available" as const;
  }
  return "output-error" as const;
}

function normalizeToolTraceEntries(entries: ReaderAskToolTraceEntryDto[]): ReaderAskToolTraceEntryDto[] {
  const normalized: ReaderAskToolTraceEntryDto[] = [];

  for (const entry of entries) {
    if (entry.status === "started") {
      normalized.push({ ...entry });
      continue;
    }

    let merged = false;
    for (let index = normalized.length - 1; index >= 0; index -= 1) {
      const candidate = normalized[index];
      if (candidate.tool_name !== entry.tool_name || candidate.status !== "started") {
        continue;
      }

      normalized[index] = {
        ...candidate,
        ...entry,
        started_at: candidate.started_at ?? entry.started_at,
        input_summary: candidate.input_summary ?? entry.input_summary,
      };
      merged = true;
      break;
    }

    if (!merged) {
      normalized.push({ ...entry });
    }
  }

  return normalized;
}

async function fetchJson<T>(url: string, init?: RequestInit, fallback = "请求失败。"): Promise<T> {
  const response = await fetch(url, { cache: "no-store", ...init });
  const rawText = await response.text();
  const payload = parseJsonPayload<T>(rawText);
  if (!response.ok) {
    throw new Error(extractErrorMessage(payload, fallback));
  }
  return payload as T;
}

function sourceDocumentPart(
  sourceId: string,
  title: string,
  mediaType = "text/plain",
): AttachmentData {
  return {
    id: sourceId,
    mediaType,
    sourceId,
    title,
    type: "source-document",
  };
}

function attachmentToAiAttachmentData(
  attachment: ReaderAskAttachment,
  variant: "history" | "composer",
): AttachmentData {
  const preferredText =
    variant === "composer" && attachment.kind === "text_selection"
      ? attachment.selectedText?.trim() || askAttachmentLabel(attachment)
      : askAttachmentLabel(attachment);
  const normalizedTitle = preferredText.replace(/\s+/g, " ").trim();

  return sourceDocumentPart(
    askAttachmentKey(attachment),
    normalizedTitle,
    attachment.kind === "record_ref"
      ? "application/vnd.claread.record"
      : "text/plain",
  );
}

function AttachmentChips({
  attachments,
  removable = false,
  onRemove,
  onJump,
  variant = "history",
}: {
  attachments: ReaderAskAttachment[];
  removable?: boolean;
  onRemove?: (attachmentKey: string) => void;
  onJump?: (attachment: ReaderAskAttachment) => void;
  variant?: "history" | "composer";
}) {
  if (attachments.length === 0) {
    return null;
  }

  return (
    <Attachments
      variant={variant === "composer" ? "inline" : "list"}
      className={cn(variant === "history" ? "w-full gap-2" : "max-w-full gap-1.5")}
    >
      {attachments.map((attachment) => {
        const attachmentKey = askAttachmentKey(attachment);
        const clickable = Boolean(onJump && attachment.kind !== "record_ref");
        return (
          <Attachment
            key={attachmentKey}
            data={attachmentToAiAttachmentData(attachment, variant)}
            onRemove={
              removable
                ? () => {
                    onRemove?.(attachmentKey);
                  }
                : undefined
            }
            className={cn(variant === "history" && "w-full", clickable && "cursor-pointer")}
            onClick={() => {
              if (clickable) {
                onJump?.(attachment);
              }
            }}
            onKeyDown={(event) => {
              if (!clickable) {
                return;
              }
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onJump?.(attachment);
              }
            }}
            role={clickable ? "button" : undefined}
            tabIndex={clickable ? 0 : undefined}
            title={askAttachmentLabel(attachment)}
          >
            <AttachmentPreview />
            <AttachmentInfo className={cn("text-xs", variant === "composer" ? "max-w-[12rem] sm:max-w-[15rem]" : undefined)} />
            {removable ? <AttachmentRemove label={`移除引用：${askAttachmentLabel(attachment)}`} /> : null}
          </Attachment>
        );
      })}
    </Attachments>
  );
}

function LiveSelectionChip({
  attachment,
  onActivate,
  onRemove,
}: {
  attachment: ReaderAskAttachment;
  onActivate?: () => void;
  onRemove?: (attachmentKey: string) => void;
}) {
  const attachmentKey = askAttachmentKey(attachment);
  const preferredText = attachment.selectedText?.trim() || askAttachmentLabel(attachment);
  const displayLabel =
    preferredText.length <= 44
      ? preferredText
      : `${preferredText.slice(0, 43).trimEnd()}…`;

  return (
    <Attachments
      variant="inline"
      className="max-w-full"
      onPointerDown={(event) => {
        event.preventDefault();
      }}
    >
      <Attachment
        data={sourceDocumentPart(attachmentKey, displayLabel)}
        onRemove={() => onRemove?.(attachmentKey)}
        className="max-w-full"
        data-live-context-activator="true"
        onPointerDown={(event) => {
          event.preventDefault();
        }}
        onClick={() => onActivate?.()}
        title={preferredText}
      >
        <AttachmentPreview fallbackIcon={<Quote className="h-3 w-3 text-muted-foreground" />} />
        <AttachmentInfo className="max-w-[15rem] text-xs sm:max-w-[19rem]" />
        <AttachmentRemove label={`移除当前选区：${askAttachmentLabel(attachment)}`} />
      </Attachment>
    </Attachments>
  );
}

function CurrentRecordChip({ recordTitle }: { recordTitle?: string | null }) {
  if (!recordTitle?.trim()) {
    return null;
  }

  return (
    <Attachments variant="inline" className="max-w-full">
      <Attachment
        data={sourceDocumentPart(
          `record:${recordTitle}`,
          recordTitle,
          "application/vnd.claread.record",
        )}
        className="max-w-full"
        title={recordTitle}
      >
        <AttachmentPreview fallbackIcon={<FileText className="h-3.5 w-3.5 text-subtle" />} />
        <AttachmentInfo className="max-w-[12rem] text-xs sm:max-w-[15rem]" />
      </Attachment>
    </Attachments>
  );
}

function RelatedRecordPicker({
  disabled,
  search,
  onSearchChange,
  onAttachRelatedRecord,
}: {
  disabled?: boolean;
  search: ContextRecordSearchState;
  onSearchChange: (value: string) => void;
  onAttachRelatedRecord: (item: ReaderAskContextRecordItemDto) => void;
}) {
  const showingRecent = search.query.trim().length === 0;

  return (
    <Command className="w-[18rem]">
      <CommandInput
        disabled={disabled}
        placeholder="搜索其他文章"
        value={search.query}
        onValueChange={onSearchChange}
      />
      <CommandList>
        <CommandGroup heading={showingRecent ? "最近文章" : "搜索结果"}>
          {search.loading ? (
            <div className="flex items-center gap-2 px-2 py-3 text-xs text-muted-foreground">
              <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
              <span>正在检索文章</span>
            </div>
          ) : null}
          {search.items.map((item) => (
            <CommandItem
              key={item.record_id}
              className={workspaceRelatedRecordItemClassName}
              disabled={disabled}
              value={`${item.title || ""} ${item.record_id}`}
              onSelect={() => onAttachRelatedRecord(item)}
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-[13px] font-medium text-ink">{item.title || "Untitled"}</p>
                <p className="mt-0.5 text-[11px] leading-4 text-muted">
                  {item.updated_at ? "最近查看的文章" : "加入当前讨论"}
                </p>
              </div>
              <BookPlus className="h-3.5 w-3.5 shrink-0 text-subtle" />
            </CommandItem>
          ))}
        </CommandGroup>
        {!search.loading ? (
          <CommandEmpty>
            {showingRecent ? "最近没有可加入的文章。" : "没有找到匹配的文章。"}
          </CommandEmpty>
        ) : null}
      </CommandList>
    </Command>
  );
}

function contextSummaryChips(
  summary?: ReaderAskResolvedContextSummaryDto | null,
  contextInput?: ReaderAskResolvedContextInputDto | null,
) {
  const chips: string[] = [];
  if (summary?.current_sentence_used) {
    chips.push("当前句");
  }
  if (summary?.current_paragraph_used) {
    chips.push("当前段");
  }
  if (summary?.used_record_insights || (contextInput?.current_record_context?.record_insights.length ?? 0) > 0) {
    chips.push("本文解析");
  }
  if (summary?.used_cross_record_context) {
    chips.push("跨文章上下文");
  }
  if (summary?.used_dictionary) {
    chips.push("词典");
  }
  if (contextInput?.current_record_context?.article_overview) {
    chips.push("文章概览");
  }
  if ((contextInput?.external_record_contexts.length ?? 0) > 0) {
    chips.push(`外部文章 ${contextInput?.external_record_contexts.length}`);
  }
  if ((contextInput?.external_asset_contexts.length ?? 0) > 0) {
    chips.push(`外部资产 ${contextInput?.external_asset_contexts.length}`);
  }
  return chips.length > 0 ? chips : ["当前文章"];
}

function overviewStatusLabel(status?: string | null) {
  switch (status) {
    case "ready":
      return "概览可用";
    case "pending":
      return "概览生成中";
    case "stale":
      return "概览待刷新";
    case "failed":
      return "概览生成失败";
    case "unavailable":
      return "不适合生成概览";
    default:
      return null;
  }
}

function overviewSourceLabel(source?: string | null) {
  switch (source) {
    case "learning_overview_hint":
      return "Learning Overview Hint";
    case "academic_render_scene":
      return "Academic Render Scene";
    default:
      return source ?? null;
  }
}

function plannerModeLabel(mode: ReaderAskTraceSummaryDto["planner_mode"]) {
  switch (mode) {
    case "direct_answer":
      return "直接回答";
    case "needs_local_clarification":
      return "需要局部澄清";
    case "partial_answer_with_followup":
      return "先答复再追问";
    case "known_reference_resolved":
      return "已命中历史文章";
    case "known_reference_ambiguous":
      return "历史文章候选冲突";
    case "known_reference_not_found":
      return "未命中历史文章";
    default:
      return mode;
  }
}

function workingSetModeLabel(mode: ReaderAskTraceSummaryDto["working_set_mode"]) {
  switch (mode) {
    case "anchor_local":
      return "围绕当前选区";
    case "article_overview":
      return "围绕文章概览";
    case "explicit_external_record":
      return "围绕显式外部文章";
    case "known_reference":
      return "围绕历史文章引用";
    case "clarification":
      return "等待补充定位";
    default:
      return mode;
  }
}

function supplementCandidateIdFromProposal(proposal: ReaderAskActionProposalDto): string | null {
  if (proposal.action_type !== "create_supplement_grammar_note") {
    return null;
  }
  const candidate = proposal.payload_json.candidate;
  if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) {
    return null;
  }
  const candidateId = (candidate as { candidate_id?: unknown }).candidate_id;
  return typeof candidateId === "string" && candidateId.trim() ? candidateId : null;
}

function pendingSupplementCandidates(message: ReaderAskUiMessageDto | null): ReaderAskSupplementCandidateDto[] {
  if (!message) {
    return [];
  }
  return message.supplement_candidates.filter((candidate) => {
    const proposal = message.action_proposals.find(
      (item) => supplementCandidateIdFromProposal(item) === candidate.candidate_id,
    );
    return !proposal;
  });
}

function messageOperationSummary(message: ReaderAskUiMessageDto) {
  const entryAction = message.resolved_context_input?.entry_action ?? null;
  const firstAttachment =
    message.resolved_context_input?.attachments[0]?.selected_text ??
    (message.context_anchors && message.context_anchors[0]?.selected_text) ??
    "";
  const compactTarget = firstAttachment.replace(/\s+/g, " ").trim();
  return compactTarget
    ? `${quickActionLabel(entryAction)} · ${compactTarget.length > 42 ? `${compactTarget.slice(0, 41).trimEnd()}…` : compactTarget}`
    : quickActionLabel(entryAction);
}

async function copyMessageText(text: string) {
  if (!text.trim() || typeof navigator === "undefined" || !navigator.clipboard?.writeText) {
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // Ignore clipboard failures; the UI action remains best-effort only.
  }
}

function buildAssistantBlocks(message: ReaderAskUiMessageDto): AskPanelBlock[] {
  const blocks: AskPanelBlock[] = [];

  if (submissionModeOf(message) === "quick_action" && message.response_cards.length > 0) {
    blocks.push({ kind: "response_cards" });
  }
  blocks.push({ kind: "answer" });

  if (message.response_cards.length > 0 && !(submissionModeOf(message) === "quick_action")) {
    blocks.push({ kind: "response_cards" });
  }
  if (
    SHOW_ASK_DEBUG_DISCLOSURES &&
    (message.context_plan || message.resolved_context_input || message.evidence.length > 0 || message.trace_summary)
  ) {
    blocks.push({ kind: "context_summary" });
  }
  if (message.disambiguation?.required) {
    blocks.push({ kind: "disambiguation" });
  }
  if (message.external_asset_disambiguation?.required) {
    blocks.push({ kind: "external_asset_disambiguation" });
  }
  if (message.action_proposals.length > 0) {
    blocks.push({ kind: "action_proposals" });
  }
  if ((message.follow_up_suggestions ?? []).length > 0) {
    blocks.push({ kind: "follow_up_suggestions" });
  }
  if (message.citations.length > 0) {
    blocks.push({ kind: "citations" });
  }
  if (message.tool_trace.length > 0 && message.status !== "streaming") {
    blocks.push({ kind: "tool_trace" });
  }
  if (
    pendingSupplementCandidates(message).length > 0 ||
    message.persisted_supplements.some((item) => item.lifecycle_status === "persisted")
  ) {
    blocks.push({ kind: "supplement_candidates" });
  }

  return blocks;
}

function contextPlanSummary(plan: ReaderAskContextPlanDto) {
  return [
    plan.entry_action,
    plan.used_article_overview ? "文章概览" : null,
    plan.used_record_context ? "正文上下文" : null,
    plan.used_dictionary ? "词典" : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

function SupplementCandidateTray({
  candidates,
  persistedSupplements,
  deletingSupplementId,
  notice,
  onDeletePersistedSupplement,
}: {
  candidates: ReaderAskSupplementCandidateDto[];
  persistedSupplements: ReaderAskPersistedSupplementDto[];
  deletingSupplementId: string | null;
  notice: string | null;
  onDeletePersistedSupplement: (supplementId: string) => void;
}) {
  if (candidates.length === 0 && persistedSupplements.length === 0 && !notice) {
    return null;
  }

  if (candidates.length === 0 && persistedSupplements.length === 0 && notice) {
    return (
      <SystemMessage
        fill
        variant="action"
        className="rounded-[18px] border-none bg-muted/45 text-[12px] text-muted-foreground shadow-none"
      >
        {notice}
      </SystemMessage>
    );
  }

  return (
    <Plan
      defaultOpen
      className="rounded-[20px] border border-border/70 bg-[color:var(--reader-entry-surface)] py-4 shadow-none backdrop-blur-sm"
    >
      <PlanHeader className="gap-3 px-4 pb-3">
        <div className="space-y-1">
          <PlanTitle className="text-[0.95rem] text-ink">补充内容</PlanTitle>
          <PlanDescription className="text-[12px] leading-5">
            {candidates.length > 0 ? "可写入当前页" : "已写入当前页"}
          </PlanDescription>
        </div>
        <PlanTrigger aria-label="补充内容" />
      </PlanHeader>
      <PlanContent className="space-y-3 px-4">
        {notice ? (
          <SystemMessage
            fill
            variant="action"
            className="rounded-[18px] border-none bg-muted/45 text-[12px] text-muted-foreground shadow-none"
          >
            {notice}
          </SystemMessage>
        ) : null}
      {candidates.length > 0 ? (
        <div className="space-y-2.5">
          <p className="px-0.5 text-[11px] font-medium uppercase tracking-[0.08em] text-muted-foreground/80">候选补充</p>
          <Attachments variant="list" className="gap-2.5">
            {candidates.map((candidate) => (
              <Attachment
                key={candidate.candidate_id}
                data={sourceDocumentPart(candidate.candidate_id, candidate.title)}
                className="items-start rounded-[16px] border border-border/65 bg-background/72 px-3 py-3 shadow-none hover:bg-background/80"
              >
                <AttachmentPreview
                  className="size-10 rounded-[12px] bg-muted/70"
                  fallbackIcon={<Sparkles className="h-4 w-4 text-muted-foreground" />}
                />
                <div className="min-w-0 flex-1 space-y-1">
                  <AttachmentInfo className="text-[13px] font-medium text-ink" />
                  <p className="text-[12px] leading-6 text-muted-foreground">{candidate.content}</p>
                </div>
              </Attachment>
            ))}
          </Attachments>
        </div>
      ) : null}
      {persistedSupplements.length > 0 ? (
        <div className="space-y-2.5">
          <p className="px-0.5 text-[11px] font-medium uppercase tracking-[0.08em] text-muted-foreground/80">已写入当前页</p>
          <Attachments variant="list" className="gap-2.5">
          {persistedSupplements.map((item) => (
            <Attachment
              key={item.supplement_id}
              data={sourceDocumentPart(item.supplement_id, item.title)}
              className="items-start rounded-[16px] border border-border/65 bg-background/72 px-3 py-3 shadow-none hover:bg-background/80"
            >
              <AttachmentPreview
                className="size-10 rounded-[12px] bg-muted/70"
                fallbackIcon={<FileText className="h-4 w-4 text-muted-foreground" />}
              />
              <div className="flex min-w-0 flex-1 items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <AttachmentInfo className="text-[13px] font-medium text-ink" />
                  <p className="mt-1 text-[12px] leading-6 text-muted-foreground">{item.content}</p>
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    {item.record_title || "当前文章"} · 句子 {item.sentence_id}
                  </p>
                </div>
                <IconButton
                  aria-label="删除补充"
                  className="mt-0.5 shrink-0"
                  disabled={deletingSupplementId === item.supplement_id}
                  onClick={() => onDeletePersistedSupplement(item.supplement_id)}
                  size="sm"
                  variant="quiet"
                >
                  {deletingSupplementId === item.supplement_id ? (
                    <LoaderCircle className="h-3 w-3 animate-spin" />
                  ) : (
                    <X className="h-3 w-3" />
                  )}
                </IconButton>
              </div>
            </Attachment>
          ))}
          </Attachments>
        </div>
      ) : null}
      </PlanContent>
    </Plan>
  );
}

function ContextSummaryDisclosure({
  summary,
  contextInput,
}: {
  summary?: ReaderAskResolvedContextSummaryDto | null;
  contextInput?: ReaderAskResolvedContextInputDto | null;
}) {
  if (!summary && !contextInput) {
    return null;
  }

  const chips = contextSummaryChips(summary, contextInput);
  const currentRecordContext = contextInput?.current_record_context;
  const externalRecordContexts = contextInput?.external_record_contexts ?? [];
  const externalAssetContexts = contextInput?.external_asset_contexts ?? [];

  return (
    <Plan>
      <PlanHeader>
        <div className="space-y-1">
          <PlanTitle>依据与上下文</PlanTitle>
          <PlanDescription>{chips.join(" · ")}</PlanDescription>
        </div>
        <PlanTrigger aria-label="依据与上下文" />
      </PlanHeader>
      <PlanContent className="space-y-4">
        <div className="space-y-2">
          <p className="text-xs font-medium text-muted">当前文章</p>
          <Attachments variant="inline" className="max-w-full">
            {chips
              .filter((chip) => !chip.startsWith("外部文章") && !chip.startsWith("外部资产"))
              .map((chip) => (
                <Attachment
                  key={chip}
                  data={sourceDocumentPart(`context-chip:${chip}`, chip)}
                >
                  <AttachmentPreview fallbackIcon={<GitBranch className="h-3 w-3 text-muted-foreground" />} />
                  <AttachmentInfo className="text-xs" />
                </Attachment>
              ))}
            {currentRecordContext?.record_title ? (
              <Attachment
                data={sourceDocumentPart(
                  `current-record:${currentRecordContext.record_title}`,
                  currentRecordContext.record_title,
                  "application/vnd.claread.record",
                )}
              >
                <AttachmentPreview fallbackIcon={<FileText className="h-3 w-3 text-muted-foreground" />} />
                <AttachmentInfo className="text-xs" />
              </Attachment>
            ) : null}
          </Attachments>
          {currentRecordContext?.article_overview || currentRecordContext?.article_overview_status ? (
            <div className="space-y-2">
              <Attachments variant="inline" className="max-w-full">
                <Attachment
                  data={sourceDocumentPart(
                    "current-overview-status",
                    overviewStatusLabel(currentRecordContext.article_overview_status) || "概览状态未知",
                  )}
                >
                  <AttachmentPreview fallbackIcon={<Sparkles className="h-3 w-3 text-muted-foreground" />} />
                  <AttachmentInfo className="text-xs" />
                </Attachment>
                {currentRecordContext.article_overview_source ? (
                  <Attachment
                    data={sourceDocumentPart(
                      "current-overview-source",
                      overviewSourceLabel(currentRecordContext.article_overview_source) || currentRecordContext.article_overview_source,
                    )}
                  >
                    <AttachmentPreview fallbackIcon={<Quote className="h-3 w-3 text-muted-foreground" />} />
                    <AttachmentInfo className="text-xs" />
                  </Attachment>
                ) : null}
                {currentRecordContext.article_overview_confidence ? (
                  <Attachment
                    data={sourceDocumentPart(
                      "current-overview-confidence",
                      `置信度 ${currentRecordContext.article_overview_confidence}`,
                    )}
                  >
                    <AttachmentPreview fallbackIcon={<Sparkles className="h-3 w-3 text-muted-foreground" />} />
                    <AttachmentInfo className="text-xs" />
                  </Attachment>
                ) : null}
              </Attachments>
              {currentRecordContext.article_overview ? (
                <p className="text-[11px] leading-5 text-muted">{currentRecordContext.article_overview}</p>
              ) : null}
            </div>
          ) : null}
          {currentRecordContext?.record_insights.length ? (
            <p className="text-[11px] leading-5 text-muted">
              已并入 {currentRecordContext.record_insights.length} 条当前文章的稳定解析。
            </p>
          ) : null}
        </div>
        {externalRecordContexts.length > 0 ? (
          <div className="space-y-2">
            <p className="text-xs font-medium text-muted">外部文章</p>
            <Attachments variant="list" className="w-full gap-2">
              {externalRecordContexts.map((item) => (
                <Attachment
                  key={item.record_id}
                  data={sourceDocumentPart(
                    `external-record:${item.record_id}`,
                    item.record_title || item.record_id,
                    "application/vnd.claread.record",
                  )}
                  className="items-start"
                >
                  <AttachmentPreview fallbackIcon={<FileText className="h-4 w-4 text-muted-foreground" />} />
                  <div className="min-w-0 flex-1 space-y-1">
                    <AttachmentInfo className="text-xs font-medium text-ink-soft" />
                    <p className="text-[11px] leading-5 text-muted">
                      {item.article_overview
                        ? "已并入文章概览。"
                        : item.record_insights.length > 0
                          ? "已并入记录级稳定解析资产。"
                          : "已定位到文章，但当前没有可用概览。"}
                    </p>
                    {item.article_overview ? (
                      <p className="line-clamp-3 text-[11px] leading-5 text-muted">{item.article_overview}</p>
                    ) : null}
                    {item.record_insights.length > 0 ? (
                      <Attachments variant="inline" className="max-w-full">
                        {item.record_insights.slice(0, 2).map((insight) => (
                          <Attachment
                            key={insight}
                            data={sourceDocumentPart(`record-insight:${item.record_id}:${insight}`, insight)}
                          >
                            <AttachmentPreview fallbackIcon={<Sparkles className="h-3 w-3 text-muted-foreground" />} />
                            <AttachmentInfo className="text-xs" />
                          </Attachment>
                        ))}
                      </Attachments>
                    ) : null}
                  </div>
                </Attachment>
              ))}
            </Attachments>
          </div>
        ) : null}
        {externalAssetContexts.length > 0 ? (
          <div className="space-y-2">
            <p className="text-xs font-medium text-muted">外部资产</p>
            <Attachments variant="list" className="w-full gap-2">
              {externalAssetContexts.map((item) => (
                <Attachment
                  key={`${item.record_id}:${item.asset_type}:${item.asset_id}`}
                  data={sourceDocumentPart(
                    `external-asset:${item.record_id}:${item.asset_id}`,
                    item.asset_title || item.asset_id,
                  )}
                  className="items-start"
                >
                  <AttachmentPreview fallbackIcon={<Quote className="h-4 w-4 text-muted-foreground" />} />
                  <div className="min-w-0 flex-1 space-y-1">
                    <AttachmentInfo className="text-xs font-medium text-ink-soft" />
                    <p className="text-[11px] text-subtle">
                      {(item.record_title || item.record_id)} · {item.asset_type === "supplement" ? "AI 补充" : "稳定分析"}
                    </p>
                    {item.content_summary ? (
                      <p className="text-[11px] leading-5 text-muted">{item.content_summary}</p>
                    ) : null}
                    {!item.content_summary && item.content_md ? (
                      <p className="line-clamp-3 text-[11px] leading-5 text-muted">{item.content_md}</p>
                    ) : null}
                  </div>
                </Attachment>
              ))}
            </Attachments>
          </div>
        ) : null}
      </PlanContent>
    </Plan>
  );
}

function EvidenceDisclosure({
  evidence,
}: {
  evidence: ReaderAskEvidenceItemDto[];
}) {
  if (evidence.length === 0) {
    return null;
  }

  return (
    <Plan className="rounded-[20px] border border-border/70 bg-[color:var(--reader-entry-surface)] py-4 shadow-none backdrop-blur-sm">
      <PlanHeader className="gap-3 px-4 pb-3">
        <div className="space-y-1">
          <PlanTitle className="text-[0.95rem] text-ink">证据</PlanTitle>
          <PlanDescription className="text-[12px] leading-5">{`${evidence.length} 条显式依据`}</PlanDescription>
        </div>
        <PlanTrigger aria-label="证据" />
      </PlanHeader>
      <PlanContent className="px-4">
        <Attachments variant="list" className="w-full gap-2.5">
          {evidence.map((item, index) => (
            <Attachment
              key={`${item.kind}-${item.record_id ?? "local"}-${item.target_key ?? index}`}
              data={sourceDocumentPart(
                `evidence:${item.kind}:${item.record_id ?? "local"}:${item.target_key ?? index}`,
                item.label,
              )}
              className="items-start rounded-[16px] border border-border/65 bg-background/68 px-3 py-3 shadow-none hover:bg-background/76"
            >
              <AttachmentPreview
                className="size-10 rounded-[12px] bg-muted/70"
                fallbackIcon={<Quote className="h-4 w-4 text-muted-foreground" />}
              />
              <div className="min-w-0 flex-1 space-y-1">
                <AttachmentInfo className="text-[13px] font-medium text-ink-soft" />
                <Attachments variant="inline" className="max-w-full gap-1.5">
                  <Attachment
                    data={sourceDocumentPart(
                      `evidence-scope:${index}`,
                      item.scope === "external_record" ? "外部文章" : "当前文章",
                    )}
                    className="border-border/60 bg-background/84 text-[11px]"
                  >
                    <AttachmentPreview fallbackIcon={<GitBranch className="h-3 w-3 text-muted-foreground" />} />
                    <AttachmentInfo className="text-xs" />
                  </Attachment>
                  <Attachment
                    data={sourceDocumentPart(
                      `evidence-kind:${index}`,
                      item.kind === "attachment"
                        ? "显式带入"
                        : item.kind === "citation"
                          ? "回答引用"
                          : item.kind === "resolved_reference"
                            ? "历史文章命中"
                            : item.kind === "supplement_candidate"
                              ? "补充候选"
                            : item.kind === "clarification"
                              ? "需要澄清"
                              : "候选项",
                    )}
                    className="border-border/60 bg-background/84 text-[11px]"
                  >
                    <AttachmentPreview fallbackIcon={<Sparkles className="h-3 w-3 text-muted-foreground" />} />
                    <AttachmentInfo className="text-xs" />
                  </Attachment>
                </Attachments>
                {item.detail ? <p className="text-[12px] leading-6 text-muted">{item.detail}</p> : null}
                {item.record_title || item.source_article_title ? (
                  <p className="text-[11px] text-subtle">
                    {[item.record_title || item.source_article_title].filter(Boolean).join(" · ")}
                  </p>
                ) : null}
              </div>
            </Attachment>
          ))}
        </Attachments>
      </PlanContent>
    </Plan>
  );
}

function clarificationHint(
  traceSummary?: ReaderAskTraceSummaryDto | null,
  evidence: ReaderAskEvidenceItemDto[] = [],
) {
  if (!traceSummary || traceSummary.planner_mode !== "needs_local_clarification") {
    return null;
  }
  const clarification = evidence.find((item) => item.kind === "clarification");
  if (traceSummary.reference_resolution_status === "ambiguous") {
    return clarification?.detail || "当前引用没有唯一命中，请补充更完整的文章标题。";
  }
  if (traceSummary.reference_resolution_status === "not_found") {
    return clarification?.detail || "当前没有命中可并入的历史文章，请补充更准确的标题。";
  }
  return "当前问题还缺少可定位锚点。先选中一句正文或加入相关解析对象，再继续问。";
}

function formatDisambiguationUpdatedAt(value?: string | null) {
  if (!value) {
    return "最近更新";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "最近更新";
  }
  return `更新于 ${date.toLocaleDateString("zh-CN", {
    month: "numeric",
    day: "numeric",
  })}`;
}

function DisambiguationCards({
  disambiguation,
  onSelectCandidate,
}: {
  disambiguation?: ReaderAskDisambiguationDto | null;
  onSelectCandidate: (candidate: ReaderAskContextRecordItemDto) => void;
}) {
  if (!disambiguation?.required || disambiguation.candidates.length === 0) {
    return null;
  }

  return (
    <Plan defaultOpen>
      <PlanHeader>
        <div className="space-y-1">
          <PlanTitle>候选文章</PlanTitle>
          <PlanDescription>
            {disambiguation.reason || "当前引用命中了多个候选，请明确指定要并入哪篇文章。"}
          </PlanDescription>
        </div>
        <PlanTrigger />
      </PlanHeader>
      <PlanContent>
        <Attachments variant="list" className="w-full gap-2">
          {disambiguation.candidates.map((candidate) => (
            <Attachment
              key={candidate.record_id}
              data={sourceDocumentPart(candidate.record_id, candidate.title || candidate.record_id)}
            >
              <AttachmentPreview />
              <AttachmentInfo
                className="text-xs"
                title={`我的文章 · ${formatDisambiguationUpdatedAt(candidate.updated_at)}`}
              />
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => onSelectCandidate(candidate)}
              >
                加入当前讨论
              </Button>
            </Attachment>
          ))}
        </Attachments>
      </PlanContent>
    </Plan>
  );
}

function AssetDisambiguationCards({
  assetDisambiguation,
  onSelectCandidate,
}: {
  assetDisambiguation?: ReaderAskAssetDisambiguationDto | null;
  onSelectCandidate: (candidate: ReaderAskAssetDisambiguationCandidateDto, assetDisambiguation: ReaderAskAssetDisambiguationDto) => void;
}) {
  if (!assetDisambiguation?.required || assetDisambiguation.candidates.length === 0) {
    return null;
  }

  return (
    <Plan defaultOpen>
      <PlanHeader>
        <div className="space-y-1">
          <PlanTitle>候选资产</PlanTitle>
          <PlanDescription>
            {assetDisambiguation.reason || "当前外部文章里命中了多个稳定资产，请先指定要并入哪一个。"}
          </PlanDescription>
        </div>
        <PlanTrigger />
      </PlanHeader>
      <PlanContent>
        <Attachments variant="list" className="w-full gap-2">
          {assetDisambiguation.candidates.map((candidate) => (
            <Attachment
              key={`${candidate.asset_type}:${candidate.asset_id}`}
              data={sourceDocumentPart(candidate.asset_id, candidate.title || candidate.asset_id)}
            >
              <AttachmentPreview />
              <AttachmentInfo
                className="text-xs"
                title={`${assetDisambiguation.record_title || "我的文章"} · ${
                  candidate.asset_type === "supplement" ? "AI 补充" : "稳定分析"
                }`}
              />
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => onSelectCandidate(candidate, assetDisambiguation)}
              >
                加入当前讨论
              </Button>
            </Attachment>
          ))}
        </Attachments>
      </PlanContent>
    </Plan>
  );
}

function TraceSummaryDisclosure({
  traceSummary,
}: {
  traceSummary?: ReaderAskTraceSummaryDto | null;
}) {
  if (!traceSummary) {
    return null;
  }

  const summary = [
    plannerModeLabel(traceSummary.planner_mode),
    workingSetModeLabel(traceSummary.working_set_mode),
    traceSummary.cross_record_context_used ? "已并入跨文章上下文" : "仅使用当前文章",
    traceSummary.used_external_asset_context ? "并入外部资产" : null,
  ].filter(Boolean).join(" · ");

  return (
    <Plan>
      <PlanHeader>
        <div className="space-y-1">
          <PlanTitle>运行轨迹</PlanTitle>
          <PlanDescription>{summary}</PlanDescription>
        </div>
        <PlanTrigger aria-label="运行轨迹" />
      </PlanHeader>
      <PlanContent className="space-y-3">
        <Attachments variant="inline" className="max-w-full">
          <Attachment
            data={sourceDocumentPart("trace-planner-mode", plannerModeLabel(traceSummary.planner_mode))}
          >
            <AttachmentPreview fallbackIcon={<Sparkles className="h-3 w-3 text-muted-foreground" />} />
            <AttachmentInfo className="text-xs" />
          </Attachment>
          <Attachment
            data={sourceDocumentPart("trace-working-set-mode", workingSetModeLabel(traceSummary.working_set_mode))}
          >
            <AttachmentPreview fallbackIcon={<GitBranch className="h-3 w-3 text-muted-foreground" />} />
            <AttachmentInfo className="text-xs" />
          </Attachment>
          {traceSummary.reference_resolution_status !== "not_needed" ? (
            <Attachment
              data={sourceDocumentPart(
                "trace-reference-resolution",
                `引用解析 · ${traceSummary.reference_resolution_status}`,
              )}
            >
              <AttachmentPreview fallbackIcon={<Quote className="h-3 w-3 text-muted-foreground" />} />
              <AttachmentInfo className="text-xs" />
            </Attachment>
          ) : null}
        </Attachments>
        {traceSummary.notes.length > 0 ? (
          <div className="space-y-1.5 text-xs text-muted">
            {traceSummary.notes.map((note, index) => (
              <p key={index} className="leading-5">
                {note}
              </p>
            ))}
          </div>
        ) : null}
        {traceSummary.tool_steps.length > 0 ? (
          <Attachments variant="inline" className="max-w-full">
            {traceSummary.tool_steps.map((step) => (
              <Attachment
                key={step}
                data={sourceDocumentPart(`trace-tool-step:${step}`, toolLabel(step))}
              >
                <AttachmentPreview fallbackIcon={<Search className="h-3 w-3 text-muted-foreground" />} />
                <AttachmentInfo className="text-xs" />
              </Attachment>
            ))}
          </Attachments>
        ) : null}
      </PlanContent>
    </Plan>
  );
}

function ResponseCards({ cards, onAnnotationFeedback, analysisRecordId }: { cards: ReaderAskResponseCardDto[]; onAnnotationFeedback?: (params: { entryType: string; entryId: string }) => void; analysisRecordId?: string }) {
  if (cards.length === 0) {
    return null;
  }

  return (
    <div className="mt-3 space-y-3">
      {cards.map((card, index) => {
        if (card.card_type === "grammar_note_card") {
          const entryId = `ask-grammar-${index}`;
          const focusHint =
            card.analysis_scope === "focus_span" && card.focus_text.trim() && card.focus_text.trim() !== card.sentence_text.trim()
              ? `聚焦片段 · ${card.focus_text}`
              : "锚定本句";
          return (
            <Plan key={`${card.card_type}-${index}`} defaultOpen>
              <PlanHeader>
                <div className="space-y-1">
                  <PlanTitle>{card.label || "句子解析"}</PlanTitle>
                  <PlanDescription>{focusHint}</PlanDescription>
                </div>
                <PlanTrigger aria-label={card.label || "句子解析"} />
              </PlanHeader>
              <PlanContent className="space-y-3">
                {card.sentence_text ? (
                  <p className="text-xs leading-6 text-muted-foreground">{card.sentence_text}</p>
                ) : null}
                <MessageResponse className="ask-message-response text-sm leading-7">
                  {card.note_zh}
                </MessageResponse>
                <MessageActions>
                  <MessageAction
                    label="标注有帮助"
                    title="标注有帮助"
                    onClick={() => {
                      if (analysisRecordId) {
                        fetch("/api/web/feedback", {
                          method: "POST",
                          headers: { "Content-Type": "application/json" },
                          body: JSON.stringify({
                            feedbackScope: "annotation",
                            sentiment: "positive",
                            feedbackType: "helpful",
                            targetId: entryId,
                            analysisRecordId,
                            annotationType: "grammar_note",
                            clientPlatform: "web",
                            clientSurface: "reader",
                            entryPoint: "ai_workspace_annotation_positive",
                            contextSummary: card.label || "AI 助手生成标注",
                            contextJson: {
                              entry_id: entryId,
                              entry_type: "grammar_note",
                            },
                          }),
                        }).catch(() => {});
                      }
                    }}
                  >
                    <ThumbsUp className="h-3.5 w-3.5" />
                  </MessageAction>
                  <MessageAction
                    label="标注有问题"
                    title="标注有问题"
                    onClick={() =>
                      onAnnotationFeedback?.({
                        entryType: "grammar_note",
                        entryId,
                      })
                    }
                  >
                    <ThumbsDown className="h-3.5 w-3.5" />
                  </MessageAction>
                </MessageActions>
                {card.spans.length > 0 ? (
                  <Attachments variant="inline" className="max-w-full">
                    {card.spans.map((span, spanIndex) => (
                      <Attachment
                        key={`${span.text}-${spanIndex}`}
                        data={sourceDocumentPart(
                          `${card.card_type}:${index}:${spanIndex}`,
                          `${span.role ? `${span.role} · ` : ""}${span.text}`,
                        )}
                      >
                        <AttachmentPreview />
                        <AttachmentInfo className="text-xs" />
                      </Attachment>
                    ))}
                  </Attachments>
                ) : null}
              </PlanContent>
            </Plan>
          );
        }

        if (card.card_type === "sentence_breakdown_card") {
          return (
            <Plan key={`${card.card_type}-${index}`} defaultOpen>
              <PlanHeader>
                <div className="space-y-1">
                  <PlanTitle>拆句卡</PlanTitle>
                  <PlanDescription>{card.sentence_text}</PlanDescription>
                </div>
                <PlanTrigger />
              </PlanHeader>
              <PlanContent className="space-y-3">
                {card.translation_zh ? (
                  <p className="text-xs leading-5 text-muted">{card.translation_zh}</p>
                ) : null}
                {card.main_clause ? (
                  <p className="text-xs font-medium text-ink-soft">
                    主线：
                    <span className="ml-1 text-ink">{card.main_clause}</span>
                  </p>
                ) : null}
                {card.parts.length > 0 ? (
                  <div className="space-y-2">
                    {card.parts.map((part, partIndex) => (
                      <TaskProcessCard
                        key={`${part.label}-${partIndex}`}
                        title={part.label}
                        detail={part.text}
                      >
                        {part.note ? <p className="text-xs text-muted">{part.note}</p> : null}
                      </TaskProcessCard>
                    ))}
                  </div>
                ) : null}
                {card.analysis_zh ? (
                  <p className="text-xs leading-5 text-muted">{card.analysis_zh}</p>
                ) : null}
              </PlanContent>
            </Plan>
          );
        }

        return null;
      })}
    </div>
  );
}

function ToolTraceBlock({ entries }: { entries: ReaderAskToolTraceEntryDto[] }) {
  const normalizedEntries = normalizeToolTraceEntries(entries);

  if (normalizedEntries.length === 0) {
    return null;
  }

  return (
    <div className="space-y-2 w-full">
      {normalizedEntries.map((entry, index) => (
        <Tool
          key={`${entry.tool_name}-${index}`}
          className="mt-0 shadow-none"
          defaultOpen={entry.status !== "completed"}
        >
          <ToolHeader
            type="dynamic-tool"
            toolName={toolLabel(entry.tool_name)}
            state={toolTraceState(entry)}
          />
          <ToolContent>
            <ToolOutput
              output={entry.summary ?? null}
              errorText={entry.status === "failed" ? entry.summary ?? "工具调用失败。" : undefined}
            />
          </ToolContent>
        </Tool>
      ))}
    </div>
  );
}

function ConfirmActionCard({
  proposal,
  busy,
  onConfirm,
  onReject,
}: {
  proposal: ReaderAskActionProposalDto;
  busy: boolean;
  onConfirm: (confirmed: boolean) => void;
  onReject: (confirmed: boolean) => void;
}) {
  if (proposal.status !== "pending") {
    const respondedCopy =
      proposal.status === "confirmed" ? "已确认建议动作" : "已取消建议动作";

    return (
      <div className="flex items-center justify-between gap-3 px-3.5 py-2 rounded-lg border border-border/30 bg-muted/10 text-xs">
        <span className="font-medium text-muted-foreground">{proposal.label}</span>
        <span className="text-muted-foreground/60">{respondedCopy}</span>
      </div>
    );
  }

  return (
    <Confirmation
      className="rounded-lg border border-border/40 bg-muted/20 px-3.5 py-2.5 shadow-none"
      approval={{ id: proposal.id }}
      state="approval-requested"
    >
      <div className="flex flex-col gap-1 w-full">
        <ConfirmationTitle className="text-xs font-semibold text-ink leading-normal">
          {proposal.label}
        </ConfirmationTitle>
        {proposal.description ? (
          <div className="text-[11px] leading-relaxed text-muted-foreground mt-0.5">{proposal.description}</div>
        ) : null}
      </div>
      <ConfirmationRequest>
        <ConfirmationActions className="gap-1.5 mt-2 self-end">
          <Button
            size="xs"
            disabled={busy}
            onClick={() => onReject(false)}
            variant="ghost"
            className="h-6.5 text-[11px] text-muted-foreground hover:text-foreground"
          >
            取消
          </Button>
          <Button
            size="xs"
            disabled={busy}
            onClick={() => onConfirm(true)}
            variant="default"
            className="h-6.5 text-[11px]"
          >
            确认
          </Button>
        </ConfirmationActions>
      </ConfirmationRequest>
      <ConfirmationAccepted>
        <div className="mt-2 text-[11px] text-muted-foreground/80">已确认此建议动作。</div>
      </ConfirmationAccepted>
      <ConfirmationRejected>
        <div className="mt-2 text-[11px] text-muted-foreground/80">已取消此建议动作。</div>
      </ConfirmationRejected>
    </Confirmation>
  );
}

function AssistantStreamingIndicator({
  hasAnswerContent,
  reasoningStatus,
  compacting,
  replanStatus,
}: {
  hasAnswerContent: boolean;
  reasoningStatus: ReaderAskMessageDto["reasoning_status"];
  compacting?: boolean;
  replanStatus?: ReaderAskMessageUiStateDto["replan_status"];
}) {
  const title = compacting
    ? "正在压缩上下文"
    : replanStatus === "replanning"
      ? "正在补充上下文"
      : hasAnswerContent
        ? "正在组织回答"
        : reasoningStatus === "streaming"
          ? "正在思考"
          : "正在整理问题";
  const detail = compacting
    ? "Claread 正在收束这轮上下文，随后继续输出答案。"
    : replanStatus === "replanning"
      ? "已经识别到需要补充上下文，会在补充后重新组织答案。"
      : hasAnswerContent
        ? "正文已经开始输出，剩余内容仍在继续生成。"
        : reasoningStatus === "streaming"
          ? "模型正在流式产出思路，随后继续输出正文。"
          : "正在读取当前文章与附件上下文，准备本轮解释。";

  if (
    !hasAnswerContent &&
    reasoningStatus === "streaming" &&
    !compacting &&
    replanStatus !== "replanning"
  ) {
    return null;
  }

  return (
    <TaskProcessCard
      title={title}
      detail={detail}
      className="mb-0.5"
    />
  );
}

function AskPanelLoadingState({
  title,
  detail,
}: {
  title: string;
  detail: string;
}) {
  return (
    <div className="flex h-full items-center justify-center px-5">
      <div className="w-full max-w-[22rem] rounded-lg border bg-card px-4 py-4 shadow-sm">
        <div className="flex items-start gap-3">
          <span className="inline-flex size-9 shrink-0 items-center justify-center rounded-full border bg-background text-lens-blue">
            <Sparkles className="h-4 w-4" />
          </span>
          <div className="min-w-0 flex-1">
            <Loader variant="text-shimmer" size="md" text={title} />
            <p className="mt-1.5 text-[13px] leading-6 text-muted">{detail}</p>
            <div className="mt-2.5">
              <Loader variant="loading-dots" size="sm" text="请稍候" />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function AssistantReasoningBlock({
  reasoningMd,
  reasoningStatus,
}: {
  reasoningMd: string | null | undefined;
  reasoningStatus: ReaderAskMessageDto["reasoning_status"];
}) {
  const hasReasoningContent = Boolean(reasoningMd?.trim());
  const isStreaming = reasoningStatus === "streaming";
  const isCompleted = reasoningStatus === "completed";
  const isActive = isStreaming;
  const shouldRender = isActive || isCompleted || hasReasoningContent;

  if (!shouldRender) {
    return null;
  }

  return (
    <ReasoningPanel
      reasoningMd={reasoningMd}
      reasoningStatus={reasoningStatus}
      className={cn("mb-0.5 transition-all", isActive ? "" : "")}
    />
  );
}

function MessageBubble({
  item,
  currentRecordId,
  pageIdentity,
  pendingActionId,
  deletingSupplementId,
  supplementNotice,
  onConfirmAction,
  onDeletePersistedSupplement,
  onSelectDisambiguationCandidate,
  onSelectAssetDisambiguationCandidate,
  onRetry,
  onJumpToAttachment,
  onAnnotationFeedback,
  analysisRecordId,
  onPickFollowUpSuggestion,
}: {
  item: AskPanelConversationItem;
  currentRecordId: string;
  pageIdentity: ReaderAskPageIdentity;
  pendingActionId: string | null;
  deletingSupplementId: string | null;
  supplementNotice: string | null;
  onConfirmAction: (actionId: string, confirmed: boolean) => void;
  onDeletePersistedSupplement: (supplementId: string) => void;
  onSelectDisambiguationCandidate: (messageId: string, candidate: ReaderAskContextRecordItemDto) => void;
  onSelectAssetDisambiguationCandidate: (
    messageId: string,
    candidate: ReaderAskAssetDisambiguationCandidateDto,
    assetDisambiguation: ReaderAskAssetDisambiguationDto,
  ) => void;
  onRetry: (messageId: string) => void;
  onJumpToAttachment?: (attachment: ReaderAskAttachment) => void;
  onAnnotationFeedback?: (params: { entryType: string; entryId: string }) => void;
  analysisRecordId?: string;
  onPickFollowUpSuggestion?: (prompt: string) => void;
}) {
  const { message, blocks } = item;
  const isAssistant = message.role === "assistant";
  const clarificationText = clarificationHint(message.trace_summary, message.evidence);
  const candidateSupplements = pendingSupplementCandidates(message);
  const persistedSupplements = message.persisted_supplements.filter((entry) => entry.lifecycle_status === "persisted");
  const hasAnswerContent = Boolean(message.content_md?.trim());

  return (
    <div className={cn("flex flex-col gap-3", isAssistant ? "items-start" : "items-end")}>
      {isAssistant ? (
        <div className="min-w-0 w-full space-y-4">
          {blocks.map((block, index) => {
            switch (block.kind) {
              case "answer":
                return (
                  <AssistantMessage
                    key={`${message.id}-${block.kind}-${index}`}
                    className="px-0.5"
                    reasoning={
                      <AssistantReasoningBlock
                        reasoningMd={message.reasoning_md}
                        reasoningStatus={message.reasoning_status}
                      />
                    }
                    process={
                      <>
                        {message.status === "streaming" ? (
                          <AssistantStreamingIndicator
                            hasAnswerContent={hasAnswerContent}
                            reasoningStatus={message.reasoning_status}
                            compacting={message.compacting ?? false}
                            replanStatus={message.replan_status}
                          />
                        ) : null}
                        {message.status === "streaming" && message.tool_trace.length > 0 ? (
                          <ToolTraceBlock entries={message.tool_trace} />
                        ) : null}
                      </>
                    }
                    answer={
                      <div className="space-y-2">
                        {clarificationText ? (
                          <SystemMessage variant="warning">
                            {clarificationText}
                          </SystemMessage>
                        ) : null}
                        {hasAnswerContent ? (
                          <MessageResponse
                            className="ask-message-response border-0 bg-transparent p-0 text-[14.5px] leading-[1.82] text-ink-soft shadow-none [&_blockquote]:my-2 [&_blockquote]:text-[13px] [&_blockquote]:leading-[1.7] [&_blockquote]:text-muted-foreground [&_h2]:mt-6 [&_h2]:text-[1rem] [&_h2]:font-semibold [&_h2]:leading-7 [&_h2]:tracking-[-0.02em] [&_h2]:text-ink [&_h2:first-child]:mt-0 [&_h3]:mt-4 [&_h3]:text-[0.95rem] [&_h3]:font-semibold [&_h3]:leading-6 [&_h3]:text-ink-soft [&_h3:first-child]:mt-0 [&_li]:[&_p+p]:mt-1.5 [&_li]:[&_ul]:mt-2 [&_li]:[&_ol]:mt-2 [&_ol]:my-2.5 [&_ol]:space-y-2.5 [&_ol]:pl-4 [&_ol]:text-[14.5px] [&_ol]:leading-[1.72] [&_ol]:text-ink-soft [&_ol]:marker:font-medium [&_ol]:marker:text-muted [&_p]:my-0 [&_p]:text-[14.5px] [&_p]:leading-[1.82] [&_p]:text-ink-soft [&_p+p]:mt-3 [&_table]:my-3 [&_ul]:my-2.5 [&_ul]:space-y-2.5 [&_ul]:pl-4 [&_ul]:text-[14.5px] [&_ul]:leading-[1.72] [&_ul]:text-ink-soft [&_ul]:marker:text-[0.9em] [&_ul]:marker:text-muted"
                          >
                            {message.content_md}
                          </MessageResponse>
                        ) : null}
                        {message.status === "interrupted" ? (
                          <SystemMessage variant="warning">
                            输出中断，可重新生成。
                          </SystemMessage>
                        ) : null}
                      </div>
                    }
                    footer={
                      message.status === "completed" || message.status === "interrupted" ? (
                        <MessageActions className="gap-0.5">
                          <MessageAction
                            label="复制内容"
                            title="复制内容"
                            onClick={() => {
                              void copyMessageText(message.content_md ?? "");
                            }}
                          >
                            <Copy className="h-3.5 w-3.5" />
                          </MessageAction>
                          <MessageAction
                            label="重新生成"
                            title="重新生成"
                            onClick={() => onRetry(message.id)}
                          >
                            <RotateCcw className="h-3.5 w-3.5" />
                          </MessageAction>
                        </MessageActions>
                      ) : null
                    }
                  />
                );
              case "response_cards":
                return <ResponseCards key={`${message.id}-${block.kind}-${index}`} cards={message.response_cards} onAnnotationFeedback={onAnnotationFeedback} analysisRecordId={analysisRecordId} />;
              case "disambiguation":
                return (
                  <DisambiguationCards
                    key={`${message.id}-${block.kind}-${index}`}
                    disambiguation={message.disambiguation}
                    onSelectCandidate={(candidate) => onSelectDisambiguationCandidate(message.id, candidate)}
                  />
                );
              case "external_asset_disambiguation":
                return (
                  <AssetDisambiguationCards
                    key={`${message.id}-${block.kind}-${index}`}
                    assetDisambiguation={message.external_asset_disambiguation}
                    onSelectCandidate={(candidate, assetDisambiguation) =>
                      onSelectAssetDisambiguationCandidate(message.id, candidate, assetDisambiguation)
                    }
                  />
                );
              case "action_proposals":
                return (
                  <div key={`${message.id}-${block.kind}-${index}`} className="space-y-3">
                    {message.action_proposals.map((proposal) => (
                      <ConfirmActionCard
                        key={proposal.id}
                        proposal={proposal}
                        busy={pendingActionId === proposal.id}
                        onConfirm={(confirmed) => onConfirmAction(proposal.id, confirmed)}
                        onReject={(confirmed) => onConfirmAction(proposal.id, confirmed)}
                      />
                    ))}
                  </div>
                );
              case "supplement_candidates":
              case "persisted_supplements":
                return (
                  <SupplementCandidateTray
                    key={`${message.id}-supplements`}
                    candidates={candidateSupplements}
                    persistedSupplements={persistedSupplements}
                    deletingSupplementId={deletingSupplementId}
                    notice={supplementNotice}
                    onDeletePersistedSupplement={onDeletePersistedSupplement}
                  />
                );
              case "citations":
                return <CitationList key={`${message.id}-${block.kind}-${index}`} citations={message.citations} />;
              case "context_summary":
                return (
                  <div key={`${message.id}-${block.kind}-${index}`} className="space-y-3">
                    <ContextSummaryDisclosure
                      summary={message.resolved_context}
                      contextInput={message.resolved_context_input}
                    />
                    {message.context_plan ? (
                      <Plan>
                        <PlanHeader>
                          <div className="space-y-1">
                            <PlanTitle>上下文策略</PlanTitle>
                            <PlanDescription>{contextPlanSummary(message.context_plan)}</PlanDescription>
                          </div>
                          <PlanTrigger aria-label="上下文策略" />
                        </PlanHeader>
                        <PlanContent className="space-y-3 text-[11px] leading-5 text-muted">
                          <p className="font-semibold text-ink-soft">本轮决策</p>
                          <Attachments variant="inline" className="max-w-full">
                            <Attachment
                              data={sourceDocumentPart("context-plan-entry-action", message.context_plan.entry_action)}
                            >
                              <AttachmentPreview fallbackIcon={<Sparkles className="h-3 w-3 text-muted-foreground" />} />
                              <AttachmentInfo className="text-xs" />
                            </Attachment>
                            {(message.context_plan.source_labels.length > 0
                              ? message.context_plan.source_labels
                              : ["当前文章"]).map((label) => (
                              <Attachment
                                key={label}
                                data={sourceDocumentPart(`context-plan-source:${label}`, label)}
                              >
                                <AttachmentPreview fallbackIcon={<GitBranch className="h-3 w-3 text-muted-foreground" />} />
                                <AttachmentInfo className="text-xs" />
                              </Attachment>
                            ))}
                          </Attachments>
                          <p>
                            {message.context_plan.used_article_overview ? "已使用文章概览" : "未使用文章概览"} ·
                            {message.context_plan.used_record_context ? " 已使用正文上下文" : " 未使用正文上下文"} ·
                            {message.context_plan.used_dictionary ? " 已查词典" : " 未查词典"}
                          </p>
                        </PlanContent>
                      </Plan>
                    ) : null}
                    <EvidenceDisclosure evidence={message.evidence} />
                    <TraceSummaryDisclosure traceSummary={message.trace_summary} />
                    <ToolTraceBlock entries={message.tool_trace} />
                  </div>
                );
              case "evidence":
              case "trace_summary":
              case "tool_trace":
                return (
                  <div key={`${message.id}-${block.kind}-${index}`} className="space-y-1.5 w-full">
                    <ToolChipRow entries={message.tool_trace} toolLabelFn={toolLabel} />
                    <ToolTraceBlock entries={message.tool_trace} />
                  </div>
                );
              case "follow_up_suggestions":
                return (
                  <FollowUpSuggestionChips
                    key={`${message.id}-${block.kind}-${index}`}
                    suggestions={message.follow_up_suggestions ?? []}
                    onPickSuggestion={(prompt) => {
                      onPickFollowUpSuggestion?.(prompt);
                    }}
                  />
                );
              default:
                return null;
            }
          })}
          {supplementNotice && candidateSupplements.length === 0 && persistedSupplements.length === 0 ? (
            <SupplementCandidateTray
              candidates={candidateSupplements}
              persistedSupplements={persistedSupplements}
              deletingSupplementId={deletingSupplementId}
              notice={supplementNotice}
              onDeletePersistedSupplement={onDeletePersistedSupplement}
            />
          ) : null}
        </div>
        ) : (
          <AiMessage from={message.role} className="w-full max-w-[31rem]">
            {submissionModeOf(message) === "quick_action" ? (
              <MessageContent className="text-[12px] font-medium">
                {messageOperationSummary(message)}
              </MessageContent>
            ) : (
              <MessageContent className="text-[14.5px]">
                <MessageResponse className="ask-message-response whitespace-pre-wrap text-[14.5px] leading-[1.78]">
                  {message.content_md}
                </MessageResponse>
              </MessageContent>
            )}
            <div className="flex items-center justify-end gap-2 pr-1 opacity-70 transition-opacity group-hover:opacity-100">
              <span className="text-[10px] text-muted">
                {message.created_at ? new Date(message.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ''}
              </span>
            </div>
          </AiMessage>
        )}
    </div>
  );
}

function StarterState({
  attachments,
  onPickPrompt,
}: {
  attachments: ReaderAskAttachment[];
  onPickPrompt: (prompt: string, entryAction: ReaderAskEntryActionDto) => void;
}) {
  const starterMode: StarterMode = (() => {
    const selectionAttachment = attachments.find((attachment) => attachment.kind === "text_selection");
    if (selectionAttachment) {
      return selectionAttachment.subtype === "sentence" ? "sentence" : "selection";
    }
    const sentenceAttachment = attachments.find(
      (attachment) => attachment.kind === "analysis_ref" && attachment.subtype === "sentence",
    );
    if (sentenceAttachment) {
      return "sentence";
    }
    return "record";
  })();
  const starterContent = STARTER_CONTENT[starterMode];
  const contextAttachment = attachments.find(
    (attachment) =>
      attachment.kind === "text_selection" ||
      (attachment.kind === "analysis_ref" && Boolean(attachment.selectedText?.trim())),
  );
  const contextLabel =
    starterMode === "sentence"
      ? "当前句子"
      : starterMode === "selection"
        ? "当前选区"
        : null;
  const contextPreview = contextAttachment?.selectedText?.trim() ?? null;
  const suggestions = [
    {
      prompt: starterContent.prompts[0],
      entryAction: "ask_about_this" as const,
      icon: MessageSquare,
      iconClassName: "text-grammar-violet",
      badgeClassName: "bg-[rgba(116,102,148,0.12)]",
    },
    {
      prompt: starterContent.prompts[1],
      entryAction: starterMode === "sentence" ? ("why_here" as const) : ("ask_about_this" as const),
      icon: Search,
      iconClassName: "text-context-blue",
      badgeClassName: "bg-[rgba(76,145,194,0.12)]",
    },
    {
      prompt: starterContent.prompts[2],
      entryAction: "ask_about_this" as const,
      icon: GitBranch,
      iconClassName: "text-structure-green",
      badgeClassName: "bg-[rgba(60,140,104,0.12)]",
    },
    {
      prompt: starterContent.prompts[3],
      entryAction: "ask_about_this" as const,
      icon: PencilLine,
      iconClassName: "text-vocab-amber",
      badgeClassName: "bg-[rgba(228,176,0,0.14)]",
    },
  ];

  return (
    <PromptSuggestions
      title={starterContent.title}
      description={starterContent.description}
      contextLabel={contextLabel}
      contextPreview={contextPreview}
      suggestions={suggestions}
      onPickPrompt={onPickPrompt}
    />
  );
}

export interface AiWorkspacePanelProps {
  open: boolean;
  presentation?: "intensive" | "immersive";
  pageIdentity: ReaderAskPageIdentity;
  recordId: string;
  recordScope?: "analysis" | "reading_record";
  hideClosedLauncher?: boolean;
  recordTitle?: string | null;
  attachments: ReaderAskAttachment[];
  liveContextAttachment?: ReaderAskAttachment | null;
  pendingQuickActionRequest?: ReaderAskQuickActionRequest | null;
  hideLauncherOnMobile?: boolean;
  hideLauncherInCompactLayout?: boolean;
  onRemoveAttachment: (attachmentKey: string) => void;
  onClearAttachments: () => void;
  onAppendAttachments?: (attachments: ReaderAskAttachment[]) => void;
  onJumpToAttachment?: (attachment: ReaderAskAttachment) => void;
  onActionExecuted?: (result: ReaderAskActionConfirmResponseDto["result"]) => void;
  onSupplementDeleted?: (supplementId: string) => void | Promise<void>;
  onPendingQuickActionConsumed?: () => void;
  onActivateLiveContextSelection?: () => void;
  onComposerTextareaFocus?: () => void;
  onComposerTextareaBlur?: () => void;
  onPanelPointerDownOutsideComposer?: () => void;
  onToggle: () => void;
  onAnnotationFeedback?: (params: { entryType: string; entryId: string }) => void;
  analysisRecordId?: string;
}

export function AiWorkspacePanel({
  attachments,
  liveContextAttachment = null,
  pageIdentity,
  pendingQuickActionRequest,
  presentation = "intensive",
  open,
  recordId,
  recordScope = "analysis",
  hideClosedLauncher = false,
  recordTitle,
  hideLauncherOnMobile = false,
  hideLauncherInCompactLayout = false,
  onAppendAttachments,
  onClearAttachments,
  onJumpToAttachment,
  onActionExecuted,
  onActivateLiveContextSelection,
  onComposerTextareaBlur,
  onComposerTextareaFocus,
  onPanelPointerDownOutsideComposer,
  onPendingQuickActionConsumed,
  onSupplementDeleted,
  onRemoveAttachment,
  onToggle,
  onAnnotationFeedback,
  analysisRecordId,
}: AiWorkspacePanelProps) {
  const isReadingRecordScope = recordScope === "reading_record";
  const supportsRelatedRecordContext = !isReadingRecordScope;
  const scopedReaderAskUrl = (pathname: string) => {
    if (!isReadingRecordScope) {
      return pathname;
    }
    const searchParams = new URLSearchParams({
      record_id: recordId,
      record_scope: "reading_record",
    });
    return `${pathname}?${searchParams.toString()}`;
  };
  const launcherVisibilityClass = hideLauncherInCompactLayout
    ? "hidden 2xl:inline-flex"
    : hideLauncherOnMobile
      ? "hidden md:inline-flex"
      : "inline-flex";

  const [threads, setThreads] = useState<ReaderAskThreadSummaryDto[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ReaderAskUiMessageDto[]>([]);
  const [modelOptions, setModelOptions] = useState<ReaderAskModelOptionSummaryDto[]>([]);
  const [defaultModelKey, setDefaultModelKey] = useState<string | null>(null);
  const [selectedModelKey, setSelectedModelKey] = useState<string | null>(null);
  const [modelOptionsLoading, setModelOptionsLoading] = useState(false);
  const [, setModelOptionsError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [pendingActionId, setPendingActionId] = useState<string | null>(null);
  const [pendingSupplementDeleteId, setPendingSupplementDeleteId] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [supplementNotice, setSupplementNotice] = useState<string | null>(null);
  const [supplementNoticeMessageId, setSupplementNoticeMessageId] = useState<string | null>(null);
  const [contextPickerOpen, setContextPickerOpen] = useState(false);
  const [contextSearch, setContextSearch] = useState<ContextRecordSearchState>({
    items: [],
    loading: false,
    query: "",
  });
  const hydrationRef = useRef(0);
  const initInProgressRef = useRef(false);
  const sseAbortRef = useRef<AbortController | null>(null);

  // Abort in-flight SSE and reset init guard when panel closes or component unmounts
  useEffect(() => {
    return () => {
      sseAbortRef.current?.abort();
      sseAbortRef.current = null;
      initInProgressRef.current = false;
    };
  }, [open]);

  const conversationItems: AskPanelConversationItem[] = messages.map((message) => ({
    id: message.id,
    role: message.role,
    status: message.status,
    message,
    blocks: message.role === "assistant" ? buildAssistantBlocks(message) : [],
  }));
  const activeThread = activeThreadId ? threads.find((thread) => thread.id === activeThreadId) ?? null : null;
  const effectiveSelectedModelKey = (() => {
    if (isKnownModelOptionKey(modelOptions, selectedModelKey)) {
      return selectedModelKey;
    }
    const threadKey = activeThread?.selected_model?.key ?? null;
    if (isKnownModelOptionKey(modelOptions, threadKey)) {
      return threadKey;
    }
    if (isKnownModelOptionKey(modelOptions, defaultModelKey)) {
      return defaultModelKey;
    }
    return selectedModelKey ?? threadKey ?? defaultModelKey ?? null;
  })();
  const selectedModelOption = findModelOptionSummary(modelOptions, effectiveSelectedModelKey);
  const selectedModelSummary =
    toSelectedModelSummary(selectedModelOption) ??
    activeThread?.selected_model ??
    toSelectedModelSummary(findModelOptionSummary(modelOptions, defaultModelKey));
  const modelSelectItems = modelOptions.map((item) => ({
    label: item.label,
    value: item.key,
  }));
  const visibleContextAttachments = attachments.filter(
    (attachment) => !(attachment.kind === "record_ref" && attachment.metadata.recordId === recordId),
  );
  const composerContextAttachments = liveContextAttachment
    ? visibleContextAttachments.filter((attachment) => askAttachmentKey(attachment) !== askAttachmentKey(liveContextAttachment))
    : visibleContextAttachments;
  async function fetchThreadList() {
    const payload = await fetchJson<{ items: ReaderAskThreadSummaryDto[] }>(
      isReadingRecordScope
        ? `/api/web/reader-ask/threads?record_id=${encodeURIComponent(recordId)}&record_scope=reading_record`
        : `/api/web/reader-ask/threads?record_id=${encodeURIComponent(recordId)}`,
      undefined,
      "Ask Claread 线程列表加载失败。",
    );
    return payload.items ?? [];
  }

  async function fetchThreadDetail(threadId: string) {
    return fetchJson<ReaderAskThreadDetailDto>(
      scopedReaderAskUrl(`/api/web/reader-ask/threads/${threadId}`),
      undefined,
      "Ask Claread 加载失败。",
    );
  }

  async function fetchContextRecords(query: string) {
    if (!supportsRelatedRecordContext) {
      return { items: [] } satisfies ReaderAskContextRecordSearchResponseDto;
    }
    return fetchJson<ReaderAskContextRecordSearchResponseDto>(
      `/api/web/reader-ask/context-records?query=${encodeURIComponent(query)}&exclude_record_id=${encodeURIComponent(recordId)}`,
      undefined,
      "上下文文章搜索失败。",
    );
  }

  async function fetchModelOptions() {
    return fetchJson<ReaderAskModelOptionListResponseDto>(
      "/api/web/reader-ask/model-options",
      undefined,
      "Ask Claread 模型列表加载失败。",
    );
  }

  async function createThread(title: string) {
    return fetchJson<ReaderAskThreadSummaryDto>(
      "/api/web/reader-ask/threads",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          record_id: recordId,
          title,
          model: effectiveSelectedModelKey,
          ...(isReadingRecordScope ? { record_scope: "reading_record" as const } : {}),
        }),
      },
      "Ask Claread 初始化失败。",
    );
  }

  async function loadThread(threadId: string, nextThreads?: ReaderAskThreadSummaryDto[]) {
    const detail = await fetchThreadDetail(threadId);
    setActiveThreadId(threadId);
    setMessages(detail.messages);
    setSupplementNotice(null);
    const nextSummary = toThreadSummary(detail);
    setSelectedModelKey(detail.selected_model?.key ?? defaultModelKey ?? null);
    setThreads((current) => replaceThreadSummary(nextThreads ?? current, nextSummary));
  }

  useEffect(() => {
    if (!contextPickerOpen || !supportsRelatedRecordContext) {
      return;
    }
    const normalizedQuery = contextSearch.query.trim();

    let cancelled = false;
    const delay = normalizedQuery ? 180 : 0;
    const timer = window.setTimeout(() => {
      setContextSearch((current) => ({ ...current, loading: true }));
      void fetchContextRecords(normalizedQuery)
        .then((payload) => {
          if (cancelled) {
            return;
          }
          setContextSearch((current) => ({
            ...current,
            items: payload.items ?? [],
            loading: false,
          }));
        })
        .catch(() => {
          if (cancelled) {
            return;
          }
          setContextSearch((current) => ({ ...current, items: [], loading: false }));
        });
    }, delay);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [contextPickerOpen, contextSearch.query, recordId, supportsRelatedRecordContext]);

  // Set loading state when panel opens (before fetch starts)
  const [prevOpenForLoading, setPrevOpenForLoading] = useState(open);
  if (open !== prevOpenForLoading) {
    setPrevOpenForLoading(open);
    if (open) {
      setModelOptionsLoading(true);
      setModelOptionsError(null);
    }
  }

  useEffect(() => {
    if (!open) {
      return;
    }

    let cancelled = false;

    void fetchModelOptions()
      .then((payload) => {
        if (cancelled) {
          return;
        }
        setModelOptions(payload.items ?? []);
        setDefaultModelKey(payload.default_key ?? null);
      })
      .catch((error) => {
        if (cancelled) {
          return;
        }
        setModelOptions([]);
        setDefaultModelKey(null);
        setModelOptionsError(error instanceof Error ? error.message : "Ask Claread 模型列表加载失败。");
      })
      .finally(() => {
        if (!cancelled) {
          setModelOptionsLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [open]);

  // Sync selected model key when model options or thread defaults change
  const [prevModelOptions, setPrevModelOptions] = useState(modelOptions);
  const [prevThreadModelKey, setPrevThreadModelKey] = useState<string | null>(
    activeThread?.selected_model?.key ?? null,
  );
  const [prevDefaultModelKey, setPrevDefaultModelKey] = useState(defaultModelKey);
  const currentThreadModelKey = activeThread?.selected_model?.key ?? null;
  if (
    modelOptions !== prevModelOptions ||
    currentThreadModelKey !== prevThreadModelKey ||
    defaultModelKey !== prevDefaultModelKey
  ) {
    setPrevModelOptions(modelOptions);
    setPrevThreadModelKey(currentThreadModelKey);
    setPrevDefaultModelKey(defaultModelKey);
    if (modelOptions.length > 0) {
      setSelectedModelKey((current) => {
        if (isKnownModelOptionKey(modelOptions, current)) {
          return current;
        }
        if (isKnownModelOptionKey(modelOptions, currentThreadModelKey)) {
          return currentThreadModelKey;
        }
        if (isKnownModelOptionKey(modelOptions, defaultModelKey)) {
          return defaultModelKey;
        }
        return current;
      });
    }
  }

  async function ensureThreadReady(): Promise<string | null> {
    setLoading(true);
    setErrorMessage(null);
    try {
      let nextThreads = await fetchThreadList();
      if (nextThreads.length === 0) {
        const createdThread = await createThread(recordTitle || "Ask Claread");
        nextThreads = [createdThread];
      }
      const preferredThreadId =
        (activeThreadId && nextThreads.some((thread) => thread.id === activeThreadId) ? activeThreadId : null) ||
        nextThreads.find((thread) => thread.is_default)?.id ||
        nextThreads[0]?.id ||
        null;
      if (!preferredThreadId) {
        throw new Error("Ask Claread 线程初始化失败。");
      }
      await loadThread(preferredThreadId, nextThreads);
      return preferredThreadId;
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Ask Claread 初始化失败。");
      return null;
    } finally {
      setLoading(false);
    }
  }

  // Reset selected model key when panel opens or record changes
  const [prevInitKey, setPrevInitKey] = useState(`${open}:${recordId}`);
  const currentInitKey = `${open}:${recordId}`;
  if (prevInitKey !== currentInitKey) {
    setPrevInitKey(currentInitKey);
    if (open && recordId) {
      setSelectedModelKey(null);
    }
  }

  useEffect(() => {
    if (!open || !recordId) {
      return;
    }
    hydrationRef.current += 1;
    const currentHydration = hydrationRef.current;
    initInProgressRef.current = true;
    void (async () => {
      try {
        const threadId = await ensureThreadReady();
        if (!threadId || hydrationRef.current !== currentHydration) {
          return;
        }
      } finally {
        initInProgressRef.current = false;
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, recordId]);

  useEffect(() => {
    if (!open || sending || loading || !pendingQuickActionRequest || initInProgressRef.current) {
      return;
    }
    void sendMessage({
      content: pendingQuickActionRequest.content,
      attachments: pendingQuickActionRequest.attachments,
      entryAction: pendingQuickActionRequest.entryAction,
      submissionMode: pendingQuickActionRequest.submissionMode ?? "quick_action",
      clearComposer: false,
    }).finally(() => {
      onPendingQuickActionConsumed?.();
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, sending, loading, pendingQuickActionRequest]);

  async function handleResetConversation() {
    if (!activeThreadId || sending) {
      return;
    }
    setErrorMessage(null);
    setLoading(true);
    try {
      const detail = await fetchJson<ReaderAskThreadDetailDto>(
        scopedReaderAskUrl(`/api/web/reader-ask/threads/${activeThreadId}/reset`),
        {
          method: "POST",
          headers: { "content-type": "application/json" },
        },
        "重置会话失败。",
      );
      setActiveThreadId(detail.id);
      setMessages(detail.messages);
      setSelectedModelKey(detail.selected_model?.key ?? defaultModelKey ?? null);
      setThreads([toThreadSummary(detail)]);
      setSupplementNotice(null);
      setSupplementNoticeMessageId(null);
      onClearAttachments();
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "重置会话失败。");
    } finally {
      setLoading(false);
    }
  }

  async function handleConfirmAction(actionId: string, confirmed: boolean) {
    if (!activeThreadId) {
      return;
    }
    const targetMessageId =
      messages.find((message) => message.action_proposals.some((proposal) => proposal.id === actionId))?.id ?? null;
    setPendingActionId(actionId);
    setErrorMessage(null);
    try {
      const payload = await fetchJson<ReaderAskActionConfirmResponseDto>(
        scopedReaderAskUrl(`/api/web/reader-ask/threads/${activeThreadId}/actions/${actionId}/confirm`),
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ confirmed }),
        },
        "动作确认失败。",
      );
      setMessages((current) =>
        current.map((message) => {
          const hasProposal = message.action_proposals.some((proposal) => proposal.id === actionId);
          if (!hasProposal) {
            return message;
          }
          return {
            ...message,
            action_proposals: message.action_proposals.map((proposal) =>
              proposal.id === actionId
                ? { ...proposal, status: payload.status ?? (confirmed ? "executed" : "rejected") }
                : proposal,
            ),
            persisted_supplements:
              confirmed && payload.result?.persisted_supplement
                ? (() => {
                    const existing = message.persisted_supplements.filter(
                      (item) => item.supplement_id !== payload.result.persisted_supplement?.supplement_id,
                    );
                    return [...existing, payload.result.persisted_supplement];
                  })()
                : message.persisted_supplements,
            trace_summary:
              confirmed && payload.result?.persisted_supplement && message.trace_summary
                ? {
                    ...message.trace_summary,
                    supplement_persisted_count: message.persisted_supplements.filter(
                      (item) => item.lifecycle_status === "persisted",
                    ).length + 1,
                  }
                : message.trace_summary,
          };
        }),
      );
      if (confirmed && payload.result?.persisted_supplement) {
        setSupplementNotice("已把这条 AI 补充写入当前页。");
        setSupplementNoticeMessageId(targetMessageId);
      } else if (!confirmed) {
        setSupplementNotice("已拒绝这条补充候选。");
        setSupplementNoticeMessageId(targetMessageId);
      }
      if (confirmed && payload.result) {
        onActionExecuted?.(payload.result);
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "动作确认失败。");
    } finally {
      setPendingActionId(null);
    }
  }

  async function handleDeletePersistedSupplement(supplementId: string) {
    const targetMessageId =
      messages.find((message) => message.persisted_supplements.some((item) => item.supplement_id === supplementId))?.id ??
      null;
    setPendingSupplementDeleteId(supplementId);
    setErrorMessage(null);
    try {
      const payload = await fetchJson<ReaderAskDeleteSupplementResponseDto>(
        scopedReaderAskUrl(`/api/web/reader-ask/supplements/${supplementId}`),
        {
          method: "DELETE",
          headers: { "content-type": "application/json" },
        },
        "删除补充失败。",
      );
      setMessages((current) =>
        current.map((message) => ({
          ...message,
          persisted_supplements: message.persisted_supplements.map((item) =>
            item.supplement_id === supplementId
              ? payload.persisted_supplement ?? { ...item, lifecycle_status: "deleted" }
              : item,
          ),
          trace_summary:
            message.persisted_supplements.some((item) => item.supplement_id === supplementId) && message.trace_summary
              ? {
                  ...message.trace_summary,
                  supplement_deleted_count: message.trace_summary.supplement_deleted_count + 1,
                }
              : message.trace_summary,
        })),
      );
      setSupplementNotice("已从当前页移除这条 AI 补充。");
      setSupplementNoticeMessageId(targetMessageId);
      await onSupplementDeleted?.(supplementId);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "删除补充失败。");
    } finally {
      setPendingSupplementDeleteId(null);
    }
  }

  function handleAttachRelatedRecord(item: ReaderAskContextRecordItemDto) {
    onAppendAttachments?.([buildRelatedRecordAttachment(pageIdentity, item)]);
    setContextSearch((current) => ({
      ...current,
      query: "",
      loading: false,
    }));
  }

  async function handleSelectDisambiguationCandidate(messageId: string, candidate: ReaderAskContextRecordItemDto) {
    if (sending) {
      return;
    }
    const candidateAttachment = buildRelatedRecordAttachment(pageIdentity, candidate);
    const assistantIndex = messages.findIndex((message) => message.id === messageId);
    const priorUserMessage =
      assistantIndex > 0
        ? [...messages.slice(0, assistantIndex)].reverse().find((message) => message.role === "user")
        : null;
    if (!priorUserMessage?.content_md.trim()) {
      setErrorMessage("没有找到这轮澄清对应的原始问题，暂时无法继续当前讨论。");
      return;
    }
    const baseAttachments = attachmentsFromResolvedContext(priorUserMessage, pageIdentity);
    const nextAttachments = mergeAttachments(baseAttachments, [candidateAttachment]);
    await sendMessage({
      content: priorUserMessage.content_md,
      attachments: nextAttachments,
      entryAction: priorUserMessage.resolved_context_input?.entry_action ?? defaultEntryAction(),
      clearComposer: false,
    });
  }

  async function handleSelectAssetDisambiguationCandidate(
    messageId: string,
    candidate: ReaderAskAssetDisambiguationCandidateDto,
    assetDisambiguation: ReaderAskAssetDisambiguationDto,
  ) {
    if (sending || !assetDisambiguation.record_id) {
      return;
    }
    const candidateAttachment = buildExternalAssetAttachment(
      pageIdentity,
      assetDisambiguation.record_id,
      assetDisambiguation.record_title,
      candidate,
    );
    const assistantIndex = messages.findIndex((message) => message.id === messageId);
    const priorUserMessage =
      assistantIndex > 0
        ? [...messages.slice(0, assistantIndex)].reverse().find((message) => message.role === "user")
        : null;
    if (!priorUserMessage?.content_md.trim()) {
      setErrorMessage("没有找到这轮资产澄清对应的原始问题，暂时无法继续当前讨论。");
      return;
    }
    const baseAttachments = attachmentsFromResolvedContext(priorUserMessage, pageIdentity);
    const nextAttachments = mergeAttachments(baseAttachments, [candidateAttachment]);
    await sendMessage({
      content: priorUserMessage.content_md,
      attachments: nextAttachments,
      entryAction: priorUserMessage.resolved_context_input?.entry_action ?? defaultEntryAction(),
      clearComposer: false,
    });
  }

  async function sendMessage(options?: {
    content?: string;
    attachments?: ReaderAskAttachment[];
    entryAction?: ReaderAskEntryActionDto;
    submissionMode?: "chat" | "quick_action";
    clearComposer?: boolean;
  }) {
    const content = (options?.content ?? "").trim();
    if (!content || sending) {
      return;
    }

    let threadId = activeThreadId;
    if (!threadId) {
      threadId = await ensureThreadReady();
    }
    if (!threadId) {
      return;
    }

    // Only auto-merge liveContextAttachment when the caller does not explicitly
    // provide attachments. Explicit options.attachments means the caller (quick
    // action, HITP candidate, etc.) has already defined the complete context set.
    const includeLiveContext = options?.attachments === undefined;
    const baseAttachments = options?.attachments ?? attachments;
    const usedAttachments = includeLiveContext && liveContextAttachment
      ? mergeAttachments(baseAttachments, [liveContextAttachment])
      : baseAttachments;
    const entryAction = options?.entryAction ?? defaultEntryAction();
    const submissionMode = options?.submissionMode ?? "chat";
    const now = Date.now();
    const tempUserId = `local-user-${now}`;
    const tempAssistantId = `local-assistant-${now}`;
    const userMessage: ReaderAskUiMessageDto = {
      id: tempUserId,
      thread_id: threadId,
      role: "user",
      status: "completed",
      content_md: content,
      submission_mode: submissionMode,
      context_anchors: [],
      citations: [],
      action_proposals: [],
      tool_trace: [],
      evidence: [],
      trace_summary: null,
      disambiguation: null,
      external_asset_disambiguation: null,
      response_cards: [],
      resolved_context: null,
      resolved_intent: null,
      context_plan: null,
      resolved_context_input: buildOptimisticResolvedContextInput(pageIdentity, entryAction, usedAttachments),
      run_info: null,
      supplement_candidates: [],
      persisted_supplements: [],
      reasoning_md: null,
      reasoning_status: null,
      regenerate_preview: false,
      usage_event_id: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    const assistantMessage: ReaderAskUiMessageDto = {
      id: tempAssistantId,
      thread_id: threadId,
      role: "assistant",
      status: "streaming",
      content_md: "",
      submission_mode: submissionMode,
      context_anchors: [],
      citations: [],
      action_proposals: [],
      tool_trace: [],
      evidence: [],
      trace_summary: null,
      disambiguation: null,
      external_asset_disambiguation: null,
      response_cards: [],
      resolved_context: null,
      resolved_intent: null,
      context_plan: null,
      resolved_context_input: null,
      run_info: null,
      supplement_candidates: [],
      persisted_supplements: [],
      reasoning_md: "",
      reasoning_status: "idle",
      follow_up_suggestions: [],
      compacting: false,
      regenerate_preview: false,
      usage_event_id: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };

    setSending(true);
    setErrorMessage(null);
    setSupplementNotice(null);
    setSupplementNoticeMessageId(null);
    const controller = new AbortController();
    sseAbortRef.current = controller;
    setMessages((current) => [...current, userMessage, assistantMessage]);
    setThreads((current) =>
      current.map((thread) =>
        thread.id === threadId
          ? {
              ...thread,
              selected_model: selectedModelSummary ?? thread.selected_model ?? null,
              last_message_at: new Date().toISOString(),
              updated_at: new Date().toISOString(),
            }
          : thread,
      ),
    );

    try {
      const requestBody: ReaderAskMessageStreamRequestDto = {
        content,
        page_identity: serializePageIdentity(pageIdentity),
        attachments: usedAttachments.map(serializeAttachment),
        entry_action: entryAction,
        model: effectiveSelectedModelKey,
      };
      const response = await fetch(scopedReaderAskUrl(`/api/web/reader-ask/threads/${threadId}/messages/stream`), {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(requestBody),
        signal: controller.signal,
      });

      if (!response.ok) {
        const errorText = await response.text().catch(() => "发送失败");
        throw new Error(errorText || "发送消息失败。");
      }

      await consumeReaderAskSse(
        response,
        createSseMessageHandler(
          tempAssistantId,
          (updater) => setMessages(updater),
          (assignedId) =>
            setMessages((current) =>
              current.map((message) => (message.id === tempAssistantId ? { ...message, id: assignedId } : message)),
            ),
          (errorMsg) => setErrorMessage(errorMsg),
        ),
        controller.signal,
      );
      onClearAttachments();
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Ask Claread 暂时不可用。");
      setMessages((current) =>
        current.map((message) => (message.id === tempAssistantId ? { ...message, status: "failed" } : message)),
      );
    } finally {
      if (sseAbortRef.current === controller) {
        sseAbortRef.current = null;
      }
      setSending(false);
    }
  }

  async function handleSend(content: string) {
    await sendMessage({ content });
  }

  /** Regenerate (not resume/continue) the assistant answer for a given message. */
  async function handleRetry(messageId: string) {
    if (!activeThreadId || sending) {
      return;
    }
    // Preserve original content so we can restore it if retry fails
    const originalContentMd = messages.find((m) => m.id === messageId)?.content_md ?? "";
    const originalReasoningMd = messages.find((m) => m.id === messageId)?.reasoning_md ?? "";
    const originalReasoningStatus = messages.find((m) => m.id === messageId)?.reasoning_status ?? "idle";
    setSending(true);
    setErrorMessage(null);
    setSupplementNotice(null);
    setSupplementNoticeMessageId(null);
    const controller = new AbortController();
    sseAbortRef.current = controller;
    setMessages((current) =>
      current.map((message) =>
        message.id === messageId
          ? {
              ...message,
              status: "streaming",
              // For interrupted messages, temporarily keep the partial content visible
              // until the regenerated answer starts streaming in. This is NOT resume/continue.
              content_md: message.status === "interrupted" ? message.content_md : "",
              regenerate_preview: message.status === "interrupted",
              citations: [],
              action_proposals: [],
              tool_trace: [],
              evidence: [],
              trace_summary: null,
              disambiguation: null,
              external_asset_disambiguation: null,
              response_cards: [],
              resolved_context: null,
              context_plan: null,
              resolved_context_input: message.resolved_context_input,
              supplement_candidates: [],
              persisted_supplements: message.persisted_supplements,
              reasoning_status: "idle",
              reasoning_md: "",
              follow_up_suggestions: [],
              compacting: false,
            }
          : message,
      ),
    );

    try {
      const response = await fetch(
        scopedReaderAskUrl(`/api/web/reader-ask/threads/${activeThreadId}/messages/${messageId}/retry/stream`),
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ model: effectiveSelectedModelKey }),
          signal: controller.signal,
        },
      );

      if (!response.ok) {
        const errorText = await response.text().catch(() => "重新生成失败");
        throw new Error(errorText || "重新生成失败。");
      }

      await consumeReaderAskSse(
        response,
        createSseMessageHandler(
          messageId,
          (updater) => setMessages(updater),
          undefined,
          (errorMsg) => setErrorMessage(errorMsg),
        ),
        controller.signal,
      );
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Ask Claread 暂时不可用。");
      setMessages((current) =>
        current.map((message) =>
          message.id === messageId
            ? {
                ...message,
                status: "failed",
                // Restore original content so the user doesn't lose the previous answer
                content_md: originalContentMd,
                reasoning_md: originalReasoningMd,
                reasoning_status: originalReasoningStatus,
              }
            : message,
        ),
      );
    } finally {
      if (sseAbortRef.current === controller) {
        sseAbortRef.current = null;
      }
      setSending(false);
    }
  }

  if (!open) {
    if (hideClosedLauncher) {
      return null;
    }
    return (
      <button
        type="button"
        className={cn(`ai-workspace-launcher ai-workspace-launcher--${presentation}`, workspaceLauncherClassName, launcherVisibilityClass)}
        onClick={onToggle}
        aria-label="打开 AI 工作区"
        title="打开 Ask Claread"
      >
        <span className="brand-aperture-shell relative inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full border">
          <span className="absolute inset-[3px] rounded-full border border-hairline/65" />
          <img
            src="/brand/claread-icon-fullcolor.png"
            alt=""
            aria-hidden="true"
            className={cn(
              "brand-aperture-mark h-[22px] w-[22px] object-contain transition-opacity",
              readerTransitionStandard,
              "group-hover:opacity-95",
            )}
          />
        </span>
      </button>
    );
  }

  return (
    <aside
      className={`ai-workspace-panel ai-workspace-panel--${presentation} fixed inset-x-3 bottom-3 z-50 flex max-h-[82vh] flex-col overflow-hidden rounded-xl border bg-background shadow-lg 2xl:inset-y-3 2xl:left-auto 2xl:right-3 2xl:w-[clamp(31rem,calc((100vw-124px-96ch)/2-0.5rem),37.5rem)] 2xl:min-w-0 2xl:max-h-none`}
      onPointerDownCapture={(event) => {
        const target = event.target instanceof HTMLElement ? event.target : null;
        if (!target) {
          return;
        }
        if (target.closest("[data-ask-composer-textarea='true']")) {
          return;
        }
        if (target.closest("[data-live-context-activator='true']")) {
          return;
        }
        onPanelPointerDownOutsideComposer?.();
      }}
    >
      <div className="border-b bg-background px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2">
            <div className="inline-flex size-8 shrink-0 items-center justify-center rounded-full border bg-background">
              <Sparkles className="h-4 w-4 text-lens-blue" />
            </div>
            <div className="min-w-0">
              <h2 className="truncate text-[15px] font-semibold tracking-[-0.02em] text-ink">Ask Claread</h2>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <IconButton
              variant="quiet"
              size="sm"
              onClick={() => {
                void handleResetConversation();
              }}
              disabled={loading || sending || !activeThreadId}
              aria-label="重新开始"
            >
              <RotateCcw aria-hidden="true" className="h-4 w-4" />
            </IconButton>
            <IconButton variant="quiet" size="sm" onClick={onToggle} aria-label="收起 AI 工作区">
              <X aria-hidden="true" className="h-4 w-4" />
            </IconButton>
          </div>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden pb-2 pt-3">
        {loading ? (
          <AskPanelLoadingState
            title="正在准备 Ask Claread"
            detail="正在恢复当前对话、同步模型设置并准备本轮上下文。"
          />
        ) : (
          <ConversationShell
            className="min-h-0 flex-1"
            hasMessages={messages.length > 0}
            contentClassName={cn(messages.length === 0 ? "" : "gap-6 px-5 pb-8 pt-4")}
            emptyState={
              <StarterState
                attachments={attachments}
                onPickPrompt={(prompt, entryAction) => {
                  void sendMessage({
                    content: prompt,
                    entryAction,
                  });
                }}
              />
            }
          >
            {conversationItems.map((item) => (
              <MessageBubble
                key={item.id}
                item={item}
                currentRecordId={recordId}
                pageIdentity={pageIdentity}
                pendingActionId={pendingActionId}
                deletingSupplementId={pendingSupplementDeleteId}
                supplementNotice={supplementNoticeMessageId === item.id ? supplementNotice : null}
                onConfirmAction={handleConfirmAction}
                onDeletePersistedSupplement={(supplementId) => {
                  void handleDeletePersistedSupplement(supplementId);
                }}
                onSelectDisambiguationCandidate={handleSelectDisambiguationCandidate}
                onSelectAssetDisambiguationCandidate={handleSelectAssetDisambiguationCandidate}
                onRetry={handleRetry}
                onJumpToAttachment={onJumpToAttachment}
                onAnnotationFeedback={onAnnotationFeedback}
                analysisRecordId={analysisRecordId}
                onPickFollowUpSuggestion={(prompt) => {
                  void sendMessage({ content: prompt });
                }}
              />
            ))}
          </ConversationShell>
        )}
      </div>

      <AskComposer
        onSubmit={handleSend}
        sending={sending}
        placeholder={COMPOSER_PLACEHOLDER}
        errorMessage={errorMessage}
        contextStrip={
          recordTitle || composerContextAttachments.length > 0 || liveContextAttachment ? (
            <>
              <CurrentRecordChip recordTitle={recordTitle} />
              <AttachmentChips
                attachments={composerContextAttachments}
                removable
                onRemove={onRemoveAttachment}
                onJump={onJumpToAttachment}
                variant="composer"
              />
              {liveContextAttachment ? (
                <LiveSelectionChip
                  attachment={liveContextAttachment}
                  onActivate={onActivateLiveContextSelection}
                  onRemove={onRemoveAttachment}
                />
              ) : null}
            </>
          ) : undefined
        }
        actionMenu={
          supportsRelatedRecordContext ? (
            <RelatedRecordPicker
              disabled={sending}
              search={contextSearch}
              onSearchChange={(value) => {
                setContextSearch((current) => ({ ...current, query: value }));
              }}
              onAttachRelatedRecord={(item) => {
                void handleAttachRelatedRecord(item);
              }}
            />
          ) : undefined
        }
        actionMenuOpen={contextPickerOpen}
        onActionMenuOpenChange={setContextPickerOpen}
        modelOptions={modelSelectItems}
        modelSelectDisabled={loading || sending || modelOptionsLoading || modelSelectItems.length === 0}
        selectedModelKey={effectiveSelectedModelKey}
        modelPlaceholder={modelOptionsLoading ? "加载模型…" : "选择模型"}
        onModelChange={(value) => setSelectedModelKey(value)}
        onTextareaFocus={onComposerTextareaFocus}
        onTextareaBlur={onComposerTextareaBlur}
      />
    </aside>
  );
}
