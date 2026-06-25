import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

vi.mock("@/services/bff/session", () => ({
  getWebSession: vi.fn(),
}));

vi.mock("@/services/api/reader-ask", () => ({
  confirmUpstreamReaderAskAction: vi.fn(),
  confirmUpstreamReadingRecordAskAction: vi.fn(),
  createUpstreamReaderAskStream: vi.fn(),
  createUpstreamReaderAskThread: vi.fn(),
  createUpstreamReadingRecordAskDefaultThread: vi.fn(),
  createUpstreamReadingRecordAskStream: vi.fn(),
  deleteUpstreamReaderAskSupplement: vi.fn(),
  deleteUpstreamReadingRecordAskSupplement: vi.fn(),
  getUpstreamReaderAskThread: vi.fn(),
  getUpstreamReadingRecordAskThread: vi.fn(),
  listUpstreamReaderAskContextRecords: vi.fn(),
  listUpstreamReaderAskModelOptions: vi.fn(),
  listUpstreamReaderAskThreads: vi.fn(),
  listUpstreamReadingRecordAskThreads: vi.fn(),
  resetUpstreamReaderAskThread: vi.fn(),
  resetUpstreamReadingRecordAskThread: vi.fn(),
  retryUpstreamReaderAskMessage: vi.fn(),
  retryUpstreamReadingRecordAskMessage: vi.fn(),
}));

import { getWebSession } from "@/services/bff/session";
import {
  createUpstreamReaderAskThread,
  createUpstreamReadingRecordAskDefaultThread,
  createUpstreamReadingRecordAskStream,
  listUpstreamReadingRecordAskThreads,
} from "@/services/api/reader-ask";
import {
  createReaderAskStreamForWeb,
  createReaderAskThreadForWeb,
  listReaderAskThreadsForWeb,
} from "./reader-ask";

const mockSession = {
  kind: "authenticated" as const,
  sessionToken: "session-token",
  source: "cookie" as const,
};

describe("reader-ask BFF RR cutover", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getWebSession).mockResolvedValue(mockSession);
  });

  it("lists RR threads through the RR upstream API", async () => {
    vi.mocked(listUpstreamReadingRecordAskThreads).mockResolvedValue({
      ok: true,
      data: {
        items: [],
      },
    });

    const result = await listReaderAskThreadsForWeb(
      "reading-record-1",
      "reading_record",
    );

    expect(result).toEqual({ items: [] });
    expect(listUpstreamReadingRecordAskThreads).toHaveBeenCalledWith(
      "reading-record-1",
      "session-token",
    );
  });

  it("creates the default RR thread instead of the legacy thread contract", async () => {
    vi.mocked(createUpstreamReadingRecordAskDefaultThread).mockResolvedValue({
      ok: true,
      data: {
        id: "thread-rr-1",
        record_id: "reading-record-1",
        title: "Ask Claread",
        is_default: true,
        selected_model: null,
        archived_at: null,
        created_at: "2026-06-25T00:00:00Z",
        updated_at: "2026-06-25T00:00:00Z",
        last_message_at: null,
      },
    });

    const result = await createReaderAskThreadForWeb({
      record_id: "reading-record-1",
      record_scope: "reading_record",
      title: "Ignored title",
      model: "ask-fast",
    });

    expect(result).toMatchObject({
      id: "thread-rr-1",
      record_id: "reading-record-1",
      is_default: true,
    });
    expect(createUpstreamReadingRecordAskDefaultThread).toHaveBeenCalledWith(
      "reading-record-1",
      "session-token",
    );
    expect(createUpstreamReaderAskThread).not.toHaveBeenCalled();
  });

  it("keeps the legacy create-thread path untouched for analysis scope", async () => {
    vi.mocked(createUpstreamReaderAskThread).mockResolvedValue({
      ok: true,
      data: {
        id: "thread-legacy-1",
        record_id: "analysis-record-1",
        title: "Legacy title",
        is_default: false,
        selected_model: null,
        archived_at: null,
        created_at: "2026-06-25T00:00:00Z",
        updated_at: "2026-06-25T00:00:00Z",
        last_message_at: null,
      },
    });

    await createReaderAskThreadForWeb({
      record_id: "analysis-record-1",
      record_scope: "analysis",
      title: "Legacy title",
      model: "ask-fast",
    });

    expect(createUpstreamReaderAskThread).toHaveBeenCalledWith(
      {
        record_id: "analysis-record-1",
        title: "Legacy title",
        model: "ask-fast",
      },
      "session-token",
    );
  });

  it("rejects RR stream requests that do not carry a reading record id", async () => {
    const result = await createReaderAskStreamForWeb(
      "thread-rr-1",
      {
        content: "Explain this paragraph",
        entry_action: "ask_about_this",
        page_identity: {
          record_id: "reading-record-1",
          title: "Reading Record",
          surface: "reader",
          source: "reader_2_0",
          available_context_capabilities: [],
          has_article_overview: false,
          has_sentence_entries: true,
          has_annotations: false,
          has_reader_notes: false,
        },
        attachments: [],
      },
      null,
      "reading_record",
    );

    expect(result.status).toBe(400);
    await expect(result.json()).resolves.toEqual({
      message: "Missing reading record id.",
    });
    expect(createUpstreamReadingRecordAskStream).not.toHaveBeenCalled();
  });

  it("streams RR asks through the RR upstream API when scope is reading_record", async () => {
    vi.mocked(createUpstreamReadingRecordAskStream).mockResolvedValue(
      new Response("event: ready\ndata: {}\n\n", {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      }),
    );

    const result = await createReaderAskStreamForWeb(
      "thread-rr-1",
      {
        content: "Explain this paragraph",
        entry_action: "ask_about_this",
        page_identity: {
          record_id: "reading-record-1",
          title: "Reading Record",
          surface: "reader",
          source: "reader_2_0",
          available_context_capabilities: [],
          has_article_overview: false,
          has_sentence_entries: true,
          has_annotations: false,
          has_reader_notes: false,
        },
        attachments: [],
      },
      "reading-record-1",
      "reading_record",
    );

    expect(result.status).toBe(200);
    expect(createUpstreamReadingRecordAskStream).toHaveBeenCalledWith(
      "reading-record-1",
      "thread-rr-1",
      expect.objectContaining({
        content: "Explain this paragraph",
      }),
      "session-token",
    );
  });
});
