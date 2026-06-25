import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

import { createUpstreamReadingRecordAskStream } from "./reader-ask";

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
});
