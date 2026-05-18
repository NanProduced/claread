"use client";

import {
  ChevronDown,
  LoaderCircle,
  MessageSquare,
  Plus,
  Send,
  Wrench,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ChatContainerContent,
  ChatContainerRoot,
  ChatContainerScrollAnchor,
} from "@/components/ui/chat-container";
import { Markdown } from "@/components/ui/markdown";
import { Message as ChatMessage, MessageContent } from "@/components/ui/message";
import {
  PromptInput,
  PromptInputActions,
  PromptInputTextarea,
} from "@/components/ui/prompt-input";
import { Tool, type ToolPart } from "@/components/ui/tool";
import { cn } from "@/lib/cn";
import type {
  ReaderAskActionProposalDto,
  ReaderAskActionStatusDto,
  ReaderAskAnchorRefDto,
  ReaderAskCitationDto,
  ReaderAskCompletedPayloadDto,
  ReaderAskMessageDto,
  ReaderAskReaderFocusDto,
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

function formatAnchorLabel(anchor: ReaderAskAnchorRefDto) {
  return (
    anchor.label ||
    anchor.selected_text ||
    anchor.query ||
    anchor.entry_type ||
    anchor.anchor_type
  );
}

function formatThreadLabel(thread: ReaderAskThreadSummaryDto, index: number) {
  if (thread.is_default) {
    return "当前会话";
  }
  if (!thread.title || thread.title === "New chat" || thread.title === "Ask Claread") {
    return `会话 ${index + 1}`;
  }
  return thread.title;
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

function AnchorChips({
  anchors,
  removable = false,
  onRemove,
}: {
  anchors: ReaderAskAnchorRefDto[];
  removable?: boolean;
  onRemove?: (index: number) => void;
}) {
  if (anchors.length === 0) {
    return null;
  }

  return (
    <div className="flex flex-wrap gap-2">
      {anchors.map((anchor, index) => (
        <span
          key={`${anchor.anchor_type}-${anchor.target_key ?? anchor.sentence_id ?? index}`}
          className="inline-flex max-w-full items-center gap-1.5 rounded-pill border border-hairline bg-surface px-2.5 py-1 text-xs font-medium text-ink-soft shadow-surface-quiet"
        >
          <span className="truncate">{formatAnchorLabel(anchor)}</span>
          {removable ? (
            <button
              type="button"
              className="inline-flex size-4 items-center justify-center rounded-full text-muted transition-colors hover:bg-reader-paper hover:text-ink"
              onClick={() => onRemove?.(index)}
              aria-label="移除上下文"
            >
              <X className="h-3 w-3" />
            </button>
          ) : null}
        </span>
      ))}
    </div>
  );
}

function CitationList({
  citations,
  currentRecordId,
  onJumpToSentence,
}: {
  citations: ReaderAskCitationDto[];
  currentRecordId: string;
  onJumpToSentence?: (sentenceId: string) => void;
}) {
  if (citations.length === 0) {
    return null;
  }

  return (
    <div className="mt-3 flex flex-col gap-2">
      {citations.map((citation) => {
        const canJump = citation.record_id === currentRecordId && typeof citation.sentence_id === "string";
        const sourceLabel =
          citation.source_article_title ||
          (citation.record_id === currentRecordId ? "当前文章" : "历史资产");

        return (
          <button
            key={citation.citation_id}
            type="button"
            disabled={!canJump}
            onClick={() => {
              if (canJump && citation.sentence_id) {
                onJumpToSentence?.(citation.sentence_id);
              }
            }}
            className={cn(
              "w-full rounded-note border border-hairline bg-reader-paper px-3 py-2 text-left text-xs transition-colors",
              canJump ? "hover:border-muted hover:bg-surface" : "cursor-default",
            )}
          >
            <div className="flex items-center justify-between gap-3">
              <span className="truncate font-semibold text-ink">{citation.label}</span>
              <span className="shrink-0 rounded-pill border border-hairline bg-surface px-2 py-0.5 text-[11px] text-muted">
                {sourceLabel}
              </span>
            </div>
            {citation.selected_text ? (
              <p className="mt-1 line-clamp-2 text-muted">{citation.selected_text}</p>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

function ToolTraceBlock({ entries }: { entries: ReaderAskToolTraceEntryDto[] }) {
  if (entries.length === 0) {
    return null;
  }

  return (
    <details className="mt-3 rounded-note border border-hairline bg-surface px-3 py-2">
      <summary className="flex cursor-pointer list-none items-center gap-2 text-xs font-semibold text-muted">
        <Wrench className="h-3.5 w-3.5" />
        <span>工具轨迹</span>
      </summary>
      <div className="mt-2 space-y-2">
        {entries.map((entry, index) => (
          <Tool
            key={`${entry.tool_name}-${index}`}
            toolPart={toolTraceToPart(entry)}
            className="mt-0 border-hairline bg-reader-paper"
          />
        ))}
      </div>
    </details>
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
    <div className="mt-3 rounded-note border border-hairline bg-reader-paper px-3 py-3">
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
          <button
            type="button"
            className="focus-ring inline-flex h-8 items-center justify-center rounded-pill bg-ink px-3 text-xs font-semibold text-surface disabled:opacity-50"
            disabled={busy}
            onClick={() => onConfirm(true)}
          >
            确认
          </button>
          <button
            type="button"
            className="focus-ring inline-flex h-8 items-center justify-center rounded-pill border border-hairline px-3 text-xs font-semibold text-muted disabled:opacity-50"
            disabled={busy}
            onClick={() => onReject(false)}
          >
            取消
          </button>
        </div>
      ) : null}
    </div>
  );
}

function ThreadSwitcher({
  threads,
  activeThreadId,
  open,
  onToggle,
  onSelect,
}: {
  threads: ReaderAskThreadSummaryDto[];
  activeThreadId: string | null;
  open: boolean;
  onToggle: () => void;
  onSelect: (threadId: string) => void;
}) {
  if (threads.length <= 1) {
    return null;
  }

  const activeIndex = threads.findIndex((thread) => thread.id === activeThreadId);
  const activeThread = activeIndex >= 0 ? threads[activeIndex] : threads[0];

  return (
    <div className="relative">
      <button
        type="button"
        className="focus-ring inline-flex h-8 items-center gap-1 rounded-pill border border-hairline px-2.5 text-xs font-semibold text-muted transition-colors hover:border-muted hover:text-ink"
        onClick={onToggle}
      >
        <span className="max-w-24 truncate">
          {formatThreadLabel(activeThread, activeIndex >= 0 ? activeIndex : 0)}
        </span>
        <ChevronDown className="h-3.5 w-3.5" />
      </button>
      {open ? (
        <div className="absolute right-0 top-10 z-10 min-w-48 rounded-note border border-hairline bg-surface p-1 shadow-surface-quiet">
          {threads.map((thread, index) => (
            <button
              key={thread.id}
              type="button"
              className={cn(
                "flex w-full items-center justify-between rounded-[10px] px-3 py-2 text-left text-xs transition-colors hover:bg-reader-paper",
                thread.id === activeThreadId && "bg-reader-paper text-ink",
              )}
              onClick={() => onSelect(thread.id)}
            >
              <span className="truncate">{formatThreadLabel(thread, index)}</span>
              {thread.id === activeThreadId ? (
                <span className="text-[11px] font-semibold text-lens-blue">当前</span>
              ) : null}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function MessageBubble({
  message,
  currentRecordId,
  pendingActionId,
  onConfirmAction,
  onJumpToSentence,
}: {
  message: ReaderAskMessageDto;
  currentRecordId: string;
  pendingActionId: string | null;
  onConfirmAction: (actionId: string, confirmed: boolean) => void;
  onJumpToSentence?: (sentenceId: string) => void;
}) {
  const isAssistant = message.role === "assistant";

  return (
    <div className={cn("flex flex-col gap-2", isAssistant ? "items-start" : "items-end")}>
      {message.context_anchors.length > 0 ? <AnchorChips anchors={message.context_anchors} /> : null}
      <ChatMessage className={cn("w-full", isAssistant ? "justify-start" : "justify-end")}>
        {isAssistant ? (
          <div className="max-w-[92%] rounded-[18px] border border-hairline bg-surface px-4 py-3 text-sm leading-6 text-ink-soft shadow-surface-quiet">
            <Markdown className="prose prose-sm max-w-none text-ink-soft prose-p:mb-3 prose-p:last:mb-0 prose-strong:text-ink prose-code:rounded prose-code:bg-reader-paper prose-code:px-1.5 prose-code:py-0.5 prose-code:text-[0.85em] prose-code:text-ink-soft">
              {message.content_md || "…"}
            </Markdown>
          </div>
        ) : (
          <MessageContent className="max-w-[92%] rounded-[18px] bg-ink px-4 py-3 text-sm leading-6 text-surface shadow-surface-quiet">
            {message.content_md}
          </MessageContent>
        )}
      </ChatMessage>
      {isAssistant ? (
        <>
          <CitationList
            citations={message.citations}
            currentRecordId={currentRecordId}
            onJumpToSentence={onJumpToSentence}
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
        </>
      ) : null}
    </div>
  );
}

export interface AiWorkspacePanelProps {
  open: boolean;
  recordId: string;
  recordTitle?: string | null;
  activeSentence: SentenceModel | null;
  draftAnchors: ReaderAskAnchorRefDto[];
  readerFocus: ReaderAskReaderFocusDto | null;
  hideLauncherOnMobile?: boolean;
  hideLauncherInCompactLayout?: boolean;
  onRemoveDraftAnchor: (index: number) => void;
  onClearDraftAnchors: () => void;
  onJumpToSentence?: (sentenceId: string) => void;
  onToggle: () => void;
}

export function AiWorkspacePanel({
  open,
  recordId,
  recordTitle,
  activeSentence,
  draftAnchors,
  readerFocus,
  hideLauncherOnMobile = false,
  hideLauncherInCompactLayout = false,
  onRemoveDraftAnchor,
  onClearDraftAnchors,
  onJumpToSentence,
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
  const [threadMenuOpen, setThreadMenuOpen] = useState(false);
  const hydrationRef = useRef(0);

  const activeThread = useMemo(
    () => threads.find((item) => item.id === activeThreadId) ?? null,
    [activeThreadId, threads],
  );

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

  async function handleNewChat() {
    setErrorMessage(null);
    setLoading(true);
    try {
      const createdThread = await createThread("new_chat", "New chat");
      const nextThreads = [createdThread, ...threads];
      await loadThread(createdThread.id, nextThreads);
      setThreadMenuOpen(false);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "新对话创建失败。");
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
      const payload = await fetchJson<{ status?: ReaderAskActionStatusDto }>(
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
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "动作确认失败。");
    } finally {
      setPendingActionId(null);
    }
  }

  async function handleSend() {
    const content = composer.trim();
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

    const usedAnchors = [...draftAnchors];
    const now = Date.now();
    const tempUserId = `local-user-${now}`;
    const tempAssistantId = `local-assistant-${now}`;
    const userMessage: ReaderAskMessageDto = {
      id: tempUserId,
      thread_id: threadId,
      role: "user",
      status: "completed",
      content_md: content,
      context_anchors: usedAnchors,
      citations: [],
      action_proposals: [],
      tool_trace: [],
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
      usage_event_id: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };

    setComposer("");
    onClearDraftAnchors();
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
      const response = await fetch(`/api/web/reader-ask/threads/${threadId}/messages/stream`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          content,
          anchors: usedAnchors,
          reader_focus: readerFocus,
        }),
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
                    citations: payload.citations,
                    action_proposals: payload.action_proposals,
                    tool_trace: payload.tool_trace,
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

  if (!open) {
    return (
      <button
        type="button"
        className={`focus-ring fixed bottom-[5.25rem] right-4 z-40 min-h-12 items-center gap-2 rounded-pill border border-hairline bg-surface/96 px-4 text-sm font-semibold text-ink shadow-surface-quiet backdrop-blur-sm transition-colors hover:border-muted md:bottom-6 md:right-6 ${launcherVisibilityClass}`}
        onClick={onToggle}
        aria-label="打开 AI 工作区"
      >
        <MessageSquare aria-hidden="true" className="h-4 w-4 text-lens-blue" />
        <span>Ask Claread</span>
      </button>
    );
  }

  return (
    <aside className="fixed inset-x-3 bottom-3 z-50 flex max-h-[82vh] flex-col overflow-hidden rounded-panel border border-hairline bg-surface shadow-[0_24px_80px_rgba(17,17,17,0.16)] 2xl:inset-y-3 2xl:left-auto 2xl:right-3 2xl:w-[clamp(31rem,calc((100vw-124px-96ch)/2-0.5rem),37.5rem)] 2xl:min-w-0 2xl:max-h-none">
      <div className="border-b border-hairline px-5 py-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-base font-semibold text-ink">Ask Claread</h2>
            <p className="mt-1 text-xs leading-5 text-muted">
              围绕当前文章继续提问，只有明确问到历史时才会扩展资产检索。
            </p>
          </div>
          <div className="flex items-center gap-2">
            <ThreadSwitcher
              threads={threads}
              activeThreadId={activeThreadId}
              open={threadMenuOpen}
              onToggle={() => setThreadMenuOpen((value) => !value)}
              onSelect={(threadId) => {
                setThreadMenuOpen(false);
                setLoading(true);
                setErrorMessage(null);
                void loadThread(threadId)
                  .catch((error) => {
                    setErrorMessage(error instanceof Error ? error.message : "Ask Claread 加载失败。");
                  })
                  .finally(() => {
                    setLoading(false);
                  });
              }}
            />
            <button
              type="button"
              className="focus-ring inline-flex h-9 items-center gap-1 rounded-pill border border-hairline px-3 text-xs font-semibold text-muted transition-colors hover:border-muted hover:text-ink"
              onClick={handleNewChat}
            >
              <Plus className="h-3.5 w-3.5" />
              <span>New chat</span>
            </button>
            <button
              type="button"
              className="focus-ring inline-flex size-10 shrink-0 items-center justify-center rounded-full border border-hairline bg-reader-paper text-muted transition-colors hover:border-muted hover:text-ink"
              onClick={onToggle}
              aria-label="收起 AI 工作区"
            >
              <X aria-hidden="true" className="h-4 w-4" />
            </button>
          </div>
        </div>
        <div className="mt-3 flex items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="truncate text-xs font-semibold text-muted">{recordTitle || "当前文章"}</p>
            <p className="mt-1 text-xs leading-5 text-subtle">
              {activeSentence?.text
                ? "当前句焦点已就绪，可直接追问或附加到输入框。"
                : "可从选区、解析卡片或右侧输入框发起。"}
            </p>
          </div>
          {activeThread ? (
            <span className="rounded-pill border border-hairline bg-reader-paper px-2.5 py-1 text-[11px] font-semibold text-muted">
              {activeThread.is_default ? "当前会话" : "新会话"}
            </span>
          ) : null}
        </div>
      </div>

      <div className="min-h-0 flex-1 px-5 py-4">
        {loading ? (
          <div className="flex h-full items-center justify-center">
            <LoaderCircle className="h-5 w-5 animate-spin text-lens-blue" />
          </div>
        ) : messages.length > 0 ? (
          <ChatContainerRoot className="min-h-0 h-full w-full">
            <ChatContainerContent className="gap-5 pr-1">
              {messages.map((message) => (
                <MessageBubble
                  key={message.id}
                  message={message}
                  currentRecordId={recordId}
                  pendingActionId={pendingActionId}
                  onConfirmAction={handleConfirmAction}
                  onJumpToSentence={onJumpToSentence}
                />
              ))}
              <ChatContainerScrollAnchor />
            </ChatContainerContent>
          </ChatContainerRoot>
        ) : (
          <div className="flex h-full items-center justify-center py-10">
            <div className="max-w-72 text-center">
              <MessageSquare aria-hidden="true" className="mx-auto h-6 w-6 text-lens-blue/75" />
              <p className="mt-3 text-sm font-semibold text-ink">从阅读阻塞点开始问</p>
              <p className="mt-2 text-sm leading-6 text-muted">
                选中一句、加入解析卡片或直接输入问题。Ask Claread 会优先围绕这篇文章回答，而不是展开成通用聊天。
              </p>
            </div>
          </div>
        )}
      </div>

      <div className="border-t border-hairline px-5 py-4">
        {errorMessage ? (
          <div className="mb-3 rounded-note border border-destructive/20 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {errorMessage}
          </div>
        ) : null}

        {draftAnchors.length > 0 ? (
          <div className="mb-3 rounded-note border border-hairline bg-reader-paper px-3 py-2">
            <div className="mb-2 flex items-center justify-between gap-3">
              <p className="text-xs font-semibold text-muted">已附加上下文</p>
              <button
                type="button"
                className="focus-ring text-[11px] font-semibold text-muted transition-colors hover:text-ink"
                onClick={onClearDraftAnchors}
              >
                清空
              </button>
            </div>
            <AnchorChips anchors={draftAnchors} removable onRemove={onRemoveDraftAnchor} />
          </div>
        ) : null}

        <PromptInput
          value={composer}
          onValueChange={setComposer}
          onSubmit={handleSend}
          isLoading={sending}
          maxHeight={220}
          className="rounded-[18px] border-hairline bg-reader-paper px-3 py-3 shadow-none"
        >
          <PromptInputTextarea placeholder="继续问这句什么意思、为什么这样写，或结合本文上下文追问。" />
          <div className="mt-3 flex items-end justify-between gap-3">
            <p className="text-[11px] leading-5 text-muted">
              默认只围绕当前文章；问到“以前/之前/见过”时才会扩展历史资产。
            </p>
            <PromptInputActions className="shrink-0">
              <button
                type="button"
                className="focus-ring inline-flex size-10 items-center justify-center rounded-full bg-ink text-surface disabled:opacity-40"
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
