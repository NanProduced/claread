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
    used_hitp_asset_disambiguation: false,
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
    used_record_assets: false,
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
  disambiguation: null,
  asset_disambiguation: null,
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
    if (url.endsWith("/api/web/reader-ask/threads/thread-1/actions/act-supplement-1/confirm")) {
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
    if (url.endsWith("/api/web/reader-ask/supplements/supp-1")) {
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

  it("sends only the current reader ask request shape", async () => {
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

  it("renders disambiguation candidate cards and re-sends the current question after selection", async () => {
    const onAppendAttachments = vi.fn();

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
              asset_disambiguation: null,
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
                used_hitp_asset_disambiguation: false,
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
              asset_disambiguation: null,
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
        activeSentence={null}
        attachments={[]}
        onAppendAttachments={onAppendAttachments}
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
      expect(onAppendAttachments).toHaveBeenCalledTimes(1);
    });
    expect(onAppendAttachments.mock.calls[0]?.[0]).toMatchObject([
      {
        kind: "record_ref",
        subtype: "related_record",
        label: "Climate Policy",
        targetKey: "record:record-2:record",
      },
    ]);

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
                used_hitp_asset_disambiguation: false,
                supplement_generation_used: true,
                supplement_persisted_count: 0,
                supplement_deleted_count: 0,
                cross_record_context_allowed: false,
                cross_record_context_used: false,
                tool_steps: [],
                notes: [],
              },
              disambiguation: null,
              asset_disambiguation: null,
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
        activeSentence={null}
        attachments={[]}
        onActionExecuted={onActionExecuted}
        onSupplementDeleted={onSupplementDeleted}
        onRemoveAttachment={vi.fn()}
        onClearAttachments={vi.fn()}
        onToggle={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("待确认补充")).not.toBeNull();
    });

    fireEvent.click(screen.getByRole("button", { name: "确认" }));

    await waitFor(() => {
      expect(screen.getByText("已写入当前页")).not.toBeNull();
      expect(screen.getByText("已把这条 AI 补充写入当前页。")).not.toBeNull();
    });
    expect(onActionExecuted).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "删除" }));

    await waitFor(() => {
      expect(screen.getByText("已从当前页移除这条 AI 补充。")).not.toBeNull();
    });
    expect(onSupplementDeleted).toHaveBeenCalledWith("supp-1");
    expect(screen.queryByText("已写入当前页")).toBeNull();
  });

  it("renders asset disambiguation cards and re-sends the current question after selection", async () => {
    const onAppendAttachments = vi.fn();

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
              asset_disambiguation: null,
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
                used_hitp_asset_disambiguation: true,
                supplement_generation_used: false,
                supplement_persisted_count: 0,
                supplement_deleted_count: 0,
                cross_record_context_allowed: true,
                cross_record_context_used: false,
                tool_steps: [],
                notes: [],
              },
              disambiguation: null,
              asset_disambiguation: {
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
        activeSentence={null}
        attachments={[]}
        onAppendAttachments={onAppendAttachments}
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
      expect(onAppendAttachments).toHaveBeenCalledTimes(1);
    });
    expect(onAppendAttachments.mock.calls[0]?.[0]).toMatchObject([
      {
        kind: "analysis_ref",
        subtype: "sentence_analysis",
        label: "Concept analysis",
        targetKey: "record:record-2:analysis:sentence_analysis:analysis-1",
        metadata: {
          sourceSurface: "ask_hitp_asset_picker",
          recordId: "record-2",
          recordTitle: "Climate Policy",
          assetId: "analysis-1",
        },
      },
    ]);

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
});
