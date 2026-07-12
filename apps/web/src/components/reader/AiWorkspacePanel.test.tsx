/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReaderAskAttachment, ReaderAskPageIdentity } from "@/lib/reader-plate";
import type {
  ReaderAskArticleRagCitationDto,
  ReaderAskArticleRagSidecarSafeDto,
  ReaderAskUiMessageDto,
} from "@/types/api/reader-ask";
import { consumeReaderAskSse } from "./ask/sse";
import {
  AiWorkspacePanel,
  createSseMessageHandler,
  type AiWorkspacePanelProps,
} from "./AiWorkspacePanel";

const completedPayload = {
  id: "msg-assistant-1",
  thread_id: "thread-1",
  content_md: "解释完成。",
  submission_mode: "chat" as const,
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
      reason: "structured_asset_lookup",
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
    used_structured_asset_lookup: true,
    used_hitp_disambiguation: false,
    used_external_asset_context: false,
    used_external_asset_disambiguation: false,
    supplement_generation_used: false,
    supplement_persisted_count: 0,
    supplement_deleted_count: 0,
    cross_record_context_allowed: true,
    cross_record_context_used: false,
    tool_steps: [],
    notes: ["已命中历史文章。"],
  },
  response_cards: [],
  resolved_context: {
    record_id: "record-1",
    record_title: "Test Reader",
    anchor_count: 0,
    explicit_attachment_count: 1,
    used_cross_record_context: true,
    current_sentence_used: false,
    current_paragraph_used: false,
    used_record_insights: false,
    used_dictionary: false,
    source_labels: ["current_record", "external_record_context"],
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
    used_cross_record_context: true,
    cross_record_context_reason: "known_reference_resolved",
    used_record_context: false,
    record_context_reason: null,
    used_record_insights: false,
    record_insights_reason: null,
    used_article_overview: false,
    article_overview_reason: null,
    used_dictionary: false,
    dictionary_reason: null,
    external_record_context_reason: "external_record_context_loaded",
    structured_asset_lookup_reason: "external_record_stable_assets_loaded",
    clarification_reason: null,
    source_labels: ["current_record", "external_record_context"],
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
      has_reader_notes: true,
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
        record_insights: ["主干分析: 先交代制度背景。"],
        source_labels: ["external_record"],
        reason: "known_reference_resolved",
      },
    ],
    external_asset_contexts: [],
  },
  run_info: null,
  supplement_candidates: [],
  persisted_supplements: [],
  usage_event_id: "usage-1",
  disambiguation: null,
  external_asset_disambiguation: null,
};

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
  availableContextCapabilities: [
    "record_context",
    "record_insights",
    "reader_annotations",
    "reader_notes",
    "dictionary",
  ],
  hasArticleOverview: true,
  hasSentenceEntries: true,
  hasAnnotations: true,
  hasReaderNotes: true,
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

const sentenceAttachment: ReaderAskAttachment = {
  kind: "text_selection",
  subtype: "sentence",
  label: "整句",
  selectedText: "Climate change presents an existential challenge.",
  metadata: {
    pageIdentity,
    sourceSurface: "reader_live_selection",
    entryAction: "explain_this",
    sentenceId: "s1",
    paragraphId: "p1",
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
    const requestUrl = new URL(String(input), "http://localhost");
    if (requestUrl.pathname === "/api/web/reader-ask/model-options") {
      return jsonResponse({
        default_key: "ask-clarity",
        items: [
          {
            key: "ask-clarity",
            label: "Qwen 3.7 Max",
            description: "适合带 reasoning 的 Ask 问答。",
            model_name: "qwen3.7-max",
            replan_model_name: "qwen3.7-max",
            price_multiplier: 1,
            is_default: true,
          },
          {
            key: "ask-fast",
            label: "DeepSeek Chat",
            description: "更快的直接回答。",
            model_name: "deepseek-chat",
            replan_model_name: "deepseek-chat",
            price_multiplier: 0.8,
            is_default: false,
          },
        ],
      });
    }
    if (
      requestUrl.pathname === "/api/web/reader-ask/threads" &&
      requestUrl.searchParams.get("record_id")
    ) {
      return jsonResponse({
        items: [
          {
            id: "thread-1",
            record_id: "record-1",
            title: "Ask Claread",
            is_default: true,
            selected_model: {
              key: "ask-clarity",
              label: "Qwen 3.7 Max",
              description: "适合带 reasoning 的 Ask 问答。",
              model_name: "qwen3.7-max",
              replan_model_name: "qwen3.7-max",
              price_multiplier: 1,
            },
            archived_at: null,
            created_at: "2026-05-20T00:00:00Z",
            updated_at: "2026-05-20T00:00:00Z",
            last_message_at: null,
          },
        ],
      });
    }
    if (requestUrl.pathname === "/api/web/reader-ask/threads/thread-1") {
      return jsonResponse({
        id: "thread-1",
        record_id: "record-1",
        title: "Ask Claread",
        is_default: true,
        selected_model: {
          key: "ask-clarity",
          label: "Qwen 3.7 Max",
          description: "适合带 reasoning 的 Ask 问答。",
          model_name: "qwen3.7-max",
          replan_model_name: "qwen3.7-max",
          price_multiplier: 1,
        },
        archived_at: null,
        created_at: "2026-05-20T00:00:00Z",
        updated_at: "2026-05-20T00:00:00Z",
        last_message_at: null,
        messages: [],
      });
    }
    if (requestUrl.pathname === "/api/web/reader-ask/threads/thread-1/reset") {
      return jsonResponse({
        id: "thread-1",
        record_id: "record-1",
        title: "Ask Claread",
        is_default: true,
        selected_model: {
          key: "ask-clarity",
          label: "Qwen 3.7 Max",
          description: "适合带 reasoning 的 Ask 问答。",
          model_name: "qwen3.7-max",
          replan_model_name: "qwen3.7-max",
          price_multiplier: 1,
        },
        archived_at: null,
        created_at: "2026-05-20T00:00:00Z",
        updated_at: "2026-05-20T00:00:00Z",
        last_message_at: null,
        messages: [],
      });
    }
    if (
      requestUrl.pathname ===
      "/api/web/reader-ask/threads/thread-1/actions/act-supplement-1/confirm"
    ) {
      return jsonResponse({
        ok: true,
        action_id: "act-supplement-1",
        status: "executed",
        result: {
          record_id: "record-1",
          supplement_projection: {
            id: "entry-supplement-1",
            sentence_id: "s1",
            entry_type: "grammar_note",
            title: "AI 语法旁注",
            content: "这里用了让步从句。",
            source_kind: "ask_supplement",
            supplement_id: "supp-1",
            deletable: true,
            created_from_turn_run_id: "run-1",
          },
          persisted_supplement: {
            supplement_id: "supp-1",
            supplement_type: "grammar_note",
            lifecycle_status: "persisted",
            record_id: "record-1",
            record_title: "Test Reader",
            target_key: "record:record-1:sentence:s1",
            sentence_id: "s1",
            paragraph_id: "p1",
            title: "AI 语法旁注",
            content: "这里用了让步从句。",
            source_kind: "assistant_supplement",
            schema_version: "1.0",
            created_from_turn_run_id: "run-1",
            created_at: "2026-05-20T00:00:00Z",
          },
        },
      });
    }
    if (requestUrl.pathname === "/api/web/reader-ask/supplements/supp-1") {
      return jsonResponse({
        deleted: true,
        supplement_id: "supp-1",
        record_id: "record-1",
        target_key: "record:record-1:sentence:s1",
        lifecycle_status: "deleted",
        persisted_supplement: {
          supplement_id: "supp-1",
          supplement_type: "grammar_note",
          lifecycle_status: "deleted",
          record_id: "record-1",
          record_title: "Test Reader",
          target_key: "record:record-1:sentence:s1",
          sentence_id: "s1",
          paragraph_id: "p1",
          title: "AI 语法旁注",
          content: "这里用了让步从句。",
          source_kind: "assistant_supplement",
          schema_version: "1.0",
          created_from_turn_run_id: "run-1",
          created_at: "2026-05-20T00:00:00Z",
        },
      });
    }
    if (requestUrl.pathname === "/api/web/reader-ask/context-records") {
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
    if (requestUrl.pathname === "/api/web/reader-ask/threads/thread-1/messages/stream") {
      return new Response("", {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      });
    }
    throw new Error(
      `Unexpected fetch: ${requestUrl.pathname}${requestUrl.search} ${init?.method ?? "GET"}`,
    );
  });
}

function createAssistantMessage(overrides: Partial<ReaderAskUiMessageDto> = {}): ReaderAskUiMessageDto {
  return {
    id: "msg-assistant-1",
    thread_id: "thread-1",
    role: "assistant",
    status: "completed",
    content_md: "Here is the answer.",
    submission_mode: "chat",
    resolved_intent: "explain",
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
    context_plan: null,
    resolved_context_input: null,
    run_info: null,
    supplement_candidates: [],
    persisted_supplements: [],
    reasoning_md: null,
    reasoning_status: null,
    replan_status: "idle",
    regenerate_preview: false,
    usage_event_id: null,
    created_at: "2026-05-20T00:00:00Z",
    updated_at: "2026-05-20T00:00:00Z",
    ...overrides,
  };
}

function mockThreadMessages(messages: ReaderAskUiMessageDto[]) {
  vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/api/web/reader-ask/threads/thread-1")) {
      return jsonResponse({
        id: "thread-1",
        record_id: "record-1",
        title: "Ask Claread",
        is_default: true,
        selected_model: {
          key: "ask-clarity",
          label: "Qwen 3.7 Max",
          description: "适合带 reasoning 的 Ask 问答。",
          model_name: "qwen3.7-max",
          replan_model_name: "qwen3.7-max",
          price_multiplier: 1,
        },
        archived_at: null,
        created_at: "2026-05-20T00:00:00Z",
        updated_at: "2026-05-20T00:00:00Z",
        last_message_at: null,
        messages,
      });
    }
    return mockFetch()(input, init);
  });
}

function renderPanel(overrides: Partial<AiWorkspacePanelProps> = {}) {
  const props: AiWorkspacePanelProps = {
    open: true,
    pageIdentity,
    recordId: "record-1",
    recordTitle: "Test Reader",
    attachments: [],
    onRemoveAttachment: vi.fn(),
    onClearAttachments: vi.fn(),
    onToggle: vi.fn(),
    ...overrides,
  };

  return render(<AiWorkspacePanel {...props} />);
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

  it("uses the Claread AI mark for the closed launcher", () => {
    const onToggle = vi.fn();
    const { container } = renderPanel({ open: false, onToggle });

    const launcher = screen.getByRole("button", { name: "打开 Ask Claread" });
    expect(container.querySelector("[data-claread-ai-mark='true']")).not.toBeNull();
    expect(container.querySelector("[data-claread-ai-mark-badge='true']")).not.toBeNull();

    fireEvent.click(launcher);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("removes legacy thread and task-mode controls from the Ask surface", async () => {
    render(
      <AiWorkspacePanel
        open
        pageIdentity={pageIdentity}
        recordId="record-1"
        recordTitle="Test Reader"
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

  it("uses article-level starter copy when there is no actual ask attachment", async () => {
    render(
      <AiWorkspacePanel
        open
        pageIdentity={pageIdentity}
        recordId="record-1"
        recordTitle="Test Reader"
        attachments={[]}
        onRemoveAttachment={vi.fn()}
        onClearAttachments={vi.fn()}
        onToggle={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalled();
    });

    expect(screen.getByText("从这篇文章开始问")).not.toBeNull();
    expect(screen.getByText("概括这篇文章的核心观点。")).not.toBeNull();
    expect(screen.queryByText("继续追问这句内容")).toBeNull();
  });

  it("switches starter copy to sentence mode only when a sentence attachment is actually present", async () => {
    render(
      <AiWorkspacePanel
        open
        pageIdentity={pageIdentity}
        recordId="record-1"
        recordTitle="Test Reader"
        attachments={[sentenceAttachment]}
        onRemoveAttachment={vi.fn()}
        onClearAttachments={vi.fn()}
        onToggle={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalled();
    });

    expect(screen.getByText("继续追问这句内容")).not.toBeNull();
    expect(screen.getByText("解释这句在这里的意思。")).not.toBeNull();
  });

  it("binds explicit starter entryAction for sentence mode prompts", async () => {
    render(
      <AiWorkspacePanel
        open
        pageIdentity={pageIdentity}
        recordId="record-1"
        recordTitle="Test Reader"
        attachments={[sentenceAttachment]}
        onRemoveAttachment={vi.fn()}
        onClearAttachments={vi.fn()}
        onToggle={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalled();
    });

    fireEvent.click(screen.getByRole("button", { name: "为什么作者这里这样写？" }));

    await waitFor(() => {
      const streamCall = vi
        .mocked(global.fetch)
        .mock.calls.findLast(([url]) => String(url).includes("/messages/stream"));
      expect(streamCall).toBeTruthy();
    });

    const streamCall = vi
      .mocked(global.fetch)
      .mock.calls.findLast(([url]) => String(url).includes("/messages/stream"));
    const body = JSON.parse(String(streamCall?.[1]?.body)) as Record<string, unknown>;
    expect(body.entry_action).toBe("why_here");
  });

  it("sends only the current reader ask request shape", async () => {
    const onClearAttachments = vi.fn();
    render(
      <AiWorkspacePanel
        open
        pageIdentity={pageIdentity}
        recordId="record-1"
        recordTitle="Test Reader"
        attachments={[attachment]}
        onRemoveAttachment={vi.fn()}
        onClearAttachments={onClearAttachments}
        onToggle={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalled();
    });

    fireEvent.change(screen.getByPlaceholderText("继续问这篇文章…"), {
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
          "reader_annotations",
          "reader_notes",
          "dictionary",
        ],
        has_article_overview: true,
        has_sentence_entries: true,
        has_annotations: true,
        has_reader_notes: true,
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
      "has_reader_notes",
    ]);
    expect(Array.isArray(body.attachments)).toBe(true);
    expect(body).not.toHaveProperty("task_mode");
    expect(body).not.toHaveProperty("anchors");
    expect(body).not.toHaveProperty("reader_focus");
    expect(onClearAttachments).toHaveBeenCalledTimes(1);
  });

  it("serializes page identity from current reader facts instead of hardcoded capability flags", async () => {
    render(
      <AiWorkspacePanel
        open
        pageIdentity={{
          recordId: "record-1",
          recordTitle: "Test Reader",
          surface: "reader",
          source: "reader_2_0",
          availableContextCapabilities: ["record_context", "dictionary"],
          hasArticleOverview: false,
          hasSentenceEntries: false,
          hasAnnotations: false,
          hasReaderNotes: false,
        }}
        recordId="record-1"
        recordTitle="Test Reader"
        attachments={[attachment]}
        onRemoveAttachment={vi.fn()}
        onClearAttachments={vi.fn()}
        onToggle={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalled();
    });

    fireEvent.change(screen.getByPlaceholderText("继续问这篇文章…"), {
      target: { value: "概括一下这篇文章" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      const calls = vi.mocked(global.fetch).mock.calls;
      expect(calls.some(([url]) => String(url).includes("/messages/stream"))).toBe(true);
    });

    const streamCall = vi
      .mocked(global.fetch)
      .mock.calls.findLast(([url]) => String(url).includes("/messages/stream"));
    const body = JSON.parse(String(streamCall?.[1]?.body)) as Record<string, unknown>;

    expect(body.page_identity).toMatchObject({
      available_context_capabilities: ["record_context", "dictionary"],
      has_article_overview: false,
      has_sentence_entries: false,
      has_annotations: false,
      has_reader_notes: false,
    });
  });

  it("does not inherit the selection attachment entryAction for normal composer sends", async () => {
    render(
      <AiWorkspacePanel
        open
        pageIdentity={pageIdentity}
        recordId="record-1"
        recordTitle="Test Reader"
        attachments={[sentenceAttachment]}
        onRemoveAttachment={vi.fn()}
        onClearAttachments={vi.fn()}
        onToggle={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalled();
    });

    fireEvent.change(screen.getByPlaceholderText("继续问这篇文章…"), {
      target: { value: "我想问这句和全文主题的关系" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      const streamCall = vi
        .mocked(global.fetch)
        .mock.calls.findLast(([url]) => String(url).includes("/messages/stream"));
      expect(streamCall).toBeTruthy();
    });

    const streamCall = vi
      .mocked(global.fetch)
      .mock.calls.findLast(([url]) => String(url).includes("/messages/stream"));
    const body = JSON.parse(String(streamCall?.[1]?.body)) as Record<string, unknown>;
    expect(body.entry_action).toBe("ask_about_this");
  });

  it("resets the active conversation and clears attachments", async () => {
    const onClearAttachments = vi.fn();

    render(
      <AiWorkspacePanel
        open
        pageIdentity={pageIdentity}
        recordId="record-1"
        recordTitle="Test Reader"
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

  it("keeps debug disclosures out of the default assistant surface while preserving source disclosures", async () => {
    render(
      <AiWorkspacePanel
        open
        pageIdentity={pageIdentity}
        recordId="record-1"
        recordTitle="Test Reader"
        attachments={[attachment]}
        onRemoveAttachment={vi.fn()}
        onClearAttachments={vi.fn()}
        onToggle={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalled();
    });

    fireEvent.change(screen.getByPlaceholderText("继续问这篇文章…"), {
      target: { value: "我之前那篇 climate policy 的解析里也提过这个吗？" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      expect(screen.getByText("解释完成。")).not.toBeNull();
    });

    expect(screen.queryByText("引用与来源")).toBeNull();
    expect(screen.queryByRole("button", { name: /证据/i })).toBeNull();
    expect(screen.queryByText("上下文策略")).toBeNull();
    expect(screen.queryByRole("button", { name: /运行轨迹/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /当前文章上下文/i })).toBeNull();
  });

  it("shows the current page chip and recent related-article search from the add menu", async () => {
    render(
      <AiWorkspacePanel
        open
        pageIdentity={pageIdentity}
        recordId="record-1"
        recordTitle="Test Reader"
        attachments={[]}
        onRemoveAttachment={vi.fn()}
        onClearAttachments={vi.fn()}
        onToggle={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalled();
    });

    expect(screen.getByText("Test Reader")).not.toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "添加其他文章" }));

    // Without forceMount, DropdownMenu content renders asynchronously after
    // the controlled open state updates.
    await waitFor(() => {
      expect(screen.getByPlaceholderText("搜索其他文章")).not.toBeNull();
    });
    expect(screen.getByText("最近文章")).not.toBeNull();
    await waitFor(() => {
      expect(screen.getByText("Climate Policy")).not.toBeNull();
    });
  });

  it("uses RR-scoped thread URLs and skips related-record search in reading_record scope", async () => {
    const rrAttachment: ReaderAskAttachment = {
      ...sentenceAttachment,
      metadata: {
        ...sentenceAttachment.metadata,
        readingRecordAnchor: {
          record_id: "reading-record-1",
          base_id: "base-1",
          generation: 3,
          unit_id: "unit-1",
          anchor_segment_id: "anchor-seg-1",
          scope: "stable_source",
          offset_unit: "utf16",
          start_offset: 0,
          end_offset: 6,
          selected_text: "memory",
          text_hash: "9fd7545a",
          hash_algorithm: "fnv1a32-utf16",
        },
      },
    };

    renderPanel({
      recordId: "reading-record-1",
      recordScope: "reading_record",
      recordTitle: "Reading Record",
      attachments: [rrAttachment],
    });

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalled();
    });

    fireEvent.change(screen.getByPlaceholderText("继续问这篇文章…"), {
      target: { value: "解释这段选中内容" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      const streamCall = vi
        .mocked(global.fetch)
        .mock.calls.findLast(([url]) => String(url).includes("/messages/stream"));
      expect(streamCall).toBeTruthy();
    });

    const calls = vi.mocked(global.fetch).mock.calls;
    expect(
      calls.some(([url]) =>
        String(url).includes(
          "/api/web/reader-ask/threads?record_id=reading-record-1&record_scope=reading_record",
        ),
      ),
    ).toBe(true);
    expect(
      calls.some(([url]) =>
        String(url).includes(
          "/api/web/reader-ask/threads/thread-1?record_id=reading-record-1&record_scope=reading_record",
        ),
      ),
    ).toBe(true);
    expect(
      calls.some(([url]) => String(url).includes("/api/web/reader-ask/context-records")),
    ).toBe(false);

    const streamCall = calls.findLast(([url]) => String(url).includes("/messages/stream"));
    expect(String(streamCall?.[0])).toContain("record_scope=reading_record");
    expect(String(streamCall?.[0])).toContain("record_id=reading-record-1");

    const body = JSON.parse(String(streamCall?.[1]?.body)) as {
      attachments: Array<{ metadata: { reading_record_anchor?: Record<string, unknown> | null } }>;
    };
    expect(body.attachments[0]?.metadata.reading_record_anchor).toMatchObject({
      record_id: "reading-record-1",
      anchor_segment_id: "anchor-seg-1",
    });
  });

  it("renders disambiguation candidate cards and re-sends the current question after selection", async () => {
    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
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
          messages: [
            {
              id: "msg-user-1",
              thread_id: "thread-1",
              role: "user",
              status: "completed",
              content_md: "我之前那篇 climate 文章里呢？",
              resolved_intent: null,
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
              context_plan: null,
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
                  has_reader_notes: true,
                },
                entry_action: "ask_about_this",
                attachments: [],
                normalized_anchors: [],
                current_record_context: null,
                external_record_contexts: [],
                external_asset_contexts: [],
              },
              run_info: null,
              supplement_candidates: [],
              persisted_supplements: [],
              usage_event_id: null,
              created_at: "2026-05-20T00:00:00Z",
              updated_at: "2026-05-20T00:00:00Z",
            },
            {
              id: "msg-assistant-1",
              thread_id: "thread-1",
              role: "assistant",
              status: "completed",
              content_md: "我需要先确认你说的是哪篇文章。",
              resolved_intent: "explain",
              context_anchors: [],
              citations: [],
              action_proposals: [],
              tool_trace: [],
              evidence: [
                {
                  kind: "clarification",
                  label: "引用解析需要补充",
                  detail: "“climate”命中了多个候选，请补充更完整的标题。",
                  scope: "current_record",
                  record_id: null,
                  record_title: null,
                  source_article_title: null,
                  reason: "clarification",
                  target_key: null,
                  metadata_json: {},
                },
              ],
              trace_summary: {
                planner_mode: "needs_local_clarification",
                reference_resolution_status: "ambiguous",
                working_set_mode: "clarification",
                used_known_reference_resolution: false,
                used_external_record_context: false,
                used_structured_asset_lookup: false,
                used_hitp_disambiguation: true,
                used_external_asset_context: false,
                used_external_asset_disambiguation: false,
                supplement_generation_used: false,
                supplement_persisted_count: 0,
                supplement_deleted_count: 0,
                cross_record_context_allowed: false,
                cross_record_context_used: false,
                tool_steps: [],
                notes: [],
              },
              disambiguation: {
                required: true,
                reason: "“climate”命中了多个候选，请选择要并入当前讨论的文章。",
                query: "climate",
                selection_mode: "panel_cards",
                candidates: [
                  {
                    record_id: "record-2",
                    title: "Climate Policy",
                    updated_at: "2026-05-20T00:00:00Z",
                  },
                ],
              },
              external_asset_disambiguation: null,
              response_cards: [],
              resolved_context: null,
              context_plan: null,
              resolved_context_input: null,
              run_info: null,
              supplement_candidates: [],
              persisted_supplements: [],
              usage_event_id: null,
              created_at: "2026-05-20T00:00:00Z",
              updated_at: "2026-05-20T00:00:00Z",
            },
          ],
        });
      }
      return mockFetch()(input, init);
    });

    render(
      <AiWorkspacePanel
        open
        pageIdentity={pageIdentity}
        recordId="record-1"
        recordTitle="Test Reader"
        attachments={[]}
        onRemoveAttachment={vi.fn()}
        onClearAttachments={vi.fn()}
        onToggle={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("候选文章")).not.toBeNull();
    });

    fireEvent.click(screen.getByRole("button", { name: "加入当前讨论" }));

    await waitFor(() => {
      const streamCall = vi
        .mocked(global.fetch)
        .mock.calls.find(([url]) => String(url).includes("/messages/stream"));
      expect(streamCall).toBeTruthy();
      const body = JSON.parse(String(streamCall?.[1]?.body)) as Record<string, unknown>;
      expect(body.content).toBe("我之前那篇 climate 文章里呢？");
      expect(body.attachments).toMatchObject([
        {
          kind: "record_ref",
          subtype: "related_record",
          label: "Climate Policy",
          target_key: "record:record-2:record",
        },
      ]);
    });
  });

  it("keeps persisted supplements in the Ask panel after confirm and supports delete", async () => {
    const onActionExecuted = vi.fn();
    const onSupplementDeleted = vi.fn();

    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
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
          messages: [
            {
              id: "msg-assistant-1",
              thread_id: "thread-1",
              role: "assistant",
              status: "completed",
              content_md: "解释完成。",
              resolved_intent: "grammar",
              context_anchors: [],
              citations: [],
              action_proposals: [
                {
                  id: "act-supplement-1",
                  action_type: "create_supplement_grammar_note",
                  label: "写入语法旁注",
                  description: "把这条解释作为 AI 语法旁注写入当前页。",
                  requires_confirmation: true,
                  status: "pending",
                  payload_json: {
                    candidate: {
                      candidate_id: "cand-1",
                      supplement_type: "grammar_note",
                      lifecycle_status: "candidate",
                      target_key: "record:record-1:sentence:s1",
                      sentence_id: "s1",
                      paragraph_id: "p1",
                      title: "AI 语法旁注",
                      content: "这里用了让步从句。",
                      anchor: {
                        anchor_type: "sentence",
                        sentence_id: "s1",
                        paragraph_id: "p1",
                        target_key: "record:record-1:sentence:s1",
                        label: "句子",
                        selected_text: "Even if he knew the risk",
                        segments: [],
                        payload_json: {},
                      },
                      schema_version: "1.0",
                      created_from_turn_run_id: "run-1",
                      label: "AI 补充语法旁注",
                    },
                  },
                },
              ],
              tool_trace: [],
              evidence: [],
              trace_summary: {
                planner_mode: "direct_answer",
                reference_resolution_status: "not_needed",
                working_set_mode: "anchor_local",
                used_known_reference_resolution: false,
                used_external_record_context: false,
                used_structured_asset_lookup: false,
                used_hitp_disambiguation: false,
                used_external_asset_context: false,
                used_external_asset_disambiguation: false,
                supplement_generation_used: true,
                supplement_persisted_count: 0,
                supplement_deleted_count: 0,
                cross_record_context_allowed: false,
                cross_record_context_used: false,
                tool_steps: [],
                notes: [],
              },
              disambiguation: null,
              external_asset_disambiguation: null,
              response_cards: [],
              resolved_context: null,
              context_plan: null,
              resolved_context_input: null,
              run_info: null,
              supplement_candidates: [
                {
                  candidate_id: "cand-1",
                  supplement_type: "grammar_note",
                  lifecycle_status: "candidate",
                  target_key: "record:record-1:sentence:s1",
                  sentence_id: "s1",
                  paragraph_id: "p1",
                  title: "AI 语法旁注",
                  content: "这里用了让步从句。",
                  anchor: {
                    anchor_type: "sentence",
                    sentence_id: "s1",
                    paragraph_id: "p1",
                    target_key: "record:record-1:sentence:s1",
                    label: "句子",
                    selected_text: "Even if he knew the risk",
                    segments: [],
                    payload_json: {},
                  },
                  schema_version: "1.0",
                  created_from_turn_run_id: "run-1",
                  label: "AI 补充语法旁注",
                },
              ],
              persisted_supplements: [],
              usage_event_id: null,
              created_at: "2026-05-20T00:00:00Z",
              updated_at: "2026-05-20T00:00:00Z",
            },
          ],
        });
      }
      return mockFetch()(input, init);
    });

    render(
      <AiWorkspacePanel
        open
        pageIdentity={pageIdentity}
        recordId="record-1"
        recordTitle="Test Reader"
        attachments={[]}
        onActionExecuted={onActionExecuted}
        onSupplementDeleted={onSupplementDeleted}
        onRemoveAttachment={vi.fn()}
        onClearAttachments={vi.fn()}
        onToggle={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("写入语法旁注")).not.toBeNull();
    });

    expect(screen.queryByText("补充内容")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "确认" }));

    await waitFor(() => {
      expect(screen.getAllByText("已写入当前页").length).toBeGreaterThan(0);
      expect(screen.getByText("已把这条 AI 补充写入当前页。")).not.toBeNull();
    });
    expect(onActionExecuted).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "删除补充" }));

    await waitFor(() => {
      expect(screen.getByText("已从当前页移除这条 AI 补充。")).not.toBeNull();
    });
    expect(onSupplementDeleted).toHaveBeenCalledWith("supp-1");
    expect(screen.queryByRole("button", { name: "删除补充" })).toBeNull();
  });

  it("renders asset disambiguation cards and re-sends the current question after selection", async () => {
    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
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
          messages: [
            {
              id: "msg-user-1",
              thread_id: "thread-1",
              role: "user",
              status: "completed",
              content_md: "我之前那篇 policy 文章的分析里怎么解释这个概念？",
              resolved_intent: null,
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
              context_plan: null,
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
                  has_reader_notes: true,
                },
                entry_action: "ask_about_this",
                attachments: [],
                normalized_anchors: [],
                current_record_context: null,
                external_record_contexts: [],
                external_asset_contexts: [],
              },
              run_info: null,
              supplement_candidates: [],
              persisted_supplements: [],
              usage_event_id: null,
              created_at: "2026-05-20T00:00:00Z",
              updated_at: "2026-05-20T00:00:00Z",
            },
            {
              id: "msg-assistant-1",
              thread_id: "thread-1",
              role: "assistant",
              status: "completed",
              content_md: "我已经定位到那篇文章，但其中有多个稳定资产可能相关，请先选一个并入当前讨论。",
              resolved_intent: "explain",
              context_anchors: [],
              citations: [],
              action_proposals: [],
              tool_trace: [],
              evidence: [
                {
                  kind: "clarification",
                  label: "外部稳定资产需要补充",
                  detail: "我已经定位到那篇文章，但其中有多个稳定资产可能相关，请先选一个并入当前讨论。",
                  scope: "external_record",
                  record_id: "record-2",
                  record_title: "Climate Policy",
                  source_article_title: "Climate Policy",
                  reason: "clarification",
                  target_key: null,
                  metadata_json: {},
                },
              ],
              trace_summary: {
                planner_mode: "needs_local_clarification",
                reference_resolution_status: "resolved",
                working_set_mode: "clarification",
                used_known_reference_resolution: true,
                used_external_record_context: true,
                used_structured_asset_lookup: true,
                used_hitp_disambiguation: false,
                used_external_asset_context: false,
                used_external_asset_disambiguation: true,
                supplement_generation_used: false,
                supplement_persisted_count: 0,
                supplement_deleted_count: 0,
                cross_record_context_allowed: true,
                cross_record_context_used: false,
                tool_steps: [],
                notes: [],
              },
              disambiguation: null,
              external_asset_disambiguation: {
                required: true,
                reason: "我已经定位到那篇文章，但其中有多个稳定资产可能相关，请先选一个并入当前讨论。",
                record_id: "record-2",
                record_title: "Climate Policy",
                candidates: [
                  {
                    asset_type: "analysis",
                    asset_id: "analysis-1",
                    entry_type: "sentence_analysis",
                    title: "Concept analysis",
                    summary: "这张分析卡解释了这个概念如何承接制度背景。",
                  },
                ],
              },
              response_cards: [],
              resolved_context: null,
              context_plan: null,
              resolved_context_input: null,
              run_info: null,
              supplement_candidates: [],
              persisted_supplements: [],
              usage_event_id: null,
              created_at: "2026-05-20T00:00:00Z",
              updated_at: "2026-05-20T00:00:00Z",
            },
          ],
        });
      }
      return mockFetch()(input, init);
    });

    render(
      <AiWorkspacePanel
        open
        pageIdentity={pageIdentity}
        recordId="record-1"
        recordTitle="Test Reader"
        attachments={[]}
        onRemoveAttachment={vi.fn()}
        onClearAttachments={vi.fn()}
        onToggle={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("候选资产")).not.toBeNull();
    });

    fireEvent.click(screen.getByRole("button", { name: "加入当前讨论" }));

    await waitFor(() => {
      const streamCall = vi
        .mocked(global.fetch)
        .mock.calls.findLast(([url]) => String(url).includes("/messages/stream"));
      expect(streamCall).toBeTruthy();
      const body = JSON.parse(String(streamCall?.[1]?.body)) as Record<string, unknown>;
      expect(body.content).toBe("我之前那篇 policy 文章的分析里怎么解释这个概念？");
      expect(body.attachments).toMatchObject([
        {
          kind: "analysis_ref",
          subtype: "sentence_analysis",
          label: "Concept analysis",
          target_key: "record:record-2:analysis:sentence_analysis:analysis-1",
          metadata: {
            record_id: "record-2",
            record_title: "Climate Policy",
          },
        },
      ]);
    });
  });

  it("renders quick actions as compact operation headers and shows AI grammar cards first", async () => {
    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
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
          messages: [
            {
              id: "msg-user-1",
              thread_id: "thread-1",
              role: "user",
              status: "completed",
              content_md: "请解释这里的语法作用。",
              submission_mode: "quick_action",
              resolved_intent: null,
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
              context_plan: null,
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
                  has_reader_notes: true,
                },
                entry_action: "why_here",
                attachments: [
                  {
                    kind: "text_selection",
                    subtype: "text_range",
                    label: "选区",
                    selected_text: "compared human behaviour and brain patterns",
                    target_key: "record:record-1:range:s1:16:61:hash",
                    anchor_payload: {
                      anchor_type: "text_range",
                      target_key: "record:record-1:range:s1:16:61:hash",
                      record_id: "record-1",
                      paragraph_id: "p1",
                      sentence_id: "s1",
                      selected_text: "compared human behaviour and brain patterns",
                      start_offset: 16,
                      end_offset: 61,
                      text_hash: "hash",
                      segments: [],
                    },
                    metadata: {
                      source_surface: "selection_toolbar",
                      entry_action: "why_here",
                      sentence_id: "s1",
                      paragraph_id: "p1",
                    },
                  },
                ],
                normalized_anchors: [],
                current_record_context: null,
                external_record_contexts: [],
                external_asset_contexts: [],
              },
              run_info: null,
              supplement_candidates: [],
              persisted_supplements: [],
              usage_event_id: null,
              created_at: "2026-05-20T00:00:00Z",
              updated_at: "2026-05-20T00:00:00Z",
            },
            {
              id: "msg-assistant-1",
              thread_id: "thread-1",
              role: "assistant",
              status: "completed",
              content_md: "这里的 compare A with B 用来引出比较对象。",
              submission_mode: "quick_action",
              resolved_intent: "grammar",
              context_anchors: [],
              citations: [],
              action_proposals: [],
              tool_trace: [],
              evidence: [],
              trace_summary: null,
              disambiguation: null,
              external_asset_disambiguation: null,
              response_cards: [
                {
                  card_type: "grammar_note_card",
                  sentence_text: "The researchers compared human behaviour and brain patterns with 41 species of monkeys and apes.",
                  focus_text: "compared human behaviour and brain patterns",
                  label: "Compare A with B",
                  note_zh: "这里的 compare A with B 用来引出比较对象。",
                  spans: [
                    { text: "compared", role: "谓语" },
                    { text: "with 41 species of monkeys and apes", role: "比较对象" },
                  ],
                  analysis_scope: "focus_span",
                  origin: "ask_ai",
                },
              ],
              resolved_context: null,
              context_plan: null,
              resolved_context_input: null,
              run_info: null,
              supplement_candidates: [],
              persisted_supplements: [],
              usage_event_id: null,
              created_at: "2026-05-20T00:00:00Z",
              updated_at: "2026-05-20T00:00:00Z",
            },
          ],
        });
      }
      return mockFetch()(input, init);
    });

    render(
      <AiWorkspacePanel
        open
        pageIdentity={pageIdentity}
        recordId="record-1"
        recordTitle="Test Reader"
        attachments={[]}
        onRemoveAttachment={vi.fn()}
        onClearAttachments={vi.fn()}
        onToggle={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(
        screen.getByText((value) => value.includes("语法解析") && value.includes("compared human behaviour")),
      ).not.toBeNull();
    });

    expect(screen.queryByText("请解释这里的语法作用。")).toBeNull();
    expect(screen.getByText("Compare A with B")).not.toBeNull();
    expect(screen.getByText(/聚焦片段/)).not.toBeNull();
    expect(screen.getByText("标注有帮助")).not.toBeNull();
  });

  it("includes liveContextAttachment in request attachments when no other attachments", async () => {
    render(
      <AiWorkspacePanel
        open
        pageIdentity={pageIdentity}
        recordId="record-1"
        recordTitle="Test Reader"
        attachments={[]}
        liveContextAttachment={sentenceAttachment}
        onRemoveAttachment={vi.fn()}
        onClearAttachments={vi.fn()}
        onToggle={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalled();
    });

    fireEvent.change(screen.getByPlaceholderText("继续问这篇文章…"), {
      target: { value: "解释这句" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      const streamCall = vi
        .mocked(global.fetch)
        .mock.calls.findLast(([url]) => String(url).includes("/messages/stream"));
      expect(streamCall).toBeTruthy();
    });

    const streamCall = vi
      .mocked(global.fetch)
      .mock.calls.findLast(([url]) => String(url).includes("/messages/stream"));
    const body = JSON.parse(String(streamCall?.[1]?.body)) as Record<string, unknown>;
    const bodyAttachments = body.attachments as Record<string, unknown>[];
    expect(bodyAttachments).toHaveLength(1);
    expect(bodyAttachments[0]).toMatchObject({
      kind: "text_selection",
      subtype: "sentence",
      label: "整句",
      selected_text: "Climate change presents an existential challenge.",
    });
  });

  it("merges liveContextAttachment with existing attachments in request", async () => {
    const externalRecordAttachment: ReaderAskAttachment = {
      kind: "record_ref",
      subtype: "related_record",
      label: "Climate Policy",
      targetKey: "record:record-2:record",
      metadata: {
        pageIdentity,
        sourceSurface: "ask_context_picker",
        entryAction: "ask_about_this",
        recordId: "record-2",
        recordTitle: "Climate Policy",
      },
    };

    render(
      <AiWorkspacePanel
        open
        pageIdentity={pageIdentity}
        recordId="record-1"
        recordTitle="Test Reader"
        attachments={[externalRecordAttachment]}
        liveContextAttachment={sentenceAttachment}
        onRemoveAttachment={vi.fn()}
        onClearAttachments={vi.fn()}
        onToggle={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalled();
    });

    fireEvent.change(screen.getByPlaceholderText("继续问这篇文章…"), {
      target: { value: "结合另一篇文章解释这句" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      const streamCall = vi
        .mocked(global.fetch)
        .mock.calls.findLast(([url]) => String(url).includes("/messages/stream"));
      expect(streamCall).toBeTruthy();
    });

    const streamCall = vi
      .mocked(global.fetch)
      .mock.calls.findLast(([url]) => String(url).includes("/messages/stream"));
    const body = JSON.parse(String(streamCall?.[1]?.body)) as Record<string, unknown>;
    const bodyAttachments = body.attachments as Record<string, unknown>[];
    expect(bodyAttachments).toHaveLength(2);
    expect(bodyAttachments.some((a) => a.kind === "record_ref" && a.subtype === "related_record")).toBe(true);
    expect(bodyAttachments.some((a) => a.kind === "text_selection" && a.subtype === "sentence")).toBe(true);
  });

  it("deduplicates liveContextAttachment when same attachment already exists in attachments", async () => {
    render(
      <AiWorkspacePanel
        open
        pageIdentity={pageIdentity}
        recordId="record-1"
        recordTitle="Test Reader"
        attachments={[sentenceAttachment]}
        liveContextAttachment={sentenceAttachment}
        onRemoveAttachment={vi.fn()}
        onClearAttachments={vi.fn()}
        onToggle={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalled();
    });

    fireEvent.change(screen.getByPlaceholderText("继续问这篇文章…"), {
      target: { value: "解释这句" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      const streamCall = vi
        .mocked(global.fetch)
        .mock.calls.findLast(([url]) => String(url).includes("/messages/stream"));
      expect(streamCall).toBeTruthy();
    });

    const streamCall = vi
      .mocked(global.fetch)
      .mock.calls.findLast(([url]) => String(url).includes("/messages/stream"));
    const body = JSON.parse(String(streamCall?.[1]?.body)) as Record<string, unknown>;
    const bodyAttachments = body.attachments as Record<string, unknown>[];
    // Same attachment should appear only once, not duplicated
    expect(bodyAttachments).toHaveLength(1);
    expect(bodyAttachments[0]).toMatchObject({
      kind: "text_selection",
      subtype: "sentence",
    });
  });

  it("does not inject liveContextAttachment into quick action requests", async () => {
    const quickActionAttachment: ReaderAskAttachment = {
      kind: "text_selection",
      subtype: "text_range",
      label: "选区",
      selectedText: "quick action text",
      targetKey: "record:record-1:range:s2:0:16:hash2",
      metadata: {
        pageIdentity,
        sourceSurface: "selection_toolbar",
        entryAction: "explain_this",
        sentenceId: "s2",
        paragraphId: "p1",
      },
    };

    const onPendingQuickActionConsumed = vi.fn();
    render(
      <AiWorkspacePanel
        open
        pageIdentity={pageIdentity}
        recordId="record-1"
        recordTitle="Test Reader"
        attachments={[]}
        liveContextAttachment={sentenceAttachment}
        pendingQuickActionRequest={{
          content: "解释这段语法",
          attachments: [quickActionAttachment],
          entryAction: "explain_this",
        }}
        onPendingQuickActionConsumed={onPendingQuickActionConsumed}
        onRemoveAttachment={vi.fn()}
        onClearAttachments={vi.fn()}
        onToggle={vi.fn()}
      />,
    );

    await waitFor(() => {
      const streamCall = vi
        .mocked(global.fetch)
        .mock.calls.find(([url]) => String(url).includes("/messages/stream"));
      expect(streamCall).toBeTruthy();
    });

    const streamCall = vi
      .mocked(global.fetch)
      .mock.calls.find(([url]) => String(url).includes("/messages/stream"));
    const body = JSON.parse(String(streamCall?.[1]?.body)) as Record<string, unknown>;
    const bodyAttachments = body.attachments as Record<string, unknown>[];

    // Quick action should only have its own attachment, not the liveContextAttachment
    expect(bodyAttachments).toHaveLength(1);
    expect(bodyAttachments[0]).toMatchObject({
      kind: "text_selection",
      subtype: "text_range",
      selected_text: "quick action text",
    });
  });

  it("shows '重新生成' (not '继续生成') for interrupted messages and triggers a full regenerate", async () => {
    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
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
          messages: [
            {
              id: "msg-user-1",
              thread_id: "thread-1",
              role: "user",
              status: "completed",
              content_md: "解释一下这个语法点",
              resolved_intent: "grammar",
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
              context_plan: null,
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
                  has_reader_notes: true,
                },
                entry_action: "why_here",
                attachments: [],
                normalized_anchors: [],
                current_record_context: null,
                external_record_contexts: [],
                external_asset_contexts: [],
              },
              run_info: null,
              supplement_candidates: [],
              persisted_supplements: [],
              usage_event_id: null,
              created_at: "2026-05-20T00:00:00Z",
              updated_at: "2026-05-20T00:00:00Z",
            },
            {
              id: "msg-assistant-1",
              thread_id: "thread-1",
              role: "assistant",
              status: "interrupted",
              content_md: "这是一个让步从句，even if 表示",
              resolved_intent: "grammar",
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
              context_plan: null,
              resolved_context_input: null,
              run_info: null,
              supplement_candidates: [],
              persisted_supplements: [],
              usage_event_id: null,
              created_at: "2026-05-20T00:00:00Z",
              updated_at: "2026-05-20T00:00:00Z",
            },
          ],
        });
      }
      // retry/stream endpoint — simulate a full regenerate
      if (url.includes("/retry/stream")) {
        return new Response("", {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        });
      }
      return mockFetch()(input, init);
    });

    render(
      <AiWorkspacePanel
        open
        pageIdentity={pageIdentity}
        recordId="record-1"
        recordTitle="Test Reader"
        attachments={[]}
        onRemoveAttachment={vi.fn()}
        onClearAttachments={vi.fn()}
        onToggle={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("输出中断，可重新生成。")).not.toBeNull();
    });

    // The button must say "重新生成", not "继续" or "继续生成"
    const regenerateButton = screen.getByRole("button", { name: "重新生成" });
    expect(regenerateButton).not.toBeNull();
    expect(regenerateButton.getAttribute("title")).toBe("重新生成");

    // Must NOT show "继续生成" or "继续" anywhere
    expect(screen.queryByText("继续生成")).toBeNull();
    expect(screen.queryByText("继续")).toBeNull();

    // Clicking the button triggers a full regenerate (retry/stream endpoint)
    fireEvent.click(regenerateButton);

    await waitFor(() => {
      const retryCall = vi
        .mocked(global.fetch)
        .mock.calls.find(([url]) => String(url).includes("/retry/stream"));
      expect(retryCall).toBeTruthy();
      expect(String(retryCall?.[0])).toContain("/retry/stream");
      expect(retryCall?.[1]?.method).toBe("POST");
    });
  });

  it("renders the current selection inside the attachment chip row", async () => {
    const onActivateLiveContextSelection = vi.fn();
    const onComposerTextareaFocus = vi.fn();
    const onComposerTextareaBlur = vi.fn();
    const onRemoveAttachment = vi.fn();
    render(
      <AiWorkspacePanel
        open
        pageIdentity={pageIdentity}
        recordId="record-1"
        recordTitle="Test Reader"
        attachments={[]}
        liveContextAttachment={sentenceAttachment}
        onActivateLiveContextSelection={onActivateLiveContextSelection}
        onComposerTextareaFocus={onComposerTextareaFocus}
        onComposerTextareaBlur={onComposerTextareaBlur}
        onRemoveAttachment={onRemoveAttachment}
        onClearAttachments={vi.fn()}
        onToggle={vi.fn()}
      />,
    );

    expect(screen.queryByText("当前可带入")).toBeNull();
    expect(screen.queryByText("当前")).toBeNull();

    const selectionChip = screen.getByTitle("Climate change presents an existential challenge.");
    fireEvent.click(selectionChip);
    expect(onActivateLiveContextSelection).toHaveBeenCalledTimes(1);
    expect(onRemoveAttachment).not.toHaveBeenCalled();
    expect(selectionChip.textContent).toContain("Climate change presents an existential");
    expect(selectionChip.textContent).toContain("…");
    expect(selectionChip.querySelector(".truncate")).not.toBeNull();
    expect(screen.getByLabelText(/移除当前选区/)).not.toBeNull();

    const textarea = screen.getByPlaceholderText("继续问这篇文章…");
    const composer = textarea.closest(".cursor-text");
    composer?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    expect(document.activeElement).not.toBe(textarea);

    fireEvent.focus(textarea);
    expect(onComposerTextareaFocus).toHaveBeenCalledTimes(1);
    fireEvent.blur(textarea);
    expect(onComposerTextareaBlur).toHaveBeenCalledTimes(1);
  });

  it("renders citation badges in the Ask answer surface", async () => {
    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
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
          messages: [
            {
              id: "msg-assistant-1",
              thread_id: "thread-1",
              role: "assistant",
              status: "completed",
              content_md: "Here is the answer.",
              context_anchors: [],
              citations: [
                {
                  citation_id: "cite-1",
                  kind: "anchor",
                  label: "Paragraph 1",
                  record_id: "record-1",
                  target_key: "p1",
                  source_article_title: "Source Article A",
                  selected_text: "This is the source text that was cited.",
                  metadata_json: {},
                }
              ],
              action_proposals: [],
              tool_trace: [],
              evidence: [],
              trace_summary: null,
              disambiguation: null,
              external_asset_disambiguation: null,
              response_cards: [],
              resolved_context: null,
              context_plan: null,
              resolved_context_input: null,
              run_info: null,
              supplement_candidates: [],
              persisted_supplements: [],
            },
          ],
        });
      }
      return mockFetch()(input, init);
    });

    render(
      <AiWorkspacePanel
        open
        pageIdentity={pageIdentity}
        recordId="record-1"
        recordTitle="Test Reader"
        attachments={[]}
        onRemoveAttachment={vi.fn()}
        onClearAttachments={vi.fn()}
        onToggle={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Here is the answer.")).not.toBeNull();
    });

    expect(screen.getByText("[1]")).not.toBeNull();
    expect(screen.getByText("Paragraph 1")).not.toBeNull();
    expect(screen.getByText("Source Article A")).not.toBeNull();
    expect(screen.queryByText("This is the source text that was cited.")).toBeNull();
  });

  it("does not render context anchor cards above user messages", async () => {
    mockThreadMessages([
      {
        ...createAssistantMessage({
          id: "msg-user-1",
          role: "user",
          content_md: "帮我分析这句话。",
          context_anchors: [
            {
              anchor_type: "sentence",
              sentence_id: "s1",
              paragraph_id: "p1",
              target_key: "record:record-1:sentence:s1",
              label: "整句",
              selected_text: "This anchor card should stay hidden.",
              segments: [],
              payload_json: {},
            },
          ],
        }),
      } as ReaderAskUiMessageDto,
      createAssistantMessage({
        id: "msg-assistant-2",
        content_md: "这是回答。",
      }),
    ]);

    renderPanel();

    await waitFor(() => {
      expect(screen.getByText("帮我分析这句话。")).not.toBeNull();
      expect(screen.getByText("这是回答。")).not.toBeNull();
    });

    expect(screen.queryByText("整句")).toBeNull();
    expect(screen.queryByText("This anchor card should stay hidden.")).toBeNull();
  });

  it("shows a prompt-kit loader for streaming answers without the ellipsis fallback", async () => {
    mockThreadMessages([
      createAssistantMessage({
        status: "streaming",
        content_md: "",
      }),
    ]);

    renderPanel();

    await waitFor(() => {
      expect(screen.getByText("正在读取当前文章与附件上下文，准备本轮解释。")).not.toBeNull();
    });

    expect(screen.queryByText("思考中")).toBeNull();
    expect(screen.queryByText("…")).toBeNull();
  });

  it("keeps the streaming loader visible alongside partial markdown content", async () => {
    mockThreadMessages([
      createAssistantMessage({
        status: "streaming",
        content_md: "已生成第一句。",
      }),
    ]);

    renderPanel();

    await waitFor(() => {
      expect(screen.getByText("正在组织回答")).not.toBeNull();
      expect(screen.getByText("已生成第一句。")).not.toBeNull();
    });
  });

  it("removes the streaming loader once the assistant message is completed", async () => {
    mockThreadMessages([
      createAssistantMessage({
        status: "completed",
        content_md: "解释完成。",
      }),
    ]);

    renderPanel();

    await waitFor(() => {
      expect(screen.getByText("解释完成。")).not.toBeNull();
    });

    expect(screen.queryByText("正在整理问题")).toBeNull();
    expect(screen.queryByText("正在组织回答")).toBeNull();
  });

  it("shows reasoning while streaming even before reasoning markdown arrives", async () => {
    mockThreadMessages([
      createAssistantMessage({
        status: "streaming",
        content_md: "",
        reasoning_md: "",
        reasoning_status: "streaming",
      }),
    ]);

    const { container } = renderPanel();

    await waitFor(() => {
      expect(screen.getByText("思考中")).not.toBeNull();
      expect(screen.getByText("正在形成可展示的思路…")).not.toBeNull();
    });

    const trigger = container.querySelector('[data-slot="reasoning-trigger"]');
    const content = container.querySelector('[data-slot="reasoning-content"]');
    expect(trigger?.getAttribute("aria-expanded")).toBe("true");
    expect(content?.getAttribute("data-state")).toBe("open");
  });

  it("normalizes duplicated tool trace entries into one visible step while streaming", async () => {
    mockThreadMessages([
      createAssistantMessage({
        status: "streaming",
        content_md: "已生成第一句。",
        tool_trace: [
          {
            tool_name: "get_record_context",
            status: "started",
            started_at: "2026-05-20T00:00:00Z",
            completed_at: null,
            input_summary: null,
            summary: null,
            next_actions: [],
            artifacts: [],
            metadata_json: {},
          },
          {
            tool_name: "get_record_context",
            status: "completed",
            started_at: "2026-05-20T00:00:00Z",
            completed_at: "2026-05-20T00:00:01Z",
            input_summary: null,
            summary: "已读取当前文章上下文。",
            next_actions: [],
            artifacts: [],
            metadata_json: {},
          },
        ],
      }),
    ]);

    renderPanel();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /当前文章上下文/ })).not.toBeNull();
      expect(screen.getByText("Completed")).not.toBeNull();
    });

    fireEvent.click(screen.getByRole("button", { name: /当前文章上下文/ }));

    await waitFor(() => {
      expect(screen.getAllByText(/已读取当前文章上下文/)).toHaveLength(1);
    });

    expect(screen.queryByText("Running")).toBeNull();
  });

  it("uses the active Ask model for new turns and retry requests", async () => {
    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/web/reader-ask/model-options")) {
        return jsonResponse({
          default_key: "ask-clarity",
          items: [
            {
              key: "ask-clarity",
              label: "Qwen 3.7 Max",
              description: "适合带 reasoning 的 Ask 问答。",
              model_name: "qwen3.7-max",
              replan_model_name: "qwen3.7-max",
              price_multiplier: 1,
              is_default: true,
            },
            {
              key: "ask-fast",
              label: "DeepSeek Chat",
              description: "更快的直接回答。",
              model_name: "deepseek-chat",
              replan_model_name: "deepseek-chat",
              price_multiplier: 0.8,
              is_default: false,
            },
          ],
        });
      }
      if (url.includes("/api/web/reader-ask/threads?record_id=")) {
        return jsonResponse({
          items: [
            {
              id: "thread-1",
              record_id: "record-1",
              title: "Ask Claread",
              is_default: true,
              selected_model: {
                key: "ask-fast",
                label: "DeepSeek Chat",
                description: "更快的直接回答。",
                model_name: "deepseek-chat",
                replan_model_name: "deepseek-chat",
                price_multiplier: 0.8,
              },
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
          selected_model: {
            key: "ask-fast",
            label: "DeepSeek Chat",
            description: "更快的直接回答。",
            model_name: "deepseek-chat",
            replan_model_name: "deepseek-chat",
            price_multiplier: 0.8,
          },
          archived_at: null,
          created_at: "2026-05-20T00:00:00Z",
          updated_at: "2026-05-20T00:00:00Z",
          last_message_at: null,
          messages: [],
        });
      }
      return mockFetch()(input, init);
    });

    renderPanel();

    await waitFor(() => {
      const modelSelect = screen.getByLabelText("切换 Ask Claread 模型");
      expect(modelSelect.textContent ?? "").toContain("DeepSeek Chat");
    });
    expect(screen.queryByText("当前模型 · DeepSeek Chat")).toBeNull();
    expect(screen.queryByText("更快的直接回答。")).toBeNull();

    const composer = screen.getByRole("textbox");
    fireEvent.change(composer, { target: { value: "帮我解释这一句。" } });
    fireEvent.keyDown(composer, { key: "Enter", code: "Enter" });

    await waitFor(() => {
      const streamCall = vi
        .mocked(global.fetch)
        .mock.calls.find(([url]) => String(url).includes("/messages/stream"));
      expect(streamCall).toBeTruthy();
      expect(streamCall?.[1]?.body).toContain('"model":"ask-fast"');
    });

    vi.mocked(global.fetch).mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/web/reader-ask/model-options")) {
        return jsonResponse({
          default_key: "ask-clarity",
          items: [
            {
              key: "ask-clarity",
              label: "Qwen 3.7 Max",
              description: "适合带 reasoning 的 Ask 问答。",
              model_name: "qwen3.7-max",
              replan_model_name: "qwen3.7-max",
              price_multiplier: 1,
              is_default: true,
            },
            {
              key: "ask-fast",
              label: "DeepSeek Chat",
              description: "更快的直接回答。",
              model_name: "deepseek-chat",
              replan_model_name: "deepseek-chat",
              price_multiplier: 0.8,
              is_default: false,
            },
          ],
        });
      }
      if (url.includes("/api/web/reader-ask/threads?record_id=")) {
        return jsonResponse({
          items: [
            {
              id: "thread-1",
              record_id: "record-1",
              title: "Ask Claread",
              is_default: true,
              selected_model: {
                key: "ask-fast",
                label: "DeepSeek Chat",
                description: "更快的直接回答。",
                model_name: "deepseek-chat",
                replan_model_name: "deepseek-chat",
                price_multiplier: 0.8,
              },
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
          selected_model: {
            key: "ask-fast",
            label: "DeepSeek Chat",
            description: "更快的直接回答。",
            model_name: "deepseek-chat",
            replan_model_name: "deepseek-chat",
            price_multiplier: 0.8,
          },
          archived_at: null,
          created_at: "2026-05-20T00:00:00Z",
          updated_at: "2026-05-20T00:00:00Z",
          last_message_at: null,
          messages: [
            createAssistantMessage({
              id: "msg-retry-target",
              status: "interrupted",
              content_md: "已有部分答案。",
            }),
          ],
        });
      }
      if (url.includes("/retry/stream")) {
        return new Response("", {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        });
      }
      return mockFetch()(input, init);
    });

    cleanup();
    renderPanel();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "重新生成" })).not.toBeNull();
    });

    fireEvent.click(screen.getByRole("button", { name: "重新生成" }));

    await waitFor(() => {
      const retryCall = vi
        .mocked(global.fetch)
        .mock.calls.find(([url]) => String(url).includes("/retry/stream"));
      expect(retryCall).toBeTruthy();
      expect(retryCall?.[1]?.body).toBe(JSON.stringify({ model: "ask-fast" }));
    });
  });

  it("renders reasoning deltas immediately while the answer is still streaming", async () => {
    mockThreadMessages([
      createAssistantMessage({
        status: "streaming",
        content_md: "正文正在生成。",
        reasoning_md: "先判断句子主干。",
        reasoning_status: "streaming",
      }),
    ]);

    renderPanel();

    await waitFor(() => {
      expect(screen.getByText("正文正在生成。")).not.toBeNull();
      expect(screen.getByText("先判断句子主干。")).not.toBeNull();
    });
  });

  it("rehydrates a persisted streaming snapshot without auto-retrying the run", async () => {
    const fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
    mockThreadMessages([
      createAssistantMessage({
        status: "streaming",
        content_md: "刷新后仍可见的正文片段。",
        reasoning_md: "刷新后仍可见的 thinking 片段。",
        reasoning_status: "streaming",
      }),
    ]);

    renderPanel();

    await waitFor(() => {
      expect(screen.getByText("刷新后仍可见的正文片段。")).not.toBeNull();
      expect(screen.getByText("刷新后仍可见的 thinking 片段。")).not.toBeNull();
    });

    expect(
      fetchMock.mock.calls.some(([url]) => {
        const value = String(url);
        return value.includes("/retry/stream");
      }),
    ).toBe(false);
  });

  it("auto-collapses completed reasoning and lets the user reopen it", async () => {
    mockThreadMessages([
      createAssistantMessage({
        content_md: "这里是答案正文。",
        reasoning_md: "推理细节在这里。",
        reasoning_status: "completed",
      }),
    ]);

    const { container } = renderPanel();

    await waitFor(() => {
      expect(screen.getByText("思考过程")).not.toBeNull();
      expect(screen.getByText("这里是答案正文。")).not.toBeNull();
    });

    const trigger = container.querySelector('[data-slot="reasoning-trigger"]');
    const content = container.querySelector('[data-slot="reasoning-content"]');
    expect(trigger?.getAttribute("aria-expanded")).toBe("false");
    expect(content?.getAttribute("data-state")).toBe("closed");

    fireEvent.click(screen.getByText("思考过程"));

    await waitFor(() => {
      expect(trigger?.getAttribute("aria-expanded")).toBe("true");
      expect(content?.getAttribute("data-state")).toBe("open");
      expect(screen.getByText("推理细节在这里。")).not.toBeNull();
    });
  });

  it("restores completed reasoning from hydration without showing streaming state", async () => {
    mockThreadMessages([
      createAssistantMessage({
        status: "completed",
        content_md: "最终答案。",
        reasoning_md: "这是刷新后恢复的推理内容。",
        reasoning_status: "completed",
      }),
    ]);

    const { container } = renderPanel();

    await waitFor(() => {
      expect(screen.getByText("最终答案。")).not.toBeNull();
    });

    // Completed reasoning must be collapsed, not streaming
    const trigger = container.querySelector('[data-slot="reasoning-trigger"]');
    expect(trigger?.getAttribute("aria-expanded")).toBe("false");

    // User can expand to see the reasoning content
    fireEvent.click(screen.getByText("思考过程"));
    await waitFor(() => {
      expect(screen.getByText("这是刷新后恢复的推理内容。")).not.toBeNull();
    });
  });

  it("keeps a completed reasoning trigger visible even when the model returned no reasoning text", async () => {
    mockThreadMessages([
      createAssistantMessage({
        status: "completed",
        content_md: "这是直接回答。",
        reasoning_md: "",
        reasoning_status: "completed",
      }),
    ]);

    const { container } = renderPanel();

    await waitFor(() => {
      expect(screen.getByText("这是直接回答。")).not.toBeNull();
      expect(screen.getByText("思考过程")).not.toBeNull();
    });

    const trigger = container.querySelector('[data-slot="reasoning-trigger"]');
    expect(trigger?.getAttribute("aria-expanded")).toBe("false");

    fireEvent.click(screen.getByText("思考过程"));

    await waitFor(() => {
      expect(screen.getByText("本轮模型未返回可展示的思考内容。")).not.toBeNull();
    });
  });

  it("shows the user-facing insufficient credits message from stream errors", async () => {
    vi.mocked(consumeReaderAskSse).mockImplementationOnce(async (_response, onEvent) => {
      onEvent({
        event: "error",
        data: {
          code: "INSUFFICIENT_CREDITS",
          detail: "Not enough credits for this Ask Claread request.",
          user_message: "当前积分不足：剩余 1 点，本次 Ask Claread 至少需要 10 点。本轮请求未发送给模型。",
          remaining_points: 1,
          required_points: 10,
        },
      });
    });

    render(
      <AiWorkspacePanel
        open
        pageIdentity={pageIdentity}
        recordId="record-1"
        recordTitle="Test Reader"
        attachments={[]}
        onRemoveAttachment={vi.fn()}
        onClearAttachments={vi.fn()}
        onToggle={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText("继续问这篇文章…"), {
      target: { value: "解释一下这个问题" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      expect(
        screen.getByText("当前积分不足：剩余 1 点，本次 Ask Claread 至少需要 10 点。本轮请求未发送给模型。"),
      ).not.toBeNull();
    });
  });

  // -------------------------------------------------------------------------
  // F6 — Ask article RAG sidecar UI integration
  // -------------------------------------------------------------------------
  // Covers: completed SSE article_rag rendering, status fallbacks,
  // strict should_attach === true gate, debug-only field stripping, and
  // coexistence with ordinary ReaderAsk citations.
  // -------------------------------------------------------------------------

  function makeArticleRagCitation(overrides: Partial<ReaderAskArticleRagCitationDto> = {}): ReaderAskArticleRagCitationDto {
    return {
      context_id: "ctx_1",
      chunk_id: "chunk_1",
      citation: {
        reading_record_id: "record-1",
        stable_document_id: "sd-1",
        base_id: "base-1",
        record_generation: 3,
        block_ids: ["block-1"],
        unit_ids: ["unit-1"],
        anchor_segment_ids: ["seg-1"],
        canonical_text_start_utf16: 0,
        canonical_text_end_utf16: 100,
      },
      ...overrides,
    };
  }

  function makeRawArticleRagSidecar(
    overrides: Record<string, unknown> = {},
  ): Record<string, unknown> {
    return {
      status: "available",
      // DEBUG-ONLY — must not appear in DOM
      failure_code: "internal_error",
      // DEBUG-ONLY — must not appear in DOM
      retryable: true,
      // DEBUG-ONLY — must not appear in DOM
      fallback_allowed: false,
      should_attach: true,
      context_ids: ["ctx_1", "ctx_2"],
      // DEBUG-ONLY — must not appear in DOM
      source_pack_hash: "pack_hash_secret",
      // DEBUG-ONLY — must not appear in DOM
      query_sha256: "query_hash_secret",
      citations: [makeArticleRagCitation()],
      ...overrides,
    };
  }

  function makeSafeArticleRagSidecar(
    overrides: Partial<ReaderAskArticleRagSidecarSafeDto> = {},
  ): ReaderAskArticleRagSidecarSafeDto {
    return {
      status: "available",
      should_attach: true,
      context_ids: ["ctx_1"],
      citations: [makeArticleRagCitation()],
      ...overrides,
    };
  }

  function mockArticleRagCompletedPayload(articleRag: unknown) {
    vi.mocked(consumeReaderAskSse).mockImplementationOnce(async (_response, onEvent) => {
      onEvent({ event: "message.started", data: { message_id: "msg-assistant-1" } });
      onEvent({
        event: "message.completed",
        data: { ...completedPayload, article_rag: articleRag },
      });
    });
  }

  async function sendArticleRagMessage() {
    fireEvent.change(screen.getByPlaceholderText("继续问这篇文章…"), {
      target: { value: "这篇里有没有提到 climate policy？" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => {
      expect(screen.getByText("解释完成。")).not.toBeNull();
    });
  }

  it("renders article RAG citation list when completed payload has available sidecar with should_attach=true", async () => {
    mockArticleRagCompletedPayload(makeRawArticleRagSidecar());

    renderPanel();

    await sendArticleRagMessage();

    expect(screen.getByText("文章引用")).not.toBeNull();
    expect(screen.getByText("引用 1")).not.toBeNull();
    // Short stable identifier tags render; chunk text / hash / provider do not.
    expect(screen.getByText(/block:block-1/)).not.toBeNull();
    expect(screen.getByText(/unit:unit-1/)).not.toBeNull();
    expect(screen.getByText(/seg:seg-1/)).not.toBeNull();
  });

  it.each([
    ["string 'true'", { should_attach: "true" }],
    ["number 1", { should_attach: 1 }],
  ])("does not render article RAG citations when should_attach is truthy %s", async (_label, override) => {
    mockArticleRagCompletedPayload(makeRawArticleRagSidecar(override));

    renderPanel();

    await sendArticleRagMessage();

    expect(screen.queryByText("文章引用")).toBeNull();
    expect(screen.queryByText("引用 1")).toBeNull();
  });

  it.each([
    ["stale_due_to_repair"],
    ["disabled"],
    ["composer_rejected"],
    ["not_indexed_or_unavailable"],
    ["empty"],
    ["totally_unknown_sidecar_status"],
  ])("silently falls back when article_rag sidecar status is %s (no citation, no error)", async (status) => {
    mockArticleRagCompletedPayload(makeRawArticleRagSidecar({ status }));

    renderPanel();

    await sendArticleRagMessage();

    expect(screen.queryByText("文章引用")).toBeNull();
    expect(screen.queryByText("引用 1")).toBeNull();
    // The Ask answer body still renders — fail-soft, no user-visible error.
    expect(screen.getByText("解释完成。")).not.toBeNull();
  });

  it("strips debug-only fields from the DOM when rendering article RAG citations", async () => {
    mockArticleRagCompletedPayload(makeRawArticleRagSidecar());

    renderPanel();

    await sendArticleRagMessage();

    // Citation list IS rendered for the available sidecar.
    expect(screen.getByText("文章引用")).not.toBeNull();
    // DEBUG-ONLY fields must NOT leak to the DOM.
    expect(screen.queryByText("internal_error")).toBeNull();
    expect(screen.queryByText("pack_hash_secret")).toBeNull();
    expect(screen.queryByText("query_hash_secret")).toBeNull();
    // `retryable` / `fallback_allowed` are booleans; assert their text form
    // doesn't appear anywhere on the page.
    expect(screen.queryByText("retryable")).toBeNull();
    expect(screen.queryByText("fallback_allowed")).toBeNull();
    expect(screen.queryByText("true")).toBeNull();
    expect(screen.queryByText("false")).toBeNull();
  });

  it("renders ordinary ReaderAsk citations alongside article RAG citations without regression", async () => {
    const safeSidecar = makeSafeArticleRagSidecar();
    const assistantMessage = createAssistantMessage({
      article_rag: safeSidecar,
      citations: [
        {
          citation_id: "c1",
          kind: "anchor",
          label: "第3句",
          metadata_json: {},
        },
      ],
    });
    // The thread-detail endpoint nominally returns ReaderAskMessageDto[] —
    // the article_rag UI field is preserved through setMessages because it
    // is an optional field on the ReaderAskUiMessageDto intersection type.
    mockThreadMessages([assistantMessage]);

    renderPanel();

    await waitFor(() => {
      expect(screen.getByText("Here is the answer.")).not.toBeNull();
    });

    // Article RAG citation list renders.
    expect(screen.getByText("文章引用")).not.toBeNull();
    expect(screen.getByText("引用 1")).not.toBeNull();
    // Ordinary ReaderAsk citations still render via CitationList.
    expect(screen.getByText("第3句")).not.toBeNull();
  });

  it("normalizes raw article_rag sidecar from thread-detail load before it reaches React state", async () => {
    // Backend thread-detail returns raw `article_rag` (containing debug-only
    // fields). The cold-load path must run mapAskArticleRagSidecar so the
    // debug-only fields never enter React state — otherwise they would be
    // typed as ReaderAskArticleRagSidecarSafeDto and could leak into the
    // DOM via future component code that trusts the type.
    const rawSidecar = makeRawArticleRagSidecar();
    const assistantMessage = createAssistantMessage({
      article_rag: rawSidecar as unknown as ReaderAskArticleRagSidecarSafeDto,
    });
    mockThreadMessages([assistantMessage]);

    renderPanel();

    await waitFor(() => {
      expect(screen.getByText("Here is the answer.")).not.toBeNull();
    });

    // Article RAG citations render because the raw sidecar's `available`
    // status + `should_attach === true` survive normalization.
    expect(screen.getByText("文章引用")).not.toBeNull();
    expect(screen.getByText("引用 1")).not.toBeNull();

    // Debug-only fields from the raw sidecar MUST NOT leak to the DOM.
    expect(screen.queryByText("internal_error")).toBeNull();
    expect(screen.queryByText("pack_hash_secret")).toBeNull();
    expect(screen.queryByText("query_hash_secret")).toBeNull();
    expect(screen.queryByText("retryable")).toBeNull();
    expect(screen.queryByText("fallback_allowed")).toBeNull();
  });

  it("source guard: thread-detail messages must pass through article RAG normalizer", async () => {
    const { readFileSync } = await import("node:fs");
    const { resolve: pathResolve } = await import("node:path");
    const source = readFileSync(
      pathResolve(process.cwd(), "src/components/reader/AiWorkspacePanel.tsx"),
      "utf-8",
    );

    expect(source).toContain("function normalizeReaderAskMessages");
    expect(source).toContain("mapAskArticleRagSidecar");
    expect(source).toContain("setMessages(normalizeReaderAskMessages(detail.messages))");
    expect(source).not.toContain("setMessages(detail.messages)");
  });

  describe("AskProvenanceLine and capacity downgrade notice", () => {
    const noteAttachment: ReaderAskAttachment = {
      kind: "text_selection",
      subtype: "reader_note",
      label: "笔记片段",
      selectedText: "An important note.",
      metadata: {
        pageIdentity,
        sourceSurface: "ask_panel",
        entryAction: "ask_about_this",
      },
    };

    it("provenance shows article context when record title is present", async () => {
      renderPanel({
        recordTitle: "Test Reader",
        attachments: [],
        liveContextAttachment: null,
      });

      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalled();
      });

      expect(screen.getByText(/基于：当前文章/)).not.toBeNull();
    });

    it("provenance shows selection and notes when live selection and attachments exist", async () => {
      renderPanel({
        recordTitle: "Test Reader",
        liveContextAttachment: sentenceAttachment,
        attachments: [noteAttachment],
      });

      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalled();
      });

      const summary = screen.getByText(/基于：/);
      expect(summary.textContent).toContain("当前文章 · 选中句");
      expect(summary.textContent).toContain("1 条笔记");
    });

    it("provenance shows no-context state when nothing is present", async () => {
      renderPanel({
        recordTitle: "",
        attachments: [],
        liveContextAttachment: null,
      });

      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalled();
      });

      expect(screen.getByText("仅按你的问题回答")).not.toBeNull();
    });

    it("keeps no-context provenance as static text instead of a dead disclosure button", async () => {
      renderPanel({
        recordTitle: "",
        attachments: [],
        liveContextAttachment: null,
      });

      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalled();
      });

      const summary = screen.getByText("仅按你的问题回答");
      expect(summary.closest("button")).toBeNull();
    });

    it("does not announce a sidecar switch until the effective surface actually changes", async () => {
      const onChangeSurface = vi.fn();
      renderPanel({
        surface: "floating",
        onChangeSurface,
      });

      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalled();
      });

      await userEvent.click(
        screen.getByRole("button", { name: "选择 Ask Claread 面板形式" }),
      );
      await userEvent.click(
        await screen.findByRole("menuitem", { name: "侧边栏" }),
      );

      expect(onChangeSurface).toHaveBeenCalledWith("sidecar");
      expect(screen.queryByText("Ask Claread 已切换为侧边栏。")).toBeNull();
    });
    it("capacity downgrade notice appears when provided and can be dismissed", async () => {
      const onDismissCapacityDowngradeNotice = vi.fn();
      renderPanel({
        capacityDowngradeNotice:
          "当前阅读区较窄，Ask Claread 已暂以浮窗展示；空间恢复后将回到侧边栏。",
        onDismissCapacityDowngradeNotice,
      });

      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalled();
      });

      const notice = screen.getByTestId("ask-capacity-downgrade-notice");
      expect(notice).not.toBeNull();
      expect(notice.textContent).toContain(
        "当前阅读区较窄，Ask Claread 已暂以浮窗展示；空间恢复后将回到侧边栏。",
      );

      await userEvent.click(screen.getByRole("button", { name: "关闭说明" }));
      expect(onDismissCapacityDowngradeNotice).toHaveBeenCalledTimes(1);
    });

    it("does not expose a dead dismiss control when a downgrade notice has no dismiss callback", async () => {
      renderPanel({
        capacityDowngradeNotice: "当前阅读区较窄，Ask Claread 以浮窗形式展示。",
      });

      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalled();
      });

      expect(screen.queryByRole("button", { name: "关闭说明" })).toBeNull();
    });
    it("capacity downgrade notice does not render when null", async () => {
      renderPanel({
        capacityDowngradeNotice: null,
      });

      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalled();
      });

      expect(screen.queryByTestId("ask-capacity-downgrade-notice")).toBeNull();
    });
  });
});

// ---------------------------------------------------------------------------
// replan.started SSE handler tests
// ---------------------------------------------------------------------------

describe("createSseMessageHandler – replan.started", () => {
  let rafCallbacks: FrameRequestCallback[] = [];
  let rafIdCounter = 1;

  beforeEach(() => {
    rafCallbacks = [];
    rafIdCounter = 1;
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
      const id = rafIdCounter++;
      rafCallbacks.push(cb);
      return id;
    });
    vi.stubGlobal("cancelAnimationFrame", (id: number) => {
      // no-op for tests
    });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function flushRaf() {
    const callbacks = [...rafCallbacks];
    rafCallbacks = [];
    for (const cb of callbacks) {
      cb(0);
    }
  }

  it("sets replan_status to 'replanning' on replan.started event", () => {
    type Msg = ReaderAskUiMessageDto;
    const targetId = "msg-1";
    const messages: Msg[] = [
      {
        id: targetId,
        thread_id: "thread-1",
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
        external_asset_disambiguation: null,
        response_cards: [],
        supplement_candidates: [],
        persisted_supplements: [],
        created_at: "2026-05-20T00:00:00Z",
        updated_at: "2026-05-20T00:00:00Z",
      },
    ];

    let updatedMessages: Msg[] = messages;
    const updateMessage = (updater: (msgs: Msg[]) => Msg[]) => {
      updatedMessages = updater(updatedMessages);
    };

    const handler = createSseMessageHandler(
      targetId,
      updateMessage,
      undefined,
      vi.fn(),
    );

    handler({
      event: "replan.started",
      data: { message_id: targetId, reason: "degenerate_answer" },
    });
    flushRaf();

    expect(updatedMessages[0].replan_status).toBe("replanning");
  });

  it("resets replan_status to 'idle' on message.completed event", () => {
    type Msg = ReaderAskUiMessageDto;
    const targetId = "msg-1";
    const messages: Msg[] = [
      {
        id: targetId,
        thread_id: "thread-1",
        role: "assistant",
        status: "streaming",
        content_md: "",
        replan_status: "replanning",
        context_anchors: [],
        citations: [],
        action_proposals: [],
        tool_trace: [],
        evidence: [],
        trace_summary: null,
        disambiguation: null,
        external_asset_disambiguation: null,
        response_cards: [],
        supplement_candidates: [],
        persisted_supplements: [],
        created_at: "2026-05-20T00:00:00Z",
        updated_at: "2026-05-20T00:00:00Z",
      },
    ];

    let updatedMessages: Msg[] = messages;
    const updateMessage = (updater: (msgs: Msg[]) => Msg[]) => {
      updatedMessages = updater(updatedMessages);
    };

    const handler = createSseMessageHandler(
      targetId,
      updateMessage,
      undefined,
      vi.fn(),
    );

    handler({
      event: "message.completed",
      data: {
        id: targetId,
        thread_id: "thread-1",
        content_md: "This is the replanned answer.",
        submission_mode: "chat",
        resolved_intent: "explain",
        citations: [],
        action_proposals: [],
        tool_trace: [],
        evidence: [],
        response_cards: [],
        supplement_candidates: [],
        persisted_supplements: [],
        usage_event_id: "usage-1",
      },
    });
    flushRaf();

    expect(updatedMessages[0].replan_status).toBe("idle");
    expect(updatedMessages[0].content_md).toBe("This is the replanned answer.");
    expect(updatedMessages[0].usage_event_id).toBe("usage-1");
  });

  it("replaces interrupted preview content on the first regenerate delta", () => {
    type Msg = ReaderAskUiMessageDto;
    const targetId = "msg-1";
    const messages: Msg[] = [
      {
        id: targetId,
        thread_id: "thread-1",
        role: "assistant",
        status: "streaming",
        content_md: "旧的中断内容",
        regenerate_preview: true,
        context_anchors: [],
        citations: [],
        action_proposals: [],
        tool_trace: [],
        evidence: [],
        trace_summary: null,
        disambiguation: null,
        external_asset_disambiguation: null,
        response_cards: [],
        supplement_candidates: [],
        persisted_supplements: [],
        created_at: "2026-05-20T00:00:00Z",
        updated_at: "2026-05-20T00:00:00Z",
      },
    ];

    let updatedMessages: Msg[] = messages;
    const updateMessage = (updater: (msgs: Msg[]) => Msg[]) => {
      updatedMessages = updater(updatedMessages);
    };

    const handler = createSseMessageHandler(targetId, updateMessage, undefined, vi.fn());

    handler({
      event: "message.delta",
      data: { delta: "新的开头" },
    });
    flushRaf();

    expect(updatedMessages[0].content_md).toBe("新的开头");
    expect(updatedMessages[0].regenerate_preview).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// createSseMessageHandler – reasoning lifecycle tests
// ---------------------------------------------------------------------------

describe("createSseMessageHandler – reasoning lifecycle", () => {
  let rafCallbacks: FrameRequestCallback[] = [];
  let rafIdCounter = 1;

  beforeEach(() => {
    rafCallbacks = [];
    rafIdCounter = 1;
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
      const id = rafIdCounter++;
      rafCallbacks.push(cb);
      return id;
    });
    vi.stubGlobal("cancelAnimationFrame", () => {});
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function flushRaf() {
    const callbacks = [...rafCallbacks];
    rafCallbacks = [];
    for (const cb of callbacks) {
      cb(0);
    }
  }

  type Msg = ReaderAskUiMessageDto;

  function makeStreamingAssistant(overrides: Partial<Msg> = {}): Msg {
    return {
      id: "msg-1",
      thread_id: "thread-1",
      role: "assistant",
      status: "streaming",
      content_md: "",
      reasoning_md: null,
      reasoning_status: null,
      context_anchors: [],
      citations: [],
      action_proposals: [],
      tool_trace: [],
      evidence: [],
      trace_summary: null,
      disambiguation: null,
      external_asset_disambiguation: null,
      response_cards: [],
      supplement_candidates: [],
      persisted_supplements: [],
      created_at: "2026-05-20T00:00:00Z",
      updated_at: "2026-05-20T00:00:00Z",
      ...overrides,
    };
  }

  function setupHandler(messages: Msg[]) {
    let updatedMessages: Msg[] = messages;
    const updateMessage = (updater: (msgs: Msg[]) => Msg[]) => {
      updatedMessages = updater(updatedMessages);
    };
    const onError = vi.fn();
    const handler = createSseMessageHandler("msg-1", updateMessage, undefined, onError);
    return {
      getMessages: () => updatedMessages,
      handler,
      onError,
    };
  }

  it("enters streaming on reasoning.started even when reasoning_md is empty", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);

    handler({ event: "reasoning.started", data: { message_id: "msg-1" } });
    flushRaf();

    expect(getMessages()[0].reasoning_status).toBe("streaming");
    expect(getMessages()[0].reasoning_md).toBe("");
  });

  it("appends reasoning.delta content without losing prior deltas", () => {
    const { handler, getMessages } = setupHandler([
      makeStreamingAssistant({ reasoning_status: "streaming", reasoning_md: "" }),
    ]);

    handler({ event: "reasoning.delta", data: { message_id: "msg-1", delta: "step 1" } });
    flushRaf();
    expect(getMessages()[0].reasoning_md).toBe("step 1");

    handler({ event: "reasoning.delta", data: { message_id: "msg-1", delta: " step 2" } });
    flushRaf();
    expect(getMessages()[0].reasoning_md).toBe("step 1 step 2");
    expect(getMessages()[0].reasoning_status).toBe("streaming");
  });

  it("transitions to completed on reasoning.completed", () => {
    const { handler, getMessages } = setupHandler([
      makeStreamingAssistant({ reasoning_status: "streaming", reasoning_md: "thinking content" }),
    ]);

    handler({ event: "reasoning.completed", data: { message_id: "msg-1" } });
    flushRaf();

    expect(getMessages()[0].reasoning_status).toBe("completed");
    // reasoning_md must not be cleared
    expect(getMessages()[0].reasoning_md).toBe("thinking content");
  });

  it("preserves streamed reasoning_md when message.completed payload has empty reasoning_md", () => {
    const { handler, getMessages } = setupHandler([
      makeStreamingAssistant({
        reasoning_status: "completed",
        reasoning_md: "accumulated thinking",
        content_md: "partial answer",
      }),
    ]);

    handler({
      event: "message.completed",
      data: {
        id: "msg-1",
        thread_id: "thread-1",
        content_md: "final answer",
        submission_mode: "chat",
        resolved_intent: "explain",
        citations: [],
        action_proposals: [],
        tool_trace: [],
        evidence: [],
        response_cards: [],
        supplement_candidates: [],
        persisted_supplements: [],
        // Server sends empty reasoning_md — frontend must keep its accumulated content
        reasoning_md: "",
        reasoning_status: "completed",
      },
    });

    expect(getMessages()[0].reasoning_md).toBe("accumulated thinking");
    expect(getMessages()[0].reasoning_status).toBe("completed");
    expect(getMessages()[0].content_md).toBe("final answer");
  });

  it("preserves streamed reasoning_md when message.completed payload omits reasoning fields", () => {
    const { handler, getMessages } = setupHandler([
      makeStreamingAssistant({
        reasoning_status: "completed",
        reasoning_md: "accumulated thinking",
        content_md: "partial answer",
      }),
    ]);

    handler({
      event: "message.completed",
      data: {
        id: "msg-1",
        thread_id: "thread-1",
        content_md: "final answer",
        submission_mode: "chat",
        resolved_intent: "explain",
        citations: [],
        action_proposals: [],
        tool_trace: [],
        evidence: [],
        response_cards: [],
        supplement_candidates: [],
        persisted_supplements: [],
        // Server omits reasoning_md and reasoning_status entirely
      },
    });

    expect(getMessages()[0].reasoning_md).toBe("accumulated thinking");
    expect(getMessages()[0].reasoning_status).toBe("completed");
  });

  it("infers reasoning_status=completed when payload has reasoning_md but no reasoning_status", () => {
    const { handler, getMessages } = setupHandler([
      makeStreamingAssistant({
        reasoning_status: null,
        reasoning_md: null,
        content_md: "",
      }),
    ]);

    handler({
      event: "message.completed",
      data: {
        id: "msg-1",
        thread_id: "thread-1",
        content_md: "final answer",
        submission_mode: "chat",
        resolved_intent: "explain",
        citations: [],
        action_proposals: [],
        tool_trace: [],
        evidence: [],
        response_cards: [],
        supplement_candidates: [],
        persisted_supplements: [],
        // Server sends reasoning_md but omits reasoning_status
        reasoning_md: "server-side reasoning content",
      },
    });

    expect(getMessages()[0].reasoning_md).toBe("server-side reasoning content");
    // Must infer "completed" from the presence of reasoning_md
    expect(getMessages()[0].reasoning_status).toBe("completed");
  });

  it("does not leave reasoning in streaming after message.interrupted", () => {
    const { handler, getMessages } = setupHandler([
      makeStreamingAssistant({
        reasoning_status: "streaming",
        reasoning_md: "partial thinking",
        content_md: "partial answer",
      }),
    ]);

    handler({ event: "message.interrupted", data: { content_md: "partial answer" } });

    expect(getMessages()[0].status).toBe("interrupted");
    expect(getMessages()[0].reasoning_status).toBe("completed");
    expect(getMessages()[0].reasoning_md).toBe("partial thinking");
  });

  it("does not leave reasoning in streaming after message.interrupted with empty reasoning_md", () => {
    // reasoning.started fired but no delta arrived yet
    const { handler, getMessages } = setupHandler([
      makeStreamingAssistant({
        reasoning_status: "streaming",
        reasoning_md: "",
        content_md: "",
      }),
    ]);

    handler({ event: "message.interrupted", data: {} });

    expect(getMessages()[0].status).toBe("interrupted");
    // Even with empty reasoning_md, streaming status must not persist
    expect(getMessages()[0].reasoning_status).toBe("completed");
  });

  it("keeps reasoning_status unchanged on interrupt when reasoning was never started", () => {
    const { handler, getMessages } = setupHandler([
      makeStreamingAssistant({
        reasoning_status: null,
        reasoning_md: null,
        content_md: "partial",
      }),
    ]);

    handler({ event: "message.interrupted", data: {} });

    expect(getMessages()[0].status).toBe("interrupted");
    expect(getMessages()[0].reasoning_status).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// createSseMessageHandler – context.compacting & CONTEXT_TOO_LARGE tests
// ---------------------------------------------------------------------------

describe("createSseMessageHandler – context compression UX", () => {
  let rafCallbacks: FrameRequestCallback[] = [];
  let rafIdCounter = 1;

  beforeEach(() => {
    rafCallbacks = [];
    rafIdCounter = 1;
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
      const id = rafIdCounter++;
      rafCallbacks.push(cb);
      return id;
    });
    vi.stubGlobal("cancelAnimationFrame", () => {});
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function flushRaf() {
    const callbacks = [...rafCallbacks];
    rafCallbacks = [];
    for (const cb of callbacks) {
      cb(0);
    }
  }

  type Msg = ReaderAskUiMessageDto;

  function makeStreamingAssistant(overrides: Partial<Msg> = {}): Msg {
    return {
      id: "msg-1",
      thread_id: "thread-1",
      role: "assistant",
      status: "streaming",
      content_md: "",
      reasoning_md: null,
      reasoning_status: null,
      context_anchors: [],
      citations: [],
      action_proposals: [],
      tool_trace: [],
      evidence: [],
      trace_summary: null,
      disambiguation: null,
      external_asset_disambiguation: null,
      response_cards: [],
      supplement_candidates: [],
      persisted_supplements: [],
      created_at: "2026-05-20T00:00:00Z",
      updated_at: "2026-05-20T00:00:00Z",
      ...overrides,
    };
  }

  function setupHandler(messages: Msg[]) {
    let updatedMessages: Msg[] = messages;
    const updateMessage = (updater: (msgs: Msg[]) => Msg[]) => {
      updatedMessages = updater(updatedMessages);
    };
    const onError = vi.fn();
    const handler = createSseMessageHandler("msg-1", updateMessage, undefined, onError);
    return {
      getMessages: () => updatedMessages,
      handler,
      onError,
    };
  }

  it("sets compacting to true on context.compacting event", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);

    handler({ event: "context.compacting", data: { message_id: "msg-1" } });
    flushRaf();

    expect(getMessages()[0].compacting).toBe(true);
  });

  it("resets compacting to false on message.delta", () => {
    const { handler, getMessages } = setupHandler([
      makeStreamingAssistant({ compacting: true }),
    ]);

    handler({ event: "message.delta", data: { message_id: "msg-1", delta: "开始回答" } });
    flushRaf();

    expect(getMessages()[0].compacting).toBe(false);
    expect(getMessages()[0].content_md).toBe("开始回答");
  });

  it("resets compacting to false on message.completed", () => {
    const { handler, getMessages } = setupHandler([
      makeStreamingAssistant({ compacting: true, content_md: "部分回答" }),
    ]);

    handler({
      event: "message.completed",
      data: {
        id: "msg-1",
        thread_id: "thread-1",
        content_md: "最终回答",
        submission_mode: "chat",
        resolved_intent: "explain",
        citations: [],
        action_proposals: [],
        tool_trace: [],
        evidence: [],
        response_cards: [],
        supplement_candidates: [],
        persisted_supplements: [],
      },
    });

    expect(getMessages()[0].compacting).toBe(false);
  });

  it("resets compacting to false on message.interrupted", () => {
    const { handler, getMessages } = setupHandler([
      makeStreamingAssistant({ compacting: true, content_md: "中断的回答" }),
    ]);

    handler({ event: "message.interrupted", data: { content_md: "中断的回答" } });

    expect(getMessages()[0].compacting).toBe(false);
    expect(getMessages()[0].status).toBe("interrupted");
  });

  it("calls onError with user_message for CONTEXT_TOO_LARGE error", () => {
    const { handler, onError } = setupHandler([makeStreamingAssistant()]);

    handler({
      event: "error",
      data: {
        code: "CONTEXT_TOO_LARGE",
        detail: "Context exceeds budget even after aggressive compaction.",
        user_message: "当前对话上下文过长，无法继续。请尝试精简问题或开始新对话。",
      },
    });

    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError).toHaveBeenCalledWith(
      "当前对话上下文过长，无法继续。请尝试精简问题或开始新对话。",
    );
  });

  it("clears compacting and replan_status on error event", () => {
    const { handler, getMessages } = setupHandler([
      makeStreamingAssistant({ compacting: true, replan_status: "replanning" }),
    ]);

    handler({
      event: "error",
      data: {
        code: "CONTEXT_TOO_LARGE",
        detail: "Context exceeds budget even after aggressive compaction.",
        user_message: "当前对话上下文过长，无法继续。请尝试精简问题或开始新对话。",
      },
    });

    expect(getMessages()[0].status).toBe("failed");
    expect(getMessages()[0].compacting).toBe(false);
    expect(getMessages()[0].replan_status).toBe("idle");
  });
});
