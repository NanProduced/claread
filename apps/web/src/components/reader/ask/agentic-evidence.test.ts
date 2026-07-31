import { describe, expect, it } from "vitest";
import {
  isReaderAskAgenticEvidenceItem,
  type ReaderAskAgenticCitationDto,
  type ReaderAskAgenticEvidenceItemDto,
} from "@/types/api/reader-ask";
import {
  projectAgenticCitationsForDisplay,
  projectAgenticEvidenceForDisplay,
  type AgenticCitationDisplayItem,
  type AgenticEvidenceDisplayItem,
} from "./agentic-evidence";

const COMPLETE_RAG_CITATION = {
  rag_substrate_id: "substrate-1",
  index_run_id: "index-run-1",
  index_version: "v1",
  plan_content_sha256: "plan-sha-abc",
  source_scope: "main_reading_text" as const,
  block_type: "paragraph",
  chunk_id: "chunk-1",
  content_sha256: "content-sha-def",
  canonical_text_start_utf16: 10,
  canonical_text_end_utf16: 42,
  snippet: "climate change impacts",
  score: 0.91,
  stable_document_id: "doc-stable-1",
  base_id: "base-1",
  record_generation: 1,
  block_ids: ["b1"],
  unit_ids: ["u1"],
  anchor_segment_ids: ["s1"],
};

function freezeSnapshot(items: AgenticEvidenceDisplayItem[]) {
  return JSON.parse(JSON.stringify(items)) as unknown;
}

describe("projectAgenticEvidenceForDisplay", () => {
  it("projects initial_anchor, read_range, and observation with stable titles and snippets", () => {
    const input: ReaderAskAgenticEvidenceItemDto[] = [
      {
        handle_id: "evh_anchor",
        kind: "initial_anchor",
        source_tool: "initial_anchor",
        snippet: "selected sentence",
        unit_id: "u1",
        anchor_segment_id: "s1",
      },
      {
        handle_id: "evh_range",
        kind: "read_range",
        source_tool: "read_range",
        snippet: "paragraph body",
        unit_id: "u2",
      },
      {
        handle_id: "evh_obs",
        kind: "observation",
        source_tool: "initial_anchor",
        snippet: null,
      },
    ];

    const projected = projectAgenticEvidenceForDisplay(input);

    expect(projected).toHaveLength(3);
    expect(projected[0]).toEqual({
      handleId: "evh_anchor",
      kind: "initial_anchor",
      title: "初始选区",
      snippet: "selected sentence",
      sourceTool: "initial_anchor",
      ragNavigation: null,
    });
    expect(projected[1]).toEqual({
      handleId: "evh_range",
      kind: "read_range",
      title: "阅读范围",
      snippet: "paragraph body",
      sourceTool: "read_range",
      ragNavigation: null,
    });
    expect(projected[2]).toEqual({
      handleId: "evh_obs",
      kind: "observation",
      title: "观察结果",
      snippet: "",
      sourceTool: "initial_anchor",
      ragNavigation: null,
    });
  });

  it("projects search_hit ragNavigation only when rag_citation is complete", () => {
    const input: ReaderAskAgenticEvidenceItemDto[] = [
      {
        handle_id: "evh_search",
        kind: "search_hit",
        source_tool: "search_current_article",
        snippet: "climate change impacts",
        unit_id: "u1",
        anchor_segment_id: "s1",
        rag_citation: COMPLETE_RAG_CITATION,
      },
    ];

    const projected = projectAgenticEvidenceForDisplay(input);

    expect(projected).toHaveLength(1);
    expect(projected[0].kind).toBe("search_hit");
    expect(projected[0].title).toBe("文章检索");
    expect(projected[0].snippet).toBe("climate change impacts");
    expect(projected[0].ragNavigation).toEqual({
      stableDocumentId: "doc-stable-1",
      baseId: "base-1",
      recordGeneration: 1,
      unitIds: ["u1"],
      anchorSegmentIds: ["s1"],
      canonicalTextStartUtf16: 10,
      canonicalTextEndUtf16: 42,
    });
  });

  it("keeps search_hit displayable when rag_citation is missing, with null navigation (defensive projection test; guard rejects this in production)", () => {
    // NOTE: With the R4-A1 strict guard, search_hit without rag_citation is
    // illegal and would be rejected by isReaderAskAgenticEvidenceItem before
    // reaching the projection. This test verifies the projection function's
    // defensive behavior when called directly with incomplete data.
    const input: ReaderAskAgenticEvidenceItemDto[] = [
      {
        handle_id: "evh_search_bare",
        kind: "search_hit",
        source_tool: "search_current_article",
        snippet: "hit without citation",
      },
    ];

    const projected = projectAgenticEvidenceForDisplay(input);

    expect(projected).toHaveLength(1);
    expect(projected[0]).toEqual({
      handleId: "evh_search_bare",
      kind: "search_hit",
      title: "文章检索",
      snippet: "hit without citation",
      sourceTool: "search_current_article",
      ragNavigation: null,
    });
  });

  it("does not invent navigation from partial rag_citation", () => {
    const input: ReaderAskAgenticEvidenceItemDto[] = [
      {
        handle_id: "evh_search_partial",
        kind: "search_hit",
        source_tool: "search_current_article",
        snippet: "partial citation",
        rag_citation: {
          ...COMPLETE_RAG_CITATION,
          // Drop identity required for navigation.
          stable_document_id: "",
        },
      },
    ];

    const projected = projectAgenticEvidenceForDisplay(input);
    expect(projected[0].ragNavigation).toBeNull();
  });


  it("rejects negative, fractional, and reversed UTF-16 navigation ranges", () => {
    const invalidRanges = [
      { start: -1, end: 5 },
      { start: 1.5, end: 5 },
      { start: 8, end: 7 },
      { start: 0, end: 2.5 },
    ];

    for (const range of invalidRanges) {
      const projected = projectAgenticEvidenceForDisplay([
        {
          handle_id: "evh_invalid_range",
          kind: "search_hit",
          source_tool: "search_current_article",
          snippet: "invalid range",
          rag_citation: {
            ...COMPLETE_RAG_CITATION,
            canonical_text_start_utf16: range.start,
            canonical_text_end_utf16: range.end,
          },
        },
      ]);

      expect(projected[0].ragNavigation).toBeNull();
    }
  });
  it("omits internal/debug fields from the projected output", () => {
    const input: ReaderAskAgenticEvidenceItemDto[] = [
      {
        handle_id: "evh_search",
        kind: "search_hit",
        source_tool: "search_current_article",
        snippet: "climate change impacts",
        rag_citation: COMPLETE_RAG_CITATION,
      },
    ];

    const projected = projectAgenticEvidenceForDisplay(input);
    const serialized = JSON.stringify(projected);

    expect(serialized).not.toContain("rag_substrate_id");
    expect(serialized).not.toContain("substrate-1");
    expect(serialized).not.toContain("index_run_id");
    expect(serialized).not.toContain("index-run-1");
    expect(serialized).not.toContain("plan_content_sha256");
    expect(serialized).not.toContain("plan-sha-abc");
    expect(serialized).not.toContain("content_sha256");
    expect(serialized).not.toContain("content-sha-def");
    expect(serialized).not.toContain("\"score\"");
    expect(serialized).not.toContain("0.91");
    expect(serialized).not.toContain("index_version");
    expect(serialized).not.toContain("source_scope");
    expect(serialized).not.toContain("block_type");
    expect(serialized).not.toContain("chunk_id");

    // Navigation is present but only with allowlisted fields.
    expect(projected[0].ragNavigation).not.toBeNull();
    expect(Object.keys(projected[0].ragNavigation!).sort()).toEqual(
      [
        "anchorSegmentIds",
        "baseId",
        "canonicalTextEndUtf16",
        "canonicalTextStartUtf16",
        "recordGeneration",
        "stableDocumentId",
        "unitIds",
      ].sort(),
    );
  });

  it("returns an empty array for empty input and preserves multi-item order", () => {
    expect(projectAgenticEvidenceForDisplay([])).toEqual([]);

    const input: ReaderAskAgenticEvidenceItemDto[] = [
      {
        handle_id: "evh_1",
        kind: "initial_anchor",
        source_tool: "initial_anchor",
        snippet: "first",
      },
      {
        handle_id: "evh_2",
        kind: "search_hit",
        source_tool: "search_current_article",
        snippet: "second",
        rag_citation: COMPLETE_RAG_CITATION,
      },
      {
        handle_id: "evh_3",
        kind: "read_range",
        source_tool: "read_range",
        snippet: "third",
      },
    ];

    const projected = projectAgenticEvidenceForDisplay(input);
    expect(projected.map((item) => item.handleId)).toEqual([
      "evh_1",
      "evh_2",
      "evh_3",
    ]);
    expect(projected.map((item) => item.kind)).toEqual([
      "initial_anchor",
      "search_hit",
      "read_range",
    ]);
  });

  it("does not mutate the input DTO", () => {
    const input: ReaderAskAgenticEvidenceItemDto[] = [
      {
        handle_id: "evh_search",
        kind: "search_hit",
        source_tool: "search_current_article",
        snippet: "climate change impacts",
        unit_id: "u1",
        rag_citation: {
          ...COMPLETE_RAG_CITATION,
          unit_ids: ["u1"],
          anchor_segment_ids: ["s1"],
        },
      },
    ];
    const before = freezeSnapshot(input as unknown as AgenticEvidenceDisplayItem[]);

    const projected = projectAgenticEvidenceForDisplay(input);

    // Mutating the projected navigation arrays must not touch input.
    projected[0].ragNavigation?.unitIds.push("mutated");
    projected[0].ragNavigation?.anchorSegmentIds.push("mutated");

    expect(freezeSnapshot(input as unknown as AgenticEvidenceDisplayItem[])).toEqual(
      before,
    );
    expect(input[0].rag_citation?.unit_ids).toEqual(["u1"]);
    expect(input[0].rag_citation?.anchor_segment_ids).toEqual(["s1"]);
    expect(input[0].snippet).toBe("climate change impacts");
  });
});

// ---------------------------------------------------------------------------
// R4-A1: article_seed evidence projection
//
// article_seed is the baseline article context handle. It carries the article
// text snippet (≤ 2000 chars) with provenance `baseline_context`. The full
// article text lives only in model-visible context chunks and must NOT appear
// in the public DTO or the projection. The projection must:
//   - use a concise Chinese title ("文章原文")
//   - keep snippet read-only
//   - never expose source_tool / fingerprint / hash / stable/base ids to UI
//   - have null ragNavigation (article_seed is non-RAG)
// ---------------------------------------------------------------------------

describe("projectAgenticEvidenceForDisplay — article_seed (R4-A1)", () => {
  it("projects article_seed with concise title and snippet only", () => {
    const input: ReaderAskAgenticEvidenceItemDto[] = [
      {
        handle_id: "evh_seed_aabbccddeeff00112233445566778899",
        kind: "article_seed",
        source_tool: "baseline_context",
        snippet: "First paragraph of the article body.",
        unit_id: "u1",
        anchor_segment_id: null,
      },
    ];

    const projected = projectAgenticEvidenceForDisplay(input);

    expect(projected).toHaveLength(1);
    expect(projected[0]).toEqual({
      handleId: "evh_seed_aabbccddeeff00112233445566778899",
      kind: "article_seed",
      title: "文章原文",
      snippet: "First paragraph of the article body.",
      sourceTool: "baseline_context",
      ragNavigation: null,
    });
  });

  it("does not expose internal identity fields in the projected output", () => {
    const input: ReaderAskAgenticEvidenceItemDto[] = [
      {
        handle_id: "evh_seed_aabbccddeeff00112233445566778899",
        kind: "article_seed",
        source_tool: "baseline_context",
        snippet: "article snippet",
        unit_id: "u1",
        anchor_segment_id: "s1",
      },
    ];

    const projected = projectAgenticEvidenceForDisplay(input);
    const serialized = JSON.stringify(projected);

    // Forbidden internal fields must never appear in the projection.
    expect(serialized).not.toContain("envelope_fingerprint");
    expect(serialized).not.toContain("stable_document_id");
    expect(serialized).not.toContain("base_id");
    expect(serialized).not.toContain("record_generation");
    expect(serialized).not.toContain("text_hash");
    expect(serialized).not.toContain("base_start_utf16");
    expect(serialized).not.toContain("base_end_utf16");
    // ragNavigation must remain null for article_seed (non-RAG).
    expect(projected[0].ragNavigation).toBeNull();
  });

  it("keeps article_seed snippet read-only and non-mutating", () => {
    const input: ReaderAskAgenticEvidenceItemDto[] = [
      {
        handle_id: "evh_seed_aabbccddeeff00112233445566778899",
        kind: "article_seed",
        source_tool: "baseline_context",
        snippet: "original snippet",
      },
    ];
    const before = freezeSnapshot(input as unknown as AgenticEvidenceDisplayItem[]);

    const projected = projectAgenticEvidenceForDisplay(input);

    // Mutating the projected snippet must not touch the input DTO.
    projected[0].snippet = "tampered";
    expect(input[0].snippet).toBe("original snippet");
    expect(freezeSnapshot(input as unknown as AgenticEvidenceDisplayItem[])).toEqual(
      before,
    );
  });

  it("preserves article_seed alongside initial_anchor without confusion", () => {
    const input: ReaderAskAgenticEvidenceItemDto[] = [
      {
        handle_id: "evh_anchor_aabbccddeeff00112233445566778899",
        kind: "initial_anchor",
        source_tool: "initial_anchor",
        snippet: "selected sentence",
        unit_id: "u1",
        anchor_segment_id: "s1",
      },
      {
        handle_id: "evh_seed_aabbccddeeff00112233445566778899",
        kind: "article_seed",
        source_tool: "baseline_context",
        snippet: "article body snippet",
        unit_id: "u1",
        anchor_segment_id: null,
      },
    ];

    const projected = projectAgenticEvidenceForDisplay(input);

    expect(projected).toHaveLength(2);
    expect(projected[0].kind).toBe("initial_anchor");
    expect(projected[0].title).toBe("初始选区");
    expect(projected[1].kind).toBe("article_seed");
    expect(projected[1].title).toBe("文章原文");
    // Same unit_id is allowed; kinds are distinct so no confusion.
    expect(projected[0].handleId).not.toBe(projected[1].handleId);
  });

  it("projects article_seed with empty snippet as empty string", () => {
    const input: ReaderAskAgenticEvidenceItemDto[] = [
      {
        handle_id: "evh_seed_aabbccddeeff00112233445566778899",
        kind: "article_seed",
        source_tool: "baseline_context",
        snippet: null,
      },
    ];

    const projected = projectAgenticEvidenceForDisplay(input);

    expect(projected[0].snippet).toBe("");
  });
});

// ---------------------------------------------------------------------------
// R4-A1 rework: strict cold/hot evidence legal-map guard integration
//
// Verifies isReaderAskAgenticEvidenceItem (the per-item guard used by both
// hot completed and cold hydration paths) enforces the legal kind↔source_tool
// map and rag_citation presence rules. Also verifies the projection handles
// every legal combination without crashing.
// ---------------------------------------------------------------------------

describe("agentic evidence legal-map — guard and projection integration (R4-A1 rework)", () => {
  const HANDLE = "evh_aabbccddeeff00112233445566778899";

  describe("guard accepts all 5 legal kind/source pairs", () => {
    it("accepts article_seed + baseline_context (no rag_citation)", () => {
      expect(
        isReaderAskAgenticEvidenceItem({
          handle_id: HANDLE,
          kind: "article_seed",
          source_tool: "baseline_context",
          snippet: "snippet",
        }),
      ).toBe(true);
    });

    it("accepts initial_anchor + initial_anchor (no rag_citation)", () => {
      expect(
        isReaderAskAgenticEvidenceItem({
          handle_id: HANDLE,
          kind: "initial_anchor",
          source_tool: "initial_anchor",
          snippet: "snippet",
        }),
      ).toBe(true);
    });

    it("accepts read_range + read_range (no rag_citation)", () => {
      expect(
        isReaderAskAgenticEvidenceItem({
          handle_id: HANDLE,
          kind: "read_range",
          source_tool: "read_range",
          snippet: "snippet",
        }),
      ).toBe(true);
    });

    it("accepts search_hit + search_current_article (with complete rag_citation)", () => {
      expect(
        isReaderAskAgenticEvidenceItem({
          handle_id: HANDLE,
          kind: "search_hit",
          source_tool: "search_current_article",
          snippet: "snippet",
          rag_citation: COMPLETE_RAG_CITATION,
        }),
      ).toBe(true);
    });

    it("accepts observation + initial_anchor (no rag_citation)", () => {
      expect(
        isReaderAskAgenticEvidenceItem({
          handle_id: HANDLE,
          kind: "observation",
          source_tool: "initial_anchor",
          snippet: "snippet",
        }),
      ).toBe(true);
    });

    it("accepts observation + read_range (no rag_citation)", () => {
      expect(
        isReaderAskAgenticEvidenceItem({
          handle_id: HANDLE,
          kind: "observation",
          source_tool: "read_range",
          snippet: "snippet",
        }),
      ).toBe(true);
    });

    it("accepts observation + search_current_article (no rag_citation)", () => {
      expect(
        isReaderAskAgenticEvidenceItem({
          handle_id: HANDLE,
          kind: "observation",
          source_tool: "search_current_article",
          snippet: "snippet",
        }),
      ).toBe(true);
    });
  });

  describe("guard rejects illegal kind/source pairs", () => {
    it("rejects article_seed + initial_anchor", () => {
      expect(
        isReaderAskAgenticEvidenceItem({
          handle_id: HANDLE,
          kind: "article_seed",
          source_tool: "initial_anchor",
          snippet: "snippet",
        }),
      ).toBe(false);
    });

    it("rejects article_seed + read_range", () => {
      expect(
        isReaderAskAgenticEvidenceItem({
          handle_id: HANDLE,
          kind: "article_seed",
          source_tool: "read_range",
          snippet: "snippet",
        }),
      ).toBe(false);
    });

    it("rejects article_seed + search_current_article", () => {
      expect(
        isReaderAskAgenticEvidenceItem({
          handle_id: HANDLE,
          kind: "article_seed",
          source_tool: "search_current_article",
          snippet: "snippet",
        }),
      ).toBe(false);
    });

    it("rejects initial_anchor + baseline_context", () => {
      expect(
        isReaderAskAgenticEvidenceItem({
          handle_id: HANDLE,
          kind: "initial_anchor",
          source_tool: "baseline_context",
          snippet: "snippet",
        }),
      ).toBe(false);
    });

    it("rejects initial_anchor + read_range", () => {
      expect(
        isReaderAskAgenticEvidenceItem({
          handle_id: HANDLE,
          kind: "initial_anchor",
          source_tool: "read_range",
          snippet: "snippet",
        }),
      ).toBe(false);
    });

    it("rejects read_range + baseline_context", () => {
      expect(
        isReaderAskAgenticEvidenceItem({
          handle_id: HANDLE,
          kind: "read_range",
          source_tool: "baseline_context",
          snippet: "snippet",
        }),
      ).toBe(false);
    });

    it("rejects search_hit + initial_anchor (even with rag_citation)", () => {
      expect(
        isReaderAskAgenticEvidenceItem({
          handle_id: HANDLE,
          kind: "search_hit",
          source_tool: "initial_anchor",
          snippet: "snippet",
          rag_citation: COMPLETE_RAG_CITATION,
        }),
      ).toBe(false);
    });

    it("rejects search_hit + baseline_context (even with rag_citation)", () => {
      expect(
        isReaderAskAgenticEvidenceItem({
          handle_id: HANDLE,
          kind: "search_hit",
          source_tool: "baseline_context",
          snippet: "snippet",
          rag_citation: COMPLETE_RAG_CITATION,
        }),
      ).toBe(false);
    });

    it("rejects observation + baseline_context", () => {
      expect(
        isReaderAskAgenticEvidenceItem({
          handle_id: HANDLE,
          kind: "observation",
          source_tool: "baseline_context",
          snippet: "snippet",
        }),
      ).toBe(false);
    });

    it("rejects observation + agent_observation (legacy source_tool no longer valid)", () => {
      expect(
        isReaderAskAgenticEvidenceItem({
          handle_id: HANDLE,
          kind: "observation",
          source_tool: "agent_observation",
          snippet: "snippet",
        }),
      ).toBe(false);
    });

    it("rejects unknown kind entirely", () => {
      expect(
        isReaderAskAgenticEvidenceItem({
          handle_id: HANDLE,
          kind: "future_kind",
          source_tool: "baseline_context",
          snippet: "snippet",
        }),
      ).toBe(false);
    });

    it("rejects unknown source_tool string (not just legal-map check)", () => {
      expect(
        isReaderAskAgenticEvidenceItem({
          handle_id: HANDLE,
          kind: "initial_anchor",
          source_tool: "random_string",
          snippet: "snippet",
        }),
      ).toBe(false);
    });
  });

  describe("guard rejects rag_citation presence violations", () => {
    it("rejects article_seed with any rag_citation present", () => {
      expect(
        isReaderAskAgenticEvidenceItem({
          handle_id: HANDLE,
          kind: "article_seed",
          source_tool: "baseline_context",
          snippet: "snippet",
          rag_citation: { snippet: "illegal" },
        }),
      ).toBe(false);
    });

    it("rejects search_hit without rag_citation", () => {
      expect(
        isReaderAskAgenticEvidenceItem({
          handle_id: HANDLE,
          kind: "search_hit",
          source_tool: "search_current_article",
          snippet: "no citation",
        }),
      ).toBe(false);
    });

    it("rejects initial_anchor with rag_citation present", () => {
      expect(
        isReaderAskAgenticEvidenceItem({
          handle_id: HANDLE,
          kind: "initial_anchor",
          source_tool: "initial_anchor",
          snippet: "snippet",
          rag_citation: { snippet: "illegal" },
        }),
      ).toBe(false);
    });

    it("rejects read_range with rag_citation present", () => {
      expect(
        isReaderAskAgenticEvidenceItem({
          handle_id: HANDLE,
          kind: "read_range",
          source_tool: "read_range",
          snippet: "snippet",
          rag_citation: { snippet: "illegal" },
        }),
      ).toBe(false);
    });

    it("rejects observation with rag_citation present", () => {
      expect(
        isReaderAskAgenticEvidenceItem({
          handle_id: HANDLE,
          kind: "observation",
          source_tool: "initial_anchor",
          snippet: "snippet",
          rag_citation: { snippet: "illegal" },
        }),
      ).toBe(false);
    });

    it("rejects search_hit with partial rag_citation (missing required fields)", () => {
      expect(
        isReaderAskAgenticEvidenceItem({
          handle_id: HANDLE,
          kind: "search_hit",
          source_tool: "search_current_article",
          snippet: "partial",
          rag_citation: { snippet: "missing identity fields" },
        }),
      ).toBe(false);
    });
  });

  describe("projection handles all legal combinations without crashing", () => {
    it("projects all 5 legal kinds in a single batch", () => {
      const input: ReaderAskAgenticEvidenceItemDto[] = [
        {
          handle_id: "evh_seed_aabbccddeeff00112233445566778899",
          kind: "article_seed",
          source_tool: "baseline_context",
          snippet: "article snippet",
        },
        {
          handle_id: "evh_anchor_aabbccddeeff00112233445566778899",
          kind: "initial_anchor",
          source_tool: "initial_anchor",
          snippet: "anchor snippet",
        },
        {
          handle_id: "evh_range_aabbccddeeff00112233445566778899",
          kind: "read_range",
          source_tool: "read_range",
          snippet: "range snippet",
        },
        {
          handle_id: "evh_search_aabbccddeeff00112233445566778899",
          kind: "search_hit",
          source_tool: "search_current_article",
          snippet: "search snippet",
          rag_citation: COMPLETE_RAG_CITATION,
        },
        {
          handle_id: "evh_obs_aabbccddeeff00112233445566778899",
          kind: "observation",
          source_tool: "initial_anchor",
          snippet: "obs snippet",
        },
      ];

      // All items pass the guard.
      expect(input.every(isReaderAskAgenticEvidenceItem)).toBe(true);

      const projected = projectAgenticEvidenceForDisplay(input);
      expect(projected).toHaveLength(5);
      expect(projected.map((p) => p.kind)).toEqual([
        "article_seed",
        "initial_anchor",
        "read_range",
        "search_hit",
        "observation",
      ]);
      // Only search_hit has ragNavigation; all others are null.
      expect(projected.filter((p) => p.ragNavigation !== null)).toHaveLength(1);
      expect(projected[3].ragNavigation).not.toBeNull();
    });

    it("projects all 3 legal observation source_tools", () => {
      const sources = [
        "initial_anchor",
        "read_range",
        "search_current_article",
      ] as const;
      const input: ReaderAskAgenticEvidenceItemDto[] = sources.map(
        (source_tool, i) => ({
          handle_id: `evh_obs_${i}_${"a".repeat(24)}`,
          kind: "observation" as const,
          source_tool,
          snippet: `obs from ${source_tool}`,
        }),
      );

      expect(input.every(isReaderAskAgenticEvidenceItem)).toBe(true);

      const projected = projectAgenticEvidenceForDisplay(input);
      expect(projected).toHaveLength(3);
      expect(projected.map((p) => p.sourceTool)).toEqual([
        "initial_anchor",
        "read_range",
        "search_current_article",
      ]);
      // All observation items have null ragNavigation.
      expect(projected.every((p) => p.ragNavigation === null)).toBe(true);
    });
  });
});

// ---------------------------------------------------------------------------
// ASK-WEB-G0/G1: projectAgenticCitationsForDisplay — web citation projection
//
// Verifies that public citations split correctly by source_kind:
//   - article citations: title="文章依据", url/sourceTitle/description=null
//   - web citations: title="网络来源", url/sourceTitle/description populated
// Snippet is always coerced to "" when missing/empty/non-string. Order is
// preserved. The projection must not invent fields or mutate the input.
// ---------------------------------------------------------------------------

describe("projectAgenticCitationsForDisplay — web citation projection (ASK-WEB-G0/G1)", () => {
  it("projects an article citation with article-stable title and null web fields", () => {
    const input: ReaderAskAgenticCitationDto[] = [
      {
        citation_id: "c1",
        source_kind: "article",
        snippet: "paragraph body",
      },
    ];
    const projected = projectAgenticCitationsForDisplay(input);
    expect(projected).toHaveLength(1);
    expect(projected[0]).toEqual({
      citationId: "c1",
      sourceKind: "article",
      title: "文章依据",
      snippet: "paragraph body",
      url: null,
      sourceTitle: null,
      description: null,
      publishedAt: null,
      retrievedAt: null,
    });
  });

  it("projects a web citation with web-stable title and url/sourceTitle/description", () => {
    const input: ReaderAskAgenticCitationDto[] = [
      {
        citation_id: "c2",
        source_kind: "web",
        snippet: "web snippet text",
        url: "https://example.com/page",
        title: "Example Page Title",
        description: "A short description of the page.",
      },
    ];
    const projected = projectAgenticCitationsForDisplay(input);
    expect(projected).toHaveLength(1);
    expect(projected[0]).toEqual({
      citationId: "c2",
      sourceKind: "web",
      title: "网络来源",
      snippet: "web snippet text",
      url: "https://example.com/page",
      sourceTitle: "Example Page Title",
      description: "A short description of the page.",
      publishedAt: null,
      retrievedAt: null,
    });
  });

  it("coerces missing snippet to empty string for both source kinds", () => {
    const input: ReaderAskAgenticCitationDto[] = [
      { citation_id: "c1", source_kind: "article" },
      {
        citation_id: "c2",
        source_kind: "web",
        url: "https://example.com/x",
        title: "Title",
      },
    ];
    const projected = projectAgenticCitationsForDisplay(input);
    expect(projected[0].snippet).toBe("");
    expect(projected[1].snippet).toBe("");
  });

  it("coerces empty-string snippet to empty string", () => {
    const input: ReaderAskAgenticCitationDto[] = [
      { citation_id: "c1", source_kind: "article", snippet: "" },
    ];
    const projected = projectAgenticCitationsForDisplay(input);
    expect(projected[0].snippet).toBe("");
  });

  it("coerces null snippet to empty string", () => {
    const input: ReaderAskAgenticCitationDto[] = [
      { citation_id: "c1", source_kind: "article", snippet: null },
    ];
    const projected = projectAgenticCitationsForDisplay(input);
    expect(projected[0].snippet).toBe("");
  });

  it("coerces null url/title/description to null for web citations", () => {
    const input: ReaderAskAgenticCitationDto[] = [
      {
        citation_id: "c1",
        source_kind: "web",
        snippet: "snippet",
        url: null,
        title: null,
        description: null,
      },
    ];
    const projected = projectAgenticCitationsForDisplay(input);
    expect(projected[0]).toEqual({
      citationId: "c1",
      sourceKind: "web",
      title: "网络来源",
      snippet: "snippet",
      url: null,
      sourceTitle: null,
      description: null,
      publishedAt: null,
      retrievedAt: null,
    });
  });

  it("forces url/sourceTitle/description to null for article citations even if present on input", () => {
    // The guard upstream rejects this, but the projection is defensive —
    // article citations must never expose web fields to the UI.
    const input = [
      {
        citation_id: "c1",
        source_kind: "article" as const,
        snippet: "snippet",
        url: "https://should-not-appear.example",
        title: "Should Not Appear",
        description: "Should not appear either",
      },
    ];
    const projected = projectAgenticCitationsForDisplay(input);
    expect(projected[0].url).toBeNull();
    expect(projected[0].sourceTitle).toBeNull();
    expect(projected[0].description).toBeNull();
  });

  it("preserves mixed article + web citation order", () => {
    const input: ReaderAskAgenticCitationDto[] = [
      { citation_id: "c1", source_kind: "article", snippet: "art1" },
      {
        citation_id: "c2",
        source_kind: "web",
        snippet: "web1",
        url: "https://a.example",
        title: "A",
      },
      { citation_id: "c3", source_kind: "article", snippet: "art2" },
      {
        citation_id: "c4",
        source_kind: "web",
        snippet: "web2",
        url: "https://b.example",
        title: "B",
      },
    ];
    const projected = projectAgenticCitationsForDisplay(input);
    expect(projected.map((p) => p.citationId)).toEqual([
      "c1",
      "c2",
      "c3",
      "c4",
    ]);
    expect(projected.map((p) => p.sourceKind)).toEqual([
      "article",
      "web",
      "article",
      "web",
    ]);
  });

  it("returns an empty array for empty input", () => {
    expect(projectAgenticCitationsForDisplay([])).toEqual([]);
  });

  it("does not mutate the input DTO", () => {
    const input: ReaderAskAgenticCitationDto[] = [
      {
        citation_id: "c1",
        source_kind: "web",
        snippet: "original",
        url: "https://example.com",
        title: "Original Title",
        description: "Original desc",
      },
    ];
    const before = JSON.parse(JSON.stringify(input)) as typeof input;
    const projected = projectAgenticCitationsForDisplay(input);
    // Mutate the projection — input must be untouched.
    projected[0].snippet = "tampered";
    projected[0].url = "tampered";
    projected[0].sourceTitle = "tampered";
    projected[0].description = "tampered";
    expect(input).toEqual(before);
  });

  it("omits internal/debug fields from the projected output", () => {
    // The DTO type does not allow these, but defensively verify they never
    // appear in the projection even if a caller sneaks them in via casts.
    const input = [
      {
        citation_id: "c1",
        source_kind: "web" as const,
        snippet: "snippet",
        url: "https://example.com",
        title: "Title",
        description: "desc",
        // Forbidden extras:
        handle_id: "HANDLE",
        rag_navigation: { foo: "bar" },
        web_snapshot: { baz: "qux" },
        provider: "SECRET_PROVIDER",
        query: "SECRET_QUERY",
        rank: 1,
        score: 0.9,
      },
    ];
    const projected = projectAgenticCitationsForDisplay(
      input as unknown as ReaderAskAgenticCitationDto[],
    );
    const serialized = JSON.stringify(projected);
    expect(serialized).not.toContain("handle_id");
    expect(serialized).not.toContain("HANDLE");
    expect(serialized).not.toContain("rag_navigation");
    expect(serialized).not.toContain("web_snapshot");
    expect(serialized).not.toContain("provider");
    expect(serialized).not.toContain("SECRET_PROVIDER");
    expect(serialized).not.toContain("SECRET_QUERY");
    expect(serialized).not.toContain("rank");
    expect(serialized).not.toContain("score");
  });

  it("projects the AgenticCitationDisplayItem shape exactly", () => {
    const input: ReaderAskAgenticCitationDto[] = [
      {
        citation_id: "c1",
        source_kind: "web",
        snippet: "snippet",
        url: "https://example.com",
        title: "Title",
        description: "desc",
      },
    ];
    const projected = projectAgenticCitationsForDisplay(input);
    expect(Object.keys(projected[0]).sort()).toEqual(
      [
        "citationId",
        "description",
        "publishedAt",
        "retrievedAt",
        "snippet",
        "sourceKind",
        "sourceTitle",
        "title",
        "url",
      ].sort(),
    );
  });

  it("type-checks: AgenticCitationDisplayItem is the projected type", () => {
    // Compile-time assertion: the projected array must be assignable to
    // AgenticCitationDisplayItem[]. If the projection shape drifts, this
    // test fails to compile.
    const projected: AgenticCitationDisplayItem[] =
      projectAgenticCitationsForDisplay([
        { citation_id: "c1", source_kind: "article", snippet: "s" },
      ]);
    expect(projected.length).toBe(1);
  });
});
