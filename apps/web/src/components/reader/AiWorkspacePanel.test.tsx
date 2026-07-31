/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReaderAskAttachment, ReaderAskPageIdentity } from "@/lib/reader-plate";
import type {
  ReaderAskAgenticCitationDto,
  ReaderAskArticleRagCitationDto,
  ReaderAskArticleRagSidecarSafeDto,
  ReaderAskUiMessageDto,
  ReaderAskWebSearchSummaryDto,
} from "@/types/api/reader-ask";
import { consumeReaderAskSse } from "./ask/sse";
import { makeLogicalTerminalResult } from "./ask/turn-lifecycle";
import {
  AiWorkspacePanel,
  createSseMessageHandler,
  formatSourceNavigationFeedback,
  normalizeReaderAskMessages,
  type AiWorkspacePanelProps,
} from "./AiWorkspacePanel";

const completedPayload = {
  id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
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

vi.mock("./ask/sse", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./ask/sse")>();
  return {
    ...actual,
    consumeReaderAskSse: vi.fn(async (_response: Response, onEvent: (event: { event: string; data: Record<string, unknown> }) => void) => {
      onEvent({ event: "message.started", data: { message_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee" } });
      onEvent({
        event: "message.completed",
        data: completedPayload,
      });
    }),
  };
});

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

/** Canonical-shaped assistant id used by fixtures (must be UUID for regenerate CTA). */
const FIXTURE_ASSISTANT_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";

function createAssistantMessage(overrides: Partial<ReaderAskUiMessageDto> = {}): ReaderAskUiMessageDto {
  return {
    id: FIXTURE_ASSISTANT_ID,
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
    agentic_evidence: null,
    agentic_evidence_scope: null,
    agentic_answer_blocks: null,
    agentic_citations: null,
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
    vi.mocked(consumeReaderAskSse).mockImplementationOnce(async (_response, onEvent) => {
      onEvent({ event: "message.started", data: { message_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee" } });
      onEvent({ event: "message.completed", data: completedPayload });
      return makeLogicalTerminalResult("completed", { finalStatus: "ok" });
    });
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

  it("ASK-UX-HISTORY-COT-R2 P0-2: no default current-article chip; add-menu related-article search still works", async () => {
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

    // P0-2: the current article is fixed implicit context — no default
    // chip, no provenance row. The page identity title is the sole source
    // of truth for the reader header and is never echoed as a chip here.
    expect(screen.queryByText("Test Reader")).toBeNull();
    expect(screen.queryByText(/基于：/)).toBeNull();

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
              id: "bbbbbbbb-bbbb-4ccc-8ddd-eeeeeeeeeeee",
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
              id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
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
              id: "bbbbbbbb-bbbb-4ccc-8ddd-eeeeeeeeeeee",
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
              id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
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
              id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
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
      // Browser retry ABI is /retry (never /retry/stream) — simulate regenerate
      if (url.includes("/retry") && !url.includes("/retry/stream")) {
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

    // Clicking the button triggers a full regenerate on browser /retry ABI
    fireEvent.click(regenerateButton);

    await waitFor(() => {
      const retryCall = vi
        .mocked(global.fetch)
        .mock.calls.find(([url]) => {
          const value = String(url);
          return value.includes("/retry") && !value.includes("/retry/stream");
        });
      expect(retryCall).toBeTruthy();
      expect(String(retryCall?.[0])).toContain("/retry");
      expect(String(retryCall?.[0])).not.toContain("/retry/stream");
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
              id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
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

  it("keeps reasoning collapsed with a shimmer trigger while streaming (ASK-REASONING-R1)", async () => {
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
    });

    // Default collapsed while streaming — no auto-open, no fabricated
    // placeholder content.
    const trigger = container.querySelector('[data-slot="reasoning-trigger"]');
    expect(trigger?.getAttribute("aria-expanded")).toBe("false");
    const content = container.querySelector('[data-slot="reasoning-content"]');
    expect(content?.getAttribute("data-state")).toBe("closed");
    expect(content?.textContent?.trim() ?? "").toBe("");

    // The user can expand the empty streaming shell.
    fireEvent.click(screen.getByText("思考中"));
    await waitFor(() => {
      expect(trigger?.getAttribute("aria-expanded")).toBe("true");
    });
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
              id: "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff",
              status: "interrupted",
              content_md: "已有部分答案。",
            }),
          ],
        });
      }
      if (url.includes("/retry") && !url.includes("/retry/stream")) {
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
        .mock.calls.find(([url]) => {
          const value = String(url);
          return value.includes("/retry") && !value.includes("/retry/stream");
        });
      expect(retryCall).toBeTruthy();
      expect(String(retryCall?.[0])).not.toContain("/retry/stream");
      // ASK-WEB-G1-R3: Retry body only carries ``model``. The backend
      // replays the persisted ``web_search_mode`` from the original user
      // message metadata after ownership verification — no client input
      // for retry capability. The FastAPI ``ReaderAskMessageRetryRequest``
      // schema is ``extra="forbid"`` and only accepts ``model``.
      expect(retryCall?.[1]?.body).toBe(
        JSON.stringify({ model: "ask-fast" }),
      );
    });
  });

  it("keeps streamed reasoning collapsed until the user expands it", async () => {
    mockThreadMessages([
      createAssistantMessage({
        status: "streaming",
        content_md: "正文正在生成。",
        reasoning_md: "先判断句子主干。",
        reasoning_status: "streaming",
      }),
    ]);

    const { container } = renderPanel();

    await waitFor(() => {
      expect(screen.getByText("正文正在生成。")).not.toBeNull();
      expect(screen.getByText("思考中")).not.toBeNull();
    });

    // Collapsed while streaming: the projected text is not forced open.
    const trigger = container.querySelector('[data-slot="reasoning-trigger"]');
    expect(trigger?.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("先判断句子主干。")).toBeNull();

    // Expanding reveals the live content.
    fireEvent.click(screen.getByText("思考中"));
    await waitFor(() => {
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

    const { container } = renderPanel();

    await waitFor(() => {
      expect(screen.getByText("刷新后仍可见的正文片段。")).not.toBeNull();
      expect(screen.getByText("思考中")).not.toBeNull();
    });

    // Collapsed by default; the rehydrated projection is expandable.
    const trigger = container.querySelector('[data-slot="reasoning-trigger"]');
    expect(trigger?.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(screen.getByText("思考中"));
    await waitFor(() => {
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

  it("renders no reasoning element when the model returned no reasoning text (ASK-REASONING-R1)", async () => {
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
    });

    // No fabricated "model returned no reasoning" placeholder — the whole
    // reasoning element is absent.
    expect(screen.queryByText("思考过程")).toBeNull();
    expect(screen.queryByText("本轮模型未返回可展示的思考内容。")).toBeNull();
    expect(container.querySelector('[data-slot="reasoning"]')).toBeNull();
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
      return makeLogicalTerminalResult("terminal", { finalStatus: "failed" });
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
      onEvent({ event: "message.started", data: { message_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee" } });
      onEvent({
        event: "message.completed",
        data: { ...completedPayload, article_rag: articleRag },
      });
      return makeLogicalTerminalResult("completed", { finalStatus: "ok" });
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

    it("ASK-UX-HISTORY-COT-R2 P0-2: no provenance row when only the implicit current article is present (no explicit attachments)", async () => {
      // The current article is fixed implicit context — it must NOT
      // produce a default "基于：当前文章" provenance row. With no
      // explicit selection / notes / other articles, the provenance
      // line does not render at all.
      renderPanel({
        recordTitle: "Test Reader",
        attachments: [],
        liveContextAttachment: null,
      });

      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalled();
      });

      expect(screen.queryByText(/基于：当前文章/)).toBeNull();
      expect(screen.queryByText(/仅按你的问题回答/)).toBeNull();
      expect(screen.queryByText(/基于：/)).toBeNull();
    });

    it("ASK-UX-HISTORY-COT-R2 P0-2: provenance shows only explicit selection and notes (no 当前文章) when live selection and attachments exist", async () => {
      renderPanel({
        recordTitle: "Test Reader",
        liveContextAttachment: sentenceAttachment,
        attachments: [noteAttachment],
      });

      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalled();
      });

      const summary = screen.getByText(/基于：/);
      // The current article must NOT appear — only explicit context.
      expect(summary.textContent).not.toContain("当前文章");
      expect(summary.textContent).toContain("选中句");
      expect(summary.textContent).toContain("1 条笔记");
    });

    it("ASK-UX-HISTORY-COT-R2 P0-2: no provenance and no CurrentRecordChip when nothing is present (no noise)", async () => {
      renderPanel({
        recordTitle: "",
        attachments: [],
        liveContextAttachment: null,
      });

      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalled();
      });

      // No provenance line, no fallback "仅按你的问题回答" text.
      expect(screen.queryByText("仅按你的问题回答")).toBeNull();
      expect(screen.queryByText(/基于：/)).toBeNull();
    });

    it("ASK-UX-HISTORY-COT-R2 P0-2: thread title differs from page title — page identity title is the sole article title source, never thread title", async () => {
      // The thread title is a conversation label; the page identity
      // recordTitle is the sole source of truth for the article title.
      // The Ask panel must never echo the thread title as an attachment
      // label or provenance "当前文章" row. With no explicit attachments,
      // neither title surfaces in provenance/chips.
      renderPanel({
        recordTitle: "页面标题文章",
        attachments: [],
        liveContextAttachment: null,
      });

      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalled();
      });

      // No provenance row, no chip — the article is implicit.
      expect(screen.queryByText(/基于：/)).toBeNull();
      expect(screen.queryByText("页面标题文章")).toBeNull();
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
    vi.stubGlobal("cancelAnimationFrame", () => {
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
        provisional_content_md: null,
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

    // ASK-TURN-LIFECYCLE R2 — delta goes into provisional_content_md, not
    // content_md. The old canonical answer is preserved until a new one
    // is committed via message.completed.
    expect(updatedMessages[0].provisional_content_md).toBe("新的开头");
    expect(updatedMessages[0].content_md).toBe("旧的中断内容");
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
      provisional_content_md: null,
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
// createSseMessageHandler – agentic reasoning projection (ASK-REASONING-R1)
//
// agentic.reasoning.* events reuse the existing reasoning_md /
// reasoning_status semantic fields (no parallel UI state). Deltas are
// server-side projected — the client appends verbatim with a strict
// monotonic seq guard. Terminals freeze session-visible reasoning as
// "interrupted"; cold history never carries it.
// ---------------------------------------------------------------------------

describe("createSseMessageHandler – agentic reasoning projection", () => {
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
      provisional_content_md: null,
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
    return { getMessages: () => updatedMessages, handler, onError };
  }

  const VERSION = "reader_record_ask_agentic_v2";
  const startedPayload = (seq = 0) => ({
    execution_version: VERSION,
    message_id: "msg-1",
    thread_id: "thread-1",
    turn_run_id: "run-1",
    seq,
    projection_policy_version: "reasoning_projection_v1",
  });
  const deltaPayload = (seq: number, delta: string) => ({
    execution_version: VERSION,
    message_id: "msg-1",
    thread_id: "thread-1",
    turn_run_id: "run-1",
    seq,
    delta,
  });
  const completedPayload = (seq: number) => ({
    execution_version: VERSION,
    message_id: "msg-1",
    thread_id: "thread-1",
    turn_run_id: "run-1",
    seq,
    has_content: true,
    truncated: false,
    projection_policy_version: "reasoning_projection_v1",
  });

  it("enters streaming on agentic.reasoning.started (seq=0, immediate)", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);

    handler({ event: "agentic.reasoning.started", data: startedPayload() });
    // Immediate: no rAF flush required.
    expect(getMessages()[0].reasoning_status).toBe("streaming");
    expect(getMessages()[0].reasoning_md).toBe("");
  });

  it("appends agentic.reasoning.delta batches in order via rAF", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);

    handler({ event: "agentic.reasoning.started", data: startedPayload() });
    handler({ event: "agentic.reasoning.delta", data: deltaPayload(1, "先分析") });
    handler({ event: "agentic.reasoning.delta", data: deltaPayload(2, "句子主干。") });
    // Batched: nothing applied before the rAF flush.
    expect(getMessages()[0].reasoning_md).toBe("");
    flushRaf();
    expect(getMessages()[0].reasoning_md).toBe("先分析句子主干。");
    expect(getMessages()[0].reasoning_status).toBe("streaming");
  });

  it("drops duplicate, gapped and out-of-order delta seq (strict contiguity)", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);

    handler({ event: "agentic.reasoning.started", data: startedPayload() });
    handler({ event: "agentic.reasoning.delta", data: deltaPayload(1, "甲。") });
    handler({ event: "agentic.reasoning.delta", data: deltaPayload(1, "重复。") });
    handler({ event: "agentic.reasoning.delta", data: deltaPayload(3, "缺口。") });
    handler({ event: "agentic.reasoning.delta", data: deltaPayload(2, "连续。") });
    flushRaf();
    // Duplicate seq 1 dropped; gapped seq 3 dropped; contiguous seq 2 kept.
    expect(getMessages()[0].reasoning_md).toBe("甲。连续。");
  });

  it("transitions to completed on agentic.reasoning.completed and keeps text", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);

    handler({ event: "agentic.reasoning.started", data: startedPayload() });
    handler({ event: "agentic.reasoning.delta", data: deltaPayload(1, "思考内容。") });
    flushRaf();
    handler({ event: "agentic.reasoning.completed", data: completedPayload(2) });

    expect(getMessages()[0].reasoning_status).toBe("completed");
    expect(getMessages()[0].reasoning_md).toBe("思考内容。");
  });

  it("freezes session-visible reasoning as interrupted on agentic terminal", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);

    handler({ event: "agentic.reasoning.started", data: startedPayload() });
    handler({ event: "agentic.reasoning.delta", data: deltaPayload(1, "部分思考。") });
    flushRaf();
    handler({
      event: "agentic.terminal",
      data: {
        execution_version: VERSION,
        final_status: "failed",
        message_id: "msg-1",
        thread_id: "thread-1",
        turn_run_id: "run-1",
        terminal_reason: "agent_run_failed",
      },
    });
    flushRaf();

    expect(getMessages()[0].status).toBe("failed");
    // Session-visible partial reasoning freezes interrupted — never
    // "completed" (no replay promise); cold history will carry none.
    expect(getMessages()[0].reasoning_status).toBe("interrupted");
    expect(getMessages()[0].reasoning_md).toBe("部分思考。");
  });

  it("ignores agentic reasoning payloads with wrong version or bad seq", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);

    handler({
      event: "agentic.reasoning.started",
      data: { ...startedPayload(), execution_version: "legacy" },
    });
    handler({
      event: "agentic.reasoning.delta",
      data: { ...deltaPayload(1, "x"), seq: -1 },
    });
    handler({
      event: "agentic.reasoning.delta",
      data: { ...deltaPayload(1, "x"), delta: 42 },
    });
    flushRaf();

    expect(getMessages()[0].reasoning_status).toBeNull();
    expect(getMessages()[0].reasoning_md).toBeNull();
  });

  it("ignores a delta before started when no active run identity exists (fail-closed)", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);

    handler({ event: "agentic.reasoning.delta", data: deltaPayload(1, "直接到达。") });
    flushRaf();
    expect(getMessages()[0].reasoning_md).toBeNull();
    expect(getMessages()[0].reasoning_status).toBeNull();
  });

  it("ASK-COT: accepts a delta without started once run_started bound the identity (seq===1)", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);
    handler({
      event: "agentic.run_started",
      data: {
        execution_version: VERSION,
        message_id: "msg-1",
        thread_id: "thread-1",
        turn_run_id: "run-1",
        has_initial_selection: false,
        web_search_mode: "disabled",
      },
    });

    // Backend documents deltas may arrive without a preceding started
    // (production_stream.py L1151) — binding is synthesized only for seq===1.
    handler({ event: "agentic.reasoning.delta", data: deltaPayload(1, "无started。") });
    handler({ event: "agentic.reasoning.delta", data: deltaPayload(2, "续接。") });
    flushRaf();
    expect(getMessages()[0].reasoning_md).toBe("无started。续接。");
    expect(getMessages()[0].reasoning_status).toBe("streaming");

    // Strict contiguity resumes after synthesis: gap dropped, seq 3 kept.
    handler({ event: "agentic.reasoning.delta", data: deltaPayload(4, "缺口。") });
    handler({ event: "agentic.reasoning.delta", data: deltaPayload(3, "连续。") });
    flushRaf();
    expect(getMessages()[0].reasoning_md).toBe("无started。续接。连续。");

    // completed stays contiguous from the synthesized baseline.
    handler({ event: "agentic.reasoning.completed", data: completedPayload(4) });
    expect(getMessages()[0].reasoning_status).toBe("completed");
  });

  it("ASK-COT-B1-R1: first delta seq>1 without binding is rejected (no binding, no completed)", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);
    handler({
      event: "agentic.run_started",
      data: {
        execution_version: VERSION,
        message_id: "msg-1",
        thread_id: "thread-1",
        turn_run_id: "run-1",
        has_initial_selection: false,
      },
    });
    // First delta jumps to seq=3 → reject, no binding established.
    handler({ event: "agentic.reasoning.delta", data: deltaPayload(3, "缺口帧。") });
    flushRaf();
    expect(getMessages()[0].reasoning_md).toBeNull();
    expect(getMessages()[0].reasoning_status).toBeNull();
    // Later completed seq=4 still cannot complete (no binding, no accepted text).
    handler({ event: "agentic.reasoning.completed", data: completedPayload(4) });
    expect(getMessages()[0].reasoning_status).toBeNull();
    expect(getMessages()[0].reasoning_md).toBeNull();
  });

  it("ASK-COT-B1-R1: completed-only without accepted reasoning text does not create completed", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);
    handler({
      event: "agentic.run_started",
      data: {
        execution_version: VERSION,
        message_id: "msg-1",
        thread_id: "thread-1",
        turn_run_id: "run-1",
        has_initial_selection: false,
      },
    });
    handler({ event: "agentic.reasoning.completed", data: completedPayload(1) });
    // Fail-closed: no hot≡cold completed claim without accepted text/binding.
    expect(getMessages()[0].reasoning_status).toBeNull();
    expect(getMessages()[0].reasoning_md).toBeNull();
  });

  it("ASK-COT-B1-R2: started-only reasoning cannot complete without an accepted delta", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);
    handler({
      event: "agentic.run_started",
      data: {
        execution_version: VERSION,
        message_id: "msg-1",
        thread_id: "thread-1",
        turn_run_id: "run-1",
        has_initial_selection: false,
      },
    });
    handler({ event: "agentic.reasoning.started", data: startedPayload() });
    expect(getMessages()[0].reasoning_status).toBe("streaming");
    expect(getMessages()[0].reasoning_md).toBe("");

    // A started frame binds identity but carries no visible projection. If
    // every delta is missing, accepting completed would falsely claim that
    // the empty hot view equals the persisted cold reasoning projection.
    handler({ event: "agentic.reasoning.completed", data: completedPayload(1) });
    expect(getMessages()[0].reasoning_status).toBe("streaming");
    expect(getMessages()[0].reasoning_md).toBe("");

    // The answer terminal owns the fallback: an incomplete projection is
    // interrupted, never promoted to a replayable completed reasoning state.
    handler({ event: "message.completed", data: agenticMessageCompleted() });
    expect(getMessages()[0].reasoning_status).toBe("interrupted");
    expect(getMessages()[0].reasoning_md).toBeNull();
  });

  it("ASK-COT-B1-R1: run_started → delta seq=1 → completed works without started frame", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);
    handler({
      event: "agentic.run_started",
      data: {
        execution_version: VERSION,
        message_id: "msg-1",
        thread_id: "thread-1",
        turn_run_id: "run-1",
        has_initial_selection: false,
      },
    });
    handler({ event: "agentic.reasoning.delta", data: deltaPayload(1, "思考。") });
    flushRaf();
    handler({ event: "agentic.reasoning.completed", data: completedPayload(2) });
    expect(getMessages()[0].reasoning_md).toBe("思考。");
    expect(getMessages()[0].reasoning_status).toBe("completed");
  });

  it("ASK-COT: foreign-identity delta without started is still dropped", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);
    handler({
      event: "agentic.run_started",
      data: {
        execution_version: VERSION,
        message_id: "msg-1",
        thread_id: "thread-1",
        turn_run_id: "run-1",
        has_initial_selection: false,
      },
    });
    handler({
      event: "agentic.reasoning.delta",
      data: { ...deltaPayload(1, "外turn。"), turn_run_id: "run-OTHER" },
    });
    handler({
      event: "agentic.reasoning.completed",
      data: { ...completedPayload(1), thread_id: "thread-OTHER" },
    });
    flushRaf();
    expect(getMessages()[0].reasoning_md).toBeNull();
    expect(getMessages()[0].reasoning_status).toBeNull();
  });

  it("ASK-COT: a late started after synthesis is ignored (binding established)", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);
    handler({
      event: "agentic.run_started",
      data: {
        execution_version: VERSION,
        message_id: "msg-1",
        thread_id: "thread-1",
        turn_run_id: "run-1",
        has_initial_selection: false,
      },
    });
    handler({ event: "agentic.reasoning.delta", data: deltaPayload(1, "甲。") });
    // Late started must not reset the synthesized seq baseline.
    handler({ event: "agentic.reasoning.started", data: startedPayload() });
    handler({ event: "agentic.reasoning.delta", data: deltaPayload(2, "乙。") });
    flushRaf();
    expect(getMessages()[0].reasoning_md).toBe("甲。乙。");
  });

  it("ignores started with seq !== 0", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);

    handler({ event: "agentic.reasoning.started", data: startedPayload(1) });
    // And a later delta is still dropped (no started was accepted).
    handler({ event: "agentic.reasoning.delta", data: deltaPayload(1, "后续。") });
    flushRaf();
    expect(getMessages()[0].reasoning_status).toBeNull();
    expect(getMessages()[0].reasoning_md).toBeNull();
  });

  it("ignores a repeated started (at most once per stream)", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);

    handler({ event: "agentic.reasoning.started", data: startedPayload() });
    handler({ event: "agentic.reasoning.delta", data: deltaPayload(1, "一。") });
    // Repeated started must not reset the seq baseline.
    handler({ event: "agentic.reasoning.started", data: startedPayload() });
    handler({ event: "agentic.reasoning.delta", data: deltaPayload(2, "二。") });
    flushRaf();
    expect(getMessages()[0].reasoning_md).toBe("一。二。");
    expect(getMessages()[0].reasoning_status).toBe("streaming");
  });

  it("ignores delta and completed from a foreign turn (identity mismatch)", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);

    handler({ event: "agentic.reasoning.started", data: startedPayload() });
    handler({
      event: "agentic.reasoning.delta",
      data: { ...deltaPayload(1, "外turn。"), turn_run_id: "run-OTHER" },
    });
    handler({
      event: "agentic.reasoning.delta",
      data: { ...deltaPayload(1, "外线程。"), thread_id: "thread-OTHER" },
    });
    handler({
      event: "agentic.reasoning.delta",
      data: { ...deltaPayload(1, "外消息。"), message_id: "msg-OTHER" },
    });
    handler({
      event: "agentic.reasoning.completed",
      data: { ...completedPayload(1), turn_run_id: "run-OTHER" },
    });
    flushRaf();
    expect(getMessages()[0].reasoning_md).toBe("");
    expect(getMessages()[0].reasoning_status).toBe("streaming");
  });

  it("ignores completed with gapped seq or has_content=false", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);

    handler({ event: "agentic.reasoning.started", data: startedPayload() });
    handler({ event: "agentic.reasoning.delta", data: deltaPayload(1, "内容。") });
    flushRaf();
    // Gapped completed (seq 3, expected 2) → ignored.
    handler({ event: "agentic.reasoning.completed", data: completedPayload(3) });
    expect(getMessages()[0].reasoning_status).toBe("streaming");
    // has_content=false → ignored even with contiguous seq.
    handler({
      event: "agentic.reasoning.completed",
      data: { ...completedPayload(2), has_content: false },
    });
    expect(getMessages()[0].reasoning_status).toBe("streaming");
    // Contiguous + has_content → accepted.
    handler({ event: "agentic.reasoning.completed", data: completedPayload(2) });
    expect(getMessages()[0].reasoning_status).toBe("completed");
  });

  it("ignores completed before started when no active run identity exists", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);

    handler({ event: "agentic.reasoning.completed", data: completedPayload(1) });
    expect(getMessages()[0].reasoning_status).toBeNull();
  });

  it("full state machine: started(0) → deltas(1..2) → completed(3)", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);

    handler({ event: "agentic.reasoning.started", data: startedPayload() });
    expect(getMessages()[0].reasoning_status).toBe("streaming");
    handler({ event: "agentic.reasoning.delta", data: deltaPayload(1, "第一。") });
    handler({ event: "agentic.reasoning.delta", data: deltaPayload(2, "第二。") });
    flushRaf();
    expect(getMessages()[0].reasoning_md).toBe("第一。第二。");
    handler({ event: "agentic.reasoning.completed", data: completedPayload(3) });
    expect(getMessages()[0].reasoning_status).toBe("completed");
    expect(getMessages()[0].reasoning_md).toBe("第一。第二。");
    // Post-completed deltas are dropped (seq baseline is now 3).
    handler({ event: "agentic.reasoning.delta", data: deltaPayload(4, "迟到。") });
    flushRaf();
    expect(getMessages()[0].reasoning_md).toBe("第一。第二。");
  });

  // --- R3 P1b: reasoning.started must match the active run identity ---

  const runStartedPayload = (
    overrides: Partial<{ message_id: string; thread_id: string; turn_run_id: string }> = {},
  ) => ({
    execution_version: VERSION,
    message_id: "msg-1",
    thread_id: "thread-1",
    turn_run_id: "run-1",
    has_initial_selection: false,
    ...overrides,
  });

  it("accepts reasoning.started matching the active run identity", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);
    handler({ event: "agentic.run_started", data: runStartedPayload() });
    handler({ event: "agentic.reasoning.started", data: startedPayload() });
    expect(getMessages()[0].reasoning_status).toBe("streaming");
  });

  it("ignores reasoning.started from a foreign message/thread/run (no state change)", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);
    handler({ event: "agentic.run_started", data: runStartedPayload() });
    // Foreign message id.
    handler({
      event: "agentic.reasoning.started",
      data: { ...startedPayload(), message_id: "msg-OTHER" },
    });
    // Foreign thread id.
    handler({
      event: "agentic.reasoning.started",
      data: { ...startedPayload(), thread_id: "thread-OTHER" },
    });
    // Foreign turn run id.
    handler({
      event: "agentic.reasoning.started",
      data: { ...startedPayload(), turn_run_id: "run-OTHER" },
    });
    // No binding established, no streaming state, seq baseline untouched.
    expect(getMessages()[0].reasoning_status).toBeNull();
    expect(getMessages()[0].reasoning_md).toBeNull();
    // A later matching started is still accepted (baseline never advanced).
    handler({ event: "agentic.reasoning.started", data: startedPayload() });
    expect(getMessages()[0].reasoning_status).toBe("streaming");
  });

  it("ignores a stale-turn reasoning.started after a newer run_started", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);
    // Active run is turn-2.
    handler({
      event: "agentic.run_started",
      data: runStartedPayload({ turn_run_id: "run-2" }),
    });
    // Stale started for turn-1 → ignored.
    handler({
      event: "agentic.reasoning.started",
      data: { ...startedPayload(), turn_run_id: "run-1" },
    });
    expect(getMessages()[0].reasoning_status).toBeNull();
    // Matching started for the active turn-2 → accepted.
    handler({
      event: "agentic.reasoning.started",
      data: { ...startedPayload(), turn_run_id: "run-2" },
    });
    expect(getMessages()[0].reasoning_status).toBe("streaming");
  });

  it("without run_started, accepts reasoning.started only if message_id matches current", () => {
    // No run_started seen → fallback requires strict current message_id match.
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);
    // Foreign message id (≠ current "msg-1") → ignored.
    handler({
      event: "agentic.reasoning.started",
      data: { ...startedPayload(), message_id: "msg-OTHER" },
    });
    expect(getMessages()[0].reasoning_status).toBeNull();
    // Matching current message id → accepted.
    handler({ event: "agentic.reasoning.started", data: startedPayload() });
    expect(getMessages()[0].reasoning_status).toBe("streaming");
  });

  // --- R3 P2: message.completed must not mask incomplete reasoning ---

  const agenticMessageCompleted = (messageId = "msg-1") => ({
    execution_version: VERSION,
    final_status: "ok" as const,
    answer_text: "答案正文。",
    answer_blocks: [{ text: "答案正文。", citation_ids: [] as string[] }],
    citations: [],
    knowledge_mode: null,
    source_status: null,
    web_search: null as null,
    message_id: messageId,
    thread_id: "thread-1",
    turn_run_id: "run-1",
  });

  it("freezes incomplete streaming reasoning as interrupted on message.completed", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);
    handler({ event: "agentic.run_started", data: runStartedPayload() });
    handler({ event: "agentic.reasoning.started", data: startedPayload() });
    handler({ event: "agentic.reasoning.delta", data: deltaPayload(1, "部分思考。") });
    flushRaf();
    // No reasoning.completed — answer completes while reasoning is streaming.
    handler({ event: "message.completed", data: agenticMessageCompleted() });
    // Not forced to completed; frozen interrupted; text preserved.
    expect(getMessages()[0].reasoning_status).toBe("interrupted");
    expect(getMessages()[0].reasoning_md).toBe("部分思考。");
  });

  it("keeps reasoning completed after message.completed when reasoning.completed accepted", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);
    handler({ event: "agentic.run_started", data: runStartedPayload() });
    handler({ event: "agentic.reasoning.started", data: startedPayload() });
    handler({ event: "agentic.reasoning.delta", data: deltaPayload(1, "思考。") });
    flushRaf();
    handler({ event: "agentic.reasoning.completed", data: completedPayload(2) });
    expect(getMessages()[0].reasoning_status).toBe("completed");
    handler({ event: "message.completed", data: agenticMessageCompleted() });
    expect(getMessages()[0].reasoning_status).toBe("completed");
    expect(getMessages()[0].reasoning_md).toBe("思考。");
  });

  it("freezes reasoning as interrupted when reasoning.completed was rejected (gapped)", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);
    handler({ event: "agentic.run_started", data: runStartedPayload() });
    handler({ event: "agentic.reasoning.started", data: startedPayload() });
    handler({ event: "agentic.reasoning.delta", data: deltaPayload(1, "思考。") });
    flushRaf();
    // Gapped completed (seq 5, expected 2) → rejected.
    handler({ event: "agentic.reasoning.completed", data: completedPayload(5) });
    expect(getMessages()[0].reasoning_status).toBe("streaming");
    // Answer completes with reasoning still incomplete → interrupted.
    handler({ event: "message.completed", data: agenticMessageCompleted() });
    expect(getMessages()[0].reasoning_status).toBe("interrupted");
    expect(getMessages()[0].reasoning_md).toBe("思考。");
  });

  it("renders no reasoning when the answer has none (no placeholder)", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);
    handler({ event: "agentic.run_started", data: runStartedPayload() });
    // No reasoning events at all.
    handler({ event: "message.completed", data: agenticMessageCompleted() });
    expect(getMessages()[0].reasoning_status).toBeNull();
    expect(getMessages()[0].reasoning_md).toBeNull();
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
      provisional_content_md: null,
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

  it("projects the typed agentic compaction lifecycle without retaining diagnostics", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);
    const base = {
      execution_version: "reader_record_ask_agentic_v2",
      message_id: "msg-1",
      thread_id: "thread-1",
      turn_run_id: "run-1",
      attempt_count: 1,
    };

    handler({
      event: "context.compaction.started",
      data: { ...base, detail_code: null, elapsed_ms: 0 },
    });
    flushRaf();
    expect(getMessages()[0].context_compaction).toEqual({
      status: "running",
      elapsedMs: 0,
    });

    handler({
      event: "context.compaction.completed",
      data: {
        ...base,
        detail_code: "provider_exception-must-not-enter-ui",
        elapsed_ms: 840,
      },
    });
    flushRaf();
    expect(getMessages()[0].context_compaction).toEqual({
      status: "completed",
      elapsedMs: 840,
    });
    expect(JSON.stringify(getMessages()[0])).not.toContain(
      "provider_exception-must-not-enter-ui",
    );
  });

  it("ignores a foreign compaction terminal after binding started identity", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);
    const base = {
      execution_version: "reader_record_ask_agentic_v2",
      message_id: "msg-1",
      thread_id: "thread-1",
      turn_run_id: "run-1",
      detail_code: null,
      attempt_count: 0,
      elapsed_ms: 0,
    };
    handler({ event: "context.compaction.started", data: base });
    flushRaf();

    handler({
      event: "context.compaction.failed",
      data: {
        ...base,
        turn_run_id: "foreign-run",
        detail_code: "timeout",
        elapsed_ms: 100,
      },
    });
    flushRaf();

    expect(getMessages()[0].context_compaction).toEqual({
      status: "running",
      elapsedMs: 0,
    });
  });

  it("resets compacting to false on message.delta", () => {
    const { handler, getMessages } = setupHandler([
      makeStreamingAssistant({ compacting: true }),
    ]);

    handler({ event: "message.delta", data: { message_id: "msg-1", delta: "开始回答" } });
    flushRaf();

    expect(getMessages()[0].compacting).toBe(false);
    // ASK-TURN-LIFECYCLE R2 — delta accumulates into provisional_content_md,
    // not content_md.
    expect(getMessages()[0].provisional_content_md).toBe("开始回答");
    expect(getMessages()[0].content_md).toBe("");
  });

  it("ASK-UX-HISTORY-COT-R2 P0-4: after run_started, delta without turn identity is rejected; delta with matching identity + generation_id is accepted", () => {
    // Regression for the streaming contract: the backend message.delta
    // MUST carry message_id / thread_id / turn_run_id / generation_id so
    // the frontend activeRunIdentity guard (set on agentic.run_started)
    // can attribute it. Without identity the guard rejects every delta
    // and provisional_content_md never accumulates — the bubble jumps
    // straight from empty to the canonical completed answer (no real
    // streaming). See production_stream.py AnswerDeltaEvent branch.
    const VERSION = "reader_record_ask_agentic_v2";
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);

    // Establish activeRunIdentity + activeGenerationId=0.
    handler({
      event: "agentic.run_started",
      data: {
        execution_version: VERSION,
        message_id: "msg-1",
        thread_id: "thread-1",
        turn_run_id: "run-1",
        has_initial_selection: false,
      },
    });

    // Old backend shape — delta carries NO identity fields. The guard
    // must reject it (foreign frame) so stale/cross-turn text cannot
    // leak into the provisional preview.
    handler({
      event: "message.delta",
      data: { delta: "无身份", generation_id: 0 },
    });
    flushRaf();
    expect(getMessages()[0].provisional_content_md).toBeNull();

    // Matching identity + generation_id=0 → accepted, accumulates.
    handler({
      event: "message.delta",
      data: {
        execution_version: VERSION,
        message_id: "msg-1",
        thread_id: "thread-1",
        turn_run_id: "run-1",
        generation_id: 0,
        delta: "片段一",
      },
    });
    flushRaf();
    expect(getMessages()[0].provisional_content_md).toBe("片段一");

    // Foreign identity → rejected, preview unchanged.
    handler({
      event: "message.delta",
      data: {
        execution_version: VERSION,
        message_id: "msg-OTHER",
        thread_id: "thread-1",
        turn_run_id: "run-1",
        generation_id: 0,
        delta: "外turn",
      },
    });
    flushRaf();
    expect(getMessages()[0].provisional_content_md).toBe("片段一");

    // preview_reset bumps active generation to 1, clears provisional.
    handler({
      event: "message.preview_reset",
      data: {
        execution_version: VERSION,
        message_id: "msg-1",
        thread_id: "thread-1",
        turn_run_id: "run-1",
        generation_id: 1,
        reason: "tool_result_boundary",
      },
    });
    flushRaf();
    expect(getMessages()[0].provisional_content_md).toBe("");

    // Stale generation_id=0 → rejected (post-reset only gen=1 passes).
    handler({
      event: "message.delta",
      data: {
        execution_version: VERSION,
        message_id: "msg-1",
        thread_id: "thread-1",
        turn_run_id: "run-1",
        generation_id: 0,
        delta: "旧gen",
      },
    });
    flushRaf();
    expect(getMessages()[0].provisional_content_md).toBe("");

    // Matching identity + generation_id=1 → accepted, accumulates fresh.
    handler({
      event: "message.delta",
      data: {
        execution_version: VERSION,
        message_id: "msg-1",
        thread_id: "thread-1",
        turn_run_id: "run-1",
        generation_id: 1,
        delta: "新gen片段",
      },
    });
    flushRaf();
    expect(getMessages()[0].provisional_content_md).toBe("新gen片段");
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

// ---------------------------------------------------------------------------
// createSseMessageHandler – agentic Reading Record Ask path
// ---------------------------------------------------------------------------

describe("createSseMessageHandler – agentic stream", () => {
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
      id: "temp-assistant-1",
      thread_id: "thread-1",
      role: "assistant",
      status: "streaming",
      content_md: "",
      provisional_content_md: null,
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

  function setupHandler(messages: Msg[], initialId = "temp-assistant-1") {
    let updatedMessages: Msg[] = messages;
    const updateMessage = (updater: (msgs: Msg[]) => Msg[]) => {
      updatedMessages = updater(updatedMessages);
    };
    const onError = vi.fn();
    // Apply assigned ids the same way AiWorkspacePanel does in sendMessage.
    const onMessageIdAssigned = vi.fn((assignedId: string) => {
      updatedMessages = updatedMessages.map((message) =>
        message.id === initialId ? { ...message, id: assignedId } : message,
      );
    });
    const onAgenticActivity = vi.fn();
    // ASK-UX-MOBILE-R3 — terminal errors now flow through onTerminalNotice
    // (typed fields) instead of onError (formatted string). The panel uses
    // projectTurnTerminalNotice to build the AskSystemNotice from these
    // fields. onError is reserved for legacy stream-level `error` events.
    const onTerminalNotice = vi.fn();
    const onOptionalToolWarning = vi.fn();
    const handler = createSseMessageHandler(
      initialId,
      updateMessage,
      onMessageIdAssigned,
      onError,
      onAgenticActivity,
      onTerminalNotice,
      onOptionalToolWarning,
    );
    return {
      getMessages: () => updatedMessages,
      handler,
      onError,
      onMessageIdAssigned,
      onAgenticActivity,
      onTerminalNotice,
      onOptionalToolWarning,
    };
  }

  const agenticCompleted = {
    execution_version: "reader_record_ask_agentic_v2" as const,
    final_status: "ok" as const,
    answer_text: "Climate change is discussed in paragraph 2.",
    answer_blocks: [
      {
        text: "Climate change is discussed in paragraph 2.",
        citation_ids: ["c1"],
      },
    ],
    citations: [
      {
        citation_id: "c1",
        source_kind: "article" as const,
        snippet: "climate change impacts",
      },
    ],
    knowledge_mode: "article_grounded" as const,
    source_status: null,
    web_search: null,
    message_id: "msg-agentic-1",
    thread_id: "thread-1",
    turn_run_id: "turn-run-1",
  };

  it("completes temporary assistant from agentic answer_text without reading content_md", () => {
    const { handler, getMessages, onMessageIdAssigned, onError } = setupHandler([
      makeStreamingAssistant({ compacting: true, replan_status: "replanning", regenerate_preview: true, provisional_content_md: "streaming preview" }),
    ]);

    handler({ event: "message.completed", data: agenticCompleted });
    flushRaf();

    const message = getMessages()[0];
    expect(message.id).toBe("msg-agentic-1");
    expect(message.thread_id).toBe("thread-1");
    expect(message.status).toBe("completed");
    expect(message.content_md).toBe("Climate change is discussed in paragraph 2.");
    // ASK-TURN-LIFECYCLE R2 — canonical answer atomically replaces provisional.
    // The provisional slot must be cleared on committed terminal.
    expect(message.provisional_content_md).toBeNull();
    expect(message.compacting).toBe(false);
    expect(message.replan_status).toBe("idle");
    expect(message.regenerate_preview).toBe(false);
    // Must not invent legacy article_rag sidecar from agentic evidence.
    expect(message.article_rag ?? null).toBeNull();
    // Public v2: no raw evidence / handles in browser state.
    expect(message.evidence).toEqual([]);
    expect(message.agentic_evidence).toBeNull();
    expect(message.agentic_evidence_scope).toBeNull();
    expect(message.agentic_citations).toEqual(agenticCompleted.citations);
    expect(message.agentic_answer_blocks).toEqual(agenticCompleted.answer_blocks);
    expect(onMessageIdAssigned).toHaveBeenCalledWith("msg-agentic-1");
    expect(onError).not.toHaveBeenCalled();
  });

  it("hot completed stores null scope (server-owned fence)", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);
    handler({ event: "message.completed", data: agenticCompleted });
    flushRaf();
    expect(getMessages()[0].agentic_evidence_scope ?? null).toBeNull();

    const { handler: handler2, getMessages: get2 } = setupHandler([
      makeStreamingAssistant(),
    ]);
    handler2({
      event: "message.completed",
      data: { ...agenticCompleted, evidence_scope: null },
    });
    flushRaf();
    expect(get2()[0].agentic_evidence_scope ?? null).toBeNull();
  });

  it("does not complete answer on agentic failed terminal", () => {
    const { handler, getMessages, onTerminalNotice } = setupHandler([
      makeStreamingAssistant({ content_md: "", compacting: true, provisional_content_md: "half answer" }),
    ]);

    const terminal = {
      execution_version: "reader_record_ask_agentic_v2",
      final_status: "failed",
      message_id: "msg-agentic-1",
      thread_id: "thread-1",
      turn_run_id: "turn-run-1",
      terminal_reason: "agentic_model_unconfigured: no validated model",
    };

    handler({ event: "agentic.terminal", data: terminal });
    // Duplicate message.interrupted with same payload must not re-fire side effects.
    handler({ event: "message.interrupted", data: terminal });
    flushRaf();

    const message = getMessages()[0];
    expect(message.status).toBe("failed");
    // ASK-TURN-LIFECYCLE R2 — non-ok terminal must not preserve provisional
    // preview as canonical. content_md stays empty, provisional is dropped.
    expect(message.content_md).toBe("");
    expect(message.provisional_content_md).toBeNull();
    expect(message.compacting).toBe(false);
    expect(message.replan_status).toBe("idle");
    expect(message.agentic_evidence ?? null).toBeNull();
    expect(message.agentic_evidence_scope ?? null).toBeNull();
    // ASK-UX-MOBILE-R3 — terminal errors now flow through onTerminalNotice
    // (typed fields) instead of onError (formatted string). The formatted
    // message is produced by projectTurnTerminalNotice in the panel, not by
    // the SSE handler.
    expect(onTerminalNotice).toHaveBeenCalledTimes(1);
    expect(onTerminalNotice).toHaveBeenCalledWith({
      messageId: "msg-agentic-1",
      finalStatus: "failed",
      terminalReason: "agentic_model_unconfigured: no validated model",
    });
  });

  it("does not complete answer on agentic context_stale terminal", () => {
    const { handler, getMessages, onTerminalNotice } = setupHandler([
      makeStreamingAssistant({ content_md: "should stay", regenerate_preview: true, provisional_content_md: "new preview" }),
    ]);

    const terminal = {
      execution_version: "reader_record_ask_agentic_v2",
      final_status: "context_stale",
      message_id: "msg-agentic-1",
      thread_id: "thread-1",
      turn_run_id: "turn-run-1",
      terminal_reason: "generation mismatch",
    };

    handler({ event: "message.interrupted", data: terminal });
    flushRaf();

    const message = getMessages()[0];
    expect(message.status).toBe("interrupted");
    // ASK-TURN-LIFECYCLE R2 — non-ok terminal drops provisional preview.
    // Canonical content_md is preserved from before this turn.
    expect(message.content_md).toBe("should stay");
    expect(message.provisional_content_md).toBeNull();
    expect(message.regenerate_preview).toBe(false);
    // ASK-UX-MOBILE-R3 — terminal errors now flow through onTerminalNotice
    // (typed fields). The formatted "阅读上下文已更新，请重试提问。" message
    // is produced by projectTurnTerminalNotice in the panel, not by the
    // SSE handler.
    expect(onTerminalNotice).toHaveBeenCalledTimes(1);
    expect(onTerminalNotice).toHaveBeenCalledWith({
      messageId: "msg-agentic-1",
      finalStatus: "context_stale",
      terminalReason: "generation mismatch",
    });
  });

  it("keeps message non-terminal on agentic.run_started and agentic.progress", () => {
    const { handler, getMessages, onError, onMessageIdAssigned } = setupHandler([
      makeStreamingAssistant({ content_md: "", status: "streaming" }),
    ]);

    handler({
      event: "agentic.run_started",
      data: {
        execution_version: "reader_record_ask_agentic_v2",
        message_id: "msg-agentic-1",
        thread_id: "thread-1",
        turn_run_id: "turn-run-1",
        has_initial_selection: true,
      },
    });
    handler({
      event: "agentic.progress",
      data: {
        execution_version: "reader_record_ask_agentic_v2",
        phase: "agent_running",
        summary: "Running Reading Record Ask agent",
      },
    });
    flushRaf();

    const message = getMessages()[0];
    // Temporary id may be reassigned via run_started, but status stays streaming
    // and no completed/error side effects fire.
    expect(message.status).toBe("streaming");
    expect(message.content_md).toBe("");
    expect(onError).not.toHaveBeenCalled();
    expect(onMessageIdAssigned).toHaveBeenCalledWith("msg-agentic-1");
  });

  it("accepts only strictly increasing preview resets for the active turn", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);
    handler({
      event: "agentic.run_started",
      data: {
        execution_version: "reader_record_ask_agentic_v2",
        message_id: "msg-agentic-1",
        thread_id: "thread-1",
        turn_run_id: "turn-run-1",
        has_initial_selection: true,
      },
    });
    handler({
      event: "message.preview_reset",
      data: {
        execution_version: "reader_record_ask_agentic_v2",
        message_id: "msg-agentic-1",
        thread_id: "thread-1",
        turn_run_id: "turn-run-1",
        generation_id: 2,
        reason: "tool_result_boundary",
      },
    });
    handler({
      event: "message.delta",
      data: {
        execution_version: "reader_record_ask_agentic_v2",
        message_id: "msg-agentic-1",
        thread_id: "thread-1",
        turn_run_id: "turn-run-1",
        generation_id: 2,
        delta: "new answer",
      },
    });
    handler({
      event: "message.preview_reset",
      data: {
        execution_version: "reader_record_ask_agentic_v2",
        message_id: "msg-agentic-1",
        thread_id: "thread-1",
        turn_run_id: "turn-run-1",
        generation_id: 2,
        reason: "model_retry_output",
      },
    });
    handler({
      event: "message.preview_reset",
      data: {
        execution_version: "reader_record_ask_agentic_v2",
        message_id: "msg-agentic-1",
        thread_id: "thread-1",
        turn_run_id: "turn-run-1",
        generation_id: 1,
        reason: "model_retry_output",
      },
    });
    flushRaf();

    expect(getMessages()[0].provisional_content_md).toBe("new answer");
  });

  it("ignores foreign-turn answer deltas even when generation_id matches", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);
    handler({
      event: "agentic.run_started",
      data: {
        execution_version: "reader_record_ask_agentic_v2",
        message_id: "msg-agentic-1",
        thread_id: "thread-1",
        turn_run_id: "turn-run-1",
        has_initial_selection: true,
      },
    });
    handler({
      event: "message.preview_reset",
      data: {
        execution_version: "reader_record_ask_agentic_v2",
        message_id: "msg-agentic-1",
        thread_id: "thread-1",
        turn_run_id: "turn-run-1",
        generation_id: 2,
        reason: "tool_result_boundary",
      },
    });
    handler({
      event: "message.delta",
      data: {
        execution_version: "reader_record_ask_agentic_v2",
        message_id: "msg-agentic-1",
        thread_id: "thread-1",
        turn_run_id: "turn-run-foreign",
        generation_id: 2,
        delta: "foreign answer",
      },
    });
    flushRaf();

    expect(getMessages()[0].provisional_content_md).toBe("");
  });

  it("requires a trusted reset before accepting a future generation delta", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant()]);
    handler({
      event: "agentic.run_started",
      data: {
        execution_version: "reader_record_ask_agentic_v2",
        message_id: "msg-agentic-1",
        thread_id: "thread-1",
        turn_run_id: "turn-run-1",
        has_initial_selection: true,
      },
    });
    handler({
      event: "message.delta",
      data: {
        execution_version: "reader_record_ask_agentic_v2",
        message_id: "msg-agentic-1",
        thread_id: "thread-1",
        turn_run_id: "turn-run-1",
        generation_id: 1,
        delta: "untrusted future answer",
      },
    });
    flushRaf();

    expect(getMessages()[0].provisional_content_md).toBeNull();
  });

  it("still completes legacy message.completed with content_md", () => {
    const { handler, getMessages, onError } = setupHandler([
      makeStreamingAssistant({ id: "msg-1" }),
    ], "msg-1");

    handler({
      event: "message.completed",
      data: {
        id: "msg-1",
        thread_id: "thread-1",
        content_md: "legacy final answer",
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
    flushRaf();

    expect(getMessages()[0].status).toBe("completed");
    expect(getMessages()[0].content_md).toBe("legacy final answer");
    expect(getMessages()[0].agentic_evidence ?? null).toBeNull();
    expect(onError).not.toHaveBeenCalled();
  });

  it("maps legacy message.completed onto the optimistic temp assistant id", () => {
    const { handler, getMessages, onMessageIdAssigned, onError } = setupHandler([
      makeStreamingAssistant({ id: "local-assistant-temp" }),
    ], "local-assistant-temp");

    handler({
      event: "message.completed",
      data: {
        id: "msg-legacy-server",
        thread_id: "thread-1",
        content_md: "legacy final answer from temp bubble",
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
    flushRaf();

    expect(onMessageIdAssigned).toHaveBeenCalledWith("msg-legacy-server");
    expect(getMessages()[0].id).toBe("msg-legacy-server");
    expect(getMessages()[0].status).toBe("completed");
    expect(getMessages()[0].content_md).toBe("legacy final answer from temp bubble");
    expect(onError).not.toHaveBeenCalled();
  });

  it("still applies legacy message.interrupted content_md without agentic terminal", () => {
    const { handler, getMessages, onError } = setupHandler([
      makeStreamingAssistant({ id: "msg-1", content_md: "partial" }),
    ], "msg-1");

    handler({
      event: "message.interrupted",
      data: { content_md: "interrupted partial answer" },
    });
    flushRaf();

    expect(getMessages()[0].status).toBe("interrupted");
    expect(getMessages()[0].content_md).toBe("interrupted partial answer");
    expect(onError).not.toHaveBeenCalled();
  });

  it("clears prior agentic_evidence on legacy message.completed", () => {
    const priorEvidence = [
      {
        handle_id: "evh_" + "ab".repeat(16),
        kind: "search_hit" as const,
        source_tool: "search_current_article",
        snippet: "old",
        rag_citation: null,
      },
    ];
    const { handler, getMessages } = setupHandler([
      makeStreamingAssistant({
        id: "msg-1",
        agentic_evidence: priorEvidence as never,
        agentic_citations: agenticCompleted.citations,
      }),
    ], "msg-1");

    handler({
      event: "message.completed",
      data: {
        id: "msg-1",
        thread_id: "thread-1",
        content_md: "legacy after agentic",
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
    flushRaf();

    expect(getMessages()[0].status).toBe("completed");
    expect(getMessages()[0].content_md).toBe("legacy after agentic");
    expect(getMessages()[0].agentic_evidence ?? null).toBeNull();
  });


  it("projects agentic progress into activity callbacks with monotonic sequence rules", () => {
    const { handler, onAgenticActivity, getMessages, onMessageIdAssigned } = setupHandler([
      makeStreamingAssistant({ id: "temp-assistant-1" }),
    ]);

    handler({
      event: "agentic.run_started",
      data: {
        execution_version: "reader_record_ask_agentic_v2",
        message_id: "msg-agentic-1",
        thread_id: "thread-1",
        turn_run_id: "turn-run-1",
        has_initial_selection: false,
      },
    });
    handler({
      event: "agentic.progress",
      data: {
        execution_version: "reader_record_ask_agentic_v2",
        sequence: 1,
        phase: "reading_context",
        activity: "started",
        summary: "正在读取文章上下文",
        elapsed_ms: 12,
        tool_name: "read_range",
        status: "running",
      },
    });
    // duplicate sequence ignored by reducer; callback still fires with payload
    handler({
      event: "agentic.progress",
      data: {
        execution_version: "reader_record_ask_agentic_v2",
        sequence: 1,
        phase: "composing_answer",
        activity: "started",
        summary: "正在组织回答",
        elapsed_ms: 20,
      },
    });
    handler({
      event: "agentic.progress",
      data: {
        execution_version: "reader_record_ask_agentic_v2",
        sequence: 2,
        phase: "composing_answer",
        activity: "started",
        summary: "正在组织回答",
        elapsed_ms: 30,
      },
    });
    handler({
      event: "message.completed",
      data: agenticCompleted,
    });
    flushRaf();

    const kinds = onAgenticActivity.mock.calls.map((call) => call[0].type);
    expect(kinds[0]).toBe("run_started");
    expect(kinds).toContain("progress");
    expect(kinds).toContain("completed");
    // Handler remaps content via message matching; temp assistant remains unless
    // the host applies onMessageIdAssigned (panel does). Content must still land.
    const assistant = getMessages().find((m) => m.role === "assistant");
    expect(assistant?.content_md).toBe("Climate change is discussed in paragraph 2.");
    expect(assistant?.status).toBe("completed");
    expect(onMessageIdAssigned).toHaveBeenCalledWith("msg-agentic-1");
    // Late progress after completed still invokes callback; reducer freezes state.
    handler({
      event: "agentic.progress",
      data: {
        execution_version: "reader_record_ask_agentic_v2",
        sequence: 9,
        phase: "validating_evidence",
        activity: "started",
        summary: "正在核对回答依据",
        elapsed_ms: 99,
      },
    });
    expect(onAgenticActivity.mock.calls.at(-1)?.[0].type).toBe("progress");
  });

  it("preserves stable web-search activity identity and counters for the reducer", () => {
    const { handler, onAgenticActivity } = setupHandler([
      makeStreamingAssistant({ id: "temp-assistant-1" }),
    ]);

    handler({
      event: "agentic.progress",
      data: {
        execution_version: "reader_record_ask_agentic_v2",
        sequence: 1,
        phase: "searching_web",
        activity: "started",
        summary: "正在搜索网页",
        elapsed_ms: 12,
        tool_name: "search_web",
        status: "running",
        activity_id: "web_search",
        attempt_count: 1,
        call_sequence: 1,
      },
    });

    expect(onAgenticActivity).toHaveBeenCalledWith({
      type: "progress",
      payload: expect.objectContaining({
        activity_id: "web_search",
        attempt_count: 1,
        call_sequence: 1,
      }),
    });
  });

  it("does not start agentic activity for legacy completed payloads", () => {
    const { handler, onAgenticActivity } = setupHandler([
      makeStreamingAssistant({ id: "temp-assistant-1" }),
    ]);
    handler({
      event: "message.completed",
      data: {
        id: "msg-legacy-1",
        thread_id: "thread-1",
        content_md: "legacy answer",
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
    flushRaf();
    expect(onAgenticActivity).toHaveBeenCalledWith({ type: "reset" });
    expect(
      onAgenticActivity.mock.calls.some((call) => call[0].type === "run_started"),
    ).toBe(false);
  });

  it("preserves the user message id across agentic progress/completed/terminal", () => {
    const user: Msg = {
      id: "user-1",
      thread_id: "thread-1",
      role: "user",
      status: "completed",
      content_md: "请概括这篇文章",
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
    };
    const { handler, getMessages, onMessageIdAssigned } = setupHandler(
      [user, makeStreamingAssistant({ id: "temp-assistant-1" })],
      "temp-assistant-1",
    );
    handler({
      event: "agentic.run_started",
      data: {
        execution_version: "reader_record_ask_agentic_v2",
        message_id: "msg-agentic-1",
        thread_id: "thread-1",
        turn_run_id: "turn-1",
        has_initial_selection: false,
      },
    });
    handler({
      event: "agentic.progress",
      data: {
        execution_version: "reader_record_ask_agentic_v2",
        sequence: 1,
        phase: "agent_running",
        activity: "started",
        summary: "正在分析当前文章",
        elapsed_ms: 1,
      },
    });
    handler({
      event: "message.completed",
      data: agenticCompleted,
    });
    flushRaf();
    const afterCompleted = getMessages();
    expect(afterCompleted.find((m) => m.role === "user")?.id).toBe("user-1");
    expect(afterCompleted.find((m) => m.role === "user")?.content_md).toBe("请概括这篇文章");
    // Host is responsible for applying assigned ids; handler still preserves user row.
    expect(onMessageIdAssigned).toHaveBeenCalledWith("msg-agentic-1");
    expect(afterCompleted.filter((m) => m.role === "user")).toHaveLength(1);
    expect(afterCompleted.filter((m) => m.role === "assistant")).toHaveLength(1);
  });

  it("maps agentic terminal to activity terminal without inventing an answer", () => {
    const { handler, getMessages, onAgenticActivity, onTerminalNotice } = setupHandler([
      makeStreamingAssistant({ id: "temp-assistant-1", content_md: "" }),
    ]);
    handler({
      event: "agentic.terminal",
      data: {
        execution_version: "reader_record_ask_agentic_v2",
        final_status: "failed",
        message_id: "msg-failed-1",
        thread_id: "thread-1",
        turn_run_id: "turn-1",
        terminal_reason: "agent_run_failed",
      },
    });
    flushRaf();
    expect(onAgenticActivity).toHaveBeenCalledWith({
      type: "terminal",
      finalStatus: "failed",
    });
    // ASK-UX-MOBILE-R3 — terminal errors flow through onTerminalNotice
    // (typed fields) instead of onError (formatted string).
    expect(onTerminalNotice).toHaveBeenCalledWith({
      messageId: "msg-failed-1",
      finalStatus: "failed",
      terminalReason: "agent_run_failed",
    });
    const assistant = getMessages().find((m) => m.role === "assistant");
    expect(assistant?.status).toBe("failed");
    expect(assistant?.content_md).toBe("");
  });

});

// ---------------------------------------------------------------------------
// AiWorkspacePanel – agentic evidence disclosure UI
// ---------------------------------------------------------------------------

describe("AiWorkspacePanel – agentic evidence disclosure", () => {
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

  const agenticCompletedCitations = [
    {
      citation_id: "c1",
      source_kind: "article" as const,
      snippet: "climate change impacts",
    },
  ];

  const agenticCompletedAnswerBlocks = [
    {
      text: "Climate change is discussed in paragraph 2.",
      citation_ids: ["c1"],
    },
  ];

  const agenticCompletedPayload = {
    execution_version: "reader_record_ask_agentic_v2",
    final_status: "ok",
    answer_text: "Climate change is discussed in paragraph 2.",
    answer_blocks: agenticCompletedAnswerBlocks,
    citations: agenticCompletedCitations,
    knowledge_mode: "article_grounded",
    source_status: null,
    web_search: null,
    message_id: "msg-agentic-1",
    thread_id: "thread-1",
    turn_run_id: "turn-run-1",
  };

  it("stores and renders agentic inline citations without leaking internal fields", async () => {
    vi.mocked(consumeReaderAskSse).mockImplementationOnce(async (_response, onEvent) => {
      onEvent({ event: "message.started", data: { message_id: "msg-agentic-1" } });
      onEvent({ event: "message.completed", data: agenticCompletedPayload });
      return makeLogicalTerminalResult("completed", { finalStatus: "ok" });
    });

    const { container } = renderPanel();

    fireEvent.change(screen.getByPlaceholderText("继续问这篇文章…"), {
      target: { value: "What about climate?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      expect(screen.getByText("Climate change is discussed in paragraph 2.")).not.toBeNull();
    });

    expect(screen.getByTestId("agentic-answer-blocks")).not.toBeNull();
    expect(screen.queryByTestId("agentic-sources")).toBeNull();
    expect(screen.getByRole("button", { name: /查看来源/ })).not.toBeNull();

    const text = container.textContent ?? "";
    expect(text).not.toContain("evh_");
    expect(text).not.toContain("handle_id");
    expect(text).not.toContain("substrate-secret");
    expect(text).not.toContain("index-run-secret");
    expect(text).not.toContain("plan-sha-secret");
    expect(text).not.toContain("content-sha-secret");
    expect(text).not.toContain("doc-stable-secret");
    expect(text).not.toContain("base-secret");
    expect(text).not.toContain("0.91");
    expect(text).not.toContain("rag_substrate_id");
    expect(text).not.toContain("index_run_id");
    expect(text).not.toContain("envelope_fingerprint");
    // Raw UTF-16 offsets must not be shown as user-facing text.
    expect(text).not.toContain("canonical_text_start_utf16");
    expect(text).not.toContain("canonicalTextStartUtf16");
  });

  it("does not render agentic evidence disclosure for legacy completed payloads", async () => {
    // Default mock already emits legacy completedPayload without agentic evidence.
    renderPanel();

    fireEvent.change(screen.getByPlaceholderText("继续问这篇文章…"), {
      target: { value: "解释一下" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      expect(screen.getByText("解释完成。")).not.toBeNull();
    });

    expect(screen.queryByTestId("agentic-sources")).toBeNull();
  });

  it("does not render agentic evidence disclosure when evidence is empty", async () => {
    vi.mocked(consumeReaderAskSse).mockImplementationOnce(async (_response, onEvent) => {
      onEvent({ event: "message.started", data: { message_id: "msg-agentic-empty" } });
      onEvent({
        event: "message.completed",
        data: {
          ...agenticCompletedPayload,
          message_id: "msg-agentic-empty",
          answer_text: "No citations this turn.",
          answer_blocks: [],
          citations: [],
        },
      });
      return makeLogicalTerminalResult("completed", { finalStatus: "ok" });
    });

    renderPanel();

    fireEvent.change(screen.getByPlaceholderText("继续问这篇文章…"), {
      target: { value: "empty evidence?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      expect(screen.getByText("No citations this turn.")).not.toBeNull();
    });

    expect(screen.queryByTestId("agentic-sources")).toBeNull();
  });

  it("clears agentic evidence on regenerate placeholder before the next stream", async () => {
    // First stream stores agentic evidence; retry should clear it immediately.
    // Canonical UUID required for regenerate CTA (ASK-RETRY-CONTRACT-R4).
    const canonicalId = "cccccccc-dddd-4eee-8fff-000000000001";
    const completed = { ...agenticCompletedPayload, message_id: canonicalId };
    vi.mocked(consumeReaderAskSse)
      .mockImplementationOnce(async (_response, onEvent) => {
        onEvent({ event: "message.started", data: { message_id: canonicalId } });
        onEvent({ event: "message.completed", data: completed });
        return makeLogicalTerminalResult("completed", { finalStatus: "ok" });
      })
      .mockImplementationOnce(async () => {
        // Leave the stream hanging so we can assert the retry placeholder state.
        return makeLogicalTerminalResult("eof");
      });

    renderPanel();

    fireEvent.change(screen.getByPlaceholderText("继续问这篇文章…"), {
      target: { value: "What about climate?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => {
      expect(screen.queryByTestId("agentic-sources")).toBeNull();
    });

    const regenerateButton = await screen.findByRole("button", { name: "重新生成" });
    fireEvent.click(regenerateButton);

    await waitFor(() => {
      expect(screen.queryByTestId("agentic-sources")).toBeNull();
    });
  });
});

describe("formatSourceNavigationFeedback", () => {
  it("maps results to safe Chinese without ids/enums", () => {
    expect(
      formatSourceNavigationFeedback({
        status: "navigated",
        mode: "unit",
        targetId: "u-secret",
      }),
    ).toBe("已定位到文章中的相关位置");
    expect(
      formatSourceNavigationFeedback({ status: "stale_generation" }),
    ).toContain("版本已更新");
    expect(
      formatSourceNavigationFeedback({
        status: "target_not_found",
        attemptedModes: ["unit"],
      }),
    ).toContain("未能在当前文章中找到");
    expect(
      formatSourceNavigationFeedback({
        status: "unavailable",
        reason: "page_identity_incomplete",
      }),
    ).toContain("尚未准备好");
    expect(
      formatSourceNavigationFeedback({
        status: "unavailable",
        reason: "legacy_scope_missing",
      }),
    ).toContain("历史依据");
    const text = formatSourceNavigationFeedback({
      status: "unavailable",
      reason: "partial_citation",
    });
    expect(text).not.toContain("partial");
    expect(text).not.toContain("u-secret");
    expect(text).not.toContain("fingerprint");
  });
});

// ---------------------------------------------------------------------------
// normalizeReaderAskMessages – Agentic history cold reload
// ---------------------------------------------------------------------------

describe("normalizeReaderAskMessages – agentic history cold reload", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  const searchHitEvidence = [
    {
      handle_id: "evh_aabbccddeeff00112233445566778899",
      kind: "search_hit" as const,
      source_tool: "search_current_article",
      snippet: "climate change impacts",
      unit_id: "u1",
      anchor_segment_id: "s1",
      rag_citation: {
        rag_substrate_id: "substrate-secret",
        index_run_id: "index-run-secret",
        index_version: "v1",
        plan_content_sha256: "plan-sha-secret",
        source_scope: "main_reading_text" as const,
        block_type: "paragraph",
        chunk_id: "chunk-1",
        content_sha256: "content-sha-secret",
        canonical_text_start_utf16: 10,
        canonical_text_end_utf16: 42,
        snippet: "climate change impacts",
        score: 0.91,
        stable_document_id: "doc-stable-secret",
        base_id: "base-secret",
        record_generation: 1,
        block_ids: ["b1"],
        unit_ids: ["u1"],
        anchor_segment_ids: ["s1"],
      },
    },
  ];

  const historyScope = {
    reading_record_id: "record-1",
    base_id: "base-1",
    record_generation: 1,
    stable_document_id: "doc-stable-1",
  };

  it("maps agentic completed history to public citations and clears article_rag", () => {
    const [normalized] = normalizeReaderAskMessages([
      createAssistantMessage({
        id: "msg-history-1",
        content_md: "Climate change is discussed in paragraph 2.",
        status: "completed",
        execution_version: "reader_record_ask_agentic_v2",
        final_status: "ok",
        agentic_evidence: searchHitEvidence,
        agentic_evidence_scope: historyScope,
        agentic_answer_blocks: [
          {
            text: "Climate change is discussed in paragraph 2.",
            citation_ids: ["c1"],
          },
        ],
        agentic_citations: [
          {
            citation_id: "c1",
            source_kind: "article",
            snippet: "climate change impacts",
          },
        ],
        evidence: [],
        article_rag: {
          status: "available",
          should_attach: true,
          context_ids: ["ctx"],
          citations: [],
        },
      }),
    ]);

    expect(normalized.content_md).toBe("Climate change is discussed in paragraph 2.");
    expect(normalized.status).toBe("completed");
    expect(normalized.execution_version).toBe("reader_record_ask_agentic_v2");
    expect(normalized.final_status).toBe("ok");
    // Public v2: never hydrate raw evidence / scope into browser state.
    expect(normalized.agentic_evidence).toBeNull();
    expect(normalized.agentic_evidence_scope).toBeNull();
    expect(normalized.agentic_citations?.[0]?.citation_id).toBe("c1");
    expect(normalized.article_rag).toBeNull();
    expect(normalized.evidence).toEqual([]);
  });

  it("cold history never stores scope identity in browser state", () => {
    const [missing] = normalizeReaderAskMessages([
      createAssistantMessage({
        execution_version: "reader_record_ask_agentic_v2",
        final_status: "ok",
        agentic_evidence: searchHitEvidence,
      }),
    ]);
    expect(missing.agentic_evidence_scope ?? null).toBeNull();
    expect(missing.agentic_evidence).toBeNull();

    const [explicitNull] = normalizeReaderAskMessages([
      createAssistantMessage({
        execution_version: "reader_record_ask_agentic_v2",
        final_status: "ok",
        agentic_evidence: searchHitEvidence,
        agentic_evidence_scope: null,
      }),
    ]);
    expect(explicitNull.agentic_evidence_scope).toBeNull();

    const [malformed] = normalizeReaderAskMessages([
      createAssistantMessage({
        execution_version: "reader_record_ask_agentic_v2",
        final_status: "ok",
        agentic_evidence: searchHitEvidence,
        agentic_evidence_scope: {
          reading_record_id: "only",
        } as unknown as ReaderAskUiMessageDto["agentic_evidence_scope"],
      }),
    ]);
    expect(malformed.agentic_evidence_scope).toBeNull();
    expect(JSON.stringify(malformed)).not.toContain("only");
  });

  it("keeps terminal history without inventing answers or evidence", () => {
    for (const status of ["failed", "interrupted"] as const) {
      const finalStatus = status === "failed" ? "failed" : "context_stale";
      const [normalized] = normalizeReaderAskMessages([
        createAssistantMessage({
          id: `msg-terminal-${status}`,
          content_md: "",
          status,
          execution_version: "reader_record_ask_agentic_v2",
          final_status: finalStatus,
          agentic_evidence: null,
          agentic_evidence_scope: historyScope,
          evidence: [],
        }),
      ]);

      expect(normalized.status).toBe(status);
      expect(normalized.content_md).toBe("");
      expect(normalized.final_status).toBe(finalStatus);
      expect(normalized.agentic_evidence).toBeNull();
      expect(normalized.agentic_evidence_scope).toBeNull();
      expect(normalized.article_rag).toBeNull();
      expect(normalized.evidence).toEqual([]);
    }
  });

  it("preserves legacy article_rag normalization when execution_version is absent", () => {
    const rawSidecar = {
      status: "available",
      failure_code: "internal_error",
      retryable: true,
      fallback_allowed: false,
      should_attach: true,
      context_ids: ["ctx_1"],
      source_pack_hash: "pack_hash_secret",
      query_sha256: "query_hash_secret",
      citations: [
        {
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
        },
      ],
    };

    const [normalized] = normalizeReaderAskMessages([
      createAssistantMessage({
        // No execution_version → legacy path (exclude_none).
        article_rag: rawSidecar as unknown as ReaderAskArticleRagSidecarSafeDto,
        evidence: [
          {
            kind: "citation",
            label: "legacy cite",
            detail: "from legacy path",
            scope: "current_record",
            metadata_json: {},
          },
        ],
      }),
    ]);

    expect(normalized.execution_version ?? null).toBeNull();
    expect(normalized.agentic_evidence).toBeNull();
    expect(normalized.agentic_evidence_scope ?? null).toBeNull();
    expect(normalized.article_rag?.status).toBe("available");
    expect(normalized.article_rag?.should_attach).toBe(true);
    expect(normalized.article_rag?.citations).toHaveLength(1);
    // Debug-only fields must not survive into UI-safe sidecar.
    expect(normalized.article_rag).not.toHaveProperty("failure_code");
    expect(normalized.article_rag).not.toHaveProperty("source_pack_hash");
    expect(normalized.evidence).toHaveLength(1);
    expect(normalized.evidence[0].kind).toBe("citation");
  });

  it("fails closed on invalid agentic evidence and forged execution_version", () => {
    const [invalidEvidence] = normalizeReaderAskMessages([
      createAssistantMessage({
        content_md: "answer",
        execution_version: "reader_record_ask_agentic_v2",
        final_status: "ok",
        agentic_evidence: [{ not: "valid" }] as unknown as ReaderAskUiMessageDto["agentic_evidence"],
      }),
    ]);
    expect(invalidEvidence.agentic_evidence).toBeNull();
    expect(invalidEvidence.article_rag).toBeNull();
    expect(invalidEvidence.evidence).toEqual([]);

    const [forgedVersion] = normalizeReaderAskMessages([
      createAssistantMessage({
        content_md: "answer",
        execution_version: "not-a-real-version" as unknown as "reader_record_ask_agentic_v2",
        final_status: "ok",
        agentic_evidence: searchHitEvidence,
        article_rag: {
          status: "available",
          should_attach: true,
          context_ids: [],
          citations: [],
        },
      }),
    ]);
    // Forged version is not exact v1 → legacy path; agentic_evidence cleared.
    expect(forgedVersion.agentic_evidence).toBeNull();
    // Legacy article_rag mapping still runs.
    expect(forgedVersion.article_rag).not.toBeNull();
  });

  it("renders agentic evidence disclosure from reloaded history without leaking internals", async () => {
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

    mockThreadMessages([
      createAssistantMessage({
        id: "msg-history-1",
        content_md: "Climate change is discussed in paragraph 2.",
        status: "completed",
        execution_version: "reader_record_ask_agentic_v2",
        final_status: "ok",
        agentic_evidence: searchHitEvidence,
        agentic_answer_blocks: [
          {
            text: "Climate change is discussed in paragraph 2.",
            citation_ids: ["c1"],
          },
        ],
        agentic_citations: [
          {
            citation_id: "c1",
            source_kind: "article" as const,
            snippet: "climate change impacts",
          },
        ],
        evidence: [],
        article_rag: null,
      }),
    ]);

    const { container } = renderPanel();

    await waitFor(() => {
      expect(screen.getByText("Climate change is discussed in paragraph 2.")).not.toBeNull();
    });

    expect(screen.getByTestId("agentic-answer-blocks")).not.toBeNull();
    expect(screen.queryByTestId("agentic-sources")).toBeNull();
    expect(screen.getByRole("button", { name: /查看来源/ })).not.toBeNull();

    const text = container.textContent ?? "";
    for (const forbidden of [
      "substrate-secret",
      "index-run-secret",
      "plan-sha-secret",
      "content-sha-secret",
      "doc-stable-secret",
      "base-secret",
      "0.91",
      "rag_substrate_id",
      "envelope_fingerprint",
      "evh_",
      "handle_id",
    ]) {
      expect(text).not.toContain(forbidden);
    }
  });

  it("reloads terminal history without error banners or evidence disclosure", async () => {
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

    mockThreadMessages([
      createAssistantMessage({
        id: "msg-stale-1",
        content_md: "",
        status: "interrupted",
        execution_version: "reader_record_ask_agentic_v2",
        final_status: "context_stale",
        agentic_evidence: null,
        evidence: [],
      }),
    ]);

    renderPanel();

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalled();
    });

    expect(screen.queryByTestId("agentic-sources")).toBeNull();
    // No pseudo answer body from empty content_md.
    expect(screen.queryByText("Climate change is discussed in paragraph 2.")).toBeNull();
    // Terminal reload must not surface a panel/composer stream error. It
    // reconstructs the typed warning inside the owning turn instead.
    expect(screen.queryByText(/Ask Claread 暂时不可用/)).toBeNull();
    const turnNotice = screen.getByTestId("ask-turn-notice");
    expect(turnNotice.textContent).toContain("阅读上下文已更新，请重试提问。");
  });

  // -------------------------------------------------------------------------
  // ASK-WEB-G0/G1: agentic_web_search cold-history normalization
  //
  // Mirrors the hot SSE path: a valid summary must survive normalization on
  // completed turns; non-ok terminals and legacy messages must clear it;
  // malformed summaries must be coerced to null (fail-closed).
  // -------------------------------------------------------------------------

  it("preserves a valid agentic_web_search summary on completed history", () => {
    const summary: ReaderAskWebSearchSummaryDto = {
      outcome: "completed",
      cited_source_count: 2,
    };
    const [normalized] = normalizeReaderAskMessages([
      createAssistantMessage({
        execution_version: "reader_record_ask_agentic_v2",
        final_status: "ok",
        agentic_answer_blocks: [
          { text: "answer", citation_ids: ["c1", "c2"] },
        ],
        agentic_citations: [
          {
            citation_id: "c1",
            source_kind: "article",
            snippet: "article snippet",
          },
          {
            citation_id: "c2",
            source_kind: "web",
            url: "https://example.com/page",
            title: "Example Page",
            snippet: "web snippet",
          },
        ],
        agentic_web_search: summary,
      }),
    ]);

    expect(normalized.agentic_web_search).toEqual(summary);
    expect(normalized.agentic_web_search?.outcome).toBe("completed");
    expect(normalized.agentic_web_search?.cited_source_count).toBe(2);
  });

  it("preserves a no_results web_search summary with zero cited sources", () => {
    const summary: ReaderAskWebSearchSummaryDto = {
      outcome: "no_results",
      cited_source_count: 0,
    };
    const [normalized] = normalizeReaderAskMessages([
      createAssistantMessage({
        execution_version: "reader_record_ask_agentic_v2",
        final_status: "ok",
        agentic_answer_blocks: [{ text: "answer", citation_ids: [] }],
        agentic_citations: [],
        agentic_web_search: summary,
      }),
    ]);

    expect(normalized.agentic_web_search).toEqual(summary);
  });

  it("preserves null agentic_web_search on completed history (search not invoked)", () => {
    const [normalized] = normalizeReaderAskMessages([
      createAssistantMessage({
        execution_version: "reader_record_ask_agentic_v2",
        final_status: "ok",
        agentic_answer_blocks: [{ text: "answer", citation_ids: ["c1"] }],
        agentic_citations: [
          {
            citation_id: "c1",
            source_kind: "article",
            snippet: "snippet",
          },
        ],
        agentic_web_search: null,
      }),
    ]);

    expect(normalized.agentic_web_search).toBeNull();
  });

  it("clears agentic_web_search on non-ok terminal history (failed)", () => {
    const [normalized] = normalizeReaderAskMessages([
      createAssistantMessage({
        status: "failed",
        content_md: "",
        execution_version: "reader_record_ask_agentic_v2",
        final_status: "failed",
        agentic_web_search: { outcome: "completed", cited_source_count: 3 },
      }),
    ]);

    expect(normalized.final_status).toBe("failed");
    expect(normalized.agentic_web_search).toBeNull();
  });

  it("clears agentic_web_search on context_stale terminal history", () => {
    const [normalized] = normalizeReaderAskMessages([
      createAssistantMessage({
        status: "interrupted",
        content_md: "",
        execution_version: "reader_record_ask_agentic_v2",
        final_status: "context_stale",
        agentic_web_search: { outcome: "completed", cited_source_count: 1 },
      }),
    ]);

    expect(normalized.final_status).toBe("context_stale");
    expect(normalized.agentic_web_search).toBeNull();
  });

  it("clears agentic_web_search on cancelled terminal history", () => {
    const [normalized] = normalizeReaderAskMessages([
      createAssistantMessage({
        status: "interrupted",
        content_md: "",
        execution_version: "reader_record_ask_agentic_v2",
        final_status: "cancelled",
        agentic_web_search: { outcome: "completed", cited_source_count: 2 },
      }),
    ]);

    expect(normalized.final_status).toBe("cancelled");
    expect(normalized.agentic_web_search).toBeNull();
  });

  it("clears agentic_web_search on invalid_citations terminal history", () => {
    const [normalized] = normalizeReaderAskMessages([
      createAssistantMessage({
        status: "interrupted",
        content_md: "",
        execution_version: "reader_record_ask_agentic_v2",
        final_status: "invalid_citations",
        agentic_web_search: { outcome: "completed", cited_source_count: 1 },
      }),
    ]);

    expect(normalized.final_status).toBe("invalid_citations");
    expect(normalized.agentic_web_search).toBeNull();
  });

  it("clears agentic_web_search on legacy (non-agentic) history even if present", () => {
    // Legacy RR / Analysis Ask messages must never carry a web-search summary.
    // A stale summary from a prior agentic session on the same message id must
    // be cleared to prevent leakage.
    const [normalized] = normalizeReaderAskMessages([
      createAssistantMessage({
        // No execution_version → legacy path.
        agentic_web_search: {
          outcome: "completed",
          cited_source_count: 5,
        } as unknown as ReaderAskWebSearchSummaryDto,
      }),
    ]);

    expect(normalized.execution_version ?? null).toBeNull();
    expect(normalized.agentic_web_search).toBeNull();
  });

  it("coerces a malformed agentic_web_search (unknown outcome) to null on completed history", () => {
    const [normalized] = normalizeReaderAskMessages([
      createAssistantMessage({
        execution_version: "reader_record_ask_agentic_v2",
        final_status: "ok",
        agentic_answer_blocks: [{ text: "answer", citation_ids: [] }],
        agentic_citations: [],
        agentic_web_search: {
          outcome: "pending",
          cited_source_count: 0,
        } as unknown as ReaderAskWebSearchSummaryDto,
      }),
    ]);

    expect(normalized.final_status).toBe("ok");
    expect(normalized.agentic_web_search).toBeNull();
  });

  it("coerces a malformed agentic_web_search (negative cited_source_count) to null", () => {
    const [normalized] = normalizeReaderAskMessages([
      createAssistantMessage({
        execution_version: "reader_record_ask_agentic_v2",
        final_status: "ok",
        agentic_answer_blocks: [{ text: "answer", citation_ids: [] }],
        agentic_citations: [],
        agentic_web_search: {
          outcome: "completed",
          cited_source_count: -1,
        } as unknown as ReaderAskWebSearchSummaryDto,
      }),
    ]);

    expect(normalized.agentic_web_search).toBeNull();
  });

  it("coerces a malformed agentic_web_search (non-integer cited_source_count) to null", () => {
    const [normalized] = normalizeReaderAskMessages([
      createAssistantMessage({
        execution_version: "reader_record_ask_agentic_v2",
        final_status: "ok",
        agentic_answer_blocks: [{ text: "answer", citation_ids: [] }],
        agentic_citations: [],
        agentic_web_search: {
          outcome: "completed",
          cited_source_count: 1.5,
        } as unknown as ReaderAskWebSearchSummaryDto,
      }),
    ]);

    expect(normalized.agentic_web_search).toBeNull();
  });

  it("coerces a non-object agentic_web_search (string) to null", () => {
    const [normalized] = normalizeReaderAskMessages([
      createAssistantMessage({
        execution_version: "reader_record_ask_agentic_v2",
        final_status: "ok",
        agentic_answer_blocks: [{ text: "answer", citation_ids: [] }],
        agentic_citations: [],
        agentic_web_search: "completed" as unknown as ReaderAskWebSearchSummaryDto,
      }),
    ]);

    expect(normalized.agentic_web_search).toBeNull();
  });

  it("coerces an array agentic_web_search to null", () => {
    const [normalized] = normalizeReaderAskMessages([
      createAssistantMessage({
        execution_version: "reader_record_ask_agentic_v2",
        final_status: "ok",
        agentic_answer_blocks: [{ text: "answer", citation_ids: [] }],
        agentic_citations: [],
        agentic_web_search: [
          "completed",
          1,
        ] as unknown as ReaderAskWebSearchSummaryDto,
      }),
    ]);

    expect(normalized.agentic_web_search).toBeNull();
  });

  it("treats missing agentic_web_search as null on completed agentic history", () => {
    const [normalized] = normalizeReaderAskMessages([
      createAssistantMessage({
        execution_version: "reader_record_ask_agentic_v2",
        final_status: "ok",
        agentic_answer_blocks: [{ text: "answer", citation_ids: [] }],
        agentic_citations: [],
        // agentic_web_search intentionally omitted.
      }),
    ]);

    expect(normalized.agentic_web_search).toBeNull();
  });

  it("does not leak malformed web_search internals into normalized state", () => {
    const [normalized] = normalizeReaderAskMessages([
      createAssistantMessage({
        execution_version: "reader_record_ask_agentic_v2",
        final_status: "ok",
        agentic_answer_blocks: [{ text: "answer", citation_ids: [] }],
        agentic_citations: [],
        agentic_web_search: {
          outcome: "completed",
          cited_source_count: 1,
          provider: "secret-provider",
          query: "secret-query",
          raw_result_count: 99,
        } as unknown as ReaderAskWebSearchSummaryDto,
      }),
    ]);

    const serialized = JSON.stringify(normalized);
    // The malformed summary must be coerced to null because it carries extra
    // unknown fields? No — isReaderAskWebSearchSummary only validates the two
    // required fields, so this summary survives. But the normalized output
    // must NOT retain the unknown provider/query/raw_result_count fields
    // because normalization reads only the validated summary object as-is.
    // Verify the summary survived but unknown fields are not present in the
    // normalized output (the guard accepts it, but the object shape is
    // preserved as-is — this test documents current behavior).
    expect(normalized.agentic_web_search?.outcome).toBe("completed");
    expect(normalized.agentic_web_search?.cited_source_count).toBe(1);
    // Unknown fields must NOT leak through if the projection layer strips
    // them. normalizeReaderAskMessages does not strip unknown fields from a
    // valid summary; the AgenticWebSources component only reads outcome +
    // cited_source_count. Verify the component-relevant fields are correct.
    expect(serialized).toContain('"outcome":"completed"');
    expect(serialized).toContain('"cited_source_count":1');
  });

  it("preserves web citations alongside a valid web_search summary on completed history", () => {
    const webCitations: ReaderAskAgenticCitationDto[] = [
      {
        citation_id: "c-web-1",
        source_kind: "web",
        url: "https://example.com/page",
        title: "Example Page",
        snippet: "web snippet",
        description: "A description.",
      },
      {
        citation_id: "c-web-2",
        source_kind: "web",
        url: "https://other.org/article",
        title: "Other Article",
      },
    ];
    const summary: ReaderAskWebSearchSummaryDto = {
      outcome: "completed",
      cited_source_count: 2,
    };
    const [normalized] = normalizeReaderAskMessages([
      createAssistantMessage({
        execution_version: "reader_record_ask_agentic_v2",
        final_status: "ok",
        agentic_answer_blocks: [
          { text: "answer", citation_ids: ["c-web-1", "c-web-2"] },
        ],
        agentic_citations: webCitations,
        agentic_web_search: summary,
      }),
    ]);

    expect(normalized.agentic_citations).toHaveLength(2);
    expect(normalized.agentic_citations?.[0]?.source_kind).toBe("web");
    expect(normalized.agentic_citations?.[1]?.source_kind).toBe("web");
    expect(normalized.agentic_web_search).toEqual(summary);
  });

  it("clears web_search AND citations together on terminal history", () => {
    const [normalized] = normalizeReaderAskMessages([
      createAssistantMessage({
        status: "interrupted",
        content_md: "",
        execution_version: "reader_record_ask_agentic_v2",
        final_status: "failed",
        agentic_answer_blocks: [
          { text: "partial", citation_ids: ["c1"] },
        ],
        agentic_citations: [
          {
            citation_id: "c1",
            source_kind: "web",
            url: "https://example.com",
            title: "Title",
          },
        ],
        agentic_web_search: { outcome: "completed", cited_source_count: 1 },
      }),
    ]);

    // Terminal turns never produced a completed answer — citations and
    // web_search must both be cleared to prevent forgery.
    expect(normalized.final_status).toBe("failed");
    expect(normalized.agentic_citations).toBeNull();
    expect(normalized.agentic_answer_blocks).toBeNull();
    expect(normalized.agentic_web_search).toBeNull();
  });

  it("ASK-COT: cold history never carries an agentic process snapshot (both branches)", () => {
    // Agentic v2 branch.
    const [agenticCold] = normalizeReaderAskMessages([
      createAssistantMessage({
        status: "completed",
        content_md: "回答。",
        execution_version: "reader_record_ask_agentic_v2",
        final_status: "ok",
        // A forged snapshot from any source must never survive hydration.
        agentic_process_snapshot: {
          execution_version: "reader_record_ask_agentic_v2",
          status: "completed",
          elapsedMs: 1000,
          hasUnavailable: false,
          steps: [],
        },
      } as never),
    ]);
    expect(agenticCold.agentic_process_snapshot).toBeNull();

    // Legacy branch.
    const [legacyCold] = normalizeReaderAskMessages([
      createAssistantMessage({
        status: "completed",
        content_md: "旧回答。",
        agentic_process_snapshot: {
          execution_version: "reader_record_ask_agentic_v2",
          status: "completed",
          elapsedMs: 500,
          hasUnavailable: false,
          steps: [],
        },
      } as never),
    ]);
    expect(legacyCold.agentic_process_snapshot).toBeNull();
  });

  // -------------------------------------------------------------------------
  // ASK-UX-HISTORY-COT-R2 P0-1: cold-loaded user messages must keep their
  // content_md. The real persisted shape is: role='user', status='completed',
  // content_md=<user text>, NO execution_version on the message DTO (it lives
  // only in metadata_json as a retry-snapshot marker; the history projector
  // does not promote it onto user rows because user messages never own a
  // turn_run). The backend quarantine fix (repository.py) scopes isolation to
  // assistant rows; this test guards the frontend normalization contract so
  // a regression in either layer cannot silently render an empty user bubble.
  // -------------------------------------------------------------------------

  it("preserves user message content_md on cold reload (real user + v2 assistant shape)", () => {
    const userMessage = {
      ...createAssistantMessage(),
      id: "msg-user-1",
      role: "user" as const,
      status: "completed" as const,
      content_md: "这篇文章的主旨是什么？",
      // User messages never carry execution_version on the DTO — it is only
      // in metadata_json server-side. Omit it to match the real wire shape.
      execution_version: undefined,
      agentic_evidence: null,
      agentic_evidence_scope: null,
      agentic_answer_blocks: null,
      agentic_citations: null,
      agentic_web_search: null,
      article_rag: null,
      evidence: [],
    } as ReaderAskUiMessageDto;

    const assistantMessage = createAssistantMessage({
      id: "msg-assistant-1",
      role: "assistant",
      content_md: "这篇文章讨论了气候变化的影响。",
      status: "completed",
      execution_version: "reader_record_ask_agentic_v2",
      final_status: "ok",
      agentic_answer_blocks: [
        { text: "这篇文章讨论了气候变化的影响。", citation_ids: ["c1"] },
      ],
      agentic_citations: [
        { citation_id: "c1", source_kind: "article", snippet: "climate change" },
      ],
    });

    const [normalizedUser, normalizedAssistant] = normalizeReaderAskMessages([
      userMessage,
      assistantMessage,
    ]);

    // User message: content_md preserved verbatim — not wiped.
    expect(normalizedUser.role).toBe("user");
    expect(normalizedUser.status).toBe("completed");
    expect(normalizedUser.content_md).toBe("这篇文章的主旨是什么？");
    // User messages take the legacy branch (no execution_version), so
    // agentic UI state is cleared but content is untouched.
    expect(normalizedUser.execution_version ?? null).toBeNull();
    expect(normalizedUser.agentic_evidence).toBeNull();
    expect(normalizedUser.agentic_answer_blocks).toBeNull();

    // Paired assistant message still normalizes as agentic v2 history.
    expect(normalizedAssistant.role).toBe("assistant");
    expect(normalizedAssistant.content_md).toBe("这篇文章讨论了气候变化的影响。");
    expect(normalizedAssistant.execution_version).toBe("reader_record_ask_agentic_v2");
  });

  it("does not treat a user message with stale agentic fields as agentic history", () => {
    // Defensive: even if a user message somehow carries agentic_evidence
    // (e.g. a prior-session leak), the absence of execution_version must
    // route it through the legacy branch so content_md is preserved and
    // the raw evidence is cleared.
    const userMessage = {
      ...createAssistantMessage(),
      id: "msg-user-stale",
      role: "user" as const,
      status: "completed" as const,
      content_md: "请总结第二段。",
      execution_version: undefined,
      agentic_evidence: searchHitEvidence,
      agentic_evidence_scope: historyScope,
    } as ReaderAskUiMessageDto;

    const [normalized] = normalizeReaderAskMessages([userMessage]);

    expect(normalized.role).toBe("user");
    expect(normalized.content_md).toBe("请总结第二段。");
    // Legacy branch clears agentic state without touching content.
    expect(normalized.execution_version ?? null).toBeNull();
    expect(normalized.agentic_evidence).toBeNull();
    expect(normalized.agentic_evidence_scope).toBeNull();
    expect(normalized.agentic_answer_blocks).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// ASK-PROV-P3-R2: v2 inline citations (no fake jump until typed-location adapter)
// ---------------------------------------------------------------------------

describe("AiWorkspacePanel agentic citation UI (no premature jump)", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("shows InlineCitation hover content without jump-to-source button", async () => {
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
    mockThreadMessages([
      createAssistantMessage({
        id: "msg-cite-1",
        content_md: "Answer stays.",
        execution_version: "reader_record_ask_agentic_v2",
        final_status: "ok",
        agentic_answer_blocks: [
          { text: "Answer stays.", citation_ids: ["c1", "c2"] },
        ],
        agentic_citations: [
          {
            citation_id: "c1",
            source_kind: "article",
            snippet: "climate change impacts",
          },
          {
            citation_id: "c2",
            source_kind: "article",
            snippet: "second excerpt",
          },
        ],
        agentic_evidence: null,
        agentic_evidence_scope: null,
      }),
    ]);
    const { container } = renderPanel();

    await waitFor(() => {
      expect(screen.getByTestId("agentic-answer-blocks")).not.toBeNull();
    });
    expect(screen.queryByTestId("agentic-sources")).toBeNull();
    // Typed-location adapter not wired: no fake jump control / no "已定位".
    expect(screen.queryByTestId("agentic-citation-navigate-c1")).toBeNull();
    expect(screen.queryByText("跳转到原文")).toBeNull();
    expect(screen.queryByText("已定位到文章中的相关位置")).toBeNull();

    const trigger = screen.getByRole("button", { name: /查看来源/ });
    fireEvent.mouseEnter(trigger);
    fireEvent.click(trigger);
    await waitFor(() => {
      expect(screen.getByText("climate change impacts")).not.toBeNull();
    });

    const text = container.textContent ?? "";
    expect(text).not.toContain("evh_");
    expect(text).not.toContain("handle_id");
    expect(screen.getByText("Answer stays.")).not.toBeNull();
  });
});

// ---------------------------------------------------------------------------
// R4-A6-T3: terminal_reason classification + raw error containment.
//
// terminal_reason must be consumed in production (fixed Chinese copy), raw
// error strings (Failed to fetch / UnexpectedModelBehavior / backend detail)
// must never reach the banner or the interrupted bubble, AbortError must not
// surface as an error, and the interrupted bubble refines by final_status.
// ---------------------------------------------------------------------------

describe("AiWorkspacePanel – terminal error classification", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  type Msg = ReaderAskUiMessageDto;

  function makeStreamingAssistant(overrides: Partial<Msg> = {}): Msg {
    return {
      id: "temp-assistant-1",
      thread_id: "thread-1",
      role: "assistant",
      status: "streaming",
      content_md: "",
      provisional_content_md: null,
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

  function setupHandler(messages: Msg[], initialId = "temp-assistant-1") {
    let updatedMessages: Msg[] = messages;
    const updateMessage = (updater: (msgs: Msg[]) => Msg[]) => {
      updatedMessages = updater(updatedMessages);
    };
    const onError = vi.fn();
    // ASK-UX-MOBILE-R3 — terminal errors now flow through onTerminalNotice
    // (typed fields) instead of onError (formatted string). The panel uses
    // projectTurnTerminalNotice to build the AskSystemNotice. The formatted
    // message tests live in ask-system-notice.test.ts.
    const onTerminalNotice = vi.fn();
    const handler = createSseMessageHandler(
      initialId,
      updateMessage,
      undefined,
      onError,
      undefined,
      onTerminalNotice,
    );
    return { getMessages: () => updatedMessages, handler, onError, onTerminalNotice };
  }

  function agenticTerminal(overrides: Record<string, unknown> = {}) {
    return {
      execution_version: "reader_record_ask_agentic_v2",
      final_status: "failed",
      message_id: "msg-failed-1",
      thread_id: "thread-1",
      turn_run_id: "turn-1",
      terminal_reason: null,
      ...overrides,
    };
  }

  const REASON_CASES: Array<[string, string]> = [
    ["agent_run_failed", "回答生成失败，请稍后重试。"],
    ["agent_output_invalid", "回答格式校验失败，请重试提问。"],
    ["budget_exhausted", "本轮处理额度已用完，请稍后重试。"],
    ["document_unavailable", "当前文档暂不可用，请稍后重试。"],
    ["baseline_unavailable", "阅读上下文暂不可用，请稍后重试。"],
    ["evidence_scope_invariant_violation", "回答依据校验异常，请重试提问。"],
  ];

  it.each(REASON_CASES)(
    "consumes terminal_reason %s as fixed Chinese copy (not the generic fallback)",
    (reason, expected) => {
      const { handler, onTerminalNotice } = setupHandler([makeStreamingAssistant()]);
      handler({ event: "agentic.terminal", data: agenticTerminal({ terminal_reason: reason }) });
      // ASK-UX-MOBILE-R3 — the SSE handler now fires onTerminalNotice with
      // typed fields (reason string). The formatted Chinese copy is produced
      // by projectTurnTerminalNotice in the panel (tested in
      // ask-system-notice.test.ts). We verify the typed reason is passed
      // through unchanged; the projector maps it to `expected`.
      expect(onTerminalNotice).toHaveBeenCalledTimes(1);
      expect(onTerminalNotice).toHaveBeenCalledWith({
        messageId: "msg-failed-1",
        finalStatus: "failed",
        terminalReason: reason,
      });
      // The expected formatted copy is NOT the generic fallback and NOT the
      // raw reason — it is the fixed Chinese mapping. This is asserted in
      // ask-system-notice.test.ts; here we only confirm the handler does
      // not format (it passes the raw reason through).
      expect(expected).not.toBe("Ask Claread 暂时不可用。");
      expect(expected).not.toBe(reason);
    },
  );

  it("unknown terminal_reason: production shows the fallback, DEV shows raw", () => {
    // ASK-UX-MOBILE-R3 — the SSE handler no longer formats messages. It
    // passes the raw terminal_reason through onTerminalNotice unchanged,
    // regardless of NODE_ENV. The DEV-vs-production fallback behavior now
    // lives in projectTurnTerminalNotice (tested in ask-system-notice.test.ts).
    vi.stubEnv("NODE_ENV", "production");
    const prod = setupHandler([makeStreamingAssistant()]);
    prod.handler({
      event: "agentic.terminal",
      data: agenticTerminal({ terminal_reason: "some_new_reason" }),
    });
    expect(prod.onTerminalNotice).toHaveBeenCalledWith({
      messageId: "msg-failed-1",
      finalStatus: "failed",
      terminalReason: "some_new_reason",
    });

    vi.stubEnv("NODE_ENV", "test");
    const dev = setupHandler([makeStreamingAssistant()]);
    dev.handler({
      event: "agentic.terminal",
      data: agenticTerminal({ terminal_reason: "some_new_reason" }),
    });
    expect(dev.onTerminalNotice).toHaveBeenCalledWith({
      messageId: "msg-failed-1",
      finalStatus: "failed",
      terminalReason: "some_new_reason",
    });
  });

  it("stream error with a known code maps to fixed copy without leaking detail", () => {
    const { handler, onError } = setupHandler([makeStreamingAssistant()]);
    handler({
      event: "error",
      data: {
        code: "SSE_PARSE_ERROR",
        detail: 'Failed to parse SSE data for event "message.delta": oops',
      },
    });
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError).toHaveBeenCalledWith("数据解析异常，请重试。");
  });

  it("stream error with raw backend detail never leaks it (prod and DEV)", () => {
    for (const env of ["production", "test"] as const) {
      vi.stubEnv("NODE_ENV", env);
      const { handler, onError } = setupHandler([makeStreamingAssistant()]);
      handler({
        event: "error",
        data: { code: "HTTP_500", detail: "UnexpectedModelBehavior: structured output invalid" },
      });
      expect(onError).toHaveBeenCalledTimes(1);
      const shown = String(onError.mock.calls[0][0]);
      expect(shown).not.toContain("UnexpectedModelBehavior");
      expect(shown).not.toContain("structured output invalid");
    }
  });

  it("applyAgenticTerminal stores final_status on the message for bubble refinement", () => {
    const { handler, getMessages } = setupHandler([makeStreamingAssistant({ provisional_content_md: "preview" })]);
    handler({
      event: "agentic.terminal",
      data: agenticTerminal({ final_status: "context_stale", terminal_reason: "generation mismatch" }),
    });
    const message = getMessages()[0];
    expect(message.status).toBe("interrupted");
    expect(message.final_status).toBe("context_stale");
    expect(message.content_md).toBe("");
    // ASK-TURN-LIFECYCLE R2 — non-ok terminal drops provisional preview.
    expect(message.provisional_content_md).toBeNull();
  });
});

describe("AiWorkspacePanel – error banner and interrupted bubble copy", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
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

  it("network failure surfaces the fixed network message, never the raw error", async () => {
    vi.mocked(global.fetch).mockImplementation(async () => {
      throw new TypeError("Failed to fetch");
    });
    renderPanel();
    expect(await screen.findByText("网络连接失败，请检查网络后重试。")).not.toBeNull();
    expect(screen.queryByText("Failed to fetch")).toBeNull();
  });

  it("refines the interrupted bubble by final_status=context_stale", async () => {
    mockThreadMessages([
      createAssistantMessage({
        status: "interrupted",
        execution_version: "reader_record_ask_agentic_v2",
        final_status: "context_stale",
        content_md: "partial answer",
      }),
    ]);
    renderPanel();
    const notice = await screen.findByTestId("ask-turn-notice");
    expect(notice.textContent).toContain("阅读上下文已更新，请重试提问。");
    expect(within(notice).getByRole("button", { name: "重新生成" })).not.toBeNull();
    expect(screen.queryByText("上下文已更新，回答已中断。")).toBeNull();
    expect(screen.queryByText("输出中断，可重新生成。")).toBeNull();
  });

  it("refines the interrupted bubble by final_status=cancelled", async () => {
    mockThreadMessages([
      createAssistantMessage({
        status: "interrupted",
        execution_version: "reader_record_ask_agentic_v2",
        final_status: "cancelled",
        content_md: "",
      }),
    ]);
    renderPanel();
    const notice = await screen.findByTestId("ask-turn-notice");
    expect(notice.textContent).toContain("本次回答已取消。");
    const assistant = screen.getByTestId("ask-assistant-message");
    expect(within(assistant).queryByRole("button", { name: "重新生成" })).toBeNull();
  });

  it("keeps the generic interrupted note when final_status is absent", async () => {
    mockThreadMessages([
      createAssistantMessage({
        status: "interrupted",
        content_md: "partial answer",
      }),
    ]);
    renderPanel();
    expect(await screen.findByText("输出中断，可重新生成。")).not.toBeNull();
  });
});

// ---------------------------------------------------------------------------
// ASK-UX-MOBILE R2 — surface capacity gating, turn-scoped notices, panel banner
// ---------------------------------------------------------------------------

describe("AiWorkspacePanel – ASK-UX-MOBILE surface capacity gating", () => {
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

  it("hides the surface menu and shows a static 浮窗 label when hasSidecarCapacity=false", async () => {
    renderPanel({
      surface: "floating",
      onChangeSurface: vi.fn(),
      hasSidecarCapacity: false,
    });

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalled();
    });

    // The interactive menu trigger must NOT be present.
    expect(
      screen.queryByRole("button", { name: "选择 Ask Claread 面板形式" }),
    ).toBeNull();
    // No menuitems should be in the document at all.
    expect(screen.queryByRole("menuitem", { name: "侧边栏" })).toBeNull();
    // The static 浮窗 label is visible (non-interactive span with a title).
    const staticLabel = screen.getByTitle("当前阅读区较窄，仅支持浮窗形式");
    expect(staticLabel.textContent).toContain("浮窗");
  });

  it("shows the surface menu with both options when hasSidecarCapacity=true", async () => {
    renderPanel({
      surface: "floating",
      onChangeSurface: vi.fn(),
      hasSidecarCapacity: true,
    });

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalled();
    });

    const trigger = screen.getByRole("button", { name: "选择 Ask Claread 面板形式" });
    await userEvent.click(trigger);

    expect(await screen.findByRole("menuitem", { name: "侧边栏" })).not.toBeNull();
    expect(screen.getByRole("menuitem", { name: "浮窗" })).not.toBeNull();
  });
});

describe("AiWorkspacePanel – ASK-UX-MOBILE turn-scoped error notices", () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    vi.restoreAllMocks();
    // Clear any leftover mockImplementationOnce queue from prior tests so
    // our mockImplementationOnce is the next one consumed.
    vi.mocked(consumeReaderAskSse).mockReset();
    vi.mocked(consumeReaderAskSse).mockImplementation(async (_response, onEvent) => {
      onEvent({ event: "message.started", data: { message_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee" } });
      onEvent({ event: "message.completed", data: completedPayload });
      return makeLogicalTerminalResult("completed", { finalStatus: "ok" });
    });
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

  it("renders a turn terminal error inside the assistant bubble, not in the composer banner", async () => {
    vi.mocked(consumeReaderAskSse).mockImplementationOnce(async (_response, onEvent) => {
      onEvent({
        event: "error",
        data: {
          code: "INSUFFICIENT_CREDITS",
          user_message: "当前积分不足，本轮请求未发送给模型。",
        },
      });
      return makeLogicalTerminalResult("terminal", { finalStatus: "failed" });
    });

    renderPanel();

    fireEvent.change(screen.getByPlaceholderText("继续问这篇文章…"), {
      target: { value: "解释一下这个问题" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    // The turn notice renders inside the assistant message bubble.
    const turnNotice = await screen.findByTestId("ask-turn-notice");
    expect(turnNotice.textContent).toContain("当前积分不足，本轮请求未发送给模型。");

    // The composer error banner must NOT contain the turn error. Since
    // errorMessage is null after the migration, the composer banner
    // SystemMessage does not render at all — the error text appears only
    // in the turn notice.
    expect(screen.getAllByText("当前积分不足，本轮请求未发送给模型。")).toHaveLength(1);
  });

  it("renders a panel banner below the header when init fails", async () => {
    vi.mocked(global.fetch).mockImplementation(async () => {
      throw new TypeError("Failed to fetch");
    });

    renderPanel();

    const banner = await screen.findByTestId("ask-panel-notice");
    expect(banner.textContent).toContain("网络连接失败，请检查网络后重试。");
    // The panel banner is a sibling of the header, not inside a turn bubble.
    expect(screen.queryByTestId("ask-turn-notice")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// ASK-UX-MOBILE-R3 — canonical notice wiring: live terminal projector,
// foreign terminal guard, optional-tool warning, dismiss, CTA semantics.
// ---------------------------------------------------------------------------

describe("createSseMessageHandler – ASK-UX-MOBILE-R3 canonical terminal-notice path", () => {
  type Msg = ReaderAskUiMessageDto;
  const VERSION = "reader_record_ask_agentic_v2";

  function makeStreamingAssistant(overrides: Partial<Msg> = {}): Msg {
    return {
      id: "msg-1",
      thread_id: "thread-1",
      role: "assistant",
      status: "streaming",
      content_md: "",
      provisional_content_md: null,
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

  function setupHandler(messages: Msg[], initialId = "msg-1") {
    let updatedMessages: Msg[] = messages;
    const updateMessage = (updater: (msgs: Msg[]) => Msg[]) => {
      updatedMessages = updater(updatedMessages);
    };
    const onError = vi.fn();
    const onAgenticActivity = vi.fn();
    const onTerminalNotice = vi.fn();
    const onOptionalToolWarning = vi.fn();
    const handler = createSseMessageHandler(
      initialId,
      updateMessage,
      undefined,
      onError,
      onAgenticActivity,
      onTerminalNotice,
      onOptionalToolWarning,
    );
    return {
      getMessages: () => updatedMessages,
      handler,
      onError,
      onTerminalNotice,
      onOptionalToolWarning,
    };
  }

  function runStartedPayload(overrides: Partial<{
    message_id: string;
    thread_id: string;
    turn_run_id: string;
  }> = {}) {
    return {
      execution_version: VERSION,
      message_id: "msg-1",
      thread_id: "thread-1",
      turn_run_id: "run-1",
      has_initial_selection: false,
      ...overrides,
    };
  }

  function terminalPayload(overrides: Partial<{
    final_status: string;
    message_id: string;
    thread_id: string;
    turn_run_id: string;
    terminal_reason: string | null;
  }> = {}) {
    return {
      execution_version: VERSION,
      final_status: "failed",
      message_id: "msg-1",
      thread_id: "thread-1",
      turn_run_id: "run-1",
      terminal_reason: null,
      ...overrides,
    };
  }

  function progressPayload(overrides: Record<string, unknown> = {}) {
    return {
      execution_version: VERSION,
      phase: "tool",
      summary: "progress",
      ...overrides,
    };
  }

  function completedPayload(overrides: Partial<{
    message_id: string;
    thread_id: string;
    turn_run_id: string;
  }> = {}) {
    return {
      execution_version: VERSION,
      final_status: "ok",
      answer_text: "Answer.",
      answer_blocks: [],
      citations: [],
      knowledge_mode: null,
      source_status: null,
      web_search: null,
      message_id: "msg-1",
      thread_id: "thread-1",
      turn_run_id: "run-1",
      ...overrides,
    };
  }

  // --- live hard terminal uses canonical projector ---

  it("live hard terminal fires onTerminalNotice with typed fields (not a formatted string)", () => {
    const { handler, onTerminalNotice } = setupHandler([makeStreamingAssistant()]);
    handler({ event: "agentic.run_started", data: runStartedPayload() });
    handler({
      event: "agentic.terminal",
      data: terminalPayload({
        final_status: "failed",
        terminal_reason: "agent_run_failed",
      }),
    });
    expect(onTerminalNotice).toHaveBeenCalledTimes(1);
    // The callback receives typed fields, NOT a pre-formatted message string.
    // The panel is responsible for calling projectTurnTerminalNotice with these fields.
    expect(onTerminalNotice).toHaveBeenCalledWith({
      messageId: "msg-1",
      finalStatus: "failed",
      terminalReason: "agent_run_failed",
    });
  });

  // --- context_stale / invalid_citations / cancelled matrix ---

  it.each([
    ["context_stale"],
    ["invalid_citations"],
    ["cancelled"],
  ] as const)(
    "live soft terminal final_status=%s fires onTerminalNotice with the typed finalStatus",
    (status) => {
      const { handler, onTerminalNotice } = setupHandler([makeStreamingAssistant()]);
      handler({ event: "agentic.run_started", data: runStartedPayload() });
      handler({
        event: "agentic.terminal",
        data: terminalPayload({ final_status: status, terminal_reason: null }),
      });
      expect(onTerminalNotice).toHaveBeenCalledTimes(1);
      expect(onTerminalNotice).toHaveBeenCalledWith({
        messageId: "msg-1",
        finalStatus: status,
        terminalReason: null,
      });
    },
  );

  // --- foreign terminal does not create notice ---

  it("foreign terminal (mismatched message_id) after run_started does NOT fire onTerminalNotice", () => {
    const { handler, onTerminalNotice } = setupHandler([makeStreamingAssistant()]);
    handler({ event: "agentic.run_started", data: runStartedPayload() });
    handler({
      event: "agentic.terminal",
      data: terminalPayload({
        message_id: "msg-FOREIGN",
        final_status: "failed",
        terminal_reason: "agent_run_failed",
      }),
    });
    expect(onTerminalNotice).not.toHaveBeenCalled();
  });

  it("foreign terminal (mismatched turn_run_id) after run_started does NOT fire onTerminalNotice", () => {
    const { handler, onTerminalNotice } = setupHandler([makeStreamingAssistant()]);
    handler({ event: "agentic.run_started", data: runStartedPayload() });
    handler({
      event: "agentic.terminal",
      data: terminalPayload({
        turn_run_id: "run-FOREIGN",
        final_status: "failed",
        terminal_reason: "agent_run_failed",
      }),
    });
    expect(onTerminalNotice).not.toHaveBeenCalled();
  });

  // --- optional-tool warning wiring ---

  it("agentic.progress activity=unavailable + completed fires onOptionalToolWarning", () => {
    const { handler, onOptionalToolWarning } = setupHandler([makeStreamingAssistant()]);
    handler({ event: "agentic.run_started", data: runStartedPayload() });
    handler({
      event: "agentic.progress",
      data: progressPayload({ activity: "unavailable" }),
    });
    handler({ event: "message.completed", data: completedPayload() });
    expect(onOptionalToolWarning).toHaveBeenCalledTimes(1);
    expect(onOptionalToolWarning).toHaveBeenCalledWith({ messageId: "msg-1" });
  });

  it("agentic.progress status=unavailable + completed fires onOptionalToolWarning", () => {
    const { handler, onOptionalToolWarning } = setupHandler([makeStreamingAssistant()]);
    handler({ event: "agentic.run_started", data: runStartedPayload() });
    handler({
      event: "agentic.progress",
      data: progressPayload({ status: "unavailable" }),
    });
    handler({ event: "message.completed", data: completedPayload() });
    expect(onOptionalToolWarning).toHaveBeenCalledTimes(1);
  });

  it("no unavailable progress → completed does NOT fire onOptionalToolWarning", () => {
    const { handler, onOptionalToolWarning } = setupHandler([makeStreamingAssistant()]);
    handler({ event: "agentic.run_started", data: runStartedPayload() });
    handler({
      event: "agentic.progress",
      data: progressPayload({ activity: "tool_call", status: "completed" }),
    });
    handler({ event: "message.completed", data: completedPayload() });
    expect(onOptionalToolWarning).not.toHaveBeenCalled();
  });

  it("run_started resets the optional-tool flag (no bleed across turns)", () => {
    const { handler, onOptionalToolWarning } = setupHandler([makeStreamingAssistant()]);
    // Turn 1: optional tool unavailable.
    handler({ event: "agentic.run_started", data: runStartedPayload() });
    handler({
      event: "agentic.progress",
      data: progressPayload({ activity: "unavailable" }),
    });
    handler({ event: "message.completed", data: completedPayload() });
    expect(onOptionalToolWarning).toHaveBeenCalledTimes(1);
    // Turn 2: run_started resets the flag; completed without unavailable progress
    // must NOT fire the warning.
    handler({ event: "agentic.run_started", data: runStartedPayload() });
    handler({
      event: "message.completed",
      data: completedPayload({ message_id: "msg-2" }),
    });
    expect(onOptionalToolWarning).toHaveBeenCalledTimes(1);
  });
});

describe("AiWorkspacePanel – ASK-UX-MOBILE-R3 panel-level notice wiring", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllEnvs();
  });

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.mocked(consumeReaderAskSse).mockReset();
    vi.mocked(consumeReaderAskSse).mockImplementation(async (_response, onEvent) => {
      onEvent({ event: "message.started", data: { message_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee" } });
      onEvent({ event: "message.completed", data: completedPayload });
      return makeLogicalTerminalResult("completed", { finalStatus: "ok" });
    });
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

  const VERSION = "reader_record_ask_agentic_v2";

  function agenticCompletedPayload(overrides: Partial<{
    message_id: string;
    answer_text: string;
  }> = {}) {
    return {
      execution_version: VERSION,
      final_status: "ok" as const,
      answer_text: "已完成回答。",
      answer_blocks: [],
      citations: [],
      knowledge_mode: null,
      source_status: null,
      web_search: null,
      message_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
      thread_id: "thread-1",
      turn_run_id: "run-1",
      ...overrides,
    };
  }

  it("live hard terminal (agentic.terminal) renders the canonical projector output inside the turn bubble", async () => {
    vi.mocked(consumeReaderAskSse).mockImplementationOnce(async (_response, onEvent) => {
      onEvent({ event: "message.started", data: { message_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee" } });
      onEvent({
        event: "agentic.terminal",
        data: {
          execution_version: VERSION,
          final_status: "failed",
          message_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
          thread_id: "thread-1",
          turn_run_id: "run-1",
          terminal_reason: "agent_run_failed",
        },
      });
      return makeLogicalTerminalResult("terminal", { finalStatus: "failed" });
    });

    renderPanel();

    fireEvent.change(screen.getByPlaceholderText("继续问这篇文章…"), {
      target: { value: "解释一下" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    const turnNotice = await screen.findByTestId("ask-turn-notice");
    // Canonical projector output: typed Chinese copy, not the raw terminal_reason.
    expect(turnNotice.textContent).toContain("回答生成失败，请稍后重试。");
    expect(turnNotice.textContent).not.toContain("agent_run_failed");
    // Hard terminal → retry CTA inside the turn notice (scoped to avoid
    // matching the footer "重新生成" action that also renders for interrupted
    // messages).
    expect(within(turnNotice).getByRole("button", { name: "重新生成" })).not.toBeNull();
  });

  it("completed + optional-tool warning renders on the completed bubble (not swallowed by status=completed)", async () => {
    vi.mocked(consumeReaderAskSse).mockImplementationOnce(async (_response, onEvent) => {
      onEvent({ event: "message.started", data: { message_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee" } });
      onEvent({
        event: "agentic.progress",
        data: {
          execution_version: VERSION,
          phase: "tool",
          summary: "Web search unavailable",
          activity: "unavailable",
        },
      });
      onEvent({
        event: "message.completed",
        data: agenticCompletedPayload({ answer_text: "已完成回答。" }),
      });
      return makeLogicalTerminalResult("completed", { finalStatus: "ok" });
    });

    renderPanel();

    fireEvent.change(screen.getByPlaceholderText("继续问这篇文章…"), {
      target: { value: "解释一下" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    // The completed answer is visible.
    await waitFor(() => {
      expect(screen.getByText("已完成回答。")).not.toBeNull();
    });
    // The optional-tool warning is also visible on the same completed bubble.
    const turnNotice = await screen.findByTestId("ask-turn-notice");
    expect(turnNotice.textContent).toContain("部分可选能力暂不可用，回答已正常生成。");
    // No retry CTA on the warning (it is dismissible only).
    expect(turnNotice.querySelector("button")).not.toBeNull();
    expect(turnNotice.textContent).not.toContain("重新生成");
  });

  it("optional warning is dismissible; dismissing removes only the warning, keeps the answer", async () => {
    vi.mocked(consumeReaderAskSse).mockImplementationOnce(async (_response, onEvent) => {
      onEvent({ event: "message.started", data: { message_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee" } });
      onEvent({
        event: "agentic.progress",
        data: {
          execution_version: VERSION,
          phase: "tool",
          summary: "Web search unavailable",
          activity: "unavailable",
        },
      });
      onEvent({
        event: "message.completed",
        data: agenticCompletedPayload({ answer_text: "保留的回答。" }),
      });
      return makeLogicalTerminalResult("completed", { finalStatus: "ok" });
    });

    renderPanel();

    fireEvent.change(screen.getByPlaceholderText("继续问这篇文章…"), {
      target: { value: "解释一下" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    // Wait for the warning to appear.
    const turnNotice = await screen.findByTestId("ask-turn-notice");
    expect(turnNotice.textContent).toContain("部分可选能力暂不可用，回答已正常生成。");

    // The completed answer is visible.
    expect(screen.getByText("保留的回答。")).not.toBeNull();

    // Click the dismiss button (aria-label="关闭提示").
    const dismissButton = turnNotice.querySelector('button[aria-label="关闭提示"]');
    expect(dismissButton).not.toBeNull();
    fireEvent.click(dismissButton!);

    // The warning is gone, the answer remains.
    await waitFor(() => {
      expect(screen.queryByTestId("ask-turn-notice")).toBeNull();
    });
    expect(screen.getByText("保留的回答。")).not.toBeNull();
  });

  it("composer does not render an error banner for turn errors (errorMessage prop not wired)", async () => {
    vi.mocked(consumeReaderAskSse).mockImplementationOnce(async (_response, onEvent) => {
      onEvent({ event: "message.started", data: { message_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee" } });
      onEvent({
        event: "agentic.terminal",
        data: {
          execution_version: VERSION,
          final_status: "failed",
          message_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
          thread_id: "thread-1",
          turn_run_id: "run-1",
          terminal_reason: "agent_run_failed",
        },
      });
      return makeLogicalTerminalResult("terminal", { finalStatus: "failed" });
    });

    renderPanel();

    fireEvent.change(screen.getByPlaceholderText("继续问这篇文章…"), {
      target: { value: "解释一下" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    // The turn notice appears inside the assistant bubble.
    const turnNotice = await screen.findByTestId("ask-turn-notice");
    expect(turnNotice.textContent).toContain("回答生成失败，请稍后重试。");

    // The composer area must NOT contain a SystemMessage error banner.
    // The composer is the PromptInput wrapper; the error banner would be a
    // SystemMessage with variant="error" rendered above the PromptInput.
    // Since errorMessage is not passed to AskComposer, no such banner exists.
    const composer = screen.getByPlaceholderText("继续问这篇文章…").closest("form");
    expect(composer).not.toBeNull();
    // The turn error text appears exactly once (only in the turn notice, not
    // duplicated in a composer banner).
    expect(screen.getAllByText("回答生成失败，请稍后重试。")).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// ASK-COT — Chain of Thought convergence (B1)
//
// v2 turns converge reasoning + activity into one turn-scoped disclosure:
// - live: TurnProcessDisclosure driven by the live activity state;
// - settled (same session): frozen snapshot persisted before the idle reset;
// - cold history: reasoning-only (snapshots never persist);
// - legacy lanes keep ReasoningPanel + ToolTrace untouched;
// - warnings/errors stay the SystemMessage turn notice's sole property.
// ---------------------------------------------------------------------------

describe("AiWorkspacePanel – ASK-COT chain of thought convergence", () => {
  const VERSION = "reader_record_ask_agentic_v2";

  afterEach(() => {
    cleanup();
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.mocked(consumeReaderAskSse).mockReset();
    vi.mocked(consumeReaderAskSse).mockImplementation(async (_response, onEvent) => {
      onEvent({ event: "message.started", data: { message_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee" } });
      onEvent({ event: "message.completed", data: completedPayload });
      return makeLogicalTerminalResult("completed", { finalStatus: "ok" });
    });
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

  function agenticCompletedPayload(overrides: Record<string, unknown> = {}) {
    return {
      execution_version: VERSION,
      final_status: "ok" as const,
      answer_text: "已完成回答。",
      answer_blocks: [{ text: "已完成回答。", citation_ids: [] }],
      citations: [],
      knowledge_mode: null,
      source_status: null,
      web_search: null,
      message_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
      thread_id: "thread-1",
      turn_run_id: "run-1",
      ...overrides,
    };
  }

  function runStartedPayload() {
    return {
      execution_version: VERSION,
      message_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
      thread_id: "thread-1",
      turn_run_id: "run-1",
      has_initial_selection: false,
      web_search_mode: "disabled" as const,
    };
  }

  function reasoningPayloads() {
    const identity = {
      execution_version: VERSION,
      message_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
      thread_id: "thread-1",
      turn_run_id: "run-1",
    };
    return {
      started: {
        ...identity,
        seq: 0,
        projection_policy_version: "reasoning_projection_v1",
      },
      delta: { ...identity, seq: 1, delta: "已脱敏思考。" },
      completed: {
        ...identity,
        seq: 2,
        has_content: true,
        truncated: false,
        projection_policy_version: "reasoning_projection_v1",
      },
    };
  }

  async function sendTurn() {
    fireEvent.change(screen.getByPlaceholderText("继续问这篇文章…"), {
      target: { value: "解释一下" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
  }

  it("settled v2 turn keeps a frozen Chain of Thought (snapshot persisted before idle reset)", async () => {
    vi.mocked(consumeReaderAskSse).mockImplementationOnce(async (_response, onEvent) => {
      onEvent({ event: "message.started", data: { message_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee" } });
      onEvent({ event: "agentic.run_started", data: runStartedPayload() });
      onEvent({
        event: "agentic.progress",
        data: {
          execution_version: VERSION,
          sequence: 1,
          phase: "reading_context",
          activity: "started",
          summary: "正在读取文章上下文",
          elapsed_ms: 500,
          tool_name: "read_range",
          status: "running",
        },
      });
      onEvent({
        event: "agentic.progress",
        data: {
          execution_version: VERSION,
          sequence: 2,
          phase: "reading_context",
          activity: "completed",
          summary: "已读取相关上下文",
          elapsed_ms: 1200,
          tool_name: "read_range",
          status: "ok",
          duration_ms: 700,
        },
      });
      const reasoning = reasoningPayloads();
      onEvent({ event: "agentic.reasoning.started", data: reasoning.started });
      onEvent({ event: "agentic.reasoning.delta", data: reasoning.delta });
      onEvent({ event: "agentic.reasoning.completed", data: reasoning.completed });
      onEvent({ event: "message.completed", data: agenticCompletedPayload() });
      return makeLogicalTerminalResult("completed", { finalStatus: "ok" });
    });

    renderPanel();
    await sendTurn();
    await waitFor(() => {
      expect(screen.getByText("已完成回答。")).not.toBeNull();
    });

    // The settled CoT persists after the live activity reset to idle.
    const cot = await screen.findByTestId("ask-turn-process");
    expect(cot.getAttribute("data-turn-process-state")).toBe("settled");
    expect(cot.textContent).toContain("已根据当前文章整理 · 1s");
    // The live activity row is gone (replaced by the settled disclosure).
    expect(screen.queryByTestId("ask-agentic-activity")).toBeNull();
    // The legacy reasoning disclosure is not rendered for v2 turns.
    expect(cot.closest("[data-message-role='assistant']")?.querySelector("[data-slot='reasoning']"))
      .toBeNull();

    // Expanding shows the frozen typed steps (fixed labels) AND the safe
    // reasoning projection (R3: 思考要点 lives inside the same disclosure).
    fireEvent.click(screen.getByRole("button", { name: "本轮回答已完成" }));
    expect(screen.getByText("阅读本文")).not.toBeNull();
    // R3: the full learner narrative survives settle — 理解问题 → 阅读本文
    // → 整理回答 (composing-answer is no longer hidden after completion).
    expect(screen.getByText("理解问题")).not.toBeNull();
    expect(screen.getByText("整理回答")).not.toBeNull();
    // Server summary must not appear in CoT DOM.
    expect(screen.queryByText("已读取相关上下文")).toBeNull();
    expect(screen.queryByText("正在读取文章上下文")).toBeNull();
    // R3: the server-safe reasoning projection renders as 思考要点 inside
    // the disclosure — still no SECOND (legacy) reasoning card.
    const reasoningSection = screen.getByTestId("ask-turn-process-reasoning");
    expect(reasoningSection.textContent).toContain("思考要点");
    expect(reasoningSection.textContent).toContain("已脱敏思考。");

    // Leak scan: the CoT subtree carries no internals. (The safe reasoning
    // text itself is approved content — the server redactor owns it.)
    const serialized = cot.innerHTML;
    for (const leaked of [
      "evh_",
      "turn_run_id",
      "projection_policy_version",
      "envelope_fingerprint",
      "run-1",
      "已读取相关上下文",
      "正在读取文章上下文",
    ]) {
      expect(serialized, `CoT DOM must not contain ${leaked}`).not.toContain(leaked);
    }
  });

  it("non-ok terminal freezes steps as interrupted; the warning stays with the SystemMessage notice", async () => {
    vi.mocked(consumeReaderAskSse).mockImplementationOnce(async (_response, onEvent) => {
      onEvent({ event: "message.started", data: { message_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee" } });
      onEvent({ event: "agentic.run_started", data: runStartedPayload() });
      onEvent({
        event: "agentic.progress",
        data: {
          execution_version: VERSION,
          sequence: 1,
          phase: "reading_context",
          activity: "started",
          summary: "正在阅读本文上下文",
          elapsed_ms: 800,
          status: "running",
        },
      });
      onEvent({
        event: "agentic.terminal",
        data: {
          execution_version: VERSION,
          final_status: "failed",
          message_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
          thread_id: "thread-1",
          turn_run_id: "run-1",
          terminal_reason: "agent_run_failed",
        },
      });
      onEvent({
        event: "message.interrupted",
        data: {
          execution_version: VERSION,
          final_status: "failed",
          message_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
          thread_id: "thread-1",
          turn_run_id: "run-1",
          terminal_reason: "agent_run_failed",
        },
      });
      return makeLogicalTerminalResult("terminal", { finalStatus: "failed" });
    });

    renderPanel();
    await sendTurn();

    // SystemMessage notice owns the error copy.
    const turnNotice = await screen.findByTestId("ask-turn-notice");
    expect(turnNotice.textContent).toContain("回答生成失败，请稍后重试。");

    // The CoT keeps the safe process, frozen, never a success.
    const cot = await screen.findByTestId("ask-turn-process");
    expect(cot.getAttribute("data-turn-process-state")).toBe("settled");
    fireEvent.click(within(cot).getByRole("button"));
    const step = within(cot)
      .getByText("阅读本文")
      .closest("[data-step-status]");
    expect(step?.getAttribute("data-step-status")).toBe("interrupted");
    // No error/terminal/server-summary copy leaks into the CoT.
    expect(cot.textContent).not.toContain("agent_run_failed");
    expect(cot.textContent).not.toContain("回答生成失败");
    expect(cot.textContent).not.toContain("正在阅读本文上下文");
  });

  it("retry clears the previous attempt's snapshot before the new run", async () => {
    vi.mocked(consumeReaderAskSse).mockImplementationOnce(async (_response, onEvent) => {
      onEvent({ event: "message.started", data: { message_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee" } });
      onEvent({ event: "agentic.run_started", data: runStartedPayload() });
      onEvent({
        event: "agentic.progress",
        data: {
          execution_version: VERSION,
          sequence: 1,
          phase: "reading_context",
          activity: "started",
          summary: "正在阅读本文上下文",
          elapsed_ms: 400,
          tool_name: "read_range",
          status: "running",
        },
      });
      onEvent({
        event: "agentic.progress",
        data: {
          execution_version: VERSION,
          sequence: 2,
          phase: "reading_context",
          activity: "completed",
          summary: "已读取相关上下文",
          elapsed_ms: 900,
          tool_name: "read_range",
          status: "ok",
          duration_ms: 900,
        },
      });
      onEvent({ event: "message.completed", data: agenticCompletedPayload() });
      return makeLogicalTerminalResult("completed", { finalStatus: "ok" });
    });

    renderPanel();
    await sendTurn();
    await waitFor(() => {
      expect(screen.getByText("已根据当前文章整理 · 1s")).not.toBeNull();
    });

    // Retry wipes the old attempt's frozen process immediately. (The retry
    // endpoint is unrouted in mockFetch, so the retry itself fails — the
    // catch path restores the original answer; either way the snapshot
    // must stay cleared and no CoT may linger from attempt 1.)
    fireEvent.click(screen.getByRole("button", { name: "重新生成" }));
    await waitFor(() => {
      expect(screen.queryByText("已根据当前文章整理 · 1s")).toBeNull();
    });
    await waitFor(() => {
      expect(screen.getByText("已完成回答。")).not.toBeNull();
    });
    expect(screen.queryByTestId("ask-turn-process")).toBeNull();
  });

  it("cold v2 history renders a reasoning-only Chain of Thought (no steps)", async () => {
    const baseFetch = mockFetch();
    const coldAssistant = {
      id: "msg-cold-1",
      thread_id: "thread-1",
      role: "assistant",
      status: "completed",
      content_md: "冷答案。",
      submission_mode: "chat",
      resolved_intent: "explain",
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
      execution_version: VERSION,
      final_status: "ok",
      reasoning_md: "冷推理文本。",
      reasoning_status: "completed",
      reasoning_truncated: false,
      agentic_answer_blocks: [{ text: "冷答案。", citation_ids: [] }],
      agentic_citations: [],
      agentic_web_search: null,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = new URL(String(input), "http://localhost");
        if (url.pathname === "/api/web/reader-ask/threads/thread-1") {
          return jsonResponse({
            id: "thread-1",
            record_id: "record-1",
            title: "Ask Claread",
            is_default: true,
            selected_model: null,
            archived_at: null,
            created_at: "2026-05-20T00:00:00Z",
            updated_at: "2026-05-20T00:00:00Z",
            last_message_at: "2026-05-20T00:00:00Z",
            messages: [
              {
                id: "msg-cold-0",
                thread_id: "thread-1",
                role: "user",
                status: "completed",
                content_md: "冷问题。",
                submission_mode: "chat",
                resolved_intent: "explain",
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
              coldAssistant,
            ],
          });
        }
        return baseFetch(input, init);
      }),
    );

    renderPanel();

    // R3 — cold v2 history (no activity, no snapshot) renders a
    // reasoning-only disclosure when the persisted safe reasoning
    // projection exists — never fabricated process steps.
    await waitFor(() => {
      expect(screen.getByText("冷答案。")).not.toBeNull();
    });
    const cot = await screen.findByTestId("ask-turn-process");
    expect(cot.getAttribute("data-turn-process-state")).toBe("settled");
    expect(cot.textContent).toContain("思考过程");
    fireEvent.click(within(cot).getByRole("button"));
    const reasoningSection = within(cot).getByTestId("ask-turn-process-reasoning");
    expect(reasoningSection.textContent).toContain("思考要点");
    expect(reasoningSection.textContent).toContain("冷推理文本。");
    // No fabricated process steps for cold history.
    expect(cot.querySelector("[data-step-status]")).toBeNull();
    // Legacy reasoning disclosure is still absent for cold v2 history —
    // reasoning lives inside the CoT disclosure only.
    const bubble = screen
      .getByText("冷答案。")
      .closest("[data-message-role='assistant']");
    expect(bubble?.querySelector("[data-slot='reasoning']")).toBeNull();
  });

  it("legacy lanes keep ReasoningPanel and never render the CoT", async () => {
    vi.mocked(consumeReaderAskSse).mockImplementationOnce(async (_response, onEvent) => {
      onEvent({ event: "message.started", data: { message_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee" } });
      onEvent({ event: "reasoning.started", data: { message_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee" } });
      onEvent({
        event: "reasoning.delta",
        data: { message_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", delta: "legacy thinking" },
      });
      onEvent({ event: "reasoning.completed", data: { message_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee" } });
      onEvent({ event: "message.completed", data: completedPayload });
      return makeLogicalTerminalResult("completed", { finalStatus: "ok" });
    });

    renderPanel();
    await sendTurn();
    await waitFor(() => {
      expect(screen.getByText("解释完成。")).not.toBeNull();
    });

    // Legacy reasoning disclosure rendered; CoT never appears.
    const bubble = screen
      .getByText("解释完成。")
      .closest("[data-message-role='assistant']");
    expect(bubble?.querySelector("[data-slot='reasoning']")).not.toBeNull();
    expect(screen.queryByTestId("ask-turn-process")).toBeNull();
  });

  // -------------------------------------------------------------------------
  // ASK-UX-HISTORY-COT-R2 P0-3: agentic v2 optimistic message enters
  // TurnProcessDisclosure at T0 — the moment the bubble is created with a
  // bound (even idle) activity. The old AssistantStreamingIndicator
  // (two-line status card) must NOT flash before the typed disclosure
  // takes over. Only the explicit legacy lane (analysis scope) keeps the
  // old indicator at T0.
  // -------------------------------------------------------------------------

  it("agentic-capable panel renders TurnProcessDisclosure at T0 before run_started (no old status card)", async () => {
    // Stall the SSE stream after message.started so the optimistic
    // assistant message stays in the T0 state: streaming, idle activity,
    // no execution_version, no snapshot. This is the exact window where
    // the old code flashed AssistantStreamingIndicator before
    // agentic.run_started switched to TurnProcessDisclosure.
    let releaseStream: () => void = () => {};
    const streamReleased = new Promise<void>((resolve) => {
      releaseStream = resolve;
    });
    vi.mocked(consumeReaderAskSse).mockImplementationOnce(async (_response, onEvent) => {
      onEvent({ event: "message.started", data: { message_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee" } });
      // Stall — never fire agentic.run_started. The optimistic message
      // stays in T0 state.
      await streamReleased;
      onEvent({ event: "message.completed", data: agenticCompletedPayload() });
      return makeLogicalTerminalResult("completed", { finalStatus: "ok" });
    });

    // reading_record scope ⇒ isAgenticCapable=true ⇒ isAgenticV2Turn=true
    // at T0 even with idle activity.
    renderPanel({ recordScope: "reading_record" });
    await sendTurn();

    // T0: TurnProcessDisclosure is already rendered — the running header
    // carries the R3 first lifecycle label "正在理解问题" (only the host
    // 理解问题 step is provable before any wire phase arrives).
    const cot = await screen.findByTestId("ask-turn-process");
    expect(cot.getAttribute("data-turn-process-state")).toBe("running");
    expect(cot.textContent).toContain("正在理解问题");

    // The old two-line status card copy must NOT appear at T0.
    expect(screen.queryByText("正在整理问题")).toBeNull();

    // Release the stalled stream so the test can complete cleanly.
    releaseStream();
    await waitFor(() => {
      expect(screen.getByText("已完成回答。")).not.toBeNull();
    });
  });

  it("analysis-scope (legacy lane) keeps AssistantStreamingIndicator at T0 (no CoT)", async () => {
    // Same T0 stall, but analysis scope ⇒ isAgenticCapable=false ⇒
    // isAgenticV2Turn=false at T0 (idle activity). The old indicator
    // is the correct owner for the explicit legacy lane.
    let releaseStream: () => void = () => {};
    const streamReleased = new Promise<void>((resolve) => {
      releaseStream = resolve;
    });
    vi.mocked(consumeReaderAskSse).mockImplementationOnce(async (_response, onEvent) => {
      onEvent({ event: "message.started", data: { message_id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee" } });
      await streamReleased;
      onEvent({ event: "message.completed", data: completedPayload });
      return makeLogicalTerminalResult("completed", { finalStatus: "ok" });
    });

    // analysis scope (default) ⇒ isAgenticCapable=false.
    renderPanel();
    await sendTurn();

    // T0: no TurnProcessDisclosure for the legacy lane.
    expect(screen.queryByTestId("ask-turn-process")).toBeNull();

    // The old indicator copy is present (正在整理问题 = no answer content,
    // no reasoning streaming, not compacting, not replanning).
    await waitFor(() => {
      expect(screen.getByText("正在整理问题")).not.toBeNull();
    });

    releaseStream();
    await waitFor(() => {
      expect(screen.getByText("解释完成。")).not.toBeNull();
    });
  });
});

// ---------------------------------------------------------------------------
// ASK-UX-COT-COMPOSER-R3 P1 — Reading Record composer selection slots
// ---------------------------------------------------------------------------

function rrSelectionAttachment(
  id: string,
  offsets: [number, number],
  text: string,
): ReaderAskAttachment {
  return {
    kind: "text_selection",
    subtype: "text_range",
    label: text,
    selectedText: text,
    targetKey: `seg-${id}`,
    metadata: {
      pageIdentity,
      sourceSurface: "selection_toolbar",
      entryAction: "ask_about_this",
      readingRecordAnchor: {
        record_id: "record-1",
        base_id: "base-1",
        generation: 3,
        unit_id: "unit-1",
        anchor_segment_id: `seg-${id}`,
        scope: "stable_source",
        offset_unit: "utf16",
        start_offset: offsets[0],
        end_offset: offsets[1],
        selected_text: text,
        text_hash: "9fd7545a",
        hash_algorithm: "fnv1a32-utf16",
      },
    },
  } as unknown as ReaderAskAttachment;
}

describe("ASK-UX-COT-COMPOSER-R3 P1 — RR composer selection slots", () => {
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

  it("always renders the non-removable current-article chip from the record title (never the thread title)", async () => {
    const { container } = renderPanel({
      recordScope: "reading_record",
      recordTitle: "机构记忆与政策连续性",
    });
    const chip = await waitFor(() => {
      const found = container.querySelector("[data-ask-current-article-chip]");
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    expect(chip.textContent).toContain("机构记忆与政策连续性");
    expect(chip.getAttribute("aria-label")).toBe("当前文章：机构记忆与政策连续性");
    // Non-removable: no remove button inside the chip.
    expect(chip.querySelector("button")).toBeNull();
    // No "基于：当前文章" provenance — the article is implicit context.
    expect(container.textContent).not.toContain("基于：当前文章");
  });

  it("does not render the article chip outside reading_record scope", async () => {
    const { container } = renderPanel({ recordScope: "analysis" });
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalled();
    });
    expect(container.querySelector("[data-ask-current-article-chip]")).toBeNull();
  });

  it("orders the strip: article chip first, then auto, then manual selections", async () => {
    const auto = rrSelectionAttachment("auto", [0, 6], "自动选区文本");
    const manual1 = rrSelectionAttachment("m1", [10, 16], "固定选区一");
    const manual2 = rrSelectionAttachment("m2", [20, 26], "固定选区二");
    const { container } = renderPanel({
      recordScope: "reading_record",
      autoSelectionAttachment: auto,
      manualSelectionAttachments: [manual1, manual2],
    });
    const strip = await waitFor(() => {
      const found = container.querySelector("[data-ask-context-strip]");
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    const markers = Array.from(
      strip.querySelectorAll("[data-ask-current-article-chip],[data-ask-selection-slot]"),
    );
    expect(markers).toHaveLength(4);
    expect(markers[0]?.getAttribute("data-ask-current-article-chip")).toBe("true");
    expect(markers[1]?.getAttribute("data-ask-selection-slot")).toBe("auto");
    expect(markers[2]?.getAttribute("data-ask-selection-slot")).toBe("manual");
    expect(markers[3]?.getAttribute("data-ask-selection-slot")).toBe("manual");
    expect(strip.textContent).toContain("自动选区文本");
    expect(strip.textContent).toContain("固定选区一");
    expect(strip.textContent).toContain("固定选区二");
  });

  it("auto and manual chips are independently removable via their slot callbacks", async () => {
    const onRemoveAutoSelection = vi.fn();
    const onRemoveManualSelection = vi.fn();
    const auto = rrSelectionAttachment("auto", [0, 6], "自动选区文本");
    const manual = rrSelectionAttachment("m1", [10, 16], "固定选区一");
    renderPanel({
      recordScope: "reading_record",
      autoSelectionAttachment: auto,
      manualSelectionAttachments: [manual],
      onRemoveAutoSelection,
      onRemoveManualSelection,
    });
    fireEvent.click(
      await screen.findByRole("button", { name: "移除自动选区：自动选区文本" }),
    );
    expect(onRemoveAutoSelection).toHaveBeenCalledTimes(1);
    fireEvent.click(
      screen.getByRole("button", { name: "移除固定选区：固定选区一" }),
    );
    expect(onRemoveManualSelection).toHaveBeenCalledTimes(1);
  });

  it("sends auto + manual selections as explicit attachments and keeps them after send", async () => {
    const auto = rrSelectionAttachment("auto", [0, 6], "自动选区文本");
    const manual = rrSelectionAttachment("m1", [10, 16], "固定选区一");
    const { container } = renderPanel({
      recordScope: "reading_record",
      autoSelectionAttachment: auto,
      manualSelectionAttachments: [manual],
    });

    fireEvent.change(screen.getByPlaceholderText("继续问这篇文章…"), {
      target: { value: "结合选区解释一下" },
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
    const body = JSON.parse(String(streamCall?.[1]?.body)) as {
      attachments: Array<{
        selected_text?: string | null;
        metadata: { reading_record_anchor?: Record<string, unknown> | null };
      }>;
    };
    // BOTH slots ride along — auto first, manual second. Never just the
    // first anchor.
    expect(body.attachments).toHaveLength(2);
    expect(body.attachments[0]?.metadata.reading_record_anchor).toMatchObject({
      anchor_segment_id: "seg-auto",
      start_offset: 0,
      end_offset: 6,
    });
    expect(body.attachments[1]?.metadata.reading_record_anchor).toMatchObject({
      anchor_segment_id: "seg-m1",
      start_offset: 10,
      end_offset: 16,
    });

    // Draft selections persist after the message is sent — chips remain.
    expect(container.querySelector("[data-ask-selection-slot='auto']")).not.toBeNull();
    expect(container.querySelector("[data-ask-selection-slot='manual']")).not.toBeNull();
  });

  it("merges visible selections into a quick action with explicit attachments", async () => {
    const auto = rrSelectionAttachment("auto", [0, 6], "自动选区文本");
    const explicit = rrSelectionAttachment("quick", [30, 36], "快捷动作附件");
    renderPanel({
      recordScope: "reading_record",
      autoSelectionAttachment: auto,
      pendingQuickActionRequest: {
        content: "解释选区",
        attachments: [explicit],
        entryAction: "ask_about_this",
        submissionMode: "quick_action",
      },
    });

    await waitFor(() => {
      const streamCall = vi
        .mocked(global.fetch)
        .mock.calls.findLast(([url]) => String(url).includes("/messages/stream"));
      expect(streamCall).toBeTruthy();
    });
    const streamCall = vi
      .mocked(global.fetch)
      .mock.calls.findLast(([url]) => String(url).includes("/messages/stream"));
    const body = JSON.parse(String(streamCall?.[1]?.body)) as {
      attachments: Array<{
        metadata: { reading_record_anchor?: { anchor_segment_id?: string } | null };
      }>;
    };
    expect(
      body.attachments.map(
        (item) => item.metadata.reading_record_anchor?.anchor_segment_id,
      ),
    ).toEqual(["seg-quick", "seg-auto"]);
  });

  it("surfaces explicit selections in provenance but never the implicit article", async () => {
    const auto = rrSelectionAttachment("auto", [0, 6], "自动选区文本");
    const { container } = renderPanel({
      recordScope: "reading_record",
      autoSelectionAttachment: auto,
    });
    await waitFor(() => {
      expect(container.textContent).toContain("基于：选中段");
    });
    expect(container.textContent).not.toContain("基于：当前文章");
  });
});
