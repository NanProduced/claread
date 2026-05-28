"use client";

import {
  ArrowUp,
  BookPlus,
  ChevronDown,
  Copy,
  FileText,
  GitBranch,
  LoaderCircle,
  MessageSquare,
  PencilLine,
  Plus,
  Quote,
  RotateCcw,
  Search,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { Components } from "react-markdown";
import { Button } from "@/components/ui/button";
import {
  ChatContainerContent,
  ChatContainerRoot,
  ChatContainerScrollAnchor,
} from "@/components/ui/chat-container";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Loader } from "@/components/ui/loader";
import { Markdown } from "@/components/ui/markdown";
import { Message as ChatMessage, MessageContent } from "@/components/ui/message";
import {
  PromptInput,
  PromptInputActions,
  PromptInputTextarea,
} from "@/components/ui/prompt-input";
import { Reasoning, ReasoningContent, ReasoningTrigger } from "@/components/ui/reasoning";
import { Tool, type ToolPart } from "@/components/ui/tool";
import { IconButton } from "@/components/primitives/icon-button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/primitives/popover";
import {
  readerCommandControl,
  readerPanelItem,
  readerTransitionStandard,
} from "@/components/reader/interaction";
import { SentenceEntryCard } from "@/components/reader/SentenceEntryCard";
import { cn } from "@/lib/cn";
import {
  askAttachmentFromAnchor,
  askAttachmentFromDto,
  askAttachmentKey,
  askAttachmentLabel,
  citationCanJump,
  type ReaderAskAttachment,
  type ReaderAskPageIdentity,
} from "@/lib/reader-plate";
import type {
  ReaderAskActionConfirmResponseDto,
  ReaderAskActionProposalDto,
  ReaderAskAttachmentDto,
  ReaderAskAssetDisambiguationCandidateDto,
  ReaderAskAssetDisambiguationDto,
  ReaderAskCitationDto,
  ReaderAskCompletedPayloadDto,
  ReaderAskContextPlanDto,
  ReaderAskContextRecordItemDto,
  ReaderAskContextRecordSearchResponseDto,
  ReaderAskDeleteSupplementResponseDto,
  ReaderAskDisambiguationDto,
  ReaderAskEntryActionDto,
  ReaderAskEvidenceItemDto,
  ReaderAskMessageDto,
  ReaderAskMessageStreamRequestDto,
  ReaderAskPageIdentityDto,
  ReaderAskPersistedSupplementDto,
  ReaderAskResolvedContextInputDto,
  ReaderAskResolvedContextSummaryDto,
  ReaderAskResponseCardDto,
  ReaderAskSupplementCandidateDto,
  ReaderAskStreamEnvelopeDto,
  ReaderAskTraceSummaryDto,
  ReaderAskThreadDetailDto,
  ReaderAskThreadSummaryDto,
  ReaderAskToolTraceEntryDto,
} from "@/types/api/reader-ask";
import type { SentenceEntryModel } from "@/types/view/ReaderMockVm";
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
const workspaceDisclosureTriggerClassName = cn(
  readerPanelItem,
  "w-full justify-between rounded-[14px] px-3.5 py-3 text-left",
);
const workspaceRelatedRecordItemClassName = cn(
  readerPanelItem,
  "w-full justify-between rounded-[12px] px-2.5 py-2 text-left",
);
const workspaceCitationButtonClassName = cn(
  readerPanelItem,
  "w-full rounded-[14px] bg-surface/60 dark:bg-surface/40 px-3 py-2.5 text-left",
);
const workspaceMessageActionClassName = cn(
  "inline-flex h-7 w-7 items-center justify-center rounded-md text-muted/68 transition-[color,opacity] duration-[var(--cl-duration-fast)] ease-[var(--cl-ease-standard)]",
  "hover:text-ink active:text-ink focus-visible:text-ink",
  "[&_svg]:stroke-[1.9] hover:[&_svg]:stroke-[2.35] focus-visible:[&_svg]:stroke-[2.35]",
);
const workspaceRoundPanelActionClassName = cn(readerPanelItem, "h-8 w-8 rounded-full");
const workspaceSendButtonClassName = cn(
  readerCommandControl,
  "h-8 w-8 rounded-full bg-ink text-surface hover:bg-ink/92 active:bg-ink/85 disabled:opacity-30",
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
  | "tool_trace";

type AskPanelBlock = {
  kind: AskPanelBlockKind;
};

type AskPanelConversationItem = {
  id: string;
  role: ReaderAskMessageDto["role"];
  status: ReaderAskMessageDto["status"];
  message: ReaderAskMessageDto;
  blocks: AskPanelBlock[];
};

type AskComposerDockState = {
  canSend: boolean;
  sending: boolean;
};

type ReaderAskQuickActionRequest = {
  content: string;
  entryAction: ReaderAskEntryActionDto;
  attachments: ReaderAskAttachment[];
};

const ASK_MARKDOWN_COMPONENTS: Partial<Components> = {
  h2: ({ children }) => (
    <h2 className="mt-7 text-[1rem] font-semibold leading-7 tracking-[-0.02em] text-ink first:mt-0">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 className="mt-4 text-[0.95rem] font-semibold leading-6 text-ink-soft first:mt-0">
      {children}
    </h3>
  ),
  p: ({ children }) => <p className="my-0 text-[14.5px] leading-[1.76] text-ink-soft">{children}</p>,
  ul: ({ children }) => (
    <ul className="my-2.5 space-y-2 pl-4 text-[14.5px] leading-[1.72] text-ink-soft marker:text-[0.9em] marker:text-muted">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="my-2.5 space-y-2 pl-4 text-[14.5px] leading-[1.72] text-ink-soft marker:font-medium marker:text-muted">
      {children}
    </ol>
  ),
  li: ({ children }) => (
    <li className="[&>p]:my-0 [&>p+p]:mt-1.5 [&>ul]:mt-2 [&>ol]:mt-2">
      {children}
    </li>
  ),
  blockquote: ({ children }) => (
    <blockquote className="my-2.5 text-[13.5px] leading-[1.68] text-muted">
      {children}
    </blockquote>
  ),
  table: ({ children }) => (
    <div className="my-4 overflow-x-auto rounded-[16px] border border-hairline/70 bg-surface/78 dark:bg-surface/30">
      <table className="min-w-full border-collapse text-left text-[13px] leading-6 text-ink-soft">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border-b border-hairline/70 bg-reader-paper/90 dark:bg-[#2a2f35]/90 px-3 py-2.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted">
      {children}
    </th>
  ),
  td: ({ children }) => <td className="border-b border-hairline/50 px-3 py-2.5 align-top text-ink-soft">{children}</td>,
  hr: () => <hr className="my-5 border-0 border-t border-dashed border-hairline/80" />,
  strong: ({ children }) => <strong className="font-semibold text-ink">{children}</strong>,
  a: ({ href, children }) => (
    <a
      href={href}
      target={href?.startsWith("#") ? undefined : "_blank"}
      rel={href?.startsWith("#") ? undefined : "noreferrer"}
      className="font-medium text-context-blue underline decoration-context-blue/35 underline-offset-[0.18em] transition-colors hover:text-ink"
    >
      {children}
    </a>
  ),
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
  message: ReaderAskMessageDto | null | undefined,
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
    created_at: detail.created_at,
    updated_at: detail.updated_at,
    last_message_at: detail.last_message_at,
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

type MessageUpdater = ( updater: (messages: ReaderAskMessageDto[]) => ReaderAskMessageDto[] ) => void;

export function createSseMessageHandler(
  initialMessageId: string,
  updateMessage: MessageUpdater,
  onMessageIdAssigned: ((assignedId: string) => void) | undefined,
  onError: (message: string) => void,
) {
  let currentMessageId = initialMessageId;

  return function handleSseEvent(event: ReaderAskStreamEnvelopeDto) {
    if (event.event === "message.started") {
      const messageId = String((event.data as { message_id?: unknown }).message_id ?? currentMessageId);
      currentMessageId = messageId;
      onMessageIdAssigned?.(messageId);
      return;
    }

    if (event.event === "message.delta") {
      const delta = String((event.data as { delta?: unknown }).delta ?? "");
      updateMessage((messages) =>
        messages.map((message) =>
          message.id === currentMessageId
            ? {
                ...message,
                content_md: message.regenerate_preview ? delta : `${message.content_md}${delta}`,
                regenerate_preview: false,
              }
            : message,
        ),
      );
      return;
    }

    if (event.event === "reasoning.started") {
      updateMessage((messages) =>
        messages.map((message) =>
          message.id === currentMessageId
            ? { ...message, reasoning_status: "streaming", reasoning_md: message.reasoning_md ?? "" }
            : message,
        ),
      );
      return;
    }

    if (event.event === "reasoning.delta") {
      const delta = String((event.data as { delta?: unknown }).delta ?? "");
      updateMessage((messages) =>
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
      updateMessage((messages) =>
        messages.map((message) =>
          message.id === currentMessageId
            ? { ...message, reasoning_status: "completed" }
            : message,
        ),
      );
      return;
    }

    if (event.event === "tool.started" || event.event === "tool.completed" || event.event === "tool.failed") {
      updateMessage((messages) =>
        messages.map((message) =>
          message.id === currentMessageId
            ? { ...message, tool_trace: syncToolTrace(message.tool_trace, event) }
            : message,
        ),
      );
      return;
    }

    if (event.event === "replan.started") {
      updateMessage((messages) =>
        messages.map((message) =>
          message.id === currentMessageId
            ? { ...message, replan_status: "replanning" }
            : message,
        ),
      );
      return;
    }

    if (event.event === "message.completed") {
      const payload = event.data as unknown as ReaderAskCompletedPayloadDto;
      // Update currentMessageId to the server-assigned id
      if (payload.id) {
        currentMessageId = payload.id;
      }
      updateMessage((messages) => {
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
              reasoning_md: payload.reasoning_md ?? message.reasoning_md ?? null,
              reasoning_status: payload.reasoning_status ?? (message.reasoning_md ? "completed" : null),
              replan_status: "idle",
              regenerate_preview: false,
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
      });
      return;
    }

    if (event.event === "message.interrupted") {
      const payload = event.data as { content_md?: unknown };
      updateMessage((messages) =>
        messages.map((message) =>
          message.id === currentMessageId
            ? {
                ...message,
                status: "interrupted",
                content_md: typeof payload.content_md === "string" ? payload.content_md : message.content_md,
                reasoning_status: message.reasoning_md ? "completed" : message.reasoning_status,
                regenerate_preview: false,
              }
            : message,
        ),
      );
      return;
    }

    if (event.event === "error") {
      onError(formatStreamError(event));
      updateMessage((messages) =>
        messages.map((message) =>
          message.id === currentMessageId
            ? { ...message, status: "failed" }
            : message,
        ),
      );
    }
  };
}

function toolLabel(toolName: string) {
  switch (toolName) {
    case "get_record_context":
      return "当前文章上下文";
    case "get_record_insights":
      return "解析卡片";
    case "search_user_vocabulary":
      return "生词资产";
    case "lookup_dictionary_entry":
      return "词典";
    case "run_dictionary_ai_context_explain":
      return "词典 AI";
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

function toolTraceToPart(entry: ReaderAskToolTraceEntryDto): ToolPart {
  return {
    type: toolLabel(entry.tool_name),
    state:
      entry.status === "started"
        ? "input-streaming"
        : entry.status === "completed"
          ? "output-available"
          : "output-error",
    output: entry.summary ? { summary: entry.summary } : undefined,
    errorText: entry.status === "failed" ? entry.summary ?? "工具调用失败。" : undefined,
  };
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
    <div className="flex flex-wrap gap-2">
      {attachments.map((attachment) => {
        const attachmentKey = askAttachmentKey(attachment);
        const clickable = Boolean(onJump && attachment.kind !== "record_ref");
        const preferredText =
          variant === "composer" && attachment.kind === "text_selection"
            ? attachment.selectedText?.trim() || askAttachmentLabel(attachment)
            : askAttachmentLabel(attachment);
        const displayLabel =
          variant === "history"
            ? preferredText
            : preferredText.length <= (variant === "composer" ? 44 : 56)
              ? preferredText
              : `${preferredText.slice(0, Math.max((variant === "composer" ? 44 : 56) - 1, 1)).trimEnd()}…`;
        const badgeLabel = attachment.kind === "record_ref" ? "页" : "AI";
        return (
          <span
            key={attachmentKey}
            className="inline-flex max-w-full items-center gap-2 rounded-full border border-hairline/80 bg-reader-paper/92 dark:bg-[#2a2f35]/92 px-2.5 py-1.5 text-xs font-medium text-ink-soft"
          >
            <span className="shrink-0 rounded-full bg-surface dark:bg-[#1e2227] px-1.5 py-0.5 text-[10px] font-semibold tracking-[0.02em] text-muted">
              {badgeLabel}
            </span>
            {clickable ? (
              <button
                type="button"
                className={cn(readerPanelItem, "h-auto max-w-[13rem] rounded-md px-1.5 py-0.5 text-left sm:max-w-[17rem]")}
                onClick={() => onJump?.(attachment)}
                title={preferredText}
              >
                {displayLabel}
              </button>
            ) : (
              <span className="max-w-[13rem] truncate sm:max-w-[17rem]" title={preferredText}>
                {displayLabel}
              </span>
            )}
            {removable ? (
              <button
                type="button"
                className={cn(readerPanelItem, "size-5 rounded-full")}
                onClick={() => onRemove?.(attachmentKey)}
                aria-label={`移除引用：${askAttachmentLabel(attachment)}`}
              >
                <X className="h-3 w-3" />
              </button>
            ) : null}
          </span>
        );
      })}
    </div>
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
    <span
      className="inline-flex h-8 max-w-full items-center gap-1.5 rounded-full border border-hairline/80 bg-reader-paper/92 dark:bg-[#2a2f35]/92 px-2 py-1 text-xs font-medium text-ink-soft"
      onPointerDown={(event) => {
        event.preventDefault();
      }}
    >
      <button
        type="button"
        className={cn(
          readerPanelItem,
          "inline-flex h-6 max-w-[15rem] items-center gap-1.5 rounded-full px-1.5 py-0.5 text-left sm:max-w-[19rem]",
        )}
        data-live-context-activator="true"
        onPointerDown={(event) => {
          event.preventDefault();
        }}
        onClick={() => onActivate?.()}
        title={preferredText}
      >
        <span
          aria-hidden="true"
          className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-surface dark:bg-[#1e2227] text-muted"
        >
          <Quote className="h-3 w-3" />
        </span>
        <span className="truncate">{displayLabel}</span>
      </button>
      <button
        type="button"
        className="inline-flex h-5 w-5 items-center justify-center rounded-full text-muted/72 transition-[color,opacity] duration-[var(--cl-duration-fast)] ease-[var(--cl-ease-standard)] hover:text-ink focus-visible:text-ink"
        onPointerDown={(event) => {
          event.preventDefault();
        }}
        onClick={() => onRemove?.(attachmentKey)}
        aria-label={`移除当前选区：${askAttachmentLabel(attachment)}`}
      >
        <X className="h-3 w-3" />
      </button>
    </span>
  );
}

function CurrentRecordChip({ recordTitle }: { recordTitle?: string | null }) {
  if (!recordTitle?.trim()) {
    return null;
  }

  return (
    <span className="inline-flex max-w-full items-center gap-2 rounded-full border border-hairline/80 bg-reader-paper/90 dark:bg-[#2a2f35]/90 px-2.5 py-1.5 text-xs font-medium text-ink-soft">
      <FileText className="h-3.5 w-3.5 shrink-0 text-subtle" />
      <span className="truncate" title={recordTitle}>
        {recordTitle}
      </span>
    </span>
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
    <div className="w-[17rem] rounded-[16px] border border-hairline/65 bg-surface dark:bg-[#1e2227] p-2 shadow-[0_12px_24px_rgba(17,17,17,0.07)] dark:shadow-[0_12px_24px_rgba(0,0,0,0.28)]">
      <div className="flex items-center gap-2 rounded-[12px] border border-hairline/75 bg-reader-paper/84 dark:bg-[#2a2f35]/84 px-2.5 py-1.5">
        <Search className="h-3.5 w-3.5 text-muted" />
        <input
          autoFocus
          value={search.query}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder="搜索其他文章"
          className="min-w-0 flex-1 bg-transparent text-[13px] leading-5 text-ink outline-none placeholder:text-subtle"
          disabled={disabled}
        />
        {search.loading ? <LoaderCircle className="h-3.5 w-3.5 animate-spin text-muted" /> : null}
      </div>
      <div className="mt-2">
        <p className="px-1 text-[11px] font-medium tracking-[0.01em] text-subtle">
          {showingRecent ? "最近文章" : "搜索结果"}
        </p>
        {search.items.length === 0 ? (
          <p className="px-1 pb-0.5 pt-2 text-xs leading-5 text-muted">
            {showingRecent ? "最近没有可加入的文章。" : "没有找到匹配的文章。"}
          </p>
        ) : (
          <div className="mt-1.5 space-y-1">
            {search.items.map((item) => (
              <button
                key={item.record_id}
                type="button"
                className={workspaceRelatedRecordItemClassName}
                onClick={() => onAttachRelatedRecord(item)}
                disabled={disabled}
              >
                <div className="min-w-0">
                  <p className="truncate text-[13px] font-medium text-ink">{item.title || "Untitled"}</p>
                  <p className="mt-0.5 text-[11px] leading-4 text-muted">
                    {item.updated_at ? "最近查看的文章" : "加入当前讨论"}
                  </p>
                </div>
                <BookPlus className="h-3.5 w-3.5 shrink-0 text-subtle" />
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
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

function pendingSupplementCandidates(message: ReaderAskMessageDto | null): ReaderAskSupplementCandidateDto[] {
  if (!message) {
    return [];
  }
  return message.supplement_candidates.filter((candidate) => {
    const proposal = message.action_proposals.find(
      (item) => supplementCandidateIdFromProposal(item) === candidate.candidate_id,
    );
    return !proposal || proposal.status === "pending";
  });
}

function messageOperationSummary(message: ReaderAskMessageDto) {
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

function buildAssistantBlocks(message: ReaderAskMessageDto): AskPanelBlock[] {
  const blocks: AskPanelBlock[] = [];

  if (submissionModeOf(message) === "quick_action" && message.response_cards.length > 0) {
    blocks.push({ kind: "response_cards" });
  }
  blocks.push({ kind: "answer" });

  if (message.citations.length > 0) {
    blocks.push({ kind: "citations" });
  }

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

  return (
    <div className="space-y-3 rounded-[20px] border border-hairline/80 bg-reader-paper/72 dark:bg-[#1e2227]/72 px-3.5 py-3.5">
      {notice ? (
        <div className="rounded-[16px] border border-lens-blue/20 bg-lens-blue/10 px-3 py-2.5 text-[11px] text-lens-blue">
          {notice}
        </div>
      ) : null}
      {candidates.length > 0 ? (
        <div>
          <div className="mb-2 flex items-center justify-between gap-3">
            <p className="text-[11px] font-semibold text-muted">待确认补充</p>
            <span className="text-[11px] text-subtle">确认后写入当前页</span>
          </div>
          <div className="space-y-2">
            {candidates.map((candidate) => (
              <div
                key={candidate.candidate_id}
                className="rounded-[16px] border border-hairline/80 bg-surface dark:bg-[#252a30] px-3 py-3"
              >
                <div className="flex items-center justify-between gap-3">
                  <p className="text-xs font-semibold text-ink">{candidate.title}</p>
                  <span className="rounded-pill border border-hairline bg-reader-paper dark:bg-[#1e2227] px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.14em] text-muted">
                    待确认
                  </span>
                </div>
                <p className="mt-1 line-clamp-3 text-[11px] leading-5 text-muted">{candidate.content}</p>
              </div>
            ))}
          </div>
        </div>
      ) : null}
      {persistedSupplements.length > 0 ? (
        <div className="space-y-2">
          <div className="mb-2 flex items-center justify-between gap-3">
            <p className="text-[11px] font-semibold text-muted">已写入当前页</p>
            <span className="text-[11px] text-subtle">可直接移除</span>
          </div>
          {persistedSupplements.map((item) => (
            <div
              key={item.supplement_id}
              className="rounded-[16px] border border-hairline/80 bg-surface dark:bg-[#252a30] px-3 py-3"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <p className="truncate text-xs font-semibold text-ink">{item.title}</p>
                    <span className="rounded-pill border border-lens-blue/20 bg-lens-blue/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.14em] text-lens-blue">
                      已写入
                    </span>
                  </div>
                  <p className="mt-1 line-clamp-3 text-[11px] leading-5 text-muted">{item.content}</p>
                  <p className="mt-1 text-[11px] text-subtle">
                    {item.record_title || "当前文章"} · 句子 {item.sentence_id}
                  </p>
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  density="compact"
                  className="h-7 rounded-full px-2.5 text-[11px] text-muted"
                  disabled={deletingSupplementId === item.supplement_id}
                  onClick={() => onDeletePersistedSupplement(item.supplement_id)}
                >
                  {deletingSupplementId === item.supplement_id ? (
                    <LoaderCircle className="h-3 w-3 animate-spin" />
                  ) : (
                    <X className="h-3 w-3" />
                  )}
                  <span>删除</span>
                </Button>
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function DisclosureSection({
  label,
  summary,
  children,
  defaultOpen = false,
}: {
  label: string;
  summary?: string | null;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <div className="rounded-[18px] border border-hairline/70 bg-reader-paper/56 dark:bg-[#1e2227]/56">
        <CollapsibleTrigger asChild>
          <button
            type="button"
            className={workspaceDisclosureTriggerClassName}
          >
            <div className="min-w-0">
              <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted">{label}</p>
              {summary ? <p className="mt-1 truncate text-[11px] leading-5 text-subtle">{summary}</p> : null}
            </div>
            <ChevronDown
              className={cn(
                "h-4 w-4 shrink-0 text-muted transition-transform duration-200",
                open && "rotate-180",
              )}
            />
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent className="overflow-hidden data-[state=closed]:animate-collapsible-up data-[state=open]:animate-collapsible-down">
          <div className="border-t border-hairline/60 px-3.5 py-3">{children}</div>
        </CollapsibleContent>
      </div>
    </Collapsible>
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
    <DisclosureSection label="依据与上下文" summary={chips.join(" · ")}>
      <div className="space-y-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-subtle">当前文章</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {chips
              .filter((chip) => !chip.startsWith("外部文章") && !chip.startsWith("外部资产"))
              .map((chip) => (
                <span
                  key={chip}
                  className="rounded-pill border border-hairline bg-surface dark:bg-[#252a30] px-2.5 py-1 text-[11px] font-medium text-muted"
                >
                  {chip}
                </span>
              ))}
            {currentRecordContext?.record_title ? (
              <span className="rounded-pill border border-hairline bg-reader-paper dark:bg-[#2a2f35] px-2.5 py-1 text-[11px] font-medium text-ink-soft">
                {currentRecordContext.record_title}
              </span>
            ) : null}
          </div>
          {currentRecordContext?.article_overview || currentRecordContext?.article_overview_status ? (
            <div className="mt-2 rounded-note border border-hairline bg-surface dark:bg-[#252a30] px-3 py-2.5">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="rounded-pill border border-hairline bg-reader-paper dark:bg-[#2a2f35] px-2 py-0.5 text-[10px] font-medium text-muted">
                  {overviewStatusLabel(currentRecordContext.article_overview_status) || "概览状态未知"}
                </span>
                {currentRecordContext.article_overview_source ? (
                  <span className="rounded-pill border border-hairline bg-reader-paper dark:bg-[#2a2f35] px-2 py-0.5 text-[10px] font-medium text-muted">
                    {overviewSourceLabel(currentRecordContext.article_overview_source)}
                  </span>
                ) : null}
                {currentRecordContext.article_overview_confidence ? (
                  <span className="rounded-pill border border-hairline bg-reader-paper dark:bg-[#2a2f35] px-2 py-0.5 text-[10px] font-medium text-muted">
                    置信度 {currentRecordContext.article_overview_confidence}
                  </span>
                ) : null}
              </div>
              {currentRecordContext.article_overview ? (
                <p className="mt-2 text-[11px] leading-5 text-muted">{currentRecordContext.article_overview}</p>
              ) : null}
            </div>
          ) : null}
          {currentRecordContext?.record_insights.length ? (
            <p className="mt-2 text-[11px] leading-5 text-muted">
              已并入 {currentRecordContext.record_insights.length} 条当前文章的稳定解析。
            </p>
          ) : null}
        </div>
        {externalRecordContexts.length > 0 ? (
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-subtle">外部文章</p>
            <div className="mt-2 space-y-2">
              {externalRecordContexts.map((item) => (
                <div
                  key={item.record_id}
                  className="rounded-note border border-hairline bg-surface dark:bg-[#252a30] px-3 py-2.5"
                >
                  <div className="flex items-center justify-between gap-3">
                    <p className="truncate text-xs font-semibold text-ink">
                      {item.record_title || item.record_id}
                    </p>
                    <span className="rounded-pill border border-hairline bg-reader-paper dark:bg-[#2a2f35] px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.14em] text-muted">
                      {item.reason === "known_reference_resolved" ? "自动命中" : "显式加入"}
                    </span>
                  </div>
                  <p className="mt-1 text-[11px] leading-5 text-muted">
                    {item.article_overview
                      ? "已并入文章概览。"
                      : item.record_insights.length > 0
                        ? "已并入记录级稳定解析资产。"
                        : "已定位到文章，但当前没有可用概览。"}
                  </p>
                  {(item.article_overview_status || item.article_overview_source || item.article_overview_confidence) ? (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {item.article_overview_status ? (
                        <span className="rounded-pill border border-hairline bg-reader-paper dark:bg-[#2a2f35] px-2 py-0.5 text-[10px] font-medium text-muted">
                          {overviewStatusLabel(item.article_overview_status) || item.article_overview_status}
                        </span>
                      ) : null}
                      {item.article_overview_source ? (
                        <span className="rounded-pill border border-hairline bg-reader-paper dark:bg-[#2a2f35] px-2 py-0.5 text-[10px] font-medium text-muted">
                          {overviewSourceLabel(item.article_overview_source)}
                        </span>
                      ) : null}
                      {item.article_overview_confidence ? (
                        <span className="rounded-pill border border-hairline bg-reader-paper dark:bg-[#2a2f35] px-2 py-0.5 text-[10px] font-medium text-muted">
                          置信度 {item.article_overview_confidence}
                        </span>
                      ) : null}
                    </div>
                  ) : null}
                  {item.article_overview ? (
                    <p className="mt-2 line-clamp-3 text-[11px] leading-5 text-muted">{item.article_overview}</p>
                  ) : null}
                  {item.record_insights.length > 0 ? (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {item.record_insights.slice(0, 2).map((insight) => (
                        <span
                          key={insight}
                          className="rounded-pill border border-hairline bg-reader-paper dark:bg-[#2a2f35] px-2 py-0.5 text-[10px] font-medium text-muted"
                        >
                          {insight}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          </div>
        ) : null}
        {externalAssetContexts.length > 0 ? (
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-subtle">外部资产</p>
            <div className="mt-2 space-y-2">
              {externalAssetContexts.map((item) => (
                <div
                  key={`${item.record_id}:${item.asset_type}:${item.asset_id}`}
                  className="rounded-note border border-hairline bg-surface dark:bg-[#252a30] px-3 py-2.5"
                >
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate text-xs font-semibold text-ink">
                        {item.asset_title || item.asset_id}
                      </p>
                      <p className="mt-1 text-[11px] text-subtle">
                        {(item.record_title || item.record_id)} · {item.asset_type === "supplement" ? "AI 补充" : "稳定分析"}
                      </p>
                    </div>
                    <span className="rounded-pill border border-hairline bg-reader-paper dark:bg-[#2a2f35] px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.14em] text-muted">
                      {item.reason === "explicit_attachment" ? "显式加入" : "自动命中"}
                    </span>
                  </div>
                  {item.content_summary ? (
                    <p className="mt-1 text-[11px] leading-5 text-muted">{item.content_summary}</p>
                  ) : null}
                  {!item.content_summary && item.content_md ? (
                    <p className="mt-1 line-clamp-3 text-[11px] leading-5 text-muted">{item.content_md}</p>
                  ) : null}
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </DisclosureSection>
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
    <DisclosureSection label="证据" summary={`${evidence.length} 条显式依据`}>
      <div className="space-y-2">
        {evidence.map((item, index) => (
          <div
            key={`${item.kind}-${item.record_id ?? "local"}-${item.target_key ?? index}`}
            className="rounded-note border border-hairline bg-surface dark:bg-[#252a30] px-3 py-2.5"
          >
            <div className="flex items-center justify-between gap-3">
              <p className="truncate text-xs font-semibold text-ink">{item.label}</p>
              <div className="flex shrink-0 items-center gap-1.5">
                <span className="rounded-pill border border-hairline bg-reader-paper dark:bg-[#2a2f35] px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.14em] text-muted">
                  {item.scope === "external_record" ? "外部" : "当前"}
                </span>
                <span className="rounded-pill border border-hairline bg-reader-paper dark:bg-[#2a2f35] px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.14em] text-muted">
                  {item.kind}
                </span>
              </div>
            </div>
            {item.detail ? <p className="mt-1.5 text-[11px] leading-5 text-muted">{item.detail}</p> : null}
            {item.record_title || item.source_article_title || item.reason ? (
              <p className="mt-1 text-[11px] text-subtle">
                {[item.record_title || item.source_article_title, item.reason].filter(Boolean).join(" · ")}
              </p>
            ) : null}
          </div>
        ))}
      </div>
    </DisclosureSection>
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
    <div className="rounded-[20px] border border-hairline/80 bg-reader-paper/72 dark:bg-[#1e2227]/72 px-3.5 py-3.5">
      <div className="mb-2">
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-subtle">候选文章</p>
        <p className="mt-1 text-[11px] leading-5 text-muted">
          {disambiguation.reason || "当前引用命中了多个候选，请明确指定要并入哪篇文章。"}
        </p>
      </div>
      <div className="space-y-2">
        {disambiguation.candidates.map((candidate) => (
          <div
            key={candidate.record_id}
            className="rounded-[16px] border border-hairline/80 bg-surface dark:bg-[#252a30] px-3 py-3"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="truncate text-xs font-semibold text-ink">
                  {candidate.title || candidate.record_id}
                </p>
                <p className="mt-1 text-[11px] text-subtle">
                  我的文章 · {formatDisambiguationUpdatedAt(candidate.updated_at)}
                </p>
              </div>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                density="compact"
                className="h-7 shrink-0 rounded-full px-2.5 text-[11px]"
                onClick={() => onSelectCandidate(candidate)}
              >
                加入当前讨论
              </Button>
            </div>
          </div>
        ))}
      </div>
    </div>
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
    <div className="rounded-[20px] border border-hairline/80 bg-reader-paper/72 dark:bg-[#1e2227]/72 px-3.5 py-3.5">
      <div className="mb-2">
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-subtle">候选资产</p>
        <p className="mt-1 text-[11px] leading-5 text-muted">
          {assetDisambiguation.reason || "当前外部文章里命中了多个稳定资产，请先指定要并入哪一个。"}
        </p>
      </div>
      <div className="space-y-2">
        {assetDisambiguation.candidates.map((candidate) => (
          <div
            key={`${candidate.asset_type}:${candidate.asset_id}`}
            className="rounded-[16px] border border-hairline/80 bg-surface dark:bg-[#252a30] px-3 py-3"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="truncate text-xs font-semibold text-ink">
                  {candidate.title || candidate.asset_id}
                </p>
                <p className="mt-1 text-[11px] text-subtle">
                  {(assetDisambiguation.record_title || "我的文章")} · {candidate.asset_type === "supplement" ? "AI 补充" : "稳定分析"}
                </p>
                {candidate.summary ? (
                  <p className="mt-1 text-[11px] leading-5 text-muted">{candidate.summary}</p>
                ) : null}
              </div>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                density="compact"
                className="h-7 shrink-0 rounded-full px-2.5 text-[11px]"
                onClick={() => onSelectCandidate(candidate, assetDisambiguation)}
              >
                加入当前讨论
              </Button>
            </div>
          </div>
        ))}
      </div>
    </div>
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
    <DisclosureSection label="运行轨迹" summary={summary}>
      <div className="space-y-3 text-xs text-muted">
        <div className="flex flex-wrap gap-2">
          <span className="rounded-pill border border-hairline bg-surface dark:bg-[#252a30] px-2.5 py-1 text-[11px] font-medium text-muted">
            {plannerModeLabel(traceSummary.planner_mode)}
          </span>
          <span className="rounded-pill border border-hairline bg-surface dark:bg-[#252a30] px-2.5 py-1 text-[11px] font-medium text-muted">
            {workingSetModeLabel(traceSummary.working_set_mode)}
          </span>
          {traceSummary.reference_resolution_status !== "not_needed" ? (
            <span className="rounded-pill border border-hairline bg-surface dark:bg-[#252a30] px-2.5 py-1 text-[11px] font-medium text-muted">
              引用解析 · {traceSummary.reference_resolution_status}
            </span>
          ) : null}
        </div>
        {traceSummary.notes.length > 0 ? (
          <div className="space-y-1.5">
            {traceSummary.notes.map((note, index) => (
              <p key={index} className="leading-5">
                {note}
              </p>
            ))}
          </div>
        ) : null}
        {traceSummary.tool_steps.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {traceSummary.tool_steps.map((step) => (
              <span
                key={step}
                className="rounded-pill border border-hairline bg-surface dark:bg-[#252a30] px-2.5 py-1 text-[11px] font-medium text-muted"
              >
                {toolLabel(step)}
              </span>
            ))}
          </div>
        ) : null}
      </div>
    </DisclosureSection>
  );
}

function ResponseCards({ cards }: { cards: ReaderAskResponseCardDto[] }) {
  if (cards.length === 0) {
    return null;
  }

  return (
    <div className="mt-3 space-y-3">
      {cards.map((card, index) => {
        if (card.card_type === "grammar_note_card") {
          const entry: SentenceEntryModel = {
            id: `ask-grammar-${index}`,
            sentenceId: `ask-grammar-${index}`,
            entryType: "grammar_note",
            label: card.label,
            title: card.label,
            content: card.note_zh,
            sourceKind: "ask_supplement",
          };
          const focusHint =
            card.analysis_scope === "focus_span" && card.focus_text.trim() && card.focus_text.trim() !== card.sentence_text.trim()
              ? `聚焦片段 · ${card.focus_text}`
              : "锚定本句";
          return (
            <div key={`${card.card_type}-${index}`} className="space-y-2">
              <SentenceEntryCard
                entry={entry}
                badgeLabel="AI 助手生成"
                footerAnchorLabel={focusHint}
                footerSourceLabel="来源: Ask Claread"
              />
              {card.spans.length > 0 ? (
                <div className="rounded-note border border-hairline bg-surface dark:bg-[#252a30] px-3 py-2.5 text-xs text-muted">
                  <p className="font-semibold text-ink-soft">关键锚点</p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {card.spans.map((span, spanIndex) => (
                      <span
                        key={`${span.text}-${spanIndex}`}
                        className="rounded-pill border border-hairline bg-reader-paper dark:bg-[#2a2f35] px-2.5 py-1"
                      >
                        {span.role ? `${span.role} · ` : ""}
                        {span.text}
                      </span>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
          );
        }

        if (card.card_type === "sentence_breakdown_card") {
          return (
            <div key={`${card.card_type}-${index}`} className="rounded-note border border-hairline bg-reader-paper dark:bg-[#1e2227] px-4 py-3">
              <div className="flex items-center justify-between gap-3">
                <p className="text-xs font-semibold uppercase tracking-[0.16em] text-muted">拆句卡</p>
                <span className="rounded-pill border border-hairline bg-surface dark:bg-[#252a30] px-2 py-0.5 text-[10px] font-medium text-muted">
                  AI 助手生成
                </span>
              </div>
              <p className="mt-2 text-sm font-semibold text-ink">{card.sentence_text}</p>
              {card.translation_zh ? <p className="mt-2 text-xs leading-5 text-muted">{card.translation_zh}</p> : null}
              {card.main_clause ? (
                <p className="mt-3 text-xs font-medium text-ink-soft">
                  主线：
                  <span className="ml-1 text-ink">{card.main_clause}</span>
                </p>
              ) : null}
              {card.parts.length > 0 ? (
                <div className="mt-3 space-y-2">
                  {card.parts.map((part, partIndex) => (
                    <div key={`${part.label}-${partIndex}`} className="rounded-note border border-hairline bg-surface dark:bg-[#252a30] px-3 py-2">
                      <p className="text-xs font-semibold text-ink">{part.label}</p>
                      <p className="mt-1 text-sm text-ink-soft">{part.text}</p>
                      {part.note ? <p className="mt-1 text-xs text-muted">{part.note}</p> : null}
                    </div>
                  ))}
                </div>
              ) : null}
              {card.analysis_zh ? <p className="mt-3 text-xs leading-5 text-muted">{card.analysis_zh}</p> : null}
            </div>
          );
        }

        if (card.card_type === "vocabulary_in_context_card") {
          return (
            <div key={`${card.card_type}-${index}`} className="rounded-note border border-hairline bg-reader-paper dark:bg-[#1e2227] px-4 py-3">
              <p className="text-xs font-semibold uppercase tracking-[0.16em] text-muted">词义卡</p>
              <div className="mt-2 flex items-end justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold text-ink">{card.display_word || card.query}</p>
                  {card.phonetic ? <p className="mt-1 text-xs text-muted">/{card.phonetic}/</p> : null}
                </div>
                <span className="rounded-pill border border-hairline bg-surface dark:bg-[#252a30] px-2 py-0.5 text-[11px] font-medium text-muted">
                  当前语境
                </span>
              </div>
              {card.meaning_zh ? <p className="mt-3 text-sm text-ink-soft">{card.meaning_zh}</p> : null}
              {card.why_here ? <p className="mt-2 text-xs leading-5 text-muted">{card.why_here}</p> : null}
              {card.translation_zh ? <p className="mt-2 text-xs text-ink-soft">译法：{card.translation_zh}</p> : null}
              {card.learning_tip ? <p className="mt-2 text-xs text-muted">提示：{card.learning_tip}</p> : null}
              {card.source_sentence ? <p className="mt-3 line-clamp-2 text-xs text-muted">{card.source_sentence}</p> : null}
            </div>
          );
        }

        return (
          <div key={`${card.card_type}-${index}`} className="rounded-note border border-hairline bg-reader-paper dark:bg-[#1e2227] px-4 py-3">
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-muted">练习卡</p>
            <p className="mt-2 text-sm font-semibold text-ink">{card.title}</p>
            <div className="mt-3 rounded-note border border-hairline bg-surface dark:bg-[#252a30] px-3 py-3 text-sm leading-6 text-ink-soft">
              <Markdown components={ASK_MARKDOWN_COMPONENTS} className="space-y-3 text-ink-soft">
                {card.prompt}
              </Markdown>
            </div>
            {card.expected_focus ? <p className="mt-3 text-xs text-ink-soft">关注点：{card.expected_focus}</p> : null}
            {card.hints.length > 0 ? (
              <div className="mt-3 space-y-1">
                {card.hints.map((hint, hintIndex) => (
                  <p key={hintIndex} className="text-xs text-muted">
                    {hint}
                  </p>
                ))}
              </div>
            ) : null}
            {card.answer_guidance ? <p className="mt-3 text-xs text-muted">{card.answer_guidance}</p> : null}
          </div>
        );
      })}
    </div>
  );
}

function CitationList({
  citations,
  currentRecordId,
  onJumpToCitation,
}: {
  citations: ReaderAskCitationDto[];
  currentRecordId: string;
  onJumpToCitation?: (citation: ReaderAskCitationDto) => void;
}) {
  if (citations.length === 0) {
    return null;
  }

  return (
    <div className="space-y-2">
      <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-subtle">引用</p>
      <div className="flex flex-col gap-2">
        {citations.map((citation) => {
          const canJump = citationCanJump(citation, currentRecordId);
          const sourceLabel =
            citation.source_article_title ||
            (citation.record_id === currentRecordId ? "当前文章" : "外部引用");
          const displayText = citation.selected_text?.trim() || citation.label.trim();

          return (
            <button
              key={citation.citation_id}
              type="button"
              disabled={!canJump}
              onClick={() => {
                if (canJump) {
                  onJumpToCitation?.(citation);
                }
              }}
              className={cn(
                workspaceCitationButtonClassName,
                !canJump && "cursor-default opacity-60 hover:border-hairline hover:bg-surface/60 dark:hover:bg-surface/40 hover:text-muted",
              )}
            >
              <div className="flex items-start justify-between gap-3">
                <p className="line-clamp-2 min-w-0 flex-1 text-xs leading-5 text-ink-soft">
                  {displayText}
                </p>
                <span className="shrink-0 rounded-full bg-reader-paper dark:bg-[#2a2f35] px-2 py-0.5 text-[11px] text-muted">
                  {sourceLabel}
                </span>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function ToolTraceBlock({ entries }: { entries: ReaderAskToolTraceEntryDto[] }) {
  if (entries.length === 0) {
    return null;
  }

  return (
    <DisclosureSection
      label="工具步骤"
      summary={`${entries.length} 个工具步骤`}
    >
      <div className="space-y-2">
        {entries.map((entry, index) => (
          <Tool
            key={`${entry.tool_name}-${index}`}
            toolPart={toolTraceToPart(entry)}
            className="mt-0 border-hairline bg-surface"
          />
        ))}
      </div>
    </DisclosureSection>
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
  return (
    <div className="rounded-[18px] border border-hairline/80 bg-reader-paper/82 dark:bg-[#1e2227]/82 px-3.5 py-3">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 rounded-full bg-lens-blue-soft p-1.5 text-lens-blue">
          <Sparkles className="h-3.5 w-3.5" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-subtle">建议动作</p>
          <p className="mt-1 text-sm font-semibold text-ink">{proposal.label}</p>
          {proposal.description ? <p className="mt-1 text-xs leading-5 text-muted">{proposal.description}</p> : null}
        </div>
      </div>
      {proposal.status === "pending" ? (
        <div className="mt-3 flex gap-2 pl-9">
          <Button type="button" variant="secondary" size="sm" density="compact" disabled={busy} onClick={() => onConfirm(true)}>
            确认
          </Button>
          <Button type="button" variant="quiet" size="sm" density="compact" disabled={busy} onClick={() => onReject(false)}>
            取消
          </Button>
        </div>
      ) : null}
    </div>
  );
}

function AssistantStreamingIndicator() {
  return (
    <div className="mb-2 px-0.5 text-[11px] leading-5 text-muted">
      <Loader variant="loading-dots" size="sm" text="正在生成解释" />
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
  const [open, setOpen] = useState(isStreaming);
  const previousStreamingRef = useRef(isStreaming);

  useEffect(() => {
    if (isStreaming && !previousStreamingRef.current) {
      setOpen(true);
    }
    if (!isStreaming && previousStreamingRef.current) {
      setOpen(false);
    }
    previousStreamingRef.current = isStreaming;
  }, [isStreaming]);

  if (!isStreaming && !hasReasoningContent) {
    return null;
  }

  return (
    <Reasoning open={open} onOpenChange={setOpen} className="mb-3">
      <ReasoningTrigger className="w-full justify-between py-0.5 text-left text-[11px] font-medium text-muted transition-colors hover:text-ink-soft">
        <span>{isStreaming ? "解释思路" : "解释思路"}</span>
      </ReasoningTrigger>
      <ReasoningContent className="pt-1.5" contentClassName="border-l border-hairline/90 pl-3 text-[12px] leading-6 text-muted">
        {hasReasoningContent ? (
          <Markdown components={ASK_MARKDOWN_COMPONENTS}>{reasoningMd ?? ""}</Markdown>
        ) : (
          <div className="py-0.5 text-[12px] leading-6 text-muted">
            <Loader variant="loading-dots" size="sm" text="正在梳理解释思路" />
          </div>
        )}
      </ReasoningContent>
    </Reasoning>
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
  onJumpToCitation,
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
  onJumpToCitation?: (citation: ReaderAskCitationDto) => void;
}) {
  const { message, blocks } = item;
  const isAssistant = message.role === "assistant";
  const historyAttachments = (message.context_anchors || []).map((anchor) => askAttachmentFromAnchor(anchor, pageIdentity).attachment);
  const clarificationText = clarificationHint(message.trace_summary, message.evidence);
  const candidateSupplements = pendingSupplementCandidates(message);
  const persistedSupplements = message.persisted_supplements.filter((entry) => entry.lifecycle_status === "persisted");
  const hasAnswerContent = Boolean(message.content_md?.trim());

  return (
    <div className={cn("flex flex-col gap-3", isAssistant ? "items-start" : "items-end")}>
      {!isAssistant && historyAttachments.length > 0 ? (
        <div className="flex w-full justify-end">
          <AttachmentChips attachments={historyAttachments} onJump={onJumpToAttachment} />
        </div>
      ) : null}
      <ChatMessage className={cn("w-full", isAssistant ? "items-start" : "justify-end")}>
        {isAssistant ? (
          <>
            <div className="group min-w-0 flex-1">
              <div className="space-y-4">
                {blocks.map((block, index) => {
                  switch (block.kind) {
                    case "answer":
                      return (
                        <div
                          key={`${message.id}-${block.kind}-${index}`}
                          className="px-1 py-1"
                        >
                          {clarificationText ? (
                            <div className="mb-2 text-[12px] leading-6 text-muted">
                              {clarificationText}
                            </div>
                          ) : null}
                          {message.replan_status === "replanning" ? (
                            <div className="mb-2 text-[12px] leading-6 text-muted">
                              正在补充上下文后重试...
                            </div>
                          ) : null}
                          <AssistantReasoningBlock reasoningMd={message.reasoning_md} reasoningStatus={message.reasoning_status} />
                          {message.status === "streaming" ? <AssistantStreamingIndicator /> : null}
                          {hasAnswerContent ? (
                            <Markdown
                              components={ASK_MARKDOWN_COMPONENTS}
                              className="border-0 bg-transparent p-0 text-[14.5px] leading-[1.8] text-ink-soft shadow-none"
                            >
                              {message.content_md}
                            </Markdown>
                          ) : null}
                          {message.status === "interrupted" ? (
                            <div className="mt-3 rounded-[14px] border border-amber-200 dark:border-amber-900/40 bg-amber-50 dark:bg-amber-950/30 px-3 py-2 text-[12px] leading-5 text-amber-900 dark:text-amber-200">
                              输出中断，可重新生成。
                            </div>
                          ) : null}
                        </div>
                      );
                    case "response_cards":
                      return <ResponseCards key={`${message.id}-${block.kind}-${index}`} cards={message.response_cards} />;
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
                      return (
                        <CitationList
                          key={`${message.id}-${block.kind}-${index}`}
                          citations={message.citations}
                          currentRecordId={currentRecordId}
                          onJumpToCitation={onJumpToCitation}
                        />
                      );
                    case "context_summary":
                      return (
                        <div key={`${message.id}-${block.kind}-${index}`} className="space-y-3">
                          <ContextSummaryDisclosure
                            summary={message.resolved_context}
                            contextInput={message.resolved_context_input}
                          />
                          {message.context_plan ? (
                            <DisclosureSection label="上下文策略" summary={contextPlanSummary(message.context_plan)}>
                              <div className="rounded-note border border-hairline bg-surface dark:bg-[#252a30] px-3 py-2.5 text-[11px] leading-5 text-muted">
                                <p className="font-semibold text-ink-soft">本轮决策</p>
                                <p className="mt-1">
                                  {message.context_plan.entry_action} · {message.context_plan.source_labels.join(" · ") || "当前文章"}
                                </p>
                                <p className="mt-1">
                                  {message.context_plan.used_article_overview ? "已使用文章概览" : "未使用文章概览"} ·
                                  {message.context_plan.used_record_context ? " 已使用正文上下文" : " 未使用正文上下文"} ·
                                  {message.context_plan.used_dictionary ? " 已查词典" : " 未查词典"}
                                </p>
                              </div>
                            </DisclosureSection>
                          ) : null}
                          <EvidenceDisclosure evidence={message.evidence} />
                          <TraceSummaryDisclosure traceSummary={message.trace_summary} />
                          <ToolTraceBlock entries={message.tool_trace} />
                        </div>
                      );
                    case "evidence":
                    case "trace_summary":
                    case "tool_trace":
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
              {(message.status === "completed" || message.status === "interrupted") && (
                <div className="mt-2 flex items-center gap-1.5 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                  <button type="button" className={workspaceMessageActionClassName} title="复制内容" aria-label="复制内容">
                    <Copy className="h-3.5 w-3.5" />
                  </button>
                  <button type="button" className={workspaceMessageActionClassName} title="有帮助" aria-label="有帮助">
                    <ThumbsUp className="h-3.5 w-3.5" />
                  </button>
                  <button type="button" className={workspaceMessageActionClassName} title="无帮助" aria-label="无帮助">
                    <ThumbsDown className="h-3.5 w-3.5" />
                  </button>
                  <button
                    type="button"
                    className={workspaceMessageActionClassName}
                    title="重新生成"
                    aria-label="重新生成"
                    onClick={() => onRetry(message.id)}
                  >
                    <RotateCcw className="h-3.5 w-3.5" />
                  </button>
                </div>
              )}
            </div>
          </>
        ) : (
          <div className="group relative flex max-w-[92%] flex-col items-end">
            {submissionModeOf(message) === "quick_action" ? (
              <div className="rounded-full border border-hairline/70 bg-reader-paper dark:bg-[#2a2f35] px-3.5 py-2 text-[12px] font-medium text-ink-soft">
                {messageOperationSummary(message)}
              </div>
            ) : (
              <MessageContent className="whitespace-pre-wrap rounded-[14px] bg-muted/10 dark:bg-[#2a2f35]/60 border border-hairline/60 px-3.5 py-1.5 text-[14px] leading-[1.6] text-ink-soft shadow-none">
                {message.content_md}
              </MessageContent>
            )}
            <div className="absolute -bottom-6 right-0 flex items-center gap-2 opacity-0 transition-opacity group-hover:opacity-100">
              <span className="text-[10px] text-muted">
                {message.created_at ? new Date(message.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ''}
              </span>
              <button className={cn(readerPanelItem, "h-6 w-6 rounded-full")} title="复制">
                <Copy className="h-3 w-3" />
              </button>
            </div>
          </div>
        )}
      </ChatMessage>
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
    <div className="flex min-h-full flex-col pb-2 pt-3">
      <div className="flex min-h-full flex-col">
        <div className="max-w-[24.5rem] space-y-4">
          <div className="relative w-fit">
            <div className="absolute inset-x-5 bottom-2 h-6 rounded-full bg-lens-blue/8 blur-2xl" />
            <div className="relative h-[124px] w-[172px] overflow-hidden rounded-[28px] border border-hairline/70 bg-[radial-gradient(circle_at_30%_18%,rgba(255,255,255,0.98),rgba(248,246,240,0.9))] dark:bg-[radial-gradient(circle_at_30%_18%,rgba(50,55,62,0.98),rgba(38,43,50,0.9))] shadow-[0_18px_40px_rgba(17,17,17,0.06)] dark:shadow-[0_18px_40px_rgba(0,0,0,0.22)]">
              <img
                src="/brand/ask-claread/empty-state-illustration.png"
                alt=""
                aria-hidden="true"
                className="h-full w-full scale-[1.08] object-cover object-center"
              />
            </div>
          </div>
          <div className="space-y-2">
            <p className="text-[26px] font-semibold tracking-[-0.04em] text-ink">{starterContent.title}</p>
            <p className="max-w-[23rem] text-[15px] leading-7 text-muted">{starterContent.description}</p>
          </div>
        </div>
        <div className="mt-auto max-w-[25rem] space-y-2.5 pt-12">
          {suggestions.map((suggestion) => (
            <button
              key={suggestion.prompt}
              type="button"
              className={cn(
                readerCommandControl,
                "group w-full justify-start gap-3 rounded-[18px] px-2.5 py-2.5 text-left hover:bg-reader-paper/75",
              )}
              onClick={() => onPickPrompt(suggestion.prompt, suggestion.entryAction)}
            >
              <span
                className={cn(
                  "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full",
                  suggestion.badgeClassName,
                )}
              >
                <suggestion.icon className={cn("h-4 w-4", suggestion.iconClassName)} />
              </span>
              <span className="text-[15px] font-medium leading-6 tracking-[-0.01em] text-ink-soft">
                {suggestion.prompt}
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

export interface AiWorkspacePanelProps {
  open: boolean;
  presentation?: "intensive" | "immersive";
  pageIdentity: ReaderAskPageIdentity;
  recordId: string;
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
  onJumpToCitation?: (citation: ReaderAskCitationDto) => void;
  onActionExecuted?: (result: ReaderAskActionConfirmResponseDto["result"]) => void;
  onSupplementDeleted?: (supplementId: string) => void | Promise<void>;
  onPendingQuickActionConsumed?: () => void;
  onActivateLiveContextSelection?: () => void;
  onComposerTextareaFocus?: () => void;
  onComposerTextareaBlur?: () => void;
  onPanelPointerDownOutsideComposer?: () => void;
  onToggle: () => void;
}

export function AiWorkspacePanel({
  attachments,
  liveContextAttachment = null,
  pageIdentity,
  pendingQuickActionRequest,
  presentation = "intensive",
  open,
  recordId,
  recordTitle,
  hideLauncherOnMobile = false,
  hideLauncherInCompactLayout = false,
  onAppendAttachments,
  onClearAttachments,
  onJumpToAttachment,
  onJumpToCitation,
  onActionExecuted,
  onActivateLiveContextSelection,
  onComposerTextareaBlur,
  onComposerTextareaFocus,
  onPanelPointerDownOutsideComposer,
  onPendingQuickActionConsumed,
  onSupplementDeleted,
  onRemoveAttachment,
  onToggle,
}: AiWorkspacePanelProps) {
  const launcherVisibilityClass = hideLauncherInCompactLayout
    ? "hidden 2xl:inline-flex"
    : hideLauncherOnMobile
      ? "hidden md:inline-flex"
      : "inline-flex";

  const [threads, setThreads] = useState<ReaderAskThreadSummaryDto[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ReaderAskMessageDto[]>([]);
  const [composer, setComposer] = useState("");
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
  const visibleContextAttachments = attachments.filter(
    (attachment) => !(attachment.kind === "record_ref" && attachment.metadata.recordId === recordId),
  );
  const composerContextAttachments = liveContextAttachment
    ? visibleContextAttachments.filter((attachment) => askAttachmentKey(attachment) !== askAttachmentKey(liveContextAttachment))
    : visibleContextAttachments;
  const composerDockState: AskComposerDockState = {
    canSend: composer.trim().length > 0 && !sending,
    sending,
  };

  async function fetchThreadList() {
    const payload = await fetchJson<{ items: ReaderAskThreadSummaryDto[] }>(
      `/api/web/reader-ask/threads?record_id=${encodeURIComponent(recordId)}`,
      undefined,
      "Ask Claread 线程列表加载失败。",
    );
    return payload.items ?? [];
  }

  async function fetchThreadDetail(threadId: string) {
    return fetchJson<ReaderAskThreadDetailDto>(
      `/api/web/reader-ask/threads/${threadId}`,
      undefined,
      "Ask Claread 加载失败。",
    );
  }

  async function fetchContextRecords(query: string) {
    return fetchJson<ReaderAskContextRecordSearchResponseDto>(
      `/api/web/reader-ask/context-records?query=${encodeURIComponent(query)}&exclude_record_id=${encodeURIComponent(recordId)}`,
      undefined,
      "上下文文章搜索失败。",
    );
  }

  async function createThread(title: string) {
    return fetchJson<ReaderAskThreadSummaryDto>(
      "/api/web/reader-ask/threads",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ record_id: recordId, title }),
      },
      "Ask Claread 初始化失败。",
    );
  }

  async function loadThread(threadId: string, nextThreads?: ReaderAskThreadSummaryDto[]) {
    const detail = await fetchThreadDetail(threadId);
    setActiveThreadId(threadId);
    setMessages(detail.messages);
    setSupplementNotice(null);
    if (nextThreads) {
      setThreads(nextThreads);
    }
  }

  useEffect(() => {
    if (!contextPickerOpen) {
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
  }, [contextPickerOpen, contextSearch.query, recordId]);

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
      submissionMode: "quick_action",
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
        `/api/web/reader-ask/threads/${activeThreadId}/reset`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
        },
        "重置会话失败。",
      );
      setActiveThreadId(detail.id);
      setMessages(detail.messages);
      setThreads([toThreadSummary(detail)]);
      setComposer("");
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
        `/api/web/reader-ask/threads/${activeThreadId}/actions/${actionId}/confirm`,
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
        `/api/web/reader-ask/supplements/${supplementId}`,
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
    const content = (options?.content ?? composer).trim();
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

    const usedAttachments = [...(options?.attachments ?? attachments)];
    const entryAction = options?.entryAction ?? defaultEntryAction();
    const submissionMode = options?.submissionMode ?? "chat";
    const now = Date.now();
    const tempUserId = `local-user-${now}`;
    const tempAssistantId = `local-assistant-${now}`;
    const userMessage: ReaderAskMessageDto = {
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
    const assistantMessage: ReaderAskMessageDto = {
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
      regenerate_preview: false,
      usage_event_id: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };

    if (options?.clearComposer !== false) {
      setComposer("");
    }
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
          ? { ...thread, last_message_at: new Date().toISOString(), updated_at: new Date().toISOString() }
          : thread,
      ),
    );

    try {
      const requestBody: ReaderAskMessageStreamRequestDto = {
        content,
        page_identity: serializePageIdentity(pageIdentity),
        attachments: usedAttachments.map(serializeAttachment),
        entry_action: entryAction,
      };
      const response = await fetch(`/api/web/reader-ask/threads/${threadId}/messages/stream`, {
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

  async function handleSend() {
    await sendMessage();
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
            }
          : message,
      ),
    );

    try {
      const response = await fetch(
        `/api/web/reader-ask/threads/${activeThreadId}/messages/${messageId}/retry/stream`,
        {
          method: "POST",
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
      className={`ai-workspace-panel ai-workspace-panel--${presentation} fixed inset-x-3 bottom-3 z-50 flex max-h-[82vh] flex-col overflow-hidden rounded-[28px] border border-hairline/85 bg-[linear-gradient(180deg,rgba(250,249,245,0.98),rgba(255,255,255,0.98))] dark:bg-[linear-gradient(180deg,rgba(30,34,39,0.98),rgba(38,43,49,0.98))] shadow-[0_26px_76px_rgba(17,17,17,0.12)] dark:shadow-[0_26px_76px_rgba(0,0,0,0.32)] 2xl:inset-y-3 2xl:left-auto 2xl:right-3 2xl:w-[clamp(31rem,calc((100vw-124px-96ch)/2-0.5rem),37.5rem)] 2xl:min-w-0 2xl:max-h-none`}
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
      <div className="border-b border-hairline/70 px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2">
            <div className="inline-flex size-7 shrink-0 items-center justify-center rounded-full border border-hairline/80 bg-surface shadow-[0_10px_22px_rgba(17,17,17,0.04)]">
              <Sparkles className="h-3.5 w-3.5 text-lens-blue" />
            </div>
            <div className="min-w-0">
              <h2 className="text-[15px] font-semibold tracking-[-0.02em] text-ink">Ask Claread</h2>
            </div>
          </div>
          <div className="flex items-center gap-1.5">
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

      <div className="min-h-0 flex-1 pb-3 pt-4">
        {loading ? (
          <div className="flex h-full items-center justify-center">
            <LoaderCircle className="h-5 w-5 animate-spin text-lens-blue" />
          </div>
        ) : (
          <ChatContainerRoot className="min-h-0 h-full w-full">
            <ChatContainerContent className={cn("px-5", messages.length === 0 ? "gap-0" : "gap-6")}>
              {messages.length === 0 ? (
                <StarterState
                  attachments={attachments}
                  onPickPrompt={(prompt, entryAction) => {
                    void sendMessage({
                      content: prompt,
                      entryAction,
                    });
                  }}
                />
              ) : null}
              {(() => {
                return conversationItems.map((item) => (
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
                  onJumpToCitation={onJumpToCitation}
                />
              ));
              })()}
              <ChatContainerScrollAnchor />
            </ChatContainerContent>
          </ChatContainerRoot>
        )}
      </div>

      <div className="bg-[rgba(250,249,245,0.98)] dark:bg-[rgba(30,34,39,0.98)] px-4 pb-4 pt-1">
        {errorMessage ? (
          <div className="mb-3 rounded-[12px] border border-destructive/20 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {errorMessage}
          </div>
        ) : null}

        <PromptInput
          value={composer}
          onValueChange={setComposer}
          onSubmit={handleSend}
          isLoading={sending}
          maxHeight={220}
          disableContainerFocus
          className="flex flex-col gap-0 rounded-[24px] border border-hairline/80 bg-surface !px-0 !py-0 shadow-[0_12px_30px_rgba(17,17,17,0.04)] transition-all focus-within:border-muted focus-within:shadow-[0_16px_34px_rgba(17,17,17,0.06)]"
        >
          {(recordTitle || composerContextAttachments.length > 0 || liveContextAttachment) && (
            <div className="flex flex-wrap items-center gap-1.5 border-b border-hairline/40 px-3 py-2">
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
            </div>
          )}

          <div className="px-3 py-2">
            <PromptInputTextarea
              placeholder={COMPOSER_PLACEHOLDER}
              className="min-h-[40px] text-[14px] leading-relaxed"
              data-ask-composer-textarea="true"
              onFocus={onComposerTextareaFocus}
              onBlur={onComposerTextareaBlur}
            />
          </div>

          <div className="flex items-center justify-between px-3 pb-2 pt-1">
            <div className="flex items-center gap-2">
              <Popover open={contextPickerOpen} onOpenChange={setContextPickerOpen}>
                <PopoverTrigger asChild>
                  <button
                    type="button"
                    className={cn(
                      workspaceRoundPanelActionClassName,
                      contextPickerOpen
                        ? "border-muted bg-reader-paper text-ink"
                        : null,
                    )}
                    disabled={sending}
                    onMouseDown={(event) => {
                      event.stopPropagation();
                    }}
                    onClick={(event) => {
                      event.stopPropagation();
                    }}
                    title="添加其他文章"
                    aria-label="添加其他文章"
                  >
                    <Plus className="h-4 w-4" />
                  </button>
                </PopoverTrigger>
                <PopoverContent side="top" align="start" className="mb-3 border-none bg-transparent p-0 shadow-none">
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
                </PopoverContent>
              </Popover>
            </div>

            <PromptInputActions>
              <button
                type="button"
                className={workspaceSendButtonClassName}
                disabled={!composerDockState.canSend}
                onClick={(event) => {
                  event.stopPropagation();
                  void handleSend();
                }}
                title="发送"
                aria-label="发送"
              >
                {composerDockState.sending ? (
                  <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <ArrowUp className="h-3.5 w-3.5" />
                )}
              </button>
            </PromptInputActions>
          </div>

        </PromptInput>
      </div>
    </aside>
  );
}
