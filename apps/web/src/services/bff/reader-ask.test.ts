import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

vi.mock("@/services/bff/session", () => ({
  getWebSession: vi.fn(),
}));

vi.mock("@/services/api/reader-ask", () => ({
  createUpstreamReadingRecordAskDefaultThread: vi.fn(),
  createUpstreamReadingRecordAskStream: vi.fn(),
  getUpstreamReadingRecordAskThread: vi.fn(),
  listUpstreamReadingRecordAskThreads: vi.fn(),
  resetUpstreamReadingRecordAskThread: vi.fn(),
  retryUpstreamReadingRecordAskMessage: vi.fn(),
}));

import { getWebSession } from "@/services/bff/session";
import {
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

    const result = await createReaderAskThreadForWeb("reading-record-1", {
      record_id: "reading-record-1",
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
  });

  it("rejects RR stream requests that do not carry a reading record id", async () => {
    const result = await createReaderAskStreamForWeb(
      "",
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
      "reading-record-1",
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
    );

    expect(result.status).toBe(200);
    // signal is the new 5th arg; undefined when
    // the caller (e.g. a server-to-server test) does not supply one.
    expect(createUpstreamReadingRecordAskStream).toHaveBeenCalledWith(
      "reading-record-1",
      "thread-rr-1",
      expect.objectContaining({
        content: "Explain this paragraph",
      }),
      "session-token",
      undefined,
    );
  });

  it("forwards the browser AbortSignal to the upstream fetch", async () => {
    // a user stop / network abort / page navigation
    // must cancel the upstream SSE connection too, so the FastAPI
    // generator's ``finally`` block fires and reconciles any still-
    // streaming turn_run / message row to ``cancelled``.
    vi.mocked(createUpstreamReadingRecordAskStream).mockResolvedValue(
      new Response("event: ready\ndata: {}\n\n", {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      }),
    );

    const controller = new AbortController();
    await createReaderAskStreamForWeb(
      "reading-record-1",
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
      controller.signal,
    );

    expect(createUpstreamReadingRecordAskStream).toHaveBeenCalledWith(
      "reading-record-1",
      "thread-rr-1",
      expect.objectContaining({ content: "Explain this paragraph" }),
      "session-token",
      controller.signal,
    );
  });
});
