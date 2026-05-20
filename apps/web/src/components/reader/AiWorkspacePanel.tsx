"use client";

import {
  BookPlus,
  Bot,
  ChevronDown,
  LoaderCircle,
  MessageSquare,
  RotateCcw,
  Search,
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
  ReaderAskActionConfirmResponseDto,
  ReaderAskActionProposalDto,
  ReaderAskAttachmentDto,
  ReaderAskAssetDisambiguationCandidateDto,
  ReaderAskAssetDisambiguationDto,
  ReaderAskCitationDto,
  ReaderAskCompletedPayloadDto,
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

type ContextRecordSearchState = {
  items: ReaderAskContextRecordItemDto[];
  loading: boolean;
  query: string;
};

type AskPanelBlockKind =
  | "answer"
  | "response_cards"
  | "disambiguation"
  | "asset_disambiguation"
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
  attachmentCount: number;
  canSend: boolean;
  sending: boolean;
};

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

function defaultEntryAction(attachments: ReaderAskAttachment[]): ReaderAskEntryActionDto {
  const fromAttachment = attachments.at(-1)?.metadata.entryAction;
  return fromAttachment ?? "ask_about_this";
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
          className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-hairline/80 bg-reader-paper/84 px-3 py-1.5 text-xs font-medium text-ink-soft"
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

function ContextPicker({
  open,
  disabled,
  recordTitle,
  search,
  onToggle,
  onSearchChange,
  onAttachCurrentRecord,
  onAttachRelatedRecord,
}: {
  open: boolean;
  disabled?: boolean;
  recordTitle?: string | null;
  search: ContextRecordSearchState;
  onToggle: () => void;
  onSearchChange: (value: string) => void;
  onAttachCurrentRecord?: () => void;
  onAttachRelatedRecord: (item: ReaderAskContextRecordItemDto) => void;
}) {
  return (
    <div className="rounded-[20px] border border-hairline/80 bg-reader-paper/72 px-3.5 py-3.5">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold text-muted">上下文</p>
          <p className="mt-1 text-[11px] leading-5 text-subtle">显式并入当前文章或你的另一篇文章。</p>
        </div>
        <button
          type="button"
          className="focus-ring inline-flex h-8 items-center gap-1.5 rounded-full border border-hairline bg-surface px-3 text-xs font-semibold text-ink-soft transition-colors hover:text-ink disabled:opacity-50"
          onClick={onToggle}
          disabled={disabled}
        >
          <BookPlus className="h-3.5 w-3.5" />
          <span>{open ? "收起" : "上下文"}</span>
        </button>
      </div>
      {open ? (
        <div className="mt-3 space-y-3">
          {onAttachCurrentRecord ? (
            <button
              type="button"
              className="focus-ring flex w-full items-center justify-between rounded-[18px] border border-hairline/80 bg-surface px-3 py-3 text-left text-xs transition-colors hover:border-muted hover:bg-reader-paper"
              onClick={onAttachCurrentRecord}
              disabled={disabled}
            >
              <span className="font-semibold text-ink-soft">引用当前文章</span>
              <span className="max-w-[12rem] truncate text-subtle">{recordTitle || "当前文章"}</span>
            </button>
          ) : null}
          <div className="rounded-[18px] border border-hairline/80 bg-surface px-3 py-3">
            <label className="mb-2 block text-[11px] font-semibold text-muted">按标题搜索并加入我的其他文章</label>
            <div className="flex items-center gap-2 rounded-[16px] border border-hairline bg-reader-paper px-3 py-2">
              <Search className="h-3.5 w-3.5 text-muted" />
              <input
                value={search.query}
                onChange={(event) => onSearchChange(event.target.value)}
                placeholder="输入文章标题，例如 climate policy"
                className="min-w-0 flex-1 bg-transparent text-xs text-ink outline-none placeholder:text-subtle"
                disabled={disabled}
              />
              {search.loading ? <LoaderCircle className="h-3.5 w-3.5 animate-spin text-muted" /> : null}
            </div>
            {search.query.trim().length === 0 ? (
              <p className="mt-2 text-[11px] text-subtle">只搜索你自己的文章标题，最多返回 8 条。</p>
            ) : search.items.length === 0 ? (
              <p className="mt-2 text-[11px] text-subtle">没有找到可加入的文章。</p>
            ) : (
              <div className="mt-2 space-y-2">
                {search.items.map((item) => (
                  <button
                    key={item.record_id}
                    type="button"
                    className="focus-ring flex w-full items-center justify-between rounded-[16px] border border-hairline bg-reader-paper px-3 py-2.5 text-left text-xs transition-colors hover:border-muted hover:bg-white"
                    onClick={() => onAttachRelatedRecord(item)}
                    disabled={disabled}
                  >
                    <span className="min-w-0 truncate font-medium text-ink-soft">{item.title || "Untitled"}</span>
                    <span className="shrink-0 text-subtle">加入上下文</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function contextSummaryChips(
  summary?: ReaderAskResolvedContextSummaryDto | null,
  contextInput?: ReaderAskResolvedContextInputDto | null,
) {
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
  if ((contextInput?.external_record_contexts.length ?? 0) > 0) {
    chips.push(`外部文章 ${contextInput?.external_record_contexts.length}`);
  }
  if ((contextInput?.external_asset_contexts.length ?? 0) > 0) {
    chips.push(`外部资产 ${contextInput?.external_asset_contexts.length}`);
  }
  return chips.length > 0 ? chips : ["当前文章"];
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

function buildAssistantBlocks(message: ReaderAskMessageDto): AskPanelBlock[] {
  const blocks: AskPanelBlock[] = [{ kind: "answer" }];

  if (message.response_cards.length > 0) {
    blocks.push({ kind: "response_cards" });
  }
  if (message.disambiguation?.required) {
    blocks.push({ kind: "disambiguation" });
  }
  if (message.asset_disambiguation?.required) {
    blocks.push({ kind: "asset_disambiguation" });
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
  if (message.resolved_context) {
    blocks.push({ kind: "context_summary" });
  }
  if (message.evidence.length > 0) {
    blocks.push({ kind: "evidence" });
  }
  if (message.trace_summary) {
    blocks.push({ kind: "trace_summary" });
  }
  if (message.citations.length > 0) {
    blocks.push({ kind: "citations" });
  }
  if (message.tool_trace.length > 0) {
    blocks.push({ kind: "tool_trace" });
  }

  return blocks;
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
    <div className="space-y-3 rounded-[20px] border border-hairline/80 bg-reader-paper/72 px-3.5 py-3.5">
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
                className="rounded-[16px] border border-hairline/80 bg-surface px-3 py-3"
              >
                <div className="flex items-center justify-between gap-3">
                  <p className="text-xs font-semibold text-ink">{candidate.title}</p>
                  <span className="rounded-pill border border-hairline bg-reader-paper px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.14em] text-muted">
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
              className="rounded-[16px] border border-hairline/80 bg-surface px-3 py-3"
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
      <div className="rounded-[18px] border border-hairline/70 bg-reader-paper/56">
        <CollapsibleTrigger asChild>
          <button
            type="button"
            className="focus-ring flex w-full items-center justify-between gap-3 px-3.5 py-3 text-left"
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
  if (!summary) {
    return null;
  }

  const chips = contextSummaryChips(summary, contextInput);
  const currentRecordContext = contextInput?.current_record_context;
  const externalRecordContexts = contextInput?.external_record_contexts ?? [];
  const externalAssetContexts = contextInput?.external_asset_contexts ?? [];

  return (
    <DisclosureSection label="使用上下文" summary={chips.join(" · ")}>
      <div className="space-y-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-subtle">当前文章</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {chips
              .filter((chip) => !chip.startsWith("外部文章") && !chip.startsWith("外部资产"))
              .map((chip) => (
                <span
                  key={chip}
                  className="rounded-pill border border-hairline bg-surface px-2.5 py-1 text-[11px] font-medium text-muted"
                >
                  {chip}
                </span>
              ))}
            {currentRecordContext?.record_title ? (
              <span className="rounded-pill border border-hairline bg-reader-paper px-2.5 py-1 text-[11px] font-medium text-ink-soft">
                {currentRecordContext.record_title}
              </span>
            ) : null}
          </div>
        </div>
        {externalRecordContexts.length > 0 ? (
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-subtle">外部文章</p>
            <div className="mt-2 space-y-2">
              {externalRecordContexts.map((item) => (
                <div
                  key={item.record_id}
                  className="rounded-note border border-hairline bg-surface px-3 py-2.5"
                >
                  <div className="flex items-center justify-between gap-3">
                    <p className="truncate text-xs font-semibold text-ink">
                      {item.record_title || item.record_id}
                    </p>
                    <span className="rounded-pill border border-hairline bg-reader-paper px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.14em] text-muted">
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
                  {item.record_insights.length > 0 ? (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {item.record_insights.slice(0, 2).map((insight) => (
                        <span
                          key={insight}
                          className="rounded-pill border border-hairline bg-reader-paper px-2 py-0.5 text-[10px] font-medium text-muted"
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
                  className="rounded-note border border-hairline bg-surface px-3 py-2.5"
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
                    <span className="rounded-pill border border-hairline bg-reader-paper px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.14em] text-muted">
                      {item.reason === "explicit_attachment" ? "显式加入" : "自动命中"}
                    </span>
                  </div>
                  {item.content_summary ? (
                    <p className="mt-1 text-[11px] leading-5 text-muted">{item.content_summary}</p>
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
            className="rounded-note border border-hairline bg-surface px-3 py-2.5"
          >
            <div className="flex items-center justify-between gap-3">
              <p className="truncate text-xs font-semibold text-ink">{item.label}</p>
              <div className="flex shrink-0 items-center gap-1.5">
                <span className="rounded-pill border border-hairline bg-reader-paper px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.14em] text-muted">
                  {item.scope === "external_record" ? "外部" : "当前"}
                </span>
                <span className="rounded-pill border border-hairline bg-reader-paper px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.14em] text-muted">
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
    <div className="rounded-[20px] border border-hairline/80 bg-reader-paper/72 px-3.5 py-3.5">
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
            className="rounded-[16px] border border-hairline/80 bg-surface px-3 py-3"
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
    <div className="rounded-[20px] border border-hairline/80 bg-reader-paper/72 px-3.5 py-3.5">
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
            className="rounded-[16px] border border-hairline/80 bg-surface px-3 py-3"
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
    traceSummary.planner_mode,
    `working-set:${traceSummary.working_set_mode}`,
    traceSummary.reference_resolution_status !== "not_needed"
      ? `reference:${traceSummary.reference_resolution_status}`
      : null,
    traceSummary.history_lookup_used ? "history:on" : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <DisclosureSection label="规划摘要" summary={summary}>
      <div className="space-y-3 text-xs text-muted">
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
                className="rounded-pill border border-hairline bg-surface px-2.5 py-1 text-[11px] font-medium text-muted"
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
    <div className="rounded-[18px] border border-hairline/80 bg-reader-paper/72 px-3.5 py-3.5">
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
  const historyAttachments = message.context_anchors.map((anchor) => askAttachmentFromAnchor(anchor, pageIdentity).attachment);
  const clarificationText = clarificationHint(message.trace_summary, message.evidence);
  const candidateSupplements = pendingSupplementCandidates(message);
  const persistedSupplements = message.persisted_supplements.filter((entry) => entry.lifecycle_status === "persisted");

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
            <Avatar className="mt-1 h-8 w-8 shrink-0 border border-hairline/80 bg-reader-paper">
              <AvatarFallback className="bg-reader-paper text-ink">
                <Bot className="h-4 w-4" />
              </AvatarFallback>
            </Avatar>
            <div className="min-w-0 max-w-[calc(100%-3rem)] flex-1">
              <div className="mb-2 flex items-center gap-2">
                <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-subtle">Claread</span>
                {message.status === "streaming" ? (
                  <span className="inline-flex items-center gap-1 text-[11px] text-subtle">
                    <LoaderCircle className="h-3 w-3 animate-spin" />
                    正在生成
                  </span>
                ) : null}
              </div>
              {message.status === "completed" ? (
                <div className="mb-2 flex items-center gap-2">
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
              <div className="space-y-3">
                {blocks.map((block, index) => {
                  switch (block.kind) {
                    case "answer":
                      return (
                        <div
                          key={`${message.id}-${block.kind}-${index}`}
                          className="rounded-[24px] border border-hairline/75 bg-[rgba(255,255,255,0.94)] px-4 py-3.5 shadow-[0_18px_40px_rgba(17,17,17,0.05)]"
                        >
                          {clarificationText ? (
                            <div className="mb-3 rounded-[16px] border border-amber-200/70 bg-amber-50/80 px-3 py-2.5 text-[11px] leading-5 text-amber-900">
                              {clarificationText}
                            </div>
                          ) : null}
                          <MessageContent
                            markdown
                            className="border-0 bg-transparent p-0 shadow-none text-[15px] leading-7 text-ink-soft prose prose-sm max-w-none prose-p:mb-3 prose-p:last:mb-0 prose-strong:text-ink prose-code:rounded prose-code:bg-reader-paper prose-code:px-1.5 prose-code:py-0.5 prose-code:text-[0.85em] prose-code:text-ink-soft"
                          >
                            {message.content_md || "…"}
                          </MessageContent>
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
                    case "asset_disambiguation":
                      return (
                        <AssetDisambiguationCards
                          key={`${message.id}-${block.kind}-${index}`}
                          assetDisambiguation={message.asset_disambiguation}
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
                    case "context_summary":
                      return (
                        <ContextSummaryDisclosure
                          key={`${message.id}-${block.kind}-${index}`}
                          summary={message.resolved_context}
                          contextInput={message.resolved_context_input}
                        />
                      );
                    case "evidence":
                      return <EvidenceDisclosure key={`${message.id}-${block.kind}-${index}`} evidence={message.evidence} />;
                    case "trace_summary":
                      return (
                        <TraceSummaryDisclosure
                          key={`${message.id}-${block.kind}-${index}`}
                          traceSummary={message.trace_summary}
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
                    case "tool_trace":
                      return <ToolTraceBlock key={`${message.id}-${block.kind}-${index}`} entries={message.tool_trace} />;
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
            </div>
          </>
        ) : (
          <div className="max-w-[85%]">
            <MessageContent className="rounded-[20px] border-[rgba(30,31,37,0.82)] bg-[linear-gradient(180deg,rgba(34,35,41,0.98),rgba(21,22,28,0.98))] px-4 py-3 text-[15px] leading-7 text-surface shadow-[0_12px_28px_rgba(17,17,17,0.12)]">
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
    <div className="flex min-h-[42vh] flex-col justify-end pb-6 pt-10">
      <div className="max-w-[28rem]">
        <div className="mb-5 flex h-11 w-11 items-center justify-center rounded-full border border-hairline/80 bg-surface shadow-[0_16px_34px_rgba(17,17,17,0.06)]">
          <Sparkles className="h-4.5 w-4.5 text-lens-blue" />
        </div>
        <p className="text-[28px] font-semibold tracking-[-0.03em] text-ink">围绕当前文章继续问</p>
        <p className="mt-3 text-sm leading-6 text-muted">
          {activeSentence?.text
            ? "当前句焦点已就绪。你可以直接追问，也可以从下面的起手问题开始。"
            : "Ask Claread 会先解释当前文章，再按需展开到上下文、历史文章和稳定资产。"}
        </p>
        {recordTitle ? (
          <p className="mt-3 truncate text-[11px] font-medium uppercase tracking-[0.14em] text-subtle">{recordTitle}</p>
        ) : null}
        <div className="mt-6 flex flex-wrap gap-2">
          {STARTER_PROMPTS.map((prompt) => (
            <button
              key={prompt}
              type="button"
              className="focus-ring rounded-full border border-hairline/80 bg-surface px-3.5 py-2 text-left text-xs font-medium text-ink-soft transition-colors hover:border-muted hover:bg-reader-paper hover:text-ink"
              onClick={() => onPickPrompt(prompt)}
            >
              {prompt}
            </button>
          ))}
          {onAttachRecord ? (
            <button
              type="button"
              className="focus-ring rounded-full border border-hairline/80 bg-surface px-3.5 py-2 text-left text-xs font-medium text-lens-blue transition-colors hover:border-muted hover:text-ink"
              onClick={onAttachRecord}
            >
              引用整篇文章
            </button>
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
  onAppendAttachments?: (attachments: ReaderAskAttachment[]) => void;
  onAttachCurrentRecord?: () => void;
  onJumpToAttachment?: (attachment: ReaderAskAttachment) => void;
  onJumpToCitation?: (citation: ReaderAskCitationDto) => void;
  onActionExecuted?: (result: ReaderAskActionConfirmResponseDto["result"]) => void;
  onSupplementDeleted?: (supplementId: string) => void | Promise<void>;
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
  onAppendAttachments,
  onAttachCurrentRecord,
  onClearAttachments,
  onJumpToAttachment,
  onJumpToCitation,
  onActionExecuted,
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
  const conversationItems: AskPanelConversationItem[] = messages.map((message) => ({
    id: message.id,
    role: message.role,
    status: message.status,
    message,
    blocks: message.role === "assistant" ? buildAssistantBlocks(message) : [],
  }));
  const composerDockState: AskComposerDockState = {
    attachmentCount: attachments.length,
    canSend: composer.trim().length > 0 && !sending,
    sending,
  };

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

  async function fetchContextRecords(query: string) {
    return fetchJson<ReaderAskContextRecordSearchResponseDto>(
      `/api/web/reader-ask/context-records?query=${encodeURIComponent(query)}&excludeRecordId=${encodeURIComponent(recordId)}`,
      undefined,
      "上下文文章搜索失败。",
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
    if (!normalizedQuery) {
      setContextSearch((current) => ({ ...current, items: [], loading: false }));
      return;
    }

    let cancelled = false;
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
    }, 180);

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
      items: [],
      loading: false,
    }));
    setContextPickerOpen(false);
  }

  async function handleSelectDisambiguationCandidate(messageId: string, candidate: ReaderAskContextRecordItemDto) {
    if (sending) {
      return;
    }
    const candidateAttachment = buildRelatedRecordAttachment(pageIdentity, candidate);
    const nextAttachments = mergeAttachments(attachments, [candidateAttachment]);
    const assistantIndex = messages.findIndex((message) => message.id === messageId);
    const priorUserMessage =
      assistantIndex > 0
        ? [...messages.slice(0, assistantIndex)].reverse().find((message) => message.role === "user")
        : null;
    if (!priorUserMessage?.content_md.trim()) {
      setErrorMessage("没有找到这轮澄清对应的原始问题，暂时无法继续当前讨论。");
      return;
    }
    if (!attachments.some((item) => askAttachmentKey(item) === askAttachmentKey(candidateAttachment))) {
      onAppendAttachments?.([candidateAttachment]);
    }
    await sendMessage({
      content: priorUserMessage.content_md,
      attachments: nextAttachments,
      entryAction: defaultEntryAction(nextAttachments),
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
    const nextAttachments = mergeAttachments(attachments, [candidateAttachment]);
    const assistantIndex = messages.findIndex((message) => message.id === messageId);
    const priorUserMessage =
      assistantIndex > 0
        ? [...messages.slice(0, assistantIndex)].reverse().find((message) => message.role === "user")
        : null;
    if (!priorUserMessage?.content_md.trim()) {
      setErrorMessage("没有找到这轮资产澄清对应的原始问题，暂时无法继续当前讨论。");
      return;
    }
    if (!attachments.some((item) => askAttachmentKey(item) === askAttachmentKey(candidateAttachment))) {
      onAppendAttachments?.([candidateAttachment]);
    }
    await sendMessage({
      content: priorUserMessage.content_md,
      attachments: nextAttachments,
      entryAction: defaultEntryAction(nextAttachments),
      clearComposer: false,
    });
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
      evidence: [],
      trace_summary: null,
      disambiguation: null,
      asset_disambiguation: null,
      response_cards: [],
      resolved_context: null,
      resolved_intent: null,
      context_plan: null,
      resolved_context_input: null,
      run_info: null,
      supplement_candidates: [],
      persisted_supplements: [],
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
      evidence: [],
      trace_summary: null,
      disambiguation: null,
      asset_disambiguation: null,
      response_cards: [],
      resolved_context: null,
      resolved_intent: null,
      context_plan: null,
      resolved_context_input: null,
      run_info: null,
      supplement_candidates: [],
      persisted_supplements: [],
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
                    evidence: payload.evidence ?? [],
                    trace_summary: payload.trace_summary ?? null,
                    disambiguation: payload.disambiguation ?? null,
                    asset_disambiguation: payload.asset_disambiguation ?? null,
                    response_cards: payload.response_cards,
                    resolved_context: payload.resolved_context,
                    context_plan: payload.context_plan ?? null,
                    resolved_context_input: payload.resolved_context_input ?? null,
                    run_info: payload.run_info ?? null,
                    supplement_candidates: payload.supplement_candidates ?? [],
                    persisted_supplements: payload.persisted_supplements ?? [],
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
    setSupplementNotice(null);
    setSupplementNoticeMessageId(null);
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
              evidence: [],
              trace_summary: null,
              disambiguation: null,
              asset_disambiguation: null,
              response_cards: [],
              resolved_context: null,
              context_plan: null,
              resolved_context_input: null,
              supplement_candidates: [],
              persisted_supplements: [],
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
                    evidence: payload.evidence ?? [],
                    trace_summary: payload.trace_summary ?? null,
                    disambiguation: payload.disambiguation ?? null,
                    asset_disambiguation: payload.asset_disambiguation ?? null,
                    response_cards: payload.response_cards,
                    resolved_context: payload.resolved_context,
                    context_plan: payload.context_plan ?? null,
                    resolved_context_input: payload.resolved_context_input ?? null,
                    run_info: payload.run_info ?? null,
                    supplement_candidates: payload.supplement_candidates ?? [],
                    persisted_supplements: payload.persisted_supplements ?? [],
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
    <aside className="fixed inset-x-3 bottom-3 z-50 flex max-h-[82vh] flex-col overflow-hidden rounded-[28px] border border-hairline/85 bg-[linear-gradient(180deg,rgba(250,249,245,0.98),rgba(255,255,255,0.98))] shadow-[0_26px_76px_rgba(17,17,17,0.12)] 2xl:inset-y-3 2xl:left-auto 2xl:right-3 2xl:w-[clamp(31rem,calc((100vw-124px-96ch)/2-0.5rem),37.5rem)] 2xl:min-w-0 2xl:max-h-none">
      <div className="border-b border-hairline/70 px-5 py-4">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h2 className="text-[15px] font-semibold text-ink">Ask Claread</h2>
              <span className="max-w-48 truncate rounded-full border border-hairline/80 bg-reader-paper px-2.5 py-1 text-[11px] font-medium text-subtle">
                {recordTitle || "当前文章"}
              </span>
            </div>
          </div>
          <div className="flex items-center gap-1.5">
            <Button
              type="button"
              variant="quiet"
              size="sm"
              density="compact"
              className="h-8 rounded-full px-3 text-xs text-ink-soft"
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

      <div className="min-h-0 flex-1 px-5 py-5">
        {loading ? (
          <div className="flex h-full items-center justify-center">
            <LoaderCircle className="h-5 w-5 animate-spin text-lens-blue" />
          </div>
        ) : (
          <ChatContainerRoot className="min-h-0 h-full w-full">
            <ChatContainerContent className="gap-6 pr-1">
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
                  onJumpToCitation={onJumpToCitation}
                />
              ))}
              <ChatContainerScrollAnchor />
            </ChatContainerContent>
          </ChatContainerRoot>
        )}
      </div>

      <div className="border-t border-hairline/70 bg-[rgba(247,246,242,0.84)] px-4 py-4">
        {errorMessage ? (
          <div className="mb-3 rounded-[18px] border border-destructive/20 bg-destructive/10 px-3 py-2.5 text-xs text-destructive">
            {errorMessage}
          </div>
        ) : null}

        <PromptInput
          value={composer}
          onValueChange={setComposer}
          onSubmit={handleSend}
          isLoading={sending}
          maxHeight={220}
          className="bg-surface px-4 py-3"
        >
          <div className="mb-3 flex items-center justify-between gap-3">
            <div className="flex min-w-0 items-center gap-2">
              <button
                type="button"
                className="focus-ring inline-flex h-8 items-center gap-1.5 rounded-full border border-hairline/80 bg-reader-paper px-3 text-xs font-semibold text-ink-soft transition-colors hover:text-ink"
                onClick={() => {
                  setContextPickerOpen((current) => !current);
                }}
                disabled={sending}
                aria-label="上下文"
              >
                <BookPlus className="h-3.5 w-3.5" />
                <span>上下文</span>
              </button>
              {composerDockState.attachmentCount > 0 ? (
                <span className="text-[11px] text-subtle">
                  已附加 {composerDockState.attachmentCount} 项
                </span>
              ) : (
                <span className="text-[11px] text-subtle">默认围绕当前文章继续问</span>
              )}
            </div>
            <PromptInputActions className="shrink-0">
              <button
                type="button"
                className="focus-ring inline-flex h-10 min-w-10 items-center justify-center rounded-full border border-[rgba(46,89,219,0.72)] bg-[linear-gradient(180deg,rgba(72,117,255,0.98),rgba(47,92,232,0.98))] px-3 text-surface shadow-[0_10px_24px_rgba(47,92,232,0.2)] disabled:opacity-40"
                disabled={!composerDockState.canSend}
                onClick={handleSend}
                aria-label="发送"
              >
                {composerDockState.sending ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
              </button>
            </PromptInputActions>
          </div>
          {attachments.length > 0 ? (
            <div className="mb-3 flex items-start justify-between gap-3 rounded-[18px] border border-hairline/80 bg-reader-paper/68 px-3 py-3">
              <div className="min-w-0 flex-1">
                <p className="mb-2 text-[11px] font-semibold text-muted">附加上下文</p>
                <AttachmentChips attachments={attachments} removable onRemove={onRemoveAttachment} onJump={onJumpToAttachment} />
              </div>
              <button
                type="button"
                className="focus-ring shrink-0 text-[11px] font-semibold text-muted transition-colors hover:text-ink"
                onClick={onClearAttachments}
              >
                清空
              </button>
            </div>
          ) : null}
          {contextPickerOpen ? (
            <div className="mb-3">
              <ContextPicker
                open={contextPickerOpen}
                disabled={sending}
                recordTitle={recordTitle}
                search={contextSearch}
                onToggle={() => {
                  setContextPickerOpen((current) => !current);
                }}
                onSearchChange={(value) => {
                  setContextSearch((current) => ({ ...current, query: value }));
                }}
                onAttachCurrentRecord={onAttachCurrentRecord}
                onAttachRelatedRecord={handleAttachRelatedRecord}
              />
            </div>
          ) : null}
          <PromptInputTextarea placeholder={COMPOSER_PLACEHOLDER} />
          <div className="mt-3 border-t border-hairline/70 pt-3">
            <p className="max-w-[22rem] text-[11px] leading-5 text-muted">
              默认只围绕当前文章；只有问到“以前 / 之前 / 见过”时才会扩展历史资产。
            </p>
          </div>
        </PromptInput>
      </div>
    </aside>
  );
}
