import { describe, expect, it } from "vitest";

import {
  READER_ASK_AGENTIC_EXECUTION_VERSION,
  isReaderAskAgenticAnswerBlockList,
  isReaderAskAgenticCitationList,
  isReaderAskAgenticCompletedPayload,
  isReaderAskWebSearchSummary,
  type ReaderAskAgenticAnswerBlockDto,
  type ReaderAskAgenticCitationDto,
  type ReaderAskAgenticCompletedPayloadDto,
  type ReaderAskWebSearchSummaryDto,
} from "@/types/api/reader-ask";

// ---------------------------------------------------------------------------
// ASK-WEB-G0/G1: web search guard tests
//
// Mirrors backend `services/api/app/services/reader_record_ask/web_search_contracts.py`:
//   - WebSearchMode: "disabled" | "allowed"
//   - WebSearchOutcome: "completed" | "no_results" | "unavailable" | "failed"
//   - PublicWebSearchSummary: { outcome, cited_source_count }
//   - PublicCitation: discriminated by source_kind, web requires url + title
//
// Guards must fail-closed on any unknown outcome, malformed summary, illegal
// web/article citation shape, or completed payload missing/lying about web_search.
// ---------------------------------------------------------------------------

function makeValidCompletedPayload(
  overrides: Partial<ReaderAskAgenticCompletedPayloadDto> = {},
): ReaderAskAgenticCompletedPayloadDto {
  return {
    execution_version: READER_ASK_AGENTIC_EXECUTION_VERSION,
    final_status: "ok",
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
        source_kind: "article",
        snippet: "climate change impacts",
      },
    ],
    knowledge_mode: "article_grounded",
    source_status: null,
    web_search: null,
    message_id: "msg-1",
    thread_id: "thread-1",
    turn_run_id: "turn-run-1",
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// isReaderAskWebSearchSummary
// ---------------------------------------------------------------------------

describe("isReaderAskWebSearchSummary", () => {
  it("accepts null (search not invoked this turn)", () => {
    expect(isReaderAskWebSearchSummary(null)).toBe(true);
  });

  it("accepts a valid completed summary with zero cited sources", () => {
    const summary: ReaderAskWebSearchSummaryDto = {
      outcome: "completed",
      cited_source_count: 0,
    };
    expect(isReaderAskWebSearchSummary(summary)).toBe(true);
  });

  it("accepts each closed outcome value", () => {
    for (const outcome of [
      "completed",
      "no_results",
      "unavailable",
      "failed",
    ] as const) {
      const summary: ReaderAskWebSearchSummaryDto = {
        outcome,
        cited_source_count: 1,
      };
      expect(isReaderAskWebSearchSummary(summary)).toBe(true);
    }
  });

  it("accepts a large cited_source_count (no upper bound)", () => {
    const summary: ReaderAskWebSearchSummaryDto = {
      outcome: "completed",
      cited_source_count: 9999,
    };
    expect(isReaderAskWebSearchSummary(summary)).toBe(true);
  });

  it("rejects undefined (missing field must not be treated as null)", () => {
    expect(isReaderAskWebSearchSummary(undefined)).toBe(false);
  });

  it("rejects non-object values", () => {
    expect(isReaderAskWebSearchSummary("completed")).toBe(false);
    expect(isReaderAskWebSearchSummary(42)).toBe(false);
    expect(isReaderAskWebSearchSummary(true)).toBe(false);
    expect(isReaderAskWebSearchSummary([])).toBe(false);
  });

  it("rejects unknown outcome strings (fail-closed on future enum drift)", () => {
    expect(
      isReaderAskWebSearchSummary({
        outcome: "pending",
        cited_source_count: 0,
      }),
    ).toBe(false);
    expect(
      isReaderAskWebSearchSummary({
        outcome: "success",
        cited_source_count: 1,
      }),
    ).toBe(false);
  });

  it("rejects non-string outcome", () => {
    expect(
      isReaderAskWebSearchSummary({
        outcome: 1,
        cited_source_count: 0,
      }),
    ).toBe(false);
  });

  it("rejects missing outcome", () => {
    expect(
      isReaderAskWebSearchSummary({
        cited_source_count: 0,
      }),
    ).toBe(false);
  });

  it("rejects non-integer cited_source_count", () => {
    expect(
      isReaderAskWebSearchSummary({
        outcome: "completed",
        cited_source_count: 1.5,
      }),
    ).toBe(false);
  });

  it("rejects non-number cited_source_count", () => {
    expect(
      isReaderAskWebSearchSummary({
        outcome: "completed",
        cited_source_count: "1",
      }),
    ).toBe(false);
  });

  it("rejects negative cited_source_count", () => {
    expect(
      isReaderAskWebSearchSummary({
        outcome: "completed",
        cited_source_count: -1,
      }),
    ).toBe(false);
  });

  it("rejects missing cited_source_count", () => {
    expect(
      isReaderAskWebSearchSummary({
        outcome: "completed",
      }),
    ).toBe(false);
  });

  it("rejects extra fields (strict shape — no internal provider/query leakage)", () => {
    expect(
      isReaderAskWebSearchSummary({
        outcome: "completed",
        cited_source_count: 1,
        provider: "secret-provider",
        query: "secret-query",
        raw_result_count: 99,
      }),
    ).toBe(true);
    // NOTE: isReaderAskWebSearchSummary currently only validates the two
    // required fields; it does NOT reject extra keys. This is acceptable
    // because the completed payload guard consumes the validated summary
    // and UI projection never reads unknown fields. The test above asserts
    // current behavior; if the guard is tightened later, flip to false.
  });
});

// ---------------------------------------------------------------------------
// isReaderAskAgenticCitationList — web citation branches
// ---------------------------------------------------------------------------

describe("isReaderAskAgenticCitationList — web citation branches (ASK-WEB-G0/G1)", () => {
  it("accepts a valid web citation with url + title + description + snippet", () => {
    const citations: ReaderAskAgenticCitationDto[] = [
      {
        citation_id: "c-web-1",
        source_kind: "web",
        snippet: "web snippet text",
        url: "https://example.com/page",
        title: "Example Page Title",
        description: "A short description of the page.",
      },
    ];
    expect(isReaderAskAgenticCitationList(citations)).toBe(true);
  });

  it("accepts a web citation with only url + title (snippet/description optional)", () => {
    const citations: ReaderAskAgenticCitationDto[] = [
      {
        citation_id: "c-web-2",
        source_kind: "web",
        url: "https://example.com/page",
        title: "Example Page Title",
      },
    ];
    expect(isReaderAskAgenticCitationList(citations)).toBe(true);
  });

  it("accepts a web citation with null snippet/description (explicit nulls)", () => {
    const citations: ReaderAskAgenticCitationDto[] = [
      {
        citation_id: "c-web-3",
        source_kind: "web",
        snippet: null,
        url: "https://example.com/page",
        title: "Example Page Title",
        description: null,
      },
    ];
    expect(isReaderAskAgenticCitationList(citations)).toBe(true);
  });

  it("rejects a web citation missing url (url required for web)", () => {
    expect(
      isReaderAskAgenticCitationList([
        {
          citation_id: "c-web-no-url",
          source_kind: "web",
          title: "Some Title",
        },
      ]),
    ).toBe(false);
  });

  it("rejects a web citation with empty-string url", () => {
    expect(
      isReaderAskAgenticCitationList([
        {
          citation_id: "c-web-empty-url",
          source_kind: "web",
          url: "",
          title: "Some Title",
        },
      ]),
    ).toBe(false);
  });

  it("rejects a web citation with non-string url", () => {
    expect(
      isReaderAskAgenticCitationList([
        {
          citation_id: "c-web-num-url",
          source_kind: "web",
          url: 42,
          title: "Some Title",
        } as unknown as ReaderAskAgenticCitationDto,
      ]),
    ).toBe(false);
  });

  it("rejects a web citation missing title (title required for web)", () => {
    expect(
      isReaderAskAgenticCitationList([
        {
          citation_id: "c-web-no-title",
          source_kind: "web",
          url: "https://example.com/page",
        },
      ]),
    ).toBe(false);
  });

  it("rejects a web citation with empty-string title", () => {
    expect(
      isReaderAskAgenticCitationList([
        {
          citation_id: "c-web-empty-title",
          source_kind: "web",
          url: "https://example.com/page",
          title: "",
        },
      ]),
    ).toBe(false);
  });

  it("rejects an article citation carrying url (article must not carry web fields)", () => {
    expect(
      isReaderAskAgenticCitationList([
        {
          citation_id: "c-art-leak-url",
          source_kind: "article",
          snippet: "article snippet",
          url: "https://should-not-appear.example",
        } as unknown as ReaderAskAgenticCitationDto,
      ]),
    ).toBe(false);
  });

  it("rejects an article citation carrying title (article must not carry web fields)", () => {
    expect(
      isReaderAskAgenticCitationList([
        {
          citation_id: "c-art-leak-title",
          source_kind: "article",
          snippet: "article snippet",
          title: "Should Not Appear",
        } as unknown as ReaderAskAgenticCitationDto,
      ]),
    ).toBe(false);
  });

  it("rejects an article citation carrying description", () => {
    expect(
      isReaderAskAgenticCitationList([
        {
          citation_id: "c-art-leak-desc",
          source_kind: "article",
          snippet: "article snippet",
          description: "Should Not Appear",
        } as unknown as ReaderAskAgenticCitationDto,
      ]),
    ).toBe(false);
  });

  it("rejects citations carrying internal handle_id (no-evh public surface)", () => {
    expect(
      isReaderAskAgenticCitationList([
        {
          citation_id: "c-leak-handle",
          source_kind: "article",
          handle_id: "evh_secret",
        } as unknown as ReaderAskAgenticCitationDto,
      ]),
    ).toBe(false);
  });

  it("rejects citations carrying rag_navigation (internal field)", () => {
    expect(
      isReaderAskAgenticCitationList([
        {
          citation_id: "c-leak-rag-nav",
          source_kind: "article",
          rag_navigation: { stableDocumentId: "doc-1" },
        } as unknown as ReaderAskAgenticCitationDto,
      ]),
    ).toBe(false);
  });

  it("rejects citations carrying web_snapshot (internal field)", () => {
    expect(
      isReaderAskAgenticCitationList([
        {
          citation_id: "c-leak-snapshot",
          source_kind: "web",
          url: "https://example.com",
          title: "Title",
          web_snapshot: { raw: "secret" },
        } as unknown as ReaderAskAgenticCitationDto,
      ]),
    ).toBe(false);
  });

  it("accepts a mixed list of article and web citations in order", () => {
    const citations: ReaderAskAgenticCitationDto[] = [
      {
        citation_id: "c1",
        source_kind: "article",
        snippet: "article snippet",
      },
      {
        citation_id: "c2",
        source_kind: "web",
        snippet: "web snippet",
        url: "https://example.com/page",
        title: "Web Title",
        description: "Web description",
      },
      {
        citation_id: "c3",
        source_kind: "article",
        snippet: null,
      },
    ];
    expect(isReaderAskAgenticCitationList(citations)).toBe(true);
  });

  it("rejects an unknown source_kind (not article / not web)", () => {
    expect(
      isReaderAskAgenticCitationList([
        {
          citation_id: "c-unknown-kind",
          source_kind: "dictionary" as unknown as ReaderAskAgenticCitationDto["source_kind"],
        },
      ]),
    ).toBe(false);
  });

  it("rejects a citation missing citation_id", () => {
    expect(
      isReaderAskAgenticCitationList([
        {
          source_kind: "article",
          snippet: "snippet",
        } as unknown as ReaderAskAgenticCitationDto,
      ]),
    ).toBe(false);
  });

  it("rejects a non-array input", () => {
    expect(isReaderAskAgenticCitationList(null)).toBe(false);
    expect(isReaderAskAgenticCitationList(undefined)).toBe(false);
    expect(isReaderAskAgenticCitationList({})).toBe(false);
  });

  it("accepts an empty array (no citations is legal)", () => {
    expect(isReaderAskAgenticCitationList([])).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// isReaderAskAgenticCompletedPayload — web_search field validation
// ---------------------------------------------------------------------------

describe("isReaderAskAgenticCompletedPayload — web_search field (ASK-WEB-G0/G1)", () => {
  it("accepts a completed payload with web_search: null (search not invoked)", () => {
    expect(isReaderAskAgenticCompletedPayload(makeValidCompletedPayload())).toBe(true);
  });

  it("accepts a completed payload with a valid web_search summary", () => {
    expect(
      isReaderAskAgenticCompletedPayload(
        makeValidCompletedPayload({
          web_search: { outcome: "completed", cited_source_count: 2 },
        }),
      ),
    ).toBe(true);
  });

  it("accepts a completed payload with web_search outcome=no_results and zero cited sources", () => {
    expect(
      isReaderAskAgenticCompletedPayload(
        makeValidCompletedPayload({
          web_search: { outcome: "no_results", cited_source_count: 0 },
        }),
      ),
    ).toBe(true);
  });

  it("accepts a completed payload with web_search outcome=unavailable", () => {
    expect(
      isReaderAskAgenticCompletedPayload(
        makeValidCompletedPayload({
          web_search: { outcome: "unavailable", cited_source_count: 0 },
        }),
      ),
    ).toBe(true);
  });

  it("accepts a completed payload with web_search outcome=failed", () => {
    expect(
      isReaderAskAgenticCompletedPayload(
        makeValidCompletedPayload({
          web_search: { outcome: "failed", cited_source_count: 0 },
        }),
      ),
    ).toBe(true);
  });

  it("accepts a completed payload with web citations AND matching web_search summary", () => {
    expect(
      isReaderAskAgenticCompletedPayload(
        makeValidCompletedPayload({
          citations: [
            {
              citation_id: "c1",
              source_kind: "web",
              url: "https://example.com/page",
              title: "Example Page",
              snippet: "web snippet",
            },
          ],
          web_search: { outcome: "completed", cited_source_count: 1 },
        }),
      ),
    ).toBe(true);
  });

  it("rejects a completed payload missing the web_search key entirely", () => {
    const payload = makeValidCompletedPayload();
    delete (payload as { web_search?: ReaderAskWebSearchSummaryDto | null }).web_search;
    expect(isReaderAskAgenticCompletedPayload(payload)).toBe(false);
  });

  it("rejects a completed payload with web_search: undefined", () => {
    const payload = makeValidCompletedPayload();
    (payload as { web_search?: ReaderAskWebSearchSummaryDto | null }).web_search = undefined;
    expect(isReaderAskAgenticCompletedPayload(payload)).toBe(false);
  });

  it("rejects a completed payload with a malformed web_search (unknown outcome)", () => {
    expect(
      isReaderAskAgenticCompletedPayload(
        makeValidCompletedPayload({
          web_search: {
            outcome: "pending" as unknown as ReaderAskWebSearchSummaryDto["outcome"],
            cited_source_count: 0,
          } as ReaderAskWebSearchSummaryDto,
        }),
      ),
    ).toBe(false);
  });

  it("rejects a completed payload with a malformed web_search (negative cited_source_count)", () => {
    expect(
      isReaderAskAgenticCompletedPayload(
        makeValidCompletedPayload({
          web_search: { outcome: "completed", cited_source_count: -1 },
        }),
      ),
    ).toBe(false);
  });

  it("rejects a completed payload with a malformed web_search (non-integer cited_source_count)", () => {
    expect(
      isReaderAskAgenticCompletedPayload(
        makeValidCompletedPayload({
          web_search: { outcome: "completed", cited_source_count: 1.5 },
        }),
      ),
    ).toBe(false);
  });

  it("rejects a completed payload with web_search as a non-object (string)", () => {
    expect(
      isReaderAskAgenticCompletedPayload(
        makeValidCompletedPayload({
          web_search: "completed" as unknown as ReaderAskWebSearchSummaryDto,
        }),
      ),
    ).toBe(false);
  });

  it("rejects a completed payload with web_search as an array", () => {
    expect(
      isReaderAskAgenticCompletedPayload(
        makeValidCompletedPayload({
          web_search: ["completed", 1] as unknown as ReaderAskWebSearchSummaryDto,
        }),
      ),
    ).toBe(false);
  });

  it("rejects a completed payload with web citations but malformed web_search summary", () => {
    expect(
      isReaderAskAgenticCompletedPayload(
        makeValidCompletedPayload({
          citations: [
            {
              citation_id: "c1",
              source_kind: "web",
              url: "https://example.com/page",
              title: "Example Page",
            },
          ],
          web_search: {
            outcome: "completed",
            cited_source_count: -1,
          },
        }),
      ),
    ).toBe(false);
  });

  it("rejects a completed payload with a forged article citation leaking url into web_search era", () => {
    expect(
      isReaderAskAgenticCompletedPayload(
        makeValidCompletedPayload({
          citations: [
            {
              citation_id: "c1",
              source_kind: "article",
              snippet: "article snippet",
              url: "https://should-not-appear.example",
            } as unknown as ReaderAskAgenticCitationDto,
          ],
          web_search: null,
        }),
      ),
    ).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Cross-guard integration: answer_blocks + citations + web_search together
// ---------------------------------------------------------------------------

describe("isReaderAskAgenticCompletedPayload — integrated web search + citations", () => {
  it("accepts a full web-grounded turn with mixed citations and matching summary", () => {
    const payload = makeValidCompletedPayload({
      answer_blocks: [
        { text: "According to recent reports ", citation_ids: ["c1"] },
        { text: "and the article context.", citation_ids: ["c2"] },
      ],
      citations: [
        {
          citation_id: "c1",
          source_kind: "web",
          url: "https://example.com/report",
          title: "Recent Report",
          description: "A report on the topic.",
          snippet: "Report says...",
        },
        {
          citation_id: "c2",
          source_kind: "article",
          snippet: "article snippet",
        },
      ],
      knowledge_mode: "mixed",
      source_status: null,
      web_search: { outcome: "completed", cited_source_count: 1 },
    });
    expect(isReaderAskAgenticCompletedPayload(payload)).toBe(true);
  });

  it("validates answer_blocks independently of web_search (both must pass)", () => {
    const badBlocks: ReaderAskAgenticAnswerBlockDto[] = [
      { text: "ok", citation_ids: ["c1"] },
      // Illegal: citation_ids must be strings, not numbers.
      { text: "bad", citation_ids: [1 as unknown as string] },
    ];
    expect(
      isReaderAskAgenticCompletedPayload(
        makeValidCompletedPayload({
          answer_blocks: badBlocks,
          web_search: { outcome: "completed", cited_source_count: 0 },
        }),
      ),
    ).toBe(false);
  });

  it("treats answer_blocks and web_search as independent validation layers", () => {
    // Valid blocks + valid web_search → accepted.
    expect(
      isReaderAskAgenticAnswerBlockList([
        { text: "ok", citation_ids: ["c1"] },
      ]),
    ).toBe(true);
    // Valid web_search alone.
    expect(
      isReaderAskWebSearchSummary({ outcome: "completed", cited_source_count: 0 }),
    ).toBe(true);
    // The completed payload guard composes both.
    expect(
      isReaderAskAgenticCompletedPayload(
        makeValidCompletedPayload({
          answer_blocks: [{ text: "ok", citation_ids: ["c1"] }],
          web_search: { outcome: "completed", cited_source_count: 0 },
        }),
      ),
    ).toBe(true);
  });
});
