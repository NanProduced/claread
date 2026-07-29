"use client";

import {
  BookPlus,
  Check,
  ChevronDown,
  Copy,
  FileText,
  GitBranch,
  LoaderCircle,
  MessageSquare,
  PencilLine,
  Quote,
  PanelRightOpen,
  PictureInPicture2,
  RotateCcw,
  Search,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import {
  InlineCitation,
  InlineCitationCard,
  InlineCitationCardBody,
  InlineCitationCardTrigger,
  InlineCitationCarousel,
  InlineCitationCarouselContent,
  InlineCitationCarouselHeader,
  InlineCitationCarouselIndex,
  InlineCitationCarouselItem,
  InlineCitationCarouselNext,
  InlineCitationCarouselPrev,
  InlineCitationQuote,
  InlineCitationSource,
} from "@/components/ai-elements/inline-citation";
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { SystemMessage } from "@/components/ui/system-message";
import { ClareadAiMark } from "@/components/brand/ClareadAiMark";
import { IconButton } from "@/components/primitives/icon-button";
import { AskComposer } from "@/components/reader/ask-chat/AskComposer";
import { ArticleRagCitationList } from "@/components/reader/ask-chat/ArticleRagCitationList";
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
import { mapAskArticleRagSidecar } from "@/lib/reader-orchestration/status-mapper";
import type {
  ReaderAskActionConfirmResponseDto,
  ReaderAskActionProposalDto,
  ReaderAskAttachmentDto,
  ReaderAskAssetDisambiguationCandidateDto,
  ReaderAskAssetDisambiguationDto,
  ReaderAskAgenticCompletedPayloadDto,
  ReaderAskAgenticTerminalPayloadDto,
  ReaderAskAgenticTerminalStatusDto,
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
  WebSearchModeDto,
} from "@/types/api/reader-ask";
import {
  isReaderAskAgenticAnswerBlockList,
  isReaderAskAgenticCitationList,
  isReaderAskAgenticFinalStatus,
  isReaderAskWebSearchSummary,
  READER_ASK_AGENTIC_EXECUTION_VERSION,
  type ReaderAskAgenticAnswerBlockDto,
} from "@/types/api/reader-ask";
import type {
  NavigateAgenticSource,
  SourceNavigationResult,
} from "@/lib/reader-orchestration/agentic-source-navigation/agentic-source-navigation";
import {
  projectAgenticCitationsForDisplay,
  type AgenticCitationDisplayItem,
} from "./ask/agentic-evidence";
import { AgenticWebSources } from "./ask/agentic-web-sources";
import {
  agenticActivityAriaLabel,
  createIdleAgenticActivityState,
  isAgenticActivityVisible,
  reduceAgenticActivityEvent,
  type AgenticActivityEvent,
  type AgenticActivityState,
} from "./ask/agentic-activity";
import {
  consumeReaderAskSse,
  isReaderAskAgenticCompletedPayload,
  isReaderAskAgenticProgressPayload,
  isReaderAskAgenticReasoningCompletedPayload,
  isReaderAskAgenticReasoningDeltaPayload,
  isReaderAskAgenticReasoningStartedPayload,
  isReaderAskAgenticRunStartedPayload,
  isReaderAskAgenticTerminalPayload,
} from "./ask/sse";
import { TurnLifecycleMetrics } from "./ask/turn-lifecycle";
import {
  ASSET_CLARIFICATION_CONTEXT_MISSING_MESSAGE,
  ASK_UNAVAILABLE_MESSAGE,
  CLARIFICATION_CONTEXT_MISSING_MESSAGE,
  OPTIONAL_TOOL_WARNING_MESSAGE,
  formatAgenticTerminalMessage,
  formatStreamErrorMessage,
  interruptedBubbleMessage,
  toUserFacingErrorMessage,
} from "./ask/ask-error-messages";
import {
  projectActionFailureNotice,
  projectClarifyWarningNotice,
  projectOptionalToolWarning,
  projectPanelInitNotice,
  projectSendFailureNotice,
  projectSupplementFailureNotice,
  projectTurnTerminalNotice,
  type AskSystemNotice,
  type AskSystemNoticeCtaAction,
} from "./ask/ask-system-notice";

type ErrorEnvelope = {
  message?: string;
  detail?: string;
  code?: string;
  payload?: unknown;
};

/** Reads NODE_ENV lazily so tests can toggle dev behavior via stubEnv. */
function isDevMode(): boolean {
  return process.env.NODE_ENV !== "production";
}

/** Returns true when the error is an AbortError from `fetch` / `AbortController`. */
function isAbortError(error: unknown): boolean {
  if (error instanceof DOMException && error.name === "AbortError") {
    return true;
  }
  // Some environments surface aborts as plain Error with name "AbortError".
  return typeof error === "object" && error !== null && (error as { name?: string }).name === "AbortError";
}

const SHOW_ASK_DEBUG_DISCLOSURES = process.env.NEXT_PUBLIC_ASK_CLAREAD_DEBUG === "true";
const COMPOSER_PLACEHOLDER = "继续问这篇文章…";
const workspaceRelatedRecordItemClassName = cn(
  readerPanelItem,
  "w-full justify-between rounded-[12px] px-2.5 py-2 text-left",
);
const workspaceLauncherClassName = cn(
  readerCommandControl,
  "group fixed bottom-[5.25rem] right-4 z-[var(--reader-z-floating-ask)] h-14 w-14 rounded-full border border-hairline/85",
  "bg-surface-raised text-ink shadow-[var(--app-panel-shadow-quiet)] hover:border-muted hover:bg-surface active:scale-[0.98]",
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
  | "article_rag_citations"
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
  return formatStreamErrorMessage(
    event.data as { user_message?: unknown; code?: unknown; detail?: unknown },
    { dev: isDevMode() },
  );
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
  // Production never surfaces raw backend detail / HTTP bodies — only the
  // caller-provided fixed fallback. DEV keeps the raw envelope for debugging.
  if (!isDevMode()) {
    return fallback;
  }
  if (typeof payload === "string" && payload.trim()) {
    return payload.trim();
  }
  if (payload && typeof payload === "object") {
    const envelope = payload as ErrorEnvelope;
    const directDetail = envelope.detail || envelope.message || extractNestedDetail(envelope.payload);
    const code = envelope.code;
    if (directDetail) {
      return code ? `${code}: ${directDetail}` : directDetail;
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

function formatAgenticTerminalError(
  payload: ReaderAskAgenticTerminalPayloadDto,
): string {
  return formatAgenticTerminalMessage(payload, { dev: isDevMode() });
}

function agenticTerminalMessageStatus(
  finalStatus: ReaderAskAgenticTerminalStatusDto,
): "failed" | "interrupted" {
  // Hard failures keep failed; soft/cancel terminals reuse interrupted.
  return finalStatus === "failed" ? "failed" : "interrupted";
}

export function createSseMessageHandler(
  initialMessageId: string,
  updateMessage: MessageUpdater,
  onMessageIdAssigned: ((assignedId: string) => void) | undefined,
  onError: (message: string) => void,
  onAgenticActivity?: (event: AgenticActivityEvent) => void,
  // ASK-UX-MOBILE-R3 — canonical terminal-notice callback. Fired after a
  // trusted identity check passes (see applyAgenticTerminal). The panel uses
  // projectTurnTerminalNotice to build the AskSystemNotice from these fields
  // — it must NOT hand-craft a notice from the formatted message string.
  // Foreign / stale terminals (mismatched message_id / thread_id /
  // turn_run_id vs. the active run identity) are dropped silently: no
  // notice, no UI change, no composer unlock.
  onTerminalNotice?: (args: {
    messageId: string;
    finalStatus: string | null;
    terminalReason: string | null;
  }) => void,
  // ASK-UX-MOBILE-R3 — canonical optional-tool warning callback. Fired
  // from applyAgenticCompleted when the run succeeded (final_status=ok)
  // but an optional tool produced an `unavailable` activity/status during
  // agentic.progress. The panel uses projectOptionalToolWarning to build
  // a dismissible turn-scoped warning notice bound to the canonical
  // assistant message_id. This notice is the SOLE presentation owner for
  // the optional-tool warning — the Web activity / Sources area must not
  // duplicate it. The flag is reset on run_started (per-turn).
  onOptionalToolWarning?: (args: { messageId: string }) => void,
) {
  let currentMessageId = initialMessageId;
  // Agentic terminal may arrive as both agentic.terminal and message.interrupted
  // with the same payload; only apply UI terminal side-effects once per stream.
  let agenticTerminalHandled = false;
  // ASK-UX-MOBILE-R3 — tracks whether any optional tool produced an
  // `unavailable` activity/status during the current run. Mirrors the
  // hasUnavailable flag in agentic-activity.ts reducer (single source of
  // truth for the activity state machine); this local flag is only used
  // to decide whether to fire onOptionalToolWarning at completed time.
  // Reset on run_started so it never bleeds across turns.
  let optionalToolUnavailable = false;
  // ASK-REASONING-R2: strict identity/seq state machine for
  // agentic.reasoning.* (one stream per handler instance). `started`
  // (seq === 0) establishes the turn identity binding; delta/completed
  // must match that identity exactly and carry seq === lastSeq + 1 —
  // duplicates, gaps, out-of-order frames, foreign-turn frames, and
  // repeated started frames are all ignored. A null seq means no started
  // has been accepted yet, so delta/completed are dropped until then.
  // Once completed is accepted the stream is frozen: later deltas are
  // dropped so the displayed text never exceeds the persisted projection
  // (hot≡cold invariant).
  let agenticReasoningBinding: {
    messageId: string;
    threadId: string;
    turnRunId: string;
  } | null = null;
  let agenticReasoningLastSeq: number | null = null;
  let agenticReasoningCompleted = false;
  // R3 P1b: identity of the active run, captured when agentic.run_started
  // is accepted. agentic.reasoning.started must match this exactly to
  // establish a reasoning binding — foreign / stale-turn started frames are
  // ignored. This is part of the same handler state machine (not a second
  // parallel state).
  let activeRunIdentity: {
    messageId: string;
    threadId: string;
    turnRunId: string;
  } | null = null;
  // R4-2: generation_id tracking for message.preview_reset /
  // message.delta attribution. ``null`` means no preview_reset has been
  // accepted yet — the first generation (generation_id=0) is implicitly
  // active. After a trusted preview_reset, only deltas whose
  // generation_id matches ``activeGenerationId`` are applied to
  // provisional_content_md; stale-generation deltas are discarded so
  // the provisional preview never mixes text from two generations.
  let activeGenerationId: number | null = null;
  const commitStreamingMessageUpdate = createStreamingCommit(updateMessage);

  function matchesActiveRunIdentity(payload: {
    message_id?: string | null;
    thread_id?: string | null;
    turn_run_id?: string | null;
  }): boolean {
    return (
      activeRunIdentity === null ||
      (payload.message_id === activeRunIdentity.messageId &&
        payload.thread_id === activeRunIdentity.threadId &&
        payload.turn_run_id === activeRunIdentity.turnRunId)
    );
  }

  function applyAgenticCompleted(payload: ReaderAskAgenticCompletedPayloadDto) {
    // The SSE consumer is the trust owner and never dispatches an unattributed
    // v2 terminal. This local guard protects against foreign/stale frames once
    // run_started has established an identity, without maintaining a second
    // competing pre-start trust policy in the UI handler.
    if (!matchesActiveRunIdentity(payload)) {
      return;
    }
    // Capture the streaming temp id BEFORE reassignment so we can still find it.
    const previousMessageId = currentMessageId;
    if (payload.message_id) {
      currentMessageId = payload.message_id;
      onMessageIdAssigned?.(payload.message_id);
    }
    onAgenticActivity?.({ type: "completed" });
    // ASK-UX-MOBILE-R3 — fire the canonical optional-tool warning when
    // the run succeeded (final_status=ok by definition of the agentic
    // completed path) but an optional tool was unavailable during the
    // run. The panel projects a dismissible turn-scoped warning bound to
    // the canonical assistant message_id. We fire this AFTER the message
    // update is committed so the canonical id is already in place; the
    // panel stores the notice keyed by message_id and renders it on the
    // completed bubble (the render condition no longer swallows notices
    // for status=completed — see MessageBubble).
    if (optionalToolUnavailable && payload.message_id) {
      onOptionalToolWarning?.({ messageId: payload.message_id });
    }
    commitStreamingMessageUpdate((messages) =>
      messages.map((message) => {
        if (
          message.id !== previousMessageId &&
          message.id !== currentMessageId &&
          message.id !== payload.message_id
        ) {
          return message;
        }
        // Preserve any streamed reasoning; agentic completed does not carry it.
        const nextReasoningMd = message.reasoning_md || null;
        // R3 P2: only an accepted agentic.reasoning.completed may mark
        // reasoning completed. If reasoning was still streaming (or has
        // visible text) when the answer completed, freeze it as
        // interrupted — keep the session-visible projection, but do not
        // claim replay equivalence with cold history. No reasoning ⇒ null
        // (no placeholder is rendered).
        const nextReasoningStatus = agenticReasoningCompleted
          ? "completed"
          : message.reasoning_status === "streaming" || nextReasoningMd
            ? "interrupted"
            : message.reasoning_status ?? null;
        return {
          ...message,
          id: payload.message_id,
          thread_id: payload.thread_id || message.thread_id,
          status: "completed",
          // Agentic wire field is answer_text; map into the UI content slot only.
          content_md: payload.answer_text,
          // ASK-TURN-LIFECYCLE R2 — atomically drop the provisional preview
          // when the canonical answer arrives. The provisional slot must
          // never survive a committed terminal.
          provisional_content_md: null,
          // Keep legacy evidence fields untouched — never map agentic evidence
          // into ReaderAskEvidenceItemDto or article_rag sidecar.
          citations: message.citations ?? [],
          action_proposals: message.action_proposals ?? [],
          tool_trace: message.tool_trace ?? [],
          evidence: message.evidence ?? [],
          response_cards: message.response_cards ?? [],
          supplement_candidates: message.supplement_candidates ?? [],
          persisted_supplements: message.persisted_supplements ?? [],
          reasoning_md: nextReasoningMd,
          reasoning_status: nextReasoningStatus,
          replan_status: "idle",
          compacting: false,
          regenerate_preview: false,
          // Agentic path must not carry a legacy article_rag sidecar.
          article_rag: null,
          // Public v2: no raw evidence / handles in browser state.
          agentic_evidence: null,
          agentic_evidence_scope: null,
          // Semantic answer blocks with public citation_ids.
          agentic_answer_blocks: payload.answer_blocks ?? null,
          // Finalizer-minted public citations for InlineCitation only.
          agentic_citations: payload.citations ?? null,
          // Turn-level web search summary (null when search not invoked).
          agentic_web_search: payload.web_search ?? null,
        };
      }),
    true);
  }

  function applyAgenticTerminal(payload: ReaderAskAgenticTerminalPayloadDto) {
    if (agenticTerminalHandled) {
      return;
    }
    // ASK-UX-MOBILE-R3 — foreign / stale terminal guard. If a trusted
    // run_started was accepted, the terminal must match its identity
    // exactly (message_id / thread_id / turn_run_id). A foreign or stale
    // terminal is dropped silently: no notice, no UI change, no composer
    // unlock, no agentic-activity terminal dispatch. This prevents a
    // late-arriving terminal from a previous turn from creating a notice
    // or unlocking the composer for the wrong turn.
    if (!matchesActiveRunIdentity(payload)) {
      return;
    }
    agenticTerminalHandled = true;
    // Capture the streaming temp id BEFORE reassignment so we can still find it.
    const previousMessageId = currentMessageId;
    if (payload.message_id) {
      currentMessageId = payload.message_id;
      onMessageIdAssigned?.(payload.message_id);
    }
    onAgenticActivity?.({
      type: "terminal",
      finalStatus: payload.final_status,
    });
    // ASK-UX-MOBILE-R3 — fire the canonical terminal-notice callback with
    // the typed fields. The panel uses projectTurnTerminalNotice to build
    // the AskSystemNotice. We no longer route the formatted string through
    // onError (which the panel would hand-craft into a notice). onError is
    // now reserved for legacy stream-level `error` events only.
    const terminalMessageId = payload.message_id || currentMessageId;
    const terminalFinalStatus =
      typeof payload.final_status === "string" ? payload.final_status : null;
    const terminalReason =
      typeof payload.terminal_reason === "string" && payload.terminal_reason.trim()
        ? payload.terminal_reason.trim()
        : null;
    onTerminalNotice?.({
      messageId: terminalMessageId,
      finalStatus: terminalFinalStatus,
      terminalReason,
    });
    const nextStatus = agenticTerminalMessageStatus(payload.final_status);
    commitStreamingMessageUpdate((messages) =>
      messages.map((message) => {
        if (
          message.id !== previousMessageId &&
          message.id !== currentMessageId &&
          message.id !== payload.message_id
        ) {
          return message;
        }
        return {
          ...message,
          id: payload.message_id || message.id,
          thread_id: payload.thread_id || message.thread_id,
          status: nextStatus,
          // R4-A6-T3: keep the typed terminal status so the interrupted
          // bubble can refine its copy (context_stale / cancelled / …).
          final_status: payload.final_status,
          // ASK-TURN-LIFECYCLE R2 — non-ok terminals must NEVER preserve
          // the provisional preview as canonical. Drop the provisional
          // slot and keep `content_md` exactly as it was before this
          // turn started (empty for a fresh turn, or the previous
          // canonical answer when this was a retry/regenerate). This
          // fixes the bug where an output-validator failure left a
          // half answer visible in the bubble.
          content_md: message.content_md,
          provisional_content_md: null,
          // ASK-REASONING-R1: session-visible partial reasoning freezes as
          // interrupted on agentic terminals (cancel / failure / budget /
          // persist failure). Cold history never carries it — reload shows
          // no reasoning for this turn.
          reasoning_status:
            message.reasoning_status === "streaming" || message.reasoning_md
              ? "interrupted"
              : message.reasoning_status,
          replan_status: "idle",
          compacting: false,
          regenerate_preview: false,
          // Terminals never carry navigable sources or displayable citations.
          agentic_evidence: null,
          agentic_evidence_scope: null,
          agentic_answer_blocks: null,
          agentic_citations: null,
        };
      }),
    true);
  }

  return function handleSseEvent(event: ReaderAskStreamEnvelopeDto) {
    // Agentic-only progress events are non-terminal. They update the activity
    // indicator only — never complete or fail the assistant bubble.
    if (event.event === "agentic.run_started") {
      if (isReaderAskAgenticRunStartedPayload(event.data)) {
        if (event.data.message_id) {
          currentMessageId = event.data.message_id;
          onMessageIdAssigned?.(event.data.message_id);
        }
        // R3 P1b: capture the active run identity that a later
        // agentic.reasoning.started must match exactly.
        activeRunIdentity = {
          messageId: event.data.message_id,
          threadId: event.data.thread_id,
          turnRunId: event.data.turn_run_id,
        };
        activeGenerationId = 0;
        // ASK-UX-MOBILE-R3 — reset optional-tool warning flag for the
        // new turn. An unavailable optional tool in a previous turn must
        // not bleed into this one.
        optionalToolUnavailable = false;
        onAgenticActivity?.({
          type: "run_started",
          messageId: event.data.message_id ?? currentMessageId,
          turnRunId: event.data.turn_run_id ?? null,
        });
      }
      return;
    }

    if (event.event === "agentic.progress") {
      if (isReaderAskAgenticProgressPayload(event.data)) {
        const progressPayload = event.data as {
          execution_version?: string | null;
          sequence?: number | null;
          phase?: string | null;
          activity?: string | null;
          summary?: string | null;
          elapsed_ms?: number | null;
          tool_name?: string | null;
          status?: string | null;
          duration_ms?: number | null;
          activity_id?: "web_search" | null;
          attempt_count?: number | null;
          call_sequence?: number | null;
        };
        // ASK-UX-MOBILE-R3 — mirror agentic-activity.ts hasUnavailable
        // logic: once an optional tool reports `unavailable` (activity
        // or status), the flag stays true for the rest of the run. This
        // local flag is the sole input to onOptionalToolWarning at
        // completed time (the reducer state is async and may not have
        // applied the latest progress when applyAgenticCompleted fires).
        if (
          progressPayload.activity === "unavailable" ||
          progressPayload.status === "unavailable"
        ) {
          optionalToolUnavailable = true;
        }
        onAgenticActivity?.({
          type: "progress",
          payload: progressPayload,
        });
      }
      return;
    }

    if (event.event === "agentic.terminal") {
      if (isReaderAskAgenticTerminalPayload(event.data)) {
        applyAgenticTerminal(event.data);
      }
      return;
    }

    // ASK-REASONING-R1/R2: safe reasoning projection. The server-side
    // chokepoint owns all redaction / quota — the client only appends.
    // These events reuse the existing reasoning_md / reasoning_status
    // semantic fields (no parallel UI state). started fires only when the
    // provider produced non-empty projected reasoning, so a message with
    // no reasoning never leaves idle state and renders no reasoning UI.
    if (event.event === "agentic.reasoning.started") {
      if (isReaderAskAgenticReasoningStartedPayload(event.data)) {
        const payload = event.data;
        // Strict rules: seq must be exactly 0 and a started may only be
        // accepted once per stream (repeated started ignored).
        if (payload.seq !== 0 || agenticReasoningLastSeq !== null) {
          return;
        }
        // R3 P1b: the started must belong to the active run. If a trusted
        // run_started was accepted, require an exact identity match; a
        // foreign or stale-turn started is ignored (no state change). If no
        // run_started has been seen yet, require at least a strict match to
        // the current message id (fail-closed).
        if (activeRunIdentity !== null) {
          if (
            payload.message_id !== activeRunIdentity.messageId ||
            payload.thread_id !== activeRunIdentity.threadId ||
            payload.turn_run_id !== activeRunIdentity.turnRunId
          ) {
            return;
          }
        } else if (payload.message_id !== currentMessageId) {
          return;
        }
        // Establish the identity binding every later frame must match.
        agenticReasoningBinding = {
          messageId: payload.message_id,
          threadId: payload.thread_id,
          turnRunId: payload.turn_run_id,
        };
        agenticReasoningLastSeq = 0;
        commitStreamingMessageUpdate(
          (messages) =>
            messages.map((message) =>
              message.id === currentMessageId
                ? {
                    ...message,
                    reasoning_status: "streaming",
                    reasoning_md: message.reasoning_md ?? "",
                    compacting: false,
                  }
                : message,
            ),
          true,
        );
      }
      return;
    }

    if (event.event === "agentic.reasoning.delta") {
      if (isReaderAskAgenticReasoningDeltaPayload(event.data)) {
        const payload = event.data;
        const binding = agenticReasoningBinding;
        // Requires an accepted started, no accepted completed (stream
        // frozen), exact identity match (foreign turns dropped), and
        // seq === lastSeq + 1 (duplicates, gaps and out-of-order frames
        // dropped).
        if (
          binding === null ||
          agenticReasoningLastSeq === null ||
          agenticReasoningCompleted
        ) {
          return;
        }
        if (
          payload.message_id !== binding.messageId ||
          payload.thread_id !== binding.threadId ||
          payload.turn_run_id !== binding.turnRunId
        ) {
          return;
        }
        if (payload.seq !== agenticReasoningLastSeq + 1) {
          return;
        }
        agenticReasoningLastSeq = payload.seq;
        const delta = payload.delta;
        // Batched via rAF like message.delta / legacy reasoning.delta.
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
      }
      return;
    }

    if (event.event === "agentic.reasoning.completed") {
      if (isReaderAskAgenticReasoningCompletedPayload(event.data)) {
        const payload = event.data;
        const binding = agenticReasoningBinding;
        // Requires started + exact identity + contiguous seq + content.
        // At most one completed is accepted per stream.
        if (
          binding === null ||
          agenticReasoningLastSeq === null ||
          agenticReasoningCompleted
        ) {
          return;
        }
        if (
          payload.message_id !== binding.messageId ||
          payload.thread_id !== binding.threadId ||
          payload.turn_run_id !== binding.turnRunId
        ) {
          return;
        }
        if (payload.seq !== agenticReasoningLastSeq + 1) {
          return;
        }
        if (payload.has_content !== true) {
          return;
        }
        agenticReasoningLastSeq = payload.seq;
        agenticReasoningCompleted = true;
        // Immediate: the projection is persisted server-side; from now on
        // any reload returns the same text (collapsed, re-expandable).
        // ASK-TURN-LIFECYCLE R3 — persist the typed truncation flag so the
        // UI can surface "达到展示上限" without a marker in the body.
        const reasoningTruncated = payload.truncated === true;
        commitStreamingMessageUpdate(
          (messages) =>
            messages.map((message) =>
              message.id === currentMessageId
                ? {
                    ...message,
                    reasoning_status: "completed",
                    reasoning_truncated: reasoningTruncated,
                  }
                : message,
            ),
          true,
        );
      }
      return;
    }

    if (event.event === "message.started") {
      const messageId = String((event.data as { message_id?: unknown }).message_id ?? currentMessageId);
      currentMessageId = messageId;
      onMessageIdAssigned?.(messageId);
      return;
    }

    if (event.event === "message.preview_reset") {
      // R4-2: canonical preview-reset wire. The server emits this at a
      // tool-result / ModelRetry boundary BEFORE the new generation
      // streams its first delta. The client MUST clear
      // provisional_content_md (the in-progress preview) but MUST NOT
      // touch canonical content_md. Only deltas whose generation_id
      // matches the new generation are applied afterwards.
      //
      // Trust validation: if an active run identity was captured at
      // agentic.run_started, the reset's message_id / thread_id /
      // turn_run_id must match it exactly — foreign / stale resets are
      // ignored (no UI mutation). If no run_started was seen yet, the
      // reset is accepted only when it targets the current message id
      // (fail-closed against unattributed resets).
      const payload = event.data as {
        generation_id?: unknown;
        message_id?: unknown;
        thread_id?: unknown;
        turn_run_id?: unknown;
        reason?: unknown;
      };
      const resetGenerationId =
        typeof payload.generation_id === "number" &&
        Number.isInteger(payload.generation_id)
          ? payload.generation_id
          : null;
      if (resetGenerationId === null || resetGenerationId < 1) {
        // Invalid generation_id — ignore the reset (fail-closed).
        return;
      }
      const resetMessageId =
        typeof payload.message_id === "string" ? payload.message_id : null;
      const resetThreadId =
        typeof payload.thread_id === "string" ? payload.thread_id : null;
      const resetTurnRunId =
        typeof payload.turn_run_id === "string" ? payload.turn_run_id : null;
      if (activeRunIdentity !== null) {
        if (
          resetMessageId !== activeRunIdentity.messageId ||
          resetThreadId !== activeRunIdentity.threadId ||
          resetTurnRunId !== activeRunIdentity.turnRunId
        ) {
          // Foreign / stale reset — ignore.
          return;
        }
      } else if (resetMessageId !== currentMessageId) {
        // No run_started captured and the reset does not target the
        // current message — ignore (fail-closed).
        return;
      }
      const currentGenerationId = activeGenerationId ?? 0;
      if (resetGenerationId <= currentGenerationId) {
        // Duplicate / stale reset — never clear a newer preview.
        return;
      }
      activeGenerationId = resetGenerationId;
      commitStreamingMessageUpdate((messages) =>
        messages.map((message) =>
          message.id === currentMessageId
            ? {
                ...message,
                // R4-2: clear the provisional preview only. Canonical
                // content_md is never touched by a reset — it is
                // replaced atomically by message.completed.
                provisional_content_md: "",
                regenerate_preview: false,
              }
            : message,
        ),
        true,
      );
      return;
    }

    if (event.event === "message.delta") {
      const payload = event.data as {
        delta?: unknown;
        generation_id?: unknown;
        message_id?: unknown;
        thread_id?: unknown;
        turn_run_id?: unknown;
      };
      const delta = String(payload.delta ?? "");
      if (
        activeRunIdentity !== null &&
        (payload.message_id !== activeRunIdentity.messageId ||
          payload.thread_id !== activeRunIdentity.threadId ||
          payload.turn_run_id !== activeRunIdentity.turnRunId)
      ) {
        // Agentic answer deltas are turn-owned. A matching generation is
        // insufficient when the message/thread/run identity is foreign.
        return;
      }
      // R4-2: attribute the delta to the active generation. After a
      // trusted preview_reset, only deltas whose generation_id matches
      // activeGenerationId are applied — stale-generation deltas (from
      // an older generation whose preview was just cleared) are
      // discarded so the provisional preview never mixes text from two
      // generations. Before any preview_reset, generation_id is
      // expected to be 0 (or absent for forward-compat with streams
      // that do not tag deltas).
      const deltaGenerationId =
        typeof payload.generation_id === "number" &&
        Number.isInteger(payload.generation_id)
          ? payload.generation_id
          : null;
      if (activeGenerationId !== null) {
        if (deltaGenerationId !== activeGenerationId) {
          // Stale-generation delta — discard.
          return;
        }
      } else if (deltaGenerationId !== null && deltaGenerationId !== 0) {
        // No preview_reset seen yet but the delta carries a non-zero
        // generation_id — discard (the matching preview_reset was
        // lost or arrived out of order).
        return;
      }
      // ASK-TURN-LIFECYCLE R2 — deltas accumulate into the provisional
      // preview slot only. `content_md` is reserved for the canonical
      // answer that arrives atomically via `message.completed`. This
      // guarantees that an output-validator failure / cancel / abort
      // never preserves a half answer as canonical. The `regenerate_preview`
      // flag is kept for legacy callers but no longer drives a replace-vs-
      // append decision on `content_md` — both paths append to the
      // provisional slot, which is reset on retry boundary (see
      // `resetForRetryBoundary` callers).
      commitStreamingMessageUpdate((messages) =>
        messages.map((message) =>
          message.id === currentMessageId
            ? {
                ...message,
                provisional_content_md: message.regenerate_preview
                  ? delta
                  : `${message.provisional_content_md ?? ""}${delta}`,
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
      // Prefer agentic completed DTO when the wire payload is agentic v1.
      if (isReaderAskAgenticCompletedPayload(event.data)) {
        applyAgenticCompleted(event.data);
        return;
      }
      // Legacy completed: never enter the agentic activity state machine.
      onAgenticActivity?.({ type: "reset" });

      const payload = event.data as unknown as ReaderAskCompletedPayloadDto;
      // Capture the streaming temp id BEFORE reassignment so the optimistic
      // assistant bubble can still be found after the server id lands.
      const previousMessageId = currentMessageId;
      if (payload.id) {
        currentMessageId = payload.id;
        onMessageIdAssigned?.(payload.id);
      }
      commitStreamingMessageUpdate((messages) => {
        const assistantIndex = messages.findIndex(
          (candidate) =>
            candidate.id === previousMessageId ||
            candidate.id === currentMessageId ||
            candidate.id === payload.id,
        );
        const priorUserIndex =
          assistantIndex > 0
            ? [...messages.slice(0, assistantIndex)].reverse().findIndex((candidate) => candidate.role === "user")
            : -1;
        const normalizedPriorUserIndex =
          priorUserIndex >= 0 && assistantIndex > 0 ? assistantIndex - 1 - priorUserIndex : -1;
        return messages.map((message, index) => {
          const isStreamingAssistant =
            message.id === previousMessageId ||
            message.id === currentMessageId ||
            message.id === payload.id;
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
              // ASK-TURN-LIFECYCLE R2 — drop the provisional preview when
              // the canonical legacy completed payload arrives.
              provisional_content_md: null,
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
              // Map raw article_rag sidecar into a UI-safe shape: strips
              // debug-only fields, coerces unknown statuses, and only
              // retains citations when status === "available".
              article_rag: mapAskArticleRagSidecar(payload.article_rag ?? null),
              // Clear any prior agentic evidence so legacy completions cannot
              // keep stale agentic basis from an earlier attempt.
              agentic_evidence: null,
              agentic_evidence_scope: null,
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
      // Agentic non-ok terminal reuses message.interrupted with a typed payload.
      if (isReaderAskAgenticTerminalPayload(event.data)) {
        applyAgenticTerminal(event.data);
        return;
      }

      const payload = event.data as { content_md?: unknown };
      commitStreamingMessageUpdate((messages) =>
        messages.map((message) =>
          message.id === currentMessageId
            ? {
                ...message,
                status: "interrupted",
                // ASK-TURN-LIFECYCLE R2 — legacy interrupted must not
                // preserve the provisional preview. Only a typed
                // `content_md` from the legacy payload (when present)
                // may be promoted to canonical; otherwise keep the
                // existing canonical (empty for a fresh turn).
                content_md: typeof payload.content_md === "string" ? payload.content_md : message.content_md,
                provisional_content_md: null,
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
            ? {
                ...message,
                status: "failed",
                compacting: false,
                replan_status: "idle",
                // ASK-TURN-LIFECYCLE R2 — drop provisional preview on
                // stream error; never preserve half answers.
                provisional_content_md: null,
              }
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

function truncateProvenanceDetail(value: string, max = 80): string {
  const trimmed = value.trim();
  if (trimmed.length <= max) {
    return trimmed;
  }
  return `${trimmed.slice(0, Math.max(0, max - 1)).trimEnd()}…`;
}

function AskProvenanceLine({
  summary,
  details,
}: {
  summary: string;
  details: Array<{ label: string; value: string }>;
}) {
  const [expanded, setExpanded] = useState(false);
  const hasDetails = details.length > 0;
  const summaryClassName =
    "inline-flex max-w-full items-center gap-1 text-[11px] leading-4 text-muted-foreground";

  return (
    <div className="px-4 pt-1.5">
      {hasDetails ? (
        <button
          type="button"
          onClick={() => {
            setExpanded((prev) => !prev);
          }}
          aria-expanded={expanded}
          className={cn(
            summaryClassName,
            "transition-colors hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lens-blue/20",
          )}
        >
          <span className="truncate">{summary}</span>
          <ChevronDown
            aria-hidden="true"
            className={cn("h-3 w-3 shrink-0 transition-transform", expanded && "rotate-180")}
          />
        </button>
      ) : (
        <p className={summaryClassName}>{summary}</p>
      )}
      {expanded && hasDetails ? (
        <ul className="mt-1 space-y-0.5 text-[11px] leading-4 text-muted-foreground">
          {details.map((detail, index) => (
            <li key={`${detail.label}-${index}`} className="flex gap-1">
              <span className="shrink-0 text-ink/60">{detail.label}：</span>
              <span className="truncate">{detail.value}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
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
                <p className="mt-0.5 text-[11px] leading-4 text-muted-foreground">
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

/**
 * Decide whether the article RAG sidecar should render a citation block.
 *
 * This is the single render gate for article_rag citations: status MUST be
 * `available` (already coerced by `mapAskArticleRagSidecar`),
 * `should_attach` MUST be strictly `true`, and the citations list MUST be
 * non-empty. Anything else (silent fallback, debug-only paths, stale or
 * disabled sidecars) returns false so the Ask surface falls back to the
 * ordinary answer with no user-visible error state.
 */
function hasRenderableArticleRagCitations(
  sidecar: ReaderAskUiMessageDto["article_rag"],
): sidecar is NonNullable<NonNullable<ReaderAskUiMessageDto["article_rag"]>> {
  if (!sidecar) return false;
  if (sidecar.status !== "available") return false;
  if (sidecar.should_attach !== true) return false;
  return Array.isArray(sidecar.citations) && sidecar.citations.length > 0;
}

/**
 * Normalize thread-detail / thread-list messages into UI state.
 *
 * The backend `GET /reader-ask/threads/{id}` returns the raw `article_rag`
 * sidecar on each assistant message — that shape contains debug-only
 * fields (`failure_code`, `retryable`, `fallback_allowed`,
 * `source_pack_hash`, `query_sha256`) which MUST NOT enter React state
 * unfiltered. Running every loaded message through `mapAskArticleRagSidecar`
 * guarantees that the field on `ReaderAskUiMessageDto.article_rag` is
 * always the UI-safe projection, regardless of whether the message
 * arrived via SSE `message.completed`, thread-detail fetch, or reset.
 *
 * Reading Record Agentic history (execution_version =
 * reader_record_ask_agentic_v1) is handled here as well: validated
 * `agentic_evidence` is copied into UI state, legacy `article_rag` is
 * forced null, and terminal reloads keep the backend status without
 * inventing answers or firing stream-only onError side effects.
 *
 * The SSE merge path already calls the mapper inline; this helper covers
 * the cold-load / reset paths that bypass streaming. The mapper is
 * idempotent — it only reads `status` / `should_attach` / `context_ids`
 * / `citations` and produces the safe shape — so re-running it on a
 * message that already carries a safe sidecar is a no-op.
 */
function normalizeReaderAskMessages(
  messages: ReaderAskMessageDto[] | ReaderAskUiMessageDto[],
): ReaderAskUiMessageDto[] {
  return messages.map((message) => {
    const uiState = message as Partial<ReaderAskMessageUiStateDto>;
    const isAgenticHistory =
      message.execution_version === READER_ASK_AGENTIC_EXECUTION_VERSION;

    if (!isAgenticHistory) {
      // Legacy RR / Analysis Ask: preserve article_rag normalization only.
      // Missing agentic fields (response_model_exclude_none) must not be
      // treated as agentic.
      return {
        ...message,
        article_rag: mapAskArticleRagSidecar(
          (uiState.article_rag ?? null) as Parameters<typeof mapAskArticleRagSidecar>[0],
        ),
        // Clear any accidental agentic UI state from a prior session.
        agentic_evidence: null,
        agentic_evidence_scope: null,
        // Legacy never carries a web-search summary; clear to prevent a stale
        // summary leaking in from a prior agentic session on the same message id.
        agentic_web_search: null,
        // ASK-TURN-LIFECYCLE R2 — cold history never carries a provisional
        // preview. Only the canonical `content_md` is persisted server-side.
        provisional_content_md: null,
      } as ReaderAskUiMessageDto;
    }

    // Agentic history: fail closed on evidence — never keep raw invalid payload.
    // Public v2 never hydrates raw agentic evidence / handles into UI state.
    const agenticEvidence = null;
    const agenticAnswerBlocks = isReaderAskAgenticAnswerBlockList(
      message.agentic_answer_blocks,
    )
      ? message.agentic_answer_blocks
      : null;
    const agenticCitations = isReaderAskAgenticCitationList(message.agentic_citations)
      ? message.agentic_citations
      : null;
    // Validate the web-search summary with the same guard as the hot SSE path.
    // Malformed summaries must be coerced to null rather than half-accepted.
    const agenticWebSearch = isReaderAskWebSearchSummary(
      uiState.agentic_web_search,
    )
      ? (uiState.agentic_web_search ?? null)
      : null;
    const finalStatus = isReaderAskAgenticFinalStatus(message.final_status)
      ? message.final_status
      : null;

    // Non-ok terminals never keep citations or web-search summary (matches
    // hot applyAgenticTerminal — a terminal turn did not produce a completed
    // answer, so any persisted web_search would be a forgery).
    let finalAnswerBlocks = agenticAnswerBlocks;
    let finalCitations = agenticCitations;
    let finalWebSearch = agenticWebSearch;
    if (finalStatus != null && finalStatus !== "ok") {
      finalAnswerBlocks = null;
      finalCitations = null;
      finalWebSearch = null;
    }

    return {
      ...message,
      // Backend already projected content_md / status for completed & terminal.
      // Never invent answers for terminals; keep content_md as returned.
      execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
      final_status: finalStatus,
      // Public v2: never hydrate raw evidence / scope identity into browser state.
      agentic_evidence: agenticEvidence,
      agentic_evidence_scope: null,
      agentic_answer_blocks: finalAnswerBlocks,
      agentic_citations: finalCitations,
      agentic_web_search: finalWebSearch,
      // Agentic path must not carry legacy article_rag sidecar.
      article_rag: null,
      // Never surface agentic items through the legacy evidence channel.
      evidence: [],
      // ASK-TURN-LIFECYCLE R2 — cold history never carries a provisional
      // preview. Only the canonical `content_md` is persisted server-side.
      provisional_content_md: null,
    } as ReaderAskUiMessageDto;
  });
}

/** Exported for unit tests of cold-load normalization. */
export { normalizeReaderAskMessages };

function buildAssistantBlocks(message: ReaderAskUiMessageDto): AskPanelBlock[] {
  const blocks: AskPanelBlock[] = [];

  const responseCards = message.response_cards ?? [];
  if (submissionModeOf(message) === "quick_action" && responseCards.length > 0) {
    blocks.push({ kind: "response_cards" });
  }
  blocks.push({ kind: "answer" });

  if (responseCards.length > 0 && !(submissionModeOf(message) === "quick_action")) {
    blocks.push({ kind: "response_cards" });
  }
  if (
    SHOW_ASK_DEBUG_DISCLOSURES &&
    (message.context_plan ||
      message.resolved_context_input ||
      (message.evidence ?? []).length > 0 ||
      message.trace_summary)
  ) {
    blocks.push({ kind: "context_summary" });
  }
  if (message.disambiguation?.required) {
    blocks.push({ kind: "disambiguation" });
  }
  if (message.external_asset_disambiguation?.required) {
    blocks.push({ kind: "external_asset_disambiguation" });
  }
  if ((message.action_proposals ?? []).length > 0) {
    blocks.push({ kind: "action_proposals" });
  }
  if ((message.follow_up_suggestions ?? []).length > 0) {
    blocks.push({ kind: "follow_up_suggestions" });
  }
  // Article RAG sidecar citations render before ordinary citations so they
  // stay anchored to the answer body. The block only fires when the
  // normalized sidecar is `available`, `should_attach === true`, and at
  // least one citation was retained by `mapAskArticleRagSidecar`. All
  // other statuses (stale_due_to_repair, disabled, composer_rejected,
  // not_indexed_or_unavailable, empty, unknown) silently fall through.
  if (hasRenderableArticleRagCitations(message.article_rag)) {
    blocks.push({ kind: "article_rag_citations" });
  }
  // Article citations render inline via answer blocks — no end-of-answer Sources list.
  if ((message.citations ?? []).length > 0) {
    blocks.push({ kind: "citations" });
  }
  if ((message.tool_trace ?? []).length > 0 && message.status !== "streaming") {
    blocks.push({ kind: "tool_trace" });
  }
  if (
    pendingSupplementCandidates(message).length > 0 ||
    (message.persisted_supplements ?? []).some((item) => item.lifecycle_status === "persisted")
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
          <p className="text-xs font-medium text-muted-foreground">当前文章</p>
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
                <p className="text-[11px] leading-5 text-muted-foreground">{currentRecordContext.article_overview}</p>
              ) : null}
            </div>
          ) : null}
          {currentRecordContext?.record_insights.length ? (
            <p className="text-[11px] leading-5 text-muted-foreground">
              已并入 {currentRecordContext.record_insights.length} 条当前文章的稳定解析。
            </p>
          ) : null}
        </div>
        {externalRecordContexts.length > 0 ? (
          <div className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground">外部文章</p>
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
                    <p className="text-[11px] leading-5 text-muted-foreground">
                      {item.article_overview
                        ? "已并入文章概览。"
                        : item.record_insights.length > 0
                          ? "已并入记录级稳定解析资产。"
                          : "已定位到文章，但当前没有可用概览。"}
                    </p>
                    {item.article_overview ? (
                      <p className="line-clamp-3 text-[11px] leading-5 text-muted-foreground">{item.article_overview}</p>
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
            <p className="text-xs font-medium text-muted-foreground">外部资产</p>
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
                      <p className="text-[11px] leading-5 text-muted-foreground">{item.content_summary}</p>
                    ) : null}
                    {!item.content_summary && item.content_md ? (
                      <p className="line-clamp-3 text-[11px] leading-5 text-muted-foreground">{item.content_md}</p>
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
                {item.detail ? <p className="text-[12px] leading-6 text-muted-foreground">{item.detail}</p> : null}
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

/**
 * Safe Chinese feedback for legacy Reader-owned source navigation.
 * Kept exported for unit tests / future typed-location adapter wiring.
 * Must never surface enums or internal ids.
 */
export function formatSourceNavigationFeedback(
  result: SourceNavigationResult,
): string {
  switch (result.status) {
    case "navigated":
      return "已定位到文章中的相关位置";
    case "identity_mismatch":
    case "stale_generation":
      return "当前文章版本已更新，无法定位这条历史依据";
    case "target_not_found":
      return "未能在当前文章中找到这条依据";
    case "unavailable":
      if (result.reason === "page_identity_incomplete") {
        return "文章定位信息尚未准备好，请稍后重试";
      }
      if (result.reason === "legacy_scope_missing") {
        return "这条历史依据暂不支持定位";
      }
      return "这条依据暂不支持定位";
    default:
      return "这条依据暂不支持定位";
  }
}

/**
 * Render agentic answer blocks with Markdown + inline AI Elements citations.
 *
 * Each semantic block reuses MessageResponse (Ask Markdown). Article
 * InlineCitation appears after the block (public citation_id + snippet only).
 * No end-of-answer Article Sources.
 *
 * Jump-to-source is intentionally **not** shown until a Reader typed-location
 * adapter consumes the secure navigate API result (message_id + citation_id →
 * server fence → typed location). Do not announce false "已定位" feedback.
 * Follow-up: Plate integration passes only typed location — never handles,
 * evidence scope, or client fence fields.
 */
function AgenticAnswerBlocks({
  blocks,
  citations,
}: {
  blocks: ReaderAskAgenticAnswerBlockDto[];
  citations: AgenticCitationDisplayItem[];
}) {
  const citationById = new Map(citations.map((c) => [c.citationId, c]));

  return (
    <div className="space-y-3" data-testid="agentic-answer-blocks">
      {blocks.map((block, idx) => {
        const blockCitations = (block.citation_ids ?? [])
          .map((citationId) => citationById.get(citationId))
          .filter(
            (citation): citation is AgenticCitationDisplayItem =>
              citation != null && citation.sourceKind === "article",
          );

        return (
          <div key={`block-${idx}`} data-testid={`agentic-answer-block-${idx}`}>
            <MessageResponse
              className="ask-message-response border-0 bg-transparent p-0 text-[14.5px] leading-[1.82] text-reader-reading-ink shadow-none [&_blockquote]:my-2 [&_blockquote]:text-[13px] [&_blockquote]:leading-[1.7] [&_blockquote]:text-reader-reading-muted [&_h2]:mt-6 [&_h2]:text-[1rem] [&_h2]:font-semibold [&_h2]:leading-7 [&_h2]:tracking-[-0.02em] [&_h2]:text-reader-reading-ink-strong [&_h2:first-child]:mt-0 [&_h3]:mt-4 [&_h3]:text-[0.95rem] [&_h3]:font-semibold [&_h3]:leading-6 [&_h3]:text-reader-reading-ink-strong [&_h3:first-child]:mt-0 [&_li]:[&_p+p]:mt-1.5 [&_li]:[&_ul]:mt-2 [&_li]:[&_ol]:mt-2 [&_ol]:my-2.5 [&_ol]:space-y-2.5 [&_ol]:pl-4 [&_ol]:text-[14.5px] [&_ol]:leading-[1.72] [&_ol]:text-reader-reading-ink [&_p]:my-0 [&_p]:text-[14.5px] [&_p]:leading-[1.82] [&_p]:text-reader-reading-ink [&_p+p]:mt-3 [&_ul]:my-2.5 [&_ul]:space-y-2.5 [&_ul]:pl-4 [&_ul]:text-[14.5px] [&_ul]:leading-[1.72] [&_ul]:text-reader-reading-ink"
            >
              {block.text}
            </MessageResponse>
            {blockCitations.length > 0 ? (
              <div className="mt-1.5 inline-flex flex-wrap items-center gap-1.5">
                <InlineCitation>
                  <InlineCitationCard>
                    <InlineCitationCardTrigger
                      aria-label={`查看来源 ${blockCitations[0].citationId}${
                        blockCitations.length > 1
                          ? ` +${blockCitations.length - 1}`
                          : ""
                      } 详情`}
                    >
                      {blockCitations.length === 1
                        ? blockCitations[0].citationId
                        : `${blockCitations[0].citationId} +${blockCitations.length - 1}`}
                    </InlineCitationCardTrigger>
                    <InlineCitationCardBody>
                      {blockCitations.length === 1 ? (
                        <InlineCitationSource title={blockCitations[0].title}>
                          {blockCitations[0].snippet ? (
                            <InlineCitationQuote>
                              {blockCitations[0].snippet}
                            </InlineCitationQuote>
                          ) : null}
                        </InlineCitationSource>
                      ) : (
                        <InlineCitationCarousel count={blockCitations.length}>
                          <InlineCitationCarouselHeader>
                            <div className="flex items-center gap-1">
                              <InlineCitationCarouselPrev data-testid="inline-citation-carousel-prev" />
                              <InlineCitationCarouselNext data-testid="inline-citation-carousel-next" />
                            </div>
                            <InlineCitationCarouselIndex data-testid="inline-citation-carousel-index" />
                          </InlineCitationCarouselHeader>
                          <InlineCitationCarouselContent>
                            {blockCitations.map((citation) => (
                              <InlineCitationCarouselItem key={citation.citationId}>
                                <InlineCitationSource title={citation.title}>
                                  {citation.snippet ? (
                                    <InlineCitationQuote>
                                      {citation.snippet}
                                    </InlineCitationQuote>
                                  ) : null}
                                </InlineCitationSource>
                              </InlineCitationCarouselItem>
                            ))}
                          </InlineCitationCarouselContent>
                        </InlineCitationCarousel>
                      )}
                    </InlineCitationCardBody>
                  </InlineCitationCard>
                </InlineCitation>
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
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
          <div className="space-y-1.5 text-xs text-muted-foreground">
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
                  <p className="text-xs leading-5 text-muted-foreground">{card.translation_zh}</p>
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
                        {part.note ? <p className="text-xs text-muted-foreground">{part.note}</p> : null}
                      </TaskProcessCard>
                    ))}
                  </div>
                ) : null}
                {card.analysis_zh ? (
                  <p className="text-xs leading-5 text-muted-foreground">{card.analysis_zh}</p>
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
  agenticActivity,
}: {
  hasAnswerContent: boolean;
  reasoningStatus: ReaderAskMessageDto["reasoning_status"];
  compacting?: boolean;
  replanStatus?: ReaderAskMessageUiStateDto["replan_status"];
  agenticActivity?: AgenticActivityState | null;
}) {
  if (agenticActivity && isAgenticActivityVisible(agenticActivity)) {
    const title = agenticActivity.currentSummary ?? "Ask Claread 正在工作";
    return (
      <div
        data-testid="ask-agentic-activity"
        data-activity-status={agenticActivity.status}
        data-activity-phase={agenticActivity.currentPhase ?? ""}
        data-activity-sequence={String(agenticActivity.lastSequence)}
        role="status"
        aria-live="polite"
        aria-atomic="true"
        aria-label={agenticActivityAriaLabel(agenticActivity)}
        className="mb-1 inline-flex max-w-full items-center gap-2 rounded-md border border-hairline/70 bg-surface/40 px-2.5 py-1.5 text-[12px] leading-4 text-muted-foreground"
      >
        <span
          aria-hidden="true"
          data-testid="ask-agentic-activity-pulse"
          className={cn(
            "inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-lens-blue/80",
            "motion-safe:animate-pulse",
            "motion-reduce:animate-none",
          )}
        />
        <span className="truncate font-medium text-ink-soft">{title}</span>
      </div>
    );
  }

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
          ? "模型正在整理思路，随后继续输出正文。"
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
    <div
      role="status"
      aria-live="polite"
      className="mb-1 inline-flex max-w-full items-center gap-2 rounded-md border border-hairline/70 bg-surface/40 px-2.5 py-1.5 text-[12px] leading-4 text-muted-foreground"
    >
      <span
        aria-hidden="true"
        className={cn(
          "inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-lens-blue/80",
          "motion-safe:animate-pulse",
          "motion-reduce:animate-none",
        )}
      />
      <span className="min-w-0">
        <span className="block truncate font-medium text-ink-soft">{title}</span>
        <span className="mt-0.5 block truncate text-[11px] text-muted-foreground">
          {detail}
        </span>
      </span>
    </div>
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
            <p className="mt-1.5 text-[13px] leading-6 text-muted-foreground">{detail}</p>
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
  reasoningTruncated,
}: {
  reasoningMd: string | null | undefined;
  reasoningStatus: ReaderAskMessageDto["reasoning_status"];
  reasoningTruncated?: boolean | null;
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
      reasoningTruncated={reasoningTruncated === true}
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
  agenticActivity,
  onNavigateAgenticSource,
  onAnnounce,
  turnNotice,
  onDismissTurnNotice,
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
  agenticActivity?: AgenticActivityState | null;
  onNavigateAgenticSource?: NavigateAgenticSource;
  onAnnounce?: (message: string) => void;
  turnNotice?: AskSystemNotice | null;
  onDismissTurnNotice?: (messageId: string) => void;
}) {
  const { message, blocks } = item;
  const isAssistant = message.role === "assistant";
  const clarificationText = clarificationHint(message.trace_summary, message.evidence);
  const candidateSupplements = pendingSupplementCandidates(message);
  const persistedSupplements = message.persisted_supplements.filter((entry) => entry.lifecycle_status === "persisted");
  // ASK-TURN-LIFECYCLE R2 — pick the visible answer text based on
  // streaming state. While streaming, the provisional preview
  // accumulated from `message.delta` is shown. Once committed
  // (completed / interrupted / failed / cold history), the canonical
  // `content_md` is the only source of truth. This prevents a
  // half-answer from being displayed as canonical after an
  // output-validator failure or cancel.
  const isStreamingAssistant = isAssistant && message.status === "streaming";
  const provisionalPreview = message.provisional_content_md ?? null;
  const displayAnswerContent = isStreamingAssistant && provisionalPreview
    ? provisionalPreview
    : message.content_md;
  const hasAnswerContent = Boolean(displayAnswerContent?.trim());
  const hasAgenticAnswerBlocks = (message.agentic_answer_blocks ?? []).length > 0;
  // Project citations once for both InlineCitation (article) and WebSources (web).
  const agenticCitationItems = hasAgenticAnswerBlocks
    ? projectAgenticCitationsForDisplay(message.agentic_citations ?? [])
    : [];

  return (
    <div
      data-testid={isAssistant ? "ask-assistant-message" : "ask-user-message"}
      data-message-id={message.id}
      data-message-role={message.role}
      className={cn("flex flex-col gap-3", isAssistant ? "items-start" : "items-end")}
    >
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
                        reasoningTruncated={message.reasoning_truncated}
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
                            agenticActivity={agenticActivity}
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
                        {hasAgenticAnswerBlocks ? (
                          <AgenticAnswerBlocks
                            blocks={message.agentic_answer_blocks ?? []}
                            citations={agenticCitationItems}
                          />
                        ) : hasAnswerContent ? (
                          <MessageResponse
                            className="ask-message-response border-0 bg-transparent p-0 text-[14.5px] leading-[1.82] text-reader-reading-ink shadow-none [&_blockquote]:my-2 [&_blockquote]:text-[13px] [&_blockquote]:leading-[1.7] [&_blockquote]:text-reader-reading-muted [&_h2]:mt-6 [&_h2]:text-[1rem] [&_h2]:font-semibold [&_h2]:leading-7 [&_h2]:tracking-[-0.02em] [&_h2]:text-reader-reading-ink-strong [&_h2:first-child]:mt-0 [&_h3]:mt-4 [&_h3]:text-[0.95rem] [&_h3]:font-semibold [&_h3]:leading-6 [&_h3]:text-reader-reading-ink-strong [&_h3:first-child]:mt-0 [&_li]:[&_p+p]:mt-1.5 [&_li]:[&_ul]:mt-2 [&_li]:[&_ol]:mt-2 [&_ol]:my-2.5 [&_ol]:space-y-2.5 [&_ol]:pl-4 [&_ol]:text-[14.5px] [&_ol]:leading-[1.72] [&_ol]:text-reader-reading-ink [&_ol]:marker:font-medium [&_ol]:marker:text-reader-reading-muted [&_p]:my-0 [&_p]:text-[14.5px] [&_p]:leading-[1.82] [&_p]:text-reader-reading-ink [&_p+p]:mt-3 [&_table]:my-3 [&_ul]:my-2.5 [&_ul]:space-y-2.5 [&_ul]:pl-4 [&_ul]:text-[14.5px] [&_ul]:leading-[1.72] [&_ul]:text-reader-reading-ink [&_ul]:marker:text-[0.9em] [&_ul]:marker:text-reader-reading-muted"
                          >
                            {/* ASK-TURN-LIFECYCLE R2 — render provisional
                             * preview while streaming, canonical content_md
                             * otherwise. Copy / actions always use canonical. */}
                            {displayAnswerContent}
                          </MessageResponse>
                        ) : null}
                        {turnNotice ? (
                          // A typed turn notice is the sole presentation owner
                          // for live and cold non-ok terminals as well as
                          // successful optional-tool warnings. The generic
                          // interrupted copy is only a legacy fallback when no
                          // typed notice can be reconstructed.
                          <div data-testid="ask-turn-notice" className="space-y-1">
                            <SystemMessage
                              variant={turnNotice.severity}
                              cta={
                                turnNotice.cta
                                  ? {
                                      label: turnNotice.cta.label,
                                      onClick: () => {
                                        if (turnNotice.cta?.action === "retry") {
                                          onRetry(message.id);
                                        }
                                      },
                                    }
                                  : undefined
                              }
                            >
                              {turnNotice.message}
                            </SystemMessage>
                            {turnNotice.dismissible && onDismissTurnNotice ? (
                              <div className="flex justify-end">
                                <button
                                  type="button"
                                  onClick={() => onDismissTurnNotice(message.id)}
                                  aria-label="关闭提示"
                                  className="shrink-0 rounded p-0.5 text-muted-foreground/70 transition-colors hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lens-blue/20"
                                >
                                  <X aria-hidden="true" className="h-3.5 w-3.5" />
                                </button>
                              </div>
                            ) : null}
                          </div>
                        ) : message.status === "interrupted" ? (
                          <SystemMessage variant="warning">
                            {interruptedBubbleMessage(message.final_status)}
                          </SystemMessage>
                        ) : null}
                        {hasAgenticAnswerBlocks ? (
                          <AgenticWebSources
                            citations={agenticCitationItems}
                            webSearchSummary={message.agentic_web_search ?? null}
                          />
                        ) : null}
                      </div>
                    }
                    footer={
                      message.status === "completed" ||
                      (message.status === "interrupted" && !turnNotice) ? (
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
              case "article_rag_citations":
                // `hasRenderableArticleRagCitations` is the render gate in
                // `buildAssistantBlocks`; the sidecar here is guaranteed to
                // be `available` with `should_attach === true` and at least
                // one citation. The component also re-checks internally as a
                // defensive double gate.
                return message.article_rag ? (
                  <ArticleRagCitationList
                    key={`${message.id}-${block.kind}-${index}`}
                    sidecar={message.article_rag}
                  />
                ) : null;
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
                        <PlanContent className="space-y-3 text-[11px] leading-5 text-muted-foreground">
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
              <span className="text-[10px] text-muted-foreground">
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
      badgeClassName: "bg-grammar-violet/12",
    },
    {
      prompt: starterContent.prompts[1],
      entryAction: starterMode === "sentence" ? ("why_here" as const) : ("ask_about_this" as const),
      icon: Search,
      iconClassName: "text-context-blue",
      badgeClassName: "bg-context-blue/12",
    },
    {
      prompt: starterContent.prompts[2],
      entryAction: "ask_about_this" as const,
      icon: GitBranch,
      iconClassName: "text-structure-green",
      badgeClassName: "bg-structure-green/12",
    },
    {
      prompt: starterContent.prompts[3],
      entryAction: "ask_about_this" as const,
      icon: PencilLine,
      iconClassName: "text-vocab-amber",
      badgeClassName: "bg-vocab-amber/14",
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

export type AiWorkspaceSurface = "sidecar" | "floating";

export interface AiWorkspacePanelProps {
  layout?: "docked" | "overlay";
  onChangeSurface?: (surface: AiWorkspaceSurface) => void;
  open: boolean;
  presentation?: "intensive" | "immersive";
  surface?: AiWorkspaceSurface;
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
  onOpenSidecar?: () => void;
  onToggle: () => void;
  onAnnotationFeedback?: (params: { entryType: string; entryId: string }) => void;
  analysisRecordId?: string;
  capacityDowngradeNotice?: string | null;
  onDismissCapacityDowngradeNotice?: () => void;
  /**
   * Reader-owned NavigateAgenticSource callback (R3C-A). Optional — Analysis
   * Ask and callers without wiring keep Sources display-only.
   * Must not pass CurrentPageIdentity / Document / Element here.
   */
  onNavigateAgenticSource?: NavigateAgenticSource;
  /**
   * ASK-UX-MOBILE — whether the host layout currently has room for the
   * sidecar surface. When false, the surface switch menu is replaced by a
   * static「浮窗」label so the user cannot pick an unavailable surface.
   * Defaults to true so Analysis-scope callers (which never pass it) keep
   * the existing menu behavior.
   */
  hasSidecarCapacity?: boolean;
}

export function AiWorkspacePanel({
  layout = "overlay",
  onChangeSurface,
  attachments,
  liveContextAttachment = null,
  pageIdentity,
  pendingQuickActionRequest,
  presentation = "intensive",
  surface = "sidecar",
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
  capacityDowngradeNotice,
  onDismissCapacityDowngradeNotice,
  onNavigateAgenticSource,
  hasSidecarCapacity = true,
}: AiWorkspacePanelProps) {
  const isFloatingSurface = surface === "floating";
  const [liveAnnouncement, setLiveAnnouncement] = useState("");
  const panelHeadingRef = useRef<HTMLHeadingElement>(null);
  const explicitSurfaceSwitchRef = useRef<AiWorkspaceSurface | null>(null);
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
  const [agenticActivity, setAgenticActivity] = useState<AgenticActivityState>(
    () => createIdleAgenticActivityState(),
  );
  const [modelOptions, setModelOptions] = useState<ReaderAskModelOptionSummaryDto[]>([]);
  const [defaultModelKey, setDefaultModelKey] = useState<string | null>(null);
  const [selectedModelKey, setSelectedModelKey] = useState<string | null>(null);
  const [webSearchMode, setWebSearchMode] = useState<WebSearchModeDto>("disabled");
  const [modelOptionsLoading, setModelOptionsLoading] = useState(false);
  const [, setModelOptionsError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [pendingActionId, setPendingActionId] = useState<string | null>(null);
  const [pendingSupplementDeleteId, setPendingSupplementDeleteId] = useState<string | null>(null);
  // ASK-UX-MOBILE R2 — turn-scoped system notices keyed by messageId. These
  // persist across new turns (do not drift to the composer) and render
  // inside the corresponding assistant turn bubble, not above the composer.
  const [turnNotices, setTurnNotices] = useState<Record<string, AskSystemNotice>>({});
  // ASK-UX-MOBILE R2 — panel-level init / restore / capability notice.
  // Renders in a dedicated banner slot between the header and the
  // conversation wrapper, never in a turn bubble or the composer.
  const [panelNotice, setPanelNotice] = useState<AskSystemNotice | null>(null);
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
  const provenanceSignatureRef = useRef<string | null>(null);
  // Active streaming assistant id — used to attach activity UI only to the
  // current turn and avoid stale indicators on older messages.
  const streamingAssistantIdRef = useRef<string | null>(null);
  // ASK-TURN-LIFECYCLE R3 — per-turn lifecycle metrics. Created when a
  // turn starts (sendMessage / handleRetry), passed to the SSE consumer,
  // and finalized (``markComposerEnabled``) in the ``finally`` block
  // when ``setSending(false)`` runs. Logged to console.info as a
  // log-safe JSON object — never contains content / reasoning / secrets.
  const turnMetricsRef = useRef<TurnLifecycleMetrics | null>(null);

  const dispatchAgenticActivity = (event: AgenticActivityEvent) => {
    setAgenticActivity((current) => reduceAgenticActivityEvent(current, event));
  };

  // Abort in-flight SSE and reset init guard when panel closes or component unmounts
  useEffect(() => {
    return () => {
      sseAbortRef.current?.abort();
      sseAbortRef.current = null;
      initInProgressRef.current = false;
      // ASK-TURN-LIFECYCLE R3 — drop the metrics ref so a stale metrics
      // object from a previous turn is never logged after unmount.
      turnMetricsRef.current = null;
    };
  }, [open]);

  // ASK-UX-MOBILE R2 — minimal body scroll lock for the floating overlay.
  // Only active when the panel is open, in overlay layout, and on the
  // floating surface. Prevents background scroll on mobile while the
  // floating panel is visible. Restores the previous body overflow on
  // cleanup. SSR-safe via typeof document guard.
  useEffect(() => {
    if (typeof document === "undefined") {
      return;
    }
    if (!open || !isFloatingSurface || layout !== "overlay") {
      return;
    }
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [open, isFloatingSurface, layout]);

  const conversationItems: AskPanelConversationItem[] = messages.map((message) => ({
    id: message.id,
    role: message.role,
    status: message.status,
    message,
    blocks: message.role === "assistant" ? buildAssistantBlocks(message) : [],
  }));
  const latestUserMessageId = (() => {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      if (messages[index]?.role === "user") {
        return messages[index]?.id ?? null;
      }
    }
    return null;
  })();
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
  // ASK-WEB-G1-R2: server-declared Web Search capability for the current
  // model option. ``available`` only when a real provider is wired via
  // ``settings.reader_record_ask_web_search_provider``. The Search toggle
  // is visible/enabled only when the host has declared this capability —
  // never inferred from the request toggle or page scope alone. Defaults
  // to ``"unavailable"`` when the field is absent (legacy backend /
  // model option not yet loaded) — fail-closed: toggle hidden.
  const webSearchCapabilityAvailable =
    selectedModelOption?.web_search_capability === "available";
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

  const hasProvenancePageIdentity = Boolean(recordTitle?.trim());
  const hasProvenanceLiveSelection = Boolean(liveContextAttachment);
  const provenanceNoteCount = composerContextAttachments.length;
  const provenanceParts: string[] = [];
  if (hasProvenancePageIdentity) {
    provenanceParts.push("当前文章");
  }
  if (hasProvenanceLiveSelection) {
    provenanceParts.push("选中句");
  }
  if (provenanceNoteCount > 0) {
    provenanceParts.push(`${provenanceNoteCount} 条笔记`);
  }
  const provenanceJoinedParts = provenanceParts.join(" · ");
  const provenanceSummary =
    provenanceParts.length > 0 ? `基于：${provenanceJoinedParts}` : "仅按你的问题回答";
  const provenanceDetails: Array<{ label: string; value: string }> = [];
  if (recordTitle?.trim()) {
    provenanceDetails.push({ label: "当前文章", value: recordTitle.trim() });
  }
  if (liveContextAttachment) {
    const selectionText = liveContextAttachment.selectedText?.trim();
    provenanceDetails.push({
      label: "选中句",
      value: truncateProvenanceDetail(selectionText || askAttachmentLabel(liveContextAttachment)),
    });
  }
  composerContextAttachments.forEach((attachment, index) => {
    provenanceDetails.push({
      label: `笔记 ${index + 1}`,
      value: truncateProvenanceDetail(askAttachmentLabel(attachment)),
    });
  });
  const provenanceSignature = [
    pageIdentity.recordId ?? "",
    ...provenanceDetails.map((detail) => `${detail.label}:${detail.value}`),
  ].join("\u001f");
  useEffect(() => {
    if (provenanceSignatureRef.current === null) {
      provenanceSignatureRef.current = provenanceSignature;
      return;
    }
    if (provenanceSignatureRef.current !== provenanceSignature) {
      provenanceSignatureRef.current = provenanceSignature;
      if (provenanceJoinedParts.length > 0) {
        window.setTimeout(() => {
          setLiveAnnouncement(`Ask Claread 上下文已更新：${provenanceJoinedParts}`);
        }, 0);
      }
    }
  }, [provenanceSignature, provenanceJoinedParts]);

  // ASK-WEB-G1-R2: reset the user-visible web search toggle to
  // ``"disabled"`` when the currently selected model option does not
  // declare web search capability. This prevents a stale ``"allowed"``
  // state from leaking into a subsequent send when the user switches to
  // a model whose provider is not wired for web search. The toggle is
  // already hidden by the capability gate, but the internal state must
  // also be reset so the request body never carries ``allowed`` for
  // a model that cannot execute it — fail-closed by construction.
  useEffect(() => {
    if (!webSearchCapabilityAvailable && webSearchMode === "allowed") {
      setWebSearchMode("disabled");
    }
  }, [webSearchCapabilityAvailable, webSearchMode]);

  useEffect(() => {
    if (explicitSurfaceSwitchRef.current !== surface) {
      return;
    }
    explicitSurfaceSwitchRef.current = null;
    setLiveAnnouncement(
      surface === "floating"
        ? "Ask Claread 已切换为浮窗，位于右下角。"
        : "Ask Claread 已切换为侧边栏。",
    );
    panelHeadingRef.current?.focus();
  }, [surface]);

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
    const normalizedMessages = normalizeReaderAskMessages(detail.messages);
    setActiveThreadId(threadId);
    setMessages(normalizedMessages);
    setSupplementNotice(null);
    // ASK-UX-MOBILE R2 — reconstruct turn-scoped notices for cold history.
    // Assistant messages with a non-ok final_status get a turn notice so the
    // failure context survives a reload. The render layer suppresses turn
    // notices for "interrupted" status (the interrupted bubble handles those);
    // only non-interrupted, non-completed statuses actually render the notice.
    const restoredNotices: Record<string, AskSystemNotice> = {};
    for (const msg of normalizedMessages) {
      if (msg.role !== "assistant") {
        continue;
      }
      const fs = typeof msg.final_status === "string" ? msg.final_status : null;
      if (fs && fs !== "ok") {
        restoredNotices[msg.id] = projectTurnTerminalNotice({
          messageId: msg.id,
          finalStatus: fs,
          terminalReason: null,
          dev: isDevMode(),
        });
      }
    }
    setTurnNotices(restoredNotices);
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
      setPanelNotice(null);
      return preferredThreadId;
    } catch (error) {
      setPanelNotice(
        projectPanelInitNotice({
          kind: "init",
          message: toUserFacingErrorMessage(error, "Ask Claread 初始化失败。"),
        }),
      );
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
      setMessages(normalizeReaderAskMessages(detail.messages));
      setSelectedModelKey(detail.selected_model?.key ?? defaultModelKey ?? null);
      setThreads([toThreadSummary(detail)]);
      setSupplementNotice(null);
      setSupplementNoticeMessageId(null);
      setTurnNotices({});
      setPanelNotice(null);
      onClearAttachments();
    } catch (error) {
      setPanelNotice(
        projectPanelInitNotice({
          kind: "init",
          message: toUserFacingErrorMessage(error, "重置会话失败。"),
        }),
      );
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
      const actionMsg = toUserFacingErrorMessage(error, "动作确认失败。");
      if (targetMessageId) {
        // ASK-UX-MOBILE-R3 — action-confirm failure uses the canonical
        // projector. NOT retryable via "重新生成" (regenerate would
        // discard the action context); dismissible so the user can
        // clear the notice and retry the action card directly.
        setTurnNotices((prev) => ({
          ...prev,
          [targetMessageId]: projectActionFailureNotice({
            messageId: targetMessageId,
            message: actionMsg,
          }),
        }));
      } else {
        setPanelNotice(
          projectPanelInitNotice({ kind: "init", message: actionMsg }),
        );
      }
    } finally {
      setPendingActionId(null);
    }
  }

  async function handleDeletePersistedSupplement(supplementId: string) {
    const targetMessageId =
      messages.find((message) => message.persisted_supplements.some((item) => item.supplement_id === supplementId))?.id ??
      null;
    setPendingSupplementDeleteId(supplementId);
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
      const deleteMsg = toUserFacingErrorMessage(error, "删除补充失败。");
      if (targetMessageId) {
        // ASK-UX-MOBILE-R3 — supplement-delete failure uses the canonical
        // projector. NOT retryable via "重新生成" (regenerate would not
        // retry the delete); dismissible so the user can clear the
        // notice and retry the delete control directly.
        setTurnNotices((prev) => ({
          ...prev,
          [targetMessageId]: projectSupplementFailureNotice({
            messageId: targetMessageId,
            message: deleteMsg,
          }),
        }));
      } else {
        setPanelNotice(
          projectPanelInitNotice({ kind: "init", message: deleteMsg }),
        );
      }
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
      // ASK-UX-MOBILE-R3 — clarify warning uses the canonical projector.
      setTurnNotices((prev) => ({
        ...prev,
        [messageId]: projectClarifyWarningNotice({
          messageId,
          message: CLARIFICATION_CONTEXT_MISSING_MESSAGE,
        }),
      }));
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
      setTurnNotices((prev) => ({
        ...prev,
        [messageId]: projectClarifyWarningNotice({
          messageId,
          message: ASSET_CLARIFICATION_CONTEXT_MISSING_MESSAGE,
        }),
      }));
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
      article_rag: null,
      // Record the user's web search request mode at send time so the
      // backend can persist it as message metadata and replay the original
      // turn capability on retry (server-side source of truth). Absent on
      // cold history; retry resolves the mode from persisted metadata only.
      web_search_mode: webSearchMode,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    const assistantMessage: ReaderAskUiMessageDto = {
      id: tempAssistantId,
      thread_id: threadId,
      role: "assistant",
      status: "streaming",
      content_md: "",
      // ASK-TURN-LIFECYCLE R2 — provisional preview slot starts empty.
      // `message.delta` accumulates here; `content_md` is reserved for
      // the canonical answer that arrives via `message.completed`.
      provisional_content_md: "",
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
      // No article_rag sidecar until message.completed arrives — streaming
      // must not show partial citations.
      article_rag: null,
      // Clear agentic evidence so a new turn never inherits prior basis.
      agentic_evidence: null,
      agentic_evidence_scope: null,
      agentic_answer_blocks: null,
      agentic_citations: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };

    setSending(true);
    setSupplementNotice(null);
    setSupplementNoticeMessageId(null);
    // New user turn: clear previous activity so old summaries never linger.
    dispatchAgenticActivity({ type: "reset" });
    streamingAssistantIdRef.current = tempAssistantId;
    const controller = new AbortController();
    sseAbortRef.current = controller;
    // ASK-TURN-LIFECYCLE R3 — start per-turn metrics. The SSE consumer
    // records first_reasoning / first_answer_delta / last_answer_delta /
    // validation_done / persistence_done / terminal_received; the
    // ``finally`` block records composer_enabled.
    turnMetricsRef.current = new TurnLifecycleMetrics();
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
        web_search_mode: webSearchMode,
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
          (assignedId) => {
            streamingAssistantIdRef.current = assignedId;
            setMessages((current) =>
              current.map((message) => (message.id === tempAssistantId ? { ...message, id: assignedId } : message)),
            );
          },
          (errorMsg) => {
            // ASK-UX-MOBILE-R3 — legacy stream-level `error` event path
            // (e.g. INSUFFICIENT_CREDITS surfaced before any
            // message.assigned). Use the canonical projector instead of
            // hand-crafting a notice. Fall back to tempAssistantId when
            // the server errors before assigning a canonical message id.
            const streamingId = streamingAssistantIdRef.current ?? tempAssistantId;
            setTurnNotices((prev) => ({
              ...prev,
              [streamingId]: projectSendFailureNotice({
                messageId: streamingId,
                message: errorMsg,
              }),
            }));
          },
          dispatchAgenticActivity,
          // ASK-UX-MOBILE-R3 — canonical terminal-notice path. The SSE
          // handler has already verified the terminal matches the active
          // run identity (foreign/stale terminals are dropped silently
          // before this callback fires). projectTurnTerminalNotice builds
          // the AskSystemNotice from the typed fields; the panel never
          // hand-crafts a notice for a live terminal.
          (terminalArgs) => {
            setTurnNotices((prev) => ({
              ...prev,
              [terminalArgs.messageId]: projectTurnTerminalNotice({
                messageId: terminalArgs.messageId,
                finalStatus: terminalArgs.finalStatus,
                terminalReason: terminalArgs.terminalReason,
                dev: isDevMode(),
              }),
            }));
          },
          // ASK-UX-MOBILE-R3 — optional-tool warning. Fired when the run
          // succeeded (final_status=ok) but an optional tool was
          // unavailable. Bound to the canonical assistant message_id; the
          // notice is the SOLE presentation owner (Web activity / Sources
          // must not duplicate it). Dismissible; no CTA.
          (warningArgs) => {
            const warningNotice = projectOptionalToolWarning({
              messageId: warningArgs.messageId,
              message: OPTIONAL_TOOL_WARNING_MESSAGE,
            });
            if (warningNotice !== null) {
              setTurnNotices((prev) => ({
                ...prev,
                [warningArgs.messageId]: warningNotice,
              }));
            }
          },
        ),
        controller.signal,
        turnMetricsRef.current ?? undefined,
      );
      onClearAttachments();
    } catch (error) {
      // User-initiated stop: abort the SSE stream without showing an error.
      // Mark the assistant message as "interrupted" so the UI reflects the
      // user's intent rather than a system failure.
      if (isAbortError(error)) {
        setMessages((current) =>
          current.map((message) =>
            message.id === tempAssistantId ? { ...message, status: "interrupted" } : message,
          ),
        );
        dispatchAgenticActivity({ type: "terminal", finalStatus: "cancelled" });
      } else {
        // ASK-UX-MOBILE-R3 — send failure (network / non-ok / thrown).
        // Use the canonical projector. The message is always typed copy
        // from ask-error-messages.ts via toUserFacingErrorMessage.
        setTurnNotices((prev) => ({
          ...prev,
          [tempAssistantId]: projectSendFailureNotice({
            messageId: tempAssistantId,
            message: toUserFacingErrorMessage(error, ASK_UNAVAILABLE_MESSAGE),
          }),
        }));
        setMessages((current) =>
          current.map((message) => (message.id === tempAssistantId ? { ...message, status: "failed" } : message)),
        );
        dispatchAgenticActivity({ type: "terminal", finalStatus: "failed" });
      }
    } finally {
      if (sseAbortRef.current === controller) {
        sseAbortRef.current = null;
      }
      // Hide activity indicator once the stream ends (completed/terminal already
      // froze the state; reset to idle so a completed answer is not stuck loading).
      setAgenticActivity((current) =>
        current.status === "running" || current.status === "degraded"
          ? createIdleAgenticActivityState()
          : current.status === "completed" || current.status === "failed" || current.status === "cancelled"
            ? createIdleAgenticActivityState()
            : current,
      );
      streamingAssistantIdRef.current = null;
      setSending(false);
      // ASK-TURN-LIFECYCLE R3 — composer is interactive again. Log the
      // per-turn lifecycle metrics as a log-safe JSON object (no content,
      // reasoning text, citations, or secrets — only timestamps in ms
      // relative to turn start). The gap
      // ``composer_enabled - terminal_received`` is the client-side
      // unlock latency.
      const metrics = turnMetricsRef.current;
      if (metrics !== null) {
        metrics.markComposerEnabled();
        // eslint-disable-next-line no-console
        console.info(
          "[AskTurnLifecycle] metrics",
          JSON.stringify(metrics.toJSON()),
        );
        turnMetricsRef.current = null;
      }
    }
  }

  async function handleSend(content: string) {
    await sendMessage({ content });
  }

  /** Stop the in-flight SSE stream (user clicked the stop button). */
  function handleStop() {
    if (sseAbortRef.current) {
      sseAbortRef.current.abort();
      sseAbortRef.current = null;
    }
  }

  // ASK-UX-MOBILE R2 — panel banner CTA handler. "reload" re-runs the
  // init flow (thread + model load); "dismiss" clears the banner.
  function handlePanelCta(action: AskSystemNoticeCtaAction) {
    if (action === "reload") {
      setPanelNotice(null);
      void ensureThreadReady();
    } else if (action === "dismiss") {
      setPanelNotice(null);
    }
  }

  // ASK-UX-MOBILE-R3 — dismiss a single turn-scoped notice by message id.
  // Only removes the notice for the targeted turn; other turns and the
  // underlying assistant message are untouched. Used by the dismiss button
  // on dismissible turn notices (optional-tool warning, clarify warning,
  // action / supplement failure). Non-dismissible notices (hard terminal,
  // send failure) have no dismiss button and cannot be cleared this way.
  function handleDismissTurnNotice(messageId: string) {
    setTurnNotices((prev) => {
      if (!prev[messageId]) {
        return prev;
      }
      const next = { ...prev };
      delete next[messageId];
      return next;
    });
  }

  /** Regenerate (not resume/continue) the assistant answer for a given message. */
  async function handleRetry(messageId: string) {
    if (!activeThreadId || sending) {
      return;
    }
    // Preserve original content so we can restore it if retry fails
    const originalMessage = messages.find((m) => m.id === messageId);
    const originalContentMd = originalMessage?.content_md ?? "";
    const originalReasoningMd = originalMessage?.reasoning_md ?? "";
    const originalReasoningStatus = originalMessage?.reasoning_status ?? "idle";

    // ASK-WEB-G1-R3: Retry body must NOT carry `web_search_mode`. The FastAPI
    // Retry schema is `extra="forbid"` with only `model` accepted; sending
    // `web_search_mode` would 422. The backend replays the persisted mode
    // from the original user message metadata (server-side source of truth),
    // after verifying message/thread/record/user ownership. We no longer
    // infer the original mode from `agentic_web_search` either — that
    // heuristic was wrong when capability was allowed but the agent never
    // invoked Search.

    setSending(true);
    setSupplementNotice(null);
    setSupplementNoticeMessageId(null);
    dispatchAgenticActivity({ type: "reset" });
    streamingAssistantIdRef.current = messageId;
    const controller = new AbortController();
    sseAbortRef.current = controller;
    // ASK-TURN-LIFECYCLE R3 — start per-turn metrics for retry. Same
    // lifecycle as sendMessage: SSE consumer records phase timestamps,
    // ``finally`` records composer_enabled.
    turnMetricsRef.current = new TurnLifecycleMetrics();
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
              // ASK-TURN-LIFECYCLE R2 — reset the provisional preview slot
              // for the new generation. The previous canonical answer
              // (if any) stays in `content_md` as the display fallback
              // until the new turn's `message.completed` atomically
              // replaces it. Server-owned generation reset: no prefix
              // from the previous generation may survive into the new
              // provisional slot.
              provisional_content_md: "",
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
              // Clear any prior article_rag sidecar so streaming doesn't
              // render stale citations from the previous attempt.
              article_rag: null,
              // Clear agentic evidence so retry does not keep prior basis.
              agentic_evidence: null,
              agentic_evidence_scope: null,
              agentic_answer_blocks: null,
              agentic_citations: null,
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
          // ASK-WEB-G1-R3: only `model` is sent. The backend replays the
          // persisted `web_search_mode` from the original user message
          // metadata after ownership verification — no client input.
          body: JSON.stringify({
            model: effectiveSelectedModelKey,
          }),
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
          (assignedId) => {
            streamingAssistantIdRef.current = assignedId;
          },
          (errorMsg) => {
            // ASK-UX-MOBILE-R3 — legacy stream-level `error` event path.
            // Use the canonical projector. Fall back to messageId when
            // the server errors before assigning a canonical message id.
            const streamingId = streamingAssistantIdRef.current ?? messageId;
            setTurnNotices((prev) => ({
              ...prev,
              [streamingId]: projectSendFailureNotice({
                messageId: streamingId,
                message: errorMsg,
              }),
            }));
          },
          dispatchAgenticActivity,
          // ASK-UX-MOBILE-R3 — canonical terminal-notice path for retry.
          // Same projector semantics as sendMessage: foreign/stale
          // terminals are dropped silently by the SSE handler.
          (terminalArgs) => {
            setTurnNotices((prev) => ({
              ...prev,
              [terminalArgs.messageId]: projectTurnTerminalNotice({
                messageId: terminalArgs.messageId,
                finalStatus: terminalArgs.finalStatus,
                terminalReason: terminalArgs.terminalReason,
                dev: isDevMode(),
              }),
            }));
          },
          // ASK-UX-MOBILE-R3 — optional-tool warning for retry path.
          // Same semantics as sendMessage: bound to canonical message_id,
          // dismissible, no CTA, sole presentation owner.
          (warningArgs) => {
            const warningNotice = projectOptionalToolWarning({
              messageId: warningArgs.messageId,
              message: OPTIONAL_TOOL_WARNING_MESSAGE,
            });
            if (warningNotice !== null) {
              setTurnNotices((prev) => ({
                ...prev,
                [warningArgs.messageId]: warningNotice,
              }));
            }
          },
        ),
        controller.signal,
        turnMetricsRef.current ?? undefined,
      );
    } catch (error) {
      if (isAbortError(error)) {
        setMessages((current) =>
          current.map((message) =>
            message.id === messageId
              ? {
                  ...message,
                  status: "interrupted",
                  // Restore original content so the user doesn't lose the previous answer
                  content_md: originalContentMd,
                  // ASK-TURN-LIFECYCLE R2 — drop any partial provisional
                  // preview accumulated before the abort. The canonical
                  // answer is restored to `originalContentMd`.
                  provisional_content_md: null,
                  reasoning_md: originalReasoningMd,
                  reasoning_status: originalReasoningStatus,
                }
              : message,
          ),
        );
        dispatchAgenticActivity({ type: "terminal", finalStatus: "cancelled" });
      } else {
        // ASK-UX-MOBILE-R3 — retry failure. Use the canonical projector.
        setTurnNotices((prev) => ({
          ...prev,
          [messageId]: projectSendFailureNotice({
            messageId: messageId,
            message: toUserFacingErrorMessage(error, ASK_UNAVAILABLE_MESSAGE),
          }),
        }));
        setMessages((current) =>
          current.map((message) =>
            message.id === messageId
              ? {
                  ...message,
                  status: "failed",
                  // Restore original content so the user doesn't lose the previous answer
                  content_md: originalContentMd,
                  // ASK-TURN-LIFECYCLE R2 — drop any partial provisional
                  // preview accumulated before the failure. The canonical
                  // answer is restored to `originalContentMd`.
                  provisional_content_md: null,
                  reasoning_md: originalReasoningMd,
                  reasoning_status: originalReasoningStatus,
                }
              : message,
          ),
        );
        dispatchAgenticActivity({ type: "terminal", finalStatus: "failed" });
      }
    } finally {
      if (sseAbortRef.current === controller) {
        sseAbortRef.current = null;
      }
      setAgenticActivity(() => createIdleAgenticActivityState());
      streamingAssistantIdRef.current = null;
      setSending(false);
      // ASK-TURN-LIFECYCLE R3 — composer is interactive again. Log
      // per-turn lifecycle metrics (log-safe JSON — no content or
      // secrets, only timestamps in ms relative to turn start).
      const metrics = turnMetricsRef.current;
      if (metrics !== null) {
        metrics.markComposerEnabled();
        // eslint-disable-next-line no-console
        console.info(
          "[AskTurnLifecycle] retry metrics",
          JSON.stringify(metrics.toJSON()),
        );
        turnMetricsRef.current = null;
      }
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
        aria-label="打开 Ask Claread"
        title="打开 Ask Claread"
      >
        <ClareadAiMark
          size="lg"
          className={cn("transition-transform group-hover:scale-[1.035]", readerTransitionStandard)}
        />
      </button>
    );
  }

  return (
    <aside
      aria-labelledby="ask-claread-panel-heading"
      className={cn(
        "ai-workspace-panel",
        `ai-workspace-panel--layout-${layout}`,
        `ai-workspace-panel--${presentation}`,
        `ai-workspace-panel--surface-${surface}`,
        layout === "overlay"
          ? cn(
              "fixed z-[var(--reader-z-floating-ask,40)] flex flex-col overflow-hidden rounded-xl border border-border bg-background shadow-lg",
              isFloatingSurface
                ? "inset-x-4 bottom-4 h-[min(85dvh,38rem)] pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)] md:inset-x-auto md:right-4 md:bottom-4 md:w-[min(26rem,calc(100vw-2rem))]"
                : "inset-x-3 bottom-3 max-h-[82vh] 2xl:inset-y-3 2xl:left-auto 2xl:right-3 2xl:w-[var(--reader-record-ask-panel-width)] 2xl:min-w-0 2xl:max-h-none",
            )
          : "relative flex flex-col overflow-hidden bg-background h-full w-full",
      )}
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
      <div className="ai-workspace-panel__header border-b bg-background px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2">
            <ClareadAiMark size="sm" className="shadow-none" badgeClassName="shadow-none" />
            <div className="min-w-0">
              <h2 ref={panelHeadingRef} id="ask-claread-panel-heading" tabIndex={-1} className="truncate text-[15px] font-semibold tracking-[-0.02em] text-ink outline-none">Ask Claread</h2>
              <div aria-live="polite" role="status" className="sr-only" data-testid="ai-workspace-live-announcement">{liveAnnouncement}</div>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {onChangeSurface && hasSidecarCapacity ? (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <button
                    type="button"
                    className="ai-workspace-panel__surface-trigger inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-xs font-medium text-muted-foreground hover:bg-muted/10 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lens-blue/20"
                    aria-label="选择 Ask Claread 面板形式"
                    title="选择面板形式"
                  >
                    {isFloatingSurface ? (
                      <PictureInPicture2 aria-hidden="true" className="h-3.5 w-3.5" />
                    ) : (
                      <PanelRightOpen aria-hidden="true" className="h-3.5 w-3.5" />
                    )}
                    <span>{isFloatingSurface ? "浮窗" : "侧边栏"}</span>
                    <ChevronDown aria-hidden="true" className="h-3 w-3 opacity-70" />
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" sideOffset={6} className="min-w-36">
                  {([
                    ["sidecar", "侧边栏", PanelRightOpen],
                    ["floating", "浮窗", PictureInPicture2],
                  ] as const).map(([nextSurface, label, Icon]) => (
                    <DropdownMenuItem
                      key={nextSurface}
                      disabled={surface === nextSurface}
                      onSelect={() => {
                        if (surface === nextSurface) {
                          return;
                        }
                        explicitSurfaceSwitchRef.current = nextSurface;
                        onChangeSurface(nextSurface);
                      }}
                    >
                      <Icon aria-hidden="true" className="h-4 w-4" />
                      <span className="flex-1">{label}</span>
                      {surface === nextSurface ? <Check aria-hidden="true" className="h-4 w-4 text-ink" /> : null}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuContent>
              </DropdownMenu>
            ) : onChangeSurface && !hasSidecarCapacity ? (
              <span
                aria-label="当前以浮窗展示 Ask Claread"
                className="inline-flex h-7 cursor-default items-center gap-1.5 rounded-md px-2 text-xs font-medium text-muted-foreground/70"
                title="当前阅读区较窄，仅支持浮窗形式"
              >
                <PictureInPicture2 aria-hidden="true" className="h-3.5 w-3.5" />
                <span>浮窗</span>
              </span>
            ) : null}
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
            <IconButton
              variant="quiet"
              size="sm"
              onClick={onToggle}
              aria-label={isFloatingSurface ? "关闭 Ask Claread" : "收起 Ask Claread"}
            >
              <X aria-hidden="true" className="h-4 w-4" />
            </IconButton>
          </div>
        </div>
      </div>

      {capacityDowngradeNotice ? (
        <div
          data-testid="ask-capacity-downgrade-notice"
          className="flex items-start gap-2 border-b border-hairline/60 bg-surface/60 px-4 py-2 text-[12px] leading-4 text-muted-foreground"
          role="status"
        >
          <span className="flex-1">{capacityDowngradeNotice}</span>
          {onDismissCapacityDowngradeNotice ? (
            <button
              type="button"
              onClick={onDismissCapacityDowngradeNotice}
              aria-label="关闭说明"
              className="shrink-0 rounded p-0.5 text-muted-foreground/70 transition-colors hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lens-blue/20"
            >
              <X aria-hidden="true" className="h-3.5 w-3.5" />
            </button>
          ) : null}
        </div>
      ) : null}

      {panelNotice ? (
        <div
          data-testid="ask-panel-notice"
          className="border-b border-hairline/60 px-4 py-2"
        >
          <SystemMessage
            fill
            variant={panelNotice.severity}
            cta={
              panelNotice.cta
                ? {
                    label: panelNotice.cta.label,
                    onClick: () => handlePanelCta(panelNotice.cta!.action),
                  }
                : undefined
            }
          >
            {panelNotice.message}
          </SystemMessage>
          {panelNotice.dismissible ? (
            <button
              type="button"
              onClick={() => setPanelNotice(null)}
              aria-label="关闭提示"
              className="mt-1 shrink-0 rounded p-0.5 text-muted-foreground/70 transition-colors hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-lens-blue/20"
            >
              <X aria-hidden="true" className="h-3.5 w-3.5" />
            </button>
          ) : null}
        </div>
      ) : null}

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
            latestUserMessageId={latestUserMessageId}
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
                agenticActivity={
                  item.role === "assistant" &&
                  item.status === "streaming" &&
                  streamingAssistantIdRef.current != null &&
                  (item.id === streamingAssistantIdRef.current ||
                    agenticActivity.messageId === item.id)
                    ? agenticActivity
                    : null
                }
                onNavigateAgenticSource={onNavigateAgenticSource}
                onAnnounce={setLiveAnnouncement}
                turnNotice={turnNotices[item.id] ?? null}
                onDismissTurnNotice={handleDismissTurnNotice}
              />
            ))}
          </ConversationShell>
        )}
      </div>

      <AskProvenanceLine summary={provenanceSummary} details={provenanceDetails} />

      <AskComposer
        onSubmit={handleSend}
        sending={sending}
        onStop={handleStop}
        placeholder={COMPOSER_PLACEHOLDER}
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
        // ASK-WEB-G1-R2: gate the Search toggle by the server-declared
        // capability for the current model option, not by page scope alone.
        // ``isReadingRecordScope`` is a page condition; the actual
        // capability is declared by the host via the model option's
        // ``web_search_capability`` field. When the host has not declared
        // the capability (or no model option is selected), both props are
        // undefined so AskComposer hides the toggle entirely (no no-op
        // control per product rule). When sending, AskComposer disables
        // the toggle independently — we do not duplicate that here.
        webSearchMode={
          isReadingRecordScope && webSearchCapabilityAvailable
            ? webSearchMode
            : undefined
        }
        onWebSearchModeChange={
          isReadingRecordScope && webSearchCapabilityAvailable
            ? setWebSearchMode
            : undefined
        }
      />
    </aside>
  );
}
