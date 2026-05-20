/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReaderAskAttachment, ReaderAskPageIdentity } from "@/lib/reader-plate";
import { AiWorkspacePanel } from "./AiWorkspacePanel";

const completedPayload = {
  id: "msg-assistant-1",
  thread_id: "thread-1",
  content_md: "解释完成。",
  resolved_intent: "explain",
  citations: [],
  action_proposals: [],
  tool_trace: [],
  evidence: [
    {
      kind: "resolved_reference",
      label: "Climate Policy",
      detail: "已命中历史文章“Climate Policy”。",
      scope: "external_record",
      record_id: "record-2",
      record_title: "Climate Policy",
      source_article_title: "Climate Policy",
      reason: "known_reference_resolved",
      target_key: null,
      metadata_json: { query: "Climate Policy" },
    },
  ],
  trace_summary: {
    planner_mode: "known_reference_resolved",
    reference_resolution_status: "resolved",
    working_set_mode: "known_reference",
    used_known_reference_resolution: true,
    used_external_record_context: true,
    history_lookup_allowed: true,
    history_lookup_used: false,
    tool_steps: [],
    notes: ["已命中历史文章。"],
  },
  response_cards: [],
  resolved_context: {
    record_id: "record-1",
    record_title: "Test Reader",
    anchor_count: 0,
    explicit_attachment_count: 1,
    used_history_lookup: true,
    current_sentence_used: false,
    current_paragraph_used: false,
    used_record_assets: false,
    used_dictionary: false,
    source_labels: ["current_record", "history_assets"],
  },
  context_plan: {
    entry_action: "ask_about_this",
    explicit_attachment_count: 1,
    normalized_anchor_count: 0,
    primary_anchor_type: null,
    reference_query: "Climate Policy",
    reference_resolution_attempted: true,
    reference_resolution_status: "resolved",
    reference_resolution_reason: "已命中历史文章“Climate Policy”。",
    expanded_record_ids: ["record-2"],
    used_history_lookup: true,
    history_lookup_reason: "known_reference_resolved",
    used_record_context: false,
    record_context_reason: null,
    used_record_insights: false,
    record_insights_reason: null,
    used_article_overview: false,
    article_overview_reason: null,
    used_dictionary: false,
    dictionary_reason: null,
    clarification_reason: null,
    source_labels: ["current_record", "history_assets"],
  },
  resolved_context_input: {
    page_identity: {
      record_id: "record-1",
      title: "Test Reader",
      surface: "reader",
      source: "reader_2_0",
      available_context_capabilities: ["record_context"],
      has_article_overview: true,
      has_sentence_entries: true,
      has_annotations: true,
      has_user_assets: true,
    },
    entry_action: "ask_about_this",
    attachments: [],
    normalized_anchors: [],
    current_record_context: {
      record_id: "record-1",
      record_title: "Test Reader",
      local_context: null,
      record_insights: [],
      article_overview: null,
      source_labels: [],
    },
    external_record_contexts: [
      {
        record_id: "record-2",
        record_title: "Climate Policy",
        article_overview: "这篇文章讨论气候政策如何塑造制度解释。",
        source_labels: ["external_record"],
        reason: "known_reference_resolved",
      },
    ],
  },
  run_info: null,
  supplement_candidates: [],
};

vi.mock("@/components/ui/message", () => ({
  Message: ({ children, className }: { children: React.ReactNode; className?: string }) => (
    <div className={className}>{children}</div>
  ),
  MessageContent: ({
    children,
    className,
  }: {
    children: React.ReactNode;
    className?: string;
  }) => <div className={className}>{children}</div>,
}));

vi.mock("./ask/sse", () => ({
  consumeReaderAskSse: vi.fn(async (_response: Response, onEvent: (event: { event: string; data: Record<string, unknown> }) => void) => {
    onEvent({ event: "message.started", data: { message_id: "msg-assistant-1" } });
    onEvent({
      event: "message.completed",
      data: completedPayload,
    });
  }),
}));

const pageIdentity: ReaderAskPageIdentity = {
  recordId: "record-1",
  recordTitle: "Test Reader",
  surface: "reader",
  source: "reader_2_0",
};

const attachment: ReaderAskAttachment = {
  kind: "record_ref",
  subtype: "current_record",
  label: "当前文章",
  metadata: {
    pageIdentity,
    sourceSurface: "ask_panel",
    entryAction: "ask_about_this",
  },
};

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function mockFetch() {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/web/reader-ask/threads?recordId=")) {
      return jsonResponse({
        items: [
          {
            id: "thread-1",
            record_id: "record-1",
            title: "Ask Claread",
            is_default: true,
            archived_at: null,
            created_at: "2026-05-20T00:00:00Z",
            updated_at: "2026-05-20T00:00:00Z",
            last_message_at: null,
          },
        ],
      });
    }
    if (url.endsWith("/api/web/reader-ask/threads/thread-1")) {
      return jsonResponse({
        id: "thread-1",
        record_id: "record-1",
        title: "Ask Claread",
        is_default: true,
        archived_at: null,
        created_at: "2026-05-20T00:00:00Z",
        updated_at: "2026-05-20T00:00:00Z",
        last_message_at: null,
        messages: [],
      });
    }
    if (url.endsWith("/api/web/reader-ask/threads/thread-1/reset")) {
      return jsonResponse({
        id: "thread-1",
        record_id: "record-1",
        title: "Ask Claread",
        is_default: true,
        archived_at: null,
        created_at: "2026-05-20T00:00:00Z",
        updated_at: "2026-05-20T00:00:00Z",
        last_message_at: null,
        messages: [],
      });
    }
    if (url.includes("/api/web/reader-ask/context-records?")) {
      return jsonResponse({
        items: [
          {
            record_id: "record-2",
            title: "Climate Policy",
            updated_at: "2026-05-20T00:00:00Z",
          },
        ],
      });
    }
    if (url.endsWith("/api/web/reader-ask/threads/thread-1/messages/stream")) {
      return new Response("", {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      });
    }
    throw new Error(`Unexpected fetch: ${url} ${init?.method ?? "GET"}`);
  });
}

describe("AiWorkspacePanel", () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.stubGlobal("fetch", mockFetch());
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
    HTMLElement.prototype.scrollIntoView = vi.fn();
  });

  it("removes legacy thread and task-mode controls from the Ask surface", async () => {
    render(
      <AiWorkspacePanel
        open
        pageIdentity={pageIdentity}
        recordId="record-1"
        recordTitle="Test Reader"
        activeSentence={null}
        attachments={[]}
        onRemoveAttachment={vi.fn()}
        onClearAttachments={vi.fn()}
        onToggle={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalled();
    });

    expect(screen.queryByText("新对话")).toBeNull();
    expect(screen.queryByText("当前对话")).toBeNull();
    expect(screen.queryByText("当前讲解方式")).toBeNull();
  });

  it("sends only the V2 reader ask request shape", async () => {
    render(
      <AiWorkspacePanel
        open
        pageIdentity={pageIdentity}
        recordId="record-1"
        recordTitle="Test Reader"
        activeSentence={null}
        attachments={[attachment]}
        onRemoveAttachment={vi.fn()}
        onClearAttachments={vi.fn()}
        onToggle={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalled();
    });

    fireEvent.change(screen.getByPlaceholderText("继续围绕当前文章、句子、译文或解析对象提问。"), {
      target: { value: "解释这篇文章的核心论点" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      const calls = vi.mocked(global.fetch).mock.calls;
      expect(calls.some(([url]) => String(url).includes("/messages/stream"))).toBe(true);
    });

    const streamCall = vi
      .mocked(global.fetch)
      .mock.calls.find(([url]) => String(url).includes("/messages/stream"));
    const body = JSON.parse(String(streamCall?.[1]?.body)) as Record<string, unknown>;

    expect(body).toMatchObject({
      content: "解释这篇文章的核心论点",
      entry_action: "ask_about_this",
      page_identity: {
        record_id: "record-1",
        title: "Test Reader",
        surface: "reader",
        source: "reader_2_0",
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
      },
    });
    expect(Object.keys(body.page_identity as Record<string, unknown>)).toEqual([
      "record_id",
      "title",
      "surface",
      "source",
      "available_context_capabilities",
      "has_article_overview",
      "has_sentence_entries",
      "has_annotations",
      "has_user_assets",
    ]);
    expect(Array.isArray(body.attachments)).toBe(true);
    expect(body).not.toHaveProperty("task_mode");
    expect(body).not.toHaveProperty("anchors");
    expect(body).not.toHaveProperty("reader_focus");
  });

  it("resets the active conversation and clears attachments", async () => {
    const onClearAttachments = vi.fn();

    render(
      <AiWorkspacePanel
        open
        pageIdentity={pageIdentity}
        recordId="record-1"
        recordTitle="Test Reader"
        activeSentence={null}
        attachments={[attachment]}
        onRemoveAttachment={vi.fn()}
        onClearAttachments={onClearAttachments}
        onToggle={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalled();
    });

    fireEvent.click(screen.getByRole("button", { name: "重新开始" }));

    await waitFor(() => {
      expect(onClearAttachments).toHaveBeenCalled();
    });
    expect(
      vi
        .mocked(global.fetch)
        .mock.calls.some(([url]) => String(url).endsWith("/api/web/reader-ask/threads/thread-1/reset")),
    ).toBe(true);
  });

  it("renders evidence and planner summary from the completed payload", async () => {
    render(
      <AiWorkspacePanel
        open
        pageIdentity={pageIdentity}
        recordId="record-1"
        recordTitle="Test Reader"
        activeSentence={null}
        attachments={[attachment]}
        onRemoveAttachment={vi.fn()}
        onClearAttachments={vi.fn()}
        onToggle={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalled();
    });

    fireEvent.change(screen.getByPlaceholderText("继续围绕当前文章、句子、译文或解析对象提问。"), {
      target: { value: "我之前那篇 climate policy 的解析里也提过这个吗？" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      expect(screen.getByText("证据")).not.toBeNull();
    });

    expect(screen.getAllByText("Climate Policy").length).toBeGreaterThan(0);
    expect(screen.getByText("外部文章")).not.toBeNull();
    expect(screen.getByText("自动命中")).not.toBeNull();
    expect(screen.getByText("规划摘要")).not.toBeNull();
    expect(screen.getByText("已命中历史文章。")).not.toBeNull();
  });

  it("searches and appends a related record attachment from the context picker", async () => {
    const onAppendAttachments = vi.fn();

    render(
      <AiWorkspacePanel
        open
        pageIdentity={pageIdentity}
        recordId="record-1"
        recordTitle="Test Reader"
        activeSentence={null}
        attachments={[]}
        onAppendAttachments={onAppendAttachments}
        onRemoveAttachment={vi.fn()}
        onClearAttachments={vi.fn()}
        onToggle={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalled();
    });

    fireEvent.click(screen.getByRole("button", { name: "上下文" }));
    fireEvent.change(screen.getByPlaceholderText("输入文章标题，例如 climate policy"), {
      target: { value: "climate policy" },
    });

    await waitFor(() => {
      expect(screen.getAllByText("Climate Policy").length).toBeGreaterThan(0);
    });

    fireEvent.click(screen.getByRole("button", { name: /加入上下文/i }));

    await waitFor(() => {
      expect(onAppendAttachments).toHaveBeenCalledTimes(1);
    });
    expect(onAppendAttachments.mock.calls[0]?.[0]).toMatchObject([
      {
        kind: "record_ref",
        subtype: "related_record",
        label: "Climate Policy",
        targetKey: "record:record-2:record",
        metadata: {
          sourceSurface: "ask_context_picker",
          assetId: "record-2",
          title: "Climate Policy",
        },
      },
    ]);
  });
});
