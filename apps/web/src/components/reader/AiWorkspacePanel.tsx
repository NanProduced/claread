"use client";

import {
  Check,
  ChevronDown,
  Copy,
  FileText,
  GitBranch,
  Globe,
  MessageSquare,
  PencilLine,
  Quote,
  PanelRightOpen,
  PictureInPicture2,
  RotateCcw,
  Search,
  Sparkles,
  X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
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
  Message as AiMessage,
  MessageAction,
  MessageActions,
  MessageContent,
  MessageResponse,
} from "@/components/ai-elements/message";
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
import { AssistantMessage } from "@/components/reader/ask-chat/AssistantMessage";
import { ConversationShell } from "@/components/reader/ask-chat/ConversationShell";
import { PromptSuggestions } from "@/components/reader/ask-chat/PromptSuggestions";
import { LearnerReasoningPanel } from "@/components/reader/ask-chat/LearnerReasoningPanel";
import { TurnProcessDisclosure } from "@/components/reader/ask-chat/turn-process";
import {
  readerCommandControl,
  readerTransitionStandard,
} from "@/components/reader/interaction";
import { cn } from "@/lib/cn";
import {
  askAttachmentKey,
  askAttachmentLabel,
  type ReaderAskAttachment,
  type ReaderAskPageIdentity,
} from "@/lib/reader-plate";
import type {
  ReaderAskEntryActionDto,
  ReaderAskModelOptionListResponseDto,
  ReaderAskModelOptionSummaryDto,
  ReaderAskMessageStreamRequestDto,
  ReaderAskThreadDetailDto,
  ReaderAskThreadSummaryDto,
  ReaderAskUiMessageDto,
  WebSearchModeDto,
} from "@/types/api/reader-ask";
import {
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
  createIdleAgenticActivityState,
  reduceAgenticActivityEvent,
  type AgenticActivityOutcome,
  type AgenticActivityEvent,
  type AgenticActivityState,
} from "./ask/agentic-activity";
import { buildAgenticProcessSnapshot } from "./ask/agentic-process-projection";
import { type AskComposerContext } from "./ask/composer-context";
import {
  buildAssistantBlocks,
  createSseMessageHandler,
  normalizeReaderAskMessages,
  type AskPanelConversationItem,
} from "./ask/message-state";
import { consumeReaderAskSse } from "./ask/sse";
import { TurnLifecycleMetrics } from "./ask/turn-lifecycle";
import {
  ASK_UNAVAILABLE_MESSAGE,
  OPTIONAL_TOOL_WARNING_MESSAGE,
  PENDING_SUBMISSION_RESEND_MESSAGE,
  interruptedBubbleMessage,
  toUserFacingErrorMessage,
} from "./ask/ask-error-messages";
import {
  browserAskRetryPath,
  browserAskStreamPath,
  browserAskSubmissionPath,
  isPersistedAssistantMessageId,
} from "@/lib/reader-ask/browser-paths";
import {
  clearPendingSendKeys,
  messageMatchesActiveAssistant,
  rekeyPendingSend,
  resolveActiveAssistantId,
} from "@/lib/reader-ask/pending-send";
import {
  classifyRetryTarget,
  classifyRetryTargetForRecovery,
  type PendingSendRequest,
} from "@/lib/reader-ask/retry-target";
import {
  buildOptimisticResolvedContextInput,
  defaultEntryAction,
  serializeAttachment,
  serializePageIdentity,
} from "@/lib/reader-ask/send-request";
import {
  findModelOptionSummary,
  isKnownModelOptionKey,
  replaceThreadSummary,
  toSelectedModelSummary,
  toThreadSummary,
} from "@/lib/reader-ask/thread-summary";
import {
  projectOptionalToolWarning,
  projectPanelInitNotice,
  projectSendFailureNotice,
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

const COMPOSER_PLACEHOLDER = "继续问这篇文章…";
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
  variant = "history",
}: {
  attachments: ReaderAskAttachment[];
  removable?: boolean;
  onRemove?: (attachmentKey: string) => void;
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
            className={cn(variant === "history" && "w-full")}
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

/**
 * Permanent current-article composer chip.
 * Renders the page-authoritative record title (snapshot.record.title via
 * the `recordTitle` prop — never the thread title) as a non-removable
 * document chip, first in the strip. It visualizes the implicit "current
 * article" context only: it constructs no attachment, never enters
 * provenance, and is never injected into the model again (the article is
 * already the ambient context server-side).
 */
function CurrentArticleChip({ title }: { title: string }) {
  return (
    <Attachments variant="inline" className="max-w-full shrink-0">
      <Attachment
        data={sourceDocumentPart(
          "current-article",
          title,
          "application/vnd.claread.record",
        )}
        className="max-w-full cursor-default"
        data-ask-current-article-chip="true"
        title={`当前文章：${title}`}
        aria-label={`当前文章：${title}`}
      >
        <AttachmentPreview fallbackIcon={<FileText className="h-3 w-3 text-muted-foreground" />} />
        <AttachmentInfo className="max-w-[12rem] text-xs sm:max-w-[15rem]" />
      </Attachment>
    </Attachments>
  );
}

/**
 * Composer chip for an auto/manual selection slot. Quote icon + truncated
 * source text; each chip is independently removable via the slot callback.
 * Selection identity (dedupe/promote) is owned by the surface via the
 * anchor fingerprint — never the label.
 */
function SelectionContextChip({
  attachment,
  slot,
  onRemove,
}: {
  attachment: ReaderAskAttachment;
  slot: "auto" | "manual";
  onRemove?: (attachmentKey: string) => void;
}) {
  const attachmentKey = askAttachmentKey(attachment);
  const preferredText = attachment.selectedText?.trim() || askAttachmentLabel(attachment);
  const displayLabel =
    preferredText.length <= 44
      ? preferredText
      : `${preferredText.slice(0, 43).trimEnd()}…`;
  const slotLabel = slot === "auto" ? "自动选区" : "固定选区";

  return (
    <Attachments
      variant="inline"
      className="max-w-full shrink-0"
      onPointerDown={(event) => {
        // Protect the native selection from collapsing when interacting
        // with the strip.
        event.preventDefault();
      }}
    >
      <Attachment
        data={sourceDocumentPart(attachmentKey, displayLabel)}
        onRemove={onRemove ? () => onRemove(attachmentKey) : undefined}
        className="max-w-full"
        data-ask-selection-slot={slot}
        onPointerDown={(event) => {
          event.preventDefault();
        }}
        title={`${slotLabel}：${preferredText}`}
        aria-label={`${slotLabel}：${preferredText}`}
      >
        <AttachmentPreview fallbackIcon={<Quote className="h-3 w-3 text-muted-foreground" />} />
        <AttachmentInfo className="max-w-[12rem] text-xs sm:max-w-[15rem]" />
        {onRemove ? (
          <AttachmentRemove label={`移除${slotLabel}：${preferredText}`} />
        ) : null}
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
  // ASK-UX-HISTORY-COT-R2 P0-2: render nothing when there is no explicit
  // context (no selection, no notes). The current article is implicit
  // and must not surface as a default provenance row.
  if (!summary && !hasDetails) {
    return null;
  }
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

function MessageBubble({
  item,
  onRetry,
  onResend,
  resolveRetryTarget,
  agenticActivity,
  turnNotice,
  onDismissTurnNotice,
}: {
  item: AskPanelConversationItem;
  onRetry: (messageId: string) => void;
  onResend?: (localAssistantId: string) => void;
  /** R8: pending recovery may force resend for UUID bubbles. */
  resolveRetryTarget: (
    messageId: string,
  ) => ReturnType<typeof classifyRetryTarget>;
  agenticActivity?: AgenticActivityState | null;
  turnNotice?: AskSystemNotice | null;
  onDismissTurnNotice?: (messageId: string) => void;
}) {
  const { message, blocks } = item;
  const isAssistant = message.role === "assistant";
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
        // P1 — vertical rhythm between answer/process/sources/actions.
        // 4/8 spacing: tighter than the old space-y-4, so the process
        // disclosure sits closer to the answer it belongs to.
        <div className="min-w-0 w-full space-y-3">
          {blocks.map((block, index) => {
            switch (block.kind) {
              case "answer":
                return (
                  <AssistantMessage
                    key={`${message.id}-${block.kind}-${index}`}
                    className="px-0.5"
                    reasoning={
                      <LearnerReasoningPanel
                        text={message.learner_reasoning_text}
                        status={
                          message.status === "streaming"
                            ? message.learner_reasoning_status === "streaming"
                              ? "streaming"
                              : message.learner_reasoning_text
                                ? "streaming"
                                : null
                            : message.learner_reasoning_text
                              ? "completed"
                              : null
                        }
                      />
                    }
                    process={
                      <TurnProcessDisclosure
                        activity={
                          message.status === "streaming"
                            ? (agenticActivity ?? null)
                            : null
                        }
                        snapshot={message.agentic_process_snapshot ?? null}
                        citations={agenticCitationItems}
                        isStreaming={message.status === "streaming"}
                        webSearchSummary={message.agentic_web_search ?? null}
                        contextCompaction={message.context_compaction ?? null}
                      />
                    }
                    answer={
                      <div className="space-y-2">
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
                                        } else if (
                                          turnNotice.cta?.action === "resend" &&
                                          onResend
                                        ) {
                                          onResend(message.id);
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
                      (message.status === "interrupted" && !turnNotice) ||
                      (message.status === "failed" &&
                        resolveRetryTarget(message.id)?.kind ===
                          "pending_submission") ? (
                        <MessageActions className="gap-0.5">
                          {message.status !== "failed" ? (
                            <MessageAction
                              label="复制内容"
                              title="复制内容"
                              onClick={() => {
                                void copyMessageText(message.content_md ?? "");
                              }}
                            >
                              <Copy className="h-3.5 w-3.5" />
                            </MessageAction>
                          ) : null}
                          {isPersistedAssistantMessageId(message.id) &&
                          message.status !== "failed" &&
                          resolveRetryTarget(message.id)?.kind !==
                            "pending_submission" ? (
                            <MessageAction
                              label="重新生成"
                              title="重新生成"
                              onClick={() => onRetry(message.id)}
                            >
                              <RotateCcw className="h-3.5 w-3.5" />
                            </MessageAction>
                          ) : null}
                          {resolveRetryTarget(message.id)?.kind ===
                            "pending_submission" &&
                          message.status === "failed" &&
                          onResend ? (
                            <MessageAction
                              label="重新发送"
                              title="重新发送"
                              onClick={() => onResend(message.id)}
                            >
                              <RotateCcw className="h-3.5 w-3.5" />
                            </MessageAction>
                          ) : null}
                        </MessageActions>
                      ) : null
                    }
                  />
                );
              default:
                return null;
            }
          })}
        </div>
        ) : (
          <AiMessage from={message.role} className="w-full max-w-[31rem]">
            <MessageContent className="text-[14.5px] px-3.5 py-2.5">
              <MessageResponse className="ask-message-response whitespace-pre-wrap text-[14.5px] leading-[1.7]">
                {message.content_md}
              </MessageResponse>
            </MessageContent>
            <div className="flex items-center justify-end gap-2 pr-1 opacity-0 transition-opacity group-hover:opacity-70">
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
  webSearchCapable,
}: {
  attachments: ReaderAskAttachment[];
  onPickPrompt: (
    prompt: string,
    entryAction: ReaderAskEntryActionDto,
    webSearchOverride?: WebSearchModeDto,
  ) => void;
  /**
   * R2.1 — when true, the model's provider declares web search capability,
   * so the empty state surfaces a 4th "查询相关资料" suggestion. When
   * false, the suggestion is hidden (no no-op affordance).
   */
  webSearchCapable: boolean;
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
  // R2.1 — article-oriented suggestions. The 4th slot is conditionally
  // swapped: "查询相关资料" appears only when the selected model's provider
  // declares web search capability. Otherwise the exercise prompt stays.
  // Total stays within 2–4 per R2.1.
  const baseSuggestions = [
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
  ];
  const suggestions = webSearchCapable
    ? [
        ...baseSuggestions,
        {
          prompt: "查询这篇文章相关的其他资料。",
          entryAction: "ask_about_this" as const,
          icon: Globe,
          iconClassName: "text-context-blue",
          badgeClassName: "bg-context-blue/12",
          // R2.1 — signals the host to enable web search for this send.
          webSearchOverride: "allowed" as const,
        },
      ]
    : [
        ...baseSuggestions,
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
  recordTitle?: string | null;
  /**
   * Ask composer send-context seam. The host (plate) owns the composer
   * context (attachment draft, selection slots, quick-action queue, send
   * merge); the panel only renders and consumes it.
   */
  composer: AskComposerContext;
  onToggle: () => void;
  capacityDowngradeNotice?: string | null;
  onDismissCapacityDowngradeNotice?: () => void;
  /**
   * Reader-owned NavigateAgenticSource callback (R3C-A). Optional — callers
   * without wiring keep canonical citations display-only.
   * Must not pass CurrentPageIdentity / Document / Element here.
   */
  onNavigateAgenticSource?: NavigateAgenticSource;
  /**
   * ASK-UX-MOBILE — whether the host layout currently has room for the
   * sidecar surface. When false, the surface switch menu is replaced by a
   * static「浮窗」label so the user cannot pick an unavailable surface.
   * Defaults to true so callers that do not provide a layout measurement keep
   * the existing menu behavior.
   */
  hasSidecarCapacity?: boolean;
}

export function AiWorkspacePanel({
  layout = "overlay",
  onChangeSurface,
  composer,
  pageIdentity,
  presentation = "intensive",
  surface = "sidecar",
  open,
  recordId,
  recordTitle,
  onToggle,
  capacityDowngradeNotice,
  onDismissCapacityDowngradeNotice,
  hasSidecarCapacity = true,
}: AiWorkspacePanelProps) {
  const {
    attachments,
    autoSelectionAttachment,
    manualSelectionAttachments,
    pendingQuickActionRequest,
  } = composer;
  const isFloatingSurface = surface === "floating";
  const [liveAnnouncement, setLiveAnnouncement] = useState("");
  const panelHeadingRef = useRef<HTMLHeadingElement>(null);
  const explicitSurfaceSwitchRef = useRef<AiWorkspaceSurface | null>(null);

  const [threads, setThreads] = useState<ReaderAskThreadSummaryDto[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ReaderAskUiMessageDto[]>([]);
  const [agenticActivity, setAgenticActivity] = useState<AgenticActivityState>(
    () => createIdleAgenticActivityState(),
  );
  // ASK-COT — synchronous mirror of the activity state. The reducer is
  // pure and dispatches are serialized per SSE event, so computing the
  // next state from the mirror keeps it current regardless of React
  // batching. The per-turn `finally` blocks read it to persist the
  // frozen process snapshot BEFORE the live state resets to idle.
  const agenticActivityMirrorRef = useRef<AgenticActivityState>(
    createIdleAgenticActivityState(),
  );
  const [modelOptions, setModelOptions] = useState<ReaderAskModelOptionSummaryDto[]>([]);
  const [defaultModelKey, setDefaultModelKey] = useState<string | null>(null);
  const [selectedModelKey, setSelectedModelKey] = useState<string | null>(null);
  const [webSearchMode, setWebSearchMode] = useState<WebSearchModeDto>("disabled");
  const [modelOptionsLoading, setModelOptionsLoading] = useState(false);
  const [, setModelOptionsError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  // ASK-UX-MOBILE R2 — turn-scoped system notices keyed by messageId. These
  // persist across new turns (do not drift to the composer) and render
  // inside the corresponding assistant turn bubble, not above the composer.
  const [turnNotices, setTurnNotices] = useState<Record<string, AskSystemNotice>>({});
  // ASK-UX-MOBILE R2 — panel-level init / restore / capability notice.
  // Renders in a dedicated banner slot between the header and the
  // conversation wrapper, never in a turn bubble or the composer.
  const [panelNotice, setPanelNotice] = useState<AskSystemNotice | null>(null);
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
  // ASK-RETRY-CONTRACT-R1/R2/R8 — retain the original send payload for
  // recovery. Keys may be local-assistant-* or a canonical UUID after
  // message.started rekey. Authority is clientSubmissionId on the value.
  // Do not delete until trusted terminal or successful hydrate.
  const pendingSendByLocalAssistantRef = useRef<
    Map<string, PendingSendRequest>
  >(new Map());
  // In-flight regenerate guard (double-click): at most one active retry
  // per assistant message id.
  const activeRetryMessageIdsRef = useRef<Set<string>>(new Set());

  /** R8: pending recovery forces 重新发送 even when bubble id is UUID. */
  const resolveRetryTarget = useCallback((messageId: string) => {
    return classifyRetryTargetForRecovery(
      messageId,
      pendingSendByLocalAssistantRef.current.has(messageId),
    );
  }, []);

  const dispatchAgenticActivity = (event: AgenticActivityEvent) => {
    const next = reduceAgenticActivityEvent(agenticActivityMirrorRef.current, event);
    agenticActivityMirrorRef.current = next;
    setAgenticActivity(next);
  };

  // ASK-COT — persist the frozen process snapshot onto the settled
  // message. Reads the synchronous mirror (never React state — rAF
  // batching could lag the last dispatch). `buildAgenticProcessSnapshot`
  // returns null when no run identity was bound (legacy lanes, failures
  // before run_started), so legacy turns never carry a snapshot.
  const persistAgenticProcessSnapshot = () => {
    const finalActivity = agenticActivityMirrorRef.current;
    const snapshotMessageId =
      finalActivity.messageId ?? streamingAssistantIdRef.current;
    if (snapshotMessageId == null) {
      return;
    }
    const snapshot = buildAgenticProcessSnapshot(finalActivity);
    if (snapshot == null) {
      return;
    }
    setMessages((current) =>
      current.map((message) =>
        message.id === snapshotMessageId
          ? { ...message, agentic_process_snapshot: snapshot }
          : message,
      ),
    );
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

  // Page-authoritative article title for the permanent composer chip
  // (snapshot.record.title via the recordTitle prop; pageIdentity as a
  // defensive fallback). Never the thread title.
  const currentArticleChipTitle =
    recordTitle?.trim() || pageIdentity.recordTitle?.trim() || "当前文章";

  // R3 P1 — Reading Record selection slots (auto 0/1 + manual ≤3) ride along
  // on send as explicit focus context. The Web Reader has one Ask contract
  // and one selection lane.
  const selectionSlotAttachments = [
    ...(autoSelectionAttachment ? [autoSelectionAttachment] : []),
    ...(manualSelectionAttachments ?? []),
  ];

  // ASK-UX-HISTORY-COT-R2 P0-2: the current article is fixed implicit
  // context — it must NOT produce a default "基于：当前文章" provenance
  // row. Only explicit selections / attachments / other articles surface
  // in provenance. When nothing is explicit, the provenance line does
  // not render at all (no "仅按你的问题回答" noise). The page identity
  // title remains the single source of truth for the reader header; it
  // is never echoed here as an attachment label.
  const provenanceNoteCount = visibleContextAttachments.length;
  const provenanceParts: string[] = [];
  // R3 P1 — explicit RR selections (auto/manual slots) surface in
  // provenance; the implicit current article never does.
  if (selectionSlotAttachments.length > 0) {
    provenanceParts.push(
      selectionSlotAttachments.length === 1
        ? "选中段"
        : `${selectionSlotAttachments.length} 处选区`,
    );
  }
  if (provenanceNoteCount > 0) {
    provenanceParts.push(`${provenanceNoteCount} 条笔记`);
  }
  const provenanceJoinedParts = provenanceParts.join(" · ");
  const provenanceSummary =
    provenanceParts.length > 0 ? `基于：${provenanceJoinedParts}` : "";
  const provenanceDetails: Array<{ label: string; value: string }> = [];
  selectionSlotAttachments.forEach((attachment, index) => {
    const selectionText = attachment.selectedText?.trim();
    provenanceDetails.push({
      label: selectionSlotAttachments.length === 1 ? "选中段" : `选区 ${index + 1}`,
      value: truncateProvenanceDetail(selectionText || askAttachmentLabel(attachment)),
    });
  });
  visibleContextAttachments.forEach((attachment, index) => {
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
      `/api/web/reader/records/${encodeURIComponent(recordId)}/ask/threads`,
      undefined,
      "Ask Claread 线程列表加载失败。",
    );
    return payload.items ?? [];
  }

  async function fetchThreadDetail(threadId: string) {
    return fetchJson<ReaderAskThreadDetailDto>(
      `/api/web/reader/records/${encodeURIComponent(recordId)}/ask/threads/${encodeURIComponent(threadId)}`,
      undefined,
      "Ask Claread 加载失败。",
    );
  }

  async function fetchModelOptions() {
    return fetchJson<ReaderAskModelOptionListResponseDto>(
      `/api/web/reader/records/${encodeURIComponent(recordId)}/ask/model-options`,
      undefined,
      "Ask Claread 模型列表加载失败。",
    );
  }

  async function createThread(title: string) {
    return fetchJson<ReaderAskThreadSummaryDto>(
      `/api/web/reader/records/${encodeURIComponent(recordId)}/ask/threads`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          record_id: recordId,
          title,
          model: effectiveSelectedModelKey,
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
      composer.consumePendingQuickAction();
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
        `/api/web/reader/records/${encodeURIComponent(recordId)}/ask/threads/${encodeURIComponent(activeThreadId)}/reset`,
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
      setTurnNotices({});
      setPanelNotice(null);
      composer.clearAttachments();
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

  async function sendMessage(options?: {
    content?: string;
    attachments?: ReaderAskAttachment[];
    /**
     * `merge_current` is the normal Composer contract: visible persistent
     * selection slots are part of the request even when a quick action
     * supplies additional attachments. `exact` is reserved for replaying a
     * previously persisted/pending turn (resend or disambiguation), where
     * current draft chips must not mutate the historical request.
     */
    attachmentMode?: "merge_current" | "exact";
    entryAction?: ReaderAskEntryActionDto;
    submissionMode?: "chat" | "quick_action";
    clearComposer?: boolean;
    /** R2: reuse a prior client_submission_id on resend (idempotent claim). */
    clientSubmissionId?: string;
    /**
     * R2.1 — per-send web search mode override. When provided, this wins
     * over the panel-level ``webSearchMode`` state for this single send
     * (used by the empty-state "查询相关资料" suggestion). The panel state
     * is also updated to match so the composer toggle reflects the choice.
     */
    webSearchModeOverride?: WebSearchModeDto;
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

    const attachmentMode = options?.attachmentMode ?? "merge_current";
    // The composer context module owns the selection slots; ordinary sends
    // merge them into the request context (draft selections survive the
    // message). `exact` replays a previously persisted/pending turn
    // verbatim, without current draft slots.
    const baseAttachments = options?.attachments ?? attachments;
    const usedAttachments =
      attachmentMode === "merge_current"
        ? composer.buildSendAttachments(baseAttachments)
        : baseAttachments;
    const entryAction = options?.entryAction ?? defaultEntryAction();
    const submissionMode = options?.submissionMode ?? "chat";
    const now = Date.now();
    const tempUserId = `local-user-${now}`;
    const tempAssistantId = `local-assistant-${now}`;
    // ASK-RETRY-CONTRACT-R2 — client-generated UUID claimed server-side
    // before any model call. Same id re-submitted after a network blip
    // must not create a second user/assistant pair. Resend reuses the
    // retained id from the failed pending submission.
    const clientSubmissionId =
      options?.clientSubmissionId ??
      (typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `00000000-0000-4000-8000-${String(now).padStart(12, "0").slice(-12)}`);
    // R2.1 — per-send override wins; fall back to panel-level state.
    const effectiveWebSearchMode =
      options?.webSearchModeOverride ?? webSearchMode;
    if (options?.webSearchModeOverride && options.webSearchModeOverride !== webSearchMode) {
      setWebSearchMode(options.webSearchModeOverride);
    }
    const pendingRequest: PendingSendRequest = {
      content,
      attachments: usedAttachments.map(serializeAttachment),
      entryAction: entryAction,
      model: effectiveSelectedModelKey,
      webSearchMode: effectiveWebSearchMode,
      clientSubmissionId,
      localUserId: tempUserId,
      localAssistantId: tempAssistantId,
      threadId,
    };
    pendingSendByLocalAssistantRef.current.set(tempAssistantId, pendingRequest);
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
      // Record the user's web search request mode at send time so the
      // backend can persist it as message metadata and replay the original
      // turn capability on retry (server-side source of truth). Absent on
      // cold history; retry resolves the mode from persisted metadata only.
      web_search_mode: effectiveWebSearchMode,
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
      context_compaction: null,
      regenerate_preview: false,
      usage_event_id: null,
      // Clear agentic evidence so a new turn never inherits prior basis.
      agentic_evidence: null,
      agentic_evidence_scope: null,
      agentic_answer_blocks: null,
      agentic_citations: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };

    setSending(true);
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

    // R7: single reconcile helper shared by submission.reconcile / eof /
    // parse_error / non-abort transport failure. Declared outside try so
    // catch can call the same function (no duplicated poll/hydrate).
    type ReconcileSnap = {
      status?: string;
      assistant_message_id?: string | null;
      user_message_id?: string | null;
      action_hint?: string | null;
      assistant_message?: {
        id?: string;
        content_md?: string;
        status?: string;
        reasoning_md?: string | null;
        reasoning_status?: string | null;
        agentic_citations?: unknown;
        agentic_answer_blocks?: unknown;
      } | null;
      user_message?: { id?: string; content_md?: string } | null;
    };

    const activeAssistantId = () =>
      resolveActiveAssistantId(
        streamingAssistantIdRef.current,
        tempAssistantId,
      );

    const clearThisPending = (...extraIds: Array<string | null | undefined>) => {
      clearPendingSendKeys(
        pendingSendByLocalAssistantRef.current,
        tempAssistantId,
        streamingAssistantIdRef.current,
        ...extraIds,
      );
    };

    const applyResendPendingNotice = () => {
      // R8: target the active bubble (may already be canonical UUID).
      // Keep pending under that key so CTA is 重新发送, not /retry.
      const activeId = activeAssistantId();
      const pending =
        pendingSendByLocalAssistantRef.current.get(activeId) ??
        pendingSendByLocalAssistantRef.current.get(tempAssistantId);
      if (pending) {
        rekeyPendingSend(
          pendingSendByLocalAssistantRef.current,
          tempAssistantId,
          activeId,
        );
      }
      setTurnNotices((prev) => {
        const next = { ...prev };
        delete next[tempAssistantId];
        next[activeId] = projectSendFailureNotice({
          messageId: activeId,
          message: PENDING_SUBMISSION_RESEND_MESSAGE,
          target: "pending",
        });
        return next;
      });
      setMessages((current) =>
        current.map((message) =>
          messageMatchesActiveAssistant(
            message.id,
            activeId,
            tempAssistantId,
          )
            ? { ...message, status: "failed" }
            : message,
        ),
      );
      dispatchAgenticActivity({ type: "terminal", finalStatus: "failed" });
    };

    const reconcileSubmission = async (): Promise<boolean> => {
      const reconcileUrl = browserAskSubmissionPath(
        recordId,
        threadId,
        clientSubmissionId,
      );
      const pollOnce = async (): Promise<ReconcileSnap | null> => {
        const reconcileRes = await fetch(reconcileUrl, {
          method: "GET",
          headers: { accept: "application/json" },
        });
        if (!reconcileRes.ok) {
          return null;
        }
        return (await reconcileRes.json()) as ReconcileSnap;
      };
      let snap = await pollOnce();
      // Poll streaming OR claimed up to 8 × 500ms. Never fabricate completed.
      for (
        let i = 0;
        i < 8 &&
        (snap?.status === "streaming" || snap?.status === "claimed");
        i += 1
      ) {
        await new Promise((r) => setTimeout(r, 500));
        snap = await pollOnce();
      }
      // R8: update the active assistant bubble (UUID after message.started).
      const activeId = activeAssistantId();
      if (
        snap?.status === "completed" &&
        snap.assistant_message &&
        typeof snap.assistant_message.id === "string" &&
        isPersistedAssistantMessageId(snap.assistant_message.id) &&
        typeof snap.assistant_message.content_md === "string"
      ) {
        const asst = snap.assistant_message;
        const userId =
          typeof snap.user_message_id === "string"
            ? snap.user_message_id
            : tempUserId;
        clearThisPending(asst.id as string, activeId);
        setMessages((current) =>
          current.map((message) => {
            if (
              messageMatchesActiveAssistant(
                message.id,
                activeId,
                tempAssistantId,
              ) ||
              message.id === asst.id
            ) {
              return {
                ...message,
                id: asst.id as string,
                status: "completed",
                content_md: asst.content_md ?? "",
                reasoning_md: asst.reasoning_md ?? message.reasoning_md,
                reasoning_status:
                  (asst.reasoning_status as typeof message.reasoning_status) ??
                  "completed",
                agentic_citations:
                  (asst.agentic_citations as typeof message.agentic_citations) ??
                  null,
                agentic_answer_blocks:
                  (asst.agentic_answer_blocks as typeof message.agentic_answer_blocks) ??
                  null,
              };
            }
            if (
              message.id === tempUserId ||
              (typeof snap.user_message_id === "string" &&
                message.id === snap.user_message_id)
            ) {
              return { ...message, id: userId };
            }
            return message;
          }),
        );
        setTurnNotices((prev) => {
          const next = { ...prev };
          delete next[tempAssistantId];
          delete next[activeId];
          delete next[asst.id as string];
          return next;
        });
        dispatchAgenticActivity({ type: "terminal", finalStatus: "ok" });
        return true;
      }
      if (
        (snap?.status === "failed" || snap?.status === "cancelled") &&
        typeof snap.assistant_message_id === "string" &&
        isPersistedAssistantMessageId(snap.assistant_message_id)
      ) {
        const asstId = snap.assistant_message_id;
        // Clear recovery — escalate to regenerate (persisted), not resend.
        clearThisPending(asstId, activeId);
        setMessages((current) =>
          current.map((message) =>
            messageMatchesActiveAssistant(
              message.id,
              activeId,
              tempAssistantId,
            ) || message.id === asstId
              ? {
                  ...message,
                  id: asstId,
                  status:
                    snap.status === "cancelled" ? "interrupted" : "failed",
                  content_md:
                    snap.assistant_message?.content_md ??
                    message.content_md ??
                    "",
                }
              : message,
          ),
        );
        setTurnNotices((prev) => {
          const next = { ...prev };
          delete next[tempAssistantId];
          delete next[activeId];
          next[asstId] = projectSendFailureNotice({
            messageId: asstId,
            message: PENDING_SUBMISSION_RESEND_MESSAGE,
            target: "persisted",
          });
          return next;
        });
        dispatchAgenticActivity({
          type: "terminal",
          finalStatus: snap.status === "cancelled" ? "cancelled" : "failed",
        });
        return true;
      }
      if (snap?.action_hint === "reask" || snap?.status === "not_found") {
        clearThisPending(activeId);
        setTurnNotices((prev) => {
          const next = { ...prev };
          delete next[tempAssistantId];
          next[activeId] = projectSendFailureNotice({
            messageId: activeId,
            message: "无法确认原执行链路，请重新提问。",
            target: "pending",
          });
          return next;
        });
        setMessages((current) =>
          current.map((message) =>
            messageMatchesActiveAssistant(
              message.id,
              activeId,
              tempAssistantId,
            )
              ? { ...message, status: "failed" }
              : message,
          ),
        );
        dispatchAgenticActivity({ type: "terminal", finalStatus: "failed" });
        return true;
      }
      if (
        snap?.status === "claimed" ||
        snap?.status === "streaming" ||
        snap?.action_hint === "resend" ||
        snap?.action_hint === "wait"
      ) {
        applyResendPendingNotice();
        return true;
      }
      return false;
    };

    try {
      const requestBody: ReaderAskMessageStreamRequestDto = {
        content,
        page_identity: serializePageIdentity(pageIdentity),
        attachments: usedAttachments.map(serializeAttachment),
        entry_action: entryAction,
        model: effectiveSelectedModelKey,
        web_search_mode: effectiveWebSearchMode,
        client_submission_id: clientSubmissionId,
      };
      // ASK-RETRY-CONTRACT-R0 — browser path from the shared path builder.
      const response = await fetch(browserAskStreamPath(recordId, threadId), {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(requestBody),
        signal: controller.signal,
      });

      if (!response.ok) {
        const errorText = await response.text().catch(() => "发送失败");
        throw new Error(errorText || "发送消息失败。");
      }

      const streamResult = await consumeReaderAskSse(
        response,
        createSseMessageHandler(
          tempAssistantId,
          (updater) => setMessages(updater),
          (assignedId) => {
            streamingAssistantIdRef.current = assignedId;
            // R8: rekey pending recovery to canonical UUID — do NOT delete
            // until trusted terminal / successful hydrate (EOF path needs it).
            rekeyPendingSend(
              pendingSendByLocalAssistantRef.current,
              tempAssistantId,
              assignedId,
            );
            setMessages((current) =>
              current.map((message) =>
                message.id === tempAssistantId
                  ? { ...message, id: assignedId }
                  : message,
              ),
            );
          },
          (errorMsg) => {
            // ASK-UX-MOBILE-R3 — legacy stream-level `error` event path
            // (e.g. INSUFFICIENT_CREDITS surfaced before any
            // message.assigned). Use the canonical projector instead of
            // hand-crafting a notice. Fall back to tempAssistantId when
            // the server errors before assigning a canonical message id.
            const streamingId = streamingAssistantIdRef.current ?? tempAssistantId;
            const stillPending = !isPersistedAssistantMessageId(streamingId);
            // Preserve typed stream error copy (e.g. insufficient credits)
            // when the server already projected a friendly message; only
            // fall back to the pending-submission copy when the error is
            // generic / unavailable.
            const noticeMessage =
              stillPending &&
              (errorMsg === ASK_UNAVAILABLE_MESSAGE || !errorMsg?.trim())
                ? PENDING_SUBMISSION_RESEND_MESSAGE
                : errorMsg || PENDING_SUBMISSION_RESEND_MESSAGE;
            setTurnNotices((prev) => ({
              ...prev,
              [streamingId]: projectSendFailureNotice({
                messageId: streamingId,
                message: noticeMessage,
                target: stillPending ? "pending" : "persisted",
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

      // R7: unknown outcome → reconcile. Trusted completed/terminal/abort
      // do not extra-GET. User abort is handled in catch, not here.
      const needsReconcile =
        streamResult.kind === "submission_reconcile" ||
        streamResult.kind === "eof" ||
        streamResult.kind === "parse_error";

      if (needsReconcile) {
        const hydrated = await reconcileSubmission();
        if (!hydrated) {
          applyResendPendingNotice();
        }
      } else if (
        streamResult.kind === "completed" ||
        streamResult.kind === "terminal" ||
        streamResult.kind === "interrupted"
      ) {
        // Trusted terminal — clear recovery; do not GET reconcile.
        clearThisPending(streamingAssistantIdRef.current);
        composer.clearAttachments();
      }
    } catch (error) {
      // User-initiated stop: no submission reconcile (intentional cancel).
      if (isAbortError(error)) {
        const activeId = activeAssistantId();
        clearThisPending(activeId);
        setMessages((current) =>
          current.map((message) =>
            messageMatchesActiveAssistant(
              message.id,
              activeId,
              tempAssistantId,
            )
              ? { ...message, status: "interrupted" }
              : message,
          ),
        );
        dispatchAgenticActivity({ type: "terminal", finalStatus: "cancelled" });
      } else {
        // R7: non-abort transport failure → same reconcileSubmission helper.
        try {
          const hydrated = await reconcileSubmission();
          if (!hydrated) {
            applyResendPendingNotice();
          }
        } catch {
          applyResendPendingNotice();
        }
      }
    } finally {
      if (sseAbortRef.current === controller) {
        sseAbortRef.current = null;
      }
      // ASK-COT — persist the frozen process snapshot before the live
      // activity resets, so the settled bubble keeps its typed Chain of
      // Thought (completed/terminal already froze the reducer state).
      persistAgenticProcessSnapshot();
      // Hide activity indicator once the stream ends (reset to idle so a
      // completed answer is not stuck loading).
      dispatchAgenticActivity({ type: "reset" });
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

  /**
   * ASK-RETRY-CONTRACT-R1 — resend a pending/optimistic submission.
   * Replays the retained SendRequest with the same client_submission_id
   * (server-side idempotent claim). Never calls `/retry`.
   */
  async function handleResend(assistantMessageId: string) {
    // R8: key may be local-assistant-* or canonical UUID after rekey.
    const pending =
      pendingSendByLocalAssistantRef.current.get(assistantMessageId) ?? null;
    if (!pending || sending) {
      return;
    }
    // Clear the failed bubble pair, then re-send with the same submission id
    // so the server can reconcile rather than create a duplicate turn.
    setTurnNotices((prev) => {
      if (!prev[assistantMessageId]) {
        return prev;
      }
      const next = { ...prev };
      delete next[assistantMessageId];
      return next;
    });
    setMessages((current) =>
      current.filter(
        (message) =>
          message.id !== assistantMessageId &&
          message.id !== pending.localUserId &&
          message.id !== pending.localAssistantId,
      ),
    );
    clearPendingSendKeys(
      pendingSendByLocalAssistantRef.current,
      assistantMessageId,
      pending.localAssistantId,
    );
    await sendMessage({
      content: pending.content,
      // Attachments were serialized for wire; resend uses the stored wire
      // shape through the same serialize path when options.attachments is
      // provided as the already-serialized list is accepted by the stream
      // body builder above. Re-hydrate is not required for wire-only resend.
      attachments: pending.attachments as unknown as ReaderAskAttachment[],
      attachmentMode: "exact",
      entryAction: pending.entryAction,
      clearComposer: false,
      clientSubmissionId: pending.clientSubmissionId,
    });
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

  /** Regenerate (not resume/continue) a persisted assistant answer. */
  async function handleRetry(messageId: string) {
    if (!activeThreadId || sending) {
      return;
    }
    // ASK-RETRY-CONTRACT-R1: local/pending assistants must never hit /retry.
    const target = resolveRetryTarget(messageId);
    if (target?.kind === "pending_submission") {
      await handleResend(messageId);
      return;
    }
    if (target?.kind !== "persisted_assistant") {
      return;
    }
    // Concurrent double-click regenerate: at most one in-flight retry per id.
    if (activeRetryMessageIdsRef.current.has(messageId)) {
      return;
    }
    activeRetryMessageIdsRef.current.add(messageId);
    // Preserve original content so we can restore it if retry fails
    const originalMessage = messages.find((m) => m.id === messageId);
    const originalContentMd = originalMessage?.content_md ?? "";
    const originalReasoningMd = originalMessage?.reasoning_md ?? "";
    const originalReasoningStatus = originalMessage?.reasoning_status ?? "idle";
    // ASK-RETRY-CONTRACT-R3: keep prior canonical answer visible during
    // regenerate until the new run commits. Do not blank a completed answer.
    const keepCanonicalUntilSuccess =
      originalMessage?.status === "completed" ||
      (originalMessage?.status === "interrupted" &&
        Boolean(originalContentMd.trim()));

    // ASK-WEB-G1-R3: Retry body must NOT carry `web_search_mode`. The FastAPI
    // Retry schema is `extra="forbid"` with only `model` accepted; sending
    // `web_search_mode` would 422. The backend replays the persisted mode
    // from the original user message metadata (server-side source of truth),
    // after verifying message/thread/record/user ownership. We no longer
    // infer the original mode from `agentic_web_search` either — that
    // heuristic was wrong when capability was allowed but the agent never
    // invoked Search.

    setSending(true);
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
              // ASK-RETRY-CONTRACT-R3: keep prior canonical answer visible
              // until the new run succeeds. Interrupted partials and
              // completed answers both stay as content_md fallback; only
              // a blank prior answer starts empty.
              content_md: keepCanonicalUntilSuccess
                ? message.content_md
                : message.status === "interrupted"
                  ? message.content_md
                  : "",
              regenerate_preview: keepCanonicalUntilSuccess || message.status === "interrupted",
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
              persisted_supplements: [],
              reasoning_status: "idle",
              reasoning_md: "",
              follow_up_suggestions: [],
              compacting: false,
              context_compaction: null,
              // Clear agentic evidence so retry does not keep prior basis.
              agentic_evidence: null,
              agentic_evidence_scope: null,
              agentic_answer_blocks: null,
              agentic_citations: null,
              // ASK-COT — the old attempt's frozen process must not
              // survive into the retry (new turn_run_id ⇒ new run).
              agentic_process_snapshot: null,
            }
          : message,
      ),
    );

    try {
      // ASK-RETRY-CONTRACT-R0: Browser ABI is `/retry` only. Upstream
      // `/retry/stream` is BFF→FastAPI exclusive.
      const response = await fetch(
        browserAskRetryPath(recordId, activeThreadId, messageId),
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          // ASK-WEB-G1-R3: only `model` is sent. The backend replays the
          // persisted `web_search_mode` from the original user message
          // metadata after ownership verification — no client input.
          // ASK-RETRY-CONTRACT-R3: model is the thread's selected model
          // already bound to this turn; body.model is accepted for
          // compatibility but must not invent a new lane.
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
        // ASK-RETRY-CONTRACT-R3 — restore prior canonical answer; transport
        // failure uses Prompt Kit action CTA (重新生成), not a blank bubble.
        setTurnNotices((prev) => ({
          ...prev,
          [messageId]: projectSendFailureNotice({
            messageId: messageId,
            message: toUserFacingErrorMessage(error, ASK_UNAVAILABLE_MESSAGE),
            target: "persisted",
          }),
        }));
        setMessages((current) =>
          current.map((message) =>
            message.id === messageId
              ? {
                  ...message,
                  // Prefer interrupted when we still have a prior answer to
                  // show; failed only when there was nothing to restore.
                  status: originalContentMd.trim()
                    ? originalMessage?.status === "completed"
                      ? "completed"
                      : "interrupted"
                    : "failed",
                  // Restore original content so the user doesn't lose the previous answer
                  content_md: originalContentMd,
                  // ASK-TURN-LIFECYCLE R2 — drop any partial provisional
                  // preview accumulated before the failure. The canonical
                  // answer is restored to `originalContentMd`.
                  provisional_content_md: null,
                  reasoning_md: originalReasoningMd,
                  reasoning_status: originalReasoningStatus,
                  regenerate_preview: false,
                }
              : message,
          ),
        );
        dispatchAgenticActivity({ type: "terminal", finalStatus: "failed" });
      }
    } finally {
      activeRetryMessageIdsRef.current.delete(messageId);
      if (sseAbortRef.current === controller) {
        sseAbortRef.current = null;
      }
      // ASK-COT — persist the frozen process snapshot before reset
      // (same contract as sendMessage's finally block).
      persistAgenticProcessSnapshot();
      dispatchAgenticActivity({ type: "reset" });
      streamingAssistantIdRef.current = null;
      setSending(false);
      // ASK-TURN-LIFECYCLE R3 — composer is interactive again. Log
      // per-turn lifecycle metrics (log-safe JSON — no content or
      // secrets, only timestamps in ms relative to turn start).
      const metrics = turnMetricsRef.current;
      if (metrics !== null) {
        metrics.markComposerEnabled();
        console.info(
          "[AskTurnLifecycle] retry metrics",
          JSON.stringify(metrics.toJSON()),
        );
        turnMetricsRef.current = null;
      }
    }
  }

  if (!open) {
    return (
      <button
        type="button"
        className={cn(`ai-workspace-launcher ai-workspace-launcher--${presentation}`, workspaceLauncherClassName, "inline-flex")}
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
                webSearchCapable={webSearchCapabilityAvailable}
                onPickPrompt={(prompt, entryAction, webSearchOverride) => {
                  void sendMessage({
                    content: prompt,
                    entryAction,
                    webSearchModeOverride: webSearchOverride,
                  });
                }}
              />
            }
          >
            {conversationItems.map((item) => (
              <MessageBubble
                key={item.id}
                item={item}
                onRetry={handleRetry}
                onResend={handleResend}
                resolveRetryTarget={resolveRetryTarget}
                agenticActivity={
                  item.role === "assistant" &&
                  item.status === "streaming" &&
                  streamingAssistantIdRef.current != null &&
                  (item.id === streamingAssistantIdRef.current ||
                    agenticActivity.messageId === item.id)
                    ? agenticActivity
                    : null
                }
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
          <>
            {/* Permanent current-article chip is implicit context and
                non-removable, followed by auto/manual selections and
                explicit attachments. */}
            <CurrentArticleChip title={currentArticleChipTitle} />
            {autoSelectionAttachment ? (
              <SelectionContextChip
                attachment={autoSelectionAttachment}
                slot="auto"
                onRemove={composer.removeAutoSelection}
              />
            ) : null}
            {manualSelectionAttachments.map((attachment) => (
              <SelectionContextChip
                key={askAttachmentKey(attachment)}
                attachment={attachment}
                slot="manual"
                onRemove={composer.removeManualSelection}
              />
            ))}
            <AttachmentChips
              attachments={visibleContextAttachments}
              removable
              onRemove={composer.removeAttachment}
              variant="composer"
            />
          </>
        }
        modelOptions={modelSelectItems}
        modelSelectDisabled={loading || sending || modelOptionsLoading || modelSelectItems.length === 0}
        selectedModelKey={effectiveSelectedModelKey}
        modelPlaceholder={modelOptionsLoading ? "加载模型…" : "选择模型"}
        onModelChange={(value) => setSelectedModelKey(value)}
        // ASK-WEB-G1-R2: gate the Search toggle by the server-declared
        // capability for the current model option. When the host has not
        // declared the capability (or no model option is selected), both
        // props are undefined so AskComposer hides the toggle entirely (no
        // no-op control per product rule). When sending, AskComposer
        // disables the toggle independently — we do not duplicate that here.
        webSearchMode={webSearchCapabilityAvailable ? webSearchMode : undefined}
        onWebSearchModeChange={
          webSearchCapabilityAvailable ? setWebSearchMode : undefined
        }
      />
    </aside>
  );
}
