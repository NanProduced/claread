import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

import {
  createUpstreamReaderAskStream,
  createUpstreamReadingRecordAskStream,
  navigateUpstreamReadingRecordAskCitation,
} from "./reader-ask";

describe("reader-ask API transport", () => {
  beforeEach(() => {
    process.env.CLAREAD_FASTAPI_BASE_URL = "http://api.example.test";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response("event: ready\ndata: {}\n\n", {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        }),
      ),
    );
  });

  afterEach(() => {
    delete process.env.CLAREAD_FASTAPI_BASE_URL;
    vi.unstubAllGlobals();
  });

  it("posts citation navigate without body or fence identity fields", async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          status: "ok",
          location: { unit_id: "u1", anchor_segment_id: "s1" },
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      ),
    );

    const result = await navigateUpstreamReadingRecordAskCitation(
      "reading-record-1",
      "msg-1",
      "c1",
      "session-token",
    );
    expect(result.ok).toBe(true);

    expect(global.fetch).toHaveBeenCalledTimes(1);
    const [url, init] = vi.mocked(global.fetch).mock.calls[0] ?? [];
    expect(String(url)).toBe(
      "http://api.example.test/reader/records/reading-record-1/ask/messages/msg-1/citations/c1/navigate",
    );
    expect(init?.method).toBe("POST");
    // No request body — client cannot smuggle fence fields.
    expect(init?.body).toBeUndefined();
    const headers = init?.headers as Headers | Record<string, string> | undefined;
    // sessionToken is applied by fastApiFetch
    const auth =
      headers instanceof Headers
        ? headers.get("authorization")
        : headers && "authorization" in headers
          ? headers.authorization
          : undefined;
    // fastApiFetch sets authorization via sessionToken option
    expect(String(url)).not.toContain("base_id");
    expect(String(url)).not.toContain("record_generation");
    expect(String(url)).not.toContain("stable_document");
    void auth;
  });

  it("projects the RR ask request to content/model/entry_action plus a single RR anchor", async () => {
    await createUpstreamReadingRecordAskStream(
      "reading-record-1",
      "thread-1",
      {
        content: "Explain this selection",
        entry_action: "explain_this",
        model: "ask-fast",
        page_identity: {
          record_id: "reading-record-1",
          title: "Reading Record",
          surface: "reader",
          source: "reader_2_0",
          available_context_capabilities: ["record_context"],
          has_article_overview: false,
          has_sentence_entries: true,
          has_annotations: true,
          has_reader_notes: false,
        },
        attachments: [
          {
            kind: "text_selection",
            subtype: "text_range",
            label: "memory",
            selected_text: "memory",
            target_key: "record:reading-record-1:range:sent-1",
            metadata: {
              source_surface: "selection_toolbar",
              reading_record_anchor: {
                record_id: "reading-record-1",
                base_id: "base-1",
                generation: 2,
                unit_id: "unit-1",
                anchor_segment_id: "seg-1",
                scope: "stable_source",
                offset_unit: "utf16",
                start_offset: 0,
                end_offset: 6,
                selected_text: "memory",
                text_hash: "9fd7545a",
                hash_algorithm: "fnv1a32-utf16",
              },
            },
          },
        ],
      },
      "session-token",
    );

    expect(global.fetch).toHaveBeenCalledTimes(1);
    const [url, init] = vi.mocked(global.fetch).mock.calls[0] ?? [];
    expect(String(url)).toBe(
      "http://api.example.test/reader/records/reading-record-1/ask/threads/thread-1/messages/stream",
    );
    expect(init?.headers).toMatchObject({
      accept: "text/event-stream",
      authorization: "Bearer session-token",
      "content-type": "application/json",
    });

    const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    expect(body).toEqual({
      content: "Explain this selection",
      entry_action: "explain_this",
      model: "ask-fast",
      // ASK-WEB-G1-R2: reader-ask transport always forwards a typed
      // web_search_mode so the host can decide capability without
      // guessing. Default is "disabled" when the caller omits it.
      web_search_mode: "disabled",
      anchor: {
        record_id: "reading-record-1",
        base_id: "base-1",
        generation: 2,
        unit_id: "unit-1",
        anchor_segment_id: "seg-1",
        scope: "stable_source",
        offset_unit: "utf16",
        start_offset: 0,
        end_offset: 6,
        selected_text: "memory",
        text_hash: "9fd7545a",
        hash_algorithm: "fnv1a32-utf16",
      },
    });
  });

  it("strips BFF-only and stale metadata before forwarding generic Reader Ask", async () => {
    await createUpstreamReaderAskStream(
      "thread-1",
      {
        content: "Explain this",
        entry_action: "ask_about_this",
        model: "ask-fast",
        page_identity: {
          record_id: "reader-record-1",
          title: "Reading Record",
          surface: "reader",
          source: "reader_2_0",
          available_context_capabilities: ["record_context"],
          has_article_overview: false,
          has_sentence_entries: true,
          has_annotations: true,
          has_reader_notes: false,
        },
        attachments: [
          {
            kind: "text_selection",
            subtype: "text_range",
            label: "memory",
            selected_text: "memory",
            target_key: "record:reader-record-1:range:sent-1",
            anchor_payload: {
              anchor_type: "text_range",
              target_key: "record:reader-record-1:range:sent-1",
              record_id: "reader-record-1",
              paragraph_id: "unit-1",
              sentence_id: "sent-1",
              selected_text: "memory",
              start_offset: 0,
              end_offset: 6,
              text_hash: "9fd7545a",
              segments: [],
            },
            metadata: {
              source_surface: "selection_toolbar",
              entry_action: "ask_about_this",
              reading_record_anchor: { record_id: "reader-record-1" },
              surface_kind: "source",
              block_type: "reader_paragraph",
              block_id: "paragraph:seg-1",
              anchor_segment_id: "seg-1",
              unit_id: "unit-1",
              layer_id: "layer-1",
              analysis_id: "analysis-1",
              source_context: { sourceText: "memory" },
              chunks: [{ order: 1, label: "subject", text: "memory" }],
            } as never,
          },
        ],
      },
      "session-token",
    );

    expect(global.fetch).toHaveBeenCalledTimes(1);
    const [url, init] = vi.mocked(global.fetch).mock.calls[0] ?? [];
    expect(String(url)).toBe(
      "http://api.example.test/reader-ask/threads/thread-1/messages/stream",
    );
    const body = JSON.parse(String(init?.body)) as {
      attachments: Array<{ metadata: Record<string, unknown> }>;
    };
    expect(body.attachments[0]?.metadata).toEqual({
      source_surface: "selection_toolbar",
      entry_action: "ask_about_this",
      record_id: null,
      record_title: null,
      sentence_id: null,
      paragraph_id: null,
      entry_id: null,
      entry_type: null,
      asset_id: null,
      annotation_type: null,
      start_offset: null,
      end_offset: null,
      translation_zh: null,
      note: null,
      title: null,
      query: null,
      lookup_text: null,
      visual_tone: null,
    });
  });
});
