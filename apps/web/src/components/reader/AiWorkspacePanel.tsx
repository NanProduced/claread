"use client";

import {
  Bot,
  ChevronDown,
  LoaderCircle,
  MessageSquare,
  RotateCcw,
  Send,
  Sparkles,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
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
import { Markdown } from "@/components/ui/markdown";
import { Message as ChatMessage, MessageContent } from "@/components/ui/message";
import {
  PromptInput,
  PromptInputActions,
  PromptInputTextarea,
} from "@/components/ui/prompt-input";
import { Tool, type ToolPart } from "@/components/ui/tool";
import { IconButton } from "@/components/primitives/icon-button";
import { cn } from "@/lib/cn";
import {
  askAttachmentFromAnchor,
  askAttachmentKey,
  askAttachmentLabel,
  askCitationViewFromDto,
  citationCanJump,
  type ReaderAskAttachment,
  type ReaderAskPageIdentity,
} from "@/lib/reader-plate";
import type {
  ReaderAskActionProposalDto,
  ReaderAskActionStatusDto,
  ReaderAskAttachmentDto,
  ReaderAskEntryActionDto,
  ReaderAskCitationDto,
  ReaderAskCompletedPayloadDto,
  ReaderAskMessageDto,
  ReaderAskMessageStreamRequestDto,
  ReaderAskPageIdentityDto,
  ReaderAskResolvedContextSummaryDto,
  ReaderAskResponseCardDto,
  ReaderAskResolvedIntentDto,
  ReaderAskSupplementCandidateDto,
  ReaderAskStreamEnvelopeDto,
  ReaderAskThreadDetailDto,
  ReaderAskThreadSummaryDto,
  ReaderAskToolTraceEntryDto,
} from "@/types/api/reader-ask";
import type { SentenceModel } from "@/types/view/ReaderMockVm";
import { consumeReaderAskSse } from "./ask/sse";

type ErrorEnvelope = {
  message?: string;
  detail?: string;
  code?: string;
  payload?: unknown;
};

const IS_DEV = process.env.NODE_ENV !== "production";
const COMPOSER_PLACEHOLDER = "继续围绕当前文章、句子、译文或解析对象提问。";
const STARTER_PROMPTS = [
  "解释这句在这里的意思。",
  "为什么作者这里这样写？",
  "这段和前面的内容是什么关系？",
  "围绕这一句出一道小练习。",
];

function serializePageIdentity(pageIdentity: ReaderAskPageIdentity): ReaderAskPageIdentityDto {
  return {
    record_id: pageIdentity.recordId,
    title: pageIdentity.recordTitle ?? null,
    surface: pageIdentity.surface,
    source: pageIdentity.source,
    available_context_capabilities: [
      "record_context",
      "record_insights",
      "record_excerpt_assets",
      "history_lookup",
      "dictionary",
    ],
    has_article_overview: true,
    has_sentence_entries: true,
    has_annotations: true,
    has_user_assets: true,
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

function defaultEntryAction(attachments: ReaderAskAttachment[]): ReaderAskEntryActionDto {
  const fromAttachment = attachments.at(-1)?.metadata.entryAction;
  return fromAttachment ?? "ask_about_this";
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
  const code =
    typeof (event.data as { code?: unknown }).code === "string"
      ? String((event.data as { code?: string }).code)
      : null;
  const detail =
    typeof (event.data as { detail?: unknown }).detail === "string"
      ? String((event.data as { detail?: string }).detail)
      : "Ask Claread 暂时不可用。";
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
        summary: null,
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
      summary:
        typeof (event.data as { summary?: unknown; detail?: unknown }).summary === "string"
          ? String((event.data as { summary?: string }).summary)
          : typeof (event.data as { detail?: unknown }).detail === "string"
            ? String((event.data as { detail?: string }).detail)
            : null,
      metadata_json: {},
    },
  ];
}

function toolLabel(toolName: string) {
  switch (toolName) {
    case "get_record_context":
      return "当前文章上下文";
    case "get_record_insights":
      return "解析卡片";
    case "get_record_excerpt_assets":
      return "本文摘录资产";
    case "search_user_excerpt_assets":
      return "历史摘录资产";
    case "search_user_vocabulary":
      return "生词资产";
    case "lookup_dictionary_entry":
      return "词典";
    case "run_dictionary_ai_context_explain":
      return "词典 AI";
    case "propose_save_note":
      return "保存笔记确认";
    case "propose_save_excerpt":
      return "保存高亮确认";
    case "propose_favorite_anchor":
      return "收藏确认";
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
}: {
  attachments: ReaderAskAttachment[];
  removable?: boolean;
  onRemove?: (attachmentKey: string) => void;
  onJump?: (attachment: ReaderAskAttachment) => void;
}) {
  if (attachments.length === 0) {
    return null;
  }

  return (
    <div className="flex flex-wrap gap-2">
      {attachments.map((attachment) => {
        const attachmentKey = askAttachmentKey(attachment);
        const clickable = Boolean(onJump && attachment.kind !== "record_ref");
        return (
        <span
          key={attachmentKey}
          className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-hairline bg-surface/95 px-3 py-1.5 text-xs font-medium text-ink-soft shadow-[0_8px_24px_rgba(17,17,17,0.06)] backdrop-blur-sm"
        >
          {clickable ? (
            <button
              type="button"
              className="truncate text-left transition-colors hover:text-ink"
              onClick={() => onJump?.(attachment)}
            >
              {askAttachmentLabel(attachment)}
            </button>
          ) : (
            <span className="truncate">{askAttachmentLabel(attachment)}</span>
          )}
          {removable ? (
            <button
              type="button"
              className="inline-flex size-4 items-center justify-center rounded-full text-muted transition-colors hover:bg-reader-paper hover:text-ink"
              onClick={() => onRemove?.(attachmentKey)}
              aria-label="移除上下文"
            >
              <X className="h-3 w-3" />
            </button>
          ) : null}
        </span>
      )})}
    </div>
  );
}

function contextSummaryChips(summary?: ReaderAskResolvedContextSummaryDto | null) {
  if (!summary) {
    return [];
  }
  const chips: string[] = [];
  if (summary.current_sentence_used) {
    chips.push("当前句");
  }
  if (summary.current_paragraph_used) {
    chips.push("当前段");
  }
  if (summary.used_record_assets) {
    chips.push("本文资产");
  }
  if (summary.used_history_lookup) {
    chips.push("历史资产");
  }
  if (summary.used_dictionary) {
    chips.push("词典");
  }
  return chips.length > 0 ? chips : ["当前文章"];
}

function SupplementCandidateTray({
  candidates,
}: {
  candidates: ReaderAskSupplementCandidateDto[];
}) {
  if (candidates.length === 0) {
    return (
      <div className="mb-3 rounded-[var(--cl-radius-control-md)] border border-dashed border-hairline/80 bg-reader-paper/55 px-3 py-2.5 text-[11px] text-subtle">
        补充候选将在这里出现。
      </div>
    );
  }

  return (
    <div className="mb-3 rounded-[var(--cl-radius-control-md)] border border-hairline bg-reader-paper/72 px-3 py-3">
      <div className="mb-2 flex items-center justify-between gap-3">
        <p className="text-[11px] font-semibold text-muted">补充候选</p>
        <span className="text-[11px] text-subtle">确认后固定到当前页</span>
      </div>
      <div className="space-y-2">
        {candidates.map((candidate) => (
          <div
            key={candidate.candidate_id}
            className="rounded-[var(--cl-radius-surface-sm)] border border-hairline bg-surface px-3 py-2.5"
          >
            <p className="text-xs font-semibold text-ink">{candidate.title}</p>
            <p className="mt-1 line-clamp-3 text-[11px] leading-5 text-muted">{candidate.content}</p>
          </div>
        ))}
      </div>
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
      <div className="rounded-[var(--cl-radius-surface-sm)] border border-hairline/80 bg-[linear-gradient(180deg,rgba(251,249,243,0.88),rgba(255,255,255,0.96))]">
        <CollapsibleTrigger asChild>
          <button
            type="button"
            className="focus-ring flex w-full items-center justify-between gap-3 px-3.5 py-3 text-left"
          >
            <div className="min-w-0">
              <p className="text-xs font-semibold text-ink">{label}</p>
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
          <div className="border-t border-hairline/80 px-3.5 py-3">{children}</div>
        </CollapsibleContent>
      </div>
    </Collapsible>
  );
}

function ContextSummaryDisclosure({
  summary,
}: {
  summary?: ReaderAskResolvedContextSummaryDto | null;
}) {
  if (!summary) {
    return null;
  }

  const chips = contextSummaryChips(summary);

  return (
    <DisclosureSection label="使用上下文" summary={chips.join(" · ")}>
      <div className="flex flex-wrap gap-2">
        {chips.map((chip) => (
          <span
            key={chip}
            className="rounded-pill border border-hairline bg-surface px-2.5 py-1 text-[11px] font-medium text-muted"
          >
            {chip}
          </span>
        ))}
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
        if (card.card_type === "sentence_breakdown_card") {
          return (
            <div key={`${card.card_type}-${index}`} className="rounded-note border border-hairline bg-reader-paper px-4 py-3">
              <p className="text-xs font-semibold uppercase tracking-[0.16em] text-muted">拆句卡</p>
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
                    <div key={`${part.label}-${partIndex}`} className="rounded-note border border-hairline bg-surface px-3 py-2">
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
            <div key={`${card.card_type}-${index}`} className="rounded-note border border-hairline bg-reader-paper px-4 py-3">
              <p className="text-xs font-semibold uppercase tracking-[0.16em] text-muted">词义卡</p>
              <div className="mt-2 flex items-end justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold text-ink">{card.display_word || card.query}</p>
                  {card.phonetic ? <p className="mt-1 text-xs text-muted">/{card.phonetic}/</p> : null}
                </div>
                <span className="rounded-pill border border-hairline bg-surface px-2 py-0.5 text-[11px] font-medium text-muted">
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
          <div key={`${card.card_type}-${index}`} className="rounded-note border border-hairline bg-reader-paper px-4 py-3">
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-muted">练习卡</p>
            <p className="mt-2 text-sm font-semibold text-ink">{card.title}</p>
            <div className="mt-3 rounded-note border border-hairline bg-surface px-3 py-3 text-sm leading-6 text-ink-soft">
              <Markdown className="prose prose-sm max-w-none text-ink-soft prose-p:mb-3 prose-p:last:mb-0">
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
    <DisclosureSection label="来源" summary={`${citations.length} 条引用`}>
      <div className="flex flex-col gap-2">
        {citations.map((citation) => {
          const citationView = askCitationViewFromDto(citation);
          const canJump = citationCanJump(citation, currentRecordId);
          const sourceLabel =
            citation.source_article_title ||
            (citation.record_id === currentRecordId ? "当前文章" : "历史资产");

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
                "w-full rounded-2xl border border-hairline bg-surface px-3 py-2.5 text-left text-xs transition-colors",
                canJump ? "hover:border-muted hover:bg-reader-paper" : "cursor-default",
              )}
            >
              <div className="flex items-center justify-between gap-3">
                <span className="truncate font-semibold text-ink">{citation.label}</span>
                <span className="shrink-0 rounded-full border border-hairline bg-reader-paper px-2 py-0.5 text-[11px] text-muted">
                  {sourceLabel}
                </span>
              </div>
              {citation.selected_text ? (
                <p className="mt-1.5 line-clamp-2 text-muted">{citation.selected_text}</p>
              ) : null}
            </button>
          );
        })}
      </div>
    </DisclosureSection>
  );
}

function ToolTraceBlock({ entries }: { entries: ReaderAskToolTraceEntryDto[] }) {
  if (entries.length === 0) {
    return null;
  }

  return (
    <DisclosureSection
      label="高级"
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
    <div className="mt-3 rounded-[var(--cl-radius-surface-sm)] border border-hairline bg-[linear-gradient(180deg,rgba(251,249,243,0.92),rgba(255,255,255,0.98))] px-3.5 py-3.5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-ink">{proposal.label}</p>
          {proposal.description ? (
            <p className="mt-1 text-xs leading-5 text-muted">{proposal.description}</p>
          ) : null}
        </div>
        <span className="rounded-pill border border-hairline bg-surface px-2 py-0.5 text-[11px] font-semibold text-muted">
          {proposal.status}
        </span>
      </div>
      {proposal.status === "pending" ? (
        <div className="mt-3 flex gap-2">
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

function MessageBubble({
  message,
  currentRecordId,
  pageIdentity,
  pendingActionId,
  onConfirmAction,
  onRetry,
  onJumpToAttachment,
  onJumpToCitation,
}: {
  message: ReaderAskMessageDto;
  currentRecordId: string;
  pageIdentity: ReaderAskPageIdentity;
  pendingActionId: string | null;
  onConfirmAction: (actionId: string, confirmed: boolean) => void;
  onRetry: (messageId: string) => void;
  onJumpToAttachment?: (attachment: ReaderAskAttachment) => void;
  onJumpToCitation?: (citation: ReaderAskCitationDto) => void;
}) {
  const isAssistant = message.role === "assistant";
  const historyAttachments = message.context_anchors.map((anchor) => askAttachmentFromAnchor(anchor, pageIdentity).attachment);

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
            <Avatar className="mt-1 h-8 w-8 shrink-0 border border-hairline bg-reader-paper">
              <AvatarFallback className="bg-reader-paper text-ink">
                <Bot className="h-4 w-4" />
              </AvatarFallback>
            </Avatar>
            <div className="min-w-0 max-w-[calc(100%-3rem)] flex-1">
              <div className="mb-2 flex items-center gap-2">
                {message.status === "streaming" ? (
                  <span className="inline-flex items-center gap-1 text-[11px] text-subtle">
                    <LoaderCircle className="h-3 w-3 animate-spin" />
                    正在生成
                  </span>
                ) : null}
              </div>
              <div className="rounded-[var(--cl-radius-surface-md)] border border-hairline bg-[linear-gradient(180deg,rgba(255,255,255,0.98),rgba(250,247,239,0.98))] px-4 py-3.5 shadow-[0_14px_30px_rgba(17,17,17,0.05)]">
                <MessageContent
                  markdown
                  className="border-0 bg-transparent p-0 shadow-none text-[15px] leading-7 text-ink-soft prose prose-sm max-w-none prose-p:mb-3 prose-p:last:mb-0 prose-strong:text-ink prose-code:rounded prose-code:bg-reader-paper prose-code:px-1.5 prose-code:py-0.5 prose-code:text-[0.85em] prose-code:text-ink-soft"
                >
                  {message.content_md || "…"}
                </MessageContent>
                <ResponseCards cards={message.response_cards} />
              </div>
              <div className="mt-3 space-y-3">
                {message.status === "completed" ? (
                  <div className="flex items-center gap-2">
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      density="compact"
                      className="h-7 rounded-full px-2.5 text-[11px] text-muted"
                      onClick={() => onRetry(message.id)}
                    >
                      <RotateCcw className="h-3 w-3" />
                      <span>重新生成</span>
                    </Button>
                  </div>
                ) : null}
                <ContextSummaryDisclosure summary={message.resolved_context} />
                <CitationList
                  citations={message.citations}
                  currentRecordId={currentRecordId}
                  onJumpToCitation={onJumpToCitation}
                />
                {message.action_proposals.map((proposal) => (
                  <ConfirmActionCard
                    key={proposal.id}
                    proposal={proposal}
                    busy={pendingActionId === proposal.id}
                    onConfirm={(confirmed) => onConfirmAction(proposal.id, confirmed)}
                    onReject={(confirmed) => onConfirmAction(proposal.id, confirmed)}
                  />
                ))}
                <ToolTraceBlock entries={message.tool_trace} />
              </div>
            </div>
          </>
        ) : (
          <div className="max-w-[85%]">
            <MessageContent className="rounded-[var(--cl-radius-surface-sm)] border-[rgba(30,31,37,0.82)] bg-[linear-gradient(180deg,rgba(34,35,41,0.98),rgba(21,22,28,0.98))] px-4 py-3 text-[15px] leading-7 text-surface shadow-[0_12px_28px_rgba(17,17,17,0.12)]">
              {message.content_md}
            </MessageContent>
          </div>
        )}
      </ChatMessage>
    </div>
  );
}

function StarterState({
  onAttachRecord,
  recordTitle,
  activeSentence,
  onPickPrompt,
}: {
  onAttachRecord?: () => void;
  recordTitle?: string | null;
  activeSentence: SentenceModel | null;
  onPickPrompt: (prompt: string) => void;
}) {
  return (
    <div className="rounded-[var(--cl-radius-surface-md)] border border-dashed border-hairline bg-reader-paper/65 px-4 py-4">
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[var(--cl-radius-control-md)] bg-surface shadow-[0_10px_24px_rgba(17,17,17,0.06)]">
          <Sparkles className="h-4.5 w-4.5 text-lens-blue" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-ink">围绕当前文章继续追问</p>
          <p className="mt-1 text-sm leading-6 text-muted">
            {activeSentence?.text
              ? "当前句焦点已就绪。你可以直接追问，也可以从下面的起手问题开始。"
              : "从当前文章、选区或已附加的上下文开始提问。Ask Claread 会先像英语老师一样解释本文，再按需扩展。"}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            {STARTER_PROMPTS.map((prompt) => (
              <button
                key={prompt}
                type="button"
                className="focus-ring rounded-pill border border-hairline bg-surface px-3 py-2 text-left text-xs font-medium text-ink-soft transition-colors hover:border-muted hover:bg-reader-paper hover:text-ink"
                onClick={() => onPickPrompt(prompt)}
              >
                {prompt}
              </button>
            ))}
            {onAttachRecord ? (
              <button
                type="button"
                className="focus-ring rounded-pill border border-hairline bg-surface px-3 py-2 text-left text-xs font-medium text-lens-blue transition-colors hover:border-muted hover:text-ink"
                onClick={onAttachRecord}
              >
                引用整篇文章
              </button>
            ) : null}
          </div>
          {recordTitle ? (
            <p className="mt-3 truncate text-[11px] font-medium text-subtle">{recordTitle}</p>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export interface AiWorkspacePanelProps {
  open: boolean;
  pageIdentity: ReaderAskPageIdentity;
  recordId: string;
  recordTitle?: string | null;
  activeSentence: SentenceModel | null;
  attachments: ReaderAskAttachment[];
  hideLauncherOnMobile?: boolean;
  hideLauncherInCompactLayout?: boolean;
  onRemoveAttachment: (attachmentKey: string) => void;
  onClearAttachments: () => void;
  onAttachCurrentRecord?: () => void;
  onJumpToAttachment?: (attachment: ReaderAskAttachment) => void;
  onJumpToCitation?: (citation: ReaderAskCitationDto) => void;
  onActionExecuted?: (result: Record<string, unknown>) => void;
  onToggle: () => void;
}

export function AiWorkspacePanel({
  attachments,
  pageIdentity,
  open,
  recordId,
  recordTitle,
  activeSentence,
  hideLauncherOnMobile = false,
  hideLauncherInCompactLayout = false,
  onAttachCurrentRecord,
  onClearAttachments,
  onJumpToAttachment,
  onJumpToCitation,
  onActionExecuted,
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
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const hydrationRef = useRef(0);
  const latestSupplementCandidates = [...messages]
    .reverse()
    .find((message) => message.role === "assistant" && (message.supplement_candidates?.length ?? 0) > 0)
    ?.supplement_candidates ?? [];

  async function fetchThreadList() {
    const payload = await fetchJson<{ items: ReaderAskThreadSummaryDto[] }>(
      `/api/web/reader-ask/threads?recordId=${encodeURIComponent(recordId)}`,
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

  async function createThread(mode: "default" | "new_chat", title: string) {
    return fetchJson<ReaderAskThreadSummaryDto>(
      "/api/web/reader-ask/threads",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ record_id: recordId, mode, title }),
      },
      mode === "default" ? "Ask Claread 初始化失败。" : "新对话创建失败。",
    );
  }

  async function loadThread(threadId: string, nextThreads?: ReaderAskThreadSummaryDto[]) {
    const detail = await fetchThreadDetail(threadId);
    setActiveThreadId(threadId);
    setMessages(detail.messages);
    if (nextThreads) {
      setThreads(nextThreads);
    }
  }

  async function ensureThreadReady(): Promise<string | null> {
    setLoading(true);
    setErrorMessage(null);
    try {
      let nextThreads = await fetchThreadList();
      if (nextThreads.length === 0) {
        const createdThread = await createThread("default", recordTitle || "Ask Claread");
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
    void (async () => {
      const threadId = await ensureThreadReady();
      if (!threadId || hydrationRef.current !== currentHydration) {
        return;
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, recordId]);

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
    setPendingActionId(actionId);
    setErrorMessage(null);
    try {
      const payload = await fetchJson<{ status?: ReaderAskActionStatusDto; result?: Record<string, unknown> }>(
        `/api/web/reader-ask/threads/${activeThreadId}/actions/${actionId}/confirm`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ confirmed }),
        },
        "动作确认失败。",
      );
      setMessages((current) =>
        current.map((message) => ({
          ...message,
          action_proposals: message.action_proposals.map((proposal) =>
            proposal.id === actionId
              ? { ...proposal, status: payload.status ?? (confirmed ? "executed" : "rejected") }
              : proposal,
          ),
        })),
      );
      if (confirmed && payload.result) {
        onActionExecuted?.(payload.result);
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "动作确认失败。");
    } finally {
      setPendingActionId(null);
    }
  }

  async function sendMessage(options?: {
    content?: string;
    attachments?: ReaderAskAttachment[];
    entryAction?: ReaderAskEntryActionDto;
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
    const entryAction = options?.entryAction ?? defaultEntryAction(usedAttachments);
    const now = Date.now();
    const tempUserId = `local-user-${now}`;
    const tempAssistantId = `local-assistant-${now}`;
    const userMessage: ReaderAskMessageDto = {
      id: tempUserId,
      thread_id: threadId,
      role: "user",
      status: "completed",
      content_md: content,
      context_anchors: [],
      citations: [],
      action_proposals: [],
      tool_trace: [],
      response_cards: [],
      resolved_context: null,
      resolved_intent: null,
      context_plan: null,
      resolved_context_input: null,
      run_info: null,
      supplement_candidates: [],
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
      context_anchors: [],
      citations: [],
      action_proposals: [],
      tool_trace: [],
      response_cards: [],
      resolved_context: null,
      resolved_intent: null,
      context_plan: null,
      resolved_context_input: null,
      run_info: null,
      supplement_candidates: [],
      usage_event_id: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };

    if (options?.clearComposer !== false) {
      setComposer("");
    }
    setSending(true);
    setErrorMessage(null);
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
      });

      await consumeReaderAskSse(response, (event) => {
        if (event.event === "message.started") {
          const messageId = String((event.data as { message_id?: unknown }).message_id ?? tempAssistantId);
          setMessages((current) =>
            current.map((message) => (message.id === tempAssistantId ? { ...message, id: messageId } : message)),
          );
          return;
        }

        if (event.event === "message.delta") {
          const delta = String((event.data as { delta?: unknown }).delta ?? "");
          setMessages((current) =>
            current.map((message) =>
              message.id === tempAssistantId || (message.role === "assistant" && message.status === "streaming")
                ? { ...message, content_md: `${message.content_md}${delta}` }
                : message,
            ),
          );
          return;
        }

        if (event.event === "tool.started" || event.event === "tool.completed" || event.event === "tool.failed") {
          setMessages((current) =>
            current.map((message) =>
              message.id === tempAssistantId || (message.role === "assistant" && message.status === "streaming")
                ? { ...message, tool_trace: syncToolTrace(message.tool_trace, event) }
                : message,
            ),
          );
          return;
        }

        if (event.event === "message.completed") {
          const payload = event.data as unknown as ReaderAskCompletedPayloadDto;
          setMessages((current) =>
            current.map((message) =>
              message.id === tempAssistantId || (message.role === "assistant" && message.status === "streaming")
                ? {
                    ...message,
                    id: payload.id,
                    thread_id: payload.thread_id,
                    status: "completed",
                    content_md: payload.content_md,
                    resolved_intent: payload.resolved_intent ?? null,
                    citations: payload.citations,
                    action_proposals: payload.action_proposals,
                    tool_trace: payload.tool_trace,
                    response_cards: payload.response_cards,
                    resolved_context: payload.resolved_context,
                    context_plan: payload.context_plan ?? null,
                    resolved_context_input: payload.resolved_context_input ?? null,
                    run_info: payload.run_info ?? null,
                    supplement_candidates: payload.supplement_candidates ?? [],
                  }
                : message,
            ),
          );
          return;
        }

        if (event.event === "error") {
          setErrorMessage(formatStreamError(event));
          setMessages((current) =>
            current.map((message) =>
              message.id === tempAssistantId || (message.role === "assistant" && message.status === "streaming")
                ? { ...message, status: "failed" }
                : message,
            ),
          );
        }
      });
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Ask Claread 暂时不可用。");
      setMessages((current) =>
        current.map((message) => (message.id === tempAssistantId ? { ...message, status: "failed" } : message)),
      );
    } finally {
      setSending(false);
    }
  }

  async function handleSend() {
    await sendMessage();
  }

  async function handleRetry(messageId: string) {
    if (!activeThreadId || sending) {
      return;
    }
    setSending(true);
    setErrorMessage(null);
    setMessages((current) =>
      current.map((message) =>
        message.id === messageId
          ? {
              ...message,
              status: "streaming",
              content_md: "",
              citations: [],
              action_proposals: [],
              tool_trace: [],
              response_cards: [],
              resolved_context: null,
              context_plan: null,
              resolved_context_input: null,
              supplement_candidates: [],
            }
          : message,
      ),
    );

    try {
      const response = await fetch(
        `/api/web/reader-ask/threads/${activeThreadId}/messages/${messageId}/retry/stream`,
        {
          method: "POST",
        },
      );

      await consumeReaderAskSse(response, (event) => {
        if (event.event === "message.delta") {
          const delta = String((event.data as { delta?: unknown }).delta ?? "");
          setMessages((current) =>
            current.map((message) =>
              message.id === messageId
                ? { ...message, content_md: `${message.content_md}${delta}` }
                : message,
            ),
          );
          return;
        }

        if (event.event === "tool.started" || event.event === "tool.completed" || event.event === "tool.failed") {
          setMessages((current) =>
            current.map((message) =>
              message.id === messageId
                ? { ...message, tool_trace: syncToolTrace(message.tool_trace, event) }
                : message,
            ),
          );
          return;
        }

        if (event.event === "message.completed") {
          const payload = event.data as unknown as ReaderAskCompletedPayloadDto;
          setMessages((current) =>
            current.map((message) =>
              message.id === messageId
                ? {
                    ...message,
                    status: "completed",
                    content_md: payload.content_md,
                    resolved_intent: payload.resolved_intent ?? null,
                    citations: payload.citations,
                    action_proposals: payload.action_proposals,
                    tool_trace: payload.tool_trace,
                    response_cards: payload.response_cards,
                    resolved_context: payload.resolved_context,
                    context_plan: payload.context_plan ?? null,
                    resolved_context_input: payload.resolved_context_input ?? null,
                    run_info: payload.run_info ?? null,
                    supplement_candidates: payload.supplement_candidates ?? [],
                  }
                : message,
            ),
          );
          return;
        }

        if (event.event === "error") {
          setErrorMessage(formatStreamError(event));
          setMessages((current) =>
            current.map((message) =>
              message.id === messageId
                ? { ...message, status: "failed" }
                : message,
            ),
          );
        }
      });
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Ask Claread 暂时不可用。");
      setMessages((current) =>
        current.map((message) =>
          message.id === messageId
            ? { ...message, status: "failed" }
            : message,
        ),
      );
    } finally {
      setSending(false);
    }
  }

  if (!open) {
    return (
      <button
        type="button"
        className={`focus-ring fixed bottom-[5.25rem] right-4 z-40 min-h-11 items-center gap-2 rounded-pill border border-hairline bg-[linear-gradient(180deg,rgba(255,255,255,0.94),rgba(249,247,241,0.98))] px-4 text-sm font-semibold text-ink shadow-[0_12px_28px_rgba(17,17,17,0.06)] transition-colors hover:border-muted hover:bg-reader-paper md:bottom-6 md:right-6 ${launcherVisibilityClass}`}
        onClick={onToggle}
        aria-label="打开 AI 工作区"
      >
        <MessageSquare aria-hidden="true" className="h-4 w-4 text-lens-blue" />
        <span>Ask Claread</span>
      </button>
    );
  }

  return (
    <aside className="fixed inset-x-3 bottom-3 z-50 flex max-h-[82vh] flex-col overflow-hidden rounded-[var(--cl-radius-surface-md)] border border-hairline bg-[linear-gradient(180deg,rgba(252,251,247,0.98),rgba(255,255,255,0.98))] shadow-[0_22px_64px_rgba(17,17,17,0.11)] 2xl:inset-y-3 2xl:left-auto 2xl:right-3 2xl:w-[clamp(31rem,calc((100vw-124px-96ch)/2-0.5rem),37.5rem)] 2xl:min-w-0 2xl:max-h-none">
      <div className="border-b border-hairline/90 px-5 py-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-base font-semibold text-ink">Ask Claread</h2>
              <span className="max-w-48 truncate rounded-pill border border-hairline bg-reader-paper px-2.5 py-1 text-[11px] font-medium text-muted">
                {recordTitle || "当前文章"}
              </span>
            </div>
            <p className="mt-1.5 text-[11px] leading-5 text-muted">
              先解释本文，再按需追问词义、语法和练习。
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="quiet"
              size="sm"
              density="compact"
              className="h-9 rounded-[var(--cl-radius-control-md)] px-3 text-xs text-ink-soft"
              onClick={() => {
                void handleResetConversation();
              }}
              disabled={loading || sending || !activeThreadId}
            >
              <RotateCcw className="h-3.5 w-3.5" />
              <span>重新开始</span>
            </Button>
            <IconButton variant="quiet" size="sm" onClick={onToggle} aria-label="收起 AI 工作区">
              <X aria-hidden="true" className="h-4 w-4" />
            </IconButton>
          </div>
        </div>
      </div>

      <div className="min-h-0 flex-1 px-5 py-4">
        {loading ? (
          <div className="flex h-full items-center justify-center">
            <LoaderCircle className="h-5 w-5 animate-spin text-lens-blue" />
          </div>
        ) : (
          <ChatContainerRoot className="min-h-0 h-full w-full">
            <ChatContainerContent className="gap-5 pr-1">
              {messages.length === 0 ? (
                <StarterState
                  onAttachRecord={onAttachCurrentRecord}
                  recordTitle={recordTitle}
                  activeSentence={activeSentence}
                  onPickPrompt={(prompt) => {
                    void sendMessage({
                      content: prompt,
                    });
                  }}
                />
              ) : null}
              {messages.map((message) => (
                <MessageBubble
                  key={message.id}
                  message={message}
                  currentRecordId={recordId}
                  pageIdentity={pageIdentity}
                  pendingActionId={pendingActionId}
                  onConfirmAction={handleConfirmAction}
                  onRetry={handleRetry}
                  onJumpToAttachment={onJumpToAttachment}
                  onJumpToCitation={onJumpToCitation}
                />
              ))}
              <ChatContainerScrollAnchor />
            </ChatContainerContent>
          </ChatContainerRoot>
        )}
      </div>

      <div className="border-t border-hairline/90 bg-reader-paper/55 px-5 py-4">
        {errorMessage ? (
          <div className="mb-3 rounded-[var(--cl-radius-surface-sm)] border border-destructive/20 bg-destructive/10 px-3 py-2.5 text-xs text-destructive">
            {errorMessage}
          </div>
        ) : null}

        <PromptInput
          value={composer}
          onValueChange={setComposer}
          onSubmit={handleSend}
          isLoading={sending}
          maxHeight={220}
          className="border-hairline bg-surface px-4 py-3"
        >
          {attachments.length > 0 ? (
            <div className="mb-3 rounded-[var(--cl-radius-control-md)] border border-hairline bg-reader-paper/72 px-3 py-3">
              <div className="mb-2 flex items-center justify-between gap-3">
                <p className="text-[11px] font-semibold text-muted">附加上下文</p>
                <button
                  type="button"
                  className="focus-ring text-[11px] font-semibold text-muted transition-colors hover:text-ink"
                  onClick={onClearAttachments}
                >
                  清空
                </button>
              </div>
              <AttachmentChips attachments={attachments} removable onRemove={onRemoveAttachment} onJump={onJumpToAttachment} />
            </div>
          ) : null}
          <SupplementCandidateTray candidates={latestSupplementCandidates} />

          <PromptInputTextarea placeholder={COMPOSER_PLACEHOLDER} />
          <div className="mt-3 flex items-end justify-between gap-3 border-t border-hairline/80 pt-3">
            <p className="max-w-[18rem] text-[11px] leading-5 text-muted">
              默认只围绕当前文章；只有问到“以前 / 之前 / 见过”时才会扩展历史资产。
            </p>
            <PromptInputActions className="shrink-0">
              <button
                type="button"
                className="focus-ring inline-flex h-10 min-w-10 items-center justify-center rounded-[var(--cl-radius-control-md)] border border-[rgba(46,89,219,0.72)] bg-[linear-gradient(180deg,rgba(72,117,255,0.98),rgba(47,92,232,0.98))] px-3 text-surface shadow-[0_10px_24px_rgba(47,92,232,0.2)] disabled:opacity-40"
                disabled={sending || composer.trim().length === 0}
                onClick={handleSend}
                aria-label="发送"
              >
                {sending ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
              </button>
            </PromptInputActions>
          </div>
        </PromptInput>
      </div>
    </aside>
  );
}
